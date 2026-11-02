# Kaggle Gemma 4 Developer Agent：On-Policy RFT 微调实验计划

## 0. 实验背景与核心目标
基于社区目前已公开的 98 题成对消融实验（Paired Local Eval）结论，常规外部大模型蒸馏 SFT 与 Pass@1 朴素 RFT 均未能稳定跑赢 Prompt-only 基线，核心瓶颈在于：
1. **文件定位非瓶颈**：失败任务大多已编辑到参考补丁的同一行或同一区域（本地 dev60：`wrong_fix` 13 题中 11 题为 `same_lines`/`same_region`）。
2. **两大真实死因**：
   - **“定位到正确区域但写错 Patch 逻辑”**（本地 dev60 `wrong_fix` 13/60）。
   - **“犹豫不决、反复查看直至耗尽预算”**（本地 dev60 `no_patch_budget` 13/60）。31/60 题用满 28 次调用，首次编辑的中位数在第 19 次调用，而 prompt 要求 ≤ 12。
3. **LoRA 推理隐性税（Serving Tax）**：在官方评测环境（4×L4 GPU + vLLM 0.19.1 + `gemma-4-31b-it-qat-w4a16-ct`）下，启用 `--enable-lora` 预计使单步解码延迟增加 15%–20%（待 Gate 2c 实测）。

**本实验核心目标**：
- 采用 **“摇摆任务拒绝采样 + 定位后关键 Turn 的 loss mask”** 的 On-Policy RFT，不引入 Off-policy 分布偏移。
- 把首次源码编辑提前到 **≤ 第 12 次调用**，将 `no_patch_budget` 桶 **减半**。
- 单任务中位工具调用步数压缩 **≥ 3 步**，用省下的步数抵消 LoRA 解码延迟。

**分两个阶段推进**：
- **阶段一（本文档）**：只用比赛公开的 129 题，**不引入任何外部数据**，跑通“采样 → 构造数据 → 训练 → vLLM 加载 LoRA → 门控评测”的完整流程，验证工程链路正确，并初步观察 RFT 对 `edit@call` 的影响。
- **阶段二（暂缓）**：链路验证通过后，再引入外部开源任务扩充训练池（见 §6）。

> 本轮不做 DPO。

---

## 1. 微调数据来源（Data Sourcing）

阶段一只使用比赛公开的 129 题。剔除 `eval/env_broken.json` 中参考补丁本地跑不通的 15 题后，剩余 114 题按 dev60 划分：

| 数据集角色 | 构成 | 规模 | 说明 |
| :--- | :--- | :--- | :--- |
| **本地采样池 (`Train Pool`)** | 不在 dev60 中、且环境可用的公开题，再剔除与验证集共享 `base_commit` 的 `rich_3882` | **57 题**（fastapi 33 / rich 19 / requests 5） | 依赖已由现有环境修复（notebook cell 2c，`FIX_TEST_ENV=True`）覆盖，**无需任何环境准备**。 |
| **本地成对验证集 (`Paired Eval`)** | dev60 去掉 4 个 env-broken（`fastapi_15745`、`fastapi_15785`、`requests_6589`、`requests_6629`） | **56 题** | 可直接复用已有的 dev60 基线结果。**严禁混入任何训练或采样流程**。 |

**注意（同仓库偏差）**：
- 训练池与验证集来自同样的 4 个仓库（fastapi/rich/requests/httpx），而隐藏测试集来自私有仓库。因此阶段一在本地观察到的提升 **会高估** 线上收益。
- 阶段一的结论只用于判断“链路是否正确、RFT 是否在改变行为”。是否有真实增益，以线上 LB 和阶段二为准。

---

## 2. 轨迹构造方法（Trajectory Construction）

直接使用当前最强的 **单一 Prompt 基线（含现有 `agent.yaml` 与工具链）** 配合底座 `gemma-4-31b-it-qat-w4a16-ct` 进行 On-Policy 采样，不使用外部 Teacher 模型蒸馏。采样用的 thinking 配置必须与最终提交一致。

### Step 2.1：真实 Harness 拦截采样（自适应 K）
- **采集方式**：
  - 在 Agent 与 vLLM Server 之间挂载轻量级 Logging Proxy，记录原始 `/v1/chat/completions` 请求与响应 JSON。
  - 同时保存渲染后的 prompt token ids，并与 `tokenizer.apply_chat_template` 的结果逐字节核对，确保 Token 化与聊天模板 100% 一致。
- **运行方式**：复用 `notebooks/getting-started-gemma-4-developer-agent.ipynb`，把 `DEV_IDS` 换成 Train Pool 的 57 题，并保持 `FIX_TEST_ENV=True`。
- **自适应 K**：
  - 第一轮每题 **K=2**。
  - 结果为 1/2 的直接保留。
  - 结果为 2/2，或 0/2 但已编辑到 gold 区域（可用 `eval/refdiff.py` 判断）的，再补 **2 条**。
  - 总计约 **170 条**轨迹。
