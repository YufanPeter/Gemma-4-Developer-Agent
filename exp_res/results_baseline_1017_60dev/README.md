# baseline, dev60 (2026-10-17)

- Submission: original baseline on `main`, fingerprint `dd3f43378887`. Leaderboard 0.17.
- What changed: nothing; this is the reference point.
- Result here: 12/60. This run had no extra test deps, so 18 tasks were `env_error`. Re-verifying the same patches with the extra deps gives **19/60**: this is the baseline number every later run is compared with.
- Other numbers: 13 empty patches, 5.4 min per task on average, 10 tasks hit the 8-minute limit.
- Findings: `triage_report.md` (late first edit, duplicate calls, malformed `edit_file` args).
