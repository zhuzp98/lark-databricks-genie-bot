"""Inbound file download → local / Volume path."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..bridge.auth import auth_headers, load_lark_credentials


def download_message_resource(
    message_id: str,
    file_key: str,
    *,
    resource_type: str = "file",
    secret_scope: str = "lark_integration",
) -> bytes:
    creds = load_lark_credentials(secret_scope=secret_scope)
    headers = auth_headers(secret_scope=secret_scope)
    r = requests.get(
        f"{creds['base_url']}/open-apis/im/v1/messages/{message_id}/resources/{file_key}",
        headers=headers,
        params={"type": resource_type},
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"download resource failed: {r.status_code} {r.text[:300]}")
    return r.content


def ingest_inbound_file(
    *,
    message_id: str,
    file_key: str,
    file_name: str,
    volume_path: str,
    resource_type: str = "file",
    secret_scope: str = "lark_integration",
    meta_table: str | None = None,
    spark=None,
    chat_id: str | None = None,
) -> str:
    raw = download_message_resource(
        message_id, file_key, resource_type=resource_type, secret_scope=secret_scope
    )
    out_dir = Path(volume_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = file_name.replace("/", "_")
    out_file = out_dir / f"{message_id}_{safe_name}"
    out_file.write_bytes(raw)
    print(f"[ok] inbound file -> {out_file} bytes={len(raw)}")
    if meta_table and spark is not None:
        spark.createDataFrame(
            [
                (
                    message_id,
                    chat_id,
                    file_key,
                    safe_name,
                    str(out_file),
                    len(raw),
                    datetime.now(timezone.utc).isoformat(),
                )
            ],
            ["message_id", "chat_id", "file_key", "file_name", "path", "bytes", "ingested_at"],
        ).write.format("delta").mode("append").saveAsTable(meta_table)
    return str(out_file)
