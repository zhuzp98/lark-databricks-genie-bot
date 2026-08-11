# How this folder was prepared

Path: `github_publish/lark-databricks-genie-bot/`

- Copied from the working tree **without** `.venv`, `__pycache__`, or `docs/credentials/local_secrets.md`
- Personal IDs (workspace URL, emails, open_id, warehouse, App URL, SP id, job ids) replaced with `YOUR_*` / `<PROFILE>` placeholders
- Added root `README.md`, `LICENSE` (MIT), `.gitignore`, `.env.example`, `docs/credentials/credentials.example.md`

## Push to GitHub

```bash
cd github_publish/lark-databricks-genie-bot
git init
git add .
git status   # confirm no secrets
git commit -m "Initial public snapshot: Lark Genie Bot on Databricks Apps (OBO)"
gh repo create lark-databricks-genie-bot --private --source=. --remote=origin --push
# or: git remote add origin git@github.com:<you>/lark-databricks-genie-bot.git && git push -u origin main
```

Before push, search once more:

```bash
rg -n 'outlook\.com|ou_[a-f0-9]{10,}|cli_aafd|dbc-[a-f0-9]|442b802|access_token.:' .
```
