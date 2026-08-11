# Databricks Free Edition limits (what matters here)

Official docs: [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations).

中文版: [`../cn/databricks-free-edition-limits.md`](../cn/databricks-free-edition-limits.md).

After fair-usage limits are hit, compute may shut down and be unavailable for the rest of the day; **data and configuration are retained**. No SLA / official support; **commercial use is prohibited**.

---

## Constraints that shape this project

| Constraint | Impact | Our mitigation |
|------------|--------|----------------|
| Apps ≤ **3**; ~**24h** auto-pause after start | Bot cannot be a forever process | Single App; UTC overnight stop/start Jobs |
| No Account Console / account-level APIs | Cannot register custom OAuth App Connections | Apps User Authorization + `/bind` |
| 1× SQL Warehouse, `2X-Small` | Genie and Jobs share one warehouse | Stagger batch work vs Q&A peaks |
| Jobs concurrency ≤ **5** | Keep-alive + sync Jobs compete | Keep-alive Jobs tiny; serialize/merge batch |
| Outbound network restricted by default | Cannot reach `open.larksuite.com` | Unlock outbound (e.g. LinkedIn verification) |
| No SSO / SCIM | Auth via email OTP / Google / Microsoft | Assume Lark enterprise email = Databricks login |
| Fair usage can stop compute | Bot / Jobs down for the day | Job failure alerts; upgrade for paid capacity |

---

## Other quotas (short)

| Resource | Limit |
|----------|--------|
| Workspace / UC metastore | 1 each per account |
| Compute | Serverless only |
| Lakeflow pipelines | 1 active per pipeline type |
| AI Search / Lakebase / Model Serving | Count/feature caps (not on this Bot hot path) |

Unsupported examples: R/Scala, custom workspace storage, Online Tables, Clean Rooms, Knowledge Assistant — see the official page.

---

## Practical guidance

- Leave App quota headroom; expect ~1h nightly downtime (this repo defaults to UTC 23:50–00:50).  
- Do not assume OBO tokens can be refreshed; send users to `/bind` when expired.  
- For Account OAuth, higher concurrency, or SLA, upgrade to commercial/enterprise and re-evaluate a dual-app broker (see [`architecture.md`](architecture.md)).
