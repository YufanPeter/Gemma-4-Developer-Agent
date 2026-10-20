# eval/ — dev evaluation + failure triage

Fast, measurable iteration loop for the Gemma-4 Developer Agent submission.
Instead of burning a daily leaderboard submission to learn one number, run a
fixed 60-task dev set on Kaggle and get a **failure breakdown** that says what to
fix next.

## The loop

1. Edit the agent (`submission/` — `agent.yaml`, `sub_agents/`, `prompts/`, `skills/`). No GPU needed locally.
2. Package and validate: `python scripts/package_submission.py` → `dist/submission.zip`
   (prints a fingerprint).
3. Upload `dist/submission.zip` as a new version of your Kaggle Dataset — or do steps 2+3
   in one go with `python scripts/package_submission.py --upload -m "<what changed>"`
   (Kaggle CLI logged in; default dataset `tianyuxu2/gemma-4-agent-submission`, `--dataset` to change).
4. Run `notebooks/getting-started-gemma-4-developer-agent.ipynb` on Kaggle (instructions
   in its first cell; set `RUN_NAME`, run with *Save & Run All*). Check that it prints
   the same submission fingerprint as step 2.
   → produces `results_<RUN_NAME>.zip` in the Output tab.
5. Unzip into `exp_res/results_<RUN_NAME>/` and run triage (stdlib only, no GPU). Pass
   the public `tasks.jsonl` (kept at the repo root, git-ignored) for the
   reference-patch analysis:
   ```bash
   python eval/triage.py --results-dir exp_res/results_<RUN_NAME> --tasks tasks.jsonl
   python eval/trace_stats.py --results-dir exp_res/results_<RUN_NAME> --thinking-budget 4096
   ```
6. Read the breakdown, change **one** variable, go to 1. Use a daily submission only
   to confirm a change that already looked good on the dev set.

## Dev set

`DEV_IDS` in the notebook's run-config cell: 60 of the 129 public tasks, picked
deterministically — stratified by repo in proportion to the public set (fastapi 31 /
rich 22 / requests 6 / httpx 1), and within each repo evenly spaced by `created_at`.
Ordered so every prefix stays repo-balanced, so a run cut short is still a fair
sample. Keep it fixed so runs compare task-by-task; `dev60_baseline` is the reference
run. (`exp_res/results_baseline_1016/` used an older 30-task subset; 13 tasks overlap.)

## Files

- `../notebooks/getting-started-gemma-4-developer-agent.ipynb` — the official starter
  notebook turned into the dev-eval runner: finds your `submission.zip` among the
  attached datasets, validates it, runs the dev set (resumable, per-task crash-safe,
  stops before Kaggle's 12 h limit), and writes the files triage reads plus
  `run_meta.json` (submission fingerprint + config snapshot) and an env probe.
- `../scripts/package_submission.py` — builds and validates `dist/submission.zip`;
  `--upload` pushes it as a new dataset version.
- `../scripts/build_extra_wheels.py` — downloads the extra test deps (+ their deps) as
  Linux/CPython 3.12 wheels into `dist/extra_wheels/`; `--upload` pushes them to
  `tianyuxu2/gemma-4-extra-test-wheels`. Only needed when that package list changes.
- `triage.py` — reads `results/`, prints + writes the failure breakdown.
- `refdiff.py` — agent patch vs reference patch: localization (`same_lines` /
  `same_region` / `same_file` / `wrong_file` / `scratch_only` / `no_edit`), missed
  reference files, and the target tests `test_patch` adds. Used by `triage.py --tasks`.
- `trace_stats.py` — per-task budget usage from `traces/`: first source-edit call,
  read count, scratch-script writes, thinking-saturated steps, decode speed.

## What triage reads (`results/`)

```
results/
├── dev_results.jsonl          # one JSON row per task (schema below) — written by the notebook
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
| `scratch_only` | patch only adds new scratch files (repro.py …), no source edit → same as no patch |
| `broken_code` | patched source no longer parses (SyntaxError/IndentationError at import) → edit hygiene |
| `env_error` | pytest failed at collection/setup for reasons unrelated to the patch → fix env or drop task |
| `wrong_fix` | patch applied, target test still fails → wrong location / wrong fix |
| `regression` | target passes, other tests break → edits not minimal enough |
| `apply_fail` | patch couldn't apply in Phase 2 → diff hygiene, avoid truncated edits |
| `patch_rejected` | not resolved, couldn't auto-classify → open the linked log |

Without `--tasks`, `wrong_fix`/`regression` need `fail_to_pass` in the results,
which the harness does not provide — most such tasks then land in `patch_rejected`.

## Notes

- Test environment: the notebook's cell 2c (`FIX_TEST_ENV = True`) installs the test
  deps the Kaggle image lacks (inline_snapshot, dirty_equals, pytest-httpbin, trustme)
  from the `tianyuxu2/gemma-4-extra-test-wheels` dataset (built by
  `scripts/build_extra_wheels.py`) — attach it — and adds a
  src-layout hook so psf/requests tests import the patched workspace. With it the
  reference patches pass on 114/129 tasks (74/129 without); the 1016/1017 baselines ran
  without it, so their `env_error` bucket (18/60 in 1017) is mostly env, not agent.
  Still broken even with the fix (dev set): fastapi_15745, fastapi_15785,
  requests_6589, requests_6629.
- Train repos (fastapi/rich/requests/httpx) ≠ the private test repos. The subset
  measures the agent *method*, not memorized repo facts — watch the by-repo line
  for over-fit to one repo.
- Kaggle free GPU ≈ 30 h/week and **L4x4 counts double**, so ~15 wall-clock h/week.
  The 60-task dev set takes ~6-8 h (= 12-16 h of quota); keep daily submissions for confirmation.
