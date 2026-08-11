# Databricks notebook source
# MAGIC %md
# MAGIC # Genie Conversation API smoke test (no Lark)
# MAGIC Validates Free Edition Genie + warehouse before wiring the Bot.

# COMMAND ----------

dbutils.widgets.text("space_id", "YOUR_GENIE_SPACE_ID", "Genie space id")
dbutils.widgets.text("question", "What tables can you query?", "question")
space_id = dbutils.widgets.get("space_id")
question = dbutils.widgets.get("question")

# COMMAND ----------

# MAGIC %pip install requests --quiet
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, "/Workspace/Shared/lark_integration_poc")

from databricks.sdk.core import Config
from lark_integration.bot.genie_client import GenieClient

cfg = Config()  # uses notebook identity
# GenieClient falls back to CLI token locally; on cluster pass token via env if needed
client = GenieClient(space_id=space_id, host=f"https://{cfg.host}", token=cfg.authenticate().get("Authorization", "").replace("Bearer ", "") or None)
result = client.ask(question)
print("conversation_id", result["conversation_id"])
print("status", result["message"].get("status"))
atts = result["message"].get("attachments") or []
print("attachments", len(atts))
for a in atts:
    print(a.keys())
