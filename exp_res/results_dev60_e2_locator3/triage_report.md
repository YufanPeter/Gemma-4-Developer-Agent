# Dev-eval triage

- tasks: **46**
- resolved: **20/46**  (resolution rate = **0.435**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 20 | 43% | - |  |
| no_patch_budget | 11 | 24% | 42% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| scratch_only | 1 | 2% | 4% | agency/prompt -> agent only wrote repro scripts, never edited source; scripts belong in /tmp |
| env_error | 1 | 2% | 4% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 11 | 24% | 42% | localization/reasoning -> find the right file/symbol before editing |
| regression | 2 | 4% | 8% | minimality -> make smaller, more targeted edits; don't touch unrelated code |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 7/17 |
| encode/httpx | 1/1 |
| fastapi/fastapi | 10/23 |
| psf/requests | 2/5 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 19 |  |  | 1 |  |  |
| no_patch_budget |  |  |  |  |  | 11 |
| scratch_only |  |  |  |  | 1 |  |
| env_error | 1 |  |  |  |  |  |
| wrong_fix | 7 | 1 | 2 | 1 |  |  |
| regression | 2 |  |  |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `fastapi_14262` missed 7: `docs_src/dependencies/tutorial008e.py`, `docs_src/dependencies/tutorial008e_an.py`, `docs_src/dependencies/tutorial008e_an_py39.py`, `fastapi/exceptions.py`, `fastapi/param_functions.py` ...
- `rich_3535` missed 1: `rich/segment.py`
- `fastapi_14371` missed 3: `fastapi/_compat/v2.py`, `fastapi/dependencies/utils.py`, `fastapi/openapi/utils.py`
- `fastapi_14605` missed 6: `fastapi/dependencies/utils.py`, `fastapi/openapi/utils.py`, `fastapi/params.py`, `fastapi/routing.py`, `fastapi/temp_pydantic_v1_params.py` ...

## Examples

### no_patch_budget  (11)
- `rich_3063` (Textualize/rich) [loc: no_edit] -- no patch; spent 8/28 tool calls, 8.1/8.0 min
- `fastapi_14246` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 7.2/8.0 min
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/fastapi_14246.log`
- `rich_3105` (Textualize/rich) [loc: no_edit] -- no patch; spent 28/28 tool calls, 6.7/8.0 min
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/rich_3105.log`
  ...and 8 more

### scratch_only  (1)
- `fastapi_12942` (fastapi/fastapi) [loc: scratch_only] -- patch only adds scratch files (repro scripts); no source file edited
    - patch: `exp_res/results_dev60_e2_locator3/patches/fastapi_12942.patch`
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/fastapi_12942.log`

### env_error  (1)
- `fastapi_14262` (fastapi/fastapi) [loc: same_lines] -- pytest errored at collection/setup; patch untested
    - patch: `exp_res/results_dev60_e2_locator3/patches/fastapi_14262.patch`
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/fastapi_14262.log`

### wrong_fix  (11)
- `fastapi_13713` (fastapi/fastapi) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_application.py::test_openapi_schema
    - patch: `exp_res/results_dev60_e2_locator3/patches/fastapi_13713.patch`
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/fastapi_13713.log`
- `rich_3468` (Textualize/rich) [loc: same_region] -- target test(s) still failing (1 failed): tests/test_console.py::test_brokenpipeerror
    - patch: `exp_res/results_dev60_e2_locator3/patches/rich_3468.patch`
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/rich_3468.log`
- `rich_3470` (Textualize/rich) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_console.py::test_capture_and_record
    - patch: `exp_res/results_dev60_e2_locator3/patches/rich_3470.patch`
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/rich_3470.log`
  ...and 8 more

### regression  (2)
- `requests_6629` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_dev60_e2_locator3/patches/requests_6629.patch`
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/requests_6629.log`
- `requests_6589` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_dev60_e2_locator3/patches/requests_6589.patch`
    - test_log: `exp_res/results_dev60_e2_locator3/test_outputs/requests_6589.log`
