# rft/ — RFT 数据构造（TASK.md §2，阶段一）

整体方案和运行顺序见 `docs/plan_rft.md`。

`gemma-4-rft-data.ipynb` 在 Kaggle（L4×4，离线）上运行，不含训练。先用 `RUN_MODE='sweep'` 对比 thinking budget，再用 `RUN_MODE='sample'` 跑以下流程：

1. Train Pool 57 题的 on-policy 采样（自适应 K）
2. Phase-2 验证
3. 高效通过筛选（选正样本）
4. 自采样审计
5. 关键 turn 的 loss mask 与打包

## 运行
1. 挂载 dev-eval notebook 用的全部输入：
   - 竞赛数据
   - `gemma-4-31b-it-qat-w4a16-ct` 模型
   - wheelhouse
   - `tianyuxu2/gemma-4-extra-test-wheels`
   - 你的 submission 数据集（不带 adapter）

   Accelerator 选 **GPU L4x4**，Internet 关闭。用 `kaggle kernels push -p rft` 推送时，`kernel-metadata.json` 已列好这些输入，但 L4x4 需要在网页上手动选。
2. 对照（`RUN_MODE='sweep'`，约 1.5 h）：`SWEEP_ARMS` 每组在 `SWEEP_N_TASKS` 题上各跑 1 条，输出 `sweep_report_<RUN_NAME>.md`。此模式的训练数据为空，属于预期行为。第 4 节的冒烟测试会确认 vLLM 采纳了改写后的 thinking budget。
3. 把胜出值写进 submission 的 `configs/sampling.yaml`，重新打包上传。之后设 `RUN_MODE='sample'`、`RESUME_DIR=<对照的输出>`、`REUSE_ARM=<胜出组>`，只复用胜出组的样本。
4. 先小跑：`TRAIN_IDS = TRAIN_IDS[:2]`、`K_FIRST = 1`、`K_EXTRA = 0`。确认以下几点：
   - 第 4 节冒烟测试通过（vLLM 返回了 token ids）
   - 第 7 节模板核对没有 `mismatch`（`known_whitespace` 是已知的空格差异，不影响训练）
   - 打印出的 label 片段是 `<|channel>thought…<channel|>` + `<|tool_call>…`
5. 全量：*Save Version → Save & Run All*，预计约 3 h 采样。
6. 超时没跑完时：把上一版本的 Output 挂上来，设置 `RESUME_DIR=<其挂载路径>`，再跑一次。已完成的样本会被复用。
7. 只想换筛选或打包参数时：设置 `SKIP_SAMPLING=True` + `RESUME_DIR`，无需 GPU，但仍要挂模型以加载 tokenizer。参数包括 `EDIT_CALL_LIMIT`、`MAX_EST_SERVING_S`、`FALLBACK_EST_SERVING_S`、`LORA_SLOWDOWN`、`MAX_PER_TASK_*`、`GOOD_LOCS`、`LONG_OUTPUT_*`、`POST_EDIT_KEEP`、`MAX_SEQ_LEN`。换一个 `RUN_NAME`，不要覆盖旧输出。

## 关键实现
- **Logging Proxy**：每个并发 slot 一个端口，`api_base` 被改写为指向它。
  - 记录原始 `/v1/chat/completions` 的请求与响应。
  - 通过 `return_token_ids` 拿到 vLLM 实际的 prompt / 生成 token ids。训练序列直接用这些 ids，不重新 tokenize。
- **采样参数**：代理强制 `temperature=0.7`、`top_p=0.95`；thinking 配置沿用 submission。对照时温度为 `SWEEP_TEMPERATURE`，thinking 参数按组改写。
- **时间预算**：harness 的墙钟预算放宽到 60 min，代理再把 prompt 里的 `Time allowance` 和 `get_status` 的 `max_time_minutes` 改回 submission 的值。
  - 未改写的残留：`get_status` 的 `time_seconds_remaining`，以及命令超时上限，仍按 60 min 计算。
