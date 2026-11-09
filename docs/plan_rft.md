# RFT 优化方案：先保证 12 小时内跑完，再用 LoRA 压缩推理 token

## 背景

带 LoRA 的 `submission.zip` 提交后整份报错、没有分数。目标是让最终推理不超时，并让模型有时间改出补丁。可用算力约 15–30 小时 L4×4。

## 诊断

### 1. 全局超时：配置没有改
- 原提交包是 `max_time_minutes: 8`，最坏情况 120 × 8 min = **16 h**。
- 用 dev60 基线模拟（按单题时长放大后封顶，另假设启动和 setup 共 0.5 h）：

| 单题上限 | 无 LoRA | LoRA 慢 15% | LoRA 慢 30% |
|---|---|---|---|
| 8 min | 11.14 h | **12.28 h** | **13.15 h** |
| 5.5 min | 9.69 h | 10.19 h | 10.56 h |
| 5 min | 9.17 h | 9.57 h | 9.88 h |

- 结论：基线本来就贴着上限，LoRA 的额外延迟很可能把它推过了 12 h。15%–30% 这个开销来自社区和 `docs/TASK.md` 的估计，本项目还没有实测，由 Gate 2c 确认。

### 2. Adapter 几乎没有训动，却要承担 serving 开销
- 旧配置只有 38 个优化步，lr 为 5e-5。cosine 调度下 Adam 每个参数的累计移动上限约 1.0e-3，而实测 `max|lora_B|` = **0.00096**，几乎贴满上限。说明训练是被“步数 × 学习率”卡住的，不是收敛了。
- 本地没有 `train_meta.json` 和 LoRA 的 dev-eval 结果，所以“行为几乎没变”还是假设。

### 3. 优化目标选错了：筛的是调用次数，耗时取决于生成 token
- 基线耗时的回归结果：每步约 4.0 s + 0.0289 s/token，decode 约占 80%（`trace_stats.py` 的粗算口径约 95%）。
- 按 LoRA 慢 30% 估算，`rft_p1b` 的 16 条选中轨迹里有 **7 条超过 300 s**，最长约 775 s。
- 没有“思考要短”的信号，而且 `rft_p1b` 是在 `thinking_budget=4096` 下采的。如果之后改 thinking 配置，这批数据就不再 on-policy，所以**必须先定配置，再采样**。

### 4. 只降上限会丢分，必须同时减 token
- 基线 12 个 resolved 里，最后一次编辑晚于 300 s 的有 3 个（`fastapi_5624`、`rich_3506`、`rich_3454`）。若 LoRA 慢 30%，有效时间约为 231 s，晚于 240 s 的已有 5 个。
- 首次编辑时间中位数是 197 s。静态估算：单步封顶 1024 / 512 token，最多可减少 21.7% / 37.9% 的 token，实际节省以对照实验为准。

## 目标与验收

| 指标 | 基线 | 目标 |
|---|---|---|
| 120 题最坏时长（上限 × 120） | 16 h | ≤ 10 h（5 min） |
| 预估总时长（含 Gate 2c 实测开销） | 超过 12 h（LoRA） | ≤ 10.8 h |
| 每题生成 token 均值 | 8718 | ≤ 5000 |
| 首次编辑时间中位数 | 197 s | ≤ 120 s |
| resolved（56 题成对） | 基线重跑 | 净胜 > 2 × 基线自身翻转数 |

**退出条件：** LoRA 过不了 Gate 就不提交，最终用无 adapter 的最优配置。这是一个正常结果，不算失败。

## 运行顺序（合计约 18–22 h）

