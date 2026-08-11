"""Resolve Lark open_id → enterprise email (aligned with Databricks username).

Requires Lark app scope ``contact:user.email:readonly`` (and contact permission
range covering the user). If email is unavailable, OBO still works: bind via
``/bind?open_id=...`` and key tokens by open_id using Databricks
``x-forwarded-email``.
"""

from __future__ import annotations

import re

import requests

from lark_integration.bridge.auth import auth_headers, load_lark_credentials
from lark_integration.bot.user_token_store import STORE

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LarkEmailError(RuntimeError):
    pass


def _pick_email(user: dict) -> str | None:
    for key in ("email", "enterprise_email", "enterprise_mail", "mail"):
        val = (user.get(key) or "").strip()
        if val and _EMAIL_RE.match(val):
            return val.lower()
    # Some tenants put email in user_id
    uid = (user.get("user_id") or "").strip()
    if uid and _EMAIL_RE.match(uid):
        return uid.lower()
    return None


def email_for_open_id(
    open_id: str,
    *,
    secret_scope: str = "lark_integration",
    use_cache: bool = True,
) -> str:
    """Fetch user email via Contact API. Caches open_id → email in STORE."""
    if not open_id:
        raise LarkEmailError("open_id is required")

    if use_cache:
        cached = STORE.email_for_open_id(open_id)
        if cached:
            return cached

    creds = load_lark_credentials(secret_scope=secret_scope)
    url = f"{creds['base_url']}/open-apis/contact/v3/users/{open_id}"
    r = requests.get(
        url,
        headers=auth_headers(secret_scope=secret_scope),
        params={"user_id_type": "open_id"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise LarkEmailError(f"contact API failed: {data}")

    user = (data.get("data") or {}).get("user") or {}
    email = _pick_email(user)
    if not email:
        raise LarkEmailError(
            f"no email on Lark user open_id={open_id}. "
            "Enable scope contact:user.email:readonly (and contact range), "
            "or send: 绑定 your@email.com"
        )

    STORE.remember_open_id(open_id, email)
    return email