- **采样参数**：`temperature=0.7`，`top_p=0.95`，`max_tool_calls=28`；**不设墙钟超时**，耗时按 token 数离线估算。
- **并发控制**：`concurrency = 6–8`，以提高 vLLM 聚合吞吐。如果 CPU 成为瓶颈（每个 sandbox 占 2 vCPU），降到 4–6。因为不设墙钟超时，并发提高不会造成假性截断。

### Step 2.2：任务级“摇摆过滤”（Sometimes-Solved Filtering）
对全部轨迹运行单元测试校验（Verify），按每个任务的通过比例进行过滤：
- **剔除全部失败**：超出当前模型能力边界，无正样本可用。
- **剔除全部通过**：模型原本就能稳定做对的简单题，学习此类样本只会让分布过度锐化。
- **仅保留部分通过的“摇摆任务（Sometimes-Solved Tasks）”**：阶段一预计 **15–25 个**。

### Step 2.3：正样本选择与关键 Turn 的 loss mask
1. **轨迹选择**：对每个摇摆任务，先筛出 **通过测试且首次源码编辑 ≤ 第 12 次调用** 的轨迹，再从中挑工具调用总步数最少的一条。
2. **loss 范围**：
   - 只对 **“首次读到 gold 文件之后”到“提交补丁”之间的 assistant turn**（Thought + Tool Call）计算 loss。这段包括 Patch 编辑步、运行测试后的 Debug 修复步和果断提交步。
   - 前期的 `find/grep` 定位步只作为上下文，不计算 loss。
3. **样本组织（按 prefix 链分段打包）**：
   - 如果同一轨迹中后一次请求恰好是“前一次请求 + 前一次回复”的前缀延伸，就合并成一条序列，并在其中所有选中的 turn 上计算 loss。
   - harness 会在 14,336 token 处压缩上下文（`EventsCompactionConfig`），压缩点即为新段的起点。
   - 阶段一预计约 **25–35 条序列**，覆盖 **100–200 个关键决策 Turn**，训练 token 约 0.4M。

---

## 3. 微调训练方法（Fine-Tuning Recipe）

### 3.1 实验组设计
- **实验组 A（Clean Turn-RFT）**：
  - 在第 2 步打包好的序列上训练。
  - 自行构造 `labels`：只对选中 turn 的 assistant 输出（`<|channel>thought…<channel|>` 与 `<|tool_call>…`）计算 Cross-Entropy Loss。
  - 历史 Prompt、Tool Observation 和未选中的 turn 全部 mask 为 `-100`。
  - 不使用 `DataCollatorForCompletionOnlyLM`（新版 TRL 已移除）。
- **训练底座**：Gemma 4 31B 的 **未量化 QAT 权重（bf16）**，与推理用的 `w4a16-ct` 量化版对应。
- **训练硬件**：阶段一数据量小，**L4×4 QLoRA**（transformers + peft + bitsandbytes，gradient checkpointing，`max_len=16k`）约 1h 即可完成，优先用这条工具链成熟的路线验证链路。阶段二数据量变大后，再评估 TPU v5e-8。

### 3.2 核心超参数配置

| 超参数 | 设定值 | 依据 |
| :--- | :--- | :--- |
| **`r` (LoRA Rank)** | **`16`** | 导出权重约 100–200MB，vLLM `max_lora_rank` 按实际 rank 设定，对 KV Cache 挤压最小。 |
| **`target_modules`** | `q_proj, k_proj, v_proj, o_proj`，**用正则限定在 `language_model` 层** | 先训 Attention 4 层（推理开销小、分布干扰低）。限定层范围是为了避免 vLLM 不识别（例如视觉塔模块）导致 adapter 被拒绝或静默失效。 |
| **训练期 `lora_alpha`** | **`16`**（α/r = 1.0） | 保守起点；推理期仍可调整。 |
| **`learning_rate`** | `5e-5` | Cosine Scheduler，`warmup_ratio=0.1`。 |
| **`num_train_epochs`** | **1** | 阶段一样本少，多 epoch 容易过拟合特定仓库路径名。 |
| **导出精度** | 纯 `bfloat16` | 仅导出标准 PEFT `adapter_model.safetensors`，不 merge。 |
| **推理期 `lora_alpha` 调优** | 在 `{8, 12, 16}` 之间扫描 | 修改 `adapter_config.json` 中的 `lora_alpha` 等价于在 base 与 LoRA 之间插值。这只是廉价的调参旋钮，以 Gate 2a 的结果为准。 |

---

## 4. 离线门控与测试提交流程（Testing & Submission Gate）

鉴于线上 Public LB（约 60 题）方差极大，任何模型必须通过以下本地门控才允许提交线上。**Gate 3 的时间预算修正应最先完成**，它与是否使用 LoRA 无关。

