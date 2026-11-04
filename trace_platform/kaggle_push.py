# ── Live trace push (source of truth: trace_platform/kaggle_push.py) ─────────
# Streams every harness trace entry, task start/end and GPU / vLLM metrics from the
# Kaggle kernel to the trace platform while the dev set runs, over one of two channels:
#   * log (default, works with Internet OFF): batches are printed as one line each,
#     '@@GT@@ <base64 gzip json>', and `python -m trace_platform.kaggle_logs <kernel>`
#     on your machine follows `kaggle kernels logs -f` and feeds them to the platform.
#     (Kaggle refuses Internet for this competition's L4 kernels, so this is the one
#     that works for dev-eval runs.)
#   * http: POST to TRACE_SINK_URL/ingest (needs Internet ON; for kernels that allow it).
# Copied into the dev-eval notebook by `python -m trace_platform.kaggle_push --sync-notebook`;
# edit this file, not the notebook cell.
#
# Never slows or breaks the eval: the hook only appends to a bounded queue (a full
# queue drops and counts), one daemon thread sends batches, another samples metrics
# every few seconds. Any error inside the push is swallowed.
import base64 as _lp_b64
import gzip as _lp_gzip
import sys as _lp_sys
import json as _lp_json
import os as _lp_os
import queue as _lp_queue
import subprocess as _lp_subprocess
import threading as _lp_threading
import time as _lp_time
import urllib.request as _lp_request

_LP_MARK = "@@GT@@ "


