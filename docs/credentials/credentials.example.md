# Credentials template (copy → `local_secrets.md`, gitignored)

> Fill locally. **Never commit** `local_secrets.md`. Prefer Databricks Secret scope in production.
> Auth guide: [`../en/auth-and-secrets.md`](../en/auth-and-secrets.md) · [中文](../cn/auth-and-secrets.md).

## Databricks

- **`databricks_host`**: `https://YOUR_WORKSPACE.cloud.databricks.com`
- **`databricks_profile`**: `DEFAULT`
- **`databricks_user`**: `you@example.com`
- **`sql_warehouse_id`**: `YOUR_WAREHOUSE_ID`

## Lark

- **`lark_app_id`**: `cli_YOUR_LARK_APP_ID`
- **`lark_app_secret`**: `(from Lark Open Platform)`
- **`lark_open_api_base`**: `https://open.larksuite.com`
- **`lark_cli_user_open_id`**: `ou_YOUR_LARK_OPEN_ID` (optional ops notify)

## Genie (optional local smoke)

- **`genie_space_id`**: `YOUR_GENIE_SPACE_ID`

## Put into Databricks Secrets (example)

```bash
PROFILE=<PROFILE>
SCOPE=lark_integration
databricks api post /api/2.0/secrets/put --profile "$PROFILE" --json "{
  \"scope\": \"$SCOPE\", \"key\": \"lark_app_id\", \"string_value\": \"cli_YOUR_LARK_APP_ID\"
}"
# repeat for lark_app_secret, lark_open_api_base
```
