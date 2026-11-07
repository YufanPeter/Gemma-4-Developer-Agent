"""Compact code navigation for the task repository (stdlib only, Python 3.8+, read-only).

Set it up once with run_skill_script(skill_name="code-nav", file_path="scripts/nav.py", args=["setup"]).
That copies this script to /tmp/nav.py; after that run every command with run_command, one per call:

  python3 /tmp/nav.py find NAME OTHER_NAME    rank source files and functions by the terms; shows the best one
  python3 /tmp/nav.py show FILE SYMBOL ...    one or more functions, classes or constants with line numbers
  python3 /tmp/nav.py show FILE 120-180       those lines (show FILE LINE: the function around LINE)
  python3 /tmp/nav.py refs NAME               uses of NAME outside tests, grouped by function
  python3 /tmp/nav.py tests NAME              existing test functions that use NAME
  python3 /tmp/nav.py edit FILE 120 124 <<'EOF'   replace lines 120-124 of FILE with the lines up to EOF
  python3 /tmp/nav.py edit FILE after 124 <<'EOF' insert lines after line 124 (after 0: at the top)
  python3 /tmp/nav.py delete FILE 120 124     remove lines 120-124
  python3 /tmp/nav.py check                   before submit_patch: compile, warnings, missed places, diff

Every output starts with a status line: code-nav calls so far, time left, edited files, code already seen.
Before the first source edit, reading (find, show, refs, tests) pauses after LOCK_READS successful reads, or after
LOCK_MIN_READS reads once LOCK_SECONDS have passed. A paused read prints the code viewed last instead; show still
works for code already viewed.
"""
import ast
import builtins
import difflib
import hashlib
import json
import keyword
import os
import re
import subprocess
import sys
import tempfile
import time

NAV = "python3 /tmp/nav.py"
SETUP_CALL = 'run_skill_script(skill_name="code-nav", file_path="scripts/nav.py", args=["setup"])'
SETUP_WORDS = ("setup", "install", "help", "load", "start", "init")


def call(command):
    """The exact shell command (for run_command) that runs a code-nav command; hints print it to copy."""
    return "%s %s" % (NAV, command)


def is_repo(path):
    return bool(path) and os.path.exists(os.path.join(path, ".git"))


def find_root():
    """The repository: NAV_ROOT (tests), else a git repo on PYTHONPATH, else the repo around the current dir.

    run_skill_script runs this script from a temporary copy, so the current directory is not the
    repository; swegemma's sandbox puts the workspace on PYTHONPATH. /workspace itself may not exist.
    """
    if os.environ.get("NAV_ROOT"):
        return os.environ["NAV_ROOT"]
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if is_repo(entry):
            return os.path.normpath(entry)
    try:
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    if is_repo("/workspace"):
        return "/workspace"
    return None


ROOT = find_root() or "/workspace"
ROOT_FOUND = os.path.exists(os.path.join(ROOT, ".git"))
MAX_CHARS = 4500  # run_command keeps only 5000 characters of output; stay below it with room for a banner
MAX_DIFF_CHARS = 2500
NON_SOURCE_DIRS = ("docs/", "docs_src/", "examples/", "benchmarks/", "scripts/")
TEST_DIRS = ("tests/", "test/", "testing/")
# The grader drops patch changes to these files (swegemma strip_protected_files_from_patch).
PROTECTED_NAMES = {"conftest.py", "pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg", ".pytest.ini",
                   "sitecustomize.py", "usercustomize.py", "_swegemma_stubs.py"}
SCRATCH_TOKENS = {"repro", "reproduce", "reproducer", "reproduction", "scratch", "tmp", "temp", "debug", "try"}


BANNER = []  # lines printed before any output: status line, syntax errors, edit reminder
TASK_SECONDS = 480  # the task's time budget (eval_config.yaml max_time_minutes)
SETUP_AT = 60  # setup usually happens about a minute into the task; count that as already spent
READ_CMDS = ("find", "show", "refs", "tests", "outline")
LOCK_READS = 7  # code-nav reads allowed before the first source edit (about tool call 12) ...
LOCK_SECONDS = 180  # ... or, after at least LOCK_MIN_READS reads, this many seconds since the first command;
LOCK_MIN_READS = 4  # then reading pauses until a source file is edited
SLOW_SCAN = 4.0  # seconds; after an edit scan this slow, later edits skip the missed-places scan
READ_ERRORS = ("No such file", "Symbol ", "Ambiguous ", "Unknown or incomplete", "Path ", "No uses of", "No test uses",
               "No classes or functions")


LAST_OUT = [""]  # the body of the last out() call, without the banner


def out(text, more=None):
    """Print at most MAX_CHARS, cut at a line boundary; `more` says how to see the rest."""
    LAST_OUT[0] = text
    text = "\n".join(BANNER + [text]) if BANNER else text
    if len(text) > MAX_CHARS:
        cut = text.rfind("\n", 0, MAX_CHARS - 200)
        text = text[:cut if cut > 0 else MAX_CHARS - 200] + "\n... [output cut at %d characters; %s]" % (
            MAX_CHARS, more or "narrow the query")
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


def last_part(name):
    """Class.method -> method: source code rarely spells the dotted name out."""
    return name.split(".")[-1] if re.match(r"^[A-Za-z_][\w.]*\.[A-Za-z_]\w*$", name) else name


def grep(term, keep):
    """git grep a fixed string in tracked .py files; whole words for identifiers."""
    term = last_part(term)
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


# ---------------------------------------------------------------- setup and state

def state_dirs():
    """Where /tmp is: run_command rewrites /tmp in commands to the sandbox's <root>/tmp next to <root>/workspace."""
    if os.environ.get("NAV_STATE_DIR"):
        return [os.environ["NAV_STATE_DIR"]]
    dirs = []
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        entry = os.path.normpath(entry) if entry else ""
        if entry and os.path.basename(entry) == "workspace":
            dirs.append(os.path.join(os.path.dirname(entry), "tmp"))
    dirs.append(tempfile.gettempdir())
    return [d for i, d in enumerate(dirs) if d not in dirs[:i] and os.path.isdir(d)]


def running_installed():
    here = os.path.realpath(os.path.abspath(__file__))
    return any(here == os.path.realpath(os.path.join(d, "nav.py")) for d in state_dirs())


def install():
    """Copy this script to every candidate /tmp as nav.py; returns the paths written."""
    with open(os.path.abspath(__file__)) as fh:
        code = fh.read()
    written = []
    for folder in state_dirs():
        try:
            with open(os.path.join(folder, "nav.py"), "w") as fh:
                fh.write(code)
            written.append(os.path.join(folder, "nav.py"))
        except Exception:
            pass
    return written


