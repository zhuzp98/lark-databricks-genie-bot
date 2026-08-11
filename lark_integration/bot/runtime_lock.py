"""Single-writer lease so only one Lark WS bot runs (App vs local dual-open).

Uses Unity Catalog table `bot_runtime_lease` (same warehouse as Phase C).
Soft-fail: if UC unavailable, allow start but log a warning (dev-friendly).
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .slog import slog
from .uc_sql import execute_sql, sql_quote, table_fqn

DEFAULT_LOCK = "lark_ws"
DEFAULT_TTL_SEC = 120
DEFAULT_HEARTBEAT_SEC = 40


def _persist_enabled() -> bool:
    return os.environ.get("LARK_UC_PERSIST", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _lock_enabled() -> bool:
    return os.environ.get("LARK_RUNTIME_LOCK", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _steal() -> bool:
    return os.environ.get("LARK_RUNTIME_LOCK_STEAL", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _kind() -> str:
    if os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("DATABRICKS_APP_NAME"):
        return "app"
    return "local"


def new_holder_id() -> str:
    host = socket.gethostname()[:32]
    return f"{_kind()}-{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


@dataclass
class Lease:
    lock_name: str
    holder_id: str
    holder_kind: str
    ttl_sec: int


class RuntimeLock:
    """Process-wide lease with background heartbeat."""

    def __init__(
        self,
        lock_name: str = DEFAULT_LOCK,
        *,
        ttl_sec: int = DEFAULT_TTL_SEC,
        heartbeat_sec: int = DEFAULT_HEARTBEAT_SEC,
    ):
        self.lock_name = lock_name
        self.ttl_sec = ttl_sec
        self.heartbeat_sec = heartbeat_sec
        self.holder_id = new_holder_id()
        self.holder_kind = _kind()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.held = False

    def acquire(self) -> bool:
        if not _lock_enabled():
            slog("runtime_lock_skipped", component="lock", reason="LARK_RUNTIME_LOCK=0")
            self.held = True
            return True
        if not _persist_enabled():
            slog("runtime_lock_skipped", component="lock", reason="LARK_UC_PERSIST=0")
            self.held = True
            return True

        try:
            self._ensure_table()
            ok = self._try_acquire()
        except Exception as exc:  # noqa: BLE001
            slog(
                "runtime_lock_uc_error",
                level="warning",
                component="lock",
                error=str(exc),
                action="allow_start",
            )
            self.held = True
            return True

        if not ok:
            slog(
                "runtime_lock_busy",
                level="error",
                component="lock",
                lock=self.lock_name,
                holder_id=self.holder_id,
            )
            return False

        self.held = True
        slog(
            "runtime_lock_acquired",
            component="lock",
            lock=self.lock_name,
            holder_id=self.holder_id,
            holder_kind=self.holder_kind,
            ttl_sec=self.ttl_sec,
        )
        self._thread = threading.Thread(
            target=self._heartbeat_loop, name="runtime-lock-hb", daemon=True
        )
        self._thread.start()
        return True

    def release(self) -> None:
        self._stop.set()
        if not self.held or not _lock_enabled() or not _persist_enabled():
            return
        try:
            fqn = table_fqn("bot_runtime_lease")
            execute_sql(
                f"DELETE FROM {fqn} WHERE lock_name = {sql_quote(self.lock_name)} "
                f"AND holder_id = {sql_quote(self.holder_id)}"
            )
            slog(
                "runtime_lock_released",
                component="lock",
                lock=self.lock_name,
                holder_id=self.holder_id,
            )
        except Exception as exc:  # noqa: BLE001
            slog(
                "runtime_lock_release_failed",
                level="warning",
                component="lock",
                error=str(exc),
            )
        self.held = False

    def status(self) -> dict:
        return {
            "enabled": _lock_enabled() and _persist_enabled(),
            "held": self.held,
            "lock_name": self.lock_name,
            "holder_id": self.holder_id,
            "holder_kind": self.holder_kind,
            "ttl_sec": self.ttl_sec,
        }

    def _ensure_table(self) -> None:
        # Table is provisioned by ops (Phase D DDL). Touch with a cheap SELECT.
        fqn = table_fqn("bot_runtime_lease")
        execute_sql(f"SELECT 1 FROM {fqn} WHERE 1=0")

    def _try_acquire(self) -> bool:
        fqn = table_fqn("bot_runtime_lease")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        steal = _steal()
        # Expire stale rows first
        execute_sql(
            f"DELETE FROM {fqn} WHERE lock_name = {sql_quote(self.lock_name)} "
            f"AND heartbeat_at < CURRENT_TIMESTAMP() - INTERVAL {int(self.ttl_sec)} SECONDS"
        )
        rows = execute_sql(
            f"SELECT holder_id, holder_kind, heartbeat_at FROM {fqn} "
            f"WHERE lock_name = {sql_quote(self.lock_name)} LIMIT 1"
        )
        if rows and not steal:
            other = rows[0].get("holder_id")
            if other and other != self.holder_id:
                slog(
                    "runtime_lock_held_by_other",
                    level="warning",
                    component="lock",
                    other_holder=other,
                    other_kind=rows[0].get("holder_kind"),
                    other_heartbeat=str(rows[0].get("heartbeat_at")),
                )
                return False

        execute_sql(
            f"""
MERGE INTO {fqn} AS t
USING (
  SELECT
    {sql_quote(self.lock_name)} AS lock_name,
    {sql_quote(self.holder_id)} AS holder_id,
    {sql_quote(self.holder_kind)} AS holder_kind,
    CAST({sql_quote(now)} AS TIMESTAMP) AS heartbeat_at,
    CAST({sql_quote(now)} AS TIMESTAMP) AS started_at,
    {sql_quote(f"pid={os.getpid()}")} AS meta
) AS s
ON t.lock_name = s.lock_name
WHEN MATCHED THEN UPDATE SET
  holder_id = s.holder_id,
  holder_kind = s.holder_kind,
  heartbeat_at = s.heartbeat_at,
  started_at = s.started_at,
  meta = s.meta
WHEN NOT MATCHED THEN INSERT (lock_name, holder_id, holder_kind, heartbeat_at, started_at, meta)
VALUES (s.lock_name, s.holder_id, s.holder_kind, s.heartbeat_at, s.started_at, s.meta)
""".strip()
        )
        return True

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_sec):
            try:
                fqn = table_fqn("bot_runtime_lease")
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                execute_sql(
                    f"UPDATE {fqn} SET heartbeat_at = CAST({sql_quote(now)} AS TIMESTAMP) "
                    f"WHERE lock_name = {sql_quote(self.lock_name)} "
                    f"AND holder_id = {sql_quote(self.holder_id)}"
                )
            except Exception as exc:  # noqa: BLE001
                slog(
                    "runtime_lock_heartbeat_failed",
                    level="warning",
                    component="lock",
                    error=str(exc),
                )
