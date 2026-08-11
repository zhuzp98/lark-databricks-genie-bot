# Auth & secrets

How to authenticate when developing or deploying `lark_integration`, and how Secrets / UC state are stored.

- Local plaintext template: [`../credentials/credentials.example.md`](../credentials/credentials.example.md)
- Architecture: [`architecture.md`](architecture.md)
- App ops: [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md)
- 中文版: [`../cn/auth-and-secrets.md`](../cn/auth-and-secrets.md)

---

## 1. Databricks CLI

Prefer an **OAuth profile** (not a long-lived PAT by default):

```bash
databricks auth profiles
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com --profile <PROFILE>
databricks current-user me --profile <PROFILE>
databricks auth token --profile <PROFILE> -o json   # short-lived token, ~1h
```

Always pass `--profile <PROFILE>` explicitly.

### Free Edition Secrets

If `databricks secrets *` returns Forbidden, use the REST API:

```bash
databricks api post /api/2.0/secrets/scopes/create --profile <PROFILE> \
  --json '{"scope":"lark_integration"}'

databricks api post /api/2.0/secrets/put --profile <PROFILE> --json '{
  "scope": "lark_integration",
  "key": "lark_app_id",
  "string_value": "<APP_ID>"
}'

databricks api get '/api/2.0/secrets/list?scope=lark_integration' --profile <PROFILE>
```

In notebooks: `dbutils.secrets.get("lark_integration", "lark_app_id")`.

---

## 2. Databricks App (production path)

| Identity | Used for |
|----------|----------|
| **App Service Principal** | Process, Lark WS, read Secrets, write UC state, SQL warehouse |
| **End-user OBO** | Genie One MCP / Genie Space; deep link + UC/RLS as that user |

### 2.1 Apps User Authorization (Free Edition OBO)

1. Enable On-Behalf-Of User Authorization in the workspace.  
2. App `user_api_scopes`: `genie`, `dashboards.genie`, `sql` (`deploy_phase_a.sh` writes these back every deploy).  
3. User opens `{APP_PUBLIC_URL}/bind?open_id=&email=` while logged into Databricks.  
4. Apps injects `x-forwarded-access-token` + `x-forwarded-email`.  
5. Bot calls Genie with that Bearer; **never** fall back to the SP for OBO answers.

After changing scopes: **stop + start**, then re-consent in an **incognito** window. The bind success page Token scopes must include `genie`.

### 2.2 Token lifetime

| Point | Notes |
|-------|--------|
| Storage | Memory + UC `bot_obo_tokens` |
| Code TTL | Default **~55 min** (slightly under a typical Apps OBO access token) |
| Refresh | **None** on Free Edition; bind again after expiry |
| Why persist | Survives App restart / overnight keep-alive; does **not** extend token life |

Commercial accounts can move to Account Console custom OAuth App Connection + refresh (see [`architecture.md`](architecture.md) §3).

### 2.3 App env vars (`app.yaml`)

| Variable | Meaning |
|----------|---------|
| `LARK_APP_ID` / `LARK_APP_SECRET` / `LARK_OPEN_API_BASE` | Bound from Secrets |
| `APP_PUBLIC_URL` | Live Apps URL (no trailing slash) |
| `DATABRICKS_WAREHOUSE_ID` | SQL warehouse id |
| `LARK_UC_CATALOG` / `LARK_UC_SCHEMA` | Default `workspace` / `lark_integration` |
| `LARK_UC_PERSIST` | `1` = write UC; `0` = memory only (debug) |
| `LARK_RUNTIME_LOCK` | `1` = UC lease mutex; `0` = off |
| `LARK_RUNTIME_LOCK_STEAL` | `1` = force-steal lease (emergency) |
| `LARK_LOG_SERVICE` | `service` field in structured logs |
| `LARK_OPS_NOTIFY_OPEN_ID` | (optional) override Job-failure Lark DM target |

Runtime SP credentials are injected by the platform; do not rely on local `local_secrets.md`.

### 2.4 UC state tables & grants

| Table | Contents |
|-------|----------|
| `workspace.lark_integration.bot_sessions` | Lark chat ↔ Genie mode / conversation |
| `workspace.lark_integration.bot_obo_tokens` | email / open_id / **access_token** / expires_at (sensitive) |
| `workspace.lark_integration.bot_runtime_lease` | Single-writer WS lease (App vs local) |

Grant to the App SP using **applicationId**, not display name:

```sql
GRANT USE CATALOG ON CATALOG workspace TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT USE SCHEMA ON SCHEMA workspace.lark_integration TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT SELECT, MODIFY ON TABLE workspace.lark_integration.bot_sessions TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT SELECT, MODIFY ON TABLE workspace.lark_integration.bot_obo_tokens TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT SELECT, MODIFY ON TABLE workspace.lark_integration.bot_runtime_lease TO `YOUR_APP_SP_APPLICATION_ID`;
```

Warehouse: App SP needs `CAN_USE`.  
**Never** log `access_token` (`bot/slog.py` redacts); keep sensitive-table ACL to App SP + owner only.

### 2.5 Keep-alive Job notifications

| Channel | Config |
|---------|--------|
| Email | Jobs `email_notifications.on_failure` |
| Lark DM | Notebook widget `notify_open_id`; `ops_notify` on failure |

Deploy: `lark_integration/jobs/deploy_keepalive.sh`.

---

## 3. Lark

### 3.1 CLI (local debug)

```bash
printf '%s' '<APP_SECRET>' | lark-cli config init \
  --app-id cli_YOUR_LARK_APP_ID --app-secret-stdin --brand lark

lark-cli config show
lark-cli whoami
lark-cli auth login --domain im,docs,sheets,wiki,drive,event --json
lark-cli event consume im.message.receive_v1 --as bot --timeout 2m
```

Use a **persistent connection** for event subscription; at least `im.message.receive_v1`. Card callback: `card.action.trigger`.

### 3.2 Bot permissions (minimum)

- IM: send/receive messages; upload/download message resources  
- Events: `im.message.receive_v1`; menu `application.bot.menu_v6` if used  
- Callback: `card.action.trigger`  
- **Contact**: `contact:user.email:readonly` (`open_id` → enterprise email)  
- Open Sheets / Docs / Wiki only when Bridge needs them  

OpenAPI base (international): `https://open.larksuite.com`.

---

## 4. Secret scope: `lark_integration`

Bound via `databricks.yml` / `apps update` (`deploy_phase_a.sh` writes them back):

| Key | Purpose |
|-----|---------|
| `lark_app_id` | Lark App ID |
| `lark_app_secret` | Lark App Secret |
| `lark_open_api_base` | `https://open.larksuite.com` |

After rotating secrets: update the scope → re-run `./app/deploy_phase_a.sh` (or at least stop/start).

---

## 5. Local plaintext backup

Path: `docs/credentials/local_secrets.md` (should be gitignored).

Use for local recovery and Secrets cross-checks.  
**Never** commit to git.  
The production App does **not** read this file. Template: [`../credentials/credentials.example.md`](../credentials/credentials.example.md).

---

## 6. Identity cheat sheet

| Scenario | Whose credentials |
|----------|-------------------|
| Local `databricks` CLI / ops Jobs | User OAuth profile `<PROFILE>` |
| App process, UC upsert, read Secrets | App SP |
| Genie Q&A / deep link | **Bound user’s** OBO token |
| Lark send/receive | Lark App ID/Secret (tenant) |

Do not dual-subscribe: local `lark_ws` and App WS must not share the same Lark App at once.