### Gate 1：打包规范与 Adapter 生效检查（约 30 分钟）——阶段一的核心验证点
- [ ] **目录结构检查**：权重位于 `submission.zip` 的 `adapters/main_lora/` 下，仅含 `adapter_config.json` 与 `adapter_model.safetensors`（无软链接，总包 `< 3 GiB`）。
- [ ] **显式挂载检查**：根目录 `agent.yaml` 的 `LlmAgent` 节点已添加 `adapter: main_lora` 字段。
- [ ] **PEFT key 检查**：adapter 的权重 key 前缀与 rank 和 vLLM 期望一致。
- [ ] **Adapter 生效探针**：
  - 在比赛 notebook 自带的 vLLM 上，确认 adapter 已注册，且 `/v1/models` 中可见。社区报告 stock vLLM 0.19.1 不接受 Gemma 4 LoRA，必须使用比赛 wheelhouse 里的补丁版。
  - 在回放的历史 prompt 上对比 Base 与 LoRA：`max |Δlogprob|` 须大于 base 自身噪声底的 5 倍，以防“加载了但不起作用”。
  - 200 个回放 prompt 上，工具调用格式合法率达 100%。

### Gate 2：本地成对评测（Paired Local Eval，56 题）
- [ ] **Gate 2a 快筛（约 1h）**：在 Paired Eval 中选 20 题（基线 `no_patch_budget` 或摇摆的任务）运行 LoRA。**首次编辑中位数未提前 ≥3 次调用即终止**，回到数据或超参调整。
- [ ] **噪声底**：以 `FIX_TEST_ENV=True` 把基线在 Paired Eval 上重跑一次，与已有 dev60 基线对比，测出自身翻转数（原本对→错，加上原本错→对）。
- [ ] **Gate 2b 全量对比（56 题，约 2h）**：
  - 基线与 LoRA 使用 **同一时间预算**（即 Gate 3 确定的值）。
  - 并发 4 运行，耗时按 token 估算。
  - **效率指标（必达）**：中位工具调用步数比基线下降 ≥ 3 步；按 token 估算的平均耗时 ≤ 基线。
  - **效果指标（必达）**：成对净胜场（原本错现在对 − 原本对现在错）**大于 2 × 基线自身翻转数**。注意 §1 的同仓库偏差，本地净胜只是必要条件。
- [ ] **Gate 2c 延迟实测（约 1h）**：选 10 题，`concurrency=1`（与官方评测器单任务顺序执行一致），实测 LoRA 的每步延迟，回填到 Gate 3 的核算中。
- [ ] **Alpha 回退**：若出现工具重复调用死循环，或因步数耗尽导致 Regression，把 `adapter_config.json` 中的 `lora_alpha` 从 `16` 下调到 `12` 或 `8`，重跑失败子集验证。

### Gate 3：12 小时全局预算校准（`eval_config.yaml`）
官方隐藏测试集约 120 题，全局硬性限时 12 小时，**包含 vLLM 启动与每题 sandbox setup**；超时会导致整份提交报错。
- [ ] 当前基线每题平均 5.42 分钟，120 题仅 agent 时间就约 10.8h，已接近上限；若每题上限设为 390s，最坏情况为 120 × 390s = 13h，超出预算。
- [ ] 将 `max_time_minutes` 设为 **5–5.5**，`max_tool_calls` 保持 **28**。
- [ ] 准入条件：**平均每题耗时（含 Gate 2c 实测的 LoRA 开销）× 120 + vLLM 启动 + setup ≤ 10.8h**（留 10% 余量）。

---

## 5. 阶段一算力预算（Kaggle L4×4 按 2 倍扣配额，每周约 15h 实际运行时间）

| 环节 | 实际运行时间（估算） |
| :--- | :--- |
| On-Policy 采样（约 170 条，并发 6–8） | 约 3–4h |
| RFT 训练（L4 QLoRA） | 约 1h |
| Gate 1 + Gate 2a/2b/2c + 基线噪声重跑 | 约 6h |
| **合计** | **约 10–11h GPU，约 20–22h 配额，单账号约 0.7 周** |

> 以上为估算。启动采样前，先实测并发 8 下的 vLLM 聚合吞吐与 CPU 占用，再确定最终并发数。

每个阶段完成后，运行 `eval/triage.py` 与 `eval/trace_stats.py`，对比 resolved、`no_patch_budget` 数量、`edit@call` 中位数与 `s/step`。

---

## 6. 阶段二（暂缓）：引入外部数据
阶段一的 Gate 1 通过、且 Gate 2a 显示 `edit@call` 确实提前后，再扩充训练池：
- 从 SWE-rebench 选纯 Python、无 C 扩展、支持 Python 3.12、依赖与 Kaggle 内核（vLLM/litellm/ADK）不冲突的任务，候选约 300 题，校准后保留约 150 题。
- 准备 `tasks.jsonl` 与 `snapshots/<id>.tgz`；测试依赖并入 `scripts/build_extra_wheels.py` 的 `PACKAGES`，装进 notebook 内核（subprocess 沙箱直接使用内核环境）。为每个任务放置最小占位 graph/embedding 文件，使初始 prompt 与正式评测一致。
- 用 `notebooks/gold-calibration/` 跑参考补丁校准（仅 CPU）；验证集同时补入 25 题外部留出集。