def _lp_clip(v, n=20_000):
    if isinstance(v, str) and len(v) > n:
        return v[:n] + f"\n... [{len(v) - n:,} chars clipped by live push]"
    if isinstance(v, dict):
        return {k: _lp_clip(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [_lp_clip(x, n) for x in v]
    return v


def _lp_prom(text):
    """Sum each vLLM Prometheus metric over its label sets."""
    out = {}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        try:
            name_labels, val = line.rsplit(" ", 1)
            name = name_labels.split("{", 1)[0]
            out[name] = out.get(name, 0.0) + float(val)
        except ValueError:
            continue
    return out


class LivePush:
    def __init__(self, url, token, run, flush_s=2.0, metrics_s=5.0, max_queue=50_000,
                 vllm_metrics_url="http://127.0.0.1:8000/metrics", clip=20_000):
        # url=None -> log channel (print marked lines); otherwise POST to url/ingest.
        self.url = None if not url else url.rstrip("/") + ("" if url.rstrip("/").endswith("/ingest") else "/ingest")
        self.clip = clip
        self.token, self.run = token, run
        self.flush_s, self.metrics_s = flush_s, metrics_s
        self.vllm_metrics_url = vllm_metrics_url
        self.q = _lp_queue.Queue(maxsize=max_queue)
        self.task = None
        self.seq = 0
        self.dropped = 0
        self.sent = 0
        self.failures = 0
        self.last_error = ""
        self._stop = _lp_threading.Event()
        self._patched = False

    # ── producers (called from the eval loop / harness thread) ──
    def _put(self, item):
        try:
            self.q.put_nowait(item)
        except _lp_queue.Full:
            self.dropped += 1

    def on_entry(self, entry):
        if self.task is None:
            return
        try:
            d = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
            self._put({"k": "e", "task": self.task, "seq": self.seq, "e": _lp_clip(d, self.clip)})
            self.seq += 1
        except Exception as exc:  # never let tracing break the harness
            self.last_error = f"entry: {exc!r}"

    def run_started(self, meta, expected_tasks):
        meta = {k: v for k, v in (meta or {}).items() if k != "submission_files"}
        self._put({"k": "run", "meta": _lp_clip(meta), "expected_tasks": expected_tasks, "status": "running"})

    def run_finished(self, **extra):
        self._put({"k": "run", "meta": extra, "status": "done"})
        self.flush(timeout=30)

    def task_start(self, task_id):
        self.task, self.seq = task_id, 0
        self._put({"k": "task_start", "task": task_id, "ts": _lp_time.time()})

    def task_end(self, task_id, row=None, patch="", test_log=""):
        self._put({"k": "task_end", "task": task_id, "ts": _lp_time.time(), "result": _lp_clip(row or {}),
                   "patch": _lp_clip(patch or "", 60_000), "test_log": (str(test_log or ""))[-40_000:]})
        self.task = None

    # ── hook ──
    def patch_harness(self):
        from adk_eval_core.tracing import trace as _tr  # the class swegemma's runner records into
        orig = _tr.SessionTrace._add
        if getattr(orig, "_live_push", False):
            orig = orig._orig
        push = self

        def _add(trace_self, entry):
            orig(trace_self, entry)
            push.on_entry(entry)
        _add._live_push, _add._orig = True, orig
        _tr.SessionTrace._add = _add
        self._patched = True

    # ── background threads ──
    def start(self):
        _lp_threading.Thread(target=self._sender, name="live-push-sender", daemon=True).start()
        _lp_threading.Thread(target=self._sampler, name="live-push-metrics", daemon=True).start()
        return self

    def _post(self, items):
        body = _lp_gzip.compress(_lp_json.dumps({"run": self.run, "items": items}, default=str).encode())
        if self.url is None:
            # One line per batch; the stream may split long lines, the reader re-joins on newline.
            _lp_sys.stdout.write(_LP_MARK + _lp_b64.b64encode(body).decode() + "\n")
            _lp_sys.stdout.flush()
            return
        req = _lp_request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Content-Encoding": "gzip", "User-Agent": "gemma-trace-push/1"})
        with _lp_request.urlopen(req, timeout=15) as r:
            r.read()

    def _drain(self, n=300):
        items = []
        while len(items) < n:
            try:
                items.append(self.q.get_nowait())
            except _lp_queue.Empty:
                break
        return items

    def _sender(self):
        batch, backoff = [], self.flush_s
        while not self._stop.is_set():
            self._stop.wait(backoff)
            batch = batch or self._drain()
            while batch:
                try:
                    self._post(batch)
                    self.sent += len(batch)
                    backoff = self.flush_s
                    batch = self._drain()
                except Exception as exc:
                    self.failures += 1
                    self.last_error = f"post: {exc!r}"[:300]
                    backoff = min(60.0, backoff * 2)
                    break  # keep `batch`, retry after backoff

    def flush(self, timeout=10.0):
        end = _lp_time.time() + timeout
        while _lp_time.time() < end:
            items = self._drain()
            if not items:
                return True
            try:
                self._post(items)
                self.sent += len(items)
            except Exception as exc:
                self.last_error = f"flush: {exc!r}"[:300]
                return False
        return False

    def sample(self):
        d = {"push": {"queued": self.q.qsize(), "dropped": self.dropped, "sent": self.sent,
                      "failures": self.failures}}
        try:
            out = _lp_subprocess.run(
                ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
            gpus = []
            for line in out.strip().splitlines():
                f = [x.strip() for x in line.split(",")]
                num = lambda s: float(s) if s not in ("", "[N/A]", "N/A") else None  # noqa: E731
                gpus.append({"i": int(f[0]), "util": num(f[1]), "mem_used": num(f[2]), "mem_total": num(f[3]),
                             "power": num(f[4]), "temp": num(f[5])})
            d["gpus"] = gpus
        except Exception as exc:
            d["gpu_error"] = repr(exc)[:200]
        try:
            with _lp_request.urlopen(self.vllm_metrics_url, timeout=3) as r:
                m = _lp_prom(r.read().decode("utf-8", "replace"))
            pick = lambda *ks: next((m[k] for k in ks if k in m), None)  # noqa: E731
            d["vllm"] = {
                "kv_cache": pick("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"),
                "running": pick("vllm:num_requests_running"),
                "waiting": pick("vllm:num_requests_waiting"),
                "prompt_tokens_total": pick("vllm:prompt_tokens_total"),
                "generation_tokens_total": pick("vllm:generation_tokens_total"),
                "preemptions_total": pick("vllm:num_preemptions_total", "vllm:num_preemptions"),
                "prefix_cache_hits": pick("vllm:prefix_cache_hits_total", "vllm:gpu_prefix_cache_hits_total"),
                "prefix_cache_queries": pick("vllm:prefix_cache_queries_total", "vllm:gpu_prefix_cache_queries_total"),
            }
        except Exception as exc:
            d["vllm_error"] = repr(exc)[:200]
        d["task"] = self.task
        return d

    def _sampler(self):
        while not self._stop.is_set():
            try:
                self._put({"k": "m", "ts": _lp_time.time(), "d": self.sample()})
            except Exception as exc:
                self.last_error = f"sample: {exc!r}"[:300]
            self._stop.wait(self.metrics_s)

    def status(self):
        return (f"live push -> {self.url or 'log'}: sent {self.sent}, queued {self.q.qsize()}, dropped {self.dropped}, "
                f"failures {self.failures}{', last error ' + self.last_error if self.last_error else ''}")


class _NoPush:
    """Stand-in when live push is off: every call is a no-op."""
    def __getattr__(self, name):
        return lambda *a, **k: None

    def status(self):
        return "live push: off"


def start_live_push(run, url=None, token=None, log=True):
    """Pick the channel and start: http if a sink URL + token are given (args, Kaggle
    Secrets TRACE_SINK_URL / TRACE_SINK_TOKEN, or env) and it answers; else the log
    channel if `log`; else a no-op. Patches the harness and starts the threads."""
    def secret(name):
        try:
            from kaggle_secrets import UserSecretsClient
            return UserSecretsClient().get_secret(name)
        except Exception:
            return None
    url = url or secret("TRACE_SINK_URL") or _lp_os.environ.get("TRACE_SINK_URL")
    token = token or secret("TRACE_SINK_TOKEN") or _lp_os.environ.get("TRACE_SINK_TOKEN")
    push = None
    if url and token:
        push = LivePush(url, token, run)
        try:
            push._post([{"k": "run", "meta": {"hello": True}}])
        except Exception as exc:
            print(f"live push: sink {url} not reachable ({exc!r}); Internet is off for this kernel?")
            push = None
    if push is None and log:
        # Log channel: smaller fields (the final trace import fills in full text) and a
        # slower metrics cadence, to keep the notebook log a few MB for a 60-task run.
        push = LivePush(None, None, run, flush_s=3.0, metrics_s=10.0, clip=4_000)
    if push is None:
        print("live push: off")
        return _NoPush()
    try:
        push.patch_harness()
    except Exception as exc:
        print(f"live push: could not patch SessionTrace ({exc!r}); sending task/metrics only")
    print(f"live push: on -> {push.url or 'notebook log (follow with: python -m trace_platform.kaggle_logs <owner/kernel>)'} (run {run})")
    return push.start()
# ── end live trace push ─────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    import pathlib
    import sys

    ap = argparse.ArgumentParser(description="Copy this file into the dev-eval notebook's live-push cell.")
    ap.add_argument("--sync-notebook", action="store_true")
    ap.add_argument("--notebook", default=str(pathlib.Path(__file__).resolve().parent.parent
                                              / "notebooks" / "getting-started-gemma-4-developer-agent.ipynb"))
    a = ap.parse_args()
    if not a.sync_notebook:
        ap.print_help()
        sys.exit(0)
    src = pathlib.Path(__file__).read_text()
    cell_src = src[: src.index("# ── end live trace push") + len("# ── end live trace push ─────────────────────────────────────────────────────")]
    cell_src += "\n\nLIVE = start_live_push(RUN_NAME, TRACE_SINK_URL, TRACE_SINK_TOKEN, log=TRACE_LOG)\n"
    nbp = pathlib.Path(a.notebook)
    nb = _lp_json.loads(nbp.read_text())
    idx = next((i for i, c in enumerate(nb["cells"]) if c["cell_type"] == "code"
                and "".join(c["source"]).startswith("# ── Live trace push")), None)
    lines = cell_src.splitlines(keepends=True)
    if idx is None:
        raise SystemExit("no live-push cell found (a code cell starting with '# ── Live trace push')")
    if nb["cells"][idx]["source"] == lines:
        print("notebook cell already in sync")
    else:
        nb["cells"][idx]["source"] = lines
        nbp.write_text(_lp_json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
        print(f"updated cell {idx} in {nbp}")
