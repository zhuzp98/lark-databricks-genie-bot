# Databricks notebook source
# MAGIC %md
# MAGIC # Lark Genie Bot — App keep-alive (Phase B + D)
# MAGIC
# MAGIC Controlled stop/start for Free Edition ~24h App pause policy.
# MAGIC
# MAGIC Recommended schedule (UTC):
# MAGIC - **stop** at `23:50` — begin overnight lake refresh window
# MAGIC - **start** at `00:50` — resume after ~60 minutes
# MAGIC
# MAGIC Phase D: structured JSON logs + optional Lark DM on failure (`notify_open_id`).
# MAGIC Job-level email_notifications also fire on failure.

# COMMAND ----------

dbutils.widgets.dropdown("action", "start", ["start", "stop", "status"], "action")
dbutils.widgets.text("app_name", "lark-genie-bot", "app_name")
dbutils.widgets.text("wait_seconds", "600", "wait_seconds (start only)")
dbutils.widgets.text(
    "notify_open_id",
    "ou_YOUR_LARK_OPEN_ID",
    "notify_open_id (Lark DM on failure; empty=skip)",
)

action = dbutils.widgets.get("action").strip().lower()
app_name = dbutils.widgets.get("app_name").strip()
wait_seconds = int(dbutils.widgets.get("wait_seconds") or "600")
notify_open_id = (dbutils.widgets.get("notify_open_id") or "").strip()

# COMMAND ----------

import json
import sys
import time
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient


def slog(event: str, level: str = "info", **fields):
    payload = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "level": level,
        "event": event,
        "service": "lark-genie-bot",
        "component": "keepalive",
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def notify_failure(detail: str) -> None:
    if not notify_open_id:
        slog("ops_notify_skipped", reason="empty notify_open_id")
        return
    try:
        # Prefer workspace package if App source is on path; else raw REST via secrets.
        # Adjust to where you uploaded this repo in the Workspace.
        pkg_root = "/Workspace/Users/YOUR_DATABRICKS_USER@example.com/lark_genie_bot"
        if pkg_root not in sys.path:
            sys.path.insert(0, pkg_root)
        from lark_integration.bot.ops_notify import format_job_alert, notify_lark_text

        msg = format_job_alert(
            title="Keep-alive FAILED",
            action=action,
            app_name=app_name,
            detail=detail,
        )
        ok = notify_lark_text(msg, open_id=notify_open_id)
        slog("ops_notify_result", ok=ok)
    except Exception as e:
        slog("ops_notify_failed", level="error", error=str(e))


w = WorkspaceClient()
slog("keepalive_begin", action=action, app_name=app_name, wait_seconds=wait_seconds)


def app_state(name: str) -> dict:
    app = w.apps.get(name)
    status = getattr(app, "app_status", None)
    compute = getattr(app, "compute_status", None)
    return {
        "name": name,
        "url": getattr(app, "url", None),
        "app_status": getattr(status, "state", None) or str(status),
        "app_message": getattr(status, "message", None),
        "compute_status": getattr(compute, "state", None) or str(compute),
        "compute_message": getattr(compute, "message", None),
    }


def wait_until(name: str, want_running: bool, timeout: int) -> dict:
    deadline = time.time() + timeout
    last = app_state(name)
    while time.time() < deadline:
        last = app_state(name)
        state = str(last.get("app_status") or "").upper()
        slog("keepalive_wait", app_status=state, compute=str(last.get("compute_status")))
        if want_running and "RUNNING" in state:
            return last
        if not want_running and ("STOPPED" in state or "UNAVAILABLE" in state):
            return last
        time.sleep(10)
    raise TimeoutError(f"timeout waiting for app={name} running={want_running}; last={last}")


try:
    before = app_state(app_name)
    slog("keepalive_before", **{k: str(v) for k, v in before.items()})

    if action == "status":
        dbutils.notebook.exit(json.dumps(before, default=str))

    elif action == "stop":
        slog("keepalive_stop_call", app_name=app_name)
        w.apps.stop(app_name)
        after = wait_until(app_name, want_running=False, timeout=max(wait_seconds, 300))
        slog("keepalive_stop_ok", **{k: str(v) for k, v in after.items()})
        dbutils.notebook.exit(f"stopped {app_name}: {after}")

    elif action == "start":
        slog("keepalive_start_call", app_name=app_name)
        w.apps.start(app_name)
        after = wait_until(app_name, want_running=True, timeout=wait_seconds)
        slog(
            "keepalive_start_ok",
            note="OBO tokens may need re-bind if expired; Phase C restores unexpired from UC",
            **{k: str(v) for k, v in after.items()},
        )
        dbutils.notebook.exit(f"started {app_name}: {after}")

    else:
        raise ValueError(f"unknown action={action!r}; use start|stop|status")

except Exception as e:
    slog("keepalive_failed", level="error", action=action, app_name=app_name, error=str(e))
    notify_failure(str(e))
    raise
