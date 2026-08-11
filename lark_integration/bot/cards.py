"""Lark interactive cards (Card JSON 2.0): answer / agent picker / controls.

Answer bodies use `tag: markdown` so Genie headings, tables, lists, and code
blocks render correctly. Interactive buttons use `behaviors` callbacks
(compatible with card.action.trigger) plus legacy `value` for older clients.
"""

from __future__ import annotations

import re
from typing import Any

# Lark markdown component content is safer under ~4k; split long Genie answers.
_MD_CHUNK = 3500
# Official docs: ≤4 tables per markdown component.
_MAX_TABLES_PER_MD = 4
_TABLE_SEP = re.compile(r"^\|[\s:\-|]+\|\s*$")


def _count_gfm_tables(text: str) -> int:
    lines = text.split("\n")
    n = 0
    i = 0
    while i + 1 < len(lines):
        if lines[i].lstrip().startswith("|") and _TABLE_SEP.match(lines[i + 1].strip()):
            n += 1
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                i += 1
            continue
        i += 1
    return n


def _split_by_table_budget(text: str, max_tables: int = _MAX_TABLES_PER_MD) -> list[str]:
    if _count_gfm_tables(text) <= max_tables:
        return [text]
    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    tables_in_buf = 0
    i = 0

    def flush() -> None:
        nonlocal buf, tables_in_buf
        if buf:
            chunks.append("\n".join(buf).strip())
            buf = []
            tables_in_buf = 0

    while i < len(lines):
        if (
            i + 1 < len(lines)
            and lines[i].lstrip().startswith("|")
            and _TABLE_SEP.match(lines[i + 1].strip())
        ):
            if tables_in_buf >= max_tables:
                flush()
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                buf.append(lines[j])
                j += 1
            tables_in_buf += 1
            i = j
            continue
        buf.append(lines[i])
        i += 1
    flush()
    return [c for c in chunks if c]


def _callback_button(
    *,
    label: str,
    value: dict[str, Any],
    btn_type: str = "default",
    width: str = "default",
) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": btn_type,
        "width": width,
        "behaviors": [{"type": "callback", "value": value}],
        # Legacy field — some SDK/client paths still surface this as action.value
        "value": value,
    }


def control_buttons() -> list[dict[str, Any]]:
    return [
        {
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        _callback_button(
                            label="Reset",
                            value={"action": "reset"},
                            btn_type="danger",
                            width="fill",
                        )
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        _callback_button(
                            label="Switch Agent",
                            value={"action": "switch"},
                            btn_type="default",
                            width="fill",
                        )
                    ],
                },
            ],
        }
    ]


def _markdown_chunks(content: str) -> list[dict[str, Any]]:
    text = (content or "").strip() or "(empty)"
    size_chunks: list[str] = []
    if len(text) <= _MD_CHUNK:
        size_chunks = [text]
    else:
        rest = text
        while rest:
            if len(rest) <= _MD_CHUNK:
                size_chunks.append(rest)
                break
            cut = rest.rfind("\n\n", 0, _MD_CHUNK)
            if cut < _MD_CHUNK // 2:
                cut = rest.rfind("\n", 0, _MD_CHUNK)
            if cut < _MD_CHUNK // 2:
                cut = _MD_CHUNK
            size_chunks.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()

    pieces: list[str] = []
    for chunk in size_chunks:
        pieces.extend(_split_by_table_budget(chunk))
    return [{"tag": "markdown", "content": c, "text_align": "left"} for c in pieces if c]


def _card_v2(
    *,
    title: str,
    template: str,
    elements: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:80]},
            "template": template,
        },
        "body": {"elements": elements},
    }


def content_card(
    *,
    title: str,
    body: str,
    agent_label: str | None = None,
    deep_link: str | None = None,
    deep_link_label: str = "Explore in Databricks",
    part: int | None = None,
    parts: int | None = None,
) -> dict[str, Any]:
    """Markdown answer card without Reset/Switch (those live on Bot Menu)."""
    header = title[:80]
    if part is not None and parts is not None and parts > 1:
        header = f"{title[:60]} ({part}/{parts})"
    elements: list[dict[str, Any]] = []
    if agent_label and (part is None or part == 1):
        elements.append(
            {
                "tag": "markdown",
                "content": f"**Agent:** {agent_label}",
                "text_align": "left",
            }
        )
    elements.extend(_markdown_chunks(body))
    if deep_link and (part is None or part == parts):
        elements.append(
            {
                "tag": "markdown",
                "content": f"[{deep_link_label}]({deep_link})",
                "text_align": "left",
            }
        )
    return _card_v2(title=header, template="blue", elements=elements)


def answer_card(
    *,
    title: str,
    body: str,
    agent_label: str,
    deep_link: str | None = None,
) -> dict[str, Any]:
    """Legacy card with control buttons (kept for older callers)."""
    card = content_card(
        title=title, body=body, agent_label=agent_label, deep_link=deep_link
    )
    # Append controls for backward compatibility
    elements = card["body"]["elements"]
    elements.append({"tag": "hr"})
    elements.extend(control_buttons())
    return card


def agent_picker_card(
    spaces: list[dict[str, str]],
    current_label: str | None = None,
    lang: str = "zh",
) -> dict[str, Any]:
    """spaces: [{space_id, title}, ...]"""
    from .i18n import t

    note = t("picker_current", lang, label=current_label) if current_label else ""
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"{note}{t('picker_body', lang)}",
            "text_align": "left",
        },
        _callback_button(
            label="Genie One (Ontology)",
            value={"action": "select_agent", "mode": "genie_one"},
            btn_type="primary",
            width="fill",
        ),
    ]
    row: list[dict[str, Any]] = []
    for sp in spaces:
        row.append(
            _callback_button(
                label=(sp["title"] or sp["space_id"])[:40],
                value={
                    "action": "select_agent",
                    "mode": "space",
                    "space_id": sp["space_id"],
                    "title": sp.get("title") or sp["space_id"],
                },
                btn_type="default",
                width="fill",
            )
        )
        if len(row) >= 2:
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "8px",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [btn],
                        }
                        for btn in row
                    ],
                }
            )
            row = []
    if row:
        elements.append(
            {
                "tag": "column_set",
                "flex_mode": "none",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [btn],
                    }
                    for btn in row
                ],
            }
        )
    return _card_v2(title=t("picker_title", lang), template="indigo", elements=elements)


def status_card(title: str, body: str) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        *_markdown_chunks(body),
        {"tag": "hr"},
        *control_buttons(),
    ]
    return _card_v2(title=title, template="turquoise", elements=elements)


# Backward-compatible name used by older imports
def control_actions() -> dict[str, Any]:
    """Deprecated 1.0 action row — prefer control_buttons() for schema 2.0 cards."""
    return {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "Reset"},
                "type": "danger",
                "value": {"action": "reset"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "Switch Agent"},
                "type": "default",
                "value": {"action": "switch"},
            },
        ],
    }
