# Databricks Free Edition 限制（与本项目相关）

官方文档：[Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)。

English: [`../en/databricks-free-edition-limits.md`](../en/databricks-free-edition-limits.md)。

超出公平使用配额后，计算资源可能被关闭，当日剩余时间可能不可用；**数据与配置会保留**。不提供 SLA / 官方支持；**禁止用于商业用途**。

---

## 对本仓库影响最大的约束

| 约束 | 影响 | 本项目对策 |
|------|------|------------|
| Apps ≤ **3**；启动后约 **24 小时**自动暂停 | Bot 无法作为长期常驻服务 | 使用单一 App；以 UTC 夜间 stop/start Job 保活 |
| 无 Account Console / account-level APIs | 无法注册自定义 OAuth App Connection | 使用 Apps User Authorization + `/bind` |
| 仅 1 个 SQL Warehouse，规格 `2X-Small` | Genie 与 Job 共用同一 warehouse | 批处理任务与问答高峰错开 |
| Jobs 并发 ≤ **5** | 保活与同步 Job 争用配额 | 保活 Job 保持轻量；批任务串行或合并 |
| 出站网络默认受限 | 无法访问 `open.larksuite.com` | 需通过 LinkedIn 验证等方式解锁出站 |
| 无 SSO / SCIM | 身份认证依赖邮箱 OTP / Google / Microsoft | 约定 Lark 企业邮箱与 Databricks 登录邮箱一致 |
| Fair usage 可能关停计算 | Bot / Job 当日不可用 | 配置 Job 失败通知；必要时升级付费账户 |

---

## 其他额度（简表）

| 资源 | 限制 |
|------|------|
| 工作区 / UC metastore | 每账户各 1 个 |
| 计算 | 仅支持 Serverless |
| Lakeflow 管道 | 每类型 1 条活跃管道 |
| AI Search / Lakebase / Model Serving | 有数量与功能上限（不在本 Bot 主路径中使用） |

不支持能力示例：R/Scala、Custom workspace storage、Online Tables、Clean Rooms、Knowledge Assistant 等——详见官方页面。

---

## 使用建议

- App 数量预留余量；预期每日夜间约有 1 小时停机窗口（本仓库默认 UTC 23:50–00:50）。  
- 请勿假设 OBO token 可 refresh；过期后引导用户执行 `/bind`。  
- 若需要 Account OAuth、更高并发或 SLA，请升级至商业 / 企业账户后，再评估双 App broker（见 [`architecture.md`](architecture.md)）。
