"""Replay a finished run's traces to /ingest as if it were live (for testing the
live path without a GPU), with synthetic GPU / vLLM metrics.

    python -m trace_platform.replay exp_res/results_dev60_e5_pipeline --run replay_test \
        --url http://127.0.0.1:8791 --token local-dev-token --speed 20 --tasks 3

It sends exactly what trace_platform/kaggle_push.py sends: a run item, then per task
task_start, each entry (paced by its real timestamps / speed), task_end, plus a metrics
sample every few (scaled) seconds. Timestamps are shifted to "now" so the run looks live.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import time
import urllib.request
from pathlib import Path

from . import model


def post(url: str, token: str, run: str, items: list[dict]) -> None:
    body = gzip.compress(json.dumps({"run": run, "items": items}, default=str).encode())
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Content-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def fake_metrics(ts: float, busy: bool, gen_total: float, prompt_total: float) -> dict:
    u = (85 + 10 * math.sin(ts / 7)) if busy else 3.0
    return {"gpus": [{"i": i, "util": round(max(0, u + random.uniform(-6, 6)), 1), "mem_used": 20500 + i * 40 + random.uniform(-50, 50),
                      "mem_total": 23034, "power": round(55 + u * 0.6 + random.uniform(-4, 4), 1), "temp": 60 + int(u / 10)} for i in range(4)],
            "vllm": {"kv_cache": round((0.25 + 0.2 * math.sin(ts / 30)) if busy else 0.02, 3), "running": 1 if busy else 0, "waiting": 0,
                     "prompt_tokens_total": prompt_total, "generation_tokens_total": gen_total}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir", type=Path)
    ap.add_argument("--run", default="replay_test")
    ap.add_argument("--url", default="http://127.0.0.1:8791")
    ap.add_argument("--token", required=True)
    ap.add_argument("--speed", type=float, default=20.0, help="time compression factor")
    ap.add_argument("--tasks", type=int, default=3)
    ap.add_argument("--dup", action="store_true", help="send every batch twice (dedupe check)")
    a = ap.parse_args()
    url = a.url.rstrip("/") + "/ingest"
    traces = sorted((a.results_dir / "traces").glob("trace_*.json"))[: a.tasks]
    rows = {}
    if (a.results_dir / "dev_results.jsonl").exists():
        for line in (a.results_dir / "dev_results.jsonl").read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r
    send = (lambda items: [post(url, a.token, a.run, items) for _ in range(2 if a.dup else 1)])
    send([{"k": "run", "meta": {"model": "replay", "replay_of": str(a.results_dir)}, "expected_tasks": len(traces), "status": "running"}])
    gen_total = prompt_total = 0.0
    last_metric = 0.0
    for tp in traces:
        tid = tp.stem[len("trace_"):]
        entries = model.atif_to_entries(json.loads(tp.read_text()))
        entries = [e for e in entries if e.get("timestamp")]
        t0_trace = entries[0]["timestamp"]
        t0 = time.time()
        send([{"k": "task_start", "task": tid, "ts": t0}])
        print(f"{tid}: {len(entries)} entries", flush=True)
        batch: list[dict] = []
        for seq, e in enumerate(entries):
            due = t0 + (e["timestamp"] - t0_trace) / a.speed
            while time.time() < due:
                time.sleep(min(0.25, max(0.0, due - time.time())))
                now = time.time()
                if now - last_metric > 5 / a.speed * 5:
                    gen_total += 25 * (now - last_metric) * a.speed
                    prompt_total += 400 * (now - last_metric) * a.speed
                    batch.append({"k": "m", "ts": now, "d": fake_metrics(now, True, gen_total, prompt_total)})
                    last_metric = now
                if batch:
                    send(batch)
                    batch = []
            shifted = {**e, "timestamp": t0 + (e["timestamp"] - t0_trace)}
            batch.append({"k": "e", "task": tid, "seq": seq, "e": shifted})
        send(batch + [{"k": "task_end", "task": tid, "ts": time.time(), "result": rows.get(tid, {}), "patch": "", "test_log": ""}])
    send([{"k": "run", "meta": {}, "status": "done"}])
    print("replay done")


if __name__ == "__main__":
    main()
