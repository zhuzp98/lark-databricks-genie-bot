# Auth 与密钥

开发 / 部署 `lark_integration` 时的认证方式与 Secrets / UC 约定。明文本地备份模板见 [`../credentials/credentials.example.md`](../credentials/credentials.example.md)；架构总览见 [`architecture.md`](architecture.md)；App 运维见 [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md)。

English: [`../en/auth-and-secrets.md`](../en/auth-and-secrets.md)

---

## 1. Databricks CLI

推荐 **OAuth profile**（不要默认长期 PAT）：

```bash
databricks auth profiles
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com --profile <PROFILE>
databricks current-user me --profile <PROFILE>
databricks auth token --profile <PROFILE> -o json   # 短期 token，约 1h
```

命令请显式带 `--profile <PROFILE>`。

### Free Edition Secrets

`databricks secrets *` 偶发 Forbidden 时可用 REST：

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

Notebook：`dbutils.secrets.get("lark_integration", "lark_app_id")`。

---

## 2. Databricks App（生产主路径）

| 身份 | 用途 |
|------|------|
| **App Service Principal** | 跑进程、Lark WS、读 Secrets、写 UC 状态表、SQL warehouse |
| **终端用户 OBO** | 调 Genie One MCP / Genie Space；deep link 与 UC/RLS 按本人 |

### 2.1 Apps User Authorization（Free Edition OBO）

1. Workspace 开启 On-Behalf-Of User Authorization。  
2. App `user_api_scopes`：`genie`、`dashboards.genie`、`sql`（`deploy_phase_a.sh` 每次写回）。  
3. 用户打开 `{APP_PUBLIC_URL}/bind?open_id=&email=`（须已登录 Databricks）。  
4. Apps 注入 `x-forwarded-access-token` + `x-forwarded-email`。  
5. Bot 用该 Bearer 调 Genie；**禁止** OBO 场景回落 SP。

改 scopes 后：必须 **stop + start**，用户用**无痕窗口**重新同意；绑定成功页 Token scopes 须含 `genie`。

### 2.2 Token 生命周期

| 点 | 说明 |
|----|------|
| 存储 | 内存 + UC `bot_obo_tokens` |
| 代码 TTL | 默认 **~55 min**（略短于典型 Apps OBO access token） |
| Refresh | Free Edition **无**；过期须再 `/bind` |
| 持久化意义 | 撑过 App 重启 / 夜间保活；**不延长** token 寿命 |

商业账户可迁 Account Console 自定义 OAuth App Connection + refresh（见 [`architecture.md`](architecture.md) §3）。

### 2.3 App 环境变量（`app.yaml`）

| 变量 | 说明 |
|------|------|
| `LARK_APP_ID` / `LARK_APP_SECRET` / `LARK_OPEN_API_BASE` | Secrets 资源绑定 |
| `APP_PUBLIC_URL` | 实际 Apps URL（无尾斜杠） |
| `DATABRICKS_WAREHOUSE_ID` | SQL warehouse id |
| `LARK_UC_CATALOG` / `LARK_UC_SCHEMA` | 默认 `workspace` / `lark_integration` |
| `LARK_UC_PERSIST` | `1` 写 UC；`0` = 仅内存（调试） |
| `LARK_RUNTIME_LOCK` | `1` = UC 租约互斥；`0` 关闭 |
| `LARK_RUNTIME_LOCK_STEAL` | `1` = 紧急抢占租约 |
| `LARK_LOG_SERVICE` | 结构化日志 `service` 字段 |
| `LARK_OPS_NOTIFY_OPEN_ID` | （可选）覆盖 Job 失败 Lark DM 目标 |

App 运行时 SP 凭证由平台注入；勿依赖本机 `local_secrets.md`。

### 2.4 UC 状态表与权限

| 表 | 内容 |
|----|------|
| `workspace.lark_integration.bot_sessions` | Lark chat ↔ Genie mode / conversation |
| `workspace.lark_integration.bot_obo_tokens` | email / open_id / **access_token** / expires_at（敏感） |
| `workspace.lark_integration.bot_runtime_lease` | 单写者 WS 租约（App vs 本机） |

对 App SP（GRANT 用 **applicationId**，勿用 display name）：

```sql
GRANT USE CATALOG ON CATALOG workspace TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT USE SCHEMA ON SCHEMA workspace.lark_integration TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT SELECT, MODIFY ON TABLE workspace.lark_integration.bot_sessions TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT SELECT, MODIFY ON TABLE workspace.lark_integration.bot_obo_tokens TO `YOUR_APP_SP_APPLICATION_ID`;
GRANT SELECT, MODIFY ON TABLE workspace.lark_integration.bot_runtime_lease TO `YOUR_APP_SP_APPLICATION_ID`;
```

Warehouse：App SP 需 `CAN_USE`。  
**禁止**把 `access_token` 打进日志（`bot/slog.py` 会脱敏）；敏感表权限仅限 App SP + owner。

### 2.5 Keep-alive Job 通知

| 通道 | 配置 |
|------|------|
| 邮件 | Jobs `email_notifications.on_failure` |
| Lark DM | Notebook widget `notify_open_id`；失败时 `ops_notify` |

部署：`lark_integration/jobs/deploy_keepalive.sh`。

---

## 3. Lark

### 3.1 CLI（本机调试）

```bash
printf '%s' '<APP_SECRET>' | lark-cli config init \
  --app-id cli_YOUR_LARK_APP_ID --app-secret-stdin --brand lark

lark-cli config show
lark-cli whoami
lark-cli auth login --domain im,docs,sheets,wiki,drive,event --json
lark-cli event consume im.message.receive_v1 --as bot --timeout 2m
```

开放平台事件订阅用**持久连接**；至少 `im.message.receive_v1`。卡片回调：`card.action.trigger`。

### 3.2 Bot 权限（最小集）

- IM：收/发消息、上传下载消息资源  
- 事件：`im.message.receive_v1`；菜单 `application.bot.menu_v6`（若用）  
- 回调：`card.action.trigger`  
- **联系人**：`contact:user.email:readonly`（`open_id` → 企业邮箱）  
- Bridge 额外需要时再开 Sheets / Docs / Wiki  

OpenAPI Base（国际版）：`https://open.larksuite.com`。

---

## 4. Secret Scope：`lark_integration`

App 通过 `databricks.yml` / `apps update` 绑定（`deploy_phase_a.sh` 会写回）：

| Key | 用途 |
|-----|------|
| `lark_app_id` | Lark App ID |
| `lark_app_secret` | Lark App Secret |
| `lark_open_api_base` | `https://open.larksuite.com` |

轮换 Secret 后：更新 scope → 重新 `./app/deploy_phase_a.sh`（或至少 stop/start）。

---

## 5. 本地明文备份

路径：`docs/credentials/local_secrets.md`（应被 gitignore）。

用途：本机恢复、对照 Secrets。  
**禁止**提交 git。  
生产 App **不**读该文件。模板：[`../credentials/credentials.example.md`](../credentials/credentials.example.md)。

---

## 6. 身份对照

| 场景 | 用谁的凭证 |
|------|------------|
| 本机 `databricks` CLI / 运维 Job | 用户 OAuth profile `<PROFILE>` |
| App 进程、UC upsert、读 Secrets | App SP |
| Genie 问答 / deep link | **已绑定用户**的 OBO token |
| Lark 收发消息 | Lark App ID/Secret（tenant） |

双开禁忌：本机 `lark_ws` 与 App WS **不要同时**订阅同一 Lark App。
