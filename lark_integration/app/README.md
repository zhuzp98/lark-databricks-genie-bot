# Lark Genie Bot — Databricks App

Hosts the Lark WebSocket bot inside a Databricks App. Genie calls run **as the
Lark user** after they bind via Apps User Authorization (Free Edition–compatible
OBO), not as the App service principal. Sessions/tokens persist to UC; keep-alive
Jobs restart overnight; RuntimeLock prevents dual WS.

Architecture for contributors: [`../../docs/en/architecture.md`](../../docs/en/architecture.md) ([中文](../../docs/cn/architecture.md)).

## Layout

| File | Role |
|------|------|
| `app.yaml` | Start command + env (`APP_PUBLIC_URL`, Lark secrets, UC, lock) |
| `databricks.yml` | App resource + secret bindings |
| `app/app_main.py` | FastAPI `/health` + `/bind` + `/oauth/*` + WS bot thread |
| `bot/user_token_store.py` | email → user token（内存 + UC `bot_obo_tokens`） |
| `bot/session_store.py` | Lark chat → Genie conversation（内存 + UC `bot_sessions`） |
| `bot/uc_sql.py` | Statement Execution REST helper |
| `bot/runtime_lock.py` | Single-writer WS lease (`bot_runtime_lease`) |
| `bot/slog.py` | Structured JSON logs (token-redacted) |
| `bot/i18n.py` | Bot UX copy zh/en |
| `bot/ops_notify.py` | Optional Lark DM on Job failure |
| `bot/obo_bind.py` | Capture `x-forwarded-access-token` |
| `bot/lark_user.py` | Lark `open_id` → enterprise email |
| `bot/`, `bridge/` | Genie clients + Lark IM |
| `jobs/` | Keep-alive stop/start Jobs |

## Auth model (OBO)

1. User asks in Lark → bot resolves Lark email (should match Databricks username).
2. If no valid token → bot sends bind URL: `{APP_PUBLIC_URL}/bind?open_id=&email=`.
3. User opens the link **while logged into Databricks**; Apps injects OBO headers.
4. Token stored in memory **and** UC `bot_obo_tokens` (~55 min TTL). Genie MCP / Space API use that Bearer token.
5. Deep links open the **user’s** Genie One conversation.
6. Sessions persist to UC `bot_sessions` so App restart / nightly keep-alive can resume.

App SP only runs the process (WS, health). It must **not** be used for Genie answers.

### Required Databricks App settings (manual)

1. Workspace: enable **On-Behalf-Of User Authorization** (Previews / Apps settings).
2. Edit app → **User authorization** → scopes covering Genie / MCP (`genie`, `dashboards.genie`, `sql`).
3. Redeploy / **stop + start** after changing scopes.
4. First visit to `/bind` prompts the user to consent (use incognito after scope changes).

### Lark permissions

- Prefer scope **`contact:user.email:readonly`** so the bot can map `open_id` → email.
- Contact permission range must include chatting users.
- If email cannot be read: open `/bind?open_id=...` and use `x-forwarded-email`; or send `绑定 you@email.com` in Lark first.

## Prerequisites

1. Databricks CLI named profile
2. Secret scope `lark_integration`: `lark_app_id`, `lark_app_secret`, `lark_open_api_base`
3. Free Edition App quota (≤3 apps; outbound to Lark unlocked)
4. **Stop local** `python -m lark_integration.bot.lark_ws` before relying on the App

## Deploy

```bash
export DATABRICKS_CONFIG_PROFILE=<PROFILE>
export DATABRICKS_USER_HOME=you@example.com
cd lark_integration
chmod +x app/deploy_phase_a.sh
./app/deploy_phase_a.sh
```

```bash
databricks apps get lark-genie-bot --profile <PROFILE>
```

### Keep-alive (UTC overnight window)

| Job | UTC cron | Action |
|-----|----------|--------|
| `lark-genie-bot-keepalive-stop` | `0 50 23 * * ?` | stop App |
| `lark-genie-bot-keepalive-start` | `0 50 0 * * ?` | start App |

Offline window ≈ **23:50–00:50 UTC** (~60 min).

```bash
cd lark_integration
./jobs/deploy_keepalive.sh
```

After each **start**, memory is empty but **unexpired** OBO tokens and Genie sessions restore from UC. Expired tokens (~55 min) still need `/bind`.

### UC persistence

| Table | Purpose |
|-------|---------|
| `workspace.lark_integration.bot_sessions` | Agent mode + conversation_id |
| `workspace.lark_integration.bot_obo_tokens` | Short-lived OBO access tokens (restrict to App SP) |
| `workspace.lark_integration.bot_runtime_lease` | Single-writer WS lease |

Env (in `app.yaml`): `DATABRICKS_WAREHOUSE_ID`, `LARK_UC_CATALOG`, `LARK_UC_SCHEMA`, `LARK_UC_PERSIST=1`.

### Hardening

| Feature | Behavior |
|---------|----------|
| Job failure email | Keep-alive jobs `email_notifications.on_failure` |
| Job failure Lark DM | Notebook widget `notify_open_id` |
| Runtime lock | UC `bot_runtime_lease` — only one WS bot (App **or** local) |
| Structured logs | JSON lines via `bot/slog.py` (tokens redacted) |

Emergency lock steal: `LARK_RUNTIME_LOCK_STEAL=1` then redeploy/restart. Disable lock: `LARK_RUNTIME_LOCK=0`.

## User commands (Lark)

| Text | Action |
|------|--------|
| `绑定` / `/bind` | Send Databricks bind link（zh/en by message language） |
| `绑定 you@email.com` / `bind you@email.com` | Map open_id → email when Contact API has no email |
| `reset` / Bot Menu Reset | Clear conversation id |
| `switch` / Bot Menu Switch | Agent picker |

### If Genie returns 403 on MCP

Bound Apps OBO token likely lacks `genie` scope.

1. Confirm App **User authorization** includes `genie` and `dashboards.genie`.
2. **Stop + start** the App; open App URL in incognito and accept consent.
3. In Lark send `绑定` again; success page must list `genie` under Token scopes.
4. Ask again.

## Free Edition caveats

- OBO tokens expire ~55 min (no Account OAuth refresh).
- UC persistence survives App restart / keep-alive; expired tokens still need re-bind.
- Do not run local `lark_ws` alongside the App.
- `APP_PUBLIC_URL` in `app.yaml` must match the live Apps URL.
- Quotas: [`../../docs/en/databricks-free-edition-limits.md`](../../docs/en/databricks-free-edition-limits.md).

## Local smoke

```bash
export DATABRICKS_APP_PORT=8000
export DATABRICKS_CONFIG_PROFILE=<PROFILE>
export APP_PUBLIC_URL=http://127.0.0.1:8000
# Simulate OBO headers when hitting /bind locally:
# curl -H 'x-forwarded-access-token: <PAT>' -H 'x-forwarded-email: you@example.com' \
#   'http://127.0.0.1:8000/bind?email=you@example.com'
python lark_integration/app/app_main.py
```
