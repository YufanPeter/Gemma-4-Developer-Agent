#!/usr/bin/env python3
"""
package_submission.py -- build dist/submission.zip from submission/ and check it
against the competition's packaging rules before you upload it.

    python scripts/package_submission.py
    python scripts/package_submission.py --src submission --out dist/submission.zip

Checks (same as the notebook's packaging cell / docs/HARNESS_README.md §2):
  * agent.yaml at the archive root
  * no symlinks
  * only allowed extensions (.yaml .yml .md .txt .py .json .safetensors)
  * total unpacked size < 3 GiB
OS junk (.DS_Store, __pycache__, *.pyc, editor swap files) is skipped, not
packaged -- .DS_Store alone would get the submission rejected.

Prints a fingerprint of the packaged files. The dev-eval notebook prints the
same fingerprint for the submission it actually ran, so you can confirm Kaggle
picked up the dataset version you just uploaded.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ALLOWED_EXT = {".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"}
MAX_TOTAL_BYTES = 3 * 1024**3
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_DIRS = {"__pycache__", ".ipynb_checkpoints", ".git"}


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Package and validate submission.zip")
    ap.add_argument("--src", default="submission", type=Path)
    ap.add_argument("--out", default=Path("dist/submission.zip"), type=Path)
    args = ap.parse_args()

    files, errors = collect(args.src)
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
    print(f"fingerprint: {submission_fingerprint(args.src, files)}")


if __name__ == "__main__":
    main()
