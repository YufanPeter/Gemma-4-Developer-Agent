# Dataset Description — Kaggle Gemma 4 SWE Agent Competition

> **Purpose of this file:** a reference for AI coding agents helping develop a submission for the Kaggle Google Gemma 4 software-engineering agent competition. It describes what data is available, how it is structured, how tasks are graded, and what the submission must produce.

---

## 1. Competition Overview

- **Goal:** Build an AI software-engineering agent that solves real-world **Python bug fixes and feature requests**.
- **Input per task:** a repository snapshot at a specific commit + a natural-language issue (`problem_statement`, optional `hints_text`).
- **Output per task:** a unified git diff patch (relative to `base_commit`) that resolves the issue.
- **Navigation aids provided:** in addition to normal repo exploration (shell, git, file reads), the competition provides:
  - **Code graphs** (AST call/dependency graphs)
  - **Graph/node embeddings** (for semantic code retrieval)
  - **Specialized graph tools:** `get_code_neighbors`, `get_code_subgraph`, `search_similar_code`

### Train vs. Test

| Split | Visible to participants? | Size | Source repos |
|---|---|---|---|
| Training (public dev set) | Yes | 129 tasks | `fastapi/fastapi`, `Textualize/rich`, `psf/requests`, `encode/httpx` |
| Test (hidden) | No | ~120 tasks, split evenly into public / private leaderboard | Curated from **private repositories** |

- The training set is for local agent development, prompt engineering, and offline validation.
- At scoring time, the training set is **replaced by the hidden test set**, produced with the same curation pipeline, identical graph generation, and the same verification standards.
- **Implication:** the agent must generalize to unseen codebases. Do not overfit to the four public repos (e.g. hard-coded paths, repo-specific heuristics).

---

## 2. Files and Directories

```
.
├── tasks.jsonl            # 129 task definitions (with reference patch + tests in public set)
├── snapshots/             # 129 .tgz git repos frozen at base_commit
├── graphs/                # NetworkX AST call/dependency graphs (JSON)
├── embeddings/            # Per-node 256-d embeddings (.npz)
├── wheels/                # 124 offline Python wheels (mounted at /wheels/)
├── docker/                # Sandbox container build files + stdlib shims
├── sandbox/setup.py       # Per-task sandbox initialization script
├── sample_submission/     # Baseline ADK submission example
├── HARNESS_README.md      # Detailed harness / tooling / CLI guide
└── submission.parquet     # Produced automatically during evaluation
```

### 2.1 `tasks.jsonl`

Line-delimited JSON; one object per task (129 in the public set).

| Field | Description |
|---|---|
| `instance_id` | Unique task ID: repo short name + issue/PR number (e.g. `fastapi_11194`, `rich_3454`). |
| `repo` | Source GitHub repo in `owner/repo` format (`fastapi/fastapi`, `Textualize/rich`, `psf/requests`, `encode/httpx`). |
| `base_commit` | 40-char hex SHA of the repo snapshot immediately before the fix. |
| `problem_statement` | Natural-language bug report / feature request — **the agent's task prompt**. |
| `hints_text` | Optional issue comments or maintainer hints (may be empty). |
| `patch` | Reference fix as a unified git diff. **Public set only** — excluded from the hidden test set (`rerun/tasks.jsonl`). |
| `test_patch` | Unified diff adding the verification tests run by pytest in grading. **Public set only** — kept in private `secret/solution.parquet` during scoring. |
| `created_at` | ISO-8601 timestamp of issue/PR creation. |

> ⚠️ **Never give `patch` or `test_patch` to the agent at inference time.** They exist only for local evaluation. The agent sees only `problem_statement`, `hints_text`, and the repo.

### 2.2 `snapshots/`

- 129 compressed archives: `snapshots/<instance_id>.tgz` (e.g. `fastapi_11194.tgz`, `requests_7205.tgz`, `rich_2725.tgz`, `httpx_3672.tgz`).
- Each contains a **full Git repository frozen at `base_commit`**, with forward commit history removed.
- Agents can safely run `git status`, `git log`, `git diff` inside `/workspace` without leaking the future fix.

### 2.3 `graphs/`

Serialized **NetworkX** AST call/dependency graphs in JSON. They back the `get_code_neighbors` and `get_code_subgraph` tools.

- **256 files total:** 129 task-named files (`<instance_id>.json`) hard-linked to 127 commit-named files (`<repo_short>_<base_commit>.json`). Some tasks share a base commit, hence 127 unique graphs.

**Schema:**

| Key | Type | Description |
|---|---|---|
| `directed` | bool | Edges are directed (`true`). |
| `multigraph` | bool | Multiple edges may connect the same pair (`true`). |
| `graph` | dict | Global graph metadata. |
| `nodes` | list | Code entities (modules, classes, functions). |
| `nodes[*].id` | str | Fully qualified Python symbol path, e.g. `fastapi.routing._prepare_response_content`. |
| `nodes[*].name` | str | Human-readable qualified name (matches `id`). |
| `nodes[*].text` | str | Full source of the definition at `base_commit`. |
| `edges` | list | Directed structural relationships. |
| `edges[*].source` | str | Symbol `id` where the call/reference originates. |
| `edges[*].target` | str | Symbol `id` being called / imported / referenced. |
| `edges[*].type` | str | Relationship category (e.g. `calls`). |
| `edges[*].key` | int | Index (0, 1, 2, …) distinguishing parallel edges. |

Loading example:

```python
import json
import networkx as nx

with open("graphs/fastapi_11194.json") as f:
    data = json.load(f)
G = nx.node_link_graph(data, directed=True, multigraph=True)
```

