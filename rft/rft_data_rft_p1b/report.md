# RFT data `rft_p1b`

- submission `dd3f43378887`, model `gemma-4-31b-it-qat-w4a16-ct`, temperature 0.7, top_p 0.95, max_tool_calls 28, model told 8.0 min
- samples: 186 (79 resolved) over 57 tasks
- task status: {'all_fail': 30, 'all_pass': 16, 'sometimes': 11}
- selection: {'edit_call_limit': 12, 'all_pass_calls_limit': 20, 'max_per_task_all_pass': 1, 'max_per_task_sometimes': 2, 'good_locs': ['same_lines', 'same_region', 'same_file'], 'post_edit_keep': 2, 'max_seq_len': 16384}
- chosen: 16 trajectories over 16 tasks {'all_pass': 11, 'sometimes': 5}
- loss turns: 164 key of 240 window turns; masked {'post_edit_excess': 57, 'discarded_edit': 1, 'duplicate': 5, 'failed_tool': 11}
- sequences: 164 | tokens: 1590226 | loss tokens: 64538
- audit: untrusted passes 8, {'sampling_temperature': 0.7, 'submission_temperature': 0.2, 'median_first_edit_call': 15, 'median_calls': 27.5, 'budget_exhausted': '90/186', 'dev60_baseline': {'median_first_edit_call': 19, 'budget_exhausted': '31/60'}}, sampling wall clock seen {'get_status_calls': 1, 'trajectories_seeing_inflated_time': 1, 'loss_turns_after_inflated_time': 0}
- template check (vLLM prompt ids == apply_chat_template): {'mismatch': 291, 'match': 9}
- packing: {'results_unmatched': 9, 'loss_turns_truncated_output': 0, 'chain_break': 281, 'turn_too_long_selected': 2}
- median edit@call: {'all': 15, 'passing': 11, 'chosen': 8.5}

| task | pass/n | status | chosen k | calls | edit@call | gold read @turn | loss window | key/window turns | masked |
|---|---|---|---|---|---|---|---|---|---|
| fastapi_11355 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_13537 | 4/4 | all_pass | 1 | 11 | 7 | 1 | (2, 12) | 9/11 | post_edit_excess 2 |
| fastapi_13786 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_13920 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_14077 | 4/4 | all_pass | 0 | 18 | 7 | 2 | (3, 20) | 11/18 | post_edit_excess 5, discarded_edit 1, duplicate 1 |
| fastapi_14099 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_14258 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_14266 | 1/2 | sometimes (no pass edits by call 12) |  |  |  |  |  |  |  |
| fastapi_14301 | 4/4 | all_pass | 3 | 19 | 9 | 2 | (3, 20) | 18/18 |  |
| fastapi_14306 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_14360 | 4/4 | all_pass (too_many_calls (all-pass, > 20)) |  |  |  |  |  |  |  |
| fastapi_14372 | 4/4 | all_pass | 2 | 14 | 10 | 1 | (2, 14) | 11/13 | post_edit_excess 2 |
| fastapi_14419 | 1/2 | sometimes (no pass edits by call 12) |  |  |  |  |  |  |  |
| fastapi_14459 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_14463 | 1/2 | sometimes | 1 | 26 | 11 | 1 | (2, 27) | 12/26 | failed_tool 11, post_edit_excess 3 |
| fastapi_14485 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_14492 | 4/4 | all_pass | 3 | 9 | 3 | 1 | (2, 9) | 4/8 | post_edit_excess 3, duplicate 1 |
| fastapi_14512 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_14583 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_14609 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_14791 | 1/2 | sometimes | 1 | 12 | 7 | 0 | (1, 13) | 9/13 | post_edit_excess 4 |
| fastapi_14851 | 4/4 | all_pass (bad_loc (no pass in same_lines/same_region/same_file)) |  |  |  |  |  |  |  |
| fastapi_14962 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_14964 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_14986 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_15030 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_15588 | 0/4 | all_fail |  |  |  |  |  |  |  |
| fastapi_15661 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_15800 | 0/2 | all_fail |  |  |  |  |  |  |  |
| fastapi_5077 | 4/4 | all_pass (too_many_calls (all-pass, > 20)) |  |  |  |  |  |  |  |
| fastapi_9425 | 1/2 | sometimes (no pass edits by call 12) |  |  |  |  |  |  |  |
| fastapi_9555 | 4/4 | all_pass | 3 | 14 | 8 | 4 | (5, 15) | 6/11 | post_edit_excess 5 |
| fastapi_9753 | 3/4 | sometimes | 1 | 15 | 11 | 6 | (7, 16) | 9/10 | post_edit_excess 1 |
| requests_6644 | 4/4 | all_pass (bad_loc (no pass in same_lines/same_region/same_file)) |  |  |  |  |  |  |  |
| requests_7309 | 4/4 | all_pass | 0 | 12 | 7 | 1 | (2, 12) | 8/11 | post_edit_excess 3 |
| requests_7315 | 4/4 | all_pass | 1 | 15 | 9 | 1 | (2, 15) | 10/14 | post_edit_excess 3, duplicate 1 |
| requests_7433 | 4/4 | all_pass | 1 | 12 | 5 | 1 | (2, 12) | 6/11 | post_edit_excess 5 |
| requests_7505 | 1/2 | sometimes | 0 | 25 | 11 | 1 | (2, 27) | 22/26 | post_edit_excess 3, duplicate 1 |
| rich_2943 | 3/4 | sometimes (no pass edits by call 12) |  |  |  |  |  |  |  |
| rich_3006 | 4/4 | all_pass | 0 | 17 | 8 | 5 | (6, 17) | 7/12 | post_edit_excess 5 |
| rich_3052 | 0/4 | all_fail |  |  |  |  |  |  |  |
| rich_3061 | 0/4 | all_fail |  |  |  |  |  |  |  |
| rich_3180 | 1/2 | sometimes | 1 | 21 | 10 | 0 | (1, 23) | 12/23 | post_edit_excess 10, duplicate 1 |
| rich_3471 | 0/4 | all_fail |  |  |  |  |  |  |  |
| rich_3472 | 0/2 | all_fail |  |  |  |  |  |  |  |
| rich_3518 | 4/4 | all_pass (no pass edits by call 12) |  |  |  |  |  |  |  |
| rich_3521 | 1/2 | sometimes (no pass edits by call 12) |  |  |  |  |  |  |  |
| rich_3676 | 1/4 | sometimes (no pass edits by call 12) |  |  |  |  |  |  |  |
| rich_3772 | 0/2 | all_fail |  |  |  |  |  |  |  |
| rich_3905 | 4/4 | all_pass | 2 | 15 | 10 | 0 | (1, 15) | 12/15 | post_edit_excess 3 |
| rich_3930 | 0/4 | all_fail |  |  |  |  |  |  |  |
| rich_3935 | 0/2 | all_fail |  |  |  |  |  |  |  |
| rich_3942 | 0/4 | all_fail |  |  |  |  |  |  |  |
| rich_3953 | 0/4 | all_fail |  |  |  |  |  |  |  |
| rich_4070 | 0/4 | all_fail |  |  |  |  |  |  |  |
| rich_4076 | 0/2 | all_fail |  |  |  |  |  |  |  |
| rich_4079 | 0/2 | all_fail |  |  |  |  |  |  |  |
