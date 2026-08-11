# Lark Integration package

| Package | Role |
|---------|------|
| `lark_integration.bot` | Lark WS bot, Genie One/Space, OBO bind, UC persist, RuntimeLock |
| `lark_integration.bridge` | Sheet/Docs/IM helpers |
| `lark_integration.app` | Databricks App entry (`app_main.py`) + deploy script |
| `lark_integration.jobs` | Keep-alive Job JSON + deploy |

See root [README.md](../README.md), [app/README.md](app/README.md), and [docs/en/architecture.md](../docs/en/architecture.md) ([中文](../docs/cn/architecture.md)).

## Install

```bash
pip install -r requirements.txt
```

## Deploy App

```bash
export DATABRICKS_CONFIG_PROFILE=<PROFILE>
export DATABRICKS_USER_HOME=you@example.com
./app/deploy_phase_a.sh
```

## Keep-alive

```bash
./jobs/deploy_keepalive.sh
```
