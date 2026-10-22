#!/usr/bin/env python3
"""
locate_recall.py -- offline check of the `locate` skill script: does it rank
the files the reference patch edits near the top? No GPU, no harness.

    python eval/locate_recall.py --repos <dir with fastapi/ rich/ requests/ httpx clones>

For every task it checks out the task's base_commit in the matching clone,
runs a locate script there and compares the ranked files with the non-test .py
files of the reference `patch`. The agent's choice of terms is simulated: the 6
most specific identifiers extract_terms() finds in the issue (plain words only
if it names fewer than 2).

    # the submission's skill (terms as positional args)
    python eval/locate_recall.py --repos R
    # the plain-text alternative, fed the issue text itself
    python eval/locate_recall.py --repos R --locate eval/locate_textsearch.py --input text

  hit@k  at least one reference source file is in the top k
  all@k  every reference source file is in the top k

--split dev (default): the dev60 ids from the 1017 run; train: the other
public tasks (tune on these, report on dev); all: every task.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCATE = ROOT / 'submission' / 'skills' / 'locate' / 'scripts' / 'locate.py'
TEXTSEARCH = ROOT / 'eval' / 'locate_textsearch.py'  # alternative locator; also extracts terms from text
TEST_RE = re.compile(r'(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$')
KS = (1, 3, 5, 10)


def gold_files(patch: str) -> list[str]:
    files = set(re.findall(r'^\+\+\+ b/(\S+)', patch, re.M)) | set(re.findall(r'^--- a/(\S+)', patch, re.M))
    return sorted(f for f in files if f.endswith('.py') and not TEST_RE.search(f))


def ranked_files(output: str) -> list[str]:
    """Files in rank order; handles both report formats (file list or RANKED DEFINITIONS)."""
    if 'RANKED DEFINITIONS' in output:
        block = output.split('RANKED DEFINITIONS', 1)[1].split('\n\n', 1)[0]
        return list(dict.fromkeys(re.findall(r'^\s+(\S+?):\d+-\d+\s', block, re.M)))
    files = re.findall(r'^\d+\. (\S+)  \(score', output, re.M)
    also = re.search(r'^also: (.+)$', output, re.M)
    if also:
        files += [f.strip() for f in also.group(1).split(',')]
    return files


def agent_terms(text: str, extract) -> list[str]:
    """Stand-in for the agent picking --terms: the 6 most specific extracted
    identifiers; plain words only when the issue names fewer than 2."""
    terms = extract(text)
    idents = [t for t in sorted(terms, key=lambda t: -terms[t]) if not t.startswith('~')]
    words = [t[1:] for t in sorted(terms, key=lambda t: -terms[t]) if t.startswith('~')]
    return (idents + (words if len(idents) < 2 else []))[:6]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--repos', required=True, type=Path)
    ap.add_argument('--tasks', default=ROOT / 'tasks.jsonl', type=Path)
    ap.add_argument('--dev-meta', default=ROOT / 'exp_res/results_baseline_1017_60dev/run_meta.json', type=Path)
    ap.add_argument('--split', choices=('dev', 'train', 'all'), default='dev')
    ap.add_argument('--out', type=Path, help='write per-task rows (jsonl) here')
    ap.add_argument('--locate', type=Path, default=LOCATE, help='locate.py to evaluate')
    ap.add_argument('--input', choices=('text', 'terms', 'positional'), default='positional',
                    help='text: --text <issue>; terms: --terms <6 extracted>; positional: the 6 terms as plain args')
    ap.add_argument('--extractor', type=Path, default=TEXTSEARCH,
                    help='locate.py whose extract_terms() simulates the agent picking terms')
    args = ap.parse_args()

    tasks = {t['instance_id']: t for t in map(json.loads, args.tasks.read_text().splitlines())}
    dev = json.loads(args.dev_meta.read_text())['dev_ids']
    ids = {'dev': dev, 'train': sorted(set(tasks) - set(dev)), 'all': sorted(tasks)}[args.split]

    args.locate, args.extractor = args.locate.resolve(), args.extractor.resolve()
    import importlib.util
    spec = importlib.util.spec_from_file_location('extractor', args.extractor)
    extractor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extractor)
    rows = []
    for i in ids:
        t = tasks[i]
        repo = args.repos / t['repo'].split('/')[1]
        subprocess.run(['git', '-C', str(repo), 'checkout', '-q', '-f', t['base_commit']], check=True)
        text = t['problem_statement'] + ('\n' + t['hints_text'] if t.get('hints_text') else '')
        terms = agent_terms(text, extractor.extract_terms)
        cmd = {'text': ['--top', '10', '--text', text], 'terms': ['--top', '10', '--terms', *terms],
               'positional': terms}[args.input]
        out = subprocess.run([sys.executable, str(args.locate), *cmd],
                             cwd=repo, env={**os.environ, 'PWD': str(repo)},
                             capture_output=True, text=True).stdout
        gold, ranked = gold_files(t['patch']), ranked_files(out)
        if not gold:
            continue  # reference fix touches no source .py file
        row = {'id': i, 'repo': t['repo'], 'gold': gold, 'ranked': ranked[:10], 'terms': terms,
               'out_chars': len(out)}
        for k in KS:
            top = set(ranked[:k])
            row[f'hit@{k}'] = any(g in top for g in gold)
            row[f'all@{k}'] = all(g in top for g in gold)
        rows.append(row)
        rank = next((n for n, f in enumerate(ranked, 1) if f in gold), None)
        print(f"{i:16} rank={rank or '-':>2}  gold={', '.join(gold)[:70]}", flush=True)

    if args.out:
        args.out.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    n = len(rows)
    print(f'\n{n} tasks with a source .py in the reference patch ({len(ids) - n} skipped)')
    print('  ' + '  '.join(f'hit@{k} {sum(r[f"hit@{k}"] for r in rows) / n:.0%}' for k in KS))
    print('  ' + '  '.join(f'all@{k} {sum(r[f"all@{k}"] for r in rows) / n:.0%}' for k in KS))
    by_repo = defaultdict(list)
    for r in rows:
        by_repo[r['repo']].append(r)
    for repo, rr in sorted(by_repo.items()):
        print(f"  {repo:18} hit@5 {sum(r['hit@5'] for r in rr)}/{len(rr)}  all@5 {sum(r['all@5'] for r in rr)}/{len(rr)}")
    print(f"  output chars: max {max(r['out_chars'] for r in rows)}, mean {sum(r['out_chars'] for r in rows) // n}")


if __name__ == '__main__':
    main()
