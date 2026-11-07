# code-nav v5, dev60 (2026-11-06)

- Submission: uncommitted v4+v5 changes on `yufanshi_exp`, fingerprint `10a3fc23057c`. Not submitted to the leaderboard.
- What changed vs v2: v4 nav (status line, seen-code tracking, multi-symbol show, dense output, SYNTAX ERROR banner); `check` now also lists related places (siblings missing a new parameter, copied lines, users of removed names) and callers in their own section; `thinking_budget` 4096 → 2048. Graph tools removed.
- Result: **21/60** (v2 22, prompt v2 22). vs v2: gained rich_2725, rich_3063, rich_3480; lost fastapi_14482, fastapi_15589, rich_3278, rich_4075. Within noise.
- Speed: 5.25 min per task (v2 5.71), 8 tasks hit the 8-minute limit (v2 18), 5.25 h for 60 tasks. Thinking 2048 made it faster without losing score.
- Buckets: wrong_fix 20 (v2 12), no_patch_budget 10 (v2 12), empty patches 10 (v2 12), patch_rejected 0 (v2 3).
- `check` ran in 33/60 tasks (34 calls), mostly as the last step (median call ~25). **No check call was followed by any edit**, here or in v2. The new related-places and callers lines were shown but never acted on, and also caused no wrong edits.
- Tasks whose reference patch touches 2+ source `.py` files: **0/16** in prompt v2, v2 and v5. Single-file: 21–22/44.
- Setup loop seen in the v4 smoke run did not recur (no task ran setup twice).
