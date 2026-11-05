"""Checkpointing, retry and fault-injection support for idempotent, restartable pipeline stages."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from orbit.logging_setup import get_logger

log = get_logger("orbit.pipeline")
T = TypeVar("T")


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class InjectedFailure(RuntimeError):
    """Raised by the fault-injection hook to test recovery. Never raised in normal operation."""


class FaultInjector:
    """Fail the first `times` attempts of (stage, batch_key) to exercise retry / restart behaviour."""

    def __init__(self, stage: str | None = None, batch_key: str | None = None, times: int = 1):
        self.stage, self.batch_key, self.remaining = stage, batch_key, times

    def maybe_fail(self, stage: str, batch_key: str) -> None:
        if self.stage == stage and (self.batch_key in (None, batch_key)) and self.remaining > 0:
            self.remaining -= 1
            raise InjectedFailure(f"injected failure in {stage}/{batch_key}")


class Checkpoints:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def is_done(self, stage: str, batch_key: str, input_hash: str | None) -> bool:
        row = self.conn.execute(
            "SELECT status, input_hash FROM etl_checkpoint WHERE stage=? AND batch_key=?", (stage, batch_key)
        ).fetchone()
        return bool(row and row["status"] == "succeeded" and row["input_hash"] == input_hash)

    def _upsert(self, stage, batch_key, status, **kw):
        cur = self.conn.execute("SELECT attempts FROM etl_checkpoint WHERE stage=? AND batch_key=?", (stage, batch_key))
        row = cur.fetchone()
        attempts = (row["attempts"] if row else 0) + (1 if status == "running" else 0)
        self.conn.execute(
            """INSERT INTO etl_checkpoint(stage,batch_key,status,input_hash,rows_in,rows_out,rows_rejected,
                   duplicates,duration_s,attempts,started_at,finished_at,error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(stage,batch_key) DO UPDATE SET
                   status=excluded.status,
                   input_hash=COALESCE(excluded.input_hash, etl_checkpoint.input_hash),
                   rows_in=COALESCE(excluded.rows_in, etl_checkpoint.rows_in),
                   rows_out=COALESCE(excluded.rows_out, etl_checkpoint.rows_out),
                   rows_rejected=COALESCE(excluded.rows_rejected, etl_checkpoint.rows_rejected),
                   duplicates=COALESCE(excluded.duplicates, etl_checkpoint.duplicates),
                   duration_s=COALESCE(excluded.duration_s, etl_checkpoint.duration_s),
                   attempts=excluded.attempts,
                   started_at=COALESCE(excluded.started_at, etl_checkpoint.started_at),
                   finished_at=excluded.finished_at, error=excluded.error""",
            (stage, batch_key, status, kw.get("input_hash"), kw.get("rows_in"), kw.get("rows_out"),
             kw.get("rows_rejected"), kw.get("duplicates"), kw.get("duration_s"), attempts,
             kw.get("started_at"), kw.get("finished_at"), kw.get("error")),
        )
        self.conn.commit()

    def start(self, stage, batch_key, input_hash=None):
        self._upsert(stage, batch_key, "running", input_hash=input_hash, started_at=now_iso())

    def succeed(self, stage, batch_key, input_hash, **kw):
        self._upsert(stage, batch_key, "succeeded", input_hash=input_hash, finished_at=now_iso(), error=None, **kw)

    def fail(self, stage, batch_key, error: str):
        self._upsert(stage, batch_key, "failed", finished_at=now_iso(), error=error[:500])

    def summary(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM etl_checkpoint ORDER BY stage, batch_key").fetchall()


def with_retry(fn: Callable[[], T], retries: int = 2, backoff_s: float = 0.0, what: str = "task") -> T:
    """Run fn, retrying on exception up to `retries` times (total attempts = retries + 1)."""
    last: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - deliberate: any failure is retried then re-raised
            last = exc
            log.warning("attempt failed", extra={"task": what, "attempt": attempt + 1, "error": str(exc)[:200]})
            if attempt < retries and backoff_s:
                time.sleep(backoff_s * (2**attempt))
    assert last is not None
    raise last
