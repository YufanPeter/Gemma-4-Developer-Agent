# eval/ — dev evaluation + failure triage

Fast, measurable iteration loop for the Gemma-4 Developer Agent submission.
Instead of burning a daily leaderboard submission to learn one number, run a
fixed 30-task subset on Kaggle and get a **failure breakdown** that says what to
fix next.

## The loop

1. Edit the agent (`submission/` — `agent.yaml`, `sub_agents/`, `prompts/`, `skills/`). No GPU needed locally.
2. Upload `submission/` as a Kaggle Dataset (first time via the web UI; later `kaggle datasets version -p submission -m "msg"`).
3. In the Kaggle notebook (L4x4, competition + `google/gemma-4` model + wheelhouse attached):
   paste **CELL A** and **CELL B** from `notebook_cells.py` after the vLLM-server cell, and run.
   → produces `results/` on `/kaggle/working`.
4. Run triage (anywhere — stdlib only, no GPU):
   ```bash
   python eval/triage.py --results-dir results/
   ```
5. Read the breakdown, change **one** variable, go to 1. Use a full-129 run or a
   daily submission only to confirm a change that already looked good on the subset.

## Files

- `notebook_cells.py` — CELL A (deterministic stratified 30-task subset, fixed seed
  so runs are comparable) + CELL B (eval loop that writes the files triage reads).
  CELL B reuses the stock notebook's `EvalConfig`/`Evaluator`; only task selection
  and per-task logging changed.
- `triage.py` — reads `results/`, prints + writes the failure breakdown.

## What triage reads (`results/`)

```
results/
├── dev_results.jsonl          # one JSON row per task (schema below) — written by CELL B
├── patches/<id>.patch         # agent diff (optional)
└── test_outputs/<id>.log      # phase-2 pytest output (optional, enables wrong_fix vs regression)
```

`dev_results.jsonl` row schema:

| field | meaning |
|---|---|
| `id`, `repo` | task id, source repo |
| `resolved` | bool — passed Phase-2 verification |
| `test_exit_code` | Phase-2 pytest exit code |
| `patch_chars` | size of agent patch (0 == NO_PATCH) |
| `tool_calls`, `max_tool_calls` | budget used / allowed |
| `duration_seconds`, `max_time_minutes` | time used / allowed |
| `error` | harness error string (e.g. apply failure) |
| `fail_to_pass`, `pass_to_pass` | target vs regression tests (if task exposes them) |
| `test_log` | relative path to the saved pytest log |

If `dev_results.jsonl` is absent, triage falls back to the harness CLI's
`task_results.jsonl` with best-effort field mapping.

## Failure buckets

| bucket | meaning → what to work on |
|---|---|
| `no_patch_budget` | no patch, ran out of tool-calls/time → localization efficiency; raise `max_tool_calls`, explore faster |
| `no_patch_gaveup` | no patch, budget left → model stopped early; prompt it to always submit |
| `wrong_fix` | patch applied, target test still fails → wrong location / wrong fix |
| `regression` | target passes, other tests break → edits not minimal enough |
| `apply_fail` | patch couldn't apply in Phase 2 → diff hygiene, avoid truncated edits |
| `patch_rejected` | not resolved, couldn't auto-classify → open the linked log |

## Try it without a run

A synthetic `results/` and its triage output live in `_demo/` so you can see the
report format before the first Kaggle run:

```bash
python eval/triage.py --results-dir eval/_demo/results
```

## Notes

- Train repos (fastapi/rich/requests/httpx) ≠ the private test repos. The subset
  measures the agent *method*, not memorized repo facts — watch the by-repo line
  for over-fit to one repo.
- Kaggle free GPU ≈ 30 h/week and **L4x4 counts double**, so ~15 wall-clock h/week.
  A 30-task subset (~2 h) is the dev unit; keep the 12-h full run for confirmation.
