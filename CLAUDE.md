# Gemma 4 Developer Agent — project notes for Claude

## The task
Kaggle "Gemma 4 Developer Agent" competition. We submit a declarative ADK agent (`submission/` → `dist/submission.zip`) that the `swegemma` harness runs on ~120 hidden SWE tasks from **private repos** (public set: 129 tasks from fastapi / rich / requests / httpx). A task scores if the agent's patch passes the hidden tests (fail-to-pass + whole suite passes). Score = fraction resolved.

Hard limits (see `docs/overview.md`, `docs/data_description.md`, `docs/HARNESS_README.md`):
- Model: only `gemma-4-31b-it-qat-w4a16-ct`, for every agent and sub-agent. `max_model_len` 32,768 per request.
- 12 h total for all tasks (setup included, validation excluded). Per-task limits are ours, in `submission/eval_config.yaml` (now 28 tool calls, 8 min, 240 s per command, 80 turns).
- Offline. Tools: only the harness tools, skills (`SKILL.md` dirs), and sub-agents via `agent_tool`.
- Zip: `agent.yaml` at the root, extensions `.yaml .yml .md .txt .py .json .safetensors`, no symlinks. `instruction: !include` takes one file; there is no `global_instruction`.
- 1 leaderboard submission per day. Generalize: no repo-specific hacks for the four public repos.

## Repo layout
- `submission/` — the agent: `agent.yaml`, `eval_config.yaml`, `configs/sampling.yaml`, `prompts/system.md`.
- `scripts/package_submission.py` — builds and validates `dist/submission.zip`, prints a fingerprint.
- `notebooks/getting-started-gemma-4-developer-agent.ipynb` — Kaggle dev-eval runner (fixed 60-task `DEV_IDS`).
- `eval/` — `triage.py`, `trace_stats.py`, `refdiff.py` (stdlib only); see `eval/README.md`.
- `exp_res/results_<run>/` — one folder per dev run (patches, traces, logs, test_outputs, triage). `results_baseline_1016` is an older 30-task run.
- `tasks.jsonl` at the root is the public set (git-ignored).

## How we work
- **Discuss before editing.** Propose changes (show the diff) and wait for approval before touching `submission/` or adding files.
- Experiments live on branch `yufanshi_exp`. Merge to `main` only after a dev60 run beats the baseline, and only when asked. Push only when asked.
- Spend a daily submission only on a version that already improved on dev60.
- Prefer measured changes: change one thing, run dev60, compare task by task.

## Dev-eval loop (Kaggle)
1. `python scripts/package_submission.py` → note the fingerprint.
2. Upload `dist/submission.zip` as a private Kaggle dataset (e.g. `yufanshi28/prompt-v2`; Kaggle auto-extracts it, which works).
3. Use the user's existing dev notebook `yufanshi28/getting-started-gemma-4-developer-agent` (pinned to the original environment, Python 3.12). A brand-new notebook gets Python 3.13 and cannot install the cp312 wheelhouse. Import the repo notebook with File → Import Notebook; earlier versions stay intact in Version history.
4. Inputs: competition data, the Gemma 4 model, `metric/gemma-4-developer-agent-wheelhouse`, the submission dataset, `yufanshi28/swe-dev-extras`. Accelerator GPU L4x4, Internet off. Set `RUN_NAME` and, if several submissions are attached, `SUBMISSION_INPUT`.
5. Save Version → Save & Run All (Commit). About 6–8 h; L4x4 quota counts double (~30 h/week free). Check the log prints the same fingerprint and `Extra test deps installed from ...`.
6. Download `results_<RUN_NAME>.zip`, unzip into `exp_res/results_<RUN_NAME>/`, then:
   ```bash
   python eval/triage.py --results-dir exp_res/results_<RUN_NAME> --tasks tasks.jsonl
   python eval/trace_stats.py --results-dir exp_res/results_<RUN_NAME> --thinking-budget 4096
   ```

`swe-dev-extras/extra-wheels` supplies `inline_snapshot`, `dirty_equals`, `pytest_httpbin`, which the competition wheels lack. Without them 18 dev tasks land in `env_error`. These notebook changes affect dev eval only, never the submission.

## Harness facts (verified in swegemma 0.2.11 / adk_submission 0.2.13 / google_adk 1.36.1)
The wheels are in `~/Downloads/*.whl` (or unzip them from the wheelhouse dataset).
- YAML callbacks are silently ignored: `compile_submission` is called without a `callback_registry`. No harness-side hooks are possible.
- Only `@budget_gated` tools count toward `max_tool_calls` (`run_command`, `read_file`, `write_file`, `edit_file`, graph tools). `submit_patch` and `get_status` do not. Skill tools (`load_skill`, `run_skill_script`) do not count but cost turns and time; `run_skill_script` runs in the agent sandbox.
- Agent loop: a final response ends the turn only after `submit_patch`; otherwise the harness nudges (up to 3 times) and reruns. The App is not resumable, so a `SequentialAgent` root restarts from its first sub-agent.
- `AgentTool` sub-agents get a fresh Runner: no context compaction, no turn cap. The old `code_analyzer` sub-agent overflowed 32k context. `skip_summarization: true` ends the caller's turn.
- `SequentialAgent` sub-agents share the invocation context (turn cap and compaction apply). With `include_contents: none` the issue text must come via `{problem_description}`.
- Context compaction triggers around 14,336 tokens.
- `edit_file` matches exact → whitespace-flexible → regex → unescaped. Gemma's tool-call format delimits strings with `<|"|>`; many quotes/backslashes make the args fail to parse (`mandatory input parameters are not present`).
- `run_command` returns `CommandError` for any non-zero exit, so `grep` with no match looks like an error.
- `submit_patch` runs `git add -N .`, so untracked scratch files end up in the patch; test files are stripped.

## Status (2026-10-19)
| version | fingerprint | dev60 | leaderboard |
|---|---|---|---|
| baseline (`main`) | `dd3f43378887` | 12/60 raw, **19/60** with extra deps | 0.17 |
| prompt v1: ROLE/RULES/WORKFLOW (`c782c3b`) | `1d11a450f1c0` | not run | 0.17 |
| prompt v2: rule-conflict and edit-template fixes (`52652ec`) | `beb0abedf101` | run `prompt_v2_1019_60dev` in progress (notebook Version 2) | not submitted |

Baseline findings (`exp_res/results_baseline_1017_60dev/triage_report.md`):
- Late editing: median first source edit at call 19; 10 of 13 no-patch tasks first edited at calls 29–30. `budget_warning` mostly ignored.
- 151 duplicate calls (120 after compaction); 31% of `edit_file` calls had malformed args; many retries of identical failing calls.
- ~5.4 min per task, close to the 12 h limit at 120 tasks.

Lesson so far: the baseline prompt already demanded an edit by call 12 and the model ignored it; prompt rewording alone did not move the leaderboard. Next directions:
1. Execution aids via skills (do not consume `max_tool_calls`), e.g. a locate script that returns ranked candidate regions in one call.
2. Structure: `SequentialAgent` locator → editor (→ reviewer that submits), keeping each step small.
Compare every new run against 19/60, task by task.
