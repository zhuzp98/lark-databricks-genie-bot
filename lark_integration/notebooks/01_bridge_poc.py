# Databricks notebook source
# MAGIC %md
# MAGIC # Lark Bridge PoC — Sheet / Docs / IM
# MAGIC
# MAGIC Widgets: set parameters then Run All.
# MAGIC Secrets scope: `lark_integration`

# COMMAND ----------

dbutils.widgets.text("action", "send_text", "action: send_text|send_card|send_file|sheet_read|sheet_write|docs_md")
dbutils.widgets.text("chat_id", "", "Lark chat_id")
dbutils.widgets.text("sheet_url", "", "Sheet/Wiki URL or token")
dbutils.widgets.text("sheet_id", "", "Sheet tab id (for write)")
dbutils.widgets.text("doc_url", "", "Docs/Wiki URL or docx token")
dbutils.widgets.text("catalog", "workspace", "UC catalog")
dbutils.widgets.text("schema", "lark_integration", "UC schema")
dbutils.widgets.text("volume_path", "/Volumes/workspace/lark_integration/docs", "Volume/local md path")
dbutils.widgets.text("secret_scope", "lark_integration", "secret scope")

action = dbutils.widgets.get("action")
chat_id = dbutils.widgets.get("chat_id")
sheet_url = dbutils.widgets.get("sheet_url")
sheet_id = dbutils.widgets.get("sheet_id")
doc_url = dbutils.widgets.get("doc_url")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
volume_path = dbutils.widgets.get("volume_path")
secret_scope = dbutils.widgets.get("secret_scope")

# COMMAND ----------

# MAGIC %pip install requests pandas --quiet
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path

# If uploaded as Workspace folder, adjust this path:
REPO_CANDIDATES = [
    Path("/Workspace/Users") / spark.conf.get("spark.databricks.user.workspaceId", "") ,
    Path.cwd(),
]
# Prefer current notebook working copy / repo sync path
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, "/Workspace/Shared/lark_integration_poc")

from lark_integration.bridge import (
    docs_to_markdown,
    send_card,
    send_file,
    send_text,
    sheet_to_tables,
    tables_to_sheet,
)
from lark_integration.bridge.im_send import simple_result_card
import pandas as pd

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

# COMMAND ----------

if action == "send_text":
    assert chat_id, "chat_id required"
    mid = send_text(chat_id, "Hello from Databricks Free Edition PoC (text)", secret_scope=secret_scope)
    print("message_id", mid)

elif action == "send_card":
    assert chat_id, "chat_id required"
    card = simple_result_card("Lark Bridge PoC", "Interactive **card** from Databricks Job.")
    mid = send_card(chat_id, card, secret_scope=secret_scope)
    print("message_id", mid)

elif action == "send_file":
    assert chat_id, "chat_id required"
    csv = "col_a,col_b\n1,2\n3,4\n".encode("utf-8-sig")
    mid = send_file(chat_id, csv, "poc_sample.csv", secret_scope=secret_scope)
    print("message_id", mid)

elif action == "sheet_read":
    assert sheet_url, "sheet_url required"
    tables = sheet_to_tables(sheet_url, catalog, schema, secret_scope=secret_scope, spark=spark)
    print("wrote", tables)

elif action == "sheet_write":
    assert sheet_url and sheet_id, "sheet_url and sheet_id required"
    df = pd.DataFrame({"metric": ["revenue", "users"], "value": ["100", "42"]})
    resp = tables_to_sheet(sheet_url, sheet_id, df, secret_scope=secret_scope)
    print(resp)

elif action == "docs_md":
    assert doc_url, "doc_url required"
    # Ensure volume path exists when using UC Volumes
    path = docs_to_markdown(
        doc_url,
        volume_path,
        meta_table=f"{catalog}.{schema}.docs_sync_meta",
        secret_scope=secret_scope,
        spark=spark,
    )
    print("md path", path)

else:
    raise ValueError(f"unknown action={action}")
