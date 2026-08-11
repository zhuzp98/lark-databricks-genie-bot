# lark_integration 架构与扩展指南

本文面向在本仓库上继续开发的贡献者，说明已落地的架构、OBO 身份模型、模块职责、常见问题与扩展方向。部署与运维见 [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md)；认证与密钥见 [`auth-and-secrets.md`](auth-and-secrets.md)。

English: [`../en/architecture.md`](../en/architecture.md)

---

## 1. 目标

| 目标 | 做法 |
|------|------|
| 在 Lark 中进行自然语言数据问答 | Bot 通过 Genie One MCP / Genie Space API 问答，并以卡片回复 |
| Genie One deep link 打开为用户本人会话 | 调用 Genie 时必须使用**终端用户** token，不得使用 App SP |
| UC / RLS / 审计按真实用户生效 | 同上；App SP 仅负责托管进程，不作为问答身份 |
| 可在 Free Edition 上运行 | 单 App + Apps User Authorization（`/bind`）；夜间 Job 应对约 24 小时自动暂停 |

**混合结构**：`bot` 负责交互面（入站事件与 Genie）；`bridge` 负责批式数据面（Sheet/Docs/出站 IM）。不宜将批同步全部放入 App 常驻路径。

---

## 2. 架构

```text
Lark 用户
  │  WebSocket 事件
  ▼
Databricks App（lark-genie-bot）
  ├─ FastAPI：/、/bind、/oauth/*、/health
  ├─ Lark WS 线程：消息 / 卡片 / 菜单
  ├─ user_token_store（内存 + UC bot_obo_tokens）
  ├─ SessionStore（内存 + UC bot_sessions）
  ├─ RuntimeLock（UC bot_runtime_lease，防止多实例同时连接 WS）
  └─ GenieOneClient / GenieClient（Bearer = 用户 OBO token）
        │
        ▼
  Genie One MCP  →  deep_link（用户本人会话）

定时 Job：UTC 23:50 stop / 00:50 start（应对 App 约 24 小时自动暂停）

bridge/（由 Jobs / notebooks 调用）：Sheet ↔ UC、Docs → Volume、出站 text/card/file
```

### 绑定流程（OBO）

1. 用户在 Lark 提问 → 通过 Contact API 由 `open_id` 解析企业邮箱 → 查询 token。  
2. 若无有效 token → 发送 `{APP_PUBLIC_URL}/bind?open_id=&email=`。  
3. 用户在已登录 Databricks 的前提下打开链接 → Apps 注入 `x-forwarded-access-token` 与 `x-forwarded-email`。  
4. `/bind` 校验 JWT scopes（须包含 `genie`），并写入内存与 UC。  
5. 用户再次提问 → 使用该用户 token 调用 Genie → 卡片附带有效 deep link。

### 身份键

| 键 | 用途 |
|----|------|
| Lark `open_id` | 关联会话与绑定 |
| Lark / Databricks **邮箱** | token 主键（约定二者一致） |
| Apps OBO JWT | 调用 Genie 的实际凭证 |

若邮箱无法对齐：用户发送 `绑定 you@email.com`，或仅携带 `open_id` 打开 `/bind`，并以 `x-forwarded-email` 作为主键。

---

## 3. 为何采用此 OBO 方案（而非 Slack 双 App）

