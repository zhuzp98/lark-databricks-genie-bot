"""Wiki node → obj_token / obj_type resolution."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import requests

from .auth import auth_headers, load_lark_credentials


def _extract_token_from_url(url: str) -> tuple[str, str | None]:
    """Return (token, hint_type) from a Lark sheet/wiki/docs URL."""
    path = urlparse(url).path
    # /wiki/<token>, /sheets/<token>, /docx/<token>, /doc/<token>
    m = re.search(r"/(wiki|sheets|docx|doc|base)/([A-Za-z0-9]+)", path)
    if not m:
        # bare token
        if re.fullmatch(r"[A-Za-z0-9]{10,}", url.strip()):
            return url.strip(), None
        raise ValueError(f"Cannot parse Lark URL/token: {url}")
    kind, token = m.group(1), m.group(2)
    hint = {"wiki": "wiki", "sheets": "sheet", "docx": "docx", "doc": "doc", "base": "bitable"}.get(kind)
    return token, hint


def resolve_wiki_node(
    url_or_token: str,
    *,
    secret_scope: str = "lark_integration",
    timeout: int = 30,
) -> dict[str, Any]:
    """Resolve wiki / sheet URL to spreadsheet or doc object token.

    Returns keys: input_token, obj_token, obj_type, node (raw if wiki).
    """
    creds = load_lark_credentials(secret_scope=secret_scope)
    token, hint = _extract_token_from_url(url_or_token)
    headers = auth_headers(secret_scope=secret_scope)

    if hint in (None, "wiki"):
        # Try wiki node get first
        r = requests.get(
            f"{creds['base_url']}/open-apis/wiki/v2/spaces/get_node",
            headers=headers,
            params={"token": token},
            timeout=timeout,
        )
        data = r.json()
        if data.get("code") == 0:
            node = data["data"]["node"]
            return {
                "input_token": token,
                "obj_token": node["obj_token"],
                "obj_type": node["obj_type"],
                "node": node,
            }
        if hint == "wiki":
            raise RuntimeError(f"wiki get_node failed: {data}")

    # Direct sheet / docx token
    obj_type = hint or "sheet"
    return {
        "input_token": token,
        "obj_token": token,
        "obj_type": obj_type,
        "node": None,
    }
