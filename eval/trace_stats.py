#!/usr/bin/env python3
"""
trace_stats.py -- how the agent spent its budget, per task, from the ATIF traces.

triage.py says WHAT went wrong; this says HOW the time and tool calls were
spent: when the first source edit happened, how many calls went to reading,
whether thinking hit the budget (slow steps), and how often the agent wrote
scratch scripts. Stdlib only.

Usage:
    python eval/trace_stats.py --results-dir exp_res/results_baseline_1016
    python eval/trace_stats.py --results-dir results/ --thinking-budget 4096
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st
from collections import Counter
from pathlib import Path

EDIT_TOOLS = {"edit_file", "write_file"}
SCRATCH_HINTS = ("repro", "reproduce", "check_", "verify_", "debug", "scratch")


def _short(fn: str, args: dict) -> str:
    if fn == "run_command":
        parts = (args.get("command") or "").split()
        return "rc:" + (parts[0][:8] if parts else "")
    return {"read_file": "read", "edit_file": "edit", "write_file": "writ",
            "submit_patch": "SUBMIT", "get_status": "stat"}.get(fn, fn[:6])


def _is_source_edit(fn: str, args: dict) -> bool:
    if fn in EDIT_TOOLS:
        path = os.path.basename(args.get("filepath") or "")
        return not any(h in path for h in SCRATCH_HINTS)
    if fn == "run_command":
        cmd = args.get("command") or ""
        return "sed -i" in cmd or (".write_text(" in cmd) or ("open(" in cmd and "'w'" in cmd)
    return False


def analyze(trace: dict, budget_tokens: int) -> dict:
    calls = 0
    first_edit_call = first_edit_s = None
    prev_el = 0.0
    step_secs, comps, seq = [], [], []
    tools = Counter()
    scratch_writes = 0
    max_prompt = 0
    for s in trace.get("steps", []):
        if s.get("source") != "agent":
            continue
        el = (s.get("extra") or {}).get("elapsed_s", prev_el)
        m = s.get("metrics") or {}
        if m.get("completion_tokens") is not None:
            comps.append(m["completion_tokens"])
            step_secs.append(el - prev_el)
        max_prompt = max(max_prompt, m.get("prompt_tokens") or 0)
        prev_el = el
        for tc in s.get("tool_calls") or []:
            fn, args = tc.get("function_name", "?"), tc.get("arguments") or {}
            tools[fn] += 1
            if fn not in ("submit_patch", "get_status"):  # free calls
                calls += 1
            seq.append(_short(fn, args))
            if fn == "write_file" and any(h in os.path.basename(args.get("filepath") or "") for h in SCRATCH_HINTS):
                scratch_writes += 1
            if first_edit_call is None and _is_source_edit(fn, args):
                first_edit_call, first_edit_s = calls, el
    sat = sum(c >= 0.95 * budget_tokens for c in comps)
    return {
        "calls": calls,
        "first_edit_call": first_edit_call,
        "first_edit_s": first_edit_s,
        "reads": tools["read_file"],
        "scratch_writes": scratch_writes,
        "llm_steps": len(comps),
        "mean_completion": st.mean(comps) if comps else 0,
        "saturated_steps": sat,
        "slowest_step_s": max(step_secs) if step_secs else 0,
        "max_prompt": max_prompt,
        "tools": tools,
        "seq": seq,
        "step_secs": step_secs,
        "comps": comps,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-task budget usage from swegemma traces.")
    ap.add_argument("--results-dir", default="results", type=Path)
    ap.add_argument("--thinking-budget", default=2048, type=int,
                    help="thinking_budget the run used; steps near it count as saturated")
    ap.add_argument("--no-seq", action="store_true", help="hide the per-task tool sequence")
    args = ap.parse_args()

    rd = args.results_dir
    rows = {}
    dev = rd / "dev_results.jsonl"
    if dev.exists():
        rows = {d["id"]: d for d in map(json.loads, filter(str.strip, dev.open()))}
    triage = {}
    if (rd / "triage.json").exists():
        triage = {d["id"]: d["bucket"] for d in json.load((rd / "triage.json").open())}

    all_tools, secs, comps, per = Counter(), [], [], []
    print(f"{'id':<15}{'bucket':<17}{'calls':>5}{'dur':>5}{'edit@call':>10}{'edit@s':>7}"
          f"{'reads':>6}{'scr':>4}{'sat':>4}{'maxPrompt':>10}")
    for f in sorted(glob.glob(str(rd / "traces" / "trace_*.json"))):
        tid = os.path.basename(f)[len("trace_"):-len(".json")]
        a = analyze(json.load(open(f)), args.thinking_budget)
        per.append(a)
        all_tools.update(a["tools"])
        secs += a["step_secs"]
        comps += a["comps"]
        r = rows.get(tid, {})
        fe_s = f"{a['first_edit_s']:.0f}" if a["first_edit_s"] is not None else "-"
        print(f"{tid:<15}{triage.get(tid, '?'):<17}{a['calls']:>5}{(r.get('duration_seconds') or 0):>5.0f}"
              f"{str(a['first_edit_call'] or '-'):>10}{fe_s:>7}{a['reads']:>6}{a['scratch_writes']:>4}"
              f"{a['saturated_steps']:>4}{a['max_prompt']:>10}")
        if not args.no_seq:
            print("    " + " ".join(a["seq"]))

    if not per:
        raise SystemExit(f"no traces under {rd / 'traces'}")
    print()
    print("tool usage:", dict(all_tools.most_common()))
    edits = [a["first_edit_call"] for a in per if a["first_edit_call"]]
    print(f"tasks with a source edit: {len(edits)}/{len(per)}; median first-edit call: "
          f"{st.median(edits) if edits else '-'}")
    print(f"LLM steps: {len(comps)}; median s/step: {st.median(secs):.1f}; mean completion tokens: "
          f"{st.mean(comps):.0f}; saturated (>=95% of {args.thinking_budget}): "
          f"{sum(a['saturated_steps'] for a in per)}")
    slow = [(s, c) for s, c in zip(secs, comps) if s > 5]
    if slow:
        print(f"approx decode speed: {sum(c for _, c in slow) / sum(s for s, _ in slow):.1f} tok/s "
              f"(=> a saturated {args.thinking_budget}-token step costs "
              f"~{args.thinking_budget / (sum(c for _, c in slow) / sum(s for s, _ in slow)):.0f}s)")
    print(f"scratch-file writes: {sum(a['scratch_writes'] for a in per)} across "
          f"{sum(1 for a in per if a['scratch_writes'])} tasks")


if __name__ == "__main__":
    main()
