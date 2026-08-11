# lark_integration architecture & extension guide

For developers continuing work on this repo: current architecture, the OBO identity model, module responsibilities, common pitfalls, and extension directions.

- Deploy / ops: [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md)
- Auth & secrets: [`auth-and-secrets.md`](auth-and-secrets.md)
- 中文版: [`../cn/architecture.md`](../cn/architecture.md)

---

## 1. Goals

| Goal | Approach |
|------|----------|
| Natural-language Q&A in Lark | Bot talks to Genie One MCP / Genie Space API; replies with cards |
| Genie One deep link opens **the user’s** conversation | Genie calls **must** use the end-user token — never the App SP |
| UC / RLS / audit as the real person | Same; App SP only runs the process |
| Runs on Free Edition | Single App + Apps User Authorization (`/bind`); overnight Jobs fight ~24h pause |

**Split design**: `bot` = interactive plane (inbound + Genie); `bridge` = batch data plane (Sheet/Docs/outbound IM). Do not force batch sync onto the always-on App path.

---

## 2. Architecture

```text
Lark user
  │  WebSocket events
  ▼
Databricks App (lark-genie-bot)
  ├─ FastAPI: /, /bind, /oauth/*, /health
  ├─ Lark WS thread: messages / cards / menus
  ├─ user_token_store (memory + UC bot_obo_tokens)
  ├─ SessionStore (memory + UC bot_sessions)
  ├─ RuntimeLock (UC bot_runtime_lease — single WS writer)
  └─ GenieOneClient / GenieClient (Bearer = user OBO token)
        │
        ▼
  Genie One MCP  →  deep_link (user’s own conversation)

Scheduled Jobs: UTC 23:50 stop / 00:50 start (vs App ~24h pause)

bridge/ (Jobs / notebooks): Sheet ↔ UC, Docs → Volume, outbound text/card/file
```

### Bind flow (OBO)

1. User asks in Lark → resolve enterprise email from `open_id` (Contact API) → look up token.  
2. No valid token → send `{APP_PUBLIC_URL}/bind?open_id=&email=`.  
3. User opens the link while logged into Databricks → Apps injects `x-forwarded-access-token` + `x-forwarded-email`.  
4. `/bind` checks JWT scopes (must include `genie`) and writes memory + UC.  
5. Ask again → Genie with user token → card with a real deep link.

### Identity keys

| Key | Role |
|-----|------|
| Lark `open_id` | Ties session and bind |
| Lark / Databricks **email** | Token primary key (convention: they match) |
| Apps OBO JWT | Credential used to call Genie |

If emails do not align: user sends `绑定 you@email.com` / `bind you@email.com`, or open `/bind` with only `open_id` and use `x-forwarded-email` as the key.

---

## 3. Why this OBO design (not the Slack dual-app pattern)

