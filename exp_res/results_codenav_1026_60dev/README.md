# code-nav v1, dev60 (2026-10-26)

- Submission: commit `38e0cc9`, fingerprint `8fb98e8d401a`. Not submitted to the leaderboard.
- What changed vs prompt v2: the `code-nav` skill was added (find / show / refs / tests / outline / check). Every call goes through `run_skill_script`.
- Result: **22/60**, the same as prompt v2 (+4 / -4 tasks).
- **The skill never worked.** `nav.py` hard-coded `ROOT=/workspace`, which does not exist in the Kaggle sandbox, so every call returned nothing. In addition, about half of the skill calls (261 in total) failed with INVALID_ARGUMENTS because the model swallowed the `skill_name` argument. So this run only measures the cost of a useless skill: 20 empty patches (v2 had 14).
- Useful takeaway: even with 261 broken calls the score did not drop. Broken calls do not count toward the 28-call budget; they only cost time.
