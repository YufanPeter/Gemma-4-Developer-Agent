# Gemma-4 Developer Agent：SFT 方案（金银牌冲刺版）

## 1. 决策摘要 (Executive Summary)

### 1.1 核心目标

用强教师蒸馏轨迹、思维回译（Thought Back-Translation）后的开源轨迹以及 On-Policy 兜底/自愈样本，对 `gemma-4-31b-it-qat-w4a16-ct` 训练全线性层 LoRA（v1）。目标是在 `22 calls / 4.5 min` 预算下，dev56 逐题配对结果显著优于无 LoRA 对照组 B0。产出为 `adapters/main_lora/adapter_model.safetensors`。

要修正的行为缺陷（基于 `exp_res/results_baseline_1017_60dev` 诊断）：

| 缺陷 | 证据 | 对策 |
|---|---|---|
| 探索发散与超时 | 首次编辑中位数在第 19 步；13/60 题未产出 patch | 过滤首次编辑 ≤ 第 9 步；System Prompt 强化 `rg -n` 组合检索；D3 注入强制编辑与强制提交 |
| `edit_file` 序列化与匹配失败 | 174 次调用失败 83 次（54 次解析失败，29 次匹配失败后放弃） | LoRA 覆盖 MLP 层提升长字符串复制精度；难例占比 ≥ 30%；加入编辑失败后的自愈（Error-Recovery）监督 |
| 缺乏自验证导致“假修” | 改完代码直接提交或跑全量测试超时 | 剔除裸 `pytest`，但强制保留轻量级定向验证（单测函数或 `/tmp/repro.py`）闭环 |
| 上下文溢出与长生成卡死 | 54/60 题超 14,336 token 压缩阈值；单步长生成威胁 60s 上限 | 轨迹 ≤ 14K token；单步最大生成下调至 2048；保留压缩摘要样本 |

### 1.2 关键技术栈与约束

| 维度 | 决定 |
|---|---|
| 基座 | `gemma-4-31b-it-qat-w4a16-ct`：INT4 权重反量化后冻结，确保训练与线上 vLLM 推理数值一致 |
| 训练 | 只训一版 `all-linear` LoRA（v1），**不 merge**。满足赛制 PEFT 限制（大小 `< 3GiB`，`rank ≤ 128`） |
| 教师 | `DeepSeek-V4-Flash`（主采样与 D2 思维回译）；难题中段补采使用 `DeepSeek-V4-Pro` |
| 数据 | D1 + D2 + D3，共约 2,130 条，约 23M tok/epoch |
| 评测 | `dev60` 永不进训练；主指标是 dev56 上相对 B0 的配对 resolved |
| 线上 | Kaggle L4×4；vLLM 启动 `< 600s`；120 题串行总时长 `< 12h` |

线上推理配置（B0 与 v1 完全相同）：

```yaml
# eval_config.yaml
evaluation: {max_time_minutes: 4.5, max_tool_calls: 22, max_turns: 30, timeout_seconds: 60}
# configs/sampling.yaml
temperature: 0.2
top_p: 0.95
repetition_penalty: 1.05
seed: 0
max_output_tokens: 2048
thinking_config: {thinking_budget: 512, include_thoughts: true}
```

- 工具保留 6 个：`run_command, read_file, edit_file, write_file, get_status, submit_patch`（从 `agent.yaml` 删除 `graph` 工具，并在 `prompts/system.md` 中加入 `rg -n "def |class "` 高密度检索范式以弥补跨文件定位效率）。
- `prompts/system.md` 同步改为 22 calls / 4.5 min。
- 时间推导：`0.5h + 120×40s + 120×4.5min = 10.8h < 12h`。

---

## 2. 数据构造 (Data Construction)

### 2.1 数据划分

| 集合 | 组成 | 用途 |
|---|---|---|
| Test | `dev60`（fastapi 31 / rich 22 / requests 6 / httpx 1）。计分用 `dev56`，去掉 `fastapi_15745, fastapi_15785, requests_6589, requests_6629` | 只做评测 |
| Train 池 | 其余 69 题去掉与 dev 共用 `base_commit` 的 `rich_3882`，共 68 题（57 题严格可验，11 题弱验证，见 `eval/env_broken.json`） | D1 采样、D3 前缀 |
| Val | Train 池中按仓库分层抽 5 道严格可验题，再加 D2 中整仓留出的 5% 仓库 | 离线挑选 checkpoint |

