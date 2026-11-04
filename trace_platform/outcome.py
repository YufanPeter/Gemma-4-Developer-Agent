"""One outcome label per task, shared by the store, the CLI and the page.

The platform does not judge patches itself: `resolved` is the harness's Phase 2 verdict
(the patch applied and FAIL_TO_PASS + PASS_TO_PASS all passed), copied into the
dev_results.jsonl row by the dev-eval notebook. Everything else only explains *why* a
task did not pass, from the row's `error` / `patch_chars` and the pytest log. The rules
read only fields every branch's notebook writes (the row schema comes from the shared
notebook template), so runs from any branch label the same way.

Order matters; the first rule that matches wins:
    RUNNING    still in progress (live runs)
    UNKNOWN    no result row
    PASS       resolved
    TIMEOUT    error says the session wall clock ran out ("Agent exceeded session timeout")
    BUDGET     error says a tool-call / turn budget ran out ("exceeded tool call budget")
    ERROR      any other error string (harness crash, LLM failure, notebook exception)
    NO_PATCH   finished without a patch
    ENV_ERROR  pytest errored at collection/setup (missing dep, broken fixture): the patch
               was never really tested -- not the agent's fault (eval/triage.py's rule)
    FAIL       patch tested and did not pass
"""
from __future__ import annotations

import re

# Bump when the rules change: the store relabels every stored task on next open.
VERSION = 1

ORDER = ["PASS", "TIMEOUT", "BUDGET", "NO_PATCH", "ENV_ERROR", "FAIL", "ERROR", "RUNNING", "UNKNOWN"]

_TIMEOUT_RE = re.compile(r"timeout|timed out", re.I)
_BUDGET_RE = re.compile(r"exceeded .*(budget|limit)", re.I)
# Same as eval/triage.py (_ENV_ERROR_RE / _BROKEN_CODE_RE), kept here so the platform has
# no import-time dependency on eval/ (which differs between branches).
_ENV_ERROR_RE = re.compile(
    r"ERROR collecting|ModuleNotFoundError|ImportError while importing"
    r"|errors? during collection|recursive dependency involving fixture"
    r"|ERROR at setup of|fixture '[^']+' not found",
    re.I,
)
_BROKEN_CODE_RE = re.compile(r"^E\s+(SyntaxError|IndentationError|TabError)\b", re.M)


def outcome(status: str | None, result: dict | None, patch: str | None = None, test_log: str | None = None) -> str:
    if status == "running":
        return "RUNNING"
    r = result or {}
    if not r:
        return "UNKNOWN"
    if r.get("resolved"):
        return "PASS"
    err = str(r.get("error") or "")
    if _TIMEOUT_RE.search(err):
        return "TIMEOUT"
    if _BUDGET_RE.search(err):
        return "BUDGET"
    if err.strip():
        return "ERROR"
    has_patch = (r.get("patch_chars") or 0) > 0 if "patch_chars" in r else bool((patch or "").strip())
    if not has_patch:
        return "NO_PATCH"
    log = test_log or ""
    if log and _ENV_ERROR_RE.search(log) and not _BROKEN_CODE_RE.search(log):
        return "ENV_ERROR"
    return "FAIL"
