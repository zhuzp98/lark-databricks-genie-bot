"""Lark tenant_access_token helpers.

Credentials preference order:
1. Explicit app_id / app_secret arguments
2. Environment variables LARK_APP_ID / LARK_APP_SECRET
3. Databricks secret scope (dbutils) when running on Databricks
4. Local plaintext file docs/credentials/local_secrets.md (PoC only)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import requests

DEFAULT_BASE = "https://open.larksuite.com"
DEFAULT_SCOPE = "lark_integration"
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_SECRETS_MD = REPO_ROOT / "docs" / "credentials" / "local_secrets.md"


def _parse_local_secrets_md(path: Path = LOCAL_SECRETS_MD) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for m in re.finditer(r"^-\s+\*\*`([^`]+)`\*\*:\s*`([^`]*)`", text, re.M):
        out[m.group(1)] = m.group(2)
    for m in re.finditer(r"^-\s+\*\*`([^`]+)`\*\*:\s*(.+)$", text, re.M):
        key, val = m.group(1), m.group(2).strip()
        if key not in out and not val.startswith("`"):
            out[key] = val.strip("`")
    return out


def _dbutils_secret(scope: str, key: str) -> str | None:
    try:
        from pyspark.dbutils import DBUtils  # type: ignore
        from pyspark.sql import SparkSession

        spark = SparkSession.getActiveSession()
        if spark is None:
            return None
        return DBUtils(spark).secrets.get(scope, key)
    except Exception:
        try:
            # Databricks notebook global
            import IPython

            ip = IPython.get_ipython()
            if ip is None:
                return None
            dbutils = ip.user_ns.get("dbutils")
            if dbutils is None:
                return None
            return dbutils.secrets.get(scope=scope, key=key)
        except Exception:
            return None


def load_lark_credentials(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    secret_scope: str = DEFAULT_SCOPE,
    base_url: str | None = None,
) -> dict[str, str]:
    local = _parse_local_secrets_md()
    resolved_id = (
        app_id
        or os.environ.get("LARK_APP_ID")
        or _dbutils_secret(secret_scope, "lark_app_id")
        or local.get("lark_app_id")
        or local.get("lark_bot_app_id_databricks")
    )
    resolved_secret = (
        app_secret
        or os.environ.get("LARK_APP_SECRET")
        or _dbutils_secret(secret_scope, "lark_app_secret")
        or local.get("lark_app_secret")
        or local.get("lark_bot_app_secret_databricks")
    )
    resolved_base = (
        base_url
        or os.environ.get("LARK_OPEN_API_BASE")
        or _dbutils_secret(secret_scope, "lark_open_api_base")
        or local.get("lark_open_api_base")
        or DEFAULT_BASE
    )
    if not resolved_id or not resolved_secret:
        raise RuntimeError(
            "Missing Lark app_id/app_secret. Set env, Databricks secrets "
            f"scope={secret_scope}, or fill docs/credentials/local_secrets.md"
        )
    return {
        "app_id": resolved_id,
        "app_secret": resolved_secret,
        "base_url": resolved_base.rstrip("/"),
        "secret_scope": secret_scope,
    }


def get_tenant_access_token(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    secret_scope: str = DEFAULT_SCOPE,
    base_url: str | None = None,
    timeout: int = 30,
) -> str:
    creds = load_lark_credentials(
        app_id=app_id,
        app_secret=app_secret,
        secret_scope=secret_scope,
        base_url=base_url,
    )
    url = f"{creds['base_url']}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url,
        json={"app_id": creds["app_id"], "app_secret": creds["app_secret"]},
        timeout=timeout,
    )
    data: dict[str, Any] = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"tenant_access_token failed: {data}")
    return data["tenant_access_token"]


def auth_headers(**kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {get_tenant_access_token(**kwargs)}"}
