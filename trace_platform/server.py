"""Gemma-4 trace platform server (stdlib only).

    python -m trace_platform.server                     # UI on http://127.0.0.1:8790
    python -m trace_platform.server --token <secret>    # also accept live pushes on :8791

Two listeners, both bound to 127.0.0.1:
  * UI port (8790): the page, the read API, the live stream. Never exposed.
  * ingest port (8791): only ``POST /ingest`` with ``Authorization: Bearer <token>``.
    This is the one port the cloudflared tunnel forwards, so nothing on the public
    URL can read traces or reach the page.

Read API (UI port):
  GET /api/runs                       runs with progress aggregates
  GET /api/run?run=R                  run + task rows (stats per task)
  GET /api/task?run=R&task=T          spans, context series, steps, patch, test log
  GET /api/metrics?run=R&since=TS     GPU / vLLM samples
  GET /api/stream                     Server-Sent Events: {"type": "entries"|"task"|"run"|"metrics", ...}
  GET /api/export?run=R               the run as a shareable .trace.jsonl.gz (see trace_platform/cli.py)

Ingest body (JSON, optionally gzip): {"run": R, "items": [...]} with items
  {"k": "run", "meta": {...}, "expected_tasks": N, "status": "running"|"done"}
  {"k": "task_start", "task": T, "ts": TS}
  {"k": "e", "task": T, "seq": N, "e": <TraceEntry.to_dict()>}
  {"k": "task_end", "task": T, "ts": TS, "result": {...}, "patch": str, "test_log": str}
  {"k": "m", "ts": TS, "d": {"gpus": [...], "vllm": {...}}}
Every item is idempotent (keyed by run/task/seq or run/ts), so senders may retry.
"""
from __future__ import annotations

import argparse
import gzip
import hmac
import json
import mimetypes
import queue
import secrets
import threading
import time
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import model
from .import_results import DEFAULT_DB, make_room
from .store import Store

STATIC = Path(__file__).resolve().parent / "static"
RAW_DIR = DEFAULT_DB.parent / "raw"
MAX_BODY = 32 * 1024 * 1024


class Hub:
    """Fan-out of change notifications to SSE clients."""

    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, msg: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


class App:
    def __init__(self, store: Store, token: str | None):
        self.store = store
        self.token = token
        self.hub = Hub()
        self.ingest_stats = defaultdict(int)
        self.last_ingest: dict[str, float] = {}

    def ingest(self, body: dict) -> dict:
        run = str(body.get("run") or "").strip()
        if not run:
            raise ValueError("missing run")
        items = body.get("items") or []
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        with (RAW_DIR / f"{run.replace('/', '_')}.jsonl").open("a") as f:
            f.write(json.dumps({"recv": time.time(), "items": items}, ensure_ascii=False, default=str) + "\n")
        if self.store.run(run) is None:
            self.store.upsert_run(run, "live", {})
            self.hub.publish({"type": "run", "run": run})
        by_task: dict[str, list[tuple[int, dict]]] = defaultdict(list)
        metrics: list[tuple[float, dict]] = []
        changed_tasks: set[str] = set()
        for it in items:
            k = it.get("k")
            if k == "e":
                by_task[str(it["task"])].append((int(it["seq"]), it["e"]))
            elif k == "m":
                metrics.append((float(it["ts"]), it.get("d") or {}))
            elif k == "task_start":
                self.store.upsert_task(run, str(it["task"]), status="running", source="live", started=it.get("ts"))
                changed_tasks.add(str(it["task"]))
            elif k == "task_end":
                self.store.upsert_task(run, str(it["task"]), status="done", ended=it.get("ts"),
                                       result=it.get("result"), patch=it.get("patch") or "",
                                       test_log=it.get("test_log") or "")
                changed_tasks.add(str(it["task"]))
            elif k == "run":
                if it.get("status") == "running" and (it.get("meta") or {}).get("started_at_utc"):
                    # A new eval run reusing a stored name (RUN_NAME defaults to dev60_baseline
                    # on every branch): move the old one aside instead of mixing them.
                    if make_room(self.store, run, it["meta"]) is None and self.store.run(run) is None:
                        self.store.upsert_run(run, "live", {})
                self.store.upsert_run(run, "live", it.get("meta"), status=it.get("status"),
                                      expected_tasks=it.get("expected_tasks"))
                self.hub.publish({"type": "run", "run": run})
        new = 0
        for task, pairs in by_task.items():
            n = self.store.add_entries(run, task, pairs)
            new += n
            if n:
                self.hub.publish({"type": "entries", "run": run, "task": task, "n": n})
        for task in changed_tasks:
            self.hub.publish({"type": "task", "run": run, "task": task})
        nm = self.store.add_metrics(run, metrics) if metrics else 0
        if nm:
            self.hub.publish({"type": "metrics", "run": run, "ts": max(t for t, _ in metrics)})
        self.ingest_stats[run] += len(items)
        self.last_ingest[run] = time.time()
        return {"ok": True, "items": len(items), "new_entries": new, "new_metrics": nm}

    def task_detail(self, run: str, task: str) -> dict | None:
        t = self.store.task(run, task)
        if t is None:
            return None
        built = model.build_task(self.store.entries(run, task))
        lim = (self.store.run(run) or {}).get("meta", {}).get("limits") or {}
        return {**t, **built, "limits": {"compaction": lim.get("compaction_token_threshold") or model.COMPACTION_THRESHOLD,
                                         "max_model_len": lim.get("max_model_len") or model.MAX_MODEL_LEN},
                "ctx_cats": model.CTX_CATS}


