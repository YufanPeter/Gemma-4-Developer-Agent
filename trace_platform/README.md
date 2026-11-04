# trace_platform — Gemma-4 agent trace platform

Watch a Kaggle dev-eval run live (waterfall, context window, compactions, every step,
GPU / vLLM), browse past runs, share runs as one file, and analyse them from the command
line. Stdlib Python only; charts use a vendored ECharts 5.6.0 (`static/vendor/`, Apache-2.0).

## Quick start

```bash
python -m trace_platform.import_results --all          # past runs from exp_res/results_*/
python -m trace_platform.server --token "$TRACE_TOKEN" # UI http://127.0.0.1:8790/
```

## Live view of a Kaggle run

Kaggle refuses Internet for this competition's L4 kernels ("Internet cannot be enabled for
this competition with the current accelerator"), so the live path goes through the
notebook log:

1. The dev-eval notebook's cell **3b** (`kaggle_push.py`, `TRACE_LOG = True` in the run
   config) hooks `adk_eval_core.tracing.trace.SessionTrace._add` and prints every trace
   entry, task start/end and a GPU / vLLM sample every 10 s as compact
   `@@GT@@ <base64 gzip json>` lines. The submission is untouched; a full queue drops
   and counts, errors are swallowed.
2. On your machine, with the server running:
   ```bash
   TRACE_TOKEN=... python -m trace_platform.kaggle_logs <owner>/<kernel> --fetch
   ```
   follows `kaggle kernels logs -f`, decodes those lines and posts them to the ingest
   port (8791). Replaying the log is idempotent, so restarting it is safe. `--fetch`
   downloads the kernel output when it finishes and imports the final traces
   (reconciliation: tasks whose live copy differs are listed).

Kernels that do allow Internet can POST directly instead: `scripts/trace_live.sh` starts the
server plus a cloudflared quick tunnel to the ingest port only and prints
`TRACE_SINK_URL` / `TRACE_SINK_TOKEN` for the notebook.

After editing `kaggle_push.py`, copy it into the notebook:
`python -m trace_platform.kaggle_push --sync-notebook`.

## Pages

- **Overview**: every run, progress, pass count and why the rest did not pass (outcomes below).
- **Run**: KPIs (pass / done, outcome breakdown, mean task time, ETA against the 12 h
  budget, current task vs the run's own per-task cap), task table (sortable, filterable
  by outcome), **GPU / vLLM**
  (per-GPU util / memory / power, KV-cache %, running / waiting, prefill / decode tok/s,
  task intervals shaded), **distribution** (duration vs peak prompt, time split per task).
- **Task**: **waterfall** (one row per LLM call with its tool calls, compactions shaded,
  sub-agent lanes), **context window** (prompt tokens per call stacked by
  system / task / summary / replies / tool args / tool output, compaction-threshold and
  max_model_len lines, selected-call breakdown, compaction cards), **steps**
  (thoughts, args, outputs), patch, test log.

The prompt total per call is exact (vLLM usage); its split by category is estimated from
character counts (3.6 chars/token, thoughts excluded, history pruned on compaction).

Budget lines come from the run itself: the per-task cap and tool-call budget from
`run_meta.eval_config` (`max_time_minutes`, `max_tool_calls`), the compaction threshold and
max_model_len from `run_meta.limits`. Runs without them (older notebooks, other branches)
fall back to 8 min / 14,336 / 32,768.

## Outcomes: how pass / fail is decided

The platform does not judge patches. `resolved` is the harness's Phase 2 verdict (patch
applied, FAIL_TO_PASS + PASS_TO_PASS all pass), copied by the notebook into the
`dev_results.jsonl` row (or the live `task_end` item). `outcome.py` turns that row into one
label; the first rule that matches wins, and the page, the CLI and the run counts all use it:

| outcome | page | rule |
|---|---|---|
| `PASS` | 通过 | `resolved` |
| `TIMEOUT` | 超时 | `error` mentions a timeout (session wall clock ran out) |
| `BUDGET` | 预算用尽 | `error` says a tool-call / turn budget was exceeded |
| `ERROR` | 报错 | any other `error` string (harness / LLM / notebook exception) |
| `NO_PATCH` | 无补丁 | finished without a patch (`patch_chars == 0`) |
| `ENV_ERROR` | 环境错误 | pytest log errored at collection / setup (missing dep, fixture): the patch was never really tested, not the agent's fault. Same rule as `eval/triage.py` |
| `FAIL` | 测试未过 | patch tested and did not pass |
| `RUNNING` / `UNKNOWN` | 运行中 / 未知 | live task in progress / no result row |

The label is stored per task (`tasks.outcome`) and recomputed automatically when the rules
change (`outcome.VERSION`). `eval/triage.py` remains the tool for the finer failure
buckets (wrong fix vs regression, scratch-only edits, …).

## Runs from other branches

Every branch's dev-eval notebook comes from the same template, so any
`results_<name>/` folder (`dev_results.jsonl`, `run_meta.json`, `traces/`, `patches/`,
`test_outputs/`) imports as is:

```bash
python -m trace_platform.cli add path/to/results_<name>    # or a kernel slug owner/kernel
```

Live view needs the notebook's cell 3b (live trace push) and the `LIVE.*` calls in the
dev-set cell. Without them a run can still be imported once it finishes.

Run names collide: the notebook defaults to `RUN_NAME = 'dev60_baseline'` everywhere and
the run name is the folder name minus `results_`. A run is identified by
`run_meta.started_at_utc` + `submission_fingerprint` (falling back to the results folder
path). If the stored run with that name is a *different* run, it is moved aside to
`<name>@<its start time>` and the new run takes the name; nothing is mixed or lost.
Importing the same run again replaces it (a live run is reconciled instead). Options on
`cli add`, `cli import` and `import_results`: `--run NAME` to pick another name,
`--replace` to overwrite whatever has the name. `cli runs` shows each run's submission
fingerprint so you can tell them apart. A kernel download whose `exp_res/results_<name>/`
already holds another run unpacks to `exp_res/results_<name>__<owner>__<kernel>/`.

## CLI (share + analyse)

```bash
python -m trace_platform.cli add <run | results dir | file.trace.jsonl.gz | owner/kernel>
python -m trace_platform.cli summary <run>
python -m trace_platform.cli tasks <run> --failed
python -m trace_platform.cli show <run> <task> [--full]
python -m trace_platform.cli grep <run> '<regex>' [--in args|results|thoughts]
python -m trace_platform.cli compare <runA> <runB>
python -m trace_platform.cli export <run> -o run.trace.jsonl.gz    # or the "导出 JSONL" button
python -m trace_platform.cli import run.trace.jsonl.gz
```

Claude Code picks this up through `.claude/skills/trace-analysis/`: asking it to analyse a
run (by name, results folder, shared file or kernel slug) is enough.

## Files

| file | role |
|---|---|
| `model.py` | entries (live `TraceEntry` dicts or ATIF steps) → LLM / tool / compaction spans, lanes, context series, steps |
| `outcome.py` | one outcome label per task (PASS / TIMEOUT / BUDGET / ERROR / NO_PATCH / ENV_ERROR / FAIL) |
| `store.py` | SQLite: runs, tasks (with outcome), raw entries, metrics |
| `server.py` | UI + read API + SSE on :8790, token-gated `POST /ingest` on :8791 |
| `import_results.py` | import `exp_res/results_*/` |
| `kaggle_push.py` | notebook side (log or HTTP channel) |
| `kaggle_logs.py` | follow a kernel's log into the platform |
| `cli.py` | share format + text analysis |
| `replay.py` | replay a finished run as if live (tests the live path without a GPU) |