STATE = {}


def state_path():
    dirs = state_dirs()
    return os.path.join(dirs[0], "nav_state.json") if dirs else None


def load_state():
    """{'start': setup time, 'calls': code-nav calls, 'seen': {key: [call, digest, label]}, 'terms': last find terms,
    'reads': reads before the first source edit,
    'spots': {path:name: [times shown, path, name, start, end, [line numbers printed]]}, 'created': new files made by edit,
    'scan_secs': duration of the last missed-places scan after an edit}"""
    data = {}
    try:
        with open(state_path()) as fh:
            data = json.load(fh)
    except Exception:
        pass
    STATE.update({"start": data.get("start") or time.time(), "calls": int(data.get("calls") or 0),
                  "seen": data.get("seen") or {}, "terms": data.get("terms") or [],
                  "reads": int(data.get("reads") or 0),
                  "spots": data.get("spots") or {}, "created": data.get("created") or [],
                  "scan_secs": float(data.get("scan_secs") or 0)})


def save_state():
    try:
        with open(state_path(), "w") as fh:
            json.dump(STATE, fh)
    except Exception:
        pass


def changed_files():
    """[(path, added, removed)] for tracked changes against the baseline plus untracked files (added = -1)."""
    _, numstat = git("diff", "--numstat", baseline_ref())
    files = []
    for ln in numstat.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3:
            files.append((parts[2], int(parts[0]) if parts[0].isdigit() else 0, int(parts[1]) if parts[1].isdigit() else 0))
    _, untracked = git("ls-files", "--others", "--exclude-standard")
    files += [(p, -1, 0) for p in untracked.splitlines() if p.strip()]
    return files