### 2.2 数据来源与配比

| 子集 | 来源 | 训练条数 | 占比 |
|---|---|---|---|
| D1 原生教师轨迹 | 教师在真实 harness 中跑 Train 池（63 题，不含 Val） | 约 140 条，上采样 ×2 后约 280 | 约 13% |
| D2 开源轨迹转换 | `nebius/SWE-rebench-openhands-trajectories`、`SWE-bench/SWE-smith-trajectories`、R2E-Gym 成功轨迹（经 Flash 思维回译） | 约 1,500 | 约 70% |
| D3 兜底收敛与自愈样本 | 以 Gemma 自身 B0 轨迹为前缀构造（§2.7） | 350 | 约 17% |

### 2.3 教师模型

| 角色 | 模型 | 依据 |
|---|---|---|
| 主教师与回译器 | `DeepSeek-V4-Flash`（非思考模式） | 价格极低（$0.14 输入 / $0.28 输出，每百万 token）；原生支持 function calling；负责 D1 主采样与 D2 思维回译 |
| 难题补采教师 | `DeepSeek-V4-Pro` | SWE-bench Verified 第一梯队（约 80%），负责攻克 Flash 未解出的硬骨头 |

运行方式：
- 在 Kaggle CPU notebook 中执行 `swegemma eval --sandbox subprocess --models-yaml teacher.yaml`，所有截断、`budget_warning`、nudge、压缩均由真实 harness 生成。
- 挂载 Logging Proxy 记录每次请求的 `messages` 和 `tools` 原文。
- 教师预算：`max_tool_calls: 22`、`max_time_minutes: 10`。
- System Prompt 末尾追加（存档时删除）：`Before each tool call write at most 2 short sentences of reasoning. Use rg -n to locate symbols quickly. Edit a source file by call 8. Verify your fix with a targeted pytest (-k) or /tmp/repro.py before submitting. Finish within 16 tool calls.`
- 教师生成的 1–2 句推理放入 Gemma 的 thought channel。

### 2.4 D1 采样

1. **冒烟**：10 题 × 2 次，核对 token 用量、定向单测执行率与成本。
2. **Flash 主采样**：每题最多 8 次，`temperature=0.7`，每次换 seed；某题攒够 3 条合格轨迹即停，共约 400 次。
3. **Pro 中段提示补采（防止第 0 步剧透导致幻觉跳步）**：对 Flash 未解出的题（约 20 题），取 Flash 或 B0 已正常探索 4–5 步（包含真实 `rg` 或报错栈分析）的前缀，在第 5 步作为临时 User Hint 注入参考 patch 涉及的**文件路径**，让 Pro 顺着前期观测自然过渡并完成修复（每题最多 4 次）。存档时删除临时 Hint，确保 `<thought>` 推理链因果自洽。
4. **判定**：严格可验题看 `resolved=True`；弱验证题用 `eval/refdiff.py`，要求编辑位置为 `same_lines`、改动文件 `py_compile` 通过、无多余文件。
5. **去重**：每题最多保留 3 条，优先保留包含轻量验证步、步数少且编辑位置有差异的轨迹。

### 2.5 D2 转换与思维回译

- **筛选**：`resolved=True`，仅限 Python 仓库，单步观测 ≤ 20K 字符，按 instance 去重，每仓最多 30 条。**整仓排除** `fastapi, rich, requests, httpx` 及其 fork。
- **动作映射**：

| 源动作 | 目标工具 |
|---|---|
| bash（去掉 `cd /testbed &&`，`/testbed` 替换为 `/workspace`） | `run_command` |
| `view` 文件（超过 150 行拆分，去掉行号前缀） | `read_file` |
| `view` 目录 | `run_command("git ls-files <dir> \| head -50")` |
| `str_replace`；`insert`（用插入点前一行作 `old_string`） | `edit_file` |
| `create`（复现脚本统一改到 `/tmp/`） | `write_file` |
| `finish` / `submit` | `run_command("git diff")` + `submit_patch()` |

