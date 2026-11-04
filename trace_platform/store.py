"""SQLite store for the trace platform: runs, tasks, raw entries, GPU/vLLM metrics.

Raw entries are kept as sent (``model.build_task`` derives spans when a task is read),
so a better span model never needs a re-import. Every live write is keyed so a retried
POST is harmless: entries by (run, task, seq), metrics by (run, ts).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import model, outcome

SCHEMA = """
create table if not exists runs (
  run text primary key, source text, meta text, created real, updated real,
  status text default 'running', expected_tasks integer
);
create table if not exists tasks (
  run text, task text, status text default 'running', source text,
  started real, ended real, result text, patch text, test_log text,
  n_entries integer default 0, stats text, stats_n integer default -1,
  primary key (run, task)
);
create table if not exists entries (
  run text, task text, seq integer, ts real, entry text,
  primary key (run, task, seq)
);
create table if not exists metrics (
  run text, ts real, data text, primary key (run, ts)
);
create index if not exists ix_metrics_run_ts on metrics(run, ts);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._local = threading.local()
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            if "outcome" not in {r["name"] for r in c.execute("pragma table_info(tasks)")}:
                c.execute("alter table tasks add column outcome text")
        if self._conn().execute("pragma user_version").fetchone()[0] != outcome.VERSION:
            self.relabel()
        else:
            # Rows written by an older server process (no outcome column in its code) or
            # whose status moved on without a relabel.
            self.relabel(where="outcome is null or (outcome='RUNNING') != (status='running')")

    def relabel(self, where: str | None = None) -> None:
        """Recompute task outcomes: all of them (on open when outcome.VERSION changed) or
        the rows matching `where`."""
        with self._lock, self._conn() as c:
            q = "select run, task, status, result, patch, test_log from tasks" + (f" where {where}" if where else "")
            rows = c.execute(q).fetchall()
            c.executemany("update tasks set outcome=? where run=? and task=?",
                          [(_outcome(r), r["run"], r["task"]) for r in rows])
            if where is None:
                c.execute(f"pragma user_version={int(outcome.VERSION)}")

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("pragma journal_mode=wal")
            c.execute("pragma synchronous=normal")
            self._local.c = c
        return c

    # ── writes ───────────────────────────────────────────────────────────────
    def upsert_run(self, run: str, source: str, meta: dict | None = None, status: str | None = None,
                   expected_tasks: int | None = None) -> None:
        now = time.time()
        with self._lock, self._conn() as c:
            row = c.execute("select meta from runs where run=?", (run,)).fetchone()
            if row is None:
                c.execute("insert into runs(run, source, meta, created, updated, status, expected_tasks) values (?,?,?,?,?,?,?)",
                          (run, source, json.dumps(meta or {}), now, now, status or "running", expected_tasks))
            else:
                merged = {**json.loads(row["meta"] or "{}"), **(meta or {})}
                c.execute("update runs set meta=?, updated=?, status=coalesce(?, status), "
                          "expected_tasks=coalesce(?, expected_tasks), source=coalesce(?, source) where run=?",
                          (json.dumps(merged), now, status, expected_tasks, source, run))

    def upsert_task(self, run: str, task: str, **fields: Any) -> None:
        cols = {k: (json.dumps(v) if k == "result" and v is not None else v) for k, v in fields.items()}
        with self._lock, self._conn() as c:
            c.execute("insert or ignore into tasks(run, task) values (?,?)", (run, task))
            if cols:
                c.execute(f"update tasks set {', '.join(f'{k}=?' for k in cols)} where run=? and task=?",
                          (*cols.values(), run, task))
            r = c.execute("select status, result, patch, test_log from tasks where run=? and task=?", (run, task)).fetchone()
            c.execute("update tasks set outcome=? where run=? and task=?", (_outcome(r), run, task))
            c.execute("update runs set updated=? where run=?", (time.time(), run))

    def add_entries(self, run: str, task: str, items: list[tuple[int, dict]]) -> int:
        """Insert (seq, entry) pairs; returns how many were new."""
        with self._lock, self._conn() as c:
            c.execute("insert or ignore into tasks(run, task, started) values (?,?,?)",
                      (run, task, min((e.get("timestamp") or time.time()) for _, e in items) if items else time.time()))
            before = c.total_changes
            c.executemany("insert or ignore into entries(run, task, seq, ts, entry) values (?,?,?,?,?)",
                          [(run, task, s, e.get("timestamp"), json.dumps(e, ensure_ascii=False, default=str)) for s, e in items])
            new = c.total_changes - before
            if new:
                c.execute("update tasks set n_entries=(select count(*) from entries where run=? and task=?) "
                          "where run=? and task=?", (run, task, run, task))
                c.execute("update runs set updated=? where run=?", (time.time(), run))
            return new

    def replace_entries(self, run: str, task: str, entries: list[dict]) -> None:
        with self._lock, self._conn() as c:
            c.execute("delete from entries where run=? and task=?", (run, task))
            c.executemany("insert into entries(run, task, seq, ts, entry) values (?,?,?,?,?)",
                          [(run, task, i, e.get("timestamp"), json.dumps(e, ensure_ascii=False, default=str))
                           for i, e in enumerate(entries)])
            c.execute("insert or ignore into tasks(run, task) values (?,?)", (run, task))
            c.execute("update tasks set n_entries=?, stats_n=-1 where run=? and task=?", (len(entries), run, task))

    def add_metrics(self, run: str, rows: list[tuple[float, dict]]) -> int:
        with self._lock, self._conn() as c:
            before = c.total_changes
            c.executemany("insert or ignore into metrics(run, ts, data) values (?,?,?)",
                          [(run, ts, json.dumps(d)) for ts, d in rows])
            return c.total_changes - before

    def rename_run(self, old: str, new: str) -> None:
        with self._lock, self._conn() as c:
            for t in ("runs", "tasks", "entries", "metrics"):
                c.execute(f"update {t} set run=? where run=?", (new, old))

    def free_name(self, base: str) -> str:
        name, i = base, 2
        while self.run_exists(name):
            name, i = f"{base}.{i}", i + 1
        return name

    def run_exists(self, run: str) -> bool:
        return self._conn().execute("select 1 from runs where run=?", (run,)).fetchone() is not None

    def delete_run(self, run: str) -> None:
        with self._lock, self._conn() as c:
            for t in ("runs", "tasks", "entries", "metrics"):
                c.execute(f"delete from {t} where run=?", (run,))

    # ── reads ────────────────────────────────────────────────────────────────
    def entries(self, run: str, task: str) -> list[dict]:
        rows = self._conn().execute("select entry from entries where run=? and task=? order by ts, seq",
                                    (run, task)).fetchall()
        return [json.loads(r["entry"]) for r in rows]

    def _stats(self, row: sqlite3.Row) -> dict:
        if row["stats_n"] == row["n_entries"] and row["stats"]:
            return json.loads(row["stats"])
        st = model.summarize(self.entries(row["run"], row["task"]))
        with self._lock, self._conn() as c:
            c.execute("update tasks set stats=?, stats_n=? where run=? and task=?",
                      (json.dumps(st), row["n_entries"], row["run"], row["task"]))
        return st

    def task_rows(self, run: str) -> list[dict]:
        rows = self._conn().execute("select * from tasks where run=? order by coalesce(started, 0), task", (run,)).fetchall()
        out = []
        for r in rows:
            res = json.loads(r["result"]) if r["result"] else {}
            out.append({"task": r["task"], "status": r["status"], "outcome": r["outcome"], "source": r["source"], "started": r["started"],
                        "ended": r["ended"], "n_entries": r["n_entries"], "result": _slim_result(res),
                        "stats": self._stats(r)})
        return out

    def task(self, run: str, task: str) -> dict | None:
        r = self._conn().execute("select * from tasks where run=? and task=?", (run, task)).fetchone()
        if r is None:
            return None
        return {"task": task, "status": r["status"], "outcome": r["outcome"], "source": r["source"], "started": r["started"],
                "ended": r["ended"], "result": json.loads(r["result"]) if r["result"] else {},
                "patch": r["patch"] or "", "test_log": r["test_log"] or ""}

    def runs(self) -> list[dict]:
        c = self._conn()
        out = []
        for r in c.execute("select * from runs order by created desc").fetchall():
            agg = c.execute(
                "select count(*) n, sum(status='done') done, sum(status='running') running, "
                "sum(outcome='PASS') resolved, sum(outcome='TIMEOUT') timeouts, "
                "min(started) t_first, max(coalesce(ended, started)) t_last from tasks where run=?", (r["run"],)).fetchone()
            m = c.execute("select max(ts) t from metrics where run=?", (r["run"],)).fetchone()
            oc = dict(c.execute("select outcome, count(*) from tasks where run=? group by outcome", (r["run"],)).fetchall())
            out.append({"run": r["run"], "source": r["source"], "status": r["status"], "meta": json.loads(r["meta"] or "{}"),
                        "created": r["created"], "updated": r["updated"], "expected_tasks": r["expected_tasks"],
                        "tasks": agg["n"], "done": agg["done"] or 0, "running": agg["running"] or 0,
                        "resolved": agg["resolved"] or 0, "timeouts": agg["timeouts"] or 0, "outcomes": oc,
                        "t_first": agg["t_first"], "t_last": agg["t_last"], "metrics_last": m["t"]})
        return out

    def run(self, run: str) -> dict | None:
        return next((r for r in self.runs() if r["run"] == run), None)

    def metrics(self, run: str, since: float = 0.0, limit: int = 20000) -> list[dict]:
        rows = self._conn().execute("select ts, data from metrics where run=? and ts>? order by ts limit ?",
                                    (run, since, limit)).fetchall()
        return [{"ts": r["ts"], **json.loads(r["data"])} for r in rows]


def _slim_result(r: dict) -> dict:
    keep = ("resolved", "error", "tool_calls", "duration_seconds", "test_exit_code", "patch_chars", "repo")
    return {k: r.get(k) for k in keep if k in r}


def _outcome(r: sqlite3.Row) -> str:
    return outcome.outcome(r["status"], json.loads(r["result"]) if r["result"] else None, r["patch"], r["test_log"])
