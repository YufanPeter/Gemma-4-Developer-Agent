# code-nav v2, dev60 (2026-10-26)

- Submission: commit `92a4a64`, fingerprint `ea0d164b49f9`. Leaderboard **0.15** (baseline 0.17; within noise of about ±0.03).
- What changed vs v1: the ROOT bug is fixed. The skill is installed once with `run_skill_script`, and after that every command runs as `python3 /tmp/nav.py ...` through `run_command`, which takes a single string argument and almost never has argument errors.
- Result: **22/60** again (vs prompt v2: gained fastapi_12942, 14482, rich_4075; lost rich_2725, 3480, 3506).
- The skill worked mechanically: installed in 57/60 tasks, 575 nav calls.
- Other numbers: 12 empty patches, 5.7 min per task, 18 tasks hit the 8-minute limit (v2: 9).
- Main problem seen in the traces: context compaction (57/60 tasks) makes the model forget what it read; it then re-searches with grep / read_file instead of nav. Only 9 tasks were solved in all four dev60 runs, so differences of a few tasks are noise.
- The later v4 (status line, time left, dense output; uncommitted, fingerprint `37fc48afe3f1`) only had a 10-task smoke run: 5/10. That folder was deleted; its numbers are in the project memory notes.
