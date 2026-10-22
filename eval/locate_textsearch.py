"""Rank the source files and lines an issue most likely needs changed, in one call.

Run from the repository root (/workspace). Standard library + git only.

    python locate.py --terms get_authorization_scheme_param "Authorization" \
                     --text "<issue text>"

--terms: the issue's literal names (functions, classes, parameters, options,
         error messages, file paths). Weighted highest.
--text:  the issue text; identifiers, file paths, `code` spans and quoted
         messages are extracted from it automatically.

Prints the top files (tests excluded) with the matching lines and the
def/class each match sits in, then the most relevant test files.
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
from collections import defaultdict

MAX_OUT = 4500        # stay under the 5000-char tool output cap
TOP_FILES = 5
LINES_PER_FILE = 4
TOO_COMMON = 60       # a term in more source files than this says nothing

STOP = set("""
a an and are as at be but by can do does for from has have if in into is it its may might
must no not of on or should so that the then there these this to was we were when where which
while will with without would you your i me my our us also use used using new old add added
get set make value values type types return returns none true false self cls args kwargs str
int float bool list dict tuple bytes object class def import from print error errors issue
bug fix fixes fixed example examples test tests expected actual behavior behaviour current
version python file files line lines code function method module package default defaults
""".split())

WORD_PREFIX = '~'      # marks a plain-word stem: case-insensitive substring search
WORD_STOP = set("""
fixes fixed fixing pull request merge merged checklist changes change changed description
please thanks thank github https http issue issues docs documentation readme changelog
contributors black pedantic review reviewer accept latest args code tests test added adds
feature features type types other none related should would could when what which there
""".split())

TEST_RE = re.compile(r'(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$')
SOURCE_EXT = ('.py', '.pyi')


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, errors='replace').stdout


def extract_terms(text: str) -> dict[str, float]:
    """Literal names from free text, weighted by how specific they look."""
    terms: dict[str, float] = {}

    def add(t: str, w: float) -> None:
        t = t.strip().strip('.,:;()[]{}\'"')
        if len(t) < 3 or t.lower() in STOP:
            return
        terms[t] = max(terms.get(t, 0.0), w)

    for span in re.findall(r'`([^`\n]{2,120})`', text):
        if re.fullmatch(r'[\w.]+(\(\))?', span):
            add(span.removesuffix('()'), 3.0)
            if '.' in span:
                add(span.removesuffix('()').rsplit('.', 1)[-1], 2.5)
        else:
            for ident in re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', span):
                if '_' in ident or re.search(r'[a-z][A-Z]', ident):
                    add(ident, 2.0)
    for path in re.findall(r'[\w./-]+\.py\b', text):
        add(path, 3.0)
    for ident in re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b', text):
        if not re.search(r'\.(com|org|io|html|md|txt|py)$', ident):
            add(ident.rsplit('.', 1)[-1], 1.5)
    for ident in re.findall(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', text):
        if '_' in ident.strip('_') or re.search(r'[a-z][A-Z]', ident):  # snake_case / CamelCase
            add(ident, 1.5)
    for msg in re.findall(r'"([^"\n]{8,80})"|\'([^\'\n]{8,80})\'', text):
        msg = msg[0] or msg[1]
        if ' ' in msg and not msg.startswith(('http', '/')):
            add(msg, 1.0)
    # Plain words (issues often name no identifier at all): stemmed, matched
    # case-insensitively, low weight; idf and TOO_COMMON drop the noisy ones.
    prose = re.sub(r'https?://\S+|`[^`]*`|```.*?```', ' ', text, flags=re.S)
    for word in re.findall(r'\b[A-Za-z]{4,}\b', prose):
        w = word.lower()
        if w in STOP or w in WORD_STOP:
            continue
        stem = re.sub(r'(ing|ed|es|s)$', '', w) if len(w) > 6 else w
        if len(stem) >= 4 and WORD_PREFIX + stem not in terms and not any(t.lower() == w for t in terms):
            terms[WORD_PREFIX + stem] = 0.6
    return terms


def is_source(path: str) -> bool:
    return path.endswith(SOURCE_EXT) and not TEST_RE.search(path)


def grep(term: str) -> dict[str, list[tuple[int, str]]]:
    if term.startswith(WORD_PREFIX):
        out = git('grep', '-n', '-F', '-I', '-i', '--no-color', '-e', term[len(WORD_PREFIX):])
    else:
        out = git('grep', '-n', '-F', '-I', '--no-color', '-e', term)
    hits: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for line in out.splitlines():
        parts = line.split(':', 2)
        if len(parts) == 3 and parts[1].isdigit():
            hits[parts[0]].append((int(parts[1]), parts[2]))
    return hits


def enclosing(path: str, lineno: int, cache: dict[str, list[str]]) -> str:
    if path not in cache:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                cache[path] = f.read().splitlines()
        except OSError:
            cache[path] = []
    lines = cache[path]
    for i in range(min(lineno, len(lines)) - 1, -1, -1):
        m = re.match(r'\s*(async\s+def|def|class)\s+\w+', lines[i])
        if m:
            return f'L{i + 1} {lines[i].strip()[:100]}'
    return ''


def rank(terms: dict[str, float]):
    scores: dict[str, float] = defaultdict(float)
    matched: dict[str, set[str]] = defaultdict(set)
    lines: dict[str, list[tuple[float, int, str]]] = defaultdict(list)
    tests: dict[str, float] = defaultdict(float)
    tracked = git('ls-files').splitlines()
    for term, w in terms.items():
        hits = grep(term)
        if term.endswith('.py'):  # a path: match file names too
            for p in tracked:
                if p.endswith(term) or p.endswith('/' + term.split('/')[-1]):
                    hits.setdefault(p, []).append((1, '<path match>'))
        src = {p: h for p, h in hits.items() if is_source(p)}
        for p, h in hits.items():
            if TEST_RE.search(p) and p.endswith('.py'):
                tests[p] += w * len(h)
        if not src or len(src) > TOO_COMMON:
            continue
        idf = 1.0 / (1.0 + math.log(len(src)))
        def_re = re.compile(rf'^\s*(async\s+def|def|class)\s+{re.escape(term)}\b|^{re.escape(term)}\s*[:=]')
        for p, h in src.items():
            is_def = any(def_re.search(text) for _, text in h)
            s = w * idf * (1 + math.log(len(h))) + (3 * w if is_def else 0)
            if term.lstrip(WORD_PREFIX).split('/')[-1].removesuffix('.py').lower() in p.split('/')[-1].lower():
                s += 2 * w
            scores[p] += s
            matched[p].add(term)
            for ln, text in h:
                lines[p].append(((3 if def_re.search(text) else 1) * w, ln, text))
    for p in scores:
        scores[p] *= 1 + 0.5 * (len(matched[p]) - 1)
    order = sorted(scores, key=lambda p: -scores[p])
    return order, scores, matched, lines, tests


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--terms', nargs='*', default=[])
    ap.add_argument('--text', default='')
    ap.add_argument('--top', type=int, default=TOP_FILES)
    args = ap.parse_args()

    terms = extract_terms(args.text) if args.text else {}
    for t in args.terms:
        t = t.strip()
        if len(t) >= 2:
            terms[t] = 4.0
    if not terms:
        print('No terms: pass --terms with the issue literals and/or --text with the issue.')
        return
    order, scores, matched, lines, tests = rank(terms)

    # Summary first (ranked files, tests), details after: truncation only
    # ever cuts details.
    out = ['TERMS: ' + ', '.join(t.lstrip(WORD_PREFIX) for t in sorted(terms, key=lambda t: -terms[t])[:20])]
    if not order:
        out.append('No source file matches. Try other literals (error text, option names).')
    else:
        out.append('FILES:')
    show = lambda ts: ', '.join(sorted(t.lstrip(WORD_PREFIX) for t in ts))[:100]
    for i, p in enumerate(order[:args.top], 1):
        out.append(f'{i}. {p}  (score {scores[p]:.1f}; terms: {show(matched[p])})')
    if len(order) > args.top:
        out.append('also: ' + ', '.join(order[args.top:args.top + 5]))
    top_tests = sorted(tests, key=lambda p: -tests[p])[:3]
    if top_tests:
        out.append('TESTS: ' + ', '.join(top_tests))
    cache: dict[str, list[str]] = {}
    for i, p in enumerate(order[:args.top], 1):
        out.append(f'\n[{i}] {p}')
        seen = set()
        for _, ln, text in sorted(lines[p], key=lambda x: (-x[0], x[1])):
            if ln in seen or len(seen) >= LINES_PER_FILE:
                continue
            seen.add(ln)
            ctx = enclosing(p, ln, cache)
            out.append(f'   L{ln}: {text.strip()[:110]}' + (f'   [in {ctx}]' if ctx and not ctx.startswith(f'L{ln} ') else ''))
    text = '\n'.join(out)
    print(text if len(text) <= MAX_OUT else text[:MAX_OUT] + '\n...(truncated)')


if __name__ == '__main__':
    main()
