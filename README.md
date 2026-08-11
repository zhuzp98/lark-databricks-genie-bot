# Lark ↔ Databricks Genie Bot

Free Edition–friendly integration: a **Lark (Feishu) WebSocket bot** hosted as a **Databricks App**, calling **Genie One / Genie Space as the end user (OBO)** so deep links and UC/RLS follow the real person.

| Area | What |
|------|------|
| App + OBO | Apps User Authorization `/bind`; Genie uses user token only |
| Keep-alive | Overnight Jobs (UTC stop/start) against ~24h App pause |
| Persistence | UC tables for sessions + short-lived OBO tokens |
| Hardening | Job failure alerts, RuntimeLock, structured JSON logs |
| UX | Bot copy auto zh/en from user message language |

> Placeholders: `YOUR_*`, `<PROFILE>`. Do not commit real secrets.

## Repo layout

```text
lark_integration/          # App + bot + bridge + jobs + notebooks
docs/en/                   # English guides
docs/cn/                   # Chinese guides (same topics)
docs/credentials/          # gitignored secrets; see credentials.example.md
.env.example               # Local env template
```

## Quick start

### 1. Prerequisites

- Databricks CLI (≥ 0.292) with a named profile
- Lark Open Platform app with Bot + persistent connection events
- Secret scope `lark_integration`: `lark_app_id`, `lark_app_secret`, `lark_open_api_base`

### 2. Configure

```bash
cp .env.example .env          # fill values; never commit .env
cp docs/credentials/credentials.example.md docs/credentials/local_secrets.md
# edit local_secrets.md OR rely on env / Databricks Secrets only
```

Edit placeholders in:

- `lark_integration/app.yaml` — `APP_PUBLIC_URL`, warehouse id  
- `lark_integration/jobs/keepalive_*.job.json` — email, notebook path, `notify_open_id`  
- Deploy scripts expect `DATABRICKS_CONFIG_PROFILE` and `DATABRICKS_USER_HOME`

### 3. Local bot (dev only)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r lark_integration/requirements.txt
export DATABRICKS_CONFIG_PROFILE=<PROFILE>
export DATABRICKS_HOST=https://YOUR_WORKSPACE.cloud.databricks.com
export LARK_APP_ID=...
export LARK_APP_SECRET=...
# Prefer App for production — do not dual-run with the App WS
python -m lark_integration.bot.lark_ws
```

### 4. Deploy Databricks App

```bash
export DATABRICKS_CONFIG_PROFILE=<PROFILE>
export DATABRICKS_USER_HOME=you@example.com
cd lark_integration
./app/deploy_phase_a.sh
```

Enable **On-Behalf-Of User Authorization** and scopes: `genie`, `dashboards.genie`, `sql`.  
Users bind via `{APP_PUBLIC_URL}/bind` (incognito after scope changes).

### 5. Keep-alive Jobs

```bash
cd lark_integration
./jobs/deploy_keepalive.sh
```

Default schedule (UTC): stop `23:50`, start `00:50`.

### 6. UC tables (once)

```sql
-- bot_sessions, bot_obo_tokens, bot_runtime_lease
-- GRANT SELECT, MODIFY to App SP applicationId (not display name)
```

See [`docs/en/auth-and-secrets.md`](docs/en/auth-and-secrets.md).

## Security

- Never commit `docs/credentials/local_secrets.md`, `.env`, or tokens  
- `bot_obo_tokens.access_token` is sensitive — restrict table ACLs  
- Structured logs (`bot/slog.py`) redact tokens  

## Docs

[English](docs/en/README.md) · [中文](docs/cn/README.md)

| Doc | Topic |
|-----|--------|
| [`docs/en/architecture.md`](docs/en/architecture.md) | Architecture, OBO model, modules, troubleshooting, extension tips |
| [`docs/en/auth-and-secrets.md`](docs/en/auth-and-secrets.md) | Auth, Secrets, UC grants |
| [`docs/en/databricks-free-edition-limits.md`](docs/en/databricks-free-edition-limits.md) | Free Edition quotas that shape the design |
| [`lark_integration/app/README.md`](lark_integration/app/README.md) | App deploy & ops |
| [`docs/README.md`](docs/README.md) | Docs language index |

## License

MIT — see [LICENSE](LICENSE).
