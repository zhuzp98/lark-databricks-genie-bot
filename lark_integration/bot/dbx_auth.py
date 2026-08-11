"""Resolve Databricks host/token for local CLI and Databricks Apps SP auth."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from typing import Any


def running_in_databricks_app() -> bool:
    return bool(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("DATABRICKS_APP_NAME"))


def default_host() -> str:
    """Workspace URL with scheme. Apps often inject DATABRICKS_HOST without https://."""
    raw = (
        os.environ.get("DATABRICKS_HOST")
        or os.environ.get("DATABRICKS_WORKSPACE_HOST")
        or ""
    ).strip().rstrip("/")
    if not raw:
        raise RuntimeError(
            "DATABRICKS_HOST is not set. In Apps it is injected; locally export "
            "DATABRICKS_HOST=https://<workspace>.cloud.databricks.com"
        )
    if "://" not in raw:
        raw = f"https://{raw}"
    return raw


def _token_via_sdk() -> str | None:
    """WorkspaceClient picks up App SP env (CLIENT_ID/SECRET) or local config."""
    try:
        from databricks.sdk import WorkspaceClient

        profile = (os.environ.get("DATABRICKS_CONFIG_PROFILE") or "").strip()
        if running_in_databricks_app() or not profile:
            w = WorkspaceClient()
        else:
            w = WorkspaceClient(profile=profile)
        headers = w.config.authenticate()
        auth = (headers or {}).get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        if auth:
            return auth
    except Exception as e:
        print(f"[dbx_auth] SDK token failed: {e}")
    return None


def _token_via_cli(profile: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["databricks", "auth", "token", "--profile", profile, "-o", "json"],
            text=True,
        )
        return json.loads(out)["access_token"]
    except Exception as e:
        print(f"[dbx_auth] CLI token failed: {e}")
        return None


@lru_cache(maxsize=1)
def resolve_token(profile: str | None = None) -> str:
    """Bearer token for Databricks REST / MCP calls.

    Order:
      - DATABRICKS_TOKEN
      - In App: SDK (SP client credentials)
      - Local: CLI profile first (avoids SDK OAuth hangs), then SDK
    """
    env_token = os.environ.get("DATABRICKS_TOKEN")
    if env_token:
        return env_token

    if running_in_databricks_app():
        token = _token_via_sdk()
        if token:
            return token
        raise RuntimeError(
            "Cannot resolve Databricks token in App (SP auth). "
            "Ensure the App runtime injects credentials."
        )

    prof = (profile or os.environ.get("DATABRICKS_CONFIG_PROFILE") or "DEFAULT").strip()
    if prof:
        token = _token_via_cli(prof)
        if token:
            return token

    token = _token_via_sdk()
    if token:
        return token

    raise RuntimeError(
        "Cannot resolve Databricks token. Locally set DATABRICKS_TOKEN or "
        "`databricks auth login --profile <name>`."
    )


def clear_token_cache() -> None:
    resolve_token.cache_clear()


def auth_headers(profile: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {resolve_token(profile)}",
        "Content-Type": "application/json",
    }


def list_genie_spaces(profile: str | None = None) -> list[dict[str, str]]:
    """List Genie spaces via REST (Apps-friendly); fall back to CLI locally."""
    host = default_host()
    headers = auth_headers(profile)
    import requests

    try:
        r = requests.get(f"{host}/api/2.0/genie/spaces", headers=headers, timeout=60)
        if r.status_code == 401:
            clear_token_cache()
            headers = auth_headers(profile)
            r = requests.get(f"{host}/api/2.0/genie/spaces", headers=headers, timeout=60)
        r.raise_for_status()
        data: Any = r.json()
        spaces = data.get("spaces") if isinstance(data, dict) else data
        return [
            {"space_id": s["space_id"], "title": s.get("title") or s["space_id"]}
            for s in (spaces or [])
        ]
    except Exception as e:
        if running_in_databricks_app():
            raise
        # Local fallback when REST is blocked (proxy) but CLI works
        prof = (profile or os.environ.get("DATABRICKS_CONFIG_PROFILE") or "DEFAULT").strip()
        print(f"[dbx_auth] REST list-spaces failed ({e}); trying CLI")
        out = subprocess.check_output(
            ["databricks", "genie", "list-spaces", "--profile", prof, "-o", "json"],
            text=True,
        )
        data = json.loads(out)
        spaces = data.get("spaces") if isinstance(data, dict) else data
        return [
            {"space_id": s["space_id"], "title": s.get("title") or s["space_id"]}
            for s in (spaces or [])
        ]
