"""Ops alerts to Lark (Job failures / keepalive). Uses Lark App credentials."""

from __future__ import annotations

import os
from typing import Any

from .slog import slog


def notify_lark_text(
    text: str,
    *,
    open_id: str | None = None,
    receive_id_type: str = "open_id",
) -> bool:
    """Best-effort DM / chat notify. Returns True if sent."""
    oid = (
        open_id
        or os.environ.get("LARK_OPS_NOTIFY_OPEN_ID")
        or os.environ.get("LARK_NOTIFY_OPEN_ID")
        or ""
    ).strip()
    if not oid or not (text or "").strip():
        slog("ops_notify_skipped", component="ops", reason="missing open_id or text")
        return False
    try:
        from lark_integration.bridge.im_send import send_text

        send_text(oid, text, receive_id_type=receive_id_type)
        slog("ops_notify_sent", component="ops", receive_id_type=receive_id_type)
        return True
    except Exception as exc:  # noqa: BLE001
        slog("ops_notify_failed", level="error", component="ops", error=str(exc))
        return False


def format_job_alert(
    *,
    title: str,
    action: str,
    app_name: str,
    detail: Any,
) -> str:
    return (
        f"[lark-genie-bot] {title}\n"
        f"action={action} app={app_name}\n"
        f"{detail}"
    )[:3500]
