# Dev-eval triage

- tasks: **60**
- resolved: **22/60**  (resolution rate = **0.367**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 22 | 37% | - |  |
| no_patch_budget | 14 | 23% | 37% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| env_error | 4 | 7% | 11% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 14 | 23% | 37% | localization/reasoning -> find the right file/symbol before editing |
| regression | 5 | 8% | 13% | minimality -> make smaller, more targeted edits; don't touch unrelated code |
| patch_rejected | 1 | 2% | 3% | inspect logs -> couldn't auto-classify; eyeball the linked pytest log |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 10/22 |
| encode/httpx | 0/1 |
| fastapi/fastapi | 12/31 |
| psf/requests | 0/6 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 21 |  | 1 |  |  |  |
| no_patch_budget |  |  |  |  |  | 14 |
| env_error | 4 |  |  |  |  |  |
| wrong_fix | 8 | 2 | 1 | 3 |  |  |
| regression | 5 |  |  |  |  |  |
| patch_rejected | 1 |  |  |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `httpx_3672` missed 4: `src/ahttpx/_parsers.py`, `src/ahttpx/_pool.py`, `src/ahttpx/_server.py`, `src/httpx/_pool.py`
- `fastapi_14448` missed 1: `fastapi/dependencies/utils.py`
- `rich_3535` missed 1: `rich/segment.py`
- `rich_3675` missed 1: `rich/diagnose.py`
- `fastapi_14371` missed 3: `fastapi/_compat/v2.py`, `fastapi/openapi/utils.py`, `fastapi/params.py`
- `fastapi_14605` missed 1: `fastapi/utils.py`
- `fastapi_14953` missed 3: `docs_src/json_base64_bytes/tutorial001_py310.py`, `fastapi/_compat/v2.py`, `scripts/playwright/json_base64_bytes/image01.py`
- `fastapi_14978` missed 2: `docs_src/strict_content_type/tutorial001_py310.py`, `fastapi/applications.py`

## Examples

### no_patch_budget  (14)
- `rich_3063` (Textualize/rich) [loc: no_edit] -- no patch; spent 27/28 tool calls, 8.0/8.0 min
- `fastapi_12942` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 4.8/8.0 min
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/fastapi_12942.log`
- `fastapi_14262` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 4.8/8.0 min
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/fastapi_14262.log`
  ...and 11 more

### env_error  (4)
- `httpx_3672` (encode/httpx) [loc: same_lines] -- pytest errored at collection/setup; patch untested
    - patch: `exp_res/results_prompt_1019_60dev/patches/httpx_3672.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/httpx_3672.log`
- `fastapi_14482` (fastapi/fastapi) [loc: same_lines] -- pytest errored at collection/setup; patch untested -- and target tests failed/errored too
    - patch: `exp_res/results_prompt_1019_60dev/patches/fastapi_14482.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/fastapi_14482.log`
- `fastapi_14953` (fastapi/fastapi) [loc: same_lines] -- test env missing module 'docs_src.json_base64_bytes' (collection error, patch untested) -- and target tests failed/errored too
    - patch: `exp_res/results_prompt_1019_60dev/patches/fastapi_14953.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/fastapi_14953.log`
  ...and 1 more

### wrong_fix  (14)
- `fastapi_13713` (fastapi/fastapi) [loc: same_lines] -- target test(s) still failing (1 failed): tests/test_application.py::test_openapi_schema
    - patch: `exp_res/results_prompt_1019_60dev/patches/fastapi_13713.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/fastapi_13713.log`
- `fastapi_14246` (fastapi/fastapi) [loc: same_region] -- target test(s) still failing (4 failed): tests/test_no_schema_split.py::test_openapi_schema
    - patch: `exp_res/results_prompt_1019_60dev/patches/fastapi_14246.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/fastapi_14246.log`
- `rich_3105` (Textualize/rich) [loc: wrong_file] -- target test(s) still failing (3 failed): tests/test_console.py::test_export_html
    - patch: `exp_res/results_prompt_1019_60dev/patches/rich_3105.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/rich_3105.log`
  ...and 11 more

### regression  (5)
- `requests_6629` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_prompt_1019_60dev/patches/requests_6629.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/requests_6629.log`
- `requests_6589` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_requests.py::TestPreparingURLs::test_redirecting_to_bad_url[http://localhost:-1-InvalidURL]
    - patch: `exp_res/results_prompt_1019_60dev/patches/requests_6589.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/requests_6589.log`
- `requests_7205` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_utils.py::TestGetNetrcAuth::test_empty_default_credentials_ignored
    - patch: `exp_res/results_prompt_1019_60dev/patches/requests_7205.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/requests_7205.log`
  ...and 2 more

### patch_rejected  (1)
- `rich_4006` (Textualize/rich) [loc: same_lines] -- patch applied but not resolved (no pytest log to inspect)
    - patch: `exp_res/results_prompt_1019_60dev/patches/rich_4006.patch`
    - test_log: `exp_res/results_prompt_1019_60dev/test_outputs/rich_4006.log`
