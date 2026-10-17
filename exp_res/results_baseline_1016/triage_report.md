# Dev-eval triage

- tasks: **30**
- resolved: **5/30**  (resolution rate = **0.167**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 5 | 17% | - |  |
| no_patch_budget | 6 | 20% | 24% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| scratch_only | 3 | 10% | 12% | agency/prompt -> agent only wrote repro scripts, never edited source; scripts belong in /tmp |
| broken_code | 1 | 3% | 4% | edit hygiene -> an edit broke the file's syntax; py_compile every edited file before submitting |
| env_error | 8 | 27% | 32% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 6 | 20% | 24% | localization/reasoning -> find the right file/symbol before editing |
| patch_rejected | 1 | 3% | 4% | inspect logs -> couldn't auto-classify; eyeball the linked pytest log |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 0/8 |
| encode/httpx | 0/1 |
| fastapi/fastapi | 5/14 |
| psf/requests | 0/7 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 5 |  |  |  |  |  |
| no_patch_budget |  |  |  |  |  | 6 |
| scratch_only |  |  |  |  | 3 |  |
| broken_code | 1 |  |  |  |  |  |
| env_error | 7 | 1 |  |  |  |  |
| wrong_fix | 5 |  |  | 1 |  |  |
| patch_rejected | 1 |  |  |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `rich_3675` missed 1: `rich/diagnose.py`
- `rich_3676` missed 1: `rich/default_styles.py`
- `fastapi_13786` missed 10: `docs_src/authentication_error_status_code/tutorial001_an.py`, `docs_src/authentication_error_status_code/tutorial001_an_py39.py`, `docs_src/security/tutorial003.py`, `docs_src/security/tutorial003_an.py`, `docs_src/security/tutorial003_an_py310.py` ...

## Examples

### no_patch_budget  (6)
- `rich_3944` (Textualize/rich) [loc: no_edit] -- no patch; spent 12/28 tool calls, 4.5/4.5 min
- `rich_3180` (Textualize/rich) [loc: no_edit] -- no patch; spent 7/28 tool calls, 4.5/4.5 min
- `httpx_3672` (encode/httpx) [loc: no_edit] -- no patch; spent 20/28 tool calls, 4.5/4.5 min
  ...and 3 more

### scratch_only  (3)
- `fastapi_14360` (fastapi/fastapi) [loc: scratch_only] -- patch only adds scratch files (repro scripts); no source file edited
    - patch: `exp_res/results_baseline_1016/patches/fastapi_14360.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/fastapi_14360.log`
- `requests_6629` (psf/requests) [loc: scratch_only] -- patch only adds scratch files (repro scripts); no source file edited
    - patch: `exp_res/results_baseline_1016/patches/requests_6629.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/requests_6629.log`
- `fastapi_11355` (fastapi/fastapi) [loc: scratch_only] -- patch only adds scratch files (repro scripts); no source file edited
    - patch: `exp_res/results_baseline_1016/patches/fastapi_11355.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/fastapi_11355.log`

### broken_code  (1)
- `fastapi_9753` (fastapi/fastapi) [loc: same_lines] -- IndentationError in fastapi/dependencies/models.py:20 -- patched source does not import
    - patch: `exp_res/results_baseline_1016/patches/fastapi_9753.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/fastapi_9753.log`

### env_error  (8)
- `fastapi_14964` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'inline_snapshot' (collection error, patch untested)
    - patch: `exp_res/results_baseline_1016/patches/fastapi_14964.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/fastapi_14964.log`
- `fastapi_13786` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'dirty_equals' (collection error, patch untested)
    - patch: `exp_res/results_baseline_1016/patches/fastapi_13786.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/fastapi_13786.log`
- `requests_7328` (psf/requests) [loc: same_lines] -- broken pytest fixture (httpbin/plugin not loaded); patch untested -- and target tests failed/errored too
    - patch: `exp_res/results_baseline_1016/patches/requests_7328.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/requests_7328.log`
  ...and 5 more

### wrong_fix  (6)
- `rich_3782` (Textualize/rich) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_syntax.py::test_padding_plus_wrap
    - patch: `exp_res/results_baseline_1016/patches/rich_3782.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/rich_3782.log`
- `rich_3675` (Textualize/rich) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_console.py::test_tty_compatible
    - patch: `exp_res/results_baseline_1016/patches/rich_3675.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/rich_3675.log`
- `rich_3676` (Textualize/rich) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_traceback.py::test_notes
    - patch: `exp_res/results_baseline_1016/patches/rich_3676.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/rich_3676.log`
  ...and 3 more

### patch_rejected  (1)
- `rich_3480` (Textualize/rich) [loc: same_lines] -- patch applied but not resolved (no pytest log to inspect)
    - patch: `exp_res/results_baseline_1016/patches/rich_3480.patch`
    - test_log: `exp_res/results_baseline_1016/test_outputs/rich_3480.log`
