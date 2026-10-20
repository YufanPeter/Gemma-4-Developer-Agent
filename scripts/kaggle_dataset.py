"""
kaggle_dataset.py -- push local files as a new version of an existing Kaggle
Dataset (shared by package_submission.py and build_extra_wheels.py).

Needs the Kaggle CLI (`pip install kaggle`) logged in as the dataset owner.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def upload_version(files: list[Path], ref: str, message: str) -> None:
    """Upload `files` as a new version of dataset `ref` ("owner/slug").

    The new version contains exactly these files; older versions stay on Kaggle.
    """
    if shutil.which("kaggle") is None:
        raise SystemExit("kaggle CLI not found: pip install kaggle, then log in")
    with tempfile.TemporaryDirectory(prefix="kaggle_upload_") as tmp:
        stage = Path(tmp)
        for f in files:
            shutil.copy2(f, stage / f.name)
        (stage / "dataset-metadata.json").write_text(
            json.dumps({"id": ref, "title": ref.split("/", 1)[1], "licenses": [{"name": "CC0-1.0"}]})
        )
        print(f"uploading {len(files)} file(s) to {ref} ...")
        subprocess.run(["kaggle", "datasets", "version", "-p", str(stage), "-m", message], check=True)
    print(f"done: https://www.kaggle.com/datasets/{ref}")
