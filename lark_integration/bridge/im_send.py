"""Outbound Lark IM: text, interactive card, file."""

from __future__ import annotations

import io
import json
from typing import Any

import requests

from .auth import auth_headers, load_lark_credentials


def _send_message(
    *,
    receive_id: str,
    msg_type: str,
    content: dict[str, Any],
    receive_id_type: str = "chat_id",
    secret_scope: str = "lark_integration",
) -> str:
    creds = load_lark_credentials(secret_scope=secret_scope)
    headers = {**auth_headers(secret_scope=secret_scope), "Content-Type": "application/json"}
    r = requests.post(
        f"{creds['base_url']}/open-apis/im/v1/messages",
        headers=headers,
        params={"receive_id_type": receive_id_type},
        json={
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        },
        timeout=60,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"send message failed: {data}")
    return data["data"]["message_id"]


def send_text(
    receive_id: str,
    text: str,
    *,
    receive_id_type: str = "chat_id",
    secret_scope: str = "lark_integration",
) -> str:
    return _send_message(
        receive_id=receive_id,
        msg_type="text",
        content={"text": text},
        receive_id_type=receive_id_type,
        secret_scope=secret_scope,
    )


def send_text_chunks(
    receive_id: str,
    text: str,
    *,
    chunk_size: int = 3500,
    receive_id_type: str = "chat_id",
    secret_scope: str = "lark_integration",
) -> list[str]:
    """Send long Genie answers as multiple text bubbles (no card table limits)."""
    body = text or "(empty)"
    ids: list[str] = []
    for i in range(0, len(body), chunk_size):
        ids.append(
            send_text(
                receive_id,
                body[i : i + chunk_size],
                receive_id_type=receive_id_type,
                secret_scope=secret_scope,
            )
        )
    return ids


def send_card(
    receive_id: str,
    card: dict[str, Any],
    *,
    receive_id_type: str = "chat_id",
    secret_scope: str = "lark_integration",
) -> str:
    """card is a Lark interactive card JSON object."""
    return _send_message(
        receive_id=receive_id,
        msg_type="interactive",
        content=card,
        receive_id_type=receive_id_type,
        secret_scope=secret_scope,
    )


def upload_file(
    file_bytes: bytes,
    file_name: str,
    *,
    file_type: str = "stream",
    secret_scope: str = "lark_integration",
) -> str:
    creds = load_lark_credentials(secret_scope=secret_scope)
    headers = auth_headers(secret_scope=secret_scope)
    r = requests.post(
        f"{creds['base_url']}/open-apis/im/v1/files",
        headers=headers,
        data={"file_type": file_type, "file_name": file_name},
        files={"file": (file_name, io.BytesIO(file_bytes), "application/octet-stream")},
        timeout=120,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"upload file failed: {data}")
    return data["data"]["file_key"]


def send_file(
    receive_id: str,
    file_bytes: bytes,
    file_name: str,
    *,
    receive_id_type: str = "chat_id",
    secret_scope: str = "lark_integration",
) -> str:
    file_key = upload_file(file_bytes, file_name, secret_scope=secret_scope)
    return _send_message(
        receive_id=receive_id,
        msg_type="file",
        content={"file_key": file_key},
        receive_id_type=receive_id_type,
        secret_scope=secret_scope,
    )


def simple_result_card(title: str, body: str, sql: str | None = None) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body[:4000]}},
    ]
    if sql:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**SQL**\n```\n{sql[:1500]}\n```"},
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:80]},
            "template": "blue",
        },
        "elements": elements,
    }
