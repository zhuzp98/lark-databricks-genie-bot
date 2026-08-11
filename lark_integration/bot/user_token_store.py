"""Per-user Databricks OBO tokens (Apps bind).

Memory cache with optional Unity Catalog read-through / upsert
(`workspace.lark_integration.bot_obo_tokens` by default).

Never log access_token values.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .uc_sql import execute_sql, sql_quote, table_fqn

log = logging.getLogger(__name__)

# Cap below typical Apps OBO token lifetime so we re-prompt before hard 401.
DEFAULT_TTL_SEC = 55 * 60


@dataclass
class UserToken:
    email: str
    access_token: str
    expires_at: float
    open_id: str | None = None

    @property
    def expires_in(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    @property
    def valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at


def _persist_enabled() -> bool:
    return os.environ.get("LARK_UC_PERSIST", "1").strip().lower() not in ("0", "false", "no", "off")


def _parse_expires_at(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: ms vs sec
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    # Databricks TIMESTAMP often returns ISO-ish strings
    cleaned = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        return None


class UserTokenStore:
    def __init__(self, default_ttl_sec: float = DEFAULT_TTL_SEC):
        self._default_ttl = default_ttl_sec
        self._by_email: dict[str, UserToken] = {}
        self._open_to_email: dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _norm_email(email: str) -> str:
        return (email or "").strip().lower()

    def remember_open_id(self, open_id: str, email: str) -> None:
        if not open_id or not email:
            return
        key = self._norm_email(email)
        with self._lock:
            self._open_to_email[open_id] = key
        if _persist_enabled():
            try:
                self._touch_open_id_uc(open_id, key)
            except Exception as exc:  # noqa: BLE001
                log.warning("token UC open_id touch failed: %s", exc)

    def email_for_open_id(self, open_id: str) -> str | None:
        with self._lock:
            hit = self._open_to_email.get(open_id)
            if hit:
                return hit
        if not open_id or not _persist_enabled():
            return None
        entry = self._load_uc_by_open_id(open_id)
        if not entry or not entry.valid:
            return None
        with self._lock:
            self._by_email[entry.email] = entry
            self._open_to_email[open_id] = entry.email
        return entry.email

    def put(
        self,
        email: str,
        access_token: str,
        *,
        expires_in: float | None = None,
        open_id: str | None = None,
    ) -> UserToken:
        key = self._norm_email(email)
        if not key or not access_token:
            raise ValueError("email and access_token are required")
        ttl = self._default_ttl if expires_in is None else min(float(expires_in), self._default_ttl)
        entry = UserToken(
            email=key,
            access_token=access_token,
            expires_at=time.time() + max(60.0, ttl),
            open_id=open_id,
        )
        with self._lock:
            self._by_email[key] = entry
            if open_id:
                self._open_to_email[open_id] = key
        if _persist_enabled():
            try:
                self._upsert_uc(entry)
            except Exception as exc:  # noqa: BLE001 — soft-fail to memory
                log.warning("token UC upsert failed (memory kept): %s", exc)
        return entry

    def get(self, email: str) -> UserToken | None:
        key = self._norm_email(email)
        with self._lock:
            entry = self._by_email.get(key)
            if entry:
                if entry.valid:
                    return entry
                del self._by_email[key]
        if not key or not _persist_enabled():
            return None
        loaded = self._load_uc(key)
        if not loaded:
            return None
        if not loaded.valid:
            try:
                self._delete_uc(key)
            except Exception as exc:  # noqa: BLE001
                log.warning("token UC delete expired failed: %s", exc)
            return None
        with self._lock:
            self._by_email[key] = loaded
            if loaded.open_id:
                self._open_to_email[loaded.open_id] = key
        return loaded

    def get_for_open_id(self, open_id: str) -> UserToken | None:
        email = self.email_for_open_id(open_id)
        if not email:
            return None
        return self.get(email)

    def invalidate(self, email: str) -> None:
        key = self._norm_email(email)
        with self._lock:
            self._by_email.pop(key, None)
        if key and _persist_enabled():
            try:
                self._delete_uc(key)
            except Exception as exc:  # noqa: BLE001
                log.warning("token UC invalidate failed: %s", exc)

    def status(self, email: str) -> dict[str, Any]:
        entry = self.get(email)
        if not entry:
            return {
                "authenticated": False,
                "user_email": self._norm_email(email),
                "message": "not authenticated or expired",
            }
        return {
            "authenticated": True,
            "user_email": entry.email,
            "expires_in": entry.expires_in,
            "message": "authenticated",
            "persist": _persist_enabled(),
        }

    def cleanup(self) -> int:
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._by_email.items() if now >= v.expires_at]
            for k in expired:
                del self._by_email[k]
        return len(expired)

    def _load_uc(self, email: str) -> UserToken | None:
        fqn = table_fqn("bot_obo_tokens")
        sql = (
            f"SELECT email, open_id, access_token, expires_at, updated_at "
            f"FROM {fqn} WHERE email = {sql_quote(email)} LIMIT 1"
        )
        try:
            rows = execute_sql(sql)
        except Exception as exc:  # noqa: BLE001
            log.warning("token UC load failed: %s", exc)
            return None
        if not rows:
            return None
        return self._row_to_token(rows[0])

    def _load_uc_by_open_id(self, open_id: str) -> UserToken | None:
        fqn = table_fqn("bot_obo_tokens")
        sql = (
            f"SELECT email, open_id, access_token, expires_at, updated_at "
            f"FROM {fqn} WHERE open_id = {sql_quote(open_id)} "
            f"ORDER BY updated_at DESC LIMIT 1"
        )
        try:
            rows = execute_sql(sql)
        except Exception as exc:  # noqa: BLE001
            log.warning("token UC load-by-open_id failed: %s", exc)
            return None
        if not rows:
            return None
        return self._row_to_token(rows[0])

    def _row_to_token(self, row: dict) -> UserToken | None:
        email = self._norm_email(str(row.get("email") or ""))
        token = row.get("access_token")
        exp = _parse_expires_at(row.get("expires_at"))
        if not email or not token or exp is None:
            return None
        open_id = row.get("open_id") or None
        return UserToken(
            email=email,
            access_token=str(token),
            expires_at=exp,
            open_id=str(open_id) if open_id else None,
        )

    def _upsert_uc(self, entry: UserToken) -> None:
        fqn = table_fqn("bot_obo_tokens")
        exp_iso = datetime.fromtimestamp(entry.expires_at, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # Do not interpolate token into logs — only into this parameterized-style quote.
        sql = f"""
