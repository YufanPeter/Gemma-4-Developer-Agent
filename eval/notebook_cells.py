# =============================================================================
# Dev-eval cells for the Gemma-4 Developer Agent competition.
#
# These REPLACE the sample loop in the getting-started notebook so that a run
# produces exactly the files eval/triage.py expects:
#     results/dev_results.jsonl      (one row per task, known schema)
#     results/patches/<id>.patch
#     results/test_outputs/<id>.log
#
# Paste CELL A and CELL B into the notebook AFTER the vLLM server cell (the one
# that defines `models`, `tasks`, `AGENT_DIR`, `TASKS_PATH`, `DATA_DIR`,
# `GRAPH_DIR`, `EMBEDDINGS_DIR`). CELL B reuses the `EvalConfig` / `Evaluator`
# wiring from the original notebook cell 10 -- only the task selection and the
# per-task logging are changed.
# =============================================================================


# ===== CELL A: deterministic, stratified 30-task dev subset ==================
import random

DEV_SUBSET_SIZE = 30
DEV_SEED = 1234  # fixed => same tasks every run => changes are comparable

# group tasks by repo so all 4 repos are represented, not just whichever sort first
_by_repo = {}
for t in tasks:
    _by_repo.setdefault(t.repo, []).append(t)

_rng = random.Random(DEV_SEED)
_per_repo = max(1, DEV_SUBSET_SIZE // len(_by_repo))
DEV_TASKS = []
for repo in sorted(_by_repo):
    pool = sorted(_by_repo[repo], key=lambda t: t.instance_id)
    _rng.shuffle(pool)
    DEV_TASKS.extend(pool[:_per_repo])

# top up / trim to exactly DEV_SUBSET_SIZE
if len(DEV_TASKS) < DEV_SUBSET_SIZE:
    rest = [t for t in tasks if t not in DEV_TASKS]
    _rng.shuffle(rest)
    DEV_TASKS.extend(rest[: DEV_SUBSET_SIZE - len(DEV_TASKS)])
DEV_TASKS = DEV_TASKS[:DEV_SUBSET_SIZE]

print(f"dev subset: {len(DEV_TASKS)} tasks")
from collections import Counter
for repo, n in Counter(t.repo for t in DEV_TASKS).items():
    print(f"  {repo}: {n}")
# (optional) freeze the ids so you can reproduce exactly even if data changes:
# print([t.instance_id for t in DEV_TASKS])


# ===== CELL B: run the subset and write triage-ready results =================
import json
import shutil
from pathlib import Path

import pandas as pd
import yaml
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps._configs import EventsCompactionConfig
from swegemma.config import EvalConfig, build_submission_limits
from swegemma.evaluate import Evaluator

# --- read the submission's eval_config.yaml (same as the stock notebook) -----
eval_config_file = next(
    (c for name in ("eval_config.yaml", "eval_config.yml") if (c := AGENT_DIR / name).is_file()),
    None,
)
raw_eval_cfg = (
    yaml.safe_load(eval_config_file.read_text(encoding="utf-8")) or {}
    if eval_config_file is not None
    else {}
)
eval_section = raw_eval_cfg.get("evaluation", raw_eval_cfg)
timeout_seconds = int(eval_section.get("timeout_seconds", 300))
verification_timeout_seconds = int(eval_section.get("verification_timeout_seconds", 300))
max_tool_calls = int(eval_section.get("max_tool_calls", 100))
max_time_minutes = float(eval_section.get("max_time_minutes", 60.0))
turns_raw = eval_section.get("max_turns", eval_section.get("max_llm_calls"))
max_turns = int(turns_raw) if turns_raw is not None else None

# --- results dir layout that triage.py reads ---------------------------------
RESULTS_DIR = WORKING_DIR / "results"
(RESULTS_DIR / "patches").mkdir(parents=True, exist_ok=True)
(RESULTS_DIR / "test_outputs").mkdir(parents=True, exist_ok=True)
DEV_JSONL = RESULTS_DIR / "dev_results.jsonl"
DEV_JSONL.write_text("", encoding="utf-8")  # fresh each run

limits, gen_constraints = build_submission_limits()
eval_config = EvalConfig(
    tasks_path=TASKS_PATH,
    snapshots_dir=DATA_DIR / "snapshots",
    results_dir=RESULTS_DIR,
    submission_dir=AGENT_DIR,
    models=models,
    sandbox="subprocess",
    timeout_seconds=timeout_seconds,
    verification_timeout_seconds=verification_timeout_seconds,
    max_time_minutes=max_time_minutes,
    max_tool_calls=max_tool_calls,
    max_turns=max_turns,
    limits=limits,
    generation_constraints=gen_constraints,
    adapter_manifest=adapters,
    context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=15, overlap_size=2, token_threshold=14336, event_retention_size=5
    ),
    graph_dir=GRAPH_DIR,
    embeddings_dir=EMBEDDINGS_DIR,
    wheels_dir=DATA_DIR / "wheels",
    verbose=False,
)
evaluator = Evaluator(eval_config)


