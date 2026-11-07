# Dev-eval triage

- tasks: **60**
- resolved: **27/60**  (resolution rate = **0.450**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 27 | 45% | - |  |
| no_patch_budget | 5 | 8% | 15% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| env_error | 5 | 8% | 15% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 21 | 35% | 64% | localization/reasoning -> find the right file/symbol before editing |
| patch_rejected | 2 | 3% | 6% | inspect logs -> couldn't auto-classify; eyeball the linked pytest log |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 9/22 |
| encode/httpx | 1/1 |
| fastapi/fastapi | 12/31 |
| psf/requests | 5/6 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 27 |  |  |  |  |  |
| no_patch_budget |  |  |  |  |  | 5 |
| env_error | 4 |  |  | 1 |  |  |
| wrong_fix | 11 | 3 | 3 | 4 |  |  |
| patch_rejected | 1 |  | 1 |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `fastapi_14262` missed 8: `docs_src/dependencies/tutorial008e.py`, `docs_src/dependencies/tutorial008e_an.py`, `docs_src/dependencies/tutorial008e_an_py39.py`, `fastapi/dependencies/models.py`, `fastapi/dependencies/utils.py` ...
- `fastapi_14448` missed 1: `fastapi/dependencies/utils.py`
- `rich_3535` missed 1: `rich/segment.py`
- `rich_3675` missed 1: `rich/diagnose.py`
- `fastapi_14371` missed 3: `fastapi/_compat/v2.py`, `fastapi/dependencies/utils.py`, `fastapi/openapi/utils.py`
- `rich_3777` missed 1: `rich/diagnose.py`
- `rich_3934` missed 1: `rich/live.py`
- `fastapi_14953` missed 3: `docs_src/json_base64_bytes/tutorial001_py310.py`, `fastapi/_compat/v2.py`, `scripts/playwright/json_base64_bytes/image01.py`
- `fastapi_15280` missed 1: `docs_src/vibe/tutorial001_py310.py`
- `fastapi_15745` missed 3: `docs_src/path_operation_advanced_configuration/tutorial002_py310.py`, `fastapi/applications.py`, `fastapi/openapi/utils.py`

## Examples

### no_patch_budget  (5)
- `rich_3063` (Textualize/rich) [loc: no_edit] -- no patch; spent 24/28 tool calls, 8.3/8.0 min
- `fastapi_14246` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 4.9/8.0 min
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/fastapi_14246.log`
- `rich_3105` (Textualize/rich) [loc: no_edit] -- no patch; spent 27/28 tool calls, 8.1/8.0 min
  ...and 2 more

### env_error  (5)
- `fastapi_14262` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'docs_src.dependencies.tutorial008e' (collection error, patch untested) -- and target tests failed/errored too
    - patch: `exp_res/results_v7_dev60_1107/patches/fastapi_14262.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/fastapi_14262.log`
- `fastapi_14605` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'fastapi.dependencies.exceptions' (collection error, patch untested)
    - patch: `exp_res/results_v7_dev60_1107/patches/fastapi_14605.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/fastapi_14605.log`
- `fastapi_14953` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'docs_src.json_base64_bytes' (collection error, patch untested) -- and target tests failed/errored too
    - patch: `exp_res/results_v7_dev60_1107/patches/fastapi_14953.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/fastapi_14953.log`
  ...and 2 more

### wrong_fix  (21)
- `fastapi_13713` (fastapi/fastapi) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_application.py::test_openapi_schema
    - patch: `exp_res/results_v7_dev60_1107/patches/fastapi_13713.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/fastapi_13713.log`
- `fastapi_5624` (fastapi/fastapi) [loc: same_file] -- target test(s) still failing (1 failed): tests/test_security_scopes_dont_propagate.py::test_security_scopes_dont_propagate
    - patch: `exp_res/results_v7_dev60_1107/patches/fastapi_5624.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/fastapi_5624.log`
- `rich_3278` (Textualize/rich) [loc: same_lines] -- target test(s) still failing (4 failed): tests/test_ansi.py::test_decode_issue_2688[\x1b[31mFound
    - patch: `exp_res/results_v7_dev60_1107/patches/rich_3278.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/rich_3278.log`
  ...and 18 more

### patch_rejected  (2)
- `rich_3777` (Textualize/rich) [loc: same_file] -- patch applied but not resolved (no pytest log to inspect)
    - patch: `exp_res/results_v7_dev60_1107/patches/rich_3777.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/rich_3777.log`
- `rich_4006` (Textualize/rich) [loc: same_lines] -- patch applied but not resolved (no pytest log to inspect)
    - patch: `exp_res/results_v7_dev60_1107/patches/rich_4006.patch`
    - test_log: `exp_res/results_v7_dev60_1107/test_outputs/rich_4006.log`
