# Google - The Gemma 4 Developer Agent Competition Reference Guide

> **Document Purpose**: This document serves as a structured reference guide for AI Agents, summarizing the core rules, architectural constraints, submission specifications, and predefined tool APIs for "The Gemma 4 Developer Agent Competition".

## 1. Overview & Description

* **Host**: Google DeepMind
* **Core Goal**: Post-train an open model (**Gemma 4**) using fine-tuning and reinforcement learning (RL) techniques to build a reliable, autonomous software engineering (SWE) agent capable of running offline on consumer/everyday hardware.
* **Core Task**: The agent must independently navigate complex codebases, comprehend repository structures, and draft fixes (patches) or add features for real software issues.

## 2. Evaluation & Scoring

### 2.1 Issue Scoring

* **Evaluation Metric**: Similar to SWE-Bench, submitted patches are evaluated using a **PASS/FAIL** metric.
* **Validation Process**: For each issue, the submitted patch is applied to the issue's code repository, and the repository's validation tests are executed.
* **Overall Score**: The percentage of patched repositories that successfully pass the validation tests.

### 2.2 Time Budget

* **Total Time Limit**: Your agent has a strict limit of **12 hours** to submit patches for all tasks. This includes sandbox setup time, but **excludes** the time taken for patch validation.
* **Per-Task Time Limit**: You may optionally configure per-task time limits inside your submission's `eval_config.yaml`.

## 3. Submission Format

You must provide a zip archive named `submission.zip` containing your Agent Config, which comprises system prompts, custom tools, skills, and LoRA adapters.

* **Root Requirement**: An `agent.yaml` file **must** be located at the root of the archive.
* **Specification**: The configuration language follows the **Google ADK Agent Config** specification, with additional security restrictions to prevent code execution outside the sandbox. Submissions are compiled into ADK agents for evaluation.

### Archive Directory Structure

```text
submission.zip
├── agent.yaml                  # REQUIRED: Root agent config
├── configs/
│   └── sampling.yaml           # Optional: Generation parameters loaded via !include
├── prompts/
│   ├── system.md               # Optional: System instructions loaded via !include
│   └── analyzer.md
├── sub_agents/
│   └── code_analyzer.yaml      # Optional: Sub-agent or AgentTool YAML configurations
├── adapters/                   # Optional: Fine-tuned PEFT LoRA adapters or model weights
│   ├── main_lora/
│   │   ├── adapter_config.json
│   │   └── adapter_model.safetensors
│   └── tool_lora/
│       ├── adapter_config.json
│       └── adapter_model.safetensors
└── skills/                     # Optional: ADK Skill directories
    └── repo_navigation/
        ├── SKILL.md
        ├── scripts/            # Python or bash scripts executed in a sandbox
        └── resources/          # Domain knowledge markdown files
```

## 4. Model Selection, Budget, and Harness Rules

### 4.1 Model Selection & LoRA Adapters

* **Supported Base Model**: Only the `gemma-4-31b-it-qat-w4a16-ct` Gemma 4 model variant is supported. **You must select this model for every agent and subagent.**
* **LoRA Adapters**: Including `.safetensors` format LoRA adapters is optional. While restricted to a single base model, **you may use different adapters for different agents** within your submission:
  1. Place PEFT LoRA directories (containing `adapter_config.json` and `adapter_model.safetensors`) inside `adapters/<adapter_name>/`.
  2. Reference `adapter: <adapter_name>` on any `LlmAgent` in `agent.yaml` or `sub_agents/*.yaml` (e.g., `adapter: main_lora` on the root coder agent, and `adapter: tool_lora` on a read-only analyzer `AgentTool`).

### 4.2 Tool, Prompt, and Skill Rules

* **`!include` Directives**: Resolves file paths relative to the directory of the file containing the tag. For instance, in `agent.yaml`, `!include prompts/system.md` loads `prompts/system.md`.
* **Sandboxing**: Path traversal outside the submission root (e.g., via `../` or symlinks) is strictly prohibited.
* **Allowed Tools**: Your agent can only request tools provided by the competition harness or custom subagents defined via `agent_tool`.
* **Skill Structure & Execution**:
  * Each skill must be a directory containing a `SKILL.md` manifest with YAML frontmatter (`name: <skill-name>`).
  * Scripts executed via `run_skill_script` run securely inside the competition's persistent Docker container, sharing filesystem access with `run_command` and debiting execution time against your central 12-hour budget.
  * Agents can inspect domain knowledge files using `load_skill_resource`.

### 4.3 Predefined Tools API

The following predefined tools are available to your agent (operating primarily within `/workspace`):

| Tool Signature | Description | 
| ----- | ----- | 
| `run_command(command: str) -> str` | Executes a shell command in `/bin/bash -c` inside `/workspace`. | 
| `submit_patch() -> str` | Stages untracked file intents (`git add -N .`) and captures `git diff HEAD` from `/workspace`. | 
| `get_status() -> str` | Returns live budget consumption and patch status. | 
| `read_file(filepath: str, start_line: int \| None = None, end_line: int \| None = None) -> str` | Reads a file from `/workspace` with 1-indexed inclusive line slicing. | 
| `edit_file(filepath: str, old_string: str, new_string: str, allow_multiple: bool = False) -> str` | Replaces `old_string` with `new_string` in an existing non-empty file inside `/workspace`. | 
| `write_file(filepath: str, content: str) -> str` | Creates or overwrites a file at `/workspace/<filepath>`, automatically creating parent directories (`mkdir -p`). | 
| `get_code_neighbors(node: str, edge_type: str \| None = None, max_neighbors: int = 50) -> str` | Finds incoming and outgoing neighbors of a symbol (`node`) in the repository call/dependency graph. | 
| `search_similar_code(query: str, k: int = 10) -> str` | Finds top-`k` graph nodes with highest cosine similarity to `query` in the pre-computed embeddings. | 
| `get_code_subgraph(nodes: list[str]) -> str` | Extracts the induced subgraph (all nodes and interconnecting edges) for a list of symbols. | 

*(Note: Refer to the `HARNESS_README.md` file in the competition dataset for detailed specifications regarding the submission format and execution environment.)*

## 5. Hardware & Upgraded Accelerators

This competition provides access to Kaggle's pool of **L4x4** machines, offering **96GB of GPU memory** to support larger models.

* **Quota Usage**: Notebooks using L4x4 machines consume GPU quota at **twice the rate (2x)** of older T4x2 and P100 machines (subject to increase if necessary to ensure availability for submission scoring).
* **Restricted Use**: L4 machines are strictly restricted to notebooks attached to this competition. Circumventing this restriction may result in team bans or account bans.
* **No Internet**: **All L4 sessions must have internet access disabled.**

## 6. Citation

```text
Elan Markowitz, Bryan Perozzi, Benedek Rózemberczki, Glenn Cameron, Hadi Hemmati, Yuchen Li, Michael Galkin, Majid Farhadi, Ryan Holbrook, and Ashley Oldacre. Google - The Gemma 4 Developer Agent Competition. https://www.kaggle.com/competitions/gemma-4-developer-agent, 2026. Kaggle.
```