| 步骤 | 做什么 | 怎么做 | L4 时长 |
|---|---|---|---|
| 0 | 提交安全版 S0，确认报错消失 | 提交 `dist/submission_s0.zip` | 0 |
| 1 | thinking 对照 | 数据 notebook，`RUN_MODE='sweep'`；读 `sweep_report_<RUN_NAME>.md` | 约 1.5 h |
| 2 | 全量采样 | 把胜出值写进 `submission/configs/sampling.yaml` 并重新打包上传；`RUN_MODE='sample'`、`RESUME_DIR=<sweep 输出>`、`REUSE_ARM=<胜出组>`。同时提交这个无 adapter 版本 S1 | 约 3 h |
| 3 | 训练 | 训练 notebook，先 `SMOKE=True`，再全量 | 约 4 h |
| 4 | 看训练 notebook 第 8 节的信号 | 留出题的加权 NLL、`<channel\|>` logprob、`max\|lora_B\|` 与上限的比值。全是负面信号就回到数据或超参 | 0 |
| 5 | Gate 2a/2c：20 题串行 | dev-eval notebook，挂 `submission.zip` | 约 1.5 h |
| 6 | Gate 2b：56 题 LoRA + 同配置基线 | dev-eval notebook | 约 8 h |
| 7 | 预留：alpha 或超参调整一次，重跑 Gate 2a | 先试 `submission_alpha32.zip` | 约 1.5 h |

若第 5 步不通过，就跳过第 6 步，省下约 8 h。按 Gate 2c 实测的 `s/step` 回填：`平均单题时长 × 120 + 启动 + setup ≤ 10.8 h`，并把 `LORA_SLOWDOWN` 改成实测值。

## 已实施的改动

### 提交配置（`submission/`）
- `eval_config.yaml`：`max_time_minutes: 5`，`timeout_seconds: 120`，`max_tool_calls: 28` 不变。
- `configs/sampling.yaml`：`max_output_tokens: 4096`；`thinking_budget: 1024` 是**对照出结果前的暂定值**。
- `prompts/system.md`：分钟数和 pytest 超时同步，增加“每次工具调用前只思考几句话”，收尾总结压到最短。
- 不再带 adapter。旧 adapter 移到 `rft/adapters_backup/lora_p1b/`；adapter 只由训练 notebook 加入。
- `scripts/package_submission.py`：`max_time_minutes × 120 > 10.5 h`，或 prompt 中的分钟数与配置不一致时，拒绝打包。

### 数据 notebook（`rft/gemma-4-rft-data.ipynb`）
- **thinking 对照**：`SWEEP_ARMS` 三组（1024 / 512 / 0），Train Pool 按仓库分层取 20 题（fastapi 11、rich 7、requests 2），每组每题 1 条，温度 0.2。proxy 按 slot 改写 thinking 参数，冒烟测试确认 vLLM 确实生效。
- **选组规则**：在“可信通过数”不低于最优组减 2 的组里，选预估耗时最短的。样本量小，通过数的小差异应视为噪声。
- **筛选**：`est_serving_s = n_turns × 4.0 + completion_tokens × 0.0289 × LORA_SLOWDOWN(1.3)`，要求 ≤ 270 s，按它排序。题数 < 12 时放宽到 330 s，并写进报告。`rft_p1b` 在 270 s 下只剩 19 条样本、8 题，330 s 下 29 条、13 题，所以必须重新采样。原来的 `ALL_PASS_CALLS_LIMIT` 由这个条件取代。
- **`long_output` mask**：生成超过 `max(1.5 × thinking_budget, 768)` 的 turn 不计 loss。
- **报告**：增加全部、通过、选中轨迹的 token 分布、`est_serving_s`、首次编辑前 token 数。
- **门控**：模板核对区分 `known_whitespace` 和 `mismatch`，真实差异在所有输出写完后报错；有 adapter 时直接报错；`max_time_minutes × 120 > 10.5 h` 时拒绝采样。

### 训练 notebook（`rft/gemma-4-rft-training.ipynb`）
- **超参**：`LR=1e-4`，`GRAD_ACCUM=2`，`EPOCHS=2`，约 150 步，累计移动上限约 7.6e-3（旧配置的 7.6 倍）。`MAX_TRAIN_HOURS=5`，`SAVE_EVERY_STEPS=25`。
- **决策 token 加权**：`<channel|>`、`<|tool_call>` 和工具名 token 在序列内的权重为 `DECISION_WEIGHT=5`。thinking 关闭时只有工具调用部分生效。
- **首次编辑 turn 加权**：`FIRST_EDIT_WEIGHT=2` 作用在 batch 内的序列之间，不在序列内部。序列基本上都是单 turn，放在序列内部的话，加权平均会把它抵消掉。
- **留出验证**：留 2–3 题（题数 < 8 时不留），每 25 步评估。导出的是最后一个加权 NLL 不超过当前最优值 0.5% 的 checkpoint。
- **诊断**：打印留出题的 `<channel|>` logprob 变化，以及 `max|lora_B|` 是否仍贴着 Adam 移动上限。
- **打包门控**：时间预算不通过、或 submission 指纹与采样时不一致，都不生成 zip（SMOKE 只警告；`ALLOW_SUBMISSION_MISMATCH=True` 可放行指纹检查）。
- **`ALPHA_VARIANTS=[16, 32]`**：α=32 是免训练的增强版本。
- `RFT_DATA_DIR`、`SUBMISSION_INPUT` 默认改为自动检测，避免误用旧数据集。

