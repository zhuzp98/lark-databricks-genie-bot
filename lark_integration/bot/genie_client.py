"""Genie Conversation API client with Free Edition-friendly polling."""

from __future__ import annotations

import time
from typing import Any

import requests

from .dbx_auth import auth_headers, clear_token_cache, default_host
from .rate_limit import RateLimiter


class GenieClient:
    def __init__(
        self,
        space_id: str,
        *,
        host: str | None = None,
        token: str | None = None,
        profile: str = "DEFAULT",
        rate_limiter: RateLimiter | None = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
    ):
        self.space_id = space_id
        self.host = (host or default_host()).rstrip("/")
        self._token = token
        self._obo = bool(token)
        self.profile = profile
        self.rate = rate_limiter or RateLimiter(min_interval_sec=13.0)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def _headers(self) -> dict[str, str]:
        if self._token:
            return {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
        if self._obo:
            raise RuntimeError(
                "Databricks user token expired or missing; re-bind via /bind in Lark"
            )
        return auth_headers(self.profile)

    def _post(self, path: str, body: dict) -> dict[str, Any]:
        self.rate.wait()
        r = requests.post(f"{self.host}{path}", headers=self._headers(), json=body, timeout=120)
        if r.status_code == 401:
            if self._obo:
                raise RuntimeError(
                    "Databricks user token rejected (401); please re-bind in Lark"
                )
            self._token = None
            clear_token_cache()
            r = requests.post(f"{self.host}{path}", headers=self._headers(), json=body, timeout=120)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict[str, Any]:
        r = requests.get(f"{self.host}{path}", headers=self._headers(), timeout=120)
        if r.status_code == 401:
            if self._obo:
                raise RuntimeError(
                    "Databricks user token rejected (401); please re-bind in Lark"
                )
            self._token = None
            clear_token_cache()
            r = requests.get(f"{self.host}{path}", headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()

    def _extract_ids(self, data: dict[str, Any]) -> tuple[str, str]:
        """Normalize start/follow-up payloads (shapes differ across endpoints)."""
        conv = (
            (data.get("conversation") or {}).get("id")
            or data.get("conversation_id")
        )
        msg = data.get("message")
        if isinstance(msg, dict):
            mid = msg.get("id") or msg.get("message_id")
        else:
            mid = data.get("message_id") or data.get("id")
        if not mid:
            raise RuntimeError(f"Unexpected Genie response (no message id): {data}")
        return conv, mid

    def start_conversation(self, content: str) -> tuple[str, str]:
        data = self._post(
            f"/api/2.0/genie/spaces/{self.space_id}/start-conversation",
            {"content": content},
        )
        conv, mid = self._extract_ids(data)
        if not conv:
            raise RuntimeError(f"Unexpected Genie start response (no conversation id): {data}")
        return conv, mid

    def ask_followup(self, conversation_id: str, content: str) -> str:
        data = self._post(
            f"/api/2.0/genie/spaces/{self.space_id}/conversations/{conversation_id}/messages",
            {"content": content},
        )
        _, mid = self._extract_ids(data)
        return mid

    def get_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        return self._get(
            f"/api/2.0/genie/spaces/{self.space_id}/conversations/{conversation_id}/messages/{message_id}"
        )

    def wait_completed(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            msg = self.get_message(conversation_id, message_id)
            status = msg.get("status")
            if status == "COMPLETED":
                return msg
            if status in {"FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED", "FILTER_FAILED"}:
                raise RuntimeError(f"Genie message ended with status={status}: {msg}")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"Genie poll timeout after {self.poll_timeout}s")

    def query_result(self, conversation_id: str, message_id: str, attachment_id: str) -> dict[str, Any]:
        return self._get(
            f"/api/2.0/genie/spaces/{self.space_id}/conversations/{conversation_id}/messages/{message_id}/query-result/{attachment_id}"
        )

    def ask(self, content: str, conversation_id: str | None = None) -> dict[str, Any]:
        """Ask a question; return {conversation_id, message, table?}."""
        try:
            if conversation_id:
                message_id = self.ask_followup(conversation_id, content)
            else:
                conversation_id, message_id = self.start_conversation(content)
        except Exception:
            # Follow-up may fail on stale conversation — start fresh.
            if conversation_id:
                conversation_id, message_id = self.start_conversation(content)
            else:
                raise
        msg = self.wait_completed(conversation_id, message_id)
        table = None
        for att in msg.get("attachments") or []:
            aid = att.get("attachment_id")
            if aid and "query" in att:
                try:
                    table = self.query_result(conversation_id, message_id, aid)
                except Exception as e:
                    table = {"error": str(e)}
                break
        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "message": msg,
            "table": table,
        }
