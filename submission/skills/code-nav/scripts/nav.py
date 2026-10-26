"""Compact code navigation for the task repository (stdlib only, Python 3.8+, read-only).

Install once with run_skill_script(skill_name="code-nav", file_path="scripts/nav.py", args=["install"]),
then run every command with run_command from the repository:

  python3 /tmp/nav.py find TERM [TERM ...]   rank source files and their functions by the issue's terms
  python3 /tmp/nav.py show FILE SYMBOL|LINE  print one function or class with line numbers (docstrings folded)
  python3 /tmp/nav.py refs NAME              every use of NAME outside tests, grouped by enclosing function
  python3 /tmp/nav.py tests NAME [NAME ...]  existing test functions that use NAME, with their first lines
  python3 /tmp/nav.py outline FILE           classes and functions of FILE with line ranges
  python3 /tmp/nav.py check                  before submit_patch: compile, file warnings, missed places, diff
"""
import ast
import builtins
import difflib
import keyword
import os
import re
import subprocess
import sys
import tempfile

NAV = "python3 /tmp/nav.py"


def find_root():
    """The repository: NAV_ROOT (tests), else the git repo that contains the current directory.

    run_command starts in the repository, so the current directory is enough there. The harness may
    not have a real /workspace directory (it rewrites that path inside run_command strings only).
    """
    if os.environ.get("NAV_ROOT"):
        return os.environ["NAV_ROOT"]
    try:
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    if os.path.isdir("/workspace/.git"):
        return "/workspace"
    return None


ROOT = find_root() or "/workspace"
ROOT_FOUND = os.path.exists(os.path.join(ROOT, ".git"))
MAX_CHARS = 6000
MAX_DIFF_CHARS = 2500
NON_SOURCE_DIRS = ("docs/", "docs_src/", "examples/", "benchmarks/", "scripts/")
TEST_DIRS = ("tests/", "test/", "testing/")
# The grader drops patch changes to these files (swegemma strip_protected_files_from_patch).
PROTECTED_NAMES = {"conftest.py", "pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg", ".pytest.ini",
                   "sitecustomize.py", "usercustomize.py", "_swegemma_stubs.py"}
SCRATCH_TOKENS = {"repro", "reproduce", "reproducer", "reproduction", "scratch", "tmp", "temp", "debug", "try"}


def out(text):
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n... [truncated; narrow the query]"
    print(text)


def git(*args):
    try:
        res = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, text=True, timeout=60)
        return res.returncode, res.stdout
    except Exception:
        return 1, ""


def in_dirs(path, dirs):
    return any(path.startswith(d) or ("/" + d) in path for d in dirs)


def is_test(path):
    base = os.path.basename(path)
    if not path.endswith(".py") or base == "conftest.py":
        return False
    return base.startswith("test_") or base.endswith("_test.py") or in_dirs(path, TEST_DIRS)


def is_protected(path):
    """Same rule as the grader: test files and runner configs, at any depth."""
    parts = path.split("/")
    name = parts[-1]
    if name in PROTECTED_NAMES or name.endswith(".pth"):
        return True
    if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
        return True
    return name.endswith(".py") and bool({d.lower() for d in parts[:-1]} & {"tests", "test", "testing"})


def is_source(path):
    """Library code: not tests, docs, examples or scripts."""
    return path.endswith(".py") and not is_test(path) and os.path.basename(path) != "conftest.py" and not in_dirs(path, NON_SOURCE_DIRS)


def is_code(path):
    """Anything a test could import: library code plus docs_src, examples and scripts."""
    return path.endswith(".py") and not is_test(path) and os.path.basename(path) != "conftest.py"


def norm_path(path):
    path = path.strip().strip("'\"")
    if path.startswith(ROOT.rstrip("/") + "/"):
        path = path[len(ROOT.rstrip("/")) + 1:]
    while path.startswith("./"):
        path = path[2:]
    return path


def resolve(path):
    """Path relative to /workspace, or None if it points outside /workspace."""
    root = os.path.realpath(ROOT)
    full = os.path.realpath(os.path.join(root, norm_path(path)))
    if os.path.commonpath([root, full]) != root:
        return None
    return os.path.relpath(full, root)


