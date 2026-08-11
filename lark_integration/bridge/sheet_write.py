"""Write tabular data into a Lark Sheet range."""

from __future__ import annotations

from typing import Any, Sequence

import pandas as pd
import requests

from .auth import auth_headers, load_lark_credentials
from .wiki import resolve_wiki_node


def _to_values(data: Any, *, include_header: bool = True) -> list[list[Any]]:
    if isinstance(data, pd.DataFrame):
        rows = data.fillna("").astype(str).values.tolist()
        if include_header:
            return [list(map(str, data.columns))] + rows
        return rows
    if isinstance(data, Sequence) and data and isinstance(data[0], Sequence):
        return [list(r) for r in data]
    raise TypeError("data must be a pandas DataFrame or list[list]")


def tables_to_sheet(
    spreadsheet_token_or_url: str,
    sheet_id: str,
    data: Any,
    *,
    range_start: str = "A1",
    include_header: bool = True,
    secret_scope: str = "lark_integration",
) -> dict:
    """Overwrite sheet values starting at range_start (e.g. A1)."""
    resolved = resolve_wiki_node(spreadsheet_token_or_url, secret_scope=secret_scope)
    token = resolved["obj_token"]
    values = _to_values(data, include_header=include_header)
    # Build range like sheetId!A1:C10
    n_rows = len(values)
    n_cols = max((len(r) for r in values), default=1)
    end_col = _col_letters(n_cols)
    end_row = n_rows
    range_str = f"{sheet_id}!{range_start}:{end_col}{end_row}"

    creds = load_lark_credentials(secret_scope=secret_scope)
    headers = {**auth_headers(secret_scope=secret_scope), "Content-Type": "application/json"}
    body = {
        "valueRange": {
            "range": range_str,
            "values": values,
        }
    }
    r = requests.put(
        f"{creds['base_url']}/open-apis/sheets/v2/spreadsheets/{token}/values",
        headers=headers,
        json=body,
        timeout=120,
    )
    data_resp = r.json()
    if data_resp.get("code") != 0:
        raise RuntimeError(f"tables_to_sheet failed: {data_resp}")
    return data_resp


def _col_letters(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s or "A"