- **Thought 思维回译（消除跨框架语义断层）**：剔除源轨迹自带的原生 `<thought>`（防止残留 `str_replace_editor`、行号引用或 `/testbed` 等幻觉词汇）。调用 `DeepSeek-V4-Flash` 给定转换后的上下文与目标 Tool Call，按 `≤ 2 short sentences` 规范批量回译生成与 `swegemma` 工具严格对齐的 `<thought>`。
- **观测与预算提示**：重包为 swegemma JSON，stdout 截断至 5,000 字符，`read_file` 截断至 150 行或 10,000 字符；从第 12 次调用起追加 harness 原文 `budget_warning`；首轮消息按 `build_agent_prompt` 重建。

### 2.6 过滤规则（D1、D2 都适用）

| 规则 | 阈值 |
|---|---|
| 结果 | resolved，或满足弱验证 |
| 工具调用数（不计 `submit_patch`、`get_status`） | D1 ≤ 16，D2 ≤ 18 |
| 首次源码编辑 | ≤ 第 9 步 |
| 渲染后总 token / 单步 thought | 总长 ≤ 14,000 tok；单步 thought ≤ 256 tok |
| 重复命令与脚本限制 | 同一条 grep/read 重复出现则剔除；连续运行脚本 ≤ 2 次 |
| 测试与自验证策略 | 篡改测试文件或运行无参数全量裸 `pytest` 剔除；**优先保留含定向单测（`pytest <path> -k <expr>`）或 `/tmp/repro.py` 验证的轨迹（占比 ≥ 60%）** |
| 提交后动作 | 截断到第一次成功 `submit_patch` 为止 |
| `edit_file` 失败与恢复 | 失败超过 2 次的轨迹剔除；仅失败 1 次且紧接着通过 `read_file` + `edit_file` 修复成功的轨迹予以保留（失败步本身不计 loss，**修复步正常计 loss**） |
| 字符串定界难例 | `old_string`/`new_string` 含引号、反斜杠、正则或多行的编辑步占比 ≥ 30% |

### 2.7 D3 兜底收敛与自愈样本（350 条）

**前缀来源**：用 B0 配置在 Train 池（不含 Val）上跑一遍 Gemma（L4×4，约 2h），取真实轨迹作为前缀。**前缀中的所有历史 assistant turn 全部置 0，仅对目标恢复/收敛步计 loss**。

| 类型 | 条数 | 构造方式 | 监督目标 |
|---|---|---|---|
| Forced-Edit | 100 | 在“已读到参考 patch 所在区域但仍在探索”的位置（第 8–11 步）截断，注入 `budget_warning` | 立即 `edit_file`（由参考 patch 首个 hunk 转换，Flash 写 1–2 句过渡 thought） |
| Error-Recovery | 100 | 在 B0 发生 `edit_file` 匹配失败或语法报错的真实步骤后截断 | 先调用 `read_file` 重新精确查看目标行区间，随后发出正确的 `edit_file`（连续监督这 2 步） |
| Forced-Submit | 80 | 在成功编辑并完成单测验证后截断，注入 `"Only 2 tool call(s) remaining (20/22 used)..."` | `git diff` → `submit_patch()` |
| Nudge 响应 | 40 | 插入 harness 的 3 种 nudge 原文 | 直接发出下一个工具调用 |
| 压缩摘要 | 30 | 取教师 rollout 中真实的压缩摘要请求 | 纯文本摘要，不含工具调用 |

### 2.8 格式对齐（必须 100% 通过）

- **渲染器与历史 Thought 对齐**：用 Logging Proxy 记录 B0 运行时 vLLM 实际收到的 `prompt_token_ids`。首先核查 harness 在拼接多轮历史时是否剥离了历史轮次的 `<thought>`：
  - 若历史 `<thought>` 保留（满足严格前缀性质），则每条轨迹作为 1 个完整序列样本，所有有效 assistant turn 计 loss。
  - 若历史 `<thought>` 被剥离（打破前缀性质），则在训练时使用 **Block-Causal Attention Mask**（使第 $t$ 步的 `<thought>` 仅对第 $t$ 步的 tool call 可见，对 $>t$ 的后续轮次不可见），确保训练注意力上下文与线上 vLLM 完全等价。
- **压缩轨迹**：在压缩点切分为前后两段独立样本，与线上实际看到的上下文保持一致。
- **Round-trip 校验**：用 vLLM 的 `gemma4` tool parser 和 reasoning parser 将所有训练样本逆向解析，函数名、JSON 参数与原始字典逐字比对 100% 通过。
- **长度控制**：每个样本 ≤ `16384` tok。

