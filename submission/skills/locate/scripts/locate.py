"""Rank the code an issue is about, in one call.

Usage (positional terms, quote multi-word strings):
    locate.py TERM [TERM ...]

TERMs are the literal names from the issue: functions, classes, parameters,
options, error-message fragments, dotted paths or file paths. The script
git-greps every term, maps each hit to its enclosing def/class with ast,
scores definitions by how many rare terms they contain, and prints the top
definitions verbatim (usable as edit_file old_string), twin definitions,
other mentions (exports, docs_src) and the test file to run.

Read-only and stdlib-only. Runs from a temp dir, so it locates the repo
itself (PWD, then sys.path[0], then /workspace).
"""

import ast
import math
import os
import re
import subprocess
import sys
from collections import defaultdict

MAX_CHARS = 7000  # stay well inside the 32k context
TOP_SHOWN = 3  # definitions printed verbatim
MAX_LINES_PER_DEF = 60
WINDOW = 12  # context lines around hits inside long definitions
HITS_PER_TERM = 3000

TEST_RE = re.compile(r'(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$')
DOCS_RE = re.compile(r'(^|/)(docs?|docs_src|examples?|benchmarks?|scripts)/')


def find_root():
    candidates = [os.environ.get('PWD'), sys.path[0] if sys.path else None, '/workspace']
    for cand in candidates:
        if cand and os.path.isdir(os.path.join(cand, '.git')):
            return cand
    return None


def git(root, *args):
    res = subprocess.run(
        ['git', *args], cwd=root, capture_output=True, text=True, errors='replace'
    )
    return res.stdout


def grep_term(root, term, ignore_case=False):
    args = ['grep', '-n', '-I', '-F', '--no-color']
    if ignore_case:
        args.append('-i')
    args += ['-e', term]
    out = git(root, *args)
    hits = []
    for line in out.splitlines():
        parts = line.split(':', 2)
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        hits.append((parts[0], int(parts[1])))
        if len(hits) >= HITS_PER_TERM:
            break
    return hits


def expand_terms(raw_terms):
    """Dotted paths also search their last two components; strip call syntax."""
    terms = []
    for raw in raw_terms:
        t = raw.strip().strip('`"\'')
        t = re.sub(r'\(\)$', '', t)
        if not t or len(t) < 3:
            continue
        terms.append(t)
        if '.' in t and '/' not in t and ' ' not in t and not t.endswith('.py'):
            parts = [p for p in t.split('.') if p]
            if len(parts) >= 2:
                terms.append('.'.join(parts[-2:]))
                terms.append(parts[-1])
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


class Def:
    __slots__ = ('path', 'name', 'qualname', 'start', 'end', 'kind')

    def __init__(self, path, name, qualname, start, end, kind):
        self.path, self.name, self.qualname = path, name, qualname
        self.start, self.end, self.kind = start, end, kind


