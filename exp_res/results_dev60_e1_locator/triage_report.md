# Dev-eval triage

- tasks: **60**
- resolved: **18/60**  (resolution rate = **0.300**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 18 | 30% | - |  |
| no_patch_budget | 23 | 38% | 55% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| scratch_only | 2 | 3% | 5% | agency/prompt -> agent only wrote repro scripts, never edited source; scripts belong in /tmp |
| env_error | 1 | 2% | 2% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 13 | 22% | 31% | localization/reasoning -> find the right file/symbol before editing |
| regression | 3 | 5% | 7% | minimality -> make smaller, more targeted edits; don't touch unrelated code |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 8/22 |
| encode/httpx | 1/1 |
| fastapi/fastapi | 7/31 |
| psf/requests | 2/6 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 17 | 1 |  |  |  |  |
| no_patch_budget |  |  |  |  |  | 23 |
| scratch_only |  |  |  |  | 2 |  |
| env_error | 1 |  |  |  |  |  |
| wrong_fix | 5 | 2 | 3 | 3 |  |  |
| regression | 3 |  |  |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `fastapi_14448` missed 1: `fastapi/dependencies/utils.py`
- `rich_3535` missed 1: `rich/segment.py`
- `rich_3675` missed 1: `rich/diagnose.py`
- `fastapi_14605` missed 6: `fastapi/dependencies/utils.py`, `fastapi/openapi/utils.py`, `fastapi/params.py`, `fastapi/routing.py`, `fastapi/temp_pydantic_v1_params.py` ...
- `fastapi_14953` missed 3: `docs_src/json_base64_bytes/tutorial001_py310.py`, `fastapi/_compat/v2.py`, `scripts/playwright/json_base64_bytes/image01.py`

## Examples

### no_patch_budget  (23)
- `fastapi_13713` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 5.5/8.0 min
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/fastapi_13713.log`
- `rich_3063` (Textualize/rich) [loc: no_edit] -- no patch; spent 10/28 tool calls, 8.0/8.0 min
- `fastapi_12942` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 5.8/8.0 min
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/fastapi_12942.log`
  ...and 20 more

### scratch_only  (2)
- `fastapi_14297` (fastapi/fastapi) [loc: scratch_only] -- patch only adds scratch files (repro scripts); no source file edited
    - patch: `exp_res/results_dev60_e1_locator/patches/fastapi_14297.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/fastapi_14297.log`
- `rich_3934` (Textualize/rich) [loc: scratch_only] -- patch only adds scratch files (repro scripts); no source file edited
    - patch: `exp_res/results_dev60_e1_locator/patches/rich_3934.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/rich_3934.log`

### env_error  (1)
- `fastapi_14953` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'docs_src.json_base64_bytes' (collection error, patch untested) -- and target tests failed/errored too
    - patch: `exp_res/results_dev60_e1_locator/patches/fastapi_14953.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/fastapi_14953.log`

### wrong_fix  (13)
- `fastapi_14246` (fastapi/fastapi) [loc: same_region] -- target test(s) still failing (4 failed): tests/test_no_schema_split.py::test_openapi_schema
    - patch: `exp_res/results_dev60_e1_locator/patches/fastapi_14246.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/fastapi_14246.log`
- `fastapi_5624` (fastapi/fastapi) [loc: same_file] -- target test(s) still failing (1 failed): tests/test_security_scopes_dont_propagate.py::test_security_scopes_dont_propagate
    - patch: `exp_res/results_dev60_e1_locator/patches/fastapi_5624.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/fastapi_5624.log`
- `rich_3468` (Textualize/rich) [loc: same_file] -- target test(s) still failing (1 failed): tests/test_console.py::test_brokenpipeerror
    - patch: `exp_res/results_dev60_e1_locator/patches/rich_3468.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/rich_3468.log`
  ...and 10 more

### regression  (3)
- `requests_6629` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_dev60_e1_locator/patches/requests_6629.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/requests_6629.log`
- `requests_6589` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_dev60_e1_locator/patches/requests_6589.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/requests_6589.log`
- `fastapi_13207` (fastapi/fastapi) [loc: same_lines] -- target passed but other test(s) broke: tests/test_computed_fields.py::test_openapi_schema[True]
    - patch: `exp_res/results_dev60_e1_locator/patches/fastapi_13207.patch`
    - test_log: `exp_res/results_dev60_e1_locator/test_outputs/fastapi_13207.log`
