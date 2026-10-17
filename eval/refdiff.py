"""
refdiff.py -- compare an agent patch against the public set's reference patch.

The public tasks.jsonl ships the reference fix (`patch`) and the hidden tests
(`test_patch`). That lets us answer, offline and per task:

  * localization: did the agent edit the same file / the same region as the
    reference fix?  (a wrong_fix in the right place is a reasoning problem; in
    the wrong place it is a localization problem -- different fixes)
  * target tests: which test functions `test_patch` adds or changes, i.e. the
    fail_to_pass set triage needs to separate wrong_fix from regression.

Stdlib only. Used by triage.py (`--tasks`), also runnable on its own:

    python eval/refdiff.py --tasks tasks.jsonl --results-dir exp_res/results_baseline_1016
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_DEF_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\(")
_CLASS_RE = re.compile(r"^(\s*)class\s+(\w+)\b")

# two edits within this many lines of each other count as "same region"
NEAR_LINES = 30

# same rule the harness uses to strip test files from agent patches
PROTECTED_TEST_RE = re.compile(r"(^|/)(tests?|testing)/.*\.py$|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")


def _strip_prefix(p: str) -> str:
    p = p.strip().split("\t")[0]
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


@dataclass
class Hunk:
    path: str
    old_start: int
    header: str          # git funcname context, raw (keeps indentation)
    lines: list[str]     # body lines, each starting with ' ', '+' or '-'
    created: bool = False  # file did not exist before (--- /dev/null)


def iter_hunks(diff: str) -> Iterator[Hunk]:
    """Yield hunks of a unified diff. Hunk bodies are consumed by the line
    counts in the @@ header, so plain multi-file diffs (no `diff --git`
    separators, like the reference patches) split correctly."""
    lines = (diff or "").splitlines()
    i, n = 0, len(lines)
    old_path = new_path = None
    while i < n:
        line = lines[i]
        if line.startswith("--- "):
            old_path = _strip_prefix(line[4:])
            i += 1
            continue
        if line.startswith("+++ "):
            new_path = _strip_prefix(line[4:])
            i += 1
            continue
        m = _HUNK_RE.match(line)
        if not m:
            i += 1
            continue
        path = old_path if new_path in (None, "/dev/null") else new_path
        old_left = int(m.group(2)) if m.group(2) is not None else 1
        new_left = int(m.group(4)) if m.group(4) is not None else 1
        body: list[str] = []
        i += 1
        while i < n and (old_left > 0 or new_left > 0):
            b = lines[i]
            if b.startswith("\\"):           # "\ No newline at end of file"
                i += 1
                continue
            tag = b[:1] or " "               # some tools drop the space on blank context lines
            if tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            elif tag == " ":
                old_left -= 1
                new_left -= 1
            else:
                break
            body.append(b if b else " ")
            i += 1
        yield Hunk(path=path or "?", old_start=int(m.group(1)), header=m.group(5), lines=body,
                   created=old_path == "/dev/null")


@dataclass
class FileEdit:
    path: str
    # line numbers in the ORIGINAL file that were removed, or next to which
    # lines were inserted
    old_lines: set[int] = field(default_factory=set)
    created: bool = False


def parse_diff(diff: str) -> dict[str, FileEdit]:
    files: dict[str, FileEdit] = {}
    for h in iter_hunks(diff):
        fe = files.setdefault(h.path, FileEdit(h.path))
        fe.created = fe.created or h.created
        old_no = h.old_start
        for b in h.lines:
            if b[0] == "-":
                fe.old_lines.add(old_no)
                old_no += 1
            elif b[0] == "+":
                fe.old_lines.update((max(1, old_no - 1), old_no))
            else:
                old_no += 1
    return files


def source_edits(edits: dict[str, FileEdit]) -> dict[str, FileEdit]:
    """Drop test/conftest files -- the harness strips them from agent patches anyway."""
    return {p: e for p, e in edits.items() if not PROTECTED_TEST_RE.search(p)}


@dataclass
class Localization:
    ref_files: list[str]
    agent_files: list[str]
    file_hit: bool          # agent touched >=1 reference source file
    region_hit: bool        # some agent edit within NEAR_LINES of a reference edit
    overlap_hit: bool       # some agent edit within 3 lines of a reference edit
    missed_files: list[str]  # reference files the agent never touched
    extra_files: list[str]   # files the agent touched that the reference did not
    scratch_files: list[str]  # files the agent created that the reference did not (repro.py ...)

    @property
    def real_files(self) -> list[str]:
        """Agent-edited source files, minus scratch scripts."""
        return [f for f in self.agent_files if f not in self.scratch_files]

    @property
    def label(self) -> str:
        if not self.agent_files:
            return "no_edit"
        if not self.real_files:
            return "scratch_only"
        if self.overlap_hit:
            return "same_lines"
        if self.region_hit:
            return "same_region"
        if self.file_hit:
            return "same_file"
        return "wrong_file"


def _min_dist(a: set[int], b: set[int]) -> int:
    if not a or not b:
        return 10**9
    sb = sorted(b)
    best = 10**9
    for x in a:
        i = bisect.bisect_left(sb, x)
        for j in (i - 1, i):
            if 0 <= j < len(sb):
                best = min(best, abs(sb[j] - x))
    return best


def localize(ref_patch: str, agent_patch: str) -> Localization:
    ref = source_edits(parse_diff(ref_patch))
    agt = source_edits(parse_diff(agent_patch))
    common = set(ref) & set(agt)
    best = min((_min_dist(ref[p].old_lines, agt[p].old_lines) for p in common), default=10**9)
    return Localization(
        ref_files=sorted(ref),
        agent_files=sorted(agt),
        file_hit=bool(common),
        region_hit=best <= NEAR_LINES,
        overlap_hit=best <= 3,
        missed_files=sorted(set(ref) - set(agt)),
        extra_files=sorted(set(agt) - set(ref)),
        scratch_files=sorted(p for p, e in agt.items() if e.created and p not in ref),
    )


def target_tests(test_patch: str) -> list[str]:
    """Test node ids (`path::[Class::]name`) that test_patch adds or modifies.

    A test counts as targeted if a +/- line falls inside its body, or its def
    line is added (a new test). A def line that is merely rewritten (e.g. a
    bulk `-> None` annotation pass) does not count by itself. The hunk header
    (git funcname context) tells us which function a hunk starts inside of.

    Changes that cannot be pinned to a test function (module-level fixtures,
    apps, parametrize lists) fall back to the bare file path, meaning "every
    test in this file is a target".
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(node: str) -> None:
        if node not in seen:
            seen.add(node)
            out.append(node)

    unpinned: list[str] = []
    for h in iter_hunks(test_patch):
        func: str | None = None
        func_indent = -1
        cls: str | None = None
        cls_indent = -1

        def scope(text: str) -> None:
            """Update func/cls from a def/class/top-level line."""
            nonlocal func, func_indent, cls, cls_indent
            cm = _CLASS_RE.match(text)
            if cm:
                cls, cls_indent, func, func_indent = cm.group(2), len(cm.group(1)), None, -1
                return
            dm = _DEF_RE.match(text)
            if dm:
                indent = len(dm.group(1))
                if func and func.startswith("test") and indent > func_indent:
                    return  # helper nested inside a test: still in that test
                if indent <= cls_indent:
                    cls, cls_indent = None, -1
                func, func_indent = dm.group(2), indent
                return
            stripped = text.lstrip()
            if stripped and text == stripped and not stripped.startswith(("@", "#", ")", "]", "}")):
                func, func_indent, cls, cls_indent = None, -1, None, -1

        def hit() -> None:
            if func and func.startswith("test"):
                add(f"{h.path}::{cls}::{func}" if cls else f"{h.path}::{func}")
            elif h.path not in unpinned:
                unpinned.append(h.path)

        removed_defs = {m.group(2) for b in h.lines if b[0] == "-" and (m := _DEF_RE.match(b[1:]))}
        if h.header:
            scope(h.header)
        for b in h.lines:
            tag, text = b[0], b[1:]
            if not text.strip():
                continue
            scope(text)
            dm = _DEF_RE.match(text)
            if dm:
                # a def line only marks the test if it is genuinely new
                if tag == "+" and dm.group(2) not in removed_defs:
                    hit()
            elif tag in "+-":
                hit()

    # file-level fallback only for test files where no specific test was found
    pinned_files = {n.split("::", 1)[0] for n in out}
    for p in unpinned:
        if p not in pinned_files and PROTECTED_TEST_RE.search(p) and not p.endswith("conftest.py"):
            add(p)
    return out


def load_tasks(path: Path) -> dict[str, dict]:
    tasks = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                tasks[d["instance_id"]] = d
    return tasks


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent patch vs reference patch, per task.")
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--results-dir", required=True, type=Path)
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)
    rows = [json.loads(l) for l in (args.results_dir / "dev_results.jsonl").open() if l.strip()]
    print(f"{'id':<16}{'resolved':<9}{'loc':<14}ref files -> agent files")
    for r in rows:
        t = tasks.get(r["id"])
        if not t:
            continue
        p = args.results_dir / "patches" / f"{r['id']}.patch"
        agent = p.read_text(encoding="utf-8") if p.exists() else ""
        loc = localize(t["patch"], agent)
        print(f"{r['id']:<16}{str(r['resolved']):<9}{loc.label:<14}{loc.ref_files} -> {loc.agent_files}")


if __name__ == "__main__":
    main()
