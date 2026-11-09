#!/usr/bin/env python3
"""
package_submission.py -- build dist/submission.zip from submission/ and check it
against the competition's packaging rules before you upload it.

    python scripts/package_submission.py
    python scripts/package_submission.py --src submission --out dist/submission.zip
    python scripts/package_submission.py --upload -m "prompt v2"   # + new dataset version

Checks (same as the notebook's packaging cell / docs/HARNESS_README.md §2):
  * agent.yaml at the archive root
  * no symlinks
  * only allowed extensions (.yaml .yml .md .txt .py .json .safetensors)
  * total unpacked size < 3 GiB
  * time budget: max_time_minutes x 120 tasks <= 10.5 h, and the minutes stated in
    prompts/*.md match it (12 h overruns error the whole submission)
OS junk (.DS_Store, __pycache__, *.pyc, editor swap files) is skipped, not
packaged -- .DS_Store alone would get the submission rejected.

Prints a fingerprint of the packaged files. The dev-eval notebook prints the
same fingerprint for the submission it actually ran, so you can confirm Kaggle
picked up the dataset version you just uploaded.

--upload pushes the zip as a new version of the submission Kaggle Dataset
(default tianyuxu2/gemma-4-agent-submission); needs the Kaggle CLI logged in.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

from kaggle_dataset import upload_version

ALLOWED_EXT = {".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"}
MAX_TOTAL_BYTES = 3 * 1024**3
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_DIRS = {"__pycache__", ".ipynb_checkpoints", ".git"}
DATASET = "tianyuxu2/gemma-4-agent-submission"
N_HIDDEN_TASKS = 120
MAX_WORST_CASE_HOURS = 10.5   # of the 12 h limit; the rest is vLLM startup, sandbox setup, overshoot


def submission_fingerprint(root: Path, files: list[Path]) -> str:
    """sha256 over (relative path, content) of every file, sorted. Must match
    the notebook's `submission_fingerprint`."""
    h = hashlib.sha256()
    for p in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


def collect(src: Path) -> tuple[list[Path], list[str]]:
    files, errors = [], []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_NAMES or p.name.endswith(("~", ".swp", ".pyc")):
            continue
        if p.is_symlink():
            errors.append(f"symlink not allowed: {rel}")
            continue
        if p.is_dir():
            continue
        if p.suffix.lower() not in ALLOWED_EXT:
            errors.append(f"disallowed extension: {rel}")
            continue
        files.append(p)
    if not (src / "agent.yaml").is_file():
        errors.append("agent.yaml missing at submission root")
    total = sum(p.stat().st_size for p in files)
    if total >= MAX_TOTAL_BYTES:
        errors.append(f"total size {total} bytes exceeds 3 GiB")
    return files, errors


def check_time_budget(src: Path) -> list[str]:
    """Worst case: every hidden task uses its whole budget. Must match the training notebook."""
    cfg = next((c for n in ("eval_config.yaml", "eval_config.yml") if (c := src / n).is_file()), None)
    m = re.search(r"^\s*max_time_minutes\s*:\s*([0-9.]+)", cfg.read_text(encoding="utf-8"), re.M) if cfg else None
    if not m:
        return ["eval_config.yaml has no max_time_minutes: the harness default is 60 min per task"]
    minutes, problems = float(m.group(1)), []
    hours = minutes * N_HIDDEN_TASKS / 60
    if hours > MAX_WORST_CASE_HOURS:
        problems.append(f"max_time_minutes {minutes:g} x {N_HIDDEN_TASKS} tasks = {hours:g} h, over the {MAX_WORST_CASE_HOURS} h limit")
    for p in sorted((src / "prompts").glob("*.md")):
        for said in re.findall(r"(\d+(?:\.\d+)?)\s+minutes", p.read_text(encoding="utf-8")):
            if float(said) != minutes:
                problems.append(f"{p.relative_to(src)} says {said} minutes, eval_config says {minutes:g}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Package and validate submission.zip")
    ap.add_argument("--src", default="submission", type=Path)
    ap.add_argument("--out", default=Path("dist/submission.zip"), type=Path)
    ap.add_argument("--upload", action="store_true", help=f"push the zip as a new version of {DATASET}")
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("-m", "--message", default="update submission")
    args = ap.parse_args()

    files, errors = collect(args.src)
    errors += check_time_budget(args.src)
    if errors:
        for e in errors:
            print("ERROR:", e, file=sys.stderr)
        raise SystemExit(1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(args.src).as_posix())

    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.1f} KiB, {len(files)} files)")
    for p in files:
        print("  ", p.relative_to(args.src).as_posix())
    fingerprint = submission_fingerprint(args.src, files)
    print(f"fingerprint: {fingerprint}")

    if args.upload:
        upload_version([args.out], args.dataset, f"{args.message} [{fingerprint}]")


if __name__ == "__main__":
    main()
