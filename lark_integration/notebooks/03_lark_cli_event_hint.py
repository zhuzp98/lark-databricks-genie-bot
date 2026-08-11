# Databricks notebook source
# MAGIC %md
# MAGIC # Optional: consume Lark events via lark-cli (shell)
# MAGIC Useful when validating event subscription before Python WS client.
# MAGIC
# MAGIC ```bash
# MAGIC lark-cli event consume im.message.receive_v1 --as bot --timeout 2m
# MAGIC ```

# COMMAND ----------

print("Run lark-cli locally — see docs/en/auth-and-secrets.md (or docs/cn/)")
