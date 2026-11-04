"""Event model shared by the trace platform and eval/trace_viewer.py.

The unit everything is built from is an **entry**: one ``TraceEntry.to_dict()`` from
``adk_eval_core.tracing.trace.SessionTrace`` -- exactly what the Kaggle push hook sends
live, one per thought / tool call / tool response / text / compaction / usage::

    {"timestamp": 1793151295.97, "elapsed_s": 7.27, "type": "tool_call",
     "author": "locate", "tool": "read_file", "args": {...}, "usage": {...}}

A finished run's ATIF trace (``traces/trace_<id>.json``) is turned back into the same
entries by :func:`atif_to_entries`, so live and imported tasks go through one code path:
:func:`build_task` makes the spans (LLM calls, tool calls, compactions, per-author lanes),
the context-window series and the step list the page shows. Stdlib only.
"""
from __future__ import annotations

import ast
import json
from typing import Any

COMPACTION_THRESHOLD = 14_336  # docs/HARNESS_README.md 7.2 (ADK EventsCompaction token_threshold)
MAX_MODEL_LEN = 32_768         # vLLM max_model_len in the dev-eval notebook
MAX_TEXT = 20_000              # per-field cap for text sent to the page
CHARS_PER_TOKEN = 3.6          # rough Gemma tokenizer ratio for code + English, used only for estimates
HARNESS_AUTHORS = {"harness", "user", "system", "compactor", ""}
RETAIN = 10                    # history entries kept after a compaction (event_retention_size 5 events ~ 10 entries)
MODEL_TYPES = {"thinking", "text", "final", "tool_call", "usage"}


# ── text helpers (also used by eval/trace_viewer.py) ─────────────────────────
def clip(s: str | None, n: int = MAX_TEXT) -> str | None:
    if s is None:
        return None
    if len(s) <= n:
        return s
    return s[:n] + f"\n\n... [{len(s) - n:,} more chars clipped]"


def _maybe_literal(s: Any) -> Any:
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except ValueError:
        pass
    try:
        return ast.literal_eval(s)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return s


def parse_observation(obs: Any) -> tuple[str | None, str | None]:
    """Return (tool_name, readable text) for a tool result.

    Accepts the ATIF observation (a repr of ``{'content': '<json>', 'extra': {...}}``),
    a live ``result`` (the JSON string the harness made), or an already-parsed dict.
    Long string fields are lifted out so their newlines render.
    """
    if obs is None:
        return None, None
    tool = None
    content = _maybe_literal(obs)
    if isinstance(content, dict) and "content" in content and set(content) <= {"content", "extra"}:
        tool = (content.get("extra") or {}).get("tool_name")
        content = _maybe_literal(content.get("content"))
    if isinstance(content, dict):
        parts, rest = [], {}
        for k, v in content.items():
            if isinstance(v, str) and ("\n" in v or len(v) > 200):
                parts.append(f"── {k} ──\n{v}")
            elif isinstance(v, dict) and k == "details":
                for k2, v2 in v.items():
                    if isinstance(v2, str) and v2:
                        parts.append(f"── details.{k2} ──\n{v2}")
                    elif v2 not in ("", None):
                        rest[f"details.{k2}"] = v2
            else:
                rest[k] = v
        head = json.dumps(rest, ensure_ascii=False, default=str) if rest else ""
        return tool, "\n".join([head] + parts) if parts else head
    if isinstance(content, str):
        return tool, content
    return tool, json.dumps(content, ensure_ascii=False, indent=1, default=str)


def fmt_args(args: Any) -> str:
    """Tool arguments as JSON, with multi-line string values (commands, edits) printed raw below."""
    args = _maybe_literal(args)
    if not isinstance(args, dict):
        return args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str)
    short = {k: v for k, v in args.items() if not (isinstance(v, str) and "\n" in v)}
    long_ = [f"── {k} ──\n{v}" for k, v in args.items() if isinstance(v, str) and "\n" in v]
    return "\n".join([json.dumps(short, ensure_ascii=False, default=str)] + long_)


def split_thought(msg: str | None) -> tuple[str | None, str | None]:
    """Gemma may emit '<|channel>thought ... <channel|>answer' as plain text; split it."""
    if not msg:
        return None, msg
    if msg.startswith("<|channel>thought"):
        body = msg[len("<|channel>thought"):].lstrip("\n")
        if "<channel|>" in body:
            th, _, ans = body.partition("<channel|>")
            return th.strip(), ans.strip() or None
        return body.strip(), None
    return None, msg


def arg_summary(args: Any) -> str:
    a = _maybe_literal(args)
    if isinstance(a, dict):
        for k in ("command", "filepath", "path", "file_path", "query", "pattern"):
            if a.get(k):
                v = a[k]
                if k == "file_path" and a.get("args"):
                    v = " ".join(map(str, a["args"]))
                return str(v)[:160]
        if a.get("args"):
            return " ".join(map(str, a["args"]))[:160]
    return ""


