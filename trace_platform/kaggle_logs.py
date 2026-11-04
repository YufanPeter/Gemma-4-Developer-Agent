"""Follow a running Kaggle kernel's log and feed its live trace into the platform.

    python -m trace_platform.kaggle_logs tianyuxu2/gemma-4-dev-eval             # follow live
    python -m trace_platform.kaggle_logs tianyuxu2/gemma-4-dev-eval --fetch     # + import results when done
    python -m trace_platform.kaggle_logs --file saved_logs.json                 # replay a saved log

The dev-eval notebook's live-trace cell (trace_platform/kaggle_push.py, log channel)
prints each batch of events as one '@@GT@@ <base64 gzip json>' line. This runs
`kaggle kernels logs -f <kernel>`, re-joins stdout into lines, decodes those batches and
POSTs them to the platform's ingest port (default http://127.0.0.1:8791, token
$TRACE_TOKEN). Every item is idempotent, so restarting the follower (which replays the
log from the top) never duplicates anything. Other stdout lines (task progress) are
echoed. When the kernel finishes, --fetch downloads its output and imports the final
traces (reconciliation).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

MARK = "@@GT@@ "


class Feeder:
    def __init__(self, ingest: str, token: str, run_override: str | None, quiet: bool = False):
        self.url = ingest.rstrip("/") + "/ingest"
        self.token = token
        self.run_override = run_override
        self.quiet = quiet
        self.buf = ""
        self.batches = self.items = self.bad = 0
        self.runs: set[str] = set()

    def post(self, body: dict) -> None:
        data = gzip.compress(json.dumps(body).encode())
        req = urllib.request.Request(self.url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Content-Encoding": "gzip"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    r.read()
                return
            except urllib.error.HTTPError as e:
                if e.code in (400, 401):
                    raise SystemExit(f"ingest refused the batch ({e.code}): {e.read()[:200]!r} -- wrong token?")
                time.sleep(1 + attempt)
            except OSError:
                time.sleep(1 + attempt)
        raise SystemExit(f"cannot reach {self.url}; is `python -m trace_platform.server --token ...` running?")

    def line(self, text: str) -> None:
        if text.startswith(MARK):
            try:
                body = json.loads(gzip.decompress(base64.b64decode(text[len(MARK):].strip())))
            except Exception:
                self.bad += 1
                return
            if self.run_override:
                body["run"] = self.run_override
            self.runs.add(body.get("run", "?"))
            self.post(body)
            self.batches += 1
            self.items += len(body.get("items") or [])
        elif text.strip() and not self.quiet:
            print("  | " + text[:300], flush=True)

    def record(self, rec: dict) -> None:
        if rec.get("stream_name") != "stdout":
            return
        self.buf += rec.get("data") or ""
        while "\n" in self.buf:
            ln, self.buf = self.buf.split("\n", 1)
            self.line(ln)

    def flush(self) -> None:
        if self.buf:
            self.line(self.buf)
            self.buf = ""


def parse_log_line(raw: str) -> dict | None:
    """One physical line of `kaggle kernels logs` output -> record (the output is a JSON
    array printed one element per line: '[{...}', ',{...}', ']')."""
    s = raw.strip()
    if not s or s.startswith("Warning:"):
        return None
    s = s.lstrip("[,").rstrip("]").rstrip(",").strip()
    if not s.startswith("{"):
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def feed_raw(feeder: Feeder, raw: str) -> None:
    """`kaggle kernels logs` prints a JSON array of records for a finished run, but with
    -f it streams the session's raw text (stdout and stderr interleaved). Take both."""
    s = raw.strip()
    if s.startswith(("[{", ",{")):
        rec = parse_log_line(raw)
        if rec:
            feeder.record(rec)
            return
    if s in ("[", "]") or s.startswith("Warning: Looks like you're using an outdated"):
        return
    feeder.line(raw.rstrip("\n"))


def kernel_status(kernel: str) -> str:
    out = subprocess.run(["kaggle", "kernels", "status", kernel], capture_output=True, text=True).stdout
    for w in ("RUNNING", "QUEUED", "COMPLETE", "ERROR", "CANCEL"):
        if w in out.upper():
            return w
    return "UNKNOWN"


def follow(kernel: str, feeder: Feeder, poll_s: float) -> None:
    while True:
        st = kernel_status(kernel)
        if st == "QUEUED" or st == "UNKNOWN":
            print(f"[{time.strftime('%H:%M:%S')}] {kernel}: {st.lower()}, waiting...", flush=True)
            time.sleep(poll_s)
            continue
        print(f"[{time.strftime('%H:%M:%S')}] {kernel}: {st.lower()} -- following log", flush=True)
        feeder.buf = ""
        # The CLI is Python: unbuffer it, or a pipe holds its output back in 8 KB blocks.
        proc = subprocess.Popen(["kaggle", "kernels", "logs", "-f", kernel], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        assert proc.stdout is not None
        for raw in proc.stdout:
            feed_raw(feeder, raw)
        proc.wait()
        feeder.flush()
        print(f"[{time.strftime('%H:%M:%S')}] log stream ended: {feeder.batches} batches / {feeder.items} items "
              f"into {sorted(feeder.runs)}" + (f", {feeder.bad} undecodable" if feeder.bad else ""), flush=True)
        if kernel_status(kernel) != "RUNNING":
            return
        time.sleep(3)  # stream dropped while still running: reconnect (replay is idempotent)


def fetch_and_import(kernel: str, run: str | None) -> None:
    from .import_results import DEFAULT_DB, import_dir
    from .store import Store
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["kaggle", "kernels", "output", kernel, "-p", td], check=True)
        zips = list(Path(td).glob("results_*.zip"))
        if not zips:
            print("no results_*.zip in the kernel output")
            return
        for z in zips:
            dest = Path(__file__).resolve().parent.parent / "exp_res" / z.stem
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
            name, n, mism = import_dir(Store(DEFAULT_DB), dest, run)
            print(f"imported {n} final traces into {name} from {dest}")
            for m in mism:
                print(f"  reconcile: {m}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kernel", nargs="?", help="owner/kernel-slug")
    ap.add_argument("--file", type=Path, help="parse a saved `kaggle kernels logs` output instead of following")
    ap.add_argument("--ingest", default="http://127.0.0.1:8791")
    ap.add_argument("--token", default=os.environ.get("TRACE_TOKEN"))
    ap.add_argument("--run", help="store under this run name instead of the notebook's RUN_NAME")
    ap.add_argument("--fetch", action="store_true", help="when the kernel finishes, download its output and import the final traces")
    ap.add_argument("--quiet", action="store_true", help="do not echo the notebook's other stdout")
    ap.add_argument("--poll", type=float, default=30.0)
    a = ap.parse_args()
    if not a.token:
        ap.error("need --token or $TRACE_TOKEN (the token the server was started with)")
    feeder = Feeder(a.ingest, a.token, a.run, a.quiet)
    if a.file:
        for raw in a.file.read_text(errors="replace").splitlines(keepends=True):
            feed_raw(feeder, raw)
        feeder.flush()
        print(f"{feeder.batches} batches / {feeder.items} items into {sorted(feeder.runs)}"
              + (f", {feeder.bad} undecodable" if feeder.bad else ""))
        return
    if not a.kernel:
        ap.error("give a kernel (owner/slug) or --file")
    follow(a.kernel, feeder, a.poll)
    if a.fetch:
        fetch_and_import(a.kernel, a.run)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