def grep(term, keep):
    """git grep a fixed string in tracked .py files; whole words for identifiers."""
    args = ["grep", "-n", "-I", "-F"]
    if re.match(r"^\w+$", term):
        args.append("-w")
    _, stdout = git(*(args + ["-e", term, "--", "*.py"]))
    hits = []
    for ln in stdout.splitlines():
        parts = ln.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit() and keep(parts[0]):
            hits.append((parts[0], int(parts[1]), parts[2]))
    return hits


# ---------------------------------------------------------------- structure

_tree_cache = {}


def parse_symbols(src):
    """[(qualname, start, end, kind)] for every def/class in src; [] if it does not parse."""
    try:
        tree = ast.parse(src)
    except Exception:
        return []
    result = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                kind = "class" if isinstance(child, ast.ClassDef) else "def"
                result.append((name, start, child.end_lineno, kind))
                walk(child, name + ".")

    try:
        walk(tree, "")
    except Exception:  # e.g. Python < 3.8 has no end_lineno
        return []
    return result


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def symbols(path):
    if path not in _tree_cache:
        try:
            _tree_cache[path] = parse_symbols(read(path))
        except Exception:
            _tree_cache[path] = []
    return _tree_cache[path]


def enclosing(path, line):
    best = None
    for name, start, end, _ in symbols(path):
        if start <= line <= end and (best is None or start >= best[1]):
            best = (name, start, end)
    return best


def func_of(path, line):
    enc = enclosing(path, line)
    return enc[0] if enc else "<module>"


# ---------------------------------------------------------------- find

def cmd_find(terms):
    by_file, aux = {}, {}
    dropped = []
    for term in terms:
        hits = grep(term, is_code)
        core = [h for h in hits if is_source(h[0])]
        if len(core) > 400:
            dropped.append(term)
            continue
        for path, line, _ in hits:
            target = by_file if is_source(path) else aux
            target.setdefault(path, {}).setdefault(term, []).append(line)

    def rank(files):
        return sorted(files.items(), key=lambda kv: (-len(kv[1]), -sum(len(v) for v in kv[1].values()), kv[0]))

    lines = []
    if dropped:
        lines.append("ignored (too common): " + ", ".join(dropped))
    if not by_file:
        lines.append("No library source matches for: " + ", ".join(terms) + ".")
    # docs_src / examples / scripts code that tests may import: listed apart, never mixed into the ranking
    aux_ranked = [kv for kv in rank(aux) if len(kv[1]) >= min(2, len(terms) - len(dropped)) or not by_file][:3]
    if not by_file and not aux_ranked:
        out("\n".join(lines + ["Try other exact strings, or tests NAME."]))
        return
    ranked = rank(by_file)
    for path, term_lines in ranked[:8]:
        lines.append("%s  (%d/%d terms)" % (path, len(term_lines), len(terms)))
        funcs = {}
        for term, lns in term_lines.items():
            for ln in lns:
                enc = enclosing(path, ln) or ("<module>", 0, 0)
                funcs.setdefault(enc, set()).add(term)
        for (name, start, end), ts in sorted(funcs.items(), key=lambda kv: (-len(kv[1]), kv[0][1]))[:6]:
            span = "L%d-%d" % (start, end) if start else ""
            lines.append("    %-45s %-12s %s" % (name, span, ", ".join(sorted(ts))))
    if aux_ranked:
        lines.append("docs/examples/scripts code with these terms (tests may import it):")
        for path, term_lines in aux_ranked:
            funcs = {}
            for term, lns in term_lines.items():
                for ln in lns:
                    enc = enclosing(path, ln) or ("<module>", 0, 0)
                    funcs.setdefault(enc, [set(), ln])[0].add(term)
                    funcs[enc][1] = min(funcs[enc][1], ln)
            (name, start, end), (ts, first) = sorted(funcs.items(), key=lambda kv: (-len(kv[1][0]), kv[1][1]))[0]
            where = "%s L%d-%d" % (name, start, end) if start else "line %d" % first
            lines.append("  %s  %s  (%d/%d terms)" % (path, where, len(term_lines), len(terms)))
    out("\n".join(lines))


