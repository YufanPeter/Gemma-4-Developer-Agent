# Dev-eval triage

- tasks: **60**
- resolved: **22/60**  (resolution rate = **0.367**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 22 | 37% | - |  |
| no_patch_budget | 20 | 33% | 53% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| env_error | 3 | 5% | 8% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 10 | 17% | 26% | localization/reasoning -> find the right file/symbol before editing |
| regression | 5 | 8% | 13% | minimality -> make smaller, more targeted edits; don't touch unrelated code |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 9/22 |
| encode/httpx | 0/1 |
| fastapi/fastapi | 13/31 |
| psf/requests | 0/6 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 21 |  | 1 |  |  |  |
| no_patch_budget |  |  |  |  |  | 20 |
| env_error | 2 |  |  | 1 |  |  |
| wrong_fix | 6 | 1 |  | 3 |  |  |
| regression | 5 |  |  |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `httpx_3672` missed 4: `src/ahttpx/_parsers.py`, `src/ahttpx/_pool.py`, `src/ahttpx/_server.py`, `src/httpx/_pool.py`
- `rich_3535` missed 1: `rich/segment.py`
- `rich_3675` missed 1: `rich/diagnose.py`
- `fastapi_14953` missed 3: `docs_src/json_base64_bytes/tutorial001_py310.py`, `fastapi/_compat/v2.py`, `scripts/playwright/json_base64_bytes/image01.py`

## Examples

### no_patch_budget  (20)
- `fastapi_13713` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 6.5/8.0 min
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/fastapi_13713.log`
- `fastapi_11194` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 6.6/8.0 min
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/fastapi_11194.log`
- `fastapi_14246` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 8.1/8.0 min
  ...and 17 more

### env_error  (3)
- `httpx_3672` (encode/httpx) [loc: same_lines] -- pytest errored at collection/setup; patch untested
    - patch: `exp_res/results_codenav_1026_60dev/patches/httpx_3672.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/httpx_3672.log`
- `fastapi_14953` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'docs_src.json_base64_bytes' (collection error, patch untested) -- and target tests failed/errored too
    - patch: `exp_res/results_codenav_1026_60dev/patches/fastapi_14953.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/fastapi_14953.log`
- `fastapi_14978` (fastapi/fastapi) [loc: wrong_file] -- pytest errored at collection/setup; patch untested
    - patch: `exp_res/results_codenav_1026_60dev/patches/fastapi_14978.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/fastapi_14978.log`

### wrong_fix  (10)
- `rich_3468` (Textualize/rich) [loc: same_region] -- target test(s) still failing (1 failed): tests/test_console.py::test_brokenpipeerror
    - patch: `exp_res/results_codenav_1026_60dev/patches/rich_3468.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/rich_3468.log`
- `fastapi_14455` (fastapi/fastapi) [loc: same_lines] -- target test(s) still failing (2 failed): tests/test_security_oauth2_authorization_code_bearer_scopes_openapi.py::test_openapi_schema
    - patch: `exp_res/results_codenav_1026_60dev/patches/fastapi_14455.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/fastapi_14455.log`
- `rich_3535` (Textualize/rich) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_cells.py::test_is_single_cell_widths
    - patch: `exp_res/results_codenav_1026_60dev/patches/rich_3535.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/rich_3535.log`
  ...and 7 more

### regression  (5)
- `requests_6629` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_codenav_1026_60dev/patches/requests_6629.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/requests_6629.log`
- `requests_6589` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_codenav_1026_60dev/patches/requests_6589.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/requests_6589.log`
- `requests_7205` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_utils.py::TestGetNetrcAuth::test_empty_default_credentials_ignored
    - patch: `exp_res/results_codenav_1026_60dev/patches/requests_7205.patch`
    - test_log: `exp_res/results_codenav_1026_60dev/test_outputs/requests_7205.log`
  ...and 2 more
