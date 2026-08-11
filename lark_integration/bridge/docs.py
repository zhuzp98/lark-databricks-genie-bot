"""Export Lark Docs (docx) to Markdown and optionally write to a local/Volume path."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import requests

from .auth import auth_headers, load_lark_credentials
from .wiki import resolve_wiki_node


def fetch_doc_markdown(doc_token: str, *, secret_scope: str = "lark_integration") -> str:
    creds = load_lark_credentials(secret_scope=secret_scope)
    headers = auth_headers(secret_scope=secret_scope)
    r = requests.get(
        f"{creds['base_url']}/open-apis/docs/v1/content",
        headers=headers,
        params={"doc_token": doc_token, "doc_type": "docx", "content_type": "markdown"},
        timeout=120,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"docs content failed: {data}")
    return data["data"]["content"]


def docs_to_markdown(
    url_or_token: str,
    volume_path: str,
    *,
    meta_table: str | None = None,
    secret_scope: str = "lark_integration",
    spark=None,
) -> str:
    """Write markdown file under volume_path; return absolute path string.

    volume_path can be a UC Volume path (/Volumes/...) or a local directory.
    """
    resolved = resolve_wiki_node(url_or_token, secret_scope=secret_scope)
    obj_token = resolved["obj_token"]
    obj_type = str(resolved["obj_type"])
    if obj_type not in ("docx", "doc", "sheet") and "doc" not in obj_type:
        # wiki may return docx
        pass
    md = fetch_doc_markdown(obj_token, secret_scope=secret_scope)

    out_dir = Path(volume_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{obj_token}.md"
    out_file.write_text(md, encoding="utf-8")
    print(f"[ok] wrote {out_file} bytes={out_file.stat().st_size}")

    if meta_table and spark is not None:
        ts = datetime.now(timezone.utc).isoformat()
        spark.createDataFrame(
            [(obj_token, obj_type, str(out_file), ts, len(md))],
            ["doc_token", "obj_type", "path", "synced_at", "chars"],
        ).write.format("delta").mode("append").saveAsTable(meta_table)
        print(f"[ok] appended meta -> {meta_table}")

    return str(out_file)
