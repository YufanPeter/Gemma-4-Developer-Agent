---
name: trace-analysis
description: Analyse a Gemma-4 dev-eval run's agent traces with the local trace platform. Use when the user asks to look at / analyse / debug / compare a run (e.g. "分析 dev60_e5_pipeline 的 trace", "看看 probe_live_e7 哪里有问题", "E5 和 E1 比一下"), hands over a shared .trace.jsonl.gz, names a Kaggle kernel whose run should be pulled, or wants to follow a running Kaggle eval live.
---

# Trace analysis with trace_platform

All commands run from the repo root, stdlib Python only. Data lives in
`trace_platform/data/traces.db` (git-ignored). The CLI reads it directly; the web UI
(`python -m trace_platform.server`, http://127.0.0.1:8790) is for the user to look at.

## 1. Get the run in (one command, whatever the user handed you)

```bash
python -m trace_platform.cli add <source>
```
`<source>` can be: a run already stored (`python -m trace_platform.cli runs` lists them),
a results folder (`exp_res/results_X/` or just `X` if `exp_res/results_X/` exists), a
shared file (`*.trace.jsonl.gz`), or a Kaggle kernel slug `owner/kernel` (downloads its
output, unzips into `exp_res/`, imports). It prints the run name to use below.

Same name, different run (every branch's notebook defaults to `RUN_NAME='dev60_baseline'`):
the stored run is moved aside to `<run>@<started_at_utc>` and the new one takes the name;
`cli runs` shows both with their submission fingerprint. `--replace` overwrites instead,
`--run NAME` imports under another name.

Running kernel (not finished yet)? Follow it live instead -- needs the server running
with a token and `TRACE_TOKEN` set to the same value:
```bash
python -m trace_platform.kaggle_logs owner/kernel --fetch     # run in the background
```

## 2. Read it top-down

1. `python -m trace_platform.cli summary <run>` -- outcome counts, error
   messages, distributions (duration, LLM/tool calls, first-edit time, peak prompt,
   compactions, time in >30 s calls, decode tok/s, repeated identical calls), tasks with
   no source edit, time per sub-agent, tool error counts, which stage timeouts ended in,
   GPU/KV-cache if live metrics exist.
2. `python -m trace_platform.cli tasks <run> --failed` -- one line per failing task.
3. Pick 2-4 representative failures (one per pattern) and
   `python -m trace_platform.cli show <run> <task>` -- per LLM call: time, sub-agent,
   prompt/output tokens, tool + argument, result size/status, compactions. Add `--full`
   for thoughts, full args/outputs and the patch (`--max-chars` to bound it).
4. `python -m trace_platform.cli grep <run> '<regex>' [--in args|results|thoughts]` --
   check a hypothesis across all tasks (e.g. `'pip install'`, `'ModuleNotFoundError'`,
   `'old_string not found'`).
5. `python -m trace_platform.cli compare <runA> <runB>` -- tasks that flipped, with
   duration / LLM calls / peak prompt before and after.
6. For reference-patch localisation (right file? right lines?) also run
   `python eval/triage.py --results-dir exp_res/results_<run> --tasks tasks.jsonl`.

Outcomes (`trace_platform/outcome.py`, first match wins): PASS = harness Phase 2
resolved; TIMEOUT = session wall clock; BUDGET = tool-call / turn budget; ERROR = other
error string; NO_PATCH; ENV_ERROR = pytest errored at collection/setup, the patch was
never really tested (env, not agent -- report pass rate with and without them); FAIL =
patch tested and failed.

## 3. What to report

Lead with the pattern that costs the most tasks, with counts and 1-2 task ids as
evidence (quote the step: "#7 run_command git log --rich -> exit 128"). Usual suspects in
this repo: time runs out before the first edit (locate overspends; >30 s thinking steps
at ~35 tok/s), compaction at 14,336 tokens followed by re-reads, repeated identical
calls, edit_file `old_string not found` loops, env errors in tests, no edit at all.
A difference of ±3 tasks on dev60 is noise. Give the user the UI link for a task:
`http://127.0.0.1:8790/#/run/<run>/task/<task>`.

## 4. Share

`python -m trace_platform.cli export <run> -o <run>.trace.jsonl.gz` (also the "导出 JSONL"
button on the run page); the receiver runs `python -m trace_platform.cli import <file>`
or `add <file>`.
