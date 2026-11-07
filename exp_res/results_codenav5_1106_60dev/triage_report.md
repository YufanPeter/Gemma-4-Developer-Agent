# Dev-eval triage

- tasks: **60**
- resolved: **21/60**  (resolution rate = **0.350**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 21 | 35% | - |  |
| no_patch_budget | 10 | 17% | 26% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| broken_code | 1 | 2% | 3% | edit hygiene -> an edit broke the file's syntax; py_compile every edited file before submitting |
| env_error | 4 | 7% | 10% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 20 | 33% | 51% | localization/reasoning -> find the right file/symbol before editing |
| regression | 4 | 7% | 10% | minimality -> make smaller, more targeted edits; don't touch unrelated code |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 9/22 |
| encode/httpx | 0/1 |
| fastapi/fastapi | 12/31 |
| psf/requests | 0/6 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 19 |  | 2 |  |  |  |
| no_patch_budget |  |  |  |  |  | 10 |
| broken_code | 1 |  |  |  |  |  |
| env_error | 3 |  |  | 1 |  |  |
| wrong_fix | 10 | 3 | 2 | 5 |  |  |
| regression | 4 |  |  |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `httpx_3672` missed 4: `src/ahttpx/_parsers.py`, `src/ahttpx/_pool.py`, `src/ahttpx/_server.py`, `src/httpx/_pool.py`
- `fastapi_14262` missed 6: `docs_src/dependencies/tutorial008e.py`, `docs_src/dependencies/tutorial008e_an.py`, `docs_src/dependencies/tutorial008e_an_py39.py`, `fastapi/exceptions.py`, `fastapi/routing.py` ...
- `fastapi_14448` missed 1: `fastapi/dependencies/utils.py`
- `rich_3535` missed 1: `rich/segment.py`
- `rich_3675` missed 1: `rich/diagnose.py`
- `fastapi_14605` missed 6: `fastapi/dependencies/utils.py`, `fastapi/openapi/utils.py`, `fastapi/params.py`, `fastapi/routing.py`, `fastapi/temp_pydantic_v1_params.py` ...
- `rich_3934` missed 1: `rich/live.py`
- `fastapi_14953` missed 3: `docs_src/json_base64_bytes/tutorial001_py310.py`, `fastapi/_compat/v2.py`, `scripts/playwright/json_base64_bytes/image01.py`
- `fastapi_14978` missed 2: `docs_src/strict_content_type/tutorial001_py310.py`, `fastapi/applications.py`
- `fastapi_15280` missed 1: `docs_src/vibe/tutorial001_py310.py`

## Examples

### no_patch_budget  (10)
- `rich_3105` (Textualize/rich) [loc: no_edit] -- no patch; spent 28/28 tool calls, 8.1/8.0 min
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/rich_3105.log`
- `rich_3470` (Textualize/rich) [loc: no_edit] -- no patch; spent 28/28 tool calls, 5.7/8.0 min
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/rich_3470.log`
- `requests_7328` (psf/requests) [loc: no_edit] -- no patch; spent 25/28 tool calls, 8.0/8.0 min
  ...and 7 more

### broken_code  (1)
- `fastapi_14262` (fastapi/fastapi) [loc: same_lines] -- SyntaxError in fastapi/dependencies/utils.py:646 -- patched source does not import
    - patch: `exp_res/results_codenav5_1106_60dev/patches/fastapi_14262.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/fastapi_14262.log`

### env_error  (4)
- `httpx_3672` (encode/httpx) [loc: same_lines] -- pytest errored at collection/setup; patch untested
    - patch: `exp_res/results_codenav5_1106_60dev/patches/httpx_3672.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/httpx_3672.log`
- `fastapi_14953` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'docs_src.json_base64_bytes' (collection error, patch untested) -- and target tests failed/errored too
    - patch: `exp_res/results_codenav5_1106_60dev/patches/fastapi_14953.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/fastapi_14953.log`
- `fastapi_14978` (fastapi/fastapi) [loc: same_lines] -- pytest errored at collection/setup; patch untested
    - patch: `exp_res/results_codenav5_1106_60dev/patches/fastapi_14978.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/fastapi_14978.log`
  ...and 1 more

### wrong_fix  (20)
- `fastapi_13713` (fastapi/fastapi) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_application.py::test_openapi_schema
    - patch: `exp_res/results_codenav5_1106_60dev/patches/fastapi_13713.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/fastapi_13713.log`
- `fastapi_14246` (fastapi/fastapi) [loc: same_region] -- target test(s) still failing (4 failed): tests/test_no_schema_split.py::test_openapi_schema
    - patch: `exp_res/results_codenav5_1106_60dev/patches/fastapi_14246.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/fastapi_14246.log`
- `rich_3278` (Textualize/rich) [loc: same_lines] -- target test(s) still failing (4 failed): tests/test_ansi.py::test_decode_issue_2688[\x1b[31mFound
    - patch: `exp_res/results_codenav5_1106_60dev/patches/rich_3278.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/rich_3278.log`
  ...and 17 more

### regression  (4)
- `requests_6629` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_codenav5_1106_60dev/patches/requests_6629.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/requests_6629.log`
- `requests_6589` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_codenav5_1106_60dev/patches/requests_6589.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/requests_6589.log`
- `requests_7205` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_utils.py::TestGetNetrcAuth::test_empty_default_credentials_ignored
    - patch: `exp_res/results_codenav5_1106_60dev/patches/requests_7205.patch`
    - test_log: `exp_res/results_codenav5_1106_60dev/test_outputs/requests_7205.log`
  ...and 1 more