---

## 3. 训练方法 (Training Methodology)

### 3.1 底座

- 训练时加载 `gemma-4-31b-it-qat-w4a16-ct` 的 INT4 权重常驻显存，前向与反向传播时即时反量化为 bf16。每卡一份模型（约 18GB），双卡 H100 数据并行。
- 训练前在 B0 真实轨迹上校验底座 NLL，必须 `< 2`；若异常则排查量化算子对齐问题。

### 3.2 LoRA 与超参

| 参数 | 值 |
|---|---|
| `r` / `lora_alpha` / `lora_dropout` | `16` / `32` / `0.05` |
| `target_modules` | `all-linear`（`q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`，仅作用于语言模型层） |
| `learning_rate` / 调度 / warmup | `1e-4` / cosine / `0.03` |
| `num_epochs` | `2`（约 135 个优化步） |
| 有效 batch | `32`（每卡 micro-batch 1 × 梯度累积 16 × 2 卡） |
| `max_seq_len` / Packing | `16384` / 关闭 Packing |
| 精度与显存优化 | bf16 计算；LoRA 参数 fp32；开启 gradient checkpointing 与 chunked CE（仅在计 loss 位置计算 logits） |
| 冒烟 | 先跑 200 条序列，实测训练 tok/s、显存峰值与 L4×4 解码延迟 |

选择 `r=16` 配合 `all-linear`（含 MLP 层），是因为 MLP 承载了代码缩进与长字符串精确复制能力，对降低 `edit_file` 参数匹配失败率至关重要；同时在 vLLM Punica 内核下，`r=16 all-linear` 与 `r=32 attn-only` 的推理解码速度差异 `< 5%`。

### 3.3 Loss Masking

- **计 loss**：目标 assistant turn 的 `<thought>`、`<|tool_call>…<tool_call|>` 及结束符、最终一句话回复、压缩摘要文本、以及 `edit_file` 报错后的**自愈修复步（Error-Recovery）**。
- **置 0**：system、user（含 nudge 和 `budget_warning`）、全部工具返回结果、失败的工具调用步本身、D3 前缀中的历史 assistant turn。

### 3.4 防过拟合与遗忘

- 底座冻结，采用 `r=16` 低秩更新并配合 `0.05` dropout。
- D2 跨仓库样本占约 70%，每仓上限 30 条，4 个评测集仓库整仓物理隔离。
- 严格训练 2 epoch，仅通过 Val 的 NLL 与解析率挑选最佳 checkpoint，零泄漏消耗 `dev60`。
- 保留 30 条压缩摘要样本，上线前静态检查 20 条摘要请求，确保输出均不含 `<|tool_call>`。
- 最终提交采用双保险策略：提交 1 为 v1（LoRA），提交 2 为调优推理配置后的无 LoRA 安全版（B0）。

### 3.5 验收

1. **离线验收**：在 Val 上评估工具名准确率、vLLM parser 解析成功率、关键决策步（首次编辑、自愈步、提交步）的 NLL，仅挑最优的 1 个 checkpoint 送测。
2. **打包与 Kaggle 冒烟**：用 `scripts/package_submission.py` 导出至 `adapters/main_lora/`，在 Kaggle L4×4 跑 3 题冒烟：vLLM 启动 `< 600s`，参数解析失败为 0。
3. **dev 前 20 题硬门槛**：

| 指标 | 阈值 |
|---|---|
| 推算总时长 `0.5h + 120×(40s + mean_dur)` | ≤ 10h |
| p90 单题耗时 | ≤ 240s |
| no_patch 率 | ≤ 5% |
| 参数解析失败 | 0 |
| `edit_file` 最终未恢复的匹配失败率 | ≤ 8% |
| 提交前执行轻量验证的比例 | ≥ 50% |
| 首次编辑中位步数 | ≤ 8 |
| 解码 tok/s 相对 B0 的降幅 | ≤ 20% |

4. **dev56 配对判定**：设 $b$ 为 v1 解出而 B0 未解出的题数，$c$ 为 B0 解出而 v1 未解出的题数。满足 $b - c \ge 5$ 且单侧符号检验 $p \le 0.10$ 视为通过；若 $c - b \ge 3$ 则触发回退。
5. **最终提交**：v1 通过配对检验且公榜不低于 B0 超过 2 题时，锁定 v1 + B0 双提交。

