#!/usr/bin/env python3
"""
triage.py -- turn a swegemma dev-eval run into an actionable failure breakdown.

It reads a results directory produced by the dev-eval notebook loop (see
notebooks/getting-started-gemma-4-developer-agent.ipynb) and classifies every task into one bucket, so you can see
WHERE the score is being lost instead of just the aggregate resolution rate.

No GPU and no third-party deps (stdlib only) -- run it on your laptop after
downloading `results/`, or inside the Kaggle notebook right after the run.

Usage:
    python eval/triage.py --results-dir results/
    python eval/triage.py --results-dir results/ --examples 3 --report results/triage_report.md
    python eval/triage.py --results-dir results/ --tasks tasks.jsonl   # + reference-patch analysis

With --tasks (the public tasks.jsonl, which carries the reference `patch` and
`test_patch`), triage also fills in the target tests (fail_to_pass) so
wrong_fix vs regression can be told apart, and reports whether the agent
edited the same place as the reference fix (see refdiff.py).

Input it expects (written by the dev-eval notebook):
    results/dev_results.jsonl        # one JSON object per task (schema in README.md)
    results/patches/<id>.patch       # optional, agent diff
    results/test_outputs/<id>.log    # optional, phase-2 pytest output

If `dev_results.jsonl` is missing it falls back to `task_results.jsonl`
(the harness CLI format) with best-effort field mapping.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from refdiff import load_tasks, localize, parse_diff, source_edits, target_tests

# ---- Failure buckets -------------------------------------------------------
# Ordered best-to-worst-understood. The whole point of triage is to find which
# of these dominates, because each one points at a different fix:
#   resolved        -> solved, nothing to do
#   apply_fail      -> patch produced but could not be applied in Phase 2
#                      (malformed diff / wrong paths / context drift)
#   no_patch_budget -> gave no patch AND ran out of tool-calls or time
#                      => localization/efficiency problem, raise budgets / speed up explore
#   no_patch_gaveup -> gave no patch but budget remained
#                      => model concluded/stopped early; prompt & agency problem
#   scratch_only    -> "patch" is only new scratch files (repro.py ...), no source edit
#                      => same as no patch; agent never changed library code
#   broken_code     -> patch applied but the edited source no longer parses
#                      (SyntaxError/IndentationError at import) => edit hygiene
#   wrong_fix       -> patch applied, but the TARGET tests still fail
#                      => wrong location or wrong fix (localization / reasoning)
#   regression      -> patch applied, target tests pass, but OTHER tests broke
#                      => edits not minimal enough
#   patch_rejected  -> patch applied, not resolved, can't tell which of the above
RESOLVED = "resolved"
APPLY_FAIL = "apply_fail"
CONTEXT_OVERFLOW = "context_overflow"
NO_PATCH_BUDGET = "no_patch_budget"
NO_PATCH_GAVEUP = "no_patch_gaveup"
SCRATCH_ONLY = "scratch_only"
BROKEN_CODE = "broken_code"
ENV_ERROR = "env_error"
WRONG_FIX = "wrong_fix"
REGRESSION = "regression"
PATCH_REJECTED = "patch_rejected"

FAIL_BUCKETS = [
    APPLY_FAIL,
    CONTEXT_OVERFLOW,
    NO_PATCH_BUDGET,
    NO_PATCH_GAVEUP,
    SCRATCH_ONLY,
    BROKEN_CODE,
    ENV_ERROR,
    WRONG_FIX,
    REGRESSION,
    PATCH_REJECTED,
]

# What each bucket suggests you work on next (printed in the report).
BUCKET_HINT = {
    ENV_ERROR: "NOT the agent's fault -> test env missing deps/fixtures (collection/setup errored). Fix the sandbox or exclude from the dev metric",
    CONTEXT_OVERFLOW: "config -> lower max_output_tokens (e.g. 4096) so input+output < 32768; the LLM call hard-fails otherwise",
    NO_PATCH_BUDGET: "commitment/efficiency -> force an edit earlier; with --tasks check whether the right file was found (trace_stats edit@call)",
    WRONG_FIX: "localization/reasoning -> find the right file/symbol before editing",
    REGRESSION: "minimality -> make smaller, more targeted edits; don't touch unrelated code",
    NO_PATCH_GAVEUP: "agency/prompt -> model stops too early; nudge it to always submit a non-empty patch",
    SCRATCH_ONLY: "agency/prompt -> agent only wrote repro scripts, never edited source; scripts belong in /tmp",
    BROKEN_CODE: "edit hygiene -> an edit broke the file's syntax; py_compile every edited file before submitting",
    APPLY_FAIL: "patch hygiene -> emit clean diffs, avoid huge edits that get truncated",
    PATCH_REJECTED: "inspect logs -> couldn't auto-classify; eyeball the linked pytest log",
}

# pytest "-q" prints lines like:  FAILED path/to/test_x.py::test_name - AssertionError
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)

# a pytest run that errored at collection/setup (missing dep, broken fixture) did NOT
# actually test the agent's patch -- it's an environment problem, not the agent's fault
_ENV_ERROR_RE = re.compile(
    r"ERROR collecting|ModuleNotFoundError|ImportError while importing"
    r"|errors? during collection|recursive dependency involving fixture"
    r"|ERROR at setup of|fixture '[^']+' not found",
    re.IGNORECASE,
)

# the agent's edit left a source file unparseable; pytest then fails at collection,
# which would otherwise look like an env error. Test files come from test_patch
# and are known-good, so a syntax error here is the agent's.
_BROKEN_CODE_RE = re.compile(r"^E\s+(SyntaxError|IndentationError|TabError)\b", re.MULTILINE)

# signatures of a 32768-token context-window overflow (hard LLM failure, not the agent giving up)
_CTX_OVERFLOW_RE = re.compile(
    r"context\s*window|maximum context length|ContextWindowExceeded|32768",
    re.IGNORECASE,
)


@dataclass
class TaskRow:
    id: str
    repo: str = "?"
    resolved: bool = False
    test_exit_code: int | None = None
    patch_chars: int = 0
    tool_calls: int | None = None
    duration_seconds: float | None = None
    max_tool_calls: int | None = None
    max_time_minutes: float | None = None
    error: str = ""
    fail_to_pass: list[str] | None = None
    pass_to_pass: list[str] | None = None
    test_log: str | None = None
    # filled in by attach_reference() when --tasks is given
    loc: str = ""                      # refdiff.Localization.label
    missed_files: list[str] = field(default_factory=list)
    targets_from_ref: bool = False
    # filled in by classify()
    bucket: str = ""
    why: str = ""
    detail_paths: dict[str, str] = field(default_factory=dict)


def _get(d: dict[str, Any], *names, default=None):
    """First present key among `names`."""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def load_rows(results_dir: Path) -> list[TaskRow]:
    dev = results_dir / "dev_results.jsonl"
    cli = results_dir / "task_results.jsonl"
    src = dev if dev.exists() else cli
    if not src.exists():
        raise SystemExit(
            f"No dev_results.jsonl or task_results.jsonl in {results_dir}. "
            "Run the dev-eval notebook (notebooks/getting-started-gemma-4-developer-agent.ipynb) first."
        )

    rows: list[TaskRow] = []
    with src.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            patch_chars = _get(d, "patch_chars", default=None)
            if patch_chars is None:
                patch = _get(d, "prediction", "patch", "agent_patch", default="") or ""
                patch_chars = 0 if patch.strip() in ("", "NO_PATCH") else len(patch)
            rows.append(
                TaskRow(
                    id=_get(d, "id", "instance_id", default="?"),
                    repo=_get(d, "repo", default="?"),
                    resolved=bool(_get(d, "resolved", default=False)),
                    test_exit_code=_get(d, "test_exit_code", "exit_code"),
                    patch_chars=int(patch_chars),
                    tool_calls=_get(d, "tool_calls"),
                    duration_seconds=_get(d, "duration_seconds", "duration"),
                    max_tool_calls=_get(d, "max_tool_calls"),
                    max_time_minutes=_get(d, "max_time_minutes"),
                    error=_get(d, "error", "agent_error", default="") or "",
                    fail_to_pass=_get(d, "fail_to_pass"),
                    pass_to_pass=_get(d, "pass_to_pass"),
                    test_log=_get(d, "test_log"),
                )
            )
    return rows


def _read_failed_nodes(results_dir: Path, row: TaskRow) -> list[str]:
    """Parse FAILED/ERROR test node ids out of the phase-2 pytest log, if present."""
    candidates = []
    if row.test_log:
        candidates.append(results_dir / row.test_log)
    candidates.append(results_dir / "test_outputs" / f"{row.id}.log")
    for p in candidates:
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            return _FAILED_RE.findall(text)
    return []


def _hit_budget(row: TaskRow) -> bool:
    if row.tool_calls is not None and row.max_tool_calls:
        if row.tool_calls >= row.max_tool_calls - 1:
            return True
    if row.duration_seconds is not None and row.max_time_minutes:
        if row.duration_seconds >= row.max_time_minutes * 60 * 0.95:
            return True
    return False


def _node_matches(node: str, targets: list[str]) -> bool:
    """A pytest node id like 'tests/test_x.py::test_name' matches a target that
    may be stored as just 'test_name' or a fuller path. Compare loosely.
    A bare file on either side ('ERROR tests/test_x.py' from a collection
    error, or a file-level target from refdiff) matches anything in that file."""
    node_file = node.split("::", 1)[0]
    base = node.split("::")[-1]
    for t in targets:
        t_file = t.split("::", 1)[0]
        if ("::" not in node or "::" not in t) and node_file.endswith(".py") and node_file == t_file:
            return True
        if "::" not in node or "::" not in t:
            continue
        tb = t.split("::")[-1]
        if base == tb or base in t or tb in node:
            return True
    return False


def _patch_text(results_dir: Path, row: TaskRow) -> str:
    p = results_dir / "patches" / f"{row.id}.patch"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _is_scratch_only(patch: str) -> bool:
    """Every source file the patch touches is newly created (repro.py etc.)."""
    edits = source_edits(parse_diff(patch))
    return bool(edits) and all(e.created for e in edits.values())


def attach_reference(results_dir: Path, rows: list[TaskRow], tasks: dict[str, dict]) -> None:
    """Fill target tests and localization from the public reference data."""
    for row in rows:
        t = tasks.get(row.id)
        if not t:
            continue
        if not row.fail_to_pass:
            row.fail_to_pass = target_tests(t.get("test_patch", ""))
            row.targets_from_ref = True
        loc = localize(t.get("patch", ""), _patch_text(results_dir, row))
        row.loc = loc.label
        row.missed_files = loc.missed_files


def classify(results_dir: Path, row: TaskRow) -> None:
    # record where to look
    patch_p = results_dir / "patches" / f"{row.id}.patch"
    log_p = results_dir / "test_outputs" / f"{row.id}.log"
    if patch_p.exists():
        row.detail_paths["patch"] = str(patch_p)
    if log_p.exists():
        row.detail_paths["test_log"] = str(log_p)

    if row.resolved:
        row.bucket, row.why = RESOLVED, "passed verification"
        return

    err = row.error.lower()
    if "apply" in err and ("patch" in err or "diff" in err):
        row.bucket = APPLY_FAIL
        row.why = "patch could not be applied in Phase 2"
        return

    # context-window overflow: the LLM call hard-fails (max_output_tokens + input > 32768)
    if row.error and _CTX_OVERFLOW_RE.search(row.error):
        row.bucket = CONTEXT_OVERFLOW
        row.why = "LLM call exceeded 32768-token window (lower max_output_tokens)"
        return

    if row.patch_chars > 0 and (row.loc == "scratch_only" or (not row.loc and _is_scratch_only(_patch_text(results_dir, row)))):
        row.bucket = SCRATCH_ONLY
        row.why = "patch only adds scratch files (repro scripts); no source file edited"
        return

    if row.patch_chars == 0:
        if _hit_budget(row):
            row.bucket = NO_PATCH_BUDGET
            row.why = (
                f"no patch; spent {row.tool_calls}/{row.max_tool_calls} tool calls, "
                f"{(row.duration_seconds or 0)/60:.1f}/{row.max_time_minutes} min"
            )
        else:
            row.bucket = NO_PATCH_GAVEUP
            row.why = (
                f"no patch, budget left ({row.tool_calls}/{row.max_tool_calls} calls) "
                "-- stopped/concluded early"
            )
        return

    # Non-empty patch but not resolved. First rule out an environment failure:
    # if pytest errored at collection/setup (missing dep, broken fixture), the
    # patch was never actually tested -- not the agent's fault.
    log_text = ""
    for p in ([results_dir / row.test_log] if row.test_log else []) + [
        results_dir / "test_outputs" / f"{row.id}.log"
    ]:
        if p.exists():
            try:
                log_text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                log_text = ""
            break
    m = _BROKEN_CODE_RE.search(log_text) if log_text else None
    if m:
        row.bucket = BROKEN_CODE
        f = re.search(r'File "[^"]*?/workspace/([^"]+)", line (\d+)', log_text)
        where = f" in {f.group(1)}:{f.group(2)}" if f else ""
        row.why = f"{m.group(1)}{where} -- patched source does not import"
        return

    if log_text and _ENV_ERROR_RE.search(log_text):
        row.bucket = ENV_ERROR
        m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", log_text)
        if m:
            row.why = f"test env missing module '{m.group(1)}' (collection error, patch untested)"
        elif "recursive dependency involving fixture" in log_text:
            row.why = "broken pytest fixture (httpbin/plugin not loaded); patch untested"
        else:
            row.why = "pytest errored at collection/setup; patch untested"
        # The env error may only hit unrelated tests. If we know the targets and
        # none of them failed/errored while some tests passed, the fix itself
        # probably works -- worth knowing when estimating the real score.
        failed = _FAILED_RE.findall(log_text)
        if row.fail_to_pass and re.search(r"\d+ passed", log_text):
            if not any(_node_matches(n, row.fail_to_pass) for n in failed):
                row.why += " -- but target tests did NOT fail (likely resolved once env is fixed)"
            else:
                row.why += " -- and target tests failed/errored too"
        return

    # figure out which tests failed.
    failed = _read_failed_nodes(results_dir, row)
    if failed and row.fail_to_pass:
        target_still_failing = any(
            _node_matches(n, row.fail_to_pass) for n in failed
        )
        if row.pass_to_pass:
            others_failing = any(_node_matches(n, row.pass_to_pass) for n in failed)
        else:
            # targets known but no explicit pass_to_pass list: any non-target
            # failure is a regression (the reference fix passes the whole suite)
            others_failing = any(not _node_matches(n, row.fail_to_pass) for n in failed)
        if target_still_failing:
            row.bucket = WRONG_FIX
            row.why = f"target test(s) still failing ({len(failed)} failed): {failed[0]}"
            return
        if others_failing:
            row.bucket = REGRESSION
            row.why = f"target passed but other test(s) broke: {failed[0]}"
            return

    if failed:
        # We saw failures but couldn't map them to fail/pass-to-pass lists.
        row.bucket = PATCH_REJECTED
        row.why = f"patch applied, {len(failed)} test(s) failed (unmapped): {failed[0]}"
    else:
        row.bucket = PATCH_REJECTED
        row.why = "patch applied but not resolved (no pytest log to inspect)"


def build_report(results_dir: Path, rows: list[TaskRow], examples: int) -> str:
    counts = Counter(r.bucket for r in rows)
    total = len(rows)
    resolved = counts.get(RESOLVED, 0)
    rate = resolved / total if total else 0.0

    lines: list[str] = []
    lines.append("# Dev-eval triage")
    lines.append("")
    lines.append(f"- tasks: **{total}**")
    lines.append(f"- resolved: **{resolved}/{total}**  (resolution rate = **{rate:.3f}**)")
    lines.append("")

    # breakdown table
    lines.append("## Failure breakdown")
    lines.append("")
    lines.append("| bucket | count | % of all | % of failures | work on |")
    lines.append("|---|---:|---:|---:|---|")
    fails = total - resolved
    order = [RESOLVED] + FAIL_BUCKETS
    for b in order:
        c = counts.get(b, 0)
        if c == 0:
            continue
        pct_all = 100 * c / total if total else 0
        pct_fail = (100 * c / fails) if (fails and b != RESOLVED) else 0
        hint = BUCKET_HINT.get(b, "")
        pf = f"{pct_fail:.0f}%" if b != RESOLVED else "-"
        lines.append(f"| {b} | {c} | {pct_all:.0f}% | {pf} | {hint} |")
    lines.append("")

    # by-repo resolution (watch generalization; train repos != private test repos)
    by_repo = defaultdict(lambda: [0, 0])
    for r in rows:
        by_repo[r.repo][1] += 1
        if r.resolved:
            by_repo[r.repo][0] += 1
    if len(by_repo) > 1:
        lines.append("## By repo")
        lines.append("")
        lines.append("| repo | resolved |")
        lines.append("|---|---|")
        for repo in sorted(by_repo):
            res, tot = by_repo[repo]
            lines.append(f"| {repo} | {res}/{tot} |")
        lines.append("")

    # reference comparison (only with --tasks)
    if any(r.loc for r in rows):
        labels = ["same_lines", "same_region", "same_file", "wrong_file", "scratch_only", "no_edit"]
        lines.append("## Localization vs reference fix")
        lines.append("")
        lines.append("Did the agent edit where the reference patch edits? (`same_lines` = within 3 lines, "
                     "`same_region` = within 30 lines; scratch files like repro.py are ignored)")
        lines.append("")
        lines.append("| bucket | " + " | ".join(labels) + " |")
        lines.append("|---|" + "---:|" * len(labels))
        for b in [RESOLVED] + FAIL_BUCKETS:
            sub = [r for r in rows if r.bucket == b and r.loc]
            if not sub:
                continue
            c = Counter(r.loc for r in sub)
            lines.append(f"| {b} | " + " | ".join(str(c.get(l, 0) or "") for l in labels) + " |")
        lines.append("")
        partial = [r for r in rows if not r.resolved and r.loc in ("same_lines", "same_region", "same_file") and r.missed_files]
        if partial:
            lines.append("Right place but reference also edits files the agent never touched (incomplete fix):")
            lines.append("")
            for r in partial:
                lines.append(f"- `{r.id}` missed {len(r.missed_files)}: " + ", ".join(f"`{f}`" for f in r.missed_files[:5])
                             + (" ..." if len(r.missed_files) > 5 else ""))
            lines.append("")
        env_ok = [r for r in rows if r.bucket == ENV_ERROR and "did NOT fail" in r.why]
        if env_ok:
            lines.append(f"env_error tasks whose target tests did not fail: **{len(env_ok)}** "
                         f"({', '.join(r.id for r in env_ok)}). Optimistic resolution rate if these count: "
                         f"**{(resolved + len(env_ok)) / total:.3f}**")
            lines.append("")

    # examples per failing bucket
    lines.append("## Examples")
    lines.append("")
    for b in FAIL_BUCKETS:
        ex = [r for r in rows if r.bucket == b]
        if not ex:
            continue
        lines.append(f"### {b}  ({len(ex)})")
        for r in ex[:examples]:
            loc = f" [loc: {r.loc}]" if r.loc else ""
            lines.append(f"- `{r.id}` ({r.repo}){loc} -- {r.why}")
            for k, p in r.detail_paths.items():
                lines.append(f"    - {k}: `{p}`")
        if len(ex) > examples:
            lines.append(f"  ...and {len(ex) - examples} more")
        lines.append("")

    return "\n".join(lines)


def print_console(rows: list[TaskRow]) -> None:
    counts = Counter(r.bucket for r in rows)
    total = len(rows)
    resolved = counts.get(RESOLVED, 0)
    fails = total - resolved
    print("\n" + "=" * 52)
    print(f"  resolution rate: {resolved}/{total} = {resolved/total if total else 0:.3f}")
    print("=" * 52)
    print(f"  {'bucket':<18}{'count':>6}{'%all':>7}{'%fail':>7}")
    print("  " + "-" * 36)
    for b in [RESOLVED] + FAIL_BUCKETS:
        c = counts.get(b, 0)
        if c == 0:
            continue
        pa = 100 * c / total if total else 0
        pf = (100 * c / fails) if (fails and b != RESOLVED) else 0
        pfs = f"{pf:>6.0f}%" if b != RESOLVED else f"{'-':>7}"
        print(f"  {b:<18}{c:>6}{pa:>6.0f}%{pfs}")
    print("=" * 52 + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Triage a swegemma dev-eval run.")
    ap.add_argument("--results-dir", default="results", type=Path)
    ap.add_argument("--examples", default=3, type=int,
                    help="example tasks to list per failure bucket")
    ap.add_argument("--report", default=None, type=Path,
                    help="write a markdown report here (default: <results-dir>/triage_report.md)")
    ap.add_argument("--json", dest="json_out", default=None, type=Path,
                    help="also write per-task classification as JSON")
    ap.add_argument("--tasks", default=None, type=Path,
                    help="public tasks.jsonl: derive target tests from test_patch and compare to the reference patch")
    args = ap.parse_args()

    rows = load_rows(args.results_dir)
    if args.tasks:
        attach_reference(args.results_dir, rows, load_tasks(args.tasks))
    for r in rows:
        classify(args.results_dir, r)

    print_console(rows)

    report = build_report(args.results_dir, rows, args.examples)
    report_path = args.report or (args.results_dir / "triage_report.md")
    report_path.write_text(report, encoding="utf-8")
    print(f"wrote {report_path}")

    json_path = args.json_out or (args.results_dir / "triage.json")
    payload = [
        {
            "id": r.id, "repo": r.repo, "bucket": r.bucket, "why": r.why,
            "resolved": r.resolved, "patch_chars": r.patch_chars,
            "tool_calls": r.tool_calls, "duration_seconds": r.duration_seconds,
            "loc": r.loc, "missed_files": r.missed_files,
        }
        for r in rows
    ]
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {json_path}")


if __name__ == "__main__":
    main()
