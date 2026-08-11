"""Format Genie Space / Genie One replies into Lark cards."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from .cards import answer_card, content_card
from .dbx_auth import default_host, running_in_databricks_app

# Lark cards reject too many markdown tables (ErrCode 11310 / code 230099).
# Docs: ≤4 tables per markdown component; card-wide hard limit is ~5.
_MAX_CARD_TABLES = 4
_TABLE_SEP = re.compile(r"^\|[\s:\-|]+\|\s*$")


def _extract_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for att in message.get("attachments") or []:
        text = (att.get("text") or {}).get("content")
        if text:
            parts.append(text)
        desc = (att.get("query") or {}).get("description")
        if desc and desc not in parts:
            parts.append(desc)
    if not parts:
        parts.append(message.get("content") or "(no text attachment)")
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "\n\n".join(out)


def _extract_sql(message: dict[str, Any]) -> str | None:
    for att in message.get("attachments") or []:
        q = (att.get("query") or {}).get("query")
        if q:
            return q
    return None


def _md_cell(value: Any) -> str:
    """Escape pipe/newlines so GFM tables stay valid inside Lark markdown."""
    if value is None:
        return ""
    s = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return s


def _table_to_markdown(table: dict[str, Any] | None, max_rows: int = 20) -> str:
    """Build a GFM table for Card JSON 2.0 `markdown` (paginates past ~5 rows in Lark UI)."""
    if not table:
        return ""
    manifest = table.get("statement_response") or table.get("result") or table
    if not isinstance(manifest, dict):
        return ""
    data_array = (manifest.get("result") or {}).get("data_array") or manifest.get("data_array")
    if not data_array:
        return ""
    cols = (manifest.get("manifest") or {}).get("schema", {}).get("columns") or manifest.get("columns")
    columns = [c.get("name") if isinstance(c, dict) else str(c) for c in cols] if cols else [
        f"c{i}" for i in range(len(data_array[0]))
    ]
    columns = [_md_cell(c) for c in columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in data_array[:max_rows]:
        lines.append("| " + " | ".join(_md_cell(v) for v in row) + " |")
    if len(data_array) > max_rows:
        lines.append(f"\n_… {len(data_array) - max_rows} more rows_")
    return "\n".join(lines)


def table_to_csv_bytes(table: dict[str, Any] | None) -> bytes | None:
    if not table:
        return None
    manifest = table.get("statement_response") or table.get("result") or table
    if not isinstance(manifest, dict):
        return None
    data_array = (manifest.get("result") or {}).get("data_array") or manifest.get("data_array")
    if not data_array:
        return None
    cols = (manifest.get("manifest") or {}).get("schema", {}).get("columns") or manifest.get("columns")
    columns = [c.get("name") if isinstance(c, dict) else str(c) for c in cols] if cols else [
        f"c{i}" for i in range(len(data_array[0]))
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    w.writerows(data_array)
    return buf.getvalue().encode("utf-8-sig")


def _is_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    if not lines[i].lstrip().startswith("|"):
        return False
    return bool(_TABLE_SEP.match(lines[i + 1].strip()))


def _iter_md_segments(text: str) -> list[tuple[str, bool]]:
    """Split markdown into (segment, is_gfm_table) pieces."""
    lines = text.split("\n")
    i = 0
    buf: list[str] = []
    out: list[tuple[str, bool]] = []

    def flush_buf() -> None:
        nonlocal buf
        if buf:
            out.append(("\n".join(buf), False))
            buf = []

    while i < len(lines):
        if _is_table_start(lines, i):
            flush_buf()
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out.append(("\n".join(lines[i:j]), True))
            i = j
            continue
        buf.append(lines[i])
        i += 1
    flush_buf()
    return out


def limit_markdown_tables(text: str, max_tables: int = _MAX_CARD_TABLES) -> str:
    """Keep at most `max_tables` GFM tables; fence the rest (avoids Lark 11310)."""
    if not text or max_tables < 0:
        return text
    segs = _iter_md_segments(text)
    table_count = sum(1 for _, is_t in segs if is_t)
    if table_count <= max_tables:
        return text
    kept = 0
    parts: list[str] = []
    for content, is_table in segs:
        if not is_table:
            parts.append(content)
            continue
        kept += 1
        if kept <= max_tables:
            parts.append(content)
        else:
            parts.append(f"```\n{content.strip()}\n```")
    if kept > max_tables:
        parts.append(
            f"_（Lark 卡片最多显示 {max_tables} 张表，其余 {kept - max_tables} 张已改为纯文本）_"
        )
    return "\n\n".join(p for p in parts if p)


def explore_link_for_user(
    deep_link: str | None,
    *,
    user_identity: bool = False,
) -> tuple[str | None, str]:
    """Pick a Databricks URL end users can open.

    With OBO (user_identity=True), MCP deep_link points at the caller's own
    Genie One thread. SP-created conversations are invisible to humans — in
    that case fall back to Genie One home when running inside the App.
    """
    if deep_link and user_identity:
        if deep_link.startswith("/"):
            return f"{default_host().rstrip('/')}{deep_link}", "Explore in Databricks"
        return deep_link, "Explore in Databricks"
    if running_in_databricks_app() and not user_identity:
        return f"{default_host()}/one/", "Open Genie One"
    if deep_link:
        if deep_link.startswith("/"):
            return f"{default_host().rstrip('/')}{deep_link}", "Explore in Databricks"
        return deep_link, "Explore in Databricks"
    return None, "Explore in Databricks"


def split_body_into_card_parts(
    text: str, max_tables: int = _MAX_CARD_TABLES
) -> list[str]:
    """Split Genie markdown so each part has ≤ max_tables GFM tables (multi-card)."""
    text = (text or "").strip() or "(empty)"
    segs = _iter_md_segments(text)
    table_count = sum(1 for _, is_t in segs if is_t)
    if table_count <= max_tables:
        return [text]

    parts: list[str] = []
    buf: list[str] = []
    tables_in_buf = 0
    for content, is_table in segs:
        if is_table and tables_in_buf >= max_tables:
            parts.append("\n\n".join(buf).strip())
            buf = []
            tables_in_buf = 0
        buf.append(content)
        if is_table:
            tables_in_buf += 1
    if buf:
        parts.append("\n\n".join(buf).strip())
    return [p for p in parts if p]


def _cards_from_body(
    *,
    title: str,
    body: str,
    agent_label: str,
    deep_link: str | None = None,
    user_identity: bool = False,
) -> list[dict[str, Any]]:
    url, label = explore_link_for_user(deep_link, user_identity=user_identity)
    parts = split_body_into_card_parts(body)
    n = len(parts)
    return [
        content_card(
            title=title,
            body=part,
            agent_label=agent_label,
            deep_link=url,
            deep_link_label=label,
            part=i + 1,
            parts=n,
        )
        for i, part in enumerate(parts)
    ]


def format_space_reply(
    ask_result: dict[str, Any],
    agent_label: str,
    *,
    user_identity: bool = True,
) -> dict[str, Any]:
    message = ask_result["message"]
    table = ask_result.get("table")
    text = _for_lark_markdown(_extract_text(message))
    md_table = _table_to_markdown(table)
    body = text
    if md_table:
        body = f"{text}\n\n{md_table}" if text else md_table
    cards = _cards_from_body(
        title="Genie Agent",
        body=body or "(empty)",
        agent_label=agent_label,
        user_identity=user_identity,
    )
    out: dict[str, Any] = {"cards": cards, "card": cards[0]}
    csv_bytes = table_to_csv_bytes(table)
    if csv_bytes and len(csv_bytes) > 500:
        out["csv_bytes"] = csv_bytes
        out["csv_name"] = f"genie_{ask_result.get('message_id', 'result')}.csv"
    return out


def _strip_agent_directives(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.S)
    return text.strip()


def _for_lark_markdown(text: str) -> str:
    """Keep Genie markdown intact for Card JSON 2.0 rich text (tables/headings/code).

    Only strips Genie agent control chrome; does not strip fences or headings.
    Waiting/search bubbles stay plain text elsewhere in lark_ws.
    """
    text = _strip_agent_directives(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_one_reply(
    ask_result: dict[str, Any],
    *,
    user_identity: bool = True,
) -> dict[str, Any]:
    answer = ask_result.get("final_answer") or ask_result.get("text") or ""
    body = _for_lark_markdown(answer) or "(empty answer)"
    cards = _cards_from_body(
        title="Genie One",
        body=body,
        agent_label="Genie One (Ontology)",
        deep_link=ask_result.get("deep_link"),
        user_identity=user_identity,
    )
    return {"cards": cards, "card": cards[0]}


def format_one_text(ask_result: dict[str, Any]) -> dict[str, Any]:
    """Plain-text Genie One reply (fallback; text bubbles do not render markdown)."""
    answer = ask_result.get("final_answer") or ask_result.get("text") or ""
    body = _for_lark_markdown(answer) or "(empty answer)"
    parts = ["[Genie One (Ontology)]", body]
    if ask_result.get("deep_link"):
        parts.append(f"Explore: {ask_result['deep_link']}")
    return {"text": "\n\n".join(parts)}


def format_space_text(ask_result: dict[str, Any], agent_label: str) -> dict[str, Any]:
    message = ask_result["message"]
    table = ask_result.get("table")
    text = _for_lark_markdown(_extract_text(message))
    md_table = _table_to_markdown(table)
    body = text
    if md_table:
        body = f"{text}\n\n{md_table}" if text else md_table
    out: dict[str, Any] = {"text": f"[{agent_label}]\n\n{body or '(empty answer)'}"}
    csv_bytes = table_to_csv_bytes(table)
    if csv_bytes and len(csv_bytes) > 500:
        out["csv_bytes"] = csv_bytes
        out["csv_name"] = f"genie_{ask_result.get('message_id', 'result')}.csv"
    return out


# backward-compatible name used by older code
def format_genie_reply(ask_result: dict[str, Any], agent_label: str = "Genie Agent") -> dict[str, Any]:
    if ask_result.get("mode") == "genie_one":
        return format_one_reply(ask_result)
    return format_space_reply(ask_result, agent_label)
