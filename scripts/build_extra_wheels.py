#!/usr/bin/env python3
"""
build_extra_wheels.py -- download the test dependencies the Kaggle image lacks
as offline wheels, and optionally push them to the Kaggle Dataset the dev-eval
notebook installs from (cell 2c, FIX_TEST_ENV).

    python scripts/build_extra_wheels.py                       # -> dist/extra_wheels/
    python scripts/build_extra_wheels.py --upload -m "add foo"  # + new dataset version

Only needed when the package list changes: the notebook just attaches the
existing dataset. Wheels are fetched for the Kaggle runtime (Linux x86_64,
CPython 3.12), not for this machine, together with all their dependencies; the
notebook's `pip install --no-index` then keeps whatever the image already has.

If you add a package here, add it to the `pip install` line in notebook cell 2c
too (and to its self-check import if tests import it directly).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from kaggle_dataset import upload_version

# Versions that passed the gold-patch calibration (114/129 reference patches).
PACKAGES = [
    "inline-snapshot==0.36.1",  # fastapi tests
    "dirty-equals==0.11",       # fastapi tests
    "pytest-httpbin==2.1.0",    # psf/requests tests (pulls httpbin, flask, ...)
    "trustme==1.2.1",           # psf/requests TLS tests
]
DATASET = "tianyuxu2/gemma-4-extra-test-wheels"

# Kaggle runtime: CPython 3.12 on glibc Linux. pip does not expand manylinux
# tags for --platform, so list the ones the wheels on PyPI actually use.
PLATFORMS = ["manylinux2014_x86_64", "manylinux_2_24_x86_64", "manylinux_2_28_x86_64", "manylinux_2_34_x86_64"]
PY_VERSION = "3.12"


def main() -> None:
    ap = argparse.ArgumentParser(description="Build (and optionally upload) the extra test wheels")
    ap.add_argument("--out", default=Path("dist/extra_wheels"), type=Path)
    ap.add_argument("--upload", action="store_true", help=f"push the wheels as a new version of {DATASET}")
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("-m", "--message", default="rebuild extra test wheels")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.whl"):
        old.unlink()
    cmd = [sys.executable, "-m", "pip", "download", "-q", "-d", str(args.out),
           "--only-binary=:all:", "--implementation", "cp", "--python-version", PY_VERSION]
    for plat in PLATFORMS:
        cmd += ["--platform", plat]
    subprocess.run(cmd + PACKAGES, check=True)

    wheels = sorted(args.out.glob("*.whl"))
    total = sum(w.stat().st_size for w in wheels)
    print(f"{len(wheels)} wheels in {args.out} ({total / 1024**2:.1f} MiB)")
    for w in wheels:
        print("  ", w.name)

    if args.upload:
        upload_version(wheels, args.dataset, args.message)


if __name__ == "__main__":
    main()