# ---------------------------------------------------------------- show

class _Folder(ast.NodeVisitor):
    """Line ranges to fold: docstrings of 4+ lines and multi-line Doc("...") annotations.

    Nothing else is folded: error messages, templates and other string literals stay visible.
    """

    def __init__(self):
        self.ranges = {}  # first line -> (last line, (col, name, end_col) or None)

    def _docstring(self, node):
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc = body[0].value
            if doc.end_lineno - doc.lineno >= 3:
                self.ranges.setdefault(doc.lineno + 1, (doc.end_lineno - 1, None))

    def visit_Module(self, node):
        self._docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._docstring(node)
        self.generic_visit(node)

    visit_FunctionDef = visit_ClassDef
    visit_AsyncFunctionDef = visit_ClassDef

    def visit_Call(self, node):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        args = list(node.args) + [k.value for k in node.keywords]
        if name == "Doc" and args and node.end_lineno - node.lineno >= 2 and all(
            isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args
        ):
            self.ranges[node.lineno] = (node.end_lineno, (node.col_offset, name, node.end_col_offset))
            return
        self.generic_visit(node)


def render(path, src_lines, start, end, folds, max_lines, header):
    lines = [header]
    ln = start
    for first, (last, call) in folds.items():
        if call is None and first < start <= last:
            lines.append("      ...  (text folded)")
            ln = last + 1
    shown = 0
    while ln <= end:
        rng = folds.get(ln)
        if rng and ln <= rng[0] <= end:
            last, call = rng
            if call:
                col, fname, end_col = call
                lines.append("%5d %s" % (ln, src_lines[ln - 1][:col] + fname + "(...)" + src_lines[last - 1][end_col:]))
            else:
                lines.append("      ...  (%d lines of text folded)" % (last - ln + 1))
            ln = last + 1
        else:
            text = src_lines[ln - 1]
            lines.append("%5d %s" % (ln, text if len(text) <= 150 else text[:150] + " ..."))
            ln += 1
        shown += 1
        if shown >= max_lines and ln <= end:
            lines.append("... stopped at line %d; run: %s show %s %d" % (ln - 1, NAV, path, ln))
            break
    return lines


def cmd_show(path, target, max_lines=160):
    rel = resolve(path)
    if rel is None:
        out("Path %s is outside the repository." % path)
        return
    path = rel
    if not os.path.isfile(os.path.join(ROOT, path)):
        out("No such file: %s (paths are relative to the repository root)" % path)
        return
    src = read(path)
    src_lines = src.splitlines()
    syms = symbols(path)
    kind = "def"
    if target.isdigit():
        line = int(target)
        enc = enclosing(path, line)
        if enc is None:
            name, start, end = "lines", max(1, line - 20), min(len(src_lines), line + 20)
        else:
            name, start, end = enc
            if end - start > max_lines:
                start = max(start, line - 10)
    else:
        cands = [s for s in syms if s[0] == target] or [s for s in syms if s[0].endswith("." + target)]
        if not cands:
            key = target.split(".")[-1].lower()
            near = [s[0] for s in syms if key in s[0].lower()][:10]
            hint = "" if syms else " (file has no parsable functions; use read_file)"
            out("Symbol %s not found in %s. Similar: %s%s" % (target, path, ", ".join(near) or "none", hint))
            return
        if len(cands) > 1:
            out("Ambiguous, pick one: " + ", ".join("%s L%d" % (c[0], c[1]) for c in cands))
            return
        name, start, end, kind = cands[0]
    if kind == "class" and end - start > max_lines:
        members = [s for s in syms if s[0].startswith(name + ".") and s[0].count(".") == name.count(".") + 1]
        first_member = min([s[1] for s in members] + [end + 1])
        lines = ["%s  class %s  L%d-%d (large: header and members only)" % (path, name, start, end)]
        for ln in range(start, min(first_member, start + 40)):
            lines.append("%5d %s" % (ln, src_lines[ln - 1]))
        for m_name, m_start, m_end, m_kind in members:
            lines.append("      %s %s  L%d-%d" % (m_kind, m_name.split(".")[-1], m_start, m_end))
        lines.append("Use: %s show %s %s.METHOD" % (NAV, path, name))
        out("\n".join(lines))
        return
    folder = _Folder()
    try:
        folder.visit(ast.parse(src))
    except Exception:
        pass
    out("\n".join(render(path, src_lines, start, end, folder.ranges, max_lines,
                         "%s  %s  L%d-%d" % (path, name, start, end))))


