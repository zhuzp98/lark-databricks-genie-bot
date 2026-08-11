# lark_integration 架构与扩展指南

面向在本仓库上继续开发的同事：说明当前已落地的架构、OBO 身份模型、模块职责，以及常见坑与扩展方向。部署与运维细节见 [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md)；认证与密钥见 [`auth-and-secrets.md`](auth-and-secrets.md)。

English: [`../en/architecture.md`](../en/architecture.md)

---

## 1. 目标

| 目标 | 做法 |
|------|------|
| Lark 里自然语言查数 | Bot 经 Genie One MCP / Genie Space API 问答，卡片回复 |
| Genie One deep link 打开为本人会话 | 调 Genie 时必须用**终端用户** token，不能用 App SP |
| UC / RLS / 审计按真人 | 同上；App SP 只跑进程，不当问答身份 |
| Free Edition 可跑 | 单 App + Apps User Authorization（`/bind`）；夜间 Job 对抗 ~24h 暂停 |

**混合结构**：`bot` = 交互面（入站 + Genie）；`bridge` = 批式 Data Plane（Sheet/Docs/出站 IM）。不要把批同步硬塞进 App 常驻路径。

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
  ├─ RuntimeLock（UC bot_runtime_lease，防双 WS）
  └─ GenieOneClient / GenieClient（Bearer = 用户 OBO token）
        │
        ▼
  Genie One MCP  →  deep_link（用户本人会话）

定时 Job：UTC 23:50 stop / 00:50 start（对抗 App ~24h 暂停）

bridge/（Jobs / notebooks 调用）：Sheet ↔ UC、Docs → Volume、出站 text/card/file
```

### 绑定流程（OBO）

1. Lark 提问 → `open_id` 解析企业邮箱（Contact API）→ 查 token。  
2. 无有效 token → 下发 `{APP_PUBLIC_URL}/bind?open_id=&email=`。  
3. 用户已登录 Databricks 时打开链接 → Apps 注入 `x-forwarded-access-token` + `x-forwarded-email`。  
4. `/bind` 校验 JWT scopes（须含 `genie`）并写入内存 + UC。  
5. 再问 → 用户 token 调 Genie → 卡片带真实 deep link。

### 身份键

| 键 | 用途 |
|----|------|
| Lark `open_id` | 会话与绑定关联 |
| Lark / Databricks **邮箱** | token 主键（约定一致） |
| Apps OBO JWT | 实际调用 Genie 的凭证 |

邮箱对不齐时：用户发 `绑定 you@email.com`，或仅带 `open_id` 打开 `/bind`，用 `x-forwarded-email` 作键。

---

## 3. 为何是这套 OBO（而非 Slack 双 App）

参考实现：[Genie + Slack OBO](https://github.com/dahora-databricks/databricks-genie-slack-obo-oauth) 依赖 Account Console 自定义 OAuth App Connection + 独立 broker。

Free Edition **无 Account Console / account-level APIs**，不能照搬。等价方案：

- **单 App** + 内置 **Apps User Authorization**（`/bind`）  
- Token ~55 min TTL，**无 refresh**；过期须重绑  
- App SP：托管进程、Lark WS、读 Secrets、写 UC 状态表  

商业 / 企业账户可再评估：Account App Connection +（可选）独立 broker + refresh。当前代码路径不必为此阻塞。

---

## 4. 代码地图

```text
lark_integration/
  app/app_main.py           # FastAPI + 首页 + WS 线程
  app/deploy_phase_a.sh     # 上传 / scopes+secrets / deploy
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
  bot/ops_notify.py         # Job 失败可选 Lark DM
  bridge/                   # Sheet / Docs / IM 出站
  jobs/                     # 保活 Job
  notebooks/                # PoC / keepalive 示例
