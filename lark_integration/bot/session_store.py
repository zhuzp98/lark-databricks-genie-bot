"""Session map for Lark chat ↔ Genie One / Space conversation.

Memory cache with optional Unity Catalog read-through / upsert
(`workspace.lark_integration.bot_sessions` by default).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from .uc_sql import execute_sql, sql_quote, table_fqn

log = logging.getLogger(__name__)


@dataclass
class Session:
    lark_chat_id: str
    lark_open_id: str
    mode: str  # genie_one | space
    space_id: str | None
    space_title: str | None
    conversation_id: str
    updated_at: str

    @property
    def agent_label(self) -> str:
        if self.mode == "genie_one":
            return "Genie One (Ontology)"
        return self.space_title or self.space_id or "Genie Agent"


def _persist_enabled() -> bool:
    return os.environ.get("LARK_UC_PERSIST", "1").strip().lower() not in ("0", "false", "no", "off")


class SessionStore:
    def __init__(self):
        self._mem: dict[tuple[str, str], Session] = {}
        self._open_to_chat: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, chat_id: str, open_id: str) -> Session | None:
        key = (chat_id, open_id)
        with self._lock:
            hit = self._mem.get(key)
            if hit:
                return hit
        if not _persist_enabled():
            return None
        loaded = self._load_uc(chat_id, open_id)
        if not loaded:
            return None
        with self._lock:
            self._mem[key] = loaded
            if loaded.lark_open_id:
                self._open_to_chat[loaded.lark_open_id] = loaded.lark_chat_id
        return loaded

    def chat_for_open_id(self, open_id: str) -> str | None:
        with self._lock:
            hit = self._open_to_chat.get(open_id)
            if hit:
                return hit
        if not open_id or not _persist_enabled():
            return None
        loaded = self._load_uc_by_open_id(open_id)
        if not loaded:
            return None
        with self._lock:
            self._mem[(loaded.lark_chat_id, loaded.lark_open_id)] = loaded
            self._open_to_chat[open_id] = loaded.lark_chat_id
        return loaded.lark_chat_id

    def put(
        self,
        chat_id: str,
        open_id: str,
        *,
        mode: str,
        conversation_id: str = "",
        space_id: str | None = None,
        space_title: str | None = None,
    ) -> Session:
        sess = Session(
            lark_chat_id=chat_id,
            lark_open_id=open_id,
            mode=mode,
            space_id=space_id if mode == "space" else None,
            space_title=space_title if mode == "space" else None,
            conversation_id=conversation_id,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._mem[(chat_id, open_id)] = sess
            if open_id:
                self._open_to_chat[open_id] = chat_id
        if _persist_enabled():
            try:
                self._upsert_uc(sess)
            except Exception as exc:  # noqa: BLE001 — soft-fail to memory
                log.warning("session UC upsert failed (memory kept): %s", exc)
        return sess

    def reset_conversation(self, chat_id: str, open_id: str) -> Session:
        prev = self.get(chat_id, open_id)
        if not prev:
            return self.put(chat_id, open_id, mode="genie_one", conversation_id="")
        return self.put(
            chat_id,
            open_id,
            mode=prev.mode,
            conversation_id="",
            space_id=prev.space_id,
            space_title=prev.space_title,
        )

    def _row_to_session(self, row: dict) -> Session:
        mode = (row.get("mode") or "genie_one") or "genie_one"
        return Session(
            lark_chat_id=str(row.get("lark_chat_id") or ""),
            lark_open_id=str(row.get("lark_open_id") or ""),
            mode=mode,
            space_id=row.get("genie_space_id") or None,
            space_title=row.get("space_title") or None,
            conversation_id=str(row.get("genie_conversation_id") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )

    def _load_uc(self, chat_id: str, open_id: str) -> Session | None:
        fqn = table_fqn("bot_sessions")
        sql = (
            f"SELECT lark_chat_id, lark_open_id, genie_space_id, genie_conversation_id, "
            f"updated_at, mode, space_title FROM {fqn} "
            f"WHERE lark_chat_id = {sql_quote(chat_id)} AND lark_open_id = {sql_quote(open_id)} "
            f"LIMIT 1"
        )
        try:
            rows = execute_sql(sql)
        except Exception as exc:  # noqa: BLE001
            log.warning("session UC load failed: %s", exc)
            return None
        if not rows:
            return None
        return self._row_to_session(rows[0])

    def _load_uc_by_open_id(self, open_id: str) -> Session | None:
        fqn = table_fqn("bot_sessions")
        sql = (
            f"SELECT lark_chat_id, lark_open_id, genie_space_id, genie_conversation_id, "
            f"updated_at, mode, space_title FROM {fqn} "
            f"WHERE lark_open_id = {sql_quote(open_id)} "
            f"ORDER BY updated_at DESC LIMIT 1"
        )
        try:
            rows = execute_sql(sql)
        except Exception as exc:  # noqa: BLE001
            log.warning("session UC load-by-open_id failed: %s", exc)
            return None
        if not rows:
            return None
        return self._row_to_session(rows[0])

    def _upsert_uc(self, sess: Session) -> None:
        fqn = table_fqn("bot_sessions")
        sql = f"""
MERGE INTO {fqn} AS t
USING (
  SELECT
    {sql_quote(sess.lark_chat_id)} AS lark_chat_id,
    {sql_quote(sess.lark_open_id)} AS lark_open_id,
    {sql_quote(sess.space_id)} AS genie_space_id,
    {sql_quote(sess.conversation_id)} AS genie_conversation_id,
    {sql_quote(sess.updated_at)} AS updated_at,
    {sql_quote(sess.mode)} AS mode,
    {sql_quote(sess.space_title)} AS space_title
) AS s
ON t.lark_chat_id = s.lark_chat_id AND t.lark_open_id = s.lark_open_id
WHEN MATCHED THEN UPDATE SET
  genie_space_id = s.genie_space_id,
  genie_conversation_id = s.genie_conversation_id,
  updated_at = s.updated_at,
  mode = s.mode,
  space_title = s.space_title
WHEN NOT MATCHED THEN INSERT (
  lark_chat_id, lark_open_id, genie_space_id, genie_conversation_id,
  updated_at, mode, space_title
) VALUES (
  s.lark_chat_id, s.lark_open_id, s.genie_space_id, s.genie_conversation_id,
  s.updated_at, s.mode, s.space_title
)
""".strip()
        execute_sql(sql)