# ── ATIF -> entries ──────────────────────────────────────────────────────────
def atif_to_entries(trace: dict) -> list[dict]:
    """Invert adk_eval_core's convert_trace_to_atif closely enough to rebuild spans.

    Per ATIF step: the message (if any) becomes one entry of its event_type, each tool
    call one ``tool_call`` entry, the observation one ``tool_response`` entry. The step's
    token usage goes on its first model entry, which is where the harness attached it.
    """
    out: list[dict] = []
    for s in trace.get("steps", []):
        ex = s.get("extra") or {}
        calls = s.get("tool_calls") or []
        ts = ex.get("timestamp")
        el = ex.get("elapsed_s")
        author = ex.get("author") or ((calls[0].get("extra") or {}).get("author") if calls else "") or ""
        usage = s.get("metrics") or None
        if usage:
            usage = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens") if k in usage}
        ev = ex.get("event_type")
        model = s.get("model_name") or ""
        first = True
        if s.get("message") is not None or (ev and not calls and s.get("observation") is None):
            e = {"timestamp": ts, "elapsed_s": el, "type": ev or ("text" if s.get("source") == "agent" else "custom"),
                 "author": author, "content": s.get("message") or ""}
            if ex.get("metadata") is not None:
                e["metadata"] = ex["metadata"]
            if usage and s.get("source") == "agent":
                e["usage"] = usage
                first = False
            if model and s.get("source") == "agent":
                e["model"] = model
            out.append(e)
        for c in calls:
            cx = c.get("extra") or {}
            e = {"timestamp": cx.get("timestamp", ts), "elapsed_s": cx.get("elapsed_s", el), "type": "tool_call",
                 "author": cx.get("author", author), "tool": c.get("function_name"), "args": c.get("arguments")}
            if first and usage:
                e["usage"] = usage
                first = False
            out.append(e)
        if s.get("observation") is not None:
            obs = s["observation"]
            parsed = _maybe_literal(obs)
            ox = parsed.get("extra", {}) if isinstance(parsed, dict) else {}
            result = parsed.get("content") if isinstance(parsed, dict) and "content" in parsed else obs
            out.append({"timestamp": ox.get("timestamp", ts), "elapsed_s": ox.get("elapsed_s", el),
                        "type": "tool_response", "author": ox.get("author", author),
                        "tool": ox.get("tool_name") or (calls[0].get("function_name") if calls else ""),
                        "result": result if isinstance(result, str) else json.dumps(result, default=str)})
        if first and usage and not calls and s.get("message") is None:
            out.append({"timestamp": ts, "elapsed_s": el, "type": "usage", "author": author, "usage": usage})
    return out


# ── entries -> spans / context / steps ───────────────────────────────────────
def _cat(e: dict) -> str | None:
    t = e.get("type")
    if t in ("task_prompt",):
        return "user"
    if t == "system_instruction":
        return "system+tools"
    if t == "thinking":
        return "thinking"
    if t in ("text", "final", "continuation_nudge"):
        return "assistant" if t != "continuation_nudge" else "user"
    if t == "tool_call":
        return "tool_calls"
    if t == "tool_response":
        return "tool_results"
    if t == "compaction":
        return "summary"
    return None


def _chars(e: dict) -> int:
    t = e.get("type")
    if t == "tool_call":
        a = e.get("args")
        return len(a if isinstance(a, str) else json.dumps(a, default=str)) + len(e.get("tool") or "") + 20
    if t == "tool_response":
        return len(e.get("result") or "")
    return len(e.get("content") or "")


# Thoughts are not replayed to the model on later turns, so they are not counted.
CTX_CATS = ["system+tools", "user", "summary", "assistant", "tool_calls", "tool_results"]


