# Files

- `train.jsonl`: one training sequence per line. `input_ids` are the exact tokens vLLM saw / generated (Gemma 4 tokenizer); `labels` = token id on trained positions, -100 elsewhere (already aligned with `input_ids`, the model shifts internally). Extra keys: id, task_id, sample, segment, n_tokens, n_loss_tokens, n_loss_turns, turns, loss_turns.
- `train_readable.jsonl`: the chosen trajectories as text, `train: true` on loss turns, `mask_reason` on window turns left out of the loss.
- `selection.json`: every task / sample with pass status, budget usage and the selection decision (`chosen_ks`, `key_turns`).
- `stats.json`, `report.md`: run summary.

Load: `datasets.load_dataset("json", data_files="train.jsonl")` and keep `input_ids` / `labels`.
