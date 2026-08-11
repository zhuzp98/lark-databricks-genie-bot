#!/usr/bin/env bash
# Phase A: upload source, create (if needed), deploy, and start lark-genie-bot.
# Run from a normal Terminal with `databricks auth login --profile <PROFILE>`.
set -euo pipefail

PROFILE="${DATABRICKS_CONFIG_PROFILE:-DEFAULT}"
APP_NAME="lark-genie-bot"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Free Edition: SCIM /Me is often Forbidden — use fixed Workspace path.
USER_HOME="${DATABRICKS_USER_HOME:?Set DATABRICKS_USER_HOME to your Workspace user path, e.g. you@example.com}"
WS_PATH="/Workspace/Users/${USER_HOME}/lark_genie_bot"

echo "==> Using profile=${PROFILE} source=${ROOT} -> ${WS_PATH}"

databricks workspace mkdirs "${WS_PATH}" --profile "${PROFILE}"
databricks workspace import-dir "${ROOT}" "${WS_PATH}" --profile "${PROFILE}" --overwrite

if ! databricks apps get "${APP_NAME}" --profile "${PROFILE}" >/dev/null 2>&1; then
  echo "==> Creating app ${APP_NAME}"
  databricks apps create --json @"${ROOT}/app/create_app.json" --no-compute --profile "${PROFILE}"
else
  echo "==> App ${APP_NAME} already exists"
fi

echo "==> Ensure OBO scopes + secret resources (update is replace-all)"
databricks apps update "${APP_NAME}" --profile "${PROFILE}" --json "{
  \"description\": \"Lark ↔ Genie One bot (WebSocket + OBO bind). Free Edition.\",
  \"user_api_scopes\": [\"dashboards.genie\", \"sql\", \"genie\"],
  \"resources\": [
    {\"name\": \"lark-app-id\", \"secret\": {\"scope\": \"lark_integration\", \"key\": \"lark_app_id\", \"permission\": \"READ\"}},
    {\"name\": \"lark-app-secret\", \"secret\": {\"scope\": \"lark_integration\", \"key\": \"lark_app_secret\", \"permission\": \"READ\"}},
    {\"name\": \"lark-open-api-base\", \"secret\": {\"scope\": \"lark_integration\", \"key\": \"lark_open_api_base\", \"permission\": \"READ\"}}
  ]
}" >/dev/null

echo "==> Starting compute (required before first deploy on Free Edition)"
databricks apps start "${APP_NAME}" --profile "${PROFILE}" || true

echo "==> Deploying"
databricks apps deploy "${APP_NAME}" \
  --source-code-path "${WS_PATH}" \
  --mode SNAPSHOT \
  --profile "${PROFILE}"

echo "==> Done. Check:"
echo "    databricks apps get ${APP_NAME} --profile ${PROFILE}"
echo "    databricks apps logs ${APP_NAME} --profile ${PROFILE}"
echo "Remember: stop local 'python -m lark_integration.bot.lark_ws' to avoid dual WS."
echo "OBO: open App URL /bind after deploy; first visit prompts User authorization consent."
