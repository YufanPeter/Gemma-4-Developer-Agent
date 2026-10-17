# Dev-eval triage

- tasks: **60**
- resolved: **12/60**  (resolution rate = **0.200**)

## Failure breakdown

| bucket | count | % of all | % of failures | work on |
|---|---:|---:|---:|---|
| resolved | 12 | 20% | - |  |
| no_patch_budget | 13 | 22% | 27% | commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call) |
| scratch_only | 1 | 2% | 2% | agency/prompt -> agent only wrote repro scripts, never edited source; scripts belong in /tmp |
| broken_code | 1 | 2% | 2% | edit hygiene -> an edit broke the file's syntax; py_compile every edited file before submitting |
| env_error | 18 | 30% | 38% | NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric |
| wrong_fix | 13 | 22% | 27% | localization/reasoning -> find the right file/symbol before editing |
| regression | 1 | 2% | 2% | minimality -> make smaller, more targeted edits; don't touch unrelated code |
| patch_rejected | 1 | 2% | 2% | inspect logs -> couldn't auto-classify; eyeball the linked pytest log |

## By repo

| repo | resolved |
|---|---|
| Textualize/rich | 8/22 |
| encode/httpx | 0/1 |
| fastapi/fastapi | 4/31 |
| psf/requests | 0/6 |

## Localization vs reference fix

Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, `same_region` = within 30 lines; scratch files like repro.py are ignored)

| bucket | same_lines | same_region | same_file | wrong_file | scratch_only | no_edit |
|---|---:|---:|---:|---:|---:|---:|
| resolved | 12 |  |  |  |  |  |
| no_patch_budget |  |  |  |  |  | 13 |
| scratch_only |  |  |  |  | 1 |  |
| broken_code |  |  | 1 |  |  |  |
| env_error | 16 | 1 |  | 1 |  |  |
| wrong_fix | 6 | 5 | 1 | 1 |  |  |
| regression | 1 |  |  |  |  |  |
| patch_rejected | 1 |  |  |  |  |  |

Right place but reference also edits files the agent never touched (incomplete fix):

- `httpx_3672` missed 5: `src/ahttpx/_parsers.py`, `src/ahttpx/_pool.py`, `src/ahttpx/_server.py`, `src/httpx/_network.py`, `src/httpx/_pool.py`
- `fastapi_14448` missed 1: `fastapi/dependencies/utils.py`
- `rich_3535` missed 1: `rich/segment.py`
- `rich_3675` missed 1: `rich/diagnose.py`
- `fastapi_14371` missed 3: `fastapi/_compat/v2.py`, `fastapi/dependencies/utils.py`, `fastapi/openapi/utils.py`
- `fastapi_14605` missed 2: `fastapi/openapi/utils.py`, `fastapi/utils.py`
- `rich_3934` missed 1: `rich/live.py`
- `rich_3938` missed 1: `rich/segment.py`
- `fastapi_14953` missed 2: `docs_src/json_base64_bytes/tutorial001_py310.py`, `scripts/playwright/json_base64_bytes/image01.py`
- `fastapi_14978` missed 2: `docs_src/strict_content_type/tutorial001_py310.py`, `fastapi/applications.py`
- `fastapi_15280` missed 1: `docs_src/vibe/tutorial001_py310.py`
- `fastapi_15745` missed 3: `docs_src/path_operation_advanced_configuration/tutorial002_py310.py`, `fastapi/applications.py`, `fastapi/openapi/utils.py`

env_error tasks whose target tests did not fail: **1** (requests_6629). Optimistic resolution rate if these count: **0.217**

## Examples

### no_patch_budget  (13)
- `fastapi_13713` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 3.0/8.0 min
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/fastapi_13713.log`
- `fastapi_14262` (fastapi/fastapi) [loc: no_edit] -- no patch; spent 28/28 tool calls, 3.2/8.0 min
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/fastapi_14262.log`
- `rich_3105` (Textualize/rich) [loc: no_edit] -- no patch; spent 28/28 tool calls, 7.1/8.0 min
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/rich_3105.log`
  ...and 10 more

### scratch_only  (1)
- `fastapi_12942` (fastapi/fastapi) [loc: scratch_only] -- patch only adds scratch files (repro scripts); no source file edited
    - patch: `exp_res/results_baseline_1017_60dev/patches/fastapi_12942.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/fastapi_12942.log`

### broken_code  (1)
- `fastapi_15280` (fastapi/fastapi) [loc: same_file] -- SyntaxError in fastapi/applications.py:1567 -- patched source does not import
    - patch: `exp_res/results_baseline_1017_60dev/patches/fastapi_15280.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/fastapi_15280.log`

### env_error  (18)
- `requests_6629` (psf/requests) [loc: same_lines] -- broken pytest fixture (httpbin/plugin not loaded); patch untested -- but target tests did NOT fail (likely resolved once env is fixed)
    - patch: `exp_res/results_baseline_1017_60dev/patches/requests_6629.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/requests_6629.log`
- `httpx_3672` (encode/httpx) [loc: same_lines] -- pytest errored at collection/setup; patch untested
    - patch: `exp_res/results_baseline_1017_60dev/patches/httpx_3672.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/httpx_3672.log`
- `fastapi_14246` (fastapi/fastapi) [loc: same_region] -- test env missing module 'inline_snapshot' (collection error, patch untested)
    - patch: `exp_res/results_baseline_1017_60dev/patches/fastapi_14246.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/fastapi_14246.log`
  ...and 15 more

### wrong_fix  (13)
- `rich_3063` (Textualize/rich) [loc: same_region] -- target test(s) still failing (3 failed): tests/test_markup.py::test_escape
    - patch: `exp_res/results_baseline_1017_60dev/patches/rich_3063.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/rich_3063.log`
- `rich_2725` (Textualize/rich) [loc: same_file] -- target test(s) still failing (3 failed): tests/test_table.py::test_placement_table_box_elements[False-False-abbbbbcbbbbbbbbbcbbbbcbbbbbd\n1Dec
    - patch: `exp_res/results_baseline_1017_60dev/patches/rich_2725.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/rich_2725.log`
- `rich_3468` (Textualize/rich) [loc: same_region] -- target test(s) still failing (1 failed): tests/test_console.py::test_brokenpipeerror
    - patch: `exp_res/results_baseline_1017_60dev/patches/rich_3468.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/rich_3468.log`
  ...and 10 more

### regression  (1)
- `requests_7205` (psf/requests) [loc: same_lines] -- target passed but other test(s) broke: tests/test_utils.py::TestGetNetrcAuth::test_empty_default_credentials_ignored
    - patch: `exp_res/results_baseline_1017_60dev/patches/requests_7205.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/requests_7205.log`

### patch_rejected  (1)
- `rich_3480` (Textualize/rich) [loc: same_lines] -- patch applied but not resolved (no pytest log to inspect)
    - patch: `exp_res/results_baseline_1017_60dev/patches/rich_3480.patch`
    - test_log: `exp_res/results_baseline_1017_60dev/test_outputs/rich_3480.log`
