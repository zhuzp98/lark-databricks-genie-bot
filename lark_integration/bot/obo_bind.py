"""Databricks Apps OBO bind + Slack-compatible /oauth status & token APIs.

Bind flow (Free Edition):
  User opens GET /bind?open_id=&email= while logged into Databricks.
  Platform injects x-forwarded-access-token + x-forwarded-email.
  We store the token keyed by email for the Lark bot thread.
"""

from __future__ import annotations

import base64
import html
import json
import os
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from lark_integration.bot.user_token_store import STORE

router = APIRouter()

# Genie One MCP requires OAuth scope `genie` (managed MCP docs).
_REQUIRED_GENIE_SCOPES = frozenset({"genie", "dashboards.genie"})


def app_public_url() -> str:
    return (os.environ.get("APP_PUBLIC_URL") or "").rstrip("/")


def build_bind_url(*, open_id: str, email: str) -> str:
    base = app_public_url()
    if not base:
        raise RuntimeError(
            "APP_PUBLIC_URL is not set; cannot build Databricks bind link"
        )
    q = urlencode({"open_id": open_id or "", "email": email or ""})
    return f"{base}/bind?{q}"


def _header(request: Request, name: str) -> str | None:
    v = request.headers.get(name) or request.headers.get(name.lower())
    return (v or "").strip() or None


def jwt_scopes(access_token: str) -> list[str]:
    """Read scope claims from a JWT without verifying the signature."""
    try:
        part = access_token.split(".")[1]
        part += "=" * ((4 - len(part) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return []
    raw = payload.get("scope") or payload.get("scp") or []
    if isinstance(raw, str):
        return [s for s in raw.replace(",", " ").split() if s]
    if isinstance(raw, list):
        return [str(s) for s in raw]
    return []


def has_genie_scope(scopes: list[str]) -> bool:
    return bool(_REQUIRED_GENIE_SCOPES.intersection(scopes))


def _success_html(email: str, expires_in: int, scopes: list[str]) -> str:
    safe = html.escape(email)
    scope_txt = html.escape(", ".join(scopes) if scopes else "(none in token)")
    genie_ok = has_genie_scope(scopes)
    warn = ""
    if not genie_ok:
        warn = (
            "<p style='color:#b00020'><strong>警告：</strong>当前 token 没有 "
            "<code>genie</code> / <code>dashboards.genie</code> scope。"
            "Genie One MCP 会返回 403。请先打开 App 首页重新同意 User authorization，"
            "再访问本绑定链接。</p>"
        )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Databricks 绑定成功</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 36rem; margin: 3rem auto; padding: 0 1rem; }}
code {{ background:#f4f4f4; padding:0.1rem 0.3rem; }}
</style></head><body>
<h1>绑定成功</h1>
<p>用户：<strong>{safe}</strong></p>
<p>Token 已保存，约 {expires_in} 秒内有效。</p>
<p>Token scopes：<code>{scope_txt}</code></p>
{warn}
<p>请关闭此窗口，回到 Lark 继续提问。</p>
<p style="color:#666;font-size:0.9rem">Free Edition：App 重启或约 24 小时停机后需重新绑定。</p>
</body></html>"""


def _error_html(message: str) -> str:
    safe = html.escape(message)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>绑定失败</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 36rem; margin: 3rem auto; padding: 0 1rem; }}
</style></head><body>
<h1>绑定失败</h1>
<p>{safe}</p>
<p>请确认：已在 Databricks 登录本 App，且 App 已开启 User authorization（含 genie scopes）。</p>
</body></html>"""


@router.get("/bind", response_class=HTMLResponse)
def bind(
    request: Request,
    open_id: str | None = Query(None),
    email: str | None = Query(None),
) -> HTMLResponse:
    """Capture Apps OBO token and store by Databricks / Lark email."""
    token = _header(request, "x-forwarded-access-token")
    hdr_email = _header(request, "x-forwarded-email")
    preferred = _header(request, "x-forwarded-preferred-username")

    resolved = (hdr_email or preferred or email or "").strip().lower()
    if not token:
        return HTMLResponse(
            _error_html(
                "未收到 x-forwarded-access-token。"
                "请在 Databricks 工作区打开此链接（不要复制到未登录环境），"
                "并确认 App 已启用 User authorization。"
            ),
            status_code=401,
        )
    if not resolved or "@" not in resolved:
        return HTMLResponse(
            _error_html("无法识别用户邮箱（缺少 x-forwarded-email）。"),
            status_code=400,
        )

    if email and email.strip().lower() != resolved:
        print(
            f"[obo] email mismatch query={email.strip().lower()} header={resolved}; "
            "storing under Databricks identity"
        )

    scopes = jwt_scopes(token)
    print(
        f"[obo] bound email={resolved} open_id={open_id or ''} "
        f"scopes={scopes} genie_ok={has_genie_scope(scopes)}"
    )
    entry = STORE.put(resolved, token, open_id=open_id or None)
    return HTMLResponse(_success_html(entry.email, entry.expires_in, scopes))


@router.get("/oauth/status")
def oauth_status(user_email: str = Query(...)) -> dict[str, Any]:
    st = STORE.status(user_email)
    entry = STORE.get(user_email)
    if entry:
        scopes = jwt_scopes(entry.access_token)
        st["scopes"] = scopes
        st["genie_scope_ok"] = has_genie_scope(scopes)
    return st


@router.get("/oauth/token")
def oauth_token(user_email: str = Query(...)) -> JSONResponse:
    entry = STORE.get(user_email)
    if not entry:
        login: dict[str, Any] = {
            "authenticated": False,
            "user_email": user_email.strip().lower(),
            "message": "not authenticated or expired",
        }
        try:
            login["login_url"] = build_bind_url(open_id="", email=user_email)
        except RuntimeError:
            pass
        return JSONResponse(login, status_code=401)
    scopes = jwt_scopes(entry.access_token)
    return JSONResponse(
        {
            "authenticated": True,
            "access_token": entry.access_token,
            "token_type": "Bearer",
            "expires_in": entry.expires_in,
            "user_email": entry.email,
            "scopes": scopes,
            "genie_scope_ok": has_genie_scope(scopes),
        }
    )


@router.get("/oauth/login")
def oauth_login(
    user_email: str = Query(...),
    open_id: str = Query(""),
) -> dict[str, Any]:
    url = build_bind_url(open_id=open_id, email=user_email)
    return {
        "login_url": url,
        "user_email": user_email.strip().lower(),
        "message": "Open the URL while logged into Databricks to bind",
    }