MERGE INTO {fqn} AS t
USING (
  SELECT
    {sql_quote(entry.email)} AS email,
    {sql_quote(entry.open_id)} AS open_id,
    {sql_quote(entry.access_token)} AS access_token,
    CAST({sql_quote(exp_iso)} AS TIMESTAMP) AS expires_at,
    CAST({sql_quote(now_iso)} AS TIMESTAMP) AS updated_at
) AS s
ON t.email = s.email
WHEN MATCHED THEN UPDATE SET
  open_id = s.open_id,
  access_token = s.access_token,
  expires_at = s.expires_at,
  updated_at = s.updated_at
WHEN NOT MATCHED THEN INSERT (email, open_id, access_token, expires_at, updated_at)
VALUES (s.email, s.open_id, s.access_token, s.expires_at, s.updated_at)
""".strip()
        execute_sql(sql)

    def _delete_uc(self, email: str) -> None:
        fqn = table_fqn("bot_obo_tokens")
        execute_sql(f"DELETE FROM {fqn} WHERE email = {sql_quote(email)}")

    def _touch_open_id_uc(self, open_id: str, email: str) -> None:
        """Update open_id on an existing row without rewriting the token."""
        fqn = table_fqn("bot_obo_tokens")
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        execute_sql(
            f"UPDATE {fqn} SET open_id = {sql_quote(open_id)}, "
            f"updated_at = CAST({sql_quote(now_iso)} AS TIMESTAMP) "
            f"WHERE email = {sql_quote(email)}"
        )


# Process-wide store shared by FastAPI bind routes and Lark WS bot thread.
STORE = UserTokenStore()