Reference: [Genie + Slack OBO](https://github.com/dahora-databricks/databricks-genie-slack-obo-oauth) needs Account Console custom OAuth App Connections plus a separate broker.

Free Edition has **no Account Console / account-level APIs**, so that pattern cannot be copied. Equivalent approach:

- **Single App** + built-in **Apps User Authorization** (`/bind`)  
- Token ~55 min TTL, **no refresh**; re-bind after expiry  
- App SP: host process, Lark WS, read Secrets, write UC state tables  

On a commercial / enterprise account you can re-evaluate Account App Connection + (optional) broker + refresh. That upgrade need not block the current code path.

---

## 4. Code map

```text
lark_integration/
  app/app_main.py           # FastAPI + home page + WS thread
  app/deploy_phase_a.sh     # upload / scopes+secrets / deploy
  app.yaml                  # APP_PUBLIC_URL, Lark secrets, UC, lock
  databricks.yml            # App resource + user_api_scopes
  bot/obo_bind.py           # /bind + JWT scope diagnostics
  bot/user_token_store.py   # OBO token (memory + UC)
  bot/session_store.py      # sessions (memory + UC)
  bot/uc_sql.py             # Statement Execution helper
  bot/runtime_lock.py       # dual-instance mutual exclusion
  bot/slog.py               # structured JSON logs (redacted)
  bot/i18n.py               # Bot copy zh/en
  bot/lark_user.py          # open_id → email
  bot/lark_ws.py            # auth, Q&A, menus
  bot/genie_one_client.py   # Genie One MCP (user token)
  bot/genie_client.py       # Genie Space Conversation API
  bot/reply_format.py       # cards / deep_link
  bot/file_ingest.py        # inbound files → Volume
  bot/ops_notify.py         # optional Lark DM on Job failure
  bridge/                   # Sheet / Docs / outbound IM
  jobs/                     # keep-alive Jobs
  notebooks/                # PoC / keepalive samples
```

Rule of thumb: Genie UX changes go in `bot/`; batch sync in `bridge/`; keep deploy entrypoints `deploy_phase_a.sh` / `deploy_keepalive.sh`.

---

## 5. Runtime capabilities (shipped)

| Capability | Notes |
|------------|--------|
| Overnight keep-alive | UTC 23:50 stop / 00:50 start — avoids daytime peaks |
| UC persistence | `bot_sessions`, `bot_obo_tokens` survive App restart; tokens still ~55 min TTL |
| RuntimeLock | Local process and App compete for one WS lease — no double answers |
| Failure alerts | Keep-alive Job email; optional Lark DM |
| Structured logs | `bot/slog.py` one-line JSON; tokens redacted |

`apps update` is a **full replace**: `deploy_phase_a.sh` always writes back both `user_api_scopes` and Lark secret resources — do not update one without the other.

---

## 6. Configuration checklist

### Databricks

- [ ] Workspace: On-Behalf-Of / User authorization enabled  
- [ ] App `user_api_scopes`: `genie`, `dashboards.genie`, `sql`  
- [ ] After scope changes: **stop + start** (redeploy alone is sometimes not enough)  
- [ ] User re-consents in an **incognito** window; `/bind` success page shows `genie`  
- [ ] `APP_PUBLIC_URL` = live Apps URL (no trailing slash)  
- [ ] Secret scope `lark_integration`: `lark_app_id` / `lark_app_secret` / `lark_open_api_base`  
- [ ] App SP grants on the three UC tables + warehouse (see [`auth-and-secrets.md`](auth-and-secrets.md))

### Lark

- [ ] Persistent connection events: `im.message.receive_v1`, `application.bot.menu_v6`, `card.action.trigger`  
- [ ] `contact:user.email:readonly` (contact visibility covers chat users)  
- [ ] Production: only the App holds one WS; do not dual-run the same Bot locally  

Quotas and 24h pause: [`databricks-free-edition-limits.md`](databricks-free-edition-limits.md).

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Contact has `open_id` but no email | Missing `contact:user.email:readonly`; or use `bind you@email.com` |
| Genie MCP 403 | OBO JWT lacks `genie`: add scopes → stop/start → **incognito** re-consent → `/bind` again |
| “Link” opens JSON | Must open full `/bind?...`; home page is HTML guide, not a status API |
| Deep link “Conversation not found” | Session was created as App SP; must use user token |
| Double replies / out of order | Local `lark_ws` + App both connected; rely on RuntimeLock or stop local |
| Secrets/scopes gone after deploy | `apps update` full replace; use `deploy_phase_a.sh` |
| App down during daytime | Check keep-alive Jobs / fair usage; see Free Edition limits |

---

## 8. User commands (Lark)

| Input | Effect |
|-------|--------|
| Ask a question | Unbound users get a `/bind` link |
| `绑定` / `/bind` | Request bind link |
| `绑定 you@email.com` / `bind you@email.com` | Manual email map when Contact has no email |
| `reset` | Clear conversation |
| `switch` | Switch agent |

System copy follows zh/en of the user message (`bot/i18n.py`); Genie answer language is decided by Genie.

---

## 9. Where to extend next

**Good next steps**

- Bridge: Sheet write-back, Docs→MD, productized outbound cards (`bridge/` + notebooks)  
- Bot: parse inbound files into tables, multi-Space routing, stronger rate-limit queue  
- After account upgrade: Account OAuth + refresh instead of ~55 min re-bind  
- Observability: alerts / dashboards on `slog`  

**Avoid unless goals change**

- Falling Genie Q&A back to the App SP (breaks deep link + RLS)  
- Forcing Account App Connection on Free Edition  
- Dual-running local + App WS on the same Lark Bot as “load balancing”  

Local smoke: [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md). Production: `./app/deploy_phase_a.sh` and `./jobs/deploy_keepalive.sh`.