```

原则：扩展 Genie 交互改 `bot/`；批同步改 `bridge/`；部署入口保持 `deploy_phase_a.sh` / `deploy_keepalive.sh`。

---

## 5. 运行时能力（已落地）

| 能力 | 说明 |
|------|------|
| 夜间保活 | UTC 23:50 stop / 00:50 start，避开白天高峰 |
| UC 持久化 | `bot_sessions`、`bot_obo_tokens` 撑过 App 重启；token 仍受 ~55 min TTL |
| RuntimeLock | 本机与 App 争用同一 WS 租约，避免双答 |
| 失败通知 | Keep-alive Job 邮件；可选 Lark DM |
| 结构化日志 | `bot/slog.py` 单行 JSON，自动脱敏 token |

`apps update` 是**整表替换**：`deploy_phase_a.sh` 每次同时写回 `user_api_scopes` 与 Lark secret resources，勿只改一项。

---

## 6. 配置检查清单

### Databricks

- [ ] Workspace 启用 On-Behalf-Of / User authorization  
- [ ] App `user_api_scopes`：`genie`、`dashboards.genie`、`sql`  
- [ ] 改 scopes 后 **stop + start**（仅 redeploy 有时不够）  
- [ ] 用户无痕窗口重新同意授权；`/bind` 成功页 scopes 含 `genie`  
- [ ] `APP_PUBLIC_URL` = 实际 Apps URL（无尾斜杠）  
- [ ] Secret scope `lark_integration`：`lark_app_id` / `lark_app_secret` / `lark_open_api_base`  
- [ ] App SP 对 UC 三表 + warehouse 有权限（见 [`auth-and-secrets.md`](auth-and-secrets.md)）

### Lark

- [ ] 长连接事件：`im.message.receive_v1`、`application.bot.menu_v6`、`card.action.trigger`  
- [ ] `contact:user.email:readonly`（通讯录范围覆盖对话用户）  
- [ ] 生产只留 App 一份 WS；开发时勿与 App 双开同一 Bot  

配额与 24h 暂停：[`databricks-free-edition-limits.md`](databricks-free-edition-limits.md)。

---

## 7. 排障速查

| 现象 | 原因 / 处理 |
|------|-------------|
| Contact 有 `open_id` 无邮箱 | 缺 `contact:user.email:readonly`；或用 `绑定 you@email.com` |
| Genie MCP 403 | OBO JWT 无 `genie`：补 scopes → stop/start → **无痕**重同意 → 再 `/bind` |
| 打开「链接」是 JSON | 应打开完整 `/bind?...`；首页是 HTML 引导，不是状态 API |
| deep link「Conversation not found」 | 用了 App SP 建会话；必须用户 token |
| 双答 / 乱序 | 本机 `lark_ws` 与 App 同时连；依赖 RuntimeLock 或停掉本机 |
| 部署后 secrets/scopes 丢了 | `apps update` 整表替换；用 `deploy_phase_a.sh` |
| App 白天挂了 | 查 keep-alive Job / fair usage；见 Free Edition limits |

---

## 8. 用户侧命令（Lark）

| 输入 | 作用 |
|------|------|
| 提问 | 未绑定则收 `/bind` 链接 |
| `绑定` / `/bind` | 索取绑定链接 |
| `绑定 you@email.com` | Contact 无邮箱时手动映射 |
| `reset` | 清空 conversation |
| `switch` | 切换 agent |

系统文案随用户消息中/英切换（`bot/i18n.py`）；Genie 正文语言由 Genie 决定。

---

## 9. 继续开发建议

**适合继续做的方向**

- Bridge：Sheet 写回、Docs→MD、出站卡片产品化（`bridge/` + notebooks）  
- Bot：入站文件解析进表、多 Space 路由、更强限流队列  
- 账户升级后：Account OAuth + refresh，替代 ~55 min 重绑  
- 观测：基于 `slog` 的告警 / 仪表盘  

**刻意不要做的（除非改目标）**

- 把 Genie 问答回落到 App SP（会破坏 deep link 与 RLS）  
- Free Edition 上强行上 Account App Connection  
- 本机与 App 双开同一 Lark Bot 做「负载均衡」  

本地调试：见 [`../../lark_integration/app/README.md`](../../lark_integration/app/README.md) 的 Local smoke；生产部署：`./app/deploy_phase_a.sh` 与 `./jobs/deploy_keepalive.sh`。