### 2.4 `embeddings/`

Compressed NumPy archives powering offline semantic retrieval via `search_similar_code`.

- **256 files total:** 129 task-named (`<instance_id>.npz`) hard-linked to 127 commit-named (`<repo_short>_<base_commit>.npz`).
- Each archive holds entries `<node_id>.npy`: a **float32 array of length 256** (`embedding_[0-255]`) for the matching graph node `node_id`.

Loading example:

```python
import numpy as np

emb = np.load("embeddings/fastapi_11194.npz")
node_ids = list(emb.files)          # graph node ids (may carry a .npy suffix depending on loader)
vec = emb[node_ids[0]]              # shape (256,), dtype float32
```

### 2.5 `wheels/`

- 124 pre-compiled offline Python wheels (`*.whl`) covering historical dependency versions for `fastapi`, `starlette`, `pydantic`, `requests`, `urllib3`, `rich`, `httpx`, `httpcore`, `pytest`, and transitive packages.
- Mounted **read-only at `/wheels/`** in the sandbox so `pip` can install without internet access.

### 2.6 `docker/`

Build specs and stdlib compatibility shims for the local `swebench-sandbox:latest` image.

| File | Purpose |
|---|---|
| `Dockerfile.sandbox` | Base sandbox: Python 3.13, git, pytest, build backends (setuptools, hatchling, flit-core, poetry-core, pdm-backend). |
| `Dockerfile.public` | Dev container variant pre-configured for the 129 public tasks. |
| `imp.py` | Shim restoring the legacy `imp` module (removed in Python 3.13). |
| `telnetlib.py` | Shim restoring the legacy `telnetlib` module (removed in Python 3.13). |

### 2.7 `sandbox/setup.py`

Container initialization script run inside `/workspace` when preparing a task sandbox. It:

1. Inspects `pyproject.toml`, `setup.cfg`, or `requirements.txt`.
2. Resolves compatible offline wheels from `/wheels/`.
3. Performs an editable install: `pip install --no-index --find-links=/wheels -e .`
4. Creates a clean baseline Git commit, so **`git diff HEAD` captures only the agent's edits**.

### 2.8 `sample_submission/`

Ready-to-run baseline **ADK** submission directory demonstrating:

- the declarative YAML schema,
- multi-agent delegation,
- multi-LoRA routing.

Use it as the starting template for `submission.zip`.

### 2.9 `HARNESS_README.md`

In-depth technical guide covering:

- the `swegemma`, `adk-submission`, and `adk-eval-core` libraries,
- the two-container sandbox lifecycle,
- tool call signatures,
- context compaction,
- local CLI evaluation commands.

> **Read this file before implementing tools or the agent loop** — it is the authoritative source for tool signatures and the evaluation CLI.

### 2.10 `submission.parquet` (output)

Produced automatically at `/kaggle/working/submission.parquet` when the inference script runs the uploaded `submission.zip` against the benchmark tasks.

| Column | Description |
|---|---|
| `id` | Task identifier matching `instance_id` in `tasks.jsonl`. |
| `prediction` | Unified git diff patch generated by the agent relative to `base_commit`, or the literal string `NO_PATCH` if no changes were produced. |

---

## 3. Dataset Generation & Verification Pipeline

Both train and test sets were built with the same automated curation + two-phase verification harness:

1. **Commit & PR mining** — select commits that modify both core logic (`.py`) and unit tests (`test_*.py` / `*_test.py`).
2. **Issue linking & prompt extraction** — match each change to its original issue / bug report.
3. **Filtering & de-noising** — exclude mechanical refactors, doc-only changes, large-scale automated changes (LSCs), and ambiguous issues without reproducible acceptance criteria.
4. **Execution verification:**
   - **Fail-to-Pass (baseline):** apply `test_patch` to `base_commit` *without* the fix → tests must **fail** (non-zero exit).
   - **Pass-to-Pass (solution):** apply `patch` + `test_patch` → the **entire test suite must pass** (exit code 0).
5. **Near-completion check (test set only):** a larger frontier model must either pass the task or come within one test case of passing.

### What this means for grading

A prediction counts as solved when, after applying the agent's patch plus the hidden `test_patch`:

- the new tests that previously failed now **pass**, and
- the rest of the existing test suite **still passes** (no regressions).

So the agent should: make minimal, targeted changes; avoid breaking unrelated behavior; and not edit or delete existing tests to make them pass.

---

## 4. Typical Per-Task Flow (Agent's View)

1. Sandbox extracts the snapshot into `/workspace` and runs `sandbox/setup.py` (offline install + baseline commit).
2. Agent receives `problem_statement` (+ `hints_text`).
3. Agent explores using shell/git/file tools plus graph tools (`get_code_neighbors`, `get_code_subgraph`) and semantic search (`search_similar_code`).
4. Agent edits files; optionally writes and runs reproduction tests with pytest (no internet; deps come from `/wheels/`).
5. Harness collects `git diff HEAD` as the `prediction` (or `NO_PATCH`).

## 5. Local Development Tips

- Use the public `patch` / `test_patch` fields to build a **local scorer** that replicates Fail-to-Pass and Pass-to-Pass checks.
- Hold out some public tasks as a validation set; the hidden test set uses **different (private) repos**, so measure generalization, not memorization.
- Environment is **offline**, Python **3.13**; older code may rely on the `imp` / `telnetlib` shims.
- Many tasks share base commits (127 unique graphs/embeddings for 129 tasks) — cache loaded graphs by commit.