参考实现：[Genie + Slack OBO](https://github.com/dahora-databricks/databricks-genie-slack-obo-oauth) 依赖 Account Console 自定义 OAuth App Connection 以及独立 broker。

Free Edition **不提供 Account Console / account-level APIs**，无法直接采用该方案。等价做法如下：

- **单 App** + 内置 **Apps User Authorization**（`/bind`）  
- Token 有效期约 55 分钟，**不支持 refresh**；过期后需重新绑定  
- App SP 职责：托管进程、Lark WS、读取 Secrets、写入 UC 状态表  

升级至商业 / 企业账户后，可再评估 Account App Connection 与（可选）独立 broker + refresh。该升级路径不作为当前实现的前置条件。

---

## 4. 代码地图

```text
lark_integration/
  app/app_main.py           # FastAPI + 首页 + WS 线程
  app/deploy_phase_a.sh     # 上传 / scopes+secrets / 部署
  app.yaml                  # APP_PUBLIC_URL、Lark secrets、UC、锁
  databricks.yml            # App 资源与 user_api_scopes
  bot/obo_bind.py           # /bind + JWT scope 诊断
  bot/user_token_store.py   # OBO token（内存 + UC）
  bot/session_store.py      # 会话（内存 + UC）
  bot/uc_sql.py             # Statement Execution helper
  bot/runtime_lock.py       # 双实例互斥
  bot/slog.py               # 结构化 JSON 日志（脱敏）
  bot/i18n.py               # Bot 文案 zh/en
  bot/lark_user.py          # open_id → email
  bot/lark_ws.py            # 鉴权、问答、菜单
  bot/genie_one_client.py   # Genie One MCP（用户 token）
  bot/genie_client.py       # Genie Space Conversation API
  bot/reply_format.py       # 卡片 / deep_link
  bot/file_ingest.py        # 入站文件 → Volume
  bot/ops_notify.py         # Job 失败时可选 Lark 私信
  bridge/                   # Sheet / Docs / IM 出站
  jobs/                     # 保活 Job
  notebooks/                # 示例与 keepalive 脚本
```

原则：扩展 Genie 交互请修改 `bot/`；批同步请修改 `bridge/`；部署入口保持 `deploy_phase_a.sh` / `deploy_keepalive.sh`。

---

## 5. 运行时能力（已落地）

| 能力 | 说明 |
|------|------|
| 夜间保活 | UTC 23:50 stop / 00:50 start，避开日间高峰 |
| UC 持久化 | `bot_sessions`、`bot_obo_tokens` 可跨越 App 重启保留；token 仍受约 55 分钟 TTL 限制 |
| RuntimeLock | 本机与 App 竞争同一 WS 租约，避免重复回复 |
| 失败通知 | Keep-alive Job 邮件；可选 Lark 私信 |
| 结构化日志 | `bot/slog.py` 单行 JSON，自动脱敏 token |

`apps update` 为**整表替换**：`deploy_phase_a.sh` 每次会同时写回 `user_api_scopes` 与 Lark secret resources，请勿单独更新其中一项。

---

## 6. 配置检查清单

### Databricks

- [ ] Workspace 已启用 On-Behalf-Of / User authorization  
- [ ] App `user_api_scopes` 包含：`genie`、`dashboards.genie`、`sql`  
- [ ] 修改 scopes 后执行 **stop + start**（仅 redeploy 有时不足）  
- [ ] 用户通过无痕窗口重新同意授权；`/bind` 成功页 scopes 含 `genie`  
- [ ] `APP_PUBLIC_URL` 等于实际 Apps URL（无尾斜杠）  
- [ ] Secret scope `lark_integration`：`lark_app_id` / `lark_app_secret` / `lark_open_api_base`  
- [ ] App SP 对 UC 三表与 warehouse 已授权（见 [`auth-and-secrets.md`](auth-and-secrets.md)）

### Lark

- [ ] 长连接事件：`im.message.receive_v1`、`application.bot.menu_v6`、`card.action.trigger`  
- [ ] `contact:user.email:readonly`（通讯录可见范围覆盖对话用户）  
- [ ] 生产环境仅保留 App 一侧的一份 WS；开发时请勿与 App 同时连接同一 Bot  

配额与约 24 小时暂停说明：[`databricks-free-edition-limits.md`](databricks-free-edition-limits.md)。

---

## 7. 排障速查

| 现象 | 原因 / 处理 |
|------|-------------|
| Contact 返回 `open_id` 但无邮箱 | 缺少 `contact:user.email:readonly`；或使用 `绑定 you@email.com` |
| Genie MCP 返回 403 | OBO JWT 缺少 `genie`：补齐 scopes → stop/start → 使用**无痕窗口**重新同意 → 再次 `/bind` |
| 打开链接后显示 JSON | 应打开完整 `/bind?...`；首页为 HTML 引导页，并非状态 API |
| deep link 提示「Conversation not found」 | 会话由 App SP 创建；须改用用户 token |
| 重复回复或顺序错乱 | 本机 `lark_ws` 与 App 同时连接；依赖 RuntimeLock，或停止本机进程 |
| 部署后 secrets/scopes 丢失 | `apps update` 为整表替换；请使用 `deploy_phase_a.sh` |
| App 在日间不可用 | 检查 keep-alive Job 与 fair usage；参见 Free Edition 限制说明 |

---

## 8. 用户侧命令（Lark）

| 输入 | 作用 |
|------|------|
| 提问 | 未绑定时发送 `/bind` 链接 |
| `绑定` / `/bind` | 请求绑定链接 |
| `绑定 you@email.com` | Contact 无法读取邮箱时手动映射 |
| `reset` | 清空 conversation |
| `switch` | 切换 agent |

系统文案随用户消息中/英切换（`bot/i18n.py`）；Genie 正文语言由 Genie 决定。

---

## 9. 继续开发建议

**建议优先考虑的方向**

- Bridge：Sheet 写回、Docs→MD、出站卡片能力完善（`bridge/` + notebooks）  
- Bot：入站文件解析入库、多 Space 路由、更严格的限流队列  
- 账户升级后：Account OAuth + refresh，以替代约 55 分钟重新绑定  
- 可观测性：基于 `slog` 的告警与仪表盘  

**不建议的做法（除非产品目标变更）**

- 将 Genie 问答回退为 App SP（会破坏 deep link 与 RLS）  
- 在 Free Edition 上强制采用 Account App Connection  
- 将本机与 App 同时连接同一 Lark Bot 当作负载分担方式  

本地验证：见 [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md) 中的 Local smoke 一节；生产部署：`./app/deploy_phase_a.sh` 与 `./jobs/deploy_keepalive.sh`。