- **轨迹选择（高效通过）**：耗时主要由生成 token 决定，所以按预估耗时筛选。部分通过的题和全部通过的题都可以入选。
  - 正样本须同时满足：
    - 通过测试
    - 补丁碰到参考文件，即 `loc` 属于 `GOOD_LOCS`（`same_lines` / `same_region` / `same_file`）。`no_edit`、`scratch_only` 的通过多半是目标测试不改代码也能过，`wrong_file` 的通过要人工确认。
    - 首次源码编辑 ≤ 第 `EDIT_CALL_LIMIT` 次调用
    - `est_serving_s = n_turns × 4.0 + completion_tokens × 0.0289 × LORA_SLOWDOWN` ≤ `MAX_EST_SERVING_S`。入选题数不足 `MIN_TASKS_BEFORE_FALLBACK` 时放宽到 `FALLBACK_EST_SERVING_S`，并写进报告。
  - 每题按预估耗时从短到长取：全部通过的题最多 `MAX_PER_TASK_ALL_PASS` 条，部分通过的题最多 `MAX_PER_TASK_SOMETIMES` 条。
  - 调用和编辑的计数与 `eval/trace_stats.py` 一致。`selection.json` 里每题的 `chosen_ks` 是选中样本，`reason` 是落选原因。
- **自采样审计**（第 6b 节，结果在 `stats.json` 的 `audit`）：
  - 不可信的通过样本
  - 采样温度下的行为与 dev60 基线的对照
  - 模型是否在 `get_status` 里看到采样用的 60 分钟墙钟
  - proxy 与 harness 的调用计数差异
- **loss 窗口**：从首次读到 gold 文件（参考补丁里的源文件）的下一个 turn 开始，一直到 `submit_patch` 所在的 turn；首次源码编辑总在窗口内。
  - 被重试替换的回复不计 loss，`finish_reason=length` 的截断回复也不计。
- **关键 turn mask**：窗口内以下 turn 不计 loss，但仍留在后续 turn 的上下文里。工具结果从下一轮请求的 tool 消息里读取。
  - `failed_tool`：文件工具报错，或参数被 harness 拒绝。`run_command` 的非零退出不算（可能是测试失败或 grep 没匹配到）。
  - `status_only`：只调用了 `get_status`。
  - `long_output`：生成超过 `max(LONG_OUTPUT_FACTOR × thinking_budget, LONG_OUTPUT_MIN)` 个 token。
  - `discarded_edit`：`edit_file` 编辑了最终补丁里没有的源文件。
  - `duplicate`：调用与之前完全相同，且期间没有成功的源码编辑。所以改完代码后重跑测试不算重复。
  - `post_edit_excess`：最后一次有效编辑之后、提交之前，超过 `POST_EDIT_KEEP` 个的验证 turn。
  - 首次编辑 turn 和提交 turn 不受后两条影响。每个 turn 的原因写在 `train_readable.jsonl` 的 `mask_reason` 里。
- **打包**：后一个 prompt 以“前一个 prompt + 前一次生成”为 token 前缀时，就并入同一条序列。遇到上下文压缩、模板重渲染，或超过 `MAX_SEQ_LEN` 时，另起一段。
  - 实际上历史会重新渲染前几轮的生成，所以前缀从来接不上（`rft_p1` 中合并数为 0），每个 loss turn 单独成一条序列。这样做是对的，代价是每个 loss turn 都要付出完整 prompt 的 token。因此关键 turn 的数量就是训练算力的主要旋钮。
  - labels 只在选中 turn 的生成 token 上取值，包括紧随其后的 turn 结束 token。

## 输出（`rft_data_<RUN_NAME>.zip`）
| 文件 | 内容 |
|---|---|
| `train.jsonl` | 每行一条序列：`input_ids`、`labels`（-100 = 不计 loss），以及 `task_id`、`sample`、`n_tokens`、`n_loss_tokens`、`first_edit` 等 |
| `train_readable.jsonl` | 选中轨迹的可读文本，loss turn 标 `train: true` |
| `selection.json` | 每题、每个样本的通过情况、预算使用、筛选结论 |
| `stats.json` / `report.md` | 汇总：摇摆题数、序列数、token 分布、`est_serving_s`、模板核对、prefix 合并率 |

另外还有 `rft_samples_<RUN_NAME>.zip`：每条样本的原始代理日志、ATIF trace、patch、测试日志，用于续跑和排查。对照运行还会输出 `sweep_report_<RUN_NAME>.md` / `.json`。

---

# 训练：`gemma-4-rft-training.ipynb`（TASK.md §3）

直接在比赛的 `gemma-4-31b-it-qat-w4a16-ct` 上训练 LoRA：int4 权重冻结，LoRA 在其上训练，训练与推理用的是同一份 base 权重。产出可直接提交的 `submission.zip`。