def build_task(entries: list[dict]) -> dict:
    """Spans, context series and steps for one task, from its entries in arrival order."""
    entries = [e for e in entries if e.get("timestamp") is not None]
    if not entries:
        return {"t0": None, "lanes": [], "llm": [], "tools": [], "compactions": [], "steps": [], "stats": {}}
    t0 = min(e["timestamp"] for e in entries)
    rel = lambda ts: round(ts - t0, 3)  # noqa: E731

    # Group model entries of one model response: same author + same timestamp, consecutive.
    groups: list[list[int]] = []
    for i, e in enumerate(entries):
        if e.get("type") in MODEL_TYPES and (e.get("author") or "") not in HARNESS_AUTHORS | {"compactor"}:
            if groups and groups[-1] and i - 1 == groups[-1][-1]:
                p = entries[groups[-1][-1]]
                if p.get("author") == e.get("author") and abs(p["timestamp"] - e["timestamp"]) < 1e-3:
                    groups[-1].append(i)
                    continue
            groups.append([i])

    llm, tools, comps, steps = [], [], [], []
    pending_tools: list[dict] = []
    # Context accounting per author: entries visible to that author's next call.
    hist: dict[str, list[tuple[float, str, int]]] = {}
    shared: list[tuple[float, str, int]] = []  # task prompt / system: visible to every lane
    last_prompt: dict[str, int] = {}
    baseline: dict[str, float] = {}
    prev_ts = entries[0]["timestamp"]
    group_at = {g[0]: g for g in groups}
    i = 0
    while i < len(entries):
        e = entries[i]
        author = e.get("author") or ""
        t = e.get("type")
        if i in group_at:
            g = [entries[j] for j in group_at[i]]
            usage = next((x.get("usage") for x in g if x.get("usage")), None) or {}
            start = prev_ts
            end = e["timestamp"]
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
            compacted = bool(pt and author in last_prompt and last_prompt[author] >= 8000
                             and pt < last_prompt[author] * 0.75)
            # Context estimate for this call. The lane's first call fixes its baseline (system
            # prompt + tool declarations = prompt minus what we can attribute). A prompt drop
            # means the harness compacted: keep the last RETAIN entries and re-measure the
            # baseline (it now holds the summary too). Otherwise, when the attributable history
            # no longer fits next to the baseline, the oldest entries were dropped: prune them.
            ha = hist.setdefault(author, [])
            est_of = lambda: sum(ch for (_, _, ch) in shared + ha) / CHARS_PER_TOKEN  # noqa: E731
            if pt:
                if compacted and not any(h[1] == "summary" for h in ha):
                    del ha[:-RETAIN]
                    baseline[author] = max(0.0, pt - est_of())
                elif author not in baseline:
                    baseline[author] = max(0.0, pt - est_of())
                while ha and est_of() + baseline[author] > pt:
                    ha.pop(0)
            comp = {c: 0 for c in CTX_CATS}
            for (_, cat, ch) in shared + ha:
                comp[cat] = comp.get(cat, 0) + ch / CHARS_PER_TOKEN
            est = sum(comp.values())
            context = None
            if pt:
                if est > pt:
                    scale = pt / est
                    comp = {k: v * scale for k, v in comp.items()}
                else:
                    comp["system+tools"] += pt - est
                context = {k: int(round(v)) for k, v in comp.items()}
            if pt:
                last_prompt[author] = pt
            thought = "\n\n".join(x.get("content") or "" for x in g if x.get("type") == "thinking") or None
            texts = [x.get("content") or "" for x in g if x.get("type") in ("text", "final")]
            msg = "\n\n".join(texts) or None
            if msg and not thought:
                thought, msg = split_thought(msg)
            calls = [x for x in g if x.get("type") == "tool_call"]
            idx = len(llm)
            llm.append({"i": idx, "author": author, "start": rel(start), "end": rel(end),
                        "dur": round(end - start, 2), "pt": pt, "ct": ct,
                        "tps": round(ct / (end - start), 1) if ct and end > start else None,
                        "compacted": compacted, "context": context,
                        "final": any(x.get("type") == "final" for x in g),
                        "calls": [c.get("tool") for c in calls],
                        "summary": (" ; ".join(f"{c.get('tool')} {arg_summary(c.get('args'))}" for c in calls)
                                    or (msg or thought or "")[:200])})
            step = {"kind": "llm", "llm": idx, "author": author, "t": rel(end), "dt": round(end - start, 2),
                    "pt": pt, "ct": ct, "compacted": compacted, "thought": clip(thought), "msg": clip(msg),
                    "calls": []}
            for c in calls:
                tc = {"i": len(tools), "author": author, "fn": c.get("tool"), "start": rel(c["timestamp"]),
                      "end": None, "dur": None, "args": clip(fmt_args(c.get("args"))),
                      "summary": arg_summary(c.get("args")), "result": None, "llm": idx, "status": "running"}
                tools.append(tc)
                pending_tools.append(tc)
                step["calls"].append(tc["i"])
                hist.setdefault(author, []).append((c["timestamp"], "tool_calls", _chars(c)))
            for x in g:
                if x.get("type") in ("text", "final"):
                    hist.setdefault(author, []).append((x["timestamp"], _cat(x), _chars(x)))
            steps.append(step)
            prev_ts = end
            i = group_at[i][-1] + 1
            continue
        if t == "tool_response":
            name = e.get("tool")
            tc = next((p for p in pending_tools if p["fn"] == name), pending_tools[0] if pending_tools else None)
            tool_name, text = parse_observation(e.get("result"))
            if tc is not None:
                pending_tools.remove(tc)
                tc["end"] = rel(e["timestamp"])
                tc["dur"] = round(tc["end"] - tc["start"], 2)
                tc["result"] = clip(text)
                tc["result_chars"] = len(text or "")
                low = (text or "")[:300].lower()
                tc["status"] = "error" if ('"status": "error"' in low or "error_type" in low) else "ok"
            else:
                steps.append({"kind": "orphan_result", "author": author, "t": rel(e["timestamp"]), "dt": 0,
                              "msg": clip(text), "calls": []})
            hist.setdefault(author, []).append((e["timestamp"], "tool_results", _chars(e)))
            prev_ts = e["timestamp"]
        elif t == "compaction":
            meta = e.get("metadata") or {}
            end_ts = meta.get("end_timestamp")
            before = last_prompt.get(author)
            # Entries up to the compacted range's end are replaced by the summary.
            if author in hist and end_ts:
                hist[author] = [h for h in hist[author] if h[0] > end_ts]
            elif author in hist:
                hist[author] = hist[author][-5:]
            hist.setdefault(author, []).append((e["timestamp"], "summary", _chars(e)))
            comps.append({"author": author, "t": rel(e["timestamp"]), "dur": round(e["timestamp"] - prev_ts, 2),
                          "before": before, "summary_chars": len(e.get("content") or ""),
                          "summary": clip(e.get("content"), 4000), "source": "harness"})
            steps.append({"kind": "compaction", "author": author, "t": rel(e["timestamp"]),
                          "dt": round(e["timestamp"] - prev_ts, 2), "msg": clip(e.get("content")), "calls": []})
            prev_ts = e["timestamp"]
        elif t in ("task_prompt", "system_instruction"):
            shared.append((e["timestamp"], _cat(e), _chars(e)))
            steps.append({"kind": t, "author": author or "harness", "t": rel(e["timestamp"]), "dt": 0,
                          "msg": clip(e.get("content")), "calls": []})
        elif t == "usage":
            pass
        else:
            cat = _cat(e)
            if cat in CTX_CATS and author:
                hist.setdefault(author, []).append((e["timestamp"], cat, _chars(e)))
            steps.append({"kind": t or "event", "author": author or "harness", "t": rel(e["timestamp"]),
                          "dt": round(e["timestamp"] - prev_ts, 2), "msg": clip(e.get("content")),
                          "meta": e.get("metadata"), "calls": []})
            prev_ts = e["timestamp"]
        i += 1

    # Inferred compactions (prompt drop) for runs whose trace has no compaction entry.
    harness_comp_authors = {c["author"] for c in comps}
    for k, c in enumerate(llm):
        if c["compacted"] and c["author"] not in harness_comp_authors:
            prev = next((p for p in reversed(llm[:k]) if p["author"] == c["author"] and p["pt"]), None)
            comps.append({"author": c["author"], "t": c["start"], "dur": c["dur"],
                          "before": prev["pt"] if prev else None, "after": c["pt"], "llm": k, "source": "inferred"})
    for c in comps:
        if c.get("after") is None:
            nxt = next((x for x in llm if x["author"] == c["author"] and x["start"] >= c["t"] - 0.01 and x["pt"]), None)
            c["after"] = nxt["pt"] if nxt else None
    comps.sort(key=lambda c: c["t"])

    lanes = []
    for a in dict.fromkeys(x["author"] for x in llm + tools):
        xs = [x for x in llm if x["author"] == a]
        ts_ = [x for x in tools if x["author"] == a]
        starts = [x["start"] for x in xs + ts_]
        ends = [x["end"] for x in xs + ts_ if x.get("end") is not None]
        lanes.append({"author": a, "start": min(starts), "end": max(ends or starts),
                      "llm": len(xs), "tools": len(ts_), "llm_s": round(sum(x["dur"] for x in xs), 1),
                      "tool_s": round(sum(x["dur"] or 0 for x in ts_), 1)})
    last = rel(entries[-1]["timestamp"])
    pts = [x["pt"] for x in llm if x["pt"]]
    stats = {
        "duration": last,
        "llm_calls": len(llm),
        "tool_calls": len(tools),
        "peak_ctx": max(pts) if pts else 0,
        "compactions": len(comps),
        "prompt_tokens": sum(pts),
        "completion_tokens": sum(x["ct"] or 0 for x in llm),
        "llm_s": round(sum(x["dur"] for x in llm), 1),
        "tool_s": round(sum(x["dur"] or 0 for x in tools), 1),
        "slow_s": round(sum(x["dur"] for x in llm if x["dur"] > 30), 1),
        "tool_errors": sum(1 for x in tools if x["status"] == "error"),
        "last_author": llm[-1]["author"] if llm else None,
    }
    return {"t0": t0, "lanes": lanes, "llm": llm, "tools": tools, "compactions": comps,
            "steps": steps, "stats": stats}


def summarize(entries: list[dict]) -> dict:
    """The task-list row numbers only (cheap enough to recompute on each list call)."""
    return build_task(entries)["stats"]