# ---------------------------------------------------------------- refs / tests / outline

def cmd_refs(name):
    hits = grep(name, is_code)
    if not hits:
        out("No uses of %s outside tests." % name)
        return
    groups = {}
    for path, line, text in hits:
        groups.setdefault((path, func_of(path, line)), []).append((line, text.strip()))
    lines = ["%d uses in %d places (tests excluded)" % (len(hits), len(groups))]
    for (path, func), items in sorted(groups.items())[:30]:
        lines.append("%s  %s" % (path, func))
        for line, text in items[:4]:
            lines.append("  %5d  %s" % (line, text[:110]))
        if len(items) > 4:
            lines.append("         ... %d more" % (len(items) - 4))
    if len(groups) > 30:
        lines.append("... %d more places" % (len(groups) - 30))
    out("\n".join(lines))


def cmd_tests(names, per_test=12, max_tests=8):
    groups = {}
    for name in names:
        for path, line, _ in grep(name, is_test):
            enc = enclosing(path, line) or ("<module>", line, line)
            g = groups.setdefault((path,) + tuple(enc), {"names": set(), "lines": set()})
            g["names"].add(name)
            g["lines"].add(line)
    if not groups:
        out("No test uses of: " + ", ".join(names))
        return

    def score(item):
        (path, fname, start, end), g = item
        in_name = any(n.lower() in fname.lower() for n in names)
        return (-len(g["names"]), not in_name, -len(g["lines"]), path, start)

    ranked = sorted(groups.items(), key=score)
    lines = ["%d test functions use %s" % (len(ranked), ", ".join(names))]
    for (path, fname, start, end), g in ranked[:max_tests]:
        src_lines = read(path).splitlines()
        lines.append("%s::%s  L%d-%d" % (path, fname, start, end))
        show = list(range(start, min(end, start + per_test - 1) + 1))
        show += sorted(l for l in g["lines"] if l not in show)[:4]
        prev = None
        for ln in show:
            if prev is not None and ln != prev + 1:
                lines.append("      ...")
            lines.append("%5d %s" % (ln, src_lines[ln - 1][:140]))
            prev = ln
        if end > show[-1]:
            lines.append("      ... (%s show %s %d for the rest)" % (NAV, path, start))
    if len(ranked) > max_tests:
        lines.append("... %d more test functions" % (len(ranked) - max_tests))
    out("\n".join(lines))


def cmd_outline(path):
    rel = resolve(path)
    if rel is None:
        out("Path %s is outside the repository." % path)
        return
    path = rel
    if not os.path.isfile(os.path.join(ROOT, path)):
        out("No such file: %s (paths are relative to the repository root)" % path)
        return
    syms = symbols(path)
    if not syms:
        out("No classes or functions parsed in " + path)
        return
    lines = [path]
    for name, start, end, kind in syms:
        lines.append("%s%s %s  L%d-%d" % ("  " * name.count("."), kind, name.split(".")[-1], start, end))
    out("\n".join(lines))


# ---------------------------------------------------------------- check

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
COMMON = set(keyword.kwlist) | {b for b in dir(builtins) if b.islower()} | {
    "self", "cls", "args", "kwargs", "value", "values", "name", "names", "data", "result",
    "key", "keys", "item", "items", "path", "text", "line", "lines", "obj", "func", "kind",
    "typing", "Optional", "Union", "List", "Dict", "Any", "Tuple", "Callable", "Annotated",
}


