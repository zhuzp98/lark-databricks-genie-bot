"""Genie One client via Databricks managed MCP `/api/2.0/mcp/genie`."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from .dbx_auth import auth_headers, clear_token_cache, default_host
from .rate_limit import RateLimiter


class GenieOneClient:
    """Chat in Genie One (workspace Ontology routing) over MCP."""

    def __init__(
        self,
        *,
        host: str | None = None,
        token: str | None = None,
        profile: str = "DEFAULT",
        rate_limiter: RateLimiter | None = None,
        poll_interval: float = 3.0,
        poll_timeout: float = 300.0,
    ):
        self.host = (host or default_host()).rstrip("/")
        self._token = token
        # When True, never fall back to App SP / CLI on 401 (OBO user identity).
        self._obo = bool(token)
        self.profile = profile
        self.rate = rate_limiter or RateLimiter(min_interval_sec=5.0)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._initialized = False
        self._rpc_id = 0

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

    def _rpc(self, method: str, params: dict | None = None) -> dict[str, Any]:
        self._rpc_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method}
        if params is not None:
            body["params"] = params
        r = requests.post(
            f"{self.host}/api/2.0/mcp/genie",
            headers=self._headers(),
            json=body,
            timeout=180,
        )
        if r.status_code == 401:
            if self._obo:
                raise RuntimeError(
                    "Databricks user token rejected (401); please re-bind in Lark"
                )
            self._token = None
            clear_token_cache()
            r = requests.post(
                f"{self.host}/api/2.0/mcp/genie",
                headers=self._headers(),
                json=body,
                timeout=180,
            )
        if r.status_code == 403:
            detail = (r.text or "").strip()[:500]
            raise RuntimeError(
                "Genie One MCP 403 Forbidden — OBO token likely missing `genie` scope. "
                "Re-bind in Lark after opening the App and accepting User authorization "
                f"(scopes must include genie). Details: {detail or r.reason}"
            )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "lark-genie-bot", "version": "0.2.0"},
            },
        )
        # notification (no id)
        requests.post(
            f"{self.host}/api/2.0/mcp/genie",
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=30,
        )
        self._initialized = True

    def _tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_init()
        self.rate.wait()
        data = self._rpc("tools/call", {"name": name, "arguments": arguments})
        result = data.get("result") or {}
        if result.get("isError"):
            raise RuntimeError(f"tool {name} error: {result}")
        structured = result.get("structuredContent") or {}
        if not structured:
            # fallback: parse first text block as JSON
            for c in result.get("content") or []:
                if c.get("type") == "text":
                    try:
                        structured = json.loads(c.get("text") or "{}")
                    except json.JSONDecodeError:
                        structured = {"final_answer": c.get("text")}
                    break
        text = "\n".join(
            c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text"
        )
        return {"structured": structured, "text": text, "raw": result}

    def ask(self, question: str, conversation_id: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"question": question}
        if conversation_id:
            args["conversation_id"] = conversation_id
        first = self._tool("genie_ask", args)
        sc = first["structured"]
        conv = sc.get("conversation_id") or conversation_id
        resp = sc.get("response_id")
        status = sc.get("status") or "completed"

        deadline = time.time() + self.poll_timeout
        while status == "in_progress" and time.time() < deadline:
            if not conv or not resp:
                raise RuntimeError(f"Genie One in_progress without ids: {sc}")
            time.sleep(self.poll_interval)
            polled = self._tool(
                "genie_poll_response",
                {"conversation_id": conv, "response_id": resp},
            )
            sc = polled["structured"]
            status = sc.get("status") or status
            first = polled

        if status == "failed":
            raise RuntimeError(f"Genie One failed: {sc}")
        if status == "in_progress":
            raise TimeoutError(f"Genie One poll timeout after {self.poll_timeout}s")

        return {
            "mode": "genie_one",
            "conversation_id": sc.get("conversation_id") or conv,
            "response_id": sc.get("response_id") or resp,
            "status": status,
            "final_answer": sc.get("final_answer") or "",
            "deep_link": sc.get("deep_link"),
            "query_items": sc.get("query_items") or [],
            "text": first.get("text") or "",
            "structured": sc,
        }