## 运行
1. 挂载以下输入。Accelerator 选 **GPU L4x4**，Internet 关闭。
   - 竞赛数据
   - `gemma-4-31b-it-qat-w4a16-ct` 模型
   - wheelhouse
   - RFT 数据：把 `rft_data_<RUN_NAME>/` 上传为 dataset，或挂数据 notebook 的 Output
   - 你的 submission 数据集：不带 adapter，且必须与采样时的 submission 相同。指纹不一致时不生成 zip（`ALLOW_SUBMISSION_MISMATCH=True` 可放行）。
2. 先设 `SMOKE = True` 小跑，约 20–30 min：5 条序列训练 2 步，然后导出、打包为 `submission_SMOKE.zip`（不要提交）。确认以下几点：
   - 第 4 节自检通过。
   - 第 6 节 base NLL < 2：确认 int4 反量化与 vLLM 一致。打印的显存峰值说明最长序列能放下。
   - 第 8 节“关掉 LoRA = base”。
   - 第 9 节 key 检查通过。
3. 全量：设 `SMOKE = False`，然后 *Save & Run All*。约 150 个优化步、4 h，`MAX_TRAIN_HOURS=5` 是上限，以第 7 节打印的 tok/s 和 ETA 为准。
4. 看第 8 节：留出题的加权 NLL、`<channel|>` logprob 变化、`max|lora_B|` 是否仍贴着 Adam 移动上限。
5. 把 `submission.zip` 挂到 dev-eval notebook，跑 Gate 2a/2c，通过后再跑 2b。`eval_config.yaml` 原样保留，时间预算在打包时检查。

## 关键实现
- **不依赖 peft / bitsandbytes / transformers 的 compressed-tensors 量化器**：
  - `W4A16Linear` 按 `pack-quantized` 格式即时反量化（每个 int32 装 8 个 int4，低位在前，减去偏移 8，乘以 group scale）。
  - 自定义 autograd 在 backward 里重新反量化，不保存 bf16 权重。
  - `LoRALinear` 的 A/B 为 fp32，初始化与 PEFT 相同。
- **多卡**：
  - 只建文本模型。decoder 层按“权重 + 激活”连续地分到 4 张卡上，GPU 0 还放 embedding 和 logits。
  - pre-hook 负责跨卡搬运输入，mask / RoPE 每步每卡只拷一次。
  - 开 non-reentrant gradient checkpointing。
  - logits 只在 loss 位置计算。
- **简短而正确**：
  - 去掉没有 `<|tool_call>` 的 turn。这是 ADK 上下文压缩的 summary，`rft_p1b` 中有 7 条。
  - 去掉生成超过 `MAX_LOSS_TOKENS=1536` 的长思考 turn（6 条）。
  - loss 先在序列内按 token 加权平均（`<channel|>`、`<|tool_call>`、工具名的权重为 `DECISION_WEIGHT`），再在 batch 内按序列加权平均（首次编辑 turn 的权重为 `FIRST_EDIT_WEIGHT`）。
- **留出验证**：留 2–3 题不参加训练，每 `SAVE_EVERY_STEPS` 步评估。导出的是最后一个加权 NLL 不超过当前最优值 `HOLDOUT_TOL` 的 checkpoint。
- **超参**（TASK.md §3.2）：
  - r=16，α=16，`q/k/v/o_proj`（只在语言模型层）
  - lr 1e-4，cosine 调度，warmup 0.1
  - 2 epochs，`GRAD_ACCUM=2`
  - 导出 bf16，不 merge
- **导出**：
  - adapter 的 key 用 checkpoint 自己的模块名：`base_model.model.model.language_model.layers.{i}.self_attn.*_proj.lora_{A,B}.weight`。
  - `target_modules` 写成列表。
  - 逐个模块核对：是 checkpoint 中的 int4 Linear，形状为 `[r,in]` / `[out,r]`。
- **推理期 alpha**：`ALPHA_VARIANTS` 默认为 `[16, 32]`，会另外产出 `submission_alpha32.zip`。
- **打包门控**：`max_time_minutes × 120 > 10.5 h`、prompt 分钟数不一致、或指纹不一致时，不生成 zip（SMOKE 只警告）。

## 输出
| 文件 | 内容 |
|---|---|
| `submission.zip` | 原 submission + `adapters/main_lora/`；`agent.yaml` 中已加 `adapter: main_lora` |
| `main_lora/` | `adapter_config.json`、`adapter_model.safetensors` |
| `checkpoint_last/` | 训练中途保存的 adapter，每 `SAVE_EVERY_STEPS` 步一次 |
| `train_meta.json` / `train_log.jsonl` | 数据统计、超参、loss 曲线、留出集历史、选中的 step、训练前后探针（`max |Δlogprob|`） |