def make_handler(app: App, role: str):
    class H(BaseHTTPRequestHandler):
        server_version = "gemma-trace/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet: only errors
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode())

        # ── ingest port ──
        def do_POST(self):
            if role != "ingest" or urlparse(self.path).path != "/ingest":
                return self._json({"error": "not found"}, 404)
            auth = self.headers.get("Authorization", "")
            if not app.token or not hmac.compare_digest(auth, f"Bearer {app.token}"):
                return self._json({"error": "unauthorized"}, 401)
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._json({"error": "bad length"}, 413)
            raw = self.rfile.read(n)
            if self.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            try:
                res = app.ingest(json.loads(raw))
            except (ValueError, KeyError, TypeError) as exc:
                return self._json({"error": str(exc)}, 400)
            self._json(res)

        def do_GET(self):
            u = urlparse(self.path)
            if role == "ingest":
                return self._json({"ok": True, "service": "gemma-trace ingest"} if u.path == "/health" else {"error": "not found"},
                                  200 if u.path == "/health" else 404)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            p = u.path
            if p == "/api/runs":
                runs = app.store.runs()
                for r in runs:
                    r["last_ingest"] = app.last_ingest.get(r["run"])
                return self._json({"runs": runs, "now": time.time()})
            if p == "/api/run":
                r = app.store.run(q.get("run", ""))
                if r is None:
                    return self._json({"error": "no such run"}, 404)
                r["last_ingest"] = app.last_ingest.get(r["run"])
                return self._json({"run": r, "tasks": app.store.task_rows(r["run"]), "now": time.time()})
            if p == "/api/task":
                d = app.task_detail(q.get("run", ""), q.get("task", ""))
                return self._json(d) if d else self._json({"error": "no such task"}, 404)
            if p == "/api/metrics":
                return self._json({"metrics": app.store.metrics(q.get("run", ""), float(q.get("since") or 0))})
            if p == "/api/stream":
                return self._stream()
            if p == "/api/export":
                import tempfile
                from .cli import export_run
                run = q.get("run", "")
                if app.store.run(run) is None:
                    return self._json({"error": "no such run"}, 404)
                with tempfile.TemporaryDirectory() as td:
                    out = Path(td) / f"{run}.trace.jsonl.gz"
                    export_run(app.store, run, out)
                    return self._send(200, out.read_bytes(), "application/gzip",
                                      {"Content-Disposition": f'attachment; filename="{out.name}"'})
            if p in ("/", "/index.html"):
                return self._file(STATIC / "index.html")
            if p.startswith("/static/"):
                f = (STATIC / p[len("/static/"):]).resolve()
                if STATIC in f.parents and f.is_file():
                    return self._file(f, cache=True)
            self._json({"error": "not found"}, 404)

        def _file(self, f: Path, cache: bool = False):
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read_bytes(), ctype, {"Cache-Control": "public, max-age=86400"} if cache else None)

        def _stream(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = app.hub.subscribe()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                        data = f"data: {json.dumps(msg)}\n\n"
                    except queue.Empty:
                        data = ": ping\n\n"
                    self.wfile.write(data.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                app.hub.unsubscribe(q)
                self.close_connection = True

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8790, help="UI / read API port")
    ap.add_argument("--ingest-port", type=int, default=8791, help="live push port (the only one to tunnel)")
    ap.add_argument("--token", help="bearer token for /ingest; default $TRACE_TOKEN; ingest is off without one")
    ap.add_argument("--gen-token", action="store_true", help="generate a random token and print it")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    import os
    token = a.token or os.environ.get("TRACE_TOKEN") or (secrets.token_urlsafe(24) if a.gen_token else None)
    app = App(Store(a.db), token)
    ui = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app, "ui"))
    ui.daemon_threads = True
    print(f"trace platform UI: http://127.0.0.1:{a.port}/   (db {a.db})", flush=True)
    if token:
        ing = ThreadingHTTPServer(("127.0.0.1", a.ingest_port), make_handler(app, "ingest"))
        ing.daemon_threads = True
        threading.Thread(target=ing.serve_forever, daemon=True).start()
        print(f"ingest: http://127.0.0.1:{a.ingest_port}/ingest  token={token if a.gen_token else '(from args/env)'}", flush=True)
    else:
        print("ingest: off (no --token / $TRACE_TOKEN)", flush=True)
    try:
        ui.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