## 明确不做

- **不修改 eval notebook**（`notebooks/getting-started-gemma-4-developer-agent.ipynb`）。因此 `docs/TASK.md` Gate 1 的 vLLM 回放探针（base 与 adapter 对比 token 数、合法率）没有实现，adapter 是否在 vLLM 上生效要靠 Gate 2a 的结果判断。训练 notebook 第 8 节只是 HF 侧信号，不能替代。
- 用失败轨迹的“尽早编辑”前缀扩充数据：可能学到错误补丁。仅当第 2 步后入选题数 < 8 时再考虑。
- 外部数据（`docs/TASK.md` 阶段二）、DPO、改 rank 或 target_modules：现在的瓶颈是训练步数和目标函数，不是模型容量；算力也不够。

## 验证状态

**本地已验证（无 GPU，harness、tokenizer、safetensors 为桩，模型是 CPU 上的小模型）：**
- 两个 notebook 的代码单元都能编译。
- 在真实的 `rft_p1b/selection.json` 上复现 19 条 / 8 题和 29 条 / 13 题。
- 数据 notebook 的第 14–22 单元，用伪造样本跑通 sweep 和 sample 两种模式：胜出组选择、只复用胜出组样本、超时样本被拒、放宽规则、`long_output`、真实模板差异报错。
- 训练 notebook 的第 8、14、16、18、20、22 单元跑通：指纹门控、时间预算门控、checkpoint 回退、SMOKE 2 步、无留出集。
- `package_submission.py`：8 分钟和分钟数不一致都被拒绝，5 分钟通过。

**尚未验证（需要在 Kaggle 上运行）：**
- `thinking_token_budget` 和 `enable_thinking` 的改写是否真的被 vLLM 采纳（sweep 的冒烟测试会检查）。
- 真实的 Gemma 4 tokenizer 下，`<channel|>` 是否是单 token（notebook 有断言）、工具名是否在 `{` 之前结束。
- 新超参的实际训练时长和效果。

## 依据与来源

- 本地数据：`exp_res/results_baseline_1017_60dev`、`rft/rft_data_rft_p1b`、`rft/adapters_backup/lora_p1b`。
- [damsolanke/gemma4-swe-kit](https://github.com/damsolanke/gemma4-swe-kit)：scorer 时间估算（每次调用 0.6 s + token/25.5 tok/s）；thinking 关闭 9/12 对 budget 256 的 7/12，预估 4.0 对 4.7 分钟（仅 12 题的小样本）；lr 5e-5 配 1000 iters。
- [lmassaron/gemma-4-developer-agent](https://github.com/lmassaron/gemma-4-developer-agent)：`max_time_minutes 5.5`、`thinking_budget 1536`、`temperature 0.15`、`timeout_seconds 90`。
- [chiba244qq/gemma4-agent-dashboard](https://github.com/chiba244qq/gemma4-agent-dashboard)：作者统计 0 次压缩 47.7% 解决、1 次 26.4%、2 次及以上 7.1%（相关性，非因果）。
- [karthik-a/sparkflashxgemma4](https://huggingface.co/karthik-a/sparkflashxgemma4)：lr 1e-4、2 epochs、α=2r，作者称 adapter “shape output format, not capability”。
- [Kaggle 讨论 751596](https://www.kaggle.com/competitions/gemma-4-developer-agent/discussion/751596)：正文无法抓取。LoRA 开销 15–20%、decode 约慢 1.3 倍只来自搜索结果摘要，未核实原帖。