def collect_defs(path, source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    defs = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                end = getattr(child, 'end_lineno', None) or child.lineno
                qual = f'{prefix}{child.name}'
                kind = 'class' if isinstance(child, ast.ClassDef) else 'def'
                defs.append(Def(path, child.name, qual, start, end, kind))
                visit(child, qual + '.')
            else:
                visit(child, prefix)

    visit(tree, '')
    return defs


def innermost(defs, line):
    best = None
    for d in defs:
        if d.start <= line <= d.end and (best is None or d.end - d.start < best.end - best.start):
            best = d
    return best


def windows(hit_lines, start, end):
    """Merge hit±WINDOW ranges clipped to [start, end]."""
    spans = []
    for ln in sorted(hit_lines):
        a, b = max(start, ln - WINDOW), min(end, ln + WINDOW)
        if spans and a <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return spans


def main(argv):
    raw = [a for a in argv if a != '--']
    if not raw:
        print('usage: locate.py TERM [TERM ...]  (literal names/messages from the issue)')
        return 1
    root = find_root()
    if root is None:
        print('error: could not find the repository root (/workspace)')
        return 1

    terms = expand_terms(raw)
    files = git(root, 'ls-files').splitlines()
    n_files = max(1, len(files))
    tracked = set(files)

    hits = {}
    for t in terms:
        h = grep_term(root, t)
        if not h and t.lower() != t:
            h = grep_term(root, t, ignore_case=True)
        hits[t] = h

    # Path-like terms: match tracked file paths directly.
    path_hits = []
    for t in terms:
        if '/' in t or t.endswith('.py'):
            path_hits += [f for f in files if f.endswith(t) or t in f]

    # Term weight: rare terms count more (idf over files).
    weight = {}
    for t, h in hits.items():
        df = len({p for p, _ in h})
        weight[t] = math.log(1 + n_files / df) if df else 0.0

    sources, defs_by_file = {}, {}

    def load(path):
        if path not in sources:
            try:
                with open(os.path.join(root, path), encoding='utf-8', errors='replace') as f:
                    sources[path] = f.read()
            except OSError:
                sources[path] = ''
            defs_by_file[path] = collect_defs(path, sources[path]) if path.endswith('.py') else []
        return sources[path]

    # Score each enclosing definition (or module region) per term.
    score = defaultdict(float)
    sym_terms = defaultdict(set)
    sym_lines = defaultdict(set)
    sym_obj = {}
    test_score = defaultdict(float)
    other_mentions = defaultdict(set)

    for t, h in hits.items():
        w = weight[t]
        if w <= 0:
            continue
        last = t.split('.')[-1]
        per_sym = defaultdict(int)
        for path, ln in h:
            if path not in tracked:
                continue
            if TEST_RE.search(path):
                test_score[path] += w * (0.3 if DOCS_RE.search(path) else 1.0)
                continue
            if not path.endswith('.py') or DOCS_RE.search(path):
                other_mentions[path].add(ln)
                continue
            load(path)
            d = innermost(defs_by_file[path], ln)
            key = (path, d.qualname) if d else (path, '<module>')
            sym_obj[key] = d
            per_sym[key] += 1
            sym_lines[key].add(ln)
            if d is None:
                other_mentions[path].add(ln)
        for key, count in per_sym.items():
            d = sym_obj[key]
            s = w * (1 + 0.3 * math.log(count))
            if d is not None:
                # Many hits spread over a huge def are weaker than a dense small one.
                s /= max(1.0, (d.end - d.start + 1) / 80) ** 0.5
                if d.name == last:
                    s += 1.5 * w  # the term names this definition
            else:
                s *= 0.5
            score[key] += s
            sym_terms[key].add(t)

    ranked = sorted(
        (k for k in score if sym_obj.get(k) is not None),
        key=lambda k: (-score[k], -len(sym_terms[k]), k),
    )

    out = []

    def emit(s=''):
        out.append(s)

    emit('TERMS: ' + ', '.join(f'{t} ({len(hits[t])} hits)' for t in terms))
    if path_hits:
        emit('FILES matching path terms: ' + ', '.join(sorted(set(path_hits))[:10]))
    if not ranked:
        emit('No source definition contains these terms. Try other literal names from the issue.')

    emit('')
    emit('RANKED DEFINITIONS (path:start-end  qualname  score  [terms])')
    for k in ranked[:10]:
        d = sym_obj[k]
        emit(f'  {d.path}:{d.start}-{d.end}  {d.qualname}  {score[k]:.1f}  [{", ".join(sorted(sym_terms[k]))}]')

    # Top definitions, skipping ones nested inside an already shown one.
    top = []
    for k in ranked:
        d = sym_obj[k]
        if any(o.path == d.path and o.start <= d.start and d.end <= o.end for o in top):
            continue
        top.append(d)
        if len(top) == TOP_SHOWN:
            break

    for d in top:
        k = (d.path, d.qualname)
        lines = load(d.path).splitlines()
        emit('')
        if d.end - d.start + 1 <= MAX_LINES_PER_DEF:
            spans = [[d.start, d.end]]
        else:
            spans = windows(sym_lines[k] | {d.start}, d.start, d.end)
        shown = 0
        for a, b in spans:
            b = min(b, a + MAX_LINES_PER_DEF - shown - 1)
            if b < a:
                break
            emit(f'=== {d.path}:{a}-{b}  ({d.kind} {d.qualname}, lines {d.start}-{d.end}) verbatim:')
            emit('\n'.join(lines[a - 1 : b]))
            shown += b - a + 1

    # Twins: other definitions with the same name as a top definition.
    twin_lines = []
    for d in top:
        if d.name.startswith('__'):
            continue  # __init__ and friends are everywhere
        pat = rf'^[[:space:]]*(async[[:space:]]+)?(def|class)[[:space:]]+{re.escape(d.name)}[^A-Za-z0-9_]'
        found = git(root, 'grep', '-n', '-E', pat, '--', '*.py').splitlines()
        found = [f for f in found if not TEST_RE.search(f.split(':', 1)[0])]
        if len(found) > 8:
            twin_lines.append(f'  {d.name}: {len(found)} definitions, too common to list')
            continue
        for line in found:
            path, ln = line.split(':', 2)[:2]
            if any(path == o.path and o.start <= int(ln) <= o.end for o in top):
                continue
            twin_lines.append(f'  {path}:{ln}  {d.name}')
    if twin_lines:
        emit('')
        emit('SAME-NAME DEFINITIONS (sync/async, compat or sibling twins that may need the same change):')
        for t in dict.fromkeys(twin_lines):
            emit(t)

    mentions = sorted(other_mentions.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    mentions = [(p, ls) for p, ls in mentions if p.endswith(('.py', '.pyi'))]
    if mentions:
        emit('')
        emit('OTHER MENTIONS (module-level code, exports, docs_src examples):')
        for p, ls in mentions[:10]:
            emit(f'  {p}: lines ' + ','.join(str(n) for n in sorted(ls)[:8]))

    # Tests: files hit by the terms, plus files named after the top modules.
    for d in top:
        stem = os.path.splitext(os.path.basename(d.path))[0].lstrip('_')
        for f in files:
            base = os.path.basename(f)
            if TEST_RE.search(f) and f.endswith('.py') and stem and stem in base:
                test_score[f] += 0.5
    tests = sorted(test_score.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    emit('')
    if tests:
        # -k only with a term that occurs in a test function name of that file.
        test_names = re.findall(r'def (test_\w+)', load(tests[0][0]))
        best_kw = None
        for t in sorted(terms, key=lambda t: -weight.get(t, 0)):
            kw = t.split('.')[-1]
            if re.fullmatch(r'\w+', kw) and any(kw.lower() in n.lower() for n in test_names):
                best_kw = kw
                break
        emit('TESTS: ' + ', '.join(p for p, _ in tests))
        k_opt = f' -k {best_kw}' if best_kw else ''
        emit(f'RUN: timeout 150 python -m pytest {tests[0][0]} -x -q{k_opt}')
    else:
        emit('TESTS: none found by name; try: find . -path ./.git -prune -o -name "test_*.py" -print | head')

    text = '\n'.join(out)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + '\n[output truncated]'
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