def elapsed():
    s = int(time.time() - STATE["start"])
    return "%dm%02ds" % (s // 60, s % 60)


def time_left_seconds():
    return TASK_SECONDS - SETUP_AT - (time.time() - STATE["start"])


def time_left():
    s = int(TASK_SECONDS - SETUP_AT - (time.time() - STATE["start"]))
    if s <= 30:
        return "time almost up: check and submit now"
    return "about %dm%02ds left of %dm" % (s // 60, s % 60, TASK_SECONDS // 60)


def status_lines(files):
    """The first output lines: progress, edits and code already seen, and a reminder when nothing is edited yet."""
    edited = ["%s %s" % (p, "new" if a < 0 else "+%d -%d" % (a, r)) for p, a, r in files]
    seen = [v[2] for v in sorted(STATE["seen"].values(), key=lambda v: v[0])][-6:]
    lines = ["[code-nav call %d | %s | edited: %s | seen: %s]" % (
        STATE["calls"], time_left(), ", ".join(edited[:3]) + (" +%d more" % (len(edited) - 3) if len(edited) > 3 else "")
        if edited else "nothing yet", ", ".join(seen) if seen else "-")]
    return lines


def note_seen(key, label, text):
    """Remember that this code was shown; returns a note if it was already shown in an earlier call."""
    if not STATE:
        return ""
    digest = hashlib.md5(text.encode("utf-8", "replace")).hexdigest()[:12]
    prev = STATE["seen"].get(key)
    STATE["seen"][key] = [STATE["calls"], digest, label]
    if not prev or prev[0] == STATE["calls"]:
        return ""
    if prev[1] == digest:
        return "  (already shown in code-nav call %d, unchanged since)" % prev[0]
    return "  (shown in code-nav call %d, changed since)" % prev[0]


# ---------------------------------------------------------------- structure

_tree_cache = {}


def parse_symbols(src):
    """[(qualname, start, end, kind)] for every def/class in src, including ones inside if/try/with
    blocks; [] if it does not parse."""
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
            elif isinstance(child, ast.stmt) or type(child).__name__ == "ExceptHandler":
                walk(child, prefix)  # if/else, try/except, with, for: same scope

    try:
        walk(tree, "")
    except Exception:  # e.g. Python < 3.8 has no end_lineno
        return []
    return sorted(result, key=lambda r: r[1])


def parse_names(src):
    """[(qualname, start, end)] for module and class level assignments (constants, class attributes)."""
    try:
        tree = ast.parse(src)
    except Exception:
        return []
    result = []

    def targets(node):
        if isinstance(node, ast.Assign):
            return [t for t in node.targets if isinstance(t, ast.Name)]
        if isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            return [node.target]
        return []

    def walk(body, prefix):
        for child in body:
            for t in targets(child):
                result.append((prefix + t.id, child.lineno, child.end_lineno))
            if isinstance(child, ast.ClassDef):
                walk(child.body, prefix + child.name + ".")
            elif isinstance(child, (ast.If, ast.Try, ast.With)):
                walk(getattr(child, "body", []) + getattr(child, "orelse", []) + getattr(child, "finalbody", []), prefix)

    try:
        walk(tree.body, "")
    except Exception:
        return []
    return result


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def split_lines(text):
    """Lines as Python numbers them: split on newlines only. str.splitlines() also splits on form feeds,
    \x1c-\x1e, \x85 and U+2028/9, which would shift line numbers against ast and git."""
    lines = text.split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


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

FIND_CODE_LINES = 40  # find prints the best function's code when it is shorter than this


def cmd_find(terms):
    if STATE:
        STATE["terms"] = list(terms)
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
    top = None  # best function of the best file, shown inline when it is short
    best = len(ranked[0][1]) if ranked else 0
    weaker = [kv for kv in ranked[:8] if len(kv[1]) < best and best >= 2]
    if len(weaker) > 5:  # files with fewer terms than the best: keep five, with two functions each
        ranked = [kv for kv in ranked[:8] if len(kv[1]) >= best] + weaker[:5]
    for path, term_lines in ranked[:8]:
        lines.append("%s  (%d/%d terms)" % (path, len(term_lines), len(terms)))
        funcs = {}
        for term, lns in term_lines.items():
            for ln in lns:
                enc = enclosing(path, ln) or ("<module>", 0, 0)
                funcs.setdefault(enc, set()).add(term)
        shown = 6 if len(term_lines) >= best else 2
        for (name, start, end), ts in sorted(funcs.items(), key=lambda kv: (-len(kv[1]), kv[0][1]))[:shown]:
            span = "L%d-%d" % (start, end) if start else ""
            lines.append("    %-45s %-12s %s" % (name, span, ", ".join(sorted(ts))))
            if top is None and start:
                top = (path, name, start, end)
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
    if top and top[3] - top[2] < FIND_CODE_LINES:
        room = min(1800, MAX_CHARS - 600 - len("\n".join(BANNER + lines)))
        if room >= 600:
            path, name, start, end = top
            src = read(path)
            src_lines = split_lines(src)
            folder = _Folder()
            try:
                folder.visit(ast.parse(src))
            except Exception:
                pass
            block = render(path, src_lines, start, end, folder.ranges, 60,
                           "Best match, %s  %s  L%d-%d:" % (path, name, start, end), room)
            lines.extend(seen_block(block, path, name, start, end, src_lines))
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


PAGE_CHARS = 3800  # one page of show output, so that header, banner and hint stay under MAX_CHARS


def render(path, src_lines, start, end, folds, max_lines, header, budget=PAGE_CHARS):
    """Numbered lines start..end with folds; stops at max_lines or budget characters and says how to go on."""
    lines = [header]
    used = len(header)
    ln = start
    for first, (last, fold) in folds.items():
        if fold is None and first < start <= last:
            lines.append("      ...  (text folded)")
            ln = last + 1
    shown = 0
    while ln <= end:
        rng = folds.get(ln)
        if rng and ln <= rng[0] <= end:
            last, fold = rng
            if fold:
                col, fname, end_col = fold
                row = "%5d %s" % (ln, src_lines[ln - 1][:col] + fname + "(...)" + src_lines[last - 1][end_col:])
            else:
                row = "      ...  (%d lines of text folded)" % (last - ln + 1)
            nxt = last + 1
        else:
            text = src_lines[ln - 1]
            row = "%5d %s" % (ln, text if len(text) <= 150 else text[:150] + " ...")
            nxt = ln + 1
        if shown and (shown >= max_lines or used + len(row) + 1 > budget):
            lines.append("... more below. Next page: " + call("show %s %d-%d" % (path, ln, end)))
            break
        lines.append(row)
        used += len(row) + 1
        shown += 1
        ln = nxt
    return lines


def where_defined(name):
    """Definitions of name anywhere in the code (def, class or top-level assignment). POSIX regex only."""
    name = last_part(name)
    if not re.match(r"^[A-Za-z_]\w*$", name):
        return []
    sp = "[[:space:]]"
    pattern = ("^%s*(async%s+)?(def|class)%s+%s([^[:alnum:]_]|$)|^%s*%s%s*(:[^=]*)?=([^=]|$)"
               % (sp, sp, sp, name, sp, name, sp))
    _, stdout = git("grep", "-n", "-I", "-E", "-e", pattern, "--", "*.py")
    found = []
    for ln in stdout.splitlines():
        parts = ln.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit() and is_code(parts[0]):
            found.append("%s:%s" % (parts[0], parts[1]))
    return found


SUMMARY_LINES = 60  # a longer function is shown as its header plus the lines with the find terms, unless --full


def signature_end(src_lines, start, end):
    """Last line of the def/class header that starts at start (decorators included)."""
    depth = 0
    for ln in range(start, min(end, start + 1000) + 1):
        text = re.sub(r"#.*$", "", src_lines[ln - 1])
        depth += sum(text.count(c) for c in "([{") - sum(text.count(c) for c in ")]}")
        if depth <= 0 and text.rstrip().endswith(":"):
            return ln
    return start


def summary(path, src_lines, name, start, end, folds, budget):
    """Header lines plus the lines that contain the last find terms (one line of context each); None if no term matches."""
    words = [last_part(t).lower() for t in STATE.get("terms", []) if len(last_part(t)) >= 3]
    if not words:
        return None
    folded = set()
    for first, (last, _) in folds.items():
        folded.update(range(first, last + 1))
    head = set(range(start, min(signature_end(src_lines, start, end), start + 6) + 1))
    hits = [ln for ln in range(start, end + 1)
            if ln not in head and ln not in folded and any(w in src_lines[ln - 1].lower() for w in words)]
    if not hits:
        return None
    keep = set(head)
    for ln in hits[:40]:
        keep.update(x for x in range(ln - 1, ln + 2) if start <= x <= end and x not in folded)
    lines = ["%s  %s  L%d-%d  (long: header and the lines with %s; full text: %s)" % (
        path, name, start, end, ", ".join(STATE["terms"][:4]), call("show %s %s --full" % (path, name)))]
    used, prev = len(lines[0]), None
    for ln in sorted(keep):
        if prev is not None and ln != prev + 1:
            lines.append("      ...")
        text = src_lines[ln - 1]
        row = "%5d %s" % (ln, text if len(text) <= 150 else text[:150] + " ...")
        if used + len(row) + 1 > budget:
            lines.append("      ... (more lines with the terms below; use the full text)")
            break
        lines.append(row)
        used += len(row) + 1
        prev = ln
    return lines


SHOWN_ROW = re.compile(r"^\s*(\d+) ")  # a numbered code row printed by render or summary


def seen_block(lines, path, name, start, end, src_lines):
    """Record the shown code and mark the header if an earlier call already showed it."""
    note = note_seen("%s:%s" % (path, name), "%s:%s" % (os.path.basename(path), name),
                     "\n".join(src_lines[start - 1:end]))
    if STATE:  # remember which lines were really printed: output may stop at a page limit
        shown = [int(m.group(1)) for m in (SHOWN_ROW.match(row) for row in lines[1:]) if m]
        spot = STATE["spots"].get("%s:%s" % (path, name)) or [0, path, name, start, end, []]
        prev = spot[5] if len(spot) > 5 else []
        STATE["spots"]["%s:%s" % (path, name)] = [spot[0] + 1, path, name, start, end, sorted(set(prev + shown))]
    if note and lines:
        lines[0] += note
    return lines


def show_block(path, src_lines, syms, folds, src, target, budget=PAGE_CHARS, full=False, max_lines=160):
    """The lines that show one target (symbol, line, or line range) of path."""
    m = re.match(r"^L?(\d+)\s*(?:-|:|\.\.)\s*L?(\d+)$", target)
    if m:  # line range A-B
        a, b = int(m.group(1)), int(m.group(2))
        a, b = max(1, min(a, b)), min(len(src_lines), max(a, b))
        return seen_block(render(path, src_lines, a, b, folds, max_lines, "%s  lines %d-%d" % (path, a, b), budget),
                          path, "%d-%d" % (a, b), a, b, src_lines)
    if target.lstrip("L").isdigit():
        line = int(target.lstrip("L"))
        enc = enclosing(path, line)
        if enc is None:
            name, start, end = "lines", max(1, line - 20), min(len(src_lines), line + 20)
        else:
            name, start, end = enc
            if end - start > max_lines:
                start = max(start, line - 10)
        return seen_block(render(path, src_lines, start, end, folds, max_lines,
                                 "%s  %s  L%d-%d" % (path, name, start, end), budget),
                          path, name if enc else "%d-%d" % (start, end), start, end, src_lines)
    cands = [s for s in syms if s[0] == target] or [s for s in syms if s[0].endswith("." + target)]
    if not cands:
        names = [n for n in parse_names(src) if n[0] == target or n[0].endswith("." + target)]
        if names:
            return [line for n, a, b in names[:3]
                    for line in seen_block(render(path, src_lines, a, b, folds, max_lines, "%s  %s  L%d-%d" % (path, n, a, b),
                                                  budget // len(names[:3])), path, n, a, b, src_lines)]
        key = target.split(".")[-1]
        near = [s[0] for s in syms if key.lower() in s[0].lower()][:8]
        msg = ["Symbol %s is not defined in %s." % (target, path)]
        imported = [i + 1 for i, t in enumerate(src_lines) if re.search(r"\bimport\b.*\b%s\b" % re.escape(key), t)]
        if imported:
            msg.append("It is imported there at line %d: %s" % (imported[0], src_lines[imported[0] - 1].strip()[:120]))
        defs = where_defined(key)
        if defs:
            msg.append("Defined at: " + ", ".join(defs[:6]) + (" ..." if len(defs) > 6 else ""))
            msg.append("Next: " + call("show %s %s" % (defs[0].split(":")[0], key)))
        if near:
            msg.append("Similar names in this file: " + ", ".join(near))
        if not syms:
            msg.append("(this file has no parsable functions; it may have a syntax error)")
        return msg
    if len(set(c[0] for c in cands)) > 1:
        return ["Ambiguous %s, pick one: " % target + ", ".join("%s L%d" % (c[0], c[1]) for c in cands)]
    if len(cands) > 1:  # same name defined twice: property getter/setter, if/else variants
        part = budget // len(cands[:3])
        return [line for n, a, b, _ in cands[:3]
                for line in seen_block(render(path, src_lines, a, b, folds, max_lines, "%s  %s  L%d-%d" % (path, n, a, b), part),
                                       path, n, a, b, src_lines)]
    name, start, end, kind = cands[0]
    if kind == "class" and end - start > max_lines:
        members = [s for s in syms if s[0].startswith(name + ".") and s[0].count(".") == name.count(".") + 1]
        first_member = min([s[1] for s in members] + [end + 1])
        lines = ["%s  class %s  L%d-%d (large: header and members only)" % (path, name, start, end)]
        for ln in range(start, min(first_member, start + 40)):
            lines.append("%5d %s" % (ln, src_lines[ln - 1][:150]))
        for m_name, m_start, m_end, m_kind in members:
            lines.append("      %s %s  L%d-%d" % (m_kind, m_name.split(".")[-1], m_start, m_end))
        lines.append("Show members: " + call("show %s %s.METHOD %s.OTHER" % (path, name, name)))
        return lines
    if not full and kind == "def" and end - start + 1 > SUMMARY_LINES:
        lines = summary(path, src_lines, name, start, end, folds, budget)
        if lines:
            return seen_block(lines, path, name + " (summary)", start, end, src_lines)
    return seen_block(render(path, src_lines, start, end, folds, max_lines, "%s  %s  L%d-%d" % (path, name, start, end), budget),
                      path, name, start, end, src_lines)


MAX_SHOW = 4  # symbols per show call; the page is split between them


def cmd_show(path, targets=None, full=False):
    rel = resolve(path)
    if rel is None:
        out("Path %s is outside the repository." % path)
        return
    path = rel
    if not os.path.isfile(os.path.join(ROOT, path)):
        out("No such file: %s (paths are relative to the repository root)" % path)
        return
    if not targets:
        cmd_outline(path)
        return
    src = read(path)
    src_lines = split_lines(src)
    folder = _Folder()
    try:
        folder.visit(ast.parse(src))
    except Exception:
        pass
    shown = [t.strip() for t in targets[:MAX_SHOW]]
    budget = PAGE_CHARS // len(shown)
    lines = [line for t in shown
             for line in show_block(path, src_lines, symbols(path), folder.ranges, src, t, budget, full)]
    if len(targets) > MAX_SHOW:
        lines.append("(at most %d names per show; not shown: %s)" % (MAX_SHOW, " ".join(targets[MAX_SHOW:])))
    out("\n".join(lines))


# ---------------------------------------------------------------- refs / tests / outline

def cmd_refs(name):
    hits = grep(name, is_code)
    note = " (searched for %s)" % last_part(name) if last_part(name) != name else ""
    if not hits:
        out("No uses of %s outside tests%s." % (name, note))
        return
    groups = {}
    for path, line, text in hits:
        groups.setdefault((path, func_of(path, line)), []).append((line, text.strip()))
    lines = ["%d uses in %d places (tests excluded)%s" % (len(hits), len(groups), note)]
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
        src_lines = split_lines(read(path))
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
            lines.append("      ... (rest: " + call("show %s %d" % (path, start)) + ")")
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


def baseline_ref():
    return "_swegemma_baseline" if git("rev-parse", "--verify", "-q", "_swegemma_baseline")[0] == 0 else "HEAD"


def syntax_errors(files):
    """['file:line: message'] for edited or new .py files (from changed_files) that do not compile."""
    errors = []
    for p in sorted(set(f[0] for f in files)):
        if not p.endswith(".py") or not os.path.isfile(os.path.join(ROOT, p)):
            continue
        try:
            compile(read(p), p, "exec")
        except SyntaxError as e:
            errors.append("%s:%s: %s" % (p, e.lineno, e.msg))
        except Exception:
            pass
    return errors


MAX_RELATED = 8  # extra lines from the related-place rules below
WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")


def index_functions():
    """Every function in the library source files of the working tree: dicts with qual (Class.method),
    path, start, end, params (without self/cls), calls (called names) and body (stripped lines)."""
    _, listed = git("ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py")
    funcs = []
    for path in sorted(set(listed.splitlines())):
        if not is_source(path) or not os.path.isfile(os.path.join(ROOT, path)):
            continue
        try:
            src = read(path)
            tree = ast.parse(src)
        except Exception:
            continue
        lines = split_lines(src)

        def walk(node, prefix):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    walk(child, prefix + child.name + ".")
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = child.args
                    params = [x.arg for x in getattr(a, "posonlyargs", []) + a.args + a.kwonlyargs
                              if x.arg not in ("self", "cls")]
                    calls = set()
                    for sub in ast.walk(child):
                        if isinstance(sub, ast.Call):
                            calls.add(getattr(sub.func, "id", None) or getattr(sub.func, "attr", None))
                    start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    funcs.append({"qual": prefix + child.name, "path": path, "start": start, "end": child.end_lineno,
                                  "params": params, "calls": calls,
                                  "body": [l.strip() for l in lines[child.lineno - 1:child.end_lineno]]})
                    walk(child, prefix + child.name + ".")
                elif isinstance(child, ast.stmt) or type(child).__name__ == "ExceptHandler":
                    walk(child, prefix)

        try:
            walk(tree, "")
        except Exception:
            continue
    return funcs


def related_places(files, touched, old_sources, listed, skip_names):
    """Places the same change may be missing, found from how the diff changed the code, then callers of the
    changed functions. Returns (missed lines, caller lines), at most MAX_RELATED lines together.
    Offline on the 50 public tasks that change several functions (seeded with one changed function):
    these rules list another changed function in 42 of them, and about 4 in 10 lines they print are right."""
    funcs = index_functions()
    by_key = {(f["path"], f["qual"]): f for f in funcs}
    seeds = [by_key[k] for k in sorted(touched) if k in by_key]
    if not seeds:
        return [], []
    skip = set(touched) | set(listed)
    missed, callers = [], []

    def add(target, f, why):
        key = (f["path"], f["qual"])
        if key in skip or len(missed) + len(callers) >= MAX_RELATED:
            return
        skip.add(key)
        target.append("    %s  %s  L%d: %s" % (f["path"], f["qual"], f["start"], why))

    # 1. a parameter the edit added: functions sharing the old parameters that still lack it
    for seed in seeds:
        old_src = old_sources.get(seed["path"])
        if old_src is None:
            code, old_src = git("show", "%s:%s" % (baseline_ref(), seed["path"]))
            old_sources[seed["path"]] = old_src if code == 0 else ""
        old = signatures(old_src or "").get(seed["qual"])
        if not old:
            continue
        old_params = [x for x in old[0] if x not in ("self", "cls")]
        gained = [x for x in seed["params"] if x not in old_params]
        if not gained or len(old_params) < 3:
            continue
        sib = []
        for f in funcs:
            if f is seed or any(g in f["params"] for g in gained):
                continue
            n = len(set(old_params) & set(f["params"]))
            if n >= 3 and n >= 0.5 * min(len(old_params), len(f["params"])):
                sib.append((-n, f["path"] != seed["path"], f["qual"], f))
        for neg, _, _, f in sorted(sib, key=lambda x: x[:3])[:6]:
            add(missed, f, "shares %d parameters with %s but has no %s" % (-neg, seed["qual"], gained[0]))

    # 2. an untouched function that still has a line the edit changed (copied code)
    removed = []
    for p, ch in files.items():
        if is_source(p):
            removed += [t.strip() for _, t in ch["minus"]]
    for line in removed:
        if len(line) < 20 or line.startswith(("#", "pass", "return None", '"""', "'''")):
            continue
        same = [f for f in funcs if line in f["body"] and (f["path"], f["qual"]) not in skip]
        if 1 <= len(same) <= 8:
            for f in same:
                add(missed, f, "still has a line you changed: %s" % line[:60])

    # 3. a rare name the edit removed that untouched functions still use
    for p, ch in files.items():
        if not is_source(p):
            continue
        gone = set()
        for old_lines, new_lines in ch["hunks"]:  # per hunk: an import line elsewhere may still name it
            gone |= {w for t in old_lines for w in WORD.findall(t)} - {w for t in new_lines for w in WORD.findall(t)}
        for w in sorted(gone - COMMON - set(skip_names)):
            pat = re.compile(r"\b%s\b" % re.escape(w))
            users = [f for f in funcs if (f["path"], f["qual"]) not in skip and any(pat.search(l) for l in f["body"])]
            if 1 <= len(users) <= 6:
                for f in users:
                    add(missed, f, "uses %s, which your edit replaced or removed" % w)

    # 4. callers of the changed functions
    for seed in seeds:
        parts = seed["qual"].split(".")
        name = parts[-2] if parts[-1] == "__init__" and len(parts) > 1 else parts[-1]
        found = [f for f in funcs if f is not seed and name in f["calls"]]
        if len(found) > 8:  # a common name: keep callers that pass the parameters on, or live in the same file
            found = [f for f in found if len(set(f["params"]) & set(seed["params"])) >= 2 or f["path"] == seed["path"]]
        for f in found[:8]:
            add(callers, f, "calls %s" % seed["qual"])
    return missed, callers


def missed_places(files, changed, untracked, base):
    """(missed, callers): places the same change may still be needed, and callers of the changed functions."""
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
    listed = set()  # (path, function) already printed, so the related-place rules do not repeat them
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
            missed.append("    (many uses; it may be a general name - check with refs %s if needed)" % tok)
            continue
        for p, ln, t in rest[:6]:
            listed.add((p, func_of(p, ln)))
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
                listed.add((cp, func_of(cp, ln)))
                missed.append("    %s  %s  %d: %s" % (cp, func_of(cp, ln), ln, t.strip()[:90]))
            if len(calls) > 6:
                missed.append("    ... %d more" % (len(calls) - 6))
    try:
        more, callers = related_places(files, {t for t in touched if t[1] != "<module>"}, old_sources, listed,
                                       [tok for _, tok in replaced[:5]])
    except Exception:
        more, callers = [], []
    if more:
        missed.append("  same kind of code elsewhere:")
        missed.extend(more)
    return missed, callers


def diff_files():
    """(base, files, untracked, changed): the parsed diff against the baseline plus untracked files."""
    base = baseline_ref()
    _, diff = git("diff", "-U0", "--no-color", "--no-ext-diff", base)
    _, untracked_out = git("ls-files", "--others", "--exclude-standard")
    untracked = [p for p in untracked_out.splitlines() if p.strip()]
    files = parse_diff(diff)
    for p in untracked:
        if p.endswith(".py"):
            try:
                files[p] = {"minus": [], "plus": [(i + 1, t) for i, t in enumerate(split_lines(read(p)))], "hunks": [],
                            "events": [], "new": True}
            except Exception:
                pass
    return base, files, untracked, sorted(set(files) | set(untracked))


def cmd_check():
    base, files, untracked, changed = diff_files()
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

    missed, callers = missed_places(files, changed, untracked, base)
    if missed:
        report.append("possible missed places (change one only if the same change is needed there):")
        report.extend(missed)
    else:
        report.append("possible missed places: none found")
    if callers:
        report.append("callers of the functions you changed (edit one only if your change alters what it passes or gets back):")
        report.extend(callers)

    # compact diff, last so that it is what gets cut. Every changed file gets a header line and its first
    # changes before any file gets more, so one long file cannot hide the others.
    blocks = []
    for p in changed:
        if p in untracked:
            try:
                new_lines = split_lines(read(p))
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


# ---------------------------------------------------------------- reading pause / edit

def source_edited(files):
    """True when a tracked source file (not a test or runner config) differs from the baseline, or a file that
    code-nav edit created exists. Untracked scratch files (repro.py) do not count."""
    created = set(STATE.get("created") or [])
    return any(not is_protected(p) and not is_test(p) and (a >= 0 or p in created) for p, a, _ in files)


RANGE = re.compile(r"^L?(\d+)\s*(?:-|:|\.\.)\s*L?(\d+)$")


def covers(spot, a, b):
    """True when spot printed one unbroken block of lines that includes a..b (a summary does not count)."""
    shown = spot[5] if len(spot) > 5 else []
    return bool(shown) and not spot[2].endswith(" (summary)") and shown[0] <= a and b <= shown[-1]


def viewed(rel, target, full=False):
    """True when show TARGET of rel would print only lines that an earlier read already printed."""
    spots = [s for s in STATE["spots"].values() if s[1] == rel]
    m = RANGE.match(target)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return any(covers(s, a, b) for s in spots)
    if target.lstrip("L").isdigit():
        return False  # show FILE LINE prints the whole enclosing function, which may be new code
    syms = symbols(rel)
    cands = {s[0]: s for s in syms if s[0] == target} or {s[0]: s for s in syms if s[0].endswith("." + target)}
    if len(cands) != 1:
        return False
    name, start, end = list(cands.values())[0][:3]
    return any(s[2] == name and covers(s, start, end) or s[2] == name + " (summary)" and not full for s in spots)


def reshow_only(rest, full=False):
    """True when a show asks only for code already printed: it gives exact line numbers, not new reading."""
    rel = resolve(rest[0]) if rest else None
    targets = rest[1:]
    nums = [x for x in targets if x not in ("-", "..", ":", "to")]
    if len(nums) == 2 and all(re.match(r"^L?\d+$", x) for x in nums):
        targets = ["%s-%s" % (nums[0], nums[1])]
    return bool(rel and targets) and all(viewed(rel, t, full) for t in targets[:MAX_SHOW])


PAUSE_SPOTS = 2  # while paused, a blocked read prints the code viewed last, up to this many functions ...
PAUSE_LINES = 30  # ... each with at most this many of the lines it printed (the ones with the find terms first)


def spot_code(spot, budget):
    """Lines that an earlier read printed for spot, read again from the file; [] if there are none."""
    path, name, start, end = spot[1:5]
    try:
        src_lines = split_lines(read(path))
    except Exception:
        return []
    keep = [ln for ln in (spot[5] if len(spot) > 5 else []) if 1 <= ln <= len(src_lines)]
    if len(keep) > PAUSE_LINES:  # the header plus the lines with the last find terms, if there are any
        words = [last_part(t).lower() for t in STATE.get("terms", []) if len(last_part(t)) >= 3]
        near = {x for ln in keep if any(w in src_lines[ln - 1].lower() for w in words) for x in (ln - 1, ln, ln + 1)}
        pick = keep[:3] + [ln for ln in keep[3:] if ln in near]
        keep = pick if len(pick) > 3 else keep
    keep = keep[:PAUSE_LINES]
    if not keep:
        return []
    name = name.replace(" (summary)", "")
    lines = ["%s  lines %d-%d" % (path, keep[0], keep[-1]) if RANGE.match(name)
             else "%s  %s  L%d-%d" % (path, name, start, end)]
    used, prev = len(lines[0]), None
    for ln in keep:
        if prev is not None and ln != prev + 1:
            lines.append("      ...")
        text = src_lines[ln - 1]
        row = "%5d %s" % (ln, text if len(text) <= 150 else text[:150] + " ...")
        if used + len(row) + 1 > budget:
            lines.append("      ...")
            break
        lines.append(row)
        used += len(row) + 1
        prev = ln
    return lines


def edit_example(path="FILE", start="START", end="END"):
    return "%s <<'EOF'\n<the new lines, with their full indentation>\nEOF" % call("edit %s %s %s" % (path, start, end))


def pause_message():
    """What a blocked read prints: the code viewed last with its line numbers, and how to edit it."""
    recent = []
    for key in sorted(STATE["spots"], key=lambda k: -(STATE["seen"].get(k) or [0])[0]):
        spot = STATE["spots"][key]
        shown = spot[5] if len(spot) > 5 else []
        if shown and len(recent) < PAUSE_SPOTS and not any(
                spot[1] == r[1] and shown[0] <= r[5][-1] and r[5][0] <= shown[-1] for r in recent):
            recent.append(spot)
    lines = ["READING PAUSED: %d code-nav reads and %s since your first code-nav command, and no source file is"
             " edited yet. This read did not run." % (STATE["reads"], elapsed()),
             "Reading resumes right after your first source edit. Make your best edit now%s: a first edit can be"
             " refined later, an unedited tree scores zero." % (" in the code below" if recent else "")]
    if recent:
        lines.append("The code you viewed last:")
        for spot in recent:
            lines.extend(spot_code(spot, 1600))
    lines.append("Edit with:\n" + edit_example(recent[0][1] if recent else "FILE"))
    lines.append("While paused, show still works for code you already viewed: %s" % call("show FILE SYMBOL"))
    return "\n".join(lines)


def read_gate(cmd, rest, files, full=False):
    """(message, counts): message is None if the read may run, else what to print instead; counts says whether a
    successful read is one of the LOCK_READS reads before the first source edit."""
    if cmd not in READ_CMDS or source_edited(files):
        return None, False
    paused = STATE["reads"] >= LOCK_READS or (
        STATE["reads"] >= LOCK_MIN_READS and time.time() - STATE["start"] >= LOCK_SECONDS)
    if not paused:
        left = LOCK_READS - STATE["reads"] - 1
        if left <= 2:
            BANNER.append("!! %s, then reading pauses until you edit a source file." % (
                "This is your last code-nav read" if left <= 0 else "%d more code-nav read%s after this one" % (
                    left, "" if left == 1 else "s")))
        return None, True
    if cmd == "show" and reshow_only(rest, full):
        BANNER.append("READING PAUSED: this is code you already viewed, shown again for its line numbers. Edit next"
                      " with %s" % call("edit FILE START END <<'EOF'"))
        return None, False
    return pause_message(), False


def read_failed(text):
    """True when a read printed only an error or no result, so it should not use up one of the reads."""
    text = text.strip()
    return text.startswith(READ_ERRORS) or text.endswith("Try other exact strings, or tests NAME.")


def read_raw(path):
    """[(line, ending)] of a file as stored, so that an edit keeps every other line byte for byte."""
    with open(os.path.join(ROOT, path), encoding="utf-8", newline="") as fh:
        src = fh.read()
    parts = src.split("\n")
    rows = [(t[:-1], "\r\n") if t.endswith("\r") else (t, "\n") for t in parts[:-1]]
    if parts[-1]:
        rows.append((parts[-1], ""))  # last line without a newline
    return rows


def indent_note(removed, new):
    """A warning when the first non-blank new line is indented differently from the first replaced line."""
    old_ind = next((len(t) - len(t.lstrip()) for t in removed if t.strip()), None)
    new_ind = next((len(t) - len(t.lstrip()) for t in new if t.strip()), None)
    if old_ind is None or new_ind is None or old_ind == new_ind:
        return None
    return "Check the indentation: the replaced lines started with %d spaces, yours with %d." % (old_ind, new_ind)


EOF_LINE = re.compile(r"""^\s*['"]?EOF['"]?\s*$""")


def cmd_edit(rest, delete=False):
    """edit FILE START END / edit FILE START-END / edit FILE after N, with the new lines on stdin (heredoc);
    delete FILE START END removes lines."""
    usage = ("Usage:\n" + edit_example() + "\nInsert without replacing: %s\nDelete lines: %s"
             % (call("edit FILE after LINE <<'EOF' ..."), call("delete FILE START END")))
    if len(rest) == 1:
        m = re.match(r"^(.+?):L?(\d+)(?:-L?(\d+))?$", rest[0])
        if m:
            rest = [m.group(1), m.group(2), m.group(3) or m.group(2)]
    if len(rest) == 2:
        m = re.match(r"^L?(\d+)\s*(?:-|:|\.\.)\s*L?(\d+)$", rest[1])
        rest = [rest[0], m.group(1), m.group(2)] if m else rest + [rest[1]]
    nums = [x.lstrip("L") for x in rest[1:3]]
    if len(rest) < 3 or not (nums[1].isdigit() and (nums[0].isdigit() or (nums[0].lower() == "after" and not delete))):
        out("Could not read the line range. " + usage)
        return
    rel = resolve(rest[0])
    if rel is None:
        out("Path %s is outside the repository." % rest[0])
        return
    if is_test(rel) or is_protected(rel):
        out("NOT WRITTEN: %s is a test or runner config file; the grader drops changes to it. Fix the source instead." % rel)
        return
    insert = nums[0].lower() == "after"
    start, end = (int(nums[1]) + 1, int(nums[1])) if insert else (int(nums[0]), int(nums[1]))
    if not insert and start > end:
        out("NOT WRITTEN: START %d is after END %d. Give the first and the last line to replace, e.g. %s;"
            " to insert use %s" % (start, end, call("%s %s %d %d" % ("delete" if delete else "edit", rel, end, start)),
                                   call("edit %s after %d <<'EOF' ..." % (rel, end))))
        return
    notes = []
    if delete:
        new = []
    else:
        text = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        new = text.replace("\r\n", "\n").split("\n")
        if new and new[-1] == "":
            new.pop()
        if new and EOF_LINE.match(new[-1]):  # the EOF line was indented, so the heredoc never closed
            new.pop()
            notes.append("note: your EOF line was indented, so it was read as code and dropped. Put EOF alone at the"
                         " start of its line.")
        if not new or not any(t.strip() for t in new):
            out("NOT WRITTEN: no new lines arrived. Put them between <<'EOF' and a line with only EOF:\n"
                + edit_example(rel, *nums) + "\nTo remove lines use: " + call("delete %s START END" % rel))
            return
    full = os.path.join(ROOT, rel)
    if os.path.isfile(full):
        try:
            rows = read_raw(rel)
        except UnicodeDecodeError:
            out("NOT WRITTEN: %s is not UTF-8 text." % rel)
            return
        created = False
    elif start == 1 and end == 0 and not delete:
        rows, created = [], True
    else:
        out("No such file: %s (paths are relative to the repository root). For a new file use: %s"
            % (rel, call("edit %s after 0 <<'EOF' ..." % rel)))
        return
    old = [t for t, _ in rows]
    if start < 1 or end > len(old) or start > end + 1:
        out("NOT WRITTEN: %s has %d lines; lines %d-%d do not exist. See the lines with %s"
            % (rel, len(old), start, end, call("show %s %d-%d" % (
                rel, max(1, min(start, len(old)) - 5), min(len(old), max(end, 1) + 5)))))
        return
    removed = old[start - 1:end]
    if removed == new:
        out("NO CHANGE: lines %d-%d of %s already are exactly these lines." % (start, end, rel))
        return
    # new lines take the line ending of the line before them (or of the first replaced line); others keep theirs
    eol = (rows[start - 2][1] if start >= 2 else rows[0][1] if rows else "\n") or "\n"
    tail_eol = rows[end - 1][1] if removed else eol  # keeps a missing final newline missing
    new_rows = [(t, eol) for t in new]
    if new_rows and end == len(old) and removed:
        new_rows[-1] = (new_rows[-1][0], tail_eol)
    result_rows = rows[:start - 1] + new_rows + rows[end:]
    for i in range(len(result_rows) - 1):  # every line but the last needs an ending
        if not result_rows[i][1]:
            result_rows[i] = (result_rows[i][0], eol)
    result = [t for t, _ in result_rows]
    body = "".join(t + e for t, e in result_rows)
    span = range(start, start + len(new))
    note = indent_note(removed, new)
    if rel.endswith(".py"):
        try:
            compile("".join(t + e for t, e in rows), rel, "exec")
            compiled_before = True
        except Exception:
            compiled_before = False  # already broken: do not block edits that may be fixing it
        if compiled_before:
            try:
                compile(body, rel, "exec")
            except SyntaxError as e:
                ln = e.lineno or start
                lines = ["NOT WRITTEN: %s would not compile: line %d: %s. The file is unchanged." % (rel, ln, e.msg),
                         "How it would look (+ = your new lines):"]
                lo, hi = max(1, min(ln, start) - 3), min(len(result), max(ln, start + max(len(new), 1) - 1) + 3)
                lines.extend("%s%5d %s" % ("+" if i in span else " ", i, result[i - 1][:150]) for i in range(lo, hi + 1))
                if note:
                    lines.append(note)
                lines.append("Fix the lines and run the same command again with the same line numbers.")
                out("\n".join(lines))
                return
    os.makedirs(os.path.dirname(full) or ROOT, exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    if created:
        STATE["created"] = sorted(set(STATE["created"]) | {rel})
    shift = len(new) - len(removed)
    report = status_lines(changed_files())[:1]
    if delete:
        what = "deleted lines %d-%d (%d)" % (start, end, len(removed))
    elif insert:
        what = "inserted %d line%s after line %d" % (len(new), "" if len(new) == 1 else "s", start - 1)
    else:
        what = "lines %d-%d (%d) replaced by %d line%s" % (start, end, len(removed), len(new), "" if len(new) == 1 else "s")
    report.append("EDITED %s: %s; %s." % (rel, what, "later line numbers moved by %+d" % shift if shift else
                                         "line numbers unchanged"))
    if removed:
        report.append("removed:")
        report.extend("-%5d %s" % (start + i, t[:150]) for i, t in enumerate(removed[:12]))
        if len(removed) > 12:
            report.append("  ... %d more" % (len(removed) - 12))
    report.append("now:")
    a, b = max(1, start - 2), min(len(result), start + len(new) + 1)
    report.extend("%s%5d %s" % ("+" if i in span else " ", i, result[i - 1][:150]) for i in range(a, min(b, a + 34) + 1))
    report.extend(notes)
    if note:
        report.append("note: " + note)
    if rel.endswith(".py") and STATE["scan_secs"] < SLOW_SCAN and time_left_seconds() > 90:
        t0 = time.time()
        try:
            base, files, untracked, changed = diff_files()
            missed, _ = missed_places(files, changed, untracked, base)
        except Exception:
            missed = []
        STATE["scan_secs"] = time.time() - t0
        if missed:
            report.append("The same change may also be needed here (edit one only if the issue needs it there too):")
            report.extend(missed[:10])
            if len(missed) > 10:
                report.append("  ... more in " + call("check"))
    report.append("Next: run one targeted test, or make the next edit; run %s once before submit_patch." % call("check"))
    out("\n".join(report))


# ---------------------------------------------------------------- main

USAGE = __doc__


def clean(arg):
    """Drop quotes and backticks the model sometimes wraps around words."""
    return arg.strip().strip("\"'`").strip()


def main(argv):
    # The whole command usually arrives as one string ("show a.py Foo"); also accept a list or dict-style args.
    if len(argv) == 1 and " " in argv[0].strip():
        argv = argv[0].split()
    argv = [clean(a) for a in argv if clean(a)]
    dict_style = bool(argv) and argv[0].startswith("--")  # ADK turns args={"show": f} into --show f
    cmd = argv[0].lstrip("-").lower() if argv else ""
    rest = [a for a in argv[1:] if not (dict_style and a.startswith("--"))]
    full = "--full" in rest
    rest = [a for a in rest if a != "--full"]
    commands = ("Commands: find NAME..., show FILE SYMBOL..., show FILE 120-180, show FILE, refs NAME, tests NAME...,"
                " edit FILE START END <<'EOF' ... EOF, delete FILE START END, check")
    if not running_installed():  # started by run_skill_script: copy to /tmp, then answer the command if there is one
        if not install():
            out("code-nav setup failed: no writable temp directory. Use git grep and read_file instead.")
            return
        if not argv or cmd in SETUP_WORDS:
            out("code-nav is ready. Do not call run_skill_script again. Run each command with run_command,"
                " one per call, for example:\n  " + call("find exact_name other_name") + "\n" + commands)
            return
        BANNER.append("code-nav is ready. Run the next commands with run_command, for example: " + call("show FILE SYMBOL"))
    elif not argv or cmd in SETUP_WORDS:
        out("code-nav is already set up. Run one command per call, for example:\n  " + call("find exact_name other_name")
            + "\n" + commands)
        return
    if not ROOT_FOUND:
        out("code-nav could not find the repository (cwd %s, PYTHONPATH %s). Use git grep and read_file instead."
            % (os.getcwd(), os.environ.get("PYTHONPATH", "")))
        return
    load_state()
    STATE["calls"] += 1
    if cmd in ("edit", "delete"):  # prints its own status line after writing
        try:
            cmd_edit(rest, delete=cmd == "delete" or "--delete" in rest)
        finally:
            save_state()
        return
    files = changed_files()
    m = re.match(r"^([^:]+\.py):(L?\d+(?:-L?\d+)?)$", rest[0]) if cmd == "show" and len(rest) == 1 else None
    if m:
        rest = [m.group(1), m.group(2)]  # show FILE:LINE or FILE:A-B
    BANNER.extend(status_lines(files) if cmd != "check" else status_lines(files)[:1])
    gate, counts = read_gate(cmd, rest, files, full)
    if gate:
        out(gate)
        save_state()
        return
    if cmd != "check":
        errors = syntax_errors(files)
        if errors:
            BANNER.append("SYNTAX ERROR in your edits - fix it before anything else (tests cannot import the file):")
            BANNER.extend("  " + e for e in errors[:3])
    try:
        dispatch(cmd, rest, argv, full)
        if counts and not read_failed(LAST_OUT[0]):
            STATE["reads"] += 1
    finally:
        save_state()


def dispatch(cmd, rest, argv, full):
    if cmd == "find" and rest:
        cmd_find(rest)
    elif cmd == "show" and len(rest) >= 2:
        targets = rest[1:]
        nums = [x for x in rest[1:] if x not in ("-", "..", ":", "to")]
        if len(nums) == 2 and re.match(r"^L?\d+$", nums[0]) and re.match(r"^L?\d+$", nums[1]):
            targets = ["%s-%s" % (nums[0], nums[1])]  # show FILE 461 519 / show FILE 461 - 519
        cmd_show(rest[0], targets, full)
    elif cmd == "show" and len(rest) == 1:
        cmd_show(rest[0])
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
