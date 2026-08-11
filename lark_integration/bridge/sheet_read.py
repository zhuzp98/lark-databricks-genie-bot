"""Read Lark Sheet pages into pandas / optional Spark Delta tables."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import requests

from .auth import auth_headers, load_lark_credentials
from .wiki import resolve_wiki_node


def _safe_table_name(title: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", title.strip()).strip("_").lower()
    if not name:
        name = "sheet"
    if name[0].isdigit():
        name = f"t_{name}"
    return name[:120]


def _should_skip_sheet(title: str, columns: list[str]) -> str | None:
    if re.search(r"[\u4e00-\u9fff]", title):
        return "Chinese characters in sheet title"
    if not columns:
        return "empty columns"
    if any(not c or not str(c).strip() for c in columns):
        return "empty column name"
    if any(re.search(r"[\u4e00-\u9fff]", str(c)) for c in columns):
        return "Chinese characters in column name"
    if any(" " in str(c) for c in columns):
        return "space in column name"
    if len(columns) != len(set(columns)):
        return "duplicate column names"
    return None


def list_sheet_metas(spreadsheet_token: str, *, secret_scope: str = "lark_integration") -> list[dict]:
    creds = load_lark_credentials(secret_scope=secret_scope)
    headers = auth_headers(secret_scope=secret_scope)
    r = requests.get(
        f"{creds['base_url']}/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query",
        headers=headers,
        timeout=60,
    )
    data = r.json()
    if data.get("code") != 0:
        # fallback v2 meta
        r2 = requests.get(
            f"{creds['base_url']}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo",
            headers=headers,
            timeout=60,
        )
        data2 = r2.json()
        if data2.get("code") != 0:
            raise RuntimeError(f"list sheets failed: {data} / {data2}")
        return data2["data"]["sheets"]
    return data["data"]["sheets"]


def read_sheet_values(
    spreadsheet_token: str,
    sheet_id: str,
    *,
    secret_scope: str = "lark_integration",
) -> list[list[Any]]:
    creds = load_lark_credentials(secret_scope=secret_scope)
    headers = auth_headers(secret_scope=secret_scope)
    # Prefer ranges API with sheetId
    r = requests.get(
        f"{creds['base_url']}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}",
        headers=headers,
        params={"valueRenderOption": "ToString"},
        timeout=120,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"read values failed: {data}")
    return data.get("data", {}).get("valueRange", {}).get("values") or []


def sheet_pages_to_dataframes(
    url_or_token: str,
    *,
    secret_scope: str = "lark_integration",
) -> tuple[list[tuple[str, pd.DataFrame]], list[dict]]:
    """Return ([(table_name, df), ...], skip_log)."""
    resolved = resolve_wiki_node(url_or_token, secret_scope=secret_scope)
    if resolved["obj_type"] not in ("sheet", "sheets", "spreadsheet"):
        # wiki may return obj_type sheet
        if "sheet" not in str(resolved["obj_type"]):
            raise RuntimeError(f"Not a spreadsheet object: {resolved}")
    token = resolved["obj_token"]
    sheets = list_sheet_metas(token, secret_scope=secret_scope)
    results: list[tuple[str, pd.DataFrame]] = []
    skips: list[dict] = []
    for sh in sheets:
        title = sh.get("title") or sh.get("sheet_id") or "sheet"
        sheet_id = sh.get("sheet_id") or sh.get("sheetId")
        values = read_sheet_values(token, sheet_id, secret_scope=secret_scope)
        if not values:
            skips.append({"title": title, "reason": "empty sheet"})
            continue
        columns = [str(c) for c in values[0]]
        reason = _should_skip_sheet(title, columns)
        if reason:
            skips.append({"title": title, "reason": reason})
            continue
        rows = values[1:]
        df = pd.DataFrame(rows, columns=columns)
        results.append((_safe_table_name(title), df))
    return results, skips


def sheet_to_tables(
    url_or_token: str,
    catalog: str,
    schema: str,
    *,
    secret_scope: str = "lark_integration",
    spark=None,
) -> list[str]:
    """Write each eligible sheet page to `{catalog}.{schema}.{table}`.

    When spark is None, only returns planned table names after local read
    (useful for unit smoke tests). Pass active SparkSession on Databricks.
    """
    pages, skips = sheet_pages_to_dataframes(url_or_token, secret_scope=secret_scope)
    for s in skips:
        print(f"[skip] {s['title']}: {s['reason']}")
    written: list[str] = []
    for table, df in pages:
        fqn = f"{catalog}.{schema}.{table}"
        if spark is None:
            print(f"[dry-run] would write {fqn} rows={len(df)} cols={list(df.columns)}")
            written.append(fqn)
            continue
        sdf = spark.createDataFrame(df.astype(str))
        sdf.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(fqn)
        print(f"[ok] wrote {fqn} rows={len(df)}")
        written.append(fqn)
    return written
