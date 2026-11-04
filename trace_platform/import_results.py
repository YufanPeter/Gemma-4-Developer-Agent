"""Import finished dev-eval results into the trace platform store.

    python -m trace_platform.import_results exp_res/results_dev60_e5_pipeline [more dirs...]
    python -m trace_platform.import_results --all           # every exp_res/results_*/ with traces

Reads traces/trace_<id>.json (ATIF), dev_results.jsonl, run_meta.json, patches/ and
test_outputs/. Run name = the folder name without 'results_'. Importing over a
live run (same run name) replaces each task's live entries with the authoritative trace
and reports tasks whose live copy had a different number of model calls -- that is
the post-run reconciliation step.

Same name, different run: every branch's notebook defaults to RUN_NAME='dev60_baseline',
so two people's results often share a folder name. A run is identified by
run_meta.json's started_at_utc + submission_fingerprint (falling back to the results
folder path); when the stored run with that name is a different run, it is moved aside
to '<run>@<its start time>' instead of being mixed with the new one. A re-import of the
same run replaces it. --replace overwrites whatever has the name.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import model
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "trace_platform" / "data" / "traces.db"
MAX_LOG = 40_000


META_KEYS = ("run_name", "submission_fingerprint", "model", "eval_config", "sampling", "limits",
             "started_at_utc", "finished_at_utc")


def same_run(a: dict, b: dict) -> bool:
    """Do two runs' meta describe the same eval run? Unknown counts as the same."""
    a, b = a or {}, b or {}
    if a.get("started_at_utc") and b.get("started_at_utc"):
        return (a["started_at_utc"], a.get("submission_fingerprint")) == (b["started_at_utc"], b.get("submission_fingerprint"))
    if a.get("submission_fingerprint") and b.get("submission_fingerprint") \
            and a["submission_fingerprint"] != b["submission_fingerprint"]:
        return False
    if a.get("results_dir") and b.get("results_dir"):
        return a["results_dir"] == b["results_dir"]
    return True


def make_room(store: Store, run: str, meta: dict, replace: bool = False) -> dict | None:
    """Before writing run `run` described by `meta`: move a different run of that name
    aside (or drop it with `replace`). Returns the stored run if it is the same run."""
    existing = store.run(run)
    if existing is None:
        return None
    if replace:
        store.delete_run(run)
        return None
    if same_run(existing["meta"], meta):
        return existing
    m = existing["meta"] or {}
    stamp = (m.get("started_at_utc") or time.strftime("%Y-%m-%d %H:%M", time.gmtime(existing["created"])))
    moved = store.free_name(f"{run}@{stamp.replace(' ', '_')}")
    store.rename_run(run, moved)
    print(f"{run}: a different run had this name (fp {m.get('submission_fingerprint')}, started {stamp}); "
          f"moved it to {moved}", file=sys.stderr)
    return None


def import_dir(store: Store, d: Path, run: str | None = None, replace: bool = False) -> tuple[str, int, list[str]]:
    meta = {}
    if (d / "run_meta.json").exists():
        rm = json.loads((d / "run_meta.json").read_text())
        meta = {k: rm[k] for k in META_KEYS if k in rm}
    meta["results_dir"] = str(d)
    # The folder name, not run_meta.run_name: two folders can share a run_name (probe_e4
    # v1/v2), and a live run named RUN_NAME is downloaded as results_<RUN_NAME>/ anyway.
    run = run or d.name.removeprefix("results_")
    rows = {}
    if (d / "dev_results.jsonl").exists():
        for line in (d / "dev_results.jsonl").read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r
    existing = make_room(store, run, meta, replace)
    if existing is not None and existing["source"] in ("import", "shared"):
        store.delete_run(run)  # a plain re-import, not a reconciliation of a live run
        existing = None
    store.upsert_run(run, "import" if existing is None else "live+final", meta,
                     status="done", expected_tasks=len(rows) or None)
    mismatched = []
    n = 0
    for tp in sorted((d / "traces").glob("trace_*.json")):
        tid = tp.stem[len("trace_"):]
        entries = model.atif_to_entries(json.loads(tp.read_text()))
        if existing is not None:
            live = store.entries(run, tid)
            if live:
                a = model.summarize(live)["llm_calls"]
                b = model.summarize(entries)["llm_calls"]
                if a != b:
                    mismatched.append(f"{tid}: live {a} LLM calls vs final {b}")
        store.replace_entries(run, tid, entries)
        r = rows.get(tid, {})
        patch = d / "patches" / f"{tid}.patch"
        log = d / "test_outputs" / f"{tid}.log"
        log_text = log.read_text(errors="replace") if log.exists() else ""
        if len(log_text) > MAX_LOG:
            log_text = f"... [{len(log_text) - MAX_LOG:,} chars clipped]\n" + log_text[-MAX_LOG:]
        ts = [e["timestamp"] for e in entries if e.get("timestamp")]
        store.upsert_task(run, tid, status="done", source="final", result=r or None,
                          patch=patch.read_text(errors="replace") if patch.exists() else "",
                          test_log=log_text, started=min(ts) if ts else None, ended=max(ts) if ts else None)
        n += 1
    return run, n, mismatched


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="*", type=Path)
    ap.add_argument("--all", action="store_true", help="import every exp_res/results_*/ that has traces/")
    ap.add_argument("--run", help="override the run name (single dir only)")
    ap.add_argument("--replace", action="store_true", help="overwrite a stored run of the same name even if it is a different run")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    dirs = list(a.dirs)
    if a.all:
        dirs += sorted(p.parent for p in (ROOT / "exp_res").glob("results_*/traces"))
    if not dirs:
        ap.error("give result dirs or --all")
    store = Store(a.db)
    for d in dirs:
        run, n, mism = import_dir(store, d, a.run if len(dirs) == 1 else None, a.replace)
        print(f"{run}: {n} tasks from {d}")
        for m in mism:
            print(f"  reconcile: {m}")


if __name__ == "__main__":
    main()