---

## 4. 预算与耗时估计 (Budget & Time Estimation)

### 4.1 算力与成本

| 项目 | 资源 / 用量 | 预期 | 悲观 |
|---|---|---|---|
| 教师 API | Flash 采样约 400 次 + Pro 中段补采 80 次 + D2 思维回译 1,500 条 + D3 thought（含 25% 重试） | $11.5 | $25（账户硬上限充值 $25） |
| 训练 GPU | 2× H100 80GB，每卡显存峰值 ≤ 52GB。按 5.5M tok/GPU·h 需 12 GPU·h，按 3.8M 需 16 GPU·h | $24 | $44 |
| 存储 | Pod 磁盘约 200GB × 3 天 | $4 | $8 |
| 教师 rollout | Kaggle CPU，免费 | $0 | $0 |
| **合计** | | **约 $39.5** | **约 $77（`< $100`）** |

熔断规则：
- 单次 rollout 成本 ≥ 估算 2 倍时，Flash 改为每题最多 4 次、攒够 2 条即停。
- Pro 中段补采累计达 $5 立即停止。
- 训练吞吐比预期慢 2 倍以上时，按比例下调 D2 条数至 1,000 条，保持 2 epoch 不变。

### 4.2 耗时与排期

| 阶段 | 耗时 |
|---|---|
| 教师 rollout（约 480 次）+ D2 批量思维回译（并发 16） | 约 7.5h |
| 单个 epoch（约 23M tok，2× H100） | 约 2.0h（悲观 3.0h） |
| v1 全程：冒烟 + 2 epoch + 离线评测与导出 | 约 6.5h（悲观 8.5h） |
| Kaggle L4×4 配额：B0 跑 dev60 (8h) + B0 跑 Train 池 (4h) + v1 跑 dev60 (8h) + 缓冲 (4h) | 共 24h（跨 2 周免费配额） |

| 日期 | 工作 |
|---|---|
| 11/11–12 | B0 跑 dev60 和 Train 池；核查历史 `<thought>` 拼接逻辑与渲染器对齐；D2 漏斗筛选；API 冒烟 |
| 11/12–15 | 构造 D1（含中段提示补采）、D2（含 Flash 思维回译）、D3（含 Error-Recovery），Round-trip 校验 100% 通过 |
| 11/15–17 | H100 冒烟训练 → v1 全量训练（`r=16, all-linear`）→ Val 挑 checkpoint → Kaggle 3 题加载冒烟 |
| 11/18–19 | v1 跑 dev 前 20 题过门槛 → 跑满 dev60 → 配对统计检验 → 提交公榜验证 |
| 11/20–25 | 缓冲期（若 v1 局部退化则微调 D3 配比再训一版）；锁定最终 2 个提交 |

---

## 5. 参考文献 (References)

- `docs/HARNESS_README.md`：赛制预算、`budget_warning`、压缩阈值、adapter 限制
- `eval/README.md`、`eval/env_broken.json`、`exp_res/results_baseline_1017_60dev`：dev60 定义与基线诊断数据
- `nebius/SWE-rebench-openhands-trajectories`、`SWE-bench/SWE-smith-trajectories`（HuggingFace）：D2 开源轨迹来源
- Jain et al. *R2E-Gym: Procedural Environments and Hybrid Verifiers for Scaling Open-Weights SWE Agents*. arXiv:2504.07164（自验证与混合验证器设计依据）
- *SWE-Lego*. arXiv:2601.01426 & *SWE-Zero to SWE-Hero*. arXiv:2604.01496（错误恢复轨迹与混合监督策略依据）
- Dettmers et al. *QLoRA: Efficient Finetuning of Quantized LLMs*. arXiv:2305.14314（`all-linear` 覆盖 MLP 层对保持生成精度的必要性证明）
- https://github.com/Peoplerepabulic/kaggle-gemma4-developer-agent ：LoRA 训练脚本与 chunked CE 实现
- Kaggle 竞赛讨论帖 751596 & vLLM Punica Kernel 基准：LoRA 解码延迟开销分析
- https://api-docs.deepseek.com ：教师模型定价与并发接口规范