def code_tokens(line):
    """Identifier-like tokens of a code line: snake_case, UPPER_CASE, CamelCase, attributes and calls."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return []
    stripped = re.sub(r"\s+#\s.*$", "", stripped)
    found = []
    for m in IDENT.finditer(stripped):
        tok = m.group()
        if tok in COMMON or len(tok) < 4:
            continue
        before = stripped[m.start() - 1] if m.start() else ""
        after = stripped[m.end()] if m.end() < len(stripped) else ""
        if "_" in tok.strip("_") or re.match(r"^[A-Z][a-z0-9]+[A-Z]", tok) or before == "." or after == "(":
            found.append(tok)
    return found


def parse_diff(diff):
    """{path: {'minus': [(old_line, text)], 'plus': [(new_line, text)], 'hunks': [([old], [new])]}} from a -U0 diff."""
    files = {}

    def entry(path):
        return files.setdefault(path, {"minus": [], "plus": [], "hunks": [], "events": [], "new": False})

    created = False

    cur = None
    old_ln = new_ln = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            if p != "/dev/null":  # "+++ /dev/null" means the file was deleted: keep the "--- a/" entry
                cur = entry(p[2:] if p.startswith("b/") else p)
                cur["new"] = cur["new"] or created
        elif raw.startswith("--- "):
            p = raw[4:].strip()
            created = p == "/dev/null"
            cur = None if created else entry(p[2:] if p.startswith("a/") else p)
        elif raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                old_ln, new_ln = int(m.group(1)), int(m.group(2))
                if cur is not None:
                    cur["hunks"].append(([], []))
        elif cur is not None and raw.startswith("-"):
            cur["minus"].append((old_ln, raw[1:]))
            cur["events"].append(("-", old_ln, raw[1:]))
            if cur["hunks"]:
                cur["hunks"][-1][0].append(raw[1:])
            old_ln += 1
        elif cur is not None and raw.startswith("+"):
            cur["plus"].append((new_ln, raw[1:]))
            cur["events"].append(("+", new_ln, raw[1:]))
            if cur["hunks"]:
                cur["hunks"][-1][1].append(raw[1:])
            new_ln += 1
    return files


def signatures(src):
    """{qualname: ([param names], {params that have a default})} for every function in src."""
    try:
        tree = ast.parse(src)
    except Exception:
        return {}
    sigs = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = child.args
                positional = getattr(a, "posonlyargs", []) + a.args
                params = [x.arg for x in positional + a.kwonlyargs]
                optional = {x.arg for x in positional[len(positional) - len(a.defaults):]} if a.defaults else set()
                optional |= {x.arg for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None}
                sigs[prefix + child.name] = (params, optional)
                walk(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")

    walk(tree, "")
    return sigs


def substitutions(files):
    """{token: lines} where an edited line is the old line with exactly this one code name swapped out."""
    subs = {}
    for p, ch in files.items():
        if not p.endswith(".py") or is_test(p):
            continue
        for old_lines, new_lines in ch["hunks"]:
            for old in old_lines:
                if not new_lines:
                    break
                new = max(new_lines, key=lambda n: difflib.SequenceMatcher(None, old, n).ratio())
                if difflib.SequenceMatcher(None, old, new).ratio() < 0.6:
                    continue
                gone = set(code_tokens(old)) - set(code_tokens(new))
                if len(gone) == 1:
                    tok = gone.pop()
                    subs[tok] = subs.get(tok, 0) + 1
    return subs


def looks_like_scratch(path):
    """A new .py file named like a throwaway script (repro.py, tmp_check.py) outside any package."""
    base = os.path.basename(path).lower()
    if not base.endswith(".py"):
        return False
    if not set(re.split(r"[^a-z0-9]+", base[:-3])) & SCRATCH_TOKENS:
        return False
    folder = os.path.dirname(path)
    return not (folder and os.path.isfile(os.path.join(ROOT, folder, "__init__.py")))


def is_global_name(tok):
    return bool(re.match(r"^_?[A-Z][a-z0-9]+[A-Z0-9]", tok) or re.match(r"^_?[A-Z][A-Z0-9_]{3,}$", tok))


_ast_cache = {}


def forwarded_keywords(path, line, short, params):
    """How many of params a call to short on this line passes by keyword (-1 if no such call)."""
    if path not in _ast_cache:
        try:
            _ast_cache[path] = ast.parse(read(path))
        except Exception:
            _ast_cache[path] = None
    tree = _ast_cache[path]
    best = -1
    if tree is None:
        return best
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == line:
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == short:
                best = max(best, sum(1 for k in node.keywords if k.arg in params))
    return best


def cmd_check():
    base = "_swegemma_baseline"
    if git("rev-parse", "--verify", "-q", base)[0] != 0:
        base = "HEAD"
    _, diff = git("diff", "-U0", "--no-color", "--no-ext-diff", base)
    _, untracked_out = git("ls-files", "--others", "--exclude-standard")
    untracked = [p for p in untracked_out.splitlines() if p.strip()]
    files = parse_diff(diff)
    for p in untracked:
        if p.endswith(".py"):
            try:
                files[p] = {"minus": [], "plus": [(i + 1, t) for i, t in enumerate(read(p).splitlines())], "hunks": [],
                            "events": [], "new": True}
            except Exception:
                pass
    changed = sorted(set(files) | set(untracked))
    report = ["CHECK (diff against %s)" % base]
    if not changed:
        report.append("NO CHANGES: the patch is empty. Edit the source before submit_patch.")
        out("\n".join(report))
        return

    report.append("changed: " + ", ".join(
        "%s (+%d -%d)%s" % (p, len(files.get(p, {}).get("plus", [])), len(files.get(p, {}).get("minus", [])),
                            " NEW" if p in untracked else "") for p in changed))

    # compile
    errors = []
    for p in changed:
        if p.endswith(".py") and os.path.isfile(os.path.join(ROOT, p)):
            try:
                compile(read(p), p, "exec")
            except SyntaxError as e:
                errors.append("  %s:%s: %s" % (p, e.lineno, e.msg))
            except Exception as e:
                errors.append("  %s: %s" % (p, e))
    report.append("compile: OK" if not errors else "compile: ERRORS - fix these first")
    report.extend(errors)

    # file warnings
    warnings = []
    for p in changed:
        if is_protected(p):
            warnings.append("  protected file changed: %s - the grader drops changes to tests and runner configs;"
                            " the fix must be in other files." % p)
        elif p in untracked and looks_like_scratch(p):
            warnings.append("  new file %s is named like a scratch script - if you created it only to reproduce or"
                            " debug, delete it (submit_patch includes new files); keep it if the issue needs it." % p)
    if not any(p.endswith(".py") and not is_protected(p) for p in changed):
        warnings.append("  no .py file outside tests and runner configs changed - after grading the patch has no code change.")
    report.append("warnings: none" if not warnings else "warnings:")
    report.extend(warnings)

    # functions the diff touches: added lines in the new file, removed lines in the base version
    touched = set()
    old_sources = {}
    for p, ch in files.items():
        if not p.endswith(".py"):
            continue
        if os.path.isfile(os.path.join(ROOT, p)):
            for ln, _ in ch["plus"]:
                touched.add((p, func_of(p, ln)))
        if ch["minus"] and p not in untracked:
            code, old_src = git("show", "%s:%s" % (base, p))
            if code == 0:
                old_sources[p] = old_src
                old_syms = parse_symbols(old_src)
                for ln, _ in ch["minus"]:
                    best = None
                    for name, start, end, _ in old_syms:
                        if start <= ln <= end and (best is None or start >= best[1]):
                            best = (name, start)
                    touched.add((p, best[0] if best else "<module>"))

    missed = []
    # rule 1: a name the diff swaps out line by line (X -> Y) that is still used elsewhere
    subs = substitutions(files)
    replaced = sorted(((n, tok) for tok, n in subs.items() if n >= 2 or is_global_name(tok)), reverse=True)
    for n, tok in replaced[:5]:
        definition = re.compile(r"^\s*(async\s+)?(def|class)\s+%s\b" % re.escape(tok))
        rest = [(p, ln, t) for p, ln, t in grep(tok, is_code)
                if (p, func_of(p, ln)) not in touched and not definition.match(t)]
        if not rest:
            continue
        missed.append("  %s: replaced on %d edited line(s); still used in %d untouched place(s):" % (tok, n, len(rest)))
        if len(rest) > 12:
            missed.append("    (many uses; it may be a general name - check with %s refs %s if needed)" % (NAV, tok))
            continue
        for p, ln, t in rest[:6]:
            missed.append("    %s  %s  %d: %s" % (p, func_of(p, ln), ln, t.strip()[:90]))
        if len(rest) > 6:
            missed.append("    ... %d more" % (len(rest) - 6))

    # rule 2: functions whose parameters changed, and calls outside the edited functions (at most 5)
    reported = 0
    for p in changed:
        if not p.endswith(".py") or is_test(p) or p in untracked:
            continue
        old_src = old_sources.get(p)
        if old_src is None:
            code, old_src = git("show", "%s:%s" % (base, p))
            if code != 0:
                continue
        if not os.path.isfile(os.path.join(ROOT, p)):
            continue
        old_sigs, new_sigs = signatures(old_src), signatures(read(p))
        for qual, (new_params, new_optional) in new_sigs.items():
            if qual not in old_sigs or reported >= 5:
                continue
            old_params = old_sigs[qual][0]
            added = [x for x in new_params if x not in old_params]
            removed = [x for x in old_params if x not in new_params]
            if not added and not removed:
                continue
            breaking = removed or any(x not in new_optional for x in added)
            short = qual.split(".")[-1]
            if short.startswith("__"):
                short = qual.split(".")[-2] if qual.count(".") else short
            # Breaking changes: every caller matters. Optional additions: only library callers that
            # already forward this function's parameters one by one.
            calls = [(cp, ln, t) for cp, ln, t in grep(short, is_code if breaking else is_source)
                     if (short + "(") in t and not re.match(r"\s*(async\s+)?(def|class)\s", t)
                     and (cp, func_of(cp, ln)) not in touched]
            if not breaking:
                # an optional new parameter only matters for calls that forward the others by keyword
                calls = [c for c in calls if forwarded_keywords(c[0], c[1], short, old_params) >= 3]
            if not calls:
                continue
            change = " ".join(["+" + x + ("" if x in new_optional else " (required)") for x in added]
                              + ["-" + x for x in removed])
            what = "calls in untouched places" if breaking else "library calls that may need to pass it on"
            reported += 1
            missed.append("  %s changed parameters (%s); %s:" % (qual, change, what))
            for cp, ln, t in calls[:6]:
                missed.append("    %s  %s  %d: %s" % (cp, func_of(cp, ln), ln, t.strip()[:90]))
            if len(calls) > 6:
                missed.append("    ... %d more" % (len(calls) - 6))
    if missed:
        report.append("possible missed places (change one only if the same change is needed there):")
        report.extend(missed)
    else:
        report.append("possible missed places: none found")

    # compact diff, last so that it is what gets cut. Every changed file gets a header line and its first
    # changes before any file gets more, so one long file cannot hide the others.
    blocks = []
    for p in changed:
        if p in untracked:
            try:
                new_lines = read(p).splitlines()
            except Exception:
                blocks.append(("%s (new file, not text)" % p, []))
                continue
            blocks.append(("%s (new file, %d lines)" % (p, len(new_lines)),
                           ["  +%5d %s" % (i + 1, t.rstrip()[:120]) for i, t in enumerate(new_lines)]))
            continue
        ch = files.get(p)
        if not ch:
            continue
        if not os.path.exists(os.path.join(ROOT, p)):
            status = " (deleted)"
        elif ch.get("new"):
            status = " (new file)"
        else:
            status = ""
        blocks.append((p + status, ["  %s%5d %s" % (sign, ln, t.rstrip()[:120]) for sign, ln, t in ch["events"]]))

    def cost(lines):
        return sum(len(x) + 1 for x in lines)

    preview = 0
    for k in (5, 3, 1):
        if sum(cost([h] + body[:k]) for h, body in blocks) <= MAX_DIFF_CHARS:
            preview = k
            break
    taken = [list(body[:preview]) for _, body in blocks]
    budget = MAX_DIFF_CHARS - sum(cost([h] + t) for (h, _), t in zip(blocks, taken))
    for (header, body), take in zip(blocks, taken):
        for line in body[len(take):]:
            if len(line) + 1 > budget:
                break
            take.append(line)
            budget -= len(line) + 1
    diff_lines = []
    for (header, body), take in zip(blocks, taken):
        diff_lines.append(header)
        diff_lines.extend(take)
        if len(body) > len(take):
            diff_lines.append("  ... %d more changed lines in this file (git diff -- %s)" % (len(body) - len(take), header.split(" (")[0]))
    diff_text = "\n".join(diff_lines)
    report.append("diff (- old line numbers, + new line numbers):")
    report.append(diff_text)
    out("\n".join(report))


# ---------------------------------------------------------------- main

USAGE = __doc__


def cmd_install():
    """Copy this script to the sandbox /tmp so that run_command can run it as python3 /tmp/nav.py."""
    with open(os.path.abspath(__file__)) as fh:
        code = fh.read()
    targets = []
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        entry = os.path.normpath(entry) if entry else ""
        if entry and os.path.basename(entry) == "workspace":  # swegemma sandbox: <root>/workspace and <root>/tmp
            targets.append(os.path.join(os.path.dirname(entry), "tmp"))
    targets.append(tempfile.gettempdir())
    written = []
    for folder in targets:
        dest = os.path.join(folder, "nav.py")
        if dest in written or not os.path.isdir(folder):
            continue
        try:
            with open(dest, "w") as fh:
                fh.write(code)
            written.append(dest)
        except Exception:
            pass
    if not written:
        out("install failed: no writable temp directory. Do not use code-nav; use git grep and read_file.")
        return
    out("installed code-nav. From now on run it with run_command from the repository, for example:\n"
        '  run_command(command="%s find exact_name other_name")\n'
        "Commands: find TERM..., show FILE SYMBOL|LINE, refs NAME, tests NAME..., outline FILE, check" % NAV)


def clean(arg):
    """Drop quotes and backticks the model sometimes wraps around words."""
    return arg.strip().strip("\"'`").strip()


def main(argv):
    # Tolerate the whole command as one string ("show a.py Foo") and dict-style args (--show a.py --symbol Foo).
    if len(argv) == 1 and " " in argv[0].strip():
        argv = argv[0].split()
    argv = [clean(a) for a in argv if clean(a)]
    if not argv:
        out(USAGE)
        return
    dict_style = argv[0].startswith("--")  # ADK turns args={"show": f, "symbol": s} into --show f --symbol s
    cmd = argv[0].lstrip("-").lower()
    rest = [a for a in argv[1:] if not (dict_style and a.startswith("--"))]
    if cmd == "install":
        cmd_install()
        return
    if not ROOT_FOUND:
        out("code-nav is not running inside the repository. Run it with run_command, for example\n"
            '  run_command(command="%s %s")\n'
            "If /tmp/nav.py does not exist, call run_skill_script with args [\"install\"] once." % (NAV, " ".join(argv)))
        return
    if cmd == "show" and len(rest) == 1 and re.match(r"^[^:]+\.py:\d+$", rest[0]):
        rest = rest[0].split(":")  # show FILE:LINE
    if cmd == "find" and rest:
        cmd_find(rest)
    elif cmd == "show" and len(rest) >= 2:
        cmd_show(rest[0], rest[1])
    elif cmd == "refs" and rest:
        cmd_refs(rest[0])
    elif cmd == "tests" and rest:
        cmd_tests(rest)
    elif cmd == "outline" and rest:
        cmd_outline(rest[0])
    elif cmd == "check":
        cmd_check()
    else:
        out("Unknown or incomplete command: %s\n%s" % (" ".join(argv), USAGE))


if __name__ == "__main__":
    main(sys.argv[1:])
