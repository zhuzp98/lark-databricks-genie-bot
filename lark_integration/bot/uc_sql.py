"""Run SQL against the workspace SQL warehouse (App SP or local CLI auth).

Uses Statement Execution REST API via requests (timeouts; no SDK hang on OAuth).
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import requests

from .dbx_auth import auth_headers, clear_token_cache, default_host


DEFAULT_CATALOG = "workspace"
DEFAULT_SCHEMA = "lark_integration"
DEFAULT_WAREHOUSE_ID = ""  # set DATABRICKS_WAREHOUSE_ID in App env / .env


def warehouse_id() -> str:
    wid = (
        os.environ.get("DATABRICKS_WAREHOUSE_ID")
        or os.environ.get("LARK_SQL_WAREHOUSE_ID")
        or DEFAULT_WAREHOUSE_ID
    ).strip()
    if not wid or wid.startswith("YOUR_"):
        raise RuntimeError(
            "DATABRICKS_WAREHOUSE_ID is not set (or still a YOUR_* placeholder)"
        )
    return wid


def table_fqn(name: str) -> str:
    cat = os.environ.get("LARK_UC_CATALOG", DEFAULT_CATALOG)
    sch = os.environ.get("LARK_UC_SCHEMA", DEFAULT_SCHEMA)
    return f"{cat}.{sch}.{name}"


def sql_quote(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _session() -> requests.Session:
    # Ignore broken local HTTP(S)_PROXY for Databricks workspace calls.
    s = requests.Session()
    s.trust_env = False
    return s


def _post_statement(statement: str, wait_timeout_sec: int) -> dict[str, Any]:
    host = default_host()
    timeout = f"{min(max(wait_timeout_sec, 5), 50)}s"
    body = {
        "warehouse_id": warehouse_id(),
        "statement": statement,
        "wait_timeout": timeout,
    }
    headers = auth_headers()
    url = f"{host}/api/2.0/sql/statements"
    sess = _session()
    r = sess.post(url, headers=headers, json=body, timeout=wait_timeout_sec + 20)
    if r.status_code == 401:
        clear_token_cache()
        headers = auth_headers()
        r = sess.post(url, headers=headers, json=body, timeout=wait_timeout_sec + 20)
    r.raise_for_status()
    return r.json()


def _get_statement(statement_id: str, timeout_sec: int = 30) -> dict[str, Any]:
    host = default_host()
    headers = auth_headers()
    url = f"{host}/api/2.0/sql/statements/{statement_id}"
    sess = _session()
    r = sess.get(url, headers=headers, timeout=timeout_sec)
    if r.status_code == 401:
        clear_token_cache()
        headers = auth_headers()
        r = sess.get(url, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    return r.json()


def execute_sql(
    statement: str,
    *,
    wait_timeout_sec: int = 50,
) -> list[dict[str, Any]]:
    """Execute SQL and return rows as list of dicts (empty if no result set)."""
    payload = _post_statement(statement, wait_timeout_sec)
    state = ((payload.get("status") or {}).get("state") or "").upper()
    deadline = time.time() + wait_timeout_sec + 30
    while state in {"PENDING", "RUNNING"} and time.time() < deadline:
        time.sleep(1.5)
        payload = _get_statement(payload["statement_id"])
        state = ((payload.get("status") or {}).get("state") or "").upper()

    if state != "SUCCEEDED":
        err = (payload.get("status") or {}).get("error") or {}
        msg = f"{err.get('error_code', '')}: {err.get('message', '')}".strip(": ")
        raise RuntimeError(
            f"SQL failed state={state} {msg}; statement={_safe_stmt_snippet(statement)}"
        )

    result = payload.get("result") or {}
    data = result.get("data_array")
    if not data:
        return []
    cols: list[str] = []
    schema = ((payload.get("manifest") or {}).get("schema") or {}).get("columns") or []
    cols = [c.get("name") for c in schema if c.get("name")]
    rows: list[dict[str, Any]] = []
    for arr in data:
        if cols:
            rows.append({cols[i]: arr[i] if i < len(arr) else None for i in range(len(cols))})
        else:
            rows.append({str(i): v for i, v in enumerate(arr)})
    return rows
