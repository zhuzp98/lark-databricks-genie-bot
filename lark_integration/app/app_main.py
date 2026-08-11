"""Databricks App entry: HTTP health on DATABRICKS_APP_PORT + Lark WS bot thread.

Phase A MVP — no AppKit UI. Platform requires a process bound to DATABRICKS_APP_PORT.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

# Bootstrap before importing lark_integration.* (supports flattened App upload root).
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
from bootstrap import ensure_package_importable  # noqa: E402

ensure_package_importable()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
import uvicorn  # noqa: E402

from lark_integration.bot.obo_bind import router as obo_router  # noqa: E402
from lark_integration.bot.runtime_lock import RuntimeLock  # noqa: E402
from lark_integration.bot.slog import slog  # noqa: E402
from lark_integration.bot.user_token_store import STORE  # noqa: E402

app = FastAPI(title="lark-genie-bot", version="0.3.0")
app.include_router(obo_router)
_bot_started = False
_bot_error: str | None = None
_runtime_lock: RuntimeLock | None = None


def _status_payload() -> dict:
    lock_status = _runtime_lock.status() if _runtime_lock else {"enabled": False, "held": False}
    return {
        "app": "lark-genie-bot",
        "role": "Lark ↔ Genie One WS bot (OBO + Phase C/D)",
        "bot_started": _bot_started,
        "bot_error": _bot_error,
        "in_databricks_app": bool(os.environ.get("DATABRICKS_APP_PORT")),
        "obo": {
            "bind": "/bind?open_id=&email=",
            "status": "/oauth/status?user_email=",
            "token": "/oauth/token?user_email=",
        },
        "uc_persist": {
            "enabled": os.environ.get("LARK_UC_PERSIST", "1").strip().lower()
            not in ("0", "false", "no", "off"),
            "warehouse_id": os.environ.get("DATABRICKS_WAREHOUSE_ID")
            or os.environ.get("LARK_SQL_WAREHOUSE_ID"),
            "catalog": os.environ.get("LARK_UC_CATALOG", "workspace"),
            "schema": os.environ.get("LARK_UC_SCHEMA", "lark_integration"),
            "tables": ["bot_sessions", "bot_obo_tokens", "bot_runtime_lease"],
        },
        "runtime_lock": lock_status,
        "app_public_url": os.environ.get("APP_PUBLIC_URL") or None,
    }


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> HTMLResponse:
    """Human landing page — opening the App URL alone used to show raw JSON."""
    # Opening `/` triggers Apps login + User authorization consent (needed for genie scope).
    return HTMLResponse(
        """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Lark Genie Bot</title>
<style>
body{font-family:system-ui,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem;line-height:1.5}
a.btn{display:inline-block;margin-top:1rem;padding:0.65rem 1.1rem;background:#1b3139;color:#fff;text-decoration:none;border-radius:6px}
code{background:#f4f4f4;padding:0.1rem 0.35rem}
.note{background:#fff8e6;border:1px solid #f0d78c;padding:0.75rem 1rem;border-radius:6px;margin:1rem 0}
</style></head><body>
<h1>Lark ↔ Databricks Genie</h1>
<div class="note">
<strong>重要：</strong>若绑定页 Token scopes 没有 <code>genie</code>，请用<strong>无痕 / 隐私窗口</strong>打开本页。
必须出现 User authorization 同意框（含 genie / sql）。同意后再点下方绑定。
</div>
<p>你已登录本 App。若弹出 <strong>User authorization</strong>，请同意（需包含 <code>genie</code>）。</p>
<p><a class="btn" href="/bind">绑定 Databricks（OBO）</a></p>
<p style="color:#666;font-size:0.9rem">技术状态 <a href="/status">/status</a> · <a href="/health">/health</a></p>
</body></html>"""
    )


@app.get("/status")
def status() -> dict:
    return _status_payload()


@app.get("/health")
def health() -> dict:
    ok = _bot_started and not _bot_error
    return {
        "status": "ok" if ok else "degraded",
        "bot_started": _bot_started,
        "bot_error": _bot_error,
    }


def _run_lark_ws() -> None:
    global _bot_started, _bot_error
    try:
        from lark_integration.bot.lark_ws import main as lark_main

        _bot_started = True
        slog("bot_thread_enter", component="app")
        lark_main()
    except SystemExit as e:
        _bot_error = str(e)
        _bot_started = False
        slog("bot_thread_exit_lock", level="error", component="app", error=str(e))
    except Exception as e:
        _bot_error = str(e)
        slog("bot_thread_error", level="error", component="app", error=str(e))
        print(f"[app] Lark WS exited with error: {e}")
        raise


def _start_bot_thread() -> None:
    t = threading.Thread(target=_run_lark_ws, name="lark-ws", daemon=True)
    t.start()
    slog("bot_thread_started", component="app")
    print("[app] Lark WS bot thread started")


def _token_cleanup_loop() -> None:
    import time

    while True:
        time.sleep(600)
        n = STORE.cleanup()
        if n:
            slog("obo_cleanup", component="app", expired=n)
            print(f"[obo] cleaned {n} expired user token(s)")


def main() -> None:
    global _runtime_lock
    inbound = os.environ.get("LARK_INBOUND_DIR", "/tmp/lark_inbound")
    Path(inbound).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LARK_INBOUND_DIR", inbound)

    _runtime_lock = RuntimeLock()
    # Lock is acquired inside lark_ws.main(); status endpoint still exposes holder intent.
    threading.Thread(target=_token_cleanup_loop, name="obo-cleanup", daemon=True).start()
    _start_bot_thread()

    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    slog(
        "app_http_start",
        component="app",
        port=port,
        app_public_url=os.environ.get("APP_PUBLIC_URL"),
    )
    print(f"[app] health server on 0.0.0.0:{port}")
    print(f"[app] APP_PUBLIC_URL={os.environ.get('APP_PUBLIC_URL') or '(unset)'}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