def _maybe(obj, *names):
    """Best-effort attribute read -- harness result field names may vary."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


predictions = []
for idx, task in enumerate(DEV_TASKS, start=1):
    print(f"[{idx}/{len(DEV_TASKS)}] {task.instance_id} ({task.repo}) ...")
    result = run_sync(  # run_sync is defined in the stock notebook cell 10
        evaluator.evaluate_task, task=task, task_index=idx, total_tasks=len(DEV_TASKS)
    )
    patch = (_maybe(result, "agent_patch", "patch") or "").strip()

    # save the patch
    if patch and patch != "NO_PATCH":
        (RESULTS_DIR / "patches" / f"{task.instance_id}.patch").write_text(patch, encoding="utf-8")

    # save the phase-2 pytest log if the result object carries it
    test_log = _maybe(result, "test_output", "test_log", "verification_output", "pytest_output")
    test_log_rel = None
    if test_log:
        rel = Path("test_outputs") / f"{task.instance_id}.log"
        (RESULTS_DIR / rel).write_text(str(test_log), encoding="utf-8")
        test_log_rel = str(rel)

    row = {
        "id": task.instance_id,
        "repo": task.repo,
        "resolved": bool(_maybe(result, "resolved") or False),
        "test_exit_code": _maybe(result, "test_exit_code", "exit_code"),
        "patch_chars": len(patch) if patch and patch != "NO_PATCH" else 0,
        "tool_calls": _maybe(result, "tool_calls"),
        "duration_seconds": _maybe(result, "duration_seconds", "duration"),
        "max_tool_calls": max_tool_calls,
        "max_time_minutes": max_time_minutes,
        "error": _maybe(result, "error", "agent_error") or "",
        # these help triage separate wrong_fix vs regression; harmless if absent
        "fail_to_pass": _maybe(task, "fail_to_pass", "FAIL_TO_PASS"),
        "pass_to_pass": _maybe(task, "pass_to_pass", "PASS_TO_PASS"),
        "test_log": test_log_rel,
    }
    with DEV_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

    predictions.append({"id": task.instance_id, "prediction": patch if patch else "NO_PATCH"})
    print(f"    resolved={row['resolved']} patch_chars={row['patch_chars']} "
          f"tool_calls={row['tool_calls']} dur={row['duration_seconds']}")

pd.DataFrame(predictions, columns=["id", "prediction"]).to_parquet(RESULTS_DIR / "submission.parquet")
print(f"\nwrote {DEV_JSONL}")
print("now run:  python eval/triage.py --results-dir", RESULTS_DIR)


# ===== CELL C (optional): run triage inline in the notebook ==================
# import subprocess, sys
# subprocess.run([sys.executable, "eval/triage.py", "--results-dir", str(RESULTS_DIR)], check=True)
