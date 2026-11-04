"""Command line for the trace platform: share runs as JSONL and analyse them as text.

Built so a teammate (or Claude Code) can go from "look at run X" to the problem quickly.
Reads/writes the SQLite store directly; the server does not need to be running.

    python -m trace_platform.cli add <source> [--run NAME] [--replace]   # run name | results dir | .jsonl[.gz] | Kaggle kernel slug
    python -m trace_platform.cli runs
    python -m trace_platform.cli summary <run>         # where it fails and where the time goes
    python -m trace_platform.cli tasks <run> [--failed]
    python -m trace_platform.cli show <run> <task> [--full]
    python -m trace_platform.cli grep <run> <regex> [--in args|results|thoughts|all]
    python -m trace_platform.cli compare <runA> <runB>
    python -m trace_platform.cli export <run> [-o file.jsonl.gz]
    python -m trace_platform.cli import <file.jsonl[.gz]> [--run NAME] [--replace]

Task outcomes (trace_platform/outcome.py): PASS TIMEOUT BUDGET ERROR NO_PATCH ENV_ERROR FAIL
(+ RUNNING / UNKNOWN). Same run name but a different run (another branch's results,
both default to dev60_baseline): the stored one is moved aside to '<run>@<start time>'.

Share format (one JSON object per line, gzip if the name ends in .gz):
    {"type": "meta", "format": "gemma-trace-v1", "run": R, "exported_at": TS}
    {"type": "run", "run": R, "source": ..., "status": ..., "meta": {...}, "expected_tasks": N}
    {"type": "task", "task": T, "status": ..., "source": ..., "started": TS, "ended": TS, "result": {...}, "patch": "...", "test_log": "..."}
    {"type": "entry", "task": T, "seq": N, "entry": <TraceEntry dict>}
    {"type": "metric", "ts": TS, "data": {...}}
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics as st
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

from . import model, outcome
from .import_results import DEFAULT_DB, ROOT, import_dir, make_room, same_run
from .store import Store

FORMAT = "gemma-trace-v1"


def _open(path: Path, mode: str):
    return gzip.open(path, mode + "t", encoding="utf-8") if path.suffix == ".gz" else path.open(mode, encoding="utf-8")


# ── share ────────────────────────────────────────────────────────────────────
def export_run(store: Store, run: str, out: Path) -> int:
    r = store.run(run)
    if r is None:
        raise SystemExit(f"no run {run!r}")
    n = 0
    with _open(out, "w") as f:
        w = lambda o: f.write(json.dumps(o, ensure_ascii=False, default=str) + "\n")  # noqa: E731
        w({"type": "meta", "format": FORMAT, "run": run, "exported_at": time.time()})
        w({"type": "run", "run": run, "source": r["source"], "status": r["status"], "meta": r["meta"],
           "expected_tasks": r["expected_tasks"]})
        for t in store.task_rows(run):
            full = store.task(run, t["task"])
            w({"type": "task", **{k: full[k] for k in ("task", "status", "source", "started", "ended", "result", "patch", "test_log")}})
            for seq, e in enumerate(store.entries(run, t["task"])):
                w({"type": "entry", "task": t["task"], "seq": seq, "entry": e})
                n += 1
        for m in store.metrics(run):
            ts = m.pop("ts")
            w({"type": "metric", "ts": ts, "data": m})
    return n


def import_jsonl(store: Store, path: Path, run: str | None = None, replace: bool = False) -> str:
    entries: dict[str, list[dict]] = {}
    tasks, metrics, run_row = [], [], None
    with _open(path, "r") as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            t = o.get("type")
            if t == "meta" and o.get("format") != FORMAT:
                raise SystemExit(f"{path}: unknown format {o.get('format')!r}")
            elif t == "run":
                run_row = o
            elif t == "task":
                tasks.append(o)
            elif t == "entry":
                entries.setdefault(o["task"], []).append(o["entry"])
            elif t == "metric":
                metrics.append((o["ts"], o["data"]))
    if run_row is None:
        raise SystemExit(f"{path}: no run line")
    name = run or run_row["run"]
    if make_room(store, name, run_row.get("meta") or {}, replace) is not None:
        store.delete_run(name)  # same run again: the file is a full copy, replace it
    store.upsert_run(name, "shared", {**(run_row.get("meta") or {}), "shared_from": str(path)},
                     status=run_row.get("status"), expected_tasks=run_row.get("expected_tasks"))
    for t in tasks:
        store.replace_entries(name, t["task"], entries.get(t["task"], []))
        store.upsert_task(name, t["task"], **{k: t.get(k) for k in ("status", "source", "started", "ended", "result", "patch", "test_log")})
    if metrics:
        store.add_metrics(name, metrics)
    return name


def add_source(store: Store, src: str, run: str | None, replace: bool = False) -> str:
    """Make `src` available as a run: already stored, a results dir, a share file, or a kernel."""
    if store.run(src) is not None and run is None and not replace:
        return src
    p = Path(src)
    if p.is_dir() and (p / "traces").is_dir():
        name, n, mism = import_dir(store, p.resolve(), run, replace)
        print(f"imported {n} tasks from {p} as {name}", file=sys.stderr)
        for m in mism:
            print(f"  reconcile: {m}", file=sys.stderr)
        return name
    if p.is_file():
        name = import_jsonl(store, p, run, replace)
        print(f"imported {p} as {name}", file=sys.stderr)
        return name
    d = ROOT / "exp_res" / f"results_{src}"
    if d.is_dir():
        return add_source(store, str(d), run, replace)
    if re.fullmatch(r"[\w.-]+/[\w.-]+", src):
        with tempfile.TemporaryDirectory() as td:
            print(f"downloading output of kernel {src} ...", file=sys.stderr)
            subprocess.run(["kaggle", "kernels", "output", src, "-p", td], check=True, capture_output=True)
            zips = sorted(Path(td).glob("results_*.zip"))
            if not zips:
                raise SystemExit(f"kernel {src} has no results_*.zip in its output (still running? follow it with kaggle_logs)")
            dest = ROOT / "exp_res" / zips[0].stem
            with zipfile.ZipFile(zips[0]) as zf:
                new_meta = json.loads(zf.read("run_meta.json")) if "run_meta.json" in zf.namelist() else {}
                old = dest / "run_meta.json"
                if dest.exists() and old.exists() and not same_run(json.loads(old.read_text()), new_meta):
                    # Another run (e.g. a teammate's dev60_baseline) already unpacked here.
                    dest = dest.with_name(f"{dest.name}__{src.replace('/', '__')}")
                    print(f"exp_res/{zips[0].stem} holds a different run; unpacking to {dest}", file=sys.stderr)
                dest.mkdir(parents=True, exist_ok=True)
                zf.extractall(dest)
            return add_source(store, str(dest), run or zips[0].stem.removeprefix("results_"), replace)
    raise SystemExit(f"cannot find {src!r}: not a stored run, results dir, share file, exp_res/results_{src}, or kernel slug")


# ── analysis ─────────────────────────────────────────────────────────────────
def _label(t: dict) -> str:
    return t.get("outcome") or outcome.outcome(t.get("status"), t.get("result"), t.get("patch"), t.get("test_log"))


def _first_edit(b: dict) -> float | None:
    return next((x["start"] for x in b["tools"] if x["fn"] in ("edit_file", "write_file")), None)


def _repeats(b: dict) -> int:
    seen, n = set(), 0
    for x in b["tools"]:
        k = (x["fn"], x["args"])
        n += k in seen
        seen.add(k)
    return n


def cmd_runs(store: Store, a) -> None:
    print(f"{'run':<34} {'source':<10} {'status':<8} {'pass':>4} {'done':>5} {'tasks':>5} {'fp':<12}  {'updated':<11}  outcomes")
    for r in store.runs():
        oc = " ".join(f"{k} {r['outcomes'][k]}" for k in outcome.ORDER if r["outcomes"].get(k) and k != "PASS")
        print(f"{r['run']:<34} {r['source']:<10} {r['status']:<8} {r['resolved']:>4} {r['done']:>5} "
              f"{(r['expected_tasks'] or r['tasks']):>5} {(r['meta'].get('submission_fingerprint') or '-'):<12}  "
              f"{time.strftime('%m-%d %H:%M', time.localtime(r['updated']))}  {oc}")


def cmd_tasks(store: Store, a) -> None:
    run = add_source(store, a.run, None)
    rows = store.task_rows(run)
    print(f"{'task':<16} {'result':<9} {'calls':>5} {'llm':>4} {'dur':>6} {'peak':>6} {'comp':>4} {'slow':>5} {'stage':<10} error")
    for t in rows:
        lab = _label(t)
        if a.failed and lab == "PASS":
            continue
        s = t["stats"]
        r = t["result"] or {}
        dur = r.get("duration_seconds") or s.get("duration") or 0
        print(f"{t['task']:<16} {lab:<9} {r.get('tool_calls', s.get('tool_calls')) or 0:>5} {s.get('llm_calls', 0):>4} "
              f"{dur:>5.0f}s {s.get('peak_ctx', 0):>6} {s.get('compactions', 0):>4} {s.get('slow_s', 0):>4.0f}s "
              f"{(s.get('last_author') or ''):<10} {(r.get('error') or '')[:70]}")


def cmd_summary(store: Store, a) -> None:
    run = add_source(store, a.run, None)
    r = store.run(run)
    rows = store.task_rows(run)
    built = {t["task"]: model.build_task(store.entries(run, t["task"])) for t in rows}
    labs = Counter(_label(t) for t in rows)
    labs = {k: labs[k] for k in outcome.ORDER if labs.get(k)}
    print(f"# {run}  ({r['source']}, {r['status']})  model={r['meta'].get('model')}  fp={r['meta'].get('submission_fingerprint')}")
    if r["meta"].get("eval_config"):
        print(f"budget: {r['meta']['eval_config']}")
    print(f"tasks {len(rows)}: " + ", ".join(f"{k} {v}" for k, v in labs.items()))
    if labs.get("ENV_ERROR"):
        print(f"  ENV_ERROR = pytest errored at collection/setup: the patch was never really tested (env, not agent); "
              f"pass rate excluding them {labs.get('PASS', 0)}/{len(rows) - labs['ENV_ERROR']}")
    errs = Counter(re.sub(r"\d+(\.\d+)?", "N", (t["result"] or {}).get("error") or "")[:80] for t in rows if (t["result"] or {}).get("error"))
    if errs:
        print("\nerror messages:")
        for e, n in errs.most_common(8):
            print(f"  {n:>3}  {e}")

    def dist(name, vals, fmt="{:.0f}"):
        vals = [v for v in vals if v is not None]
        if vals:
            print(f"  {name:<34} median {fmt.format(st.median(vals))}  p90 {fmt.format(sorted(vals)[int(len(vals) * .9) - 1 if len(vals) > 1 else 0])}  max {fmt.format(max(vals))}")
    print("\nper task (all):")
    dist("duration s", [b["stats"].get("duration") for b in built.values()])
    dist("LLM calls", [b["stats"].get("llm_calls") for b in built.values()])
    dist("tool calls", [b["stats"].get("tool_calls") for b in built.values()])
    dist("first edit at s", [_first_edit(b) for b in built.values()])
    dist("peak prompt tokens", [b["stats"].get("peak_ctx") for b in built.values()])
    dist("compactions", [b["stats"].get("compactions") for b in built.values()])
    dist("time in >30s LLM calls s", [b["stats"].get("slow_s") for b in built.values()])
    dist("decode tok/s", [b["stats"]["completion_tokens"] / b["stats"]["llm_s"] for b in built.values() if b["stats"].get("llm_s")], "{:.1f}")
    dist("repeated identical tool calls", [_repeats(b) for b in built.values()])
    noedit = [t for t in rows if _first_edit(built[t["task"]]) is None and t["status"] == "done"]
    if noedit:
        print(f"\nno source edit at all: {len(noedit)} -> " + ", ".join(t["task"] for t in noedit[:12]))
    lanes = Counter()
    lane_t = Counter()
    for b in built.values():
        for l in b["lanes"]:
            lanes[l["author"]] += 1
            lane_t[l["author"]] += l["llm_s"] + l["tool_s"]
    if lanes:
        print("\ntime by sub-agent: " + ", ".join(f"{k} {lane_t[k] / lanes[k]:.0f}s/task" for k in lanes))
    tools = Counter(x["fn"] for b in built.values() for x in b["tools"])
    terr = Counter(x["fn"] for b in built.values() for x in b["tools"] if x["status"] == "error")
    print("tools: " + ", ".join(f"{k} {v}" + (f" ({terr[k]} err)" if terr[k] else "") for k, v in tools.most_common(12)))
    ends = Counter((b["stats"].get("last_author") or "?") for t, b in built.items() if _label(next(x for x in rows if x["task"] == t)) == "TIMEOUT")
    if ends:
        print("timeouts ended in stage: " + ", ".join(f"{k} {v}" for k, v in ends.most_common()))
    ms = store.metrics(run)
    if ms:
        kv = [m.get("vllm", {}).get("kv_cache") for m in ms if m.get("vllm", {}).get("kv_cache") is not None]
        util = [g.get("util") for m in ms for g in (m.get("gpus") or []) if g.get("util") is not None]
        print(f"\nGPU: {len(ms)} samples; util median {st.median(util):.0f}%" if util else f"\nGPU: {len(ms)} samples", end="")
        print(f"; KV cache median {st.median(kv) * 100:.0f}% max {max(kv) * 100:.0f}%" if kv else "")


def cmd_show(store: Store, a) -> None:
    run = add_source(store, a.run, None)
    t = store.task(run, a.task)
    if t is None:
        raise SystemExit(f"no task {a.task!r} in {run}")
    b = model.build_task(store.entries(run, a.task))
    s = b["stats"]
    r = t["result"] or {}
    cap = a.max_chars
    clip = lambda x: (x if x is None or len(x) <= cap else x[:cap] + f" …[+{len(x) - cap}]")  # noqa: E731
    print(f"# {run} / {a.task}: {_label(t)}  {r.get('error') or ''}")
    print(f"duration {s.get('duration', 0):.0f}s · {s.get('llm_calls')} LLM · {s.get('tool_calls')} tools · peak {s.get('peak_ctx')} · "
          f"compactions {s.get('compactions')} · first edit {_first_edit(b)} · repeats {_repeats(b)}")
    print("lanes: " + ", ".join(f"{l['author']} {l['start']:.0f}-{l['end']:.0f}s ({l['llm']} llm, {l['tools']} tools)" for l in b["lanes"]))
    comp_at = {round(c["t"], 1): c for c in b["compactions"]}
    for step in b["steps"]:
        if step["kind"] == "llm":
            c = b["llm"][step["llm"]]
            mark = " [after compaction]" if c["compacted"] else ""
            print(f"\n#{c['i'] + 1:<3} {c['end']:>6.0f}s +{c['dur']:>5.1f}s  {c['author']:<9} in {c['pt'] or 0:>6} out {c['ct'] or 0:>5}{mark}")
            if a.full and step.get("thought"):
                print("     thought: " + clip(step["thought"]).replace("\n", "\n              "))
            if step.get("msg"):
                print("     says: " + clip(step["msg"] if a.full else step["msg"][:200]).replace("\n", "\n           "))
            for ti in step["calls"]:
                x = b["tools"][ti]
                res = x.get("result") or ""
                print(f"     -> {x['fn']} {x['summary']}  [{x['status']}, {x.get('result_chars', 0)} chars, {x['dur'] if x['dur'] is not None else '?'}s]")
                if a.full:
                    print("        args: " + clip(x["args"] or "").replace("\n", "\n              "))
                    print("        out:  " + clip(res).replace("\n", "\n              "))
                elif x["status"] == "error":
                    print("        out:  " + res[:240].replace("\n", " "))
        elif step["kind"] == "compaction":
            print(f"\n== compaction at {step['t']:.0f}s ==" + (f"\n   {clip(step.get('msg'))}" if a.full else ""))
        elif step["kind"] in ("task_prompt", "system_instruction"):
            if a.full:
                print(f"\n[{step['kind']}] {clip(step.get('msg'))}")
        else:
            print(f"\n[{step['kind']}] {step['t']:.0f}s {(step.get('msg') or '')[:200]}")
    for c in b["compactions"]:
        if c["source"] == "inferred":
            print(f"\n(compaction inferred at {c['t']:.0f}s: {c['before']} -> {c['after']} tokens)")
    if a.full and t["patch"]:
        print("\n--- patch ---\n" + clip(t["patch"]))


def cmd_grep(store: Store, a) -> None:
    run = add_source(store, a.run, None)
    rx = re.compile(a.pattern, re.I)
    hits = 0
    for t in store.task_rows(run):
        b = model.build_task(store.entries(run, t["task"]))
        for x in b["tools"]:
            for field in (("args", x["args"]), ("results", x.get("result"))):
                if a.where in ("all", field[0]) and field[1] and rx.search(field[1]):
                    m = rx.search(field[1])
                    ctx = field[1][max(0, m.start() - 80): m.end() + 80].replace("\n", " ")
                    print(f"{t['task']:<16} #{x['llm'] + 1:<3} {x['fn']:<16} {field[0]:<7} …{ctx}…")
                    hits += 1
        if a.where in ("all", "thoughts"):
            for step in b["steps"]:
                for txt in (step.get("thought"), step.get("msg")):
                    if txt and (m := rx.search(txt)):
                        print(f"{t['task']:<16} {step['kind']:<4} thought  …{txt[max(0, m.start() - 80): m.end() + 80]}…".replace("\n", " "))
                        hits += 1
    print(f"{hits} hits")


def cmd_compare(store: Store, a) -> None:
    ra, rb = add_source(store, a.a, None), add_source(store, a.b, None)
    A = {t["task"]: t for t in store.task_rows(ra)}
    B = {t["task"]: t for t in store.task_rows(rb)}
    common = sorted(set(A) & set(B))
    print(f"{ra} vs {rb}: {len(common)} common tasks")
    pa = sum(_label(A[t]) == "PASS" for t in common)
    pb = sum(_label(B[t]) == "PASS" for t in common)
    print(f"pass on common: {pa} vs {pb}")
    for t in common:
        la, lb = _label(A[t]), _label(B[t])
        if la != lb:
            sa, sb = A[t]["stats"], B[t]["stats"]
            print(f"  {t:<16} {la:<8} -> {lb:<8} dur {sa.get('duration', 0):.0f}s -> {sb.get('duration', 0):.0f}s  "
                  f"llm {sa.get('llm_calls')} -> {sb.get('llm_calls')}  peak {sa.get('peak_ctx')} -> {sb.get('peak_ctx')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add"); p.add_argument("source"); p.add_argument("--run"); p.add_argument("--replace", action="store_true")
    sp.add_parser("runs")
    p = sp.add_parser("summary"); p.add_argument("run")
    p = sp.add_parser("tasks"); p.add_argument("run"); p.add_argument("--failed", action="store_true")
    p = sp.add_parser("show"); p.add_argument("run"); p.add_argument("task"); p.add_argument("--full", action="store_true"); p.add_argument("--max-chars", type=int, default=1500)
    p = sp.add_parser("grep"); p.add_argument("run"); p.add_argument("pattern"); p.add_argument("--in", dest="where", default="all", choices=["all", "args", "results", "thoughts"])
    p = sp.add_parser("compare"); p.add_argument("a"); p.add_argument("b")
    p = sp.add_parser("export"); p.add_argument("run"); p.add_argument("-o", "--out", type=Path)
    p = sp.add_parser("import"); p.add_argument("file", type=Path); p.add_argument("--run"); p.add_argument("--replace", action="store_true")
    a = ap.parse_args()
    store = Store(a.db)
    if a.cmd == "add":
        print(add_source(store, a.source, a.run, a.replace))
    elif a.cmd == "runs":
        cmd_runs(store, a)
    elif a.cmd == "summary":
        cmd_summary(store, a)
    elif a.cmd == "tasks":
        cmd_tasks(store, a)
    elif a.cmd == "show":
        cmd_show(store, a)
    elif a.cmd == "grep":
        cmd_grep(store, a)
    elif a.cmd == "compare":
        cmd_compare(store, a)
    elif a.cmd == "export":
        out = a.out or Path(f"{a.run}.trace.jsonl.gz")
        n = export_run(store, a.run, out)
        print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {n} entries)")
    elif a.cmd == "import":
        print(import_jsonl(store, a.file, a.run, a.replace))


if __name__ == "__main__":
    main()
