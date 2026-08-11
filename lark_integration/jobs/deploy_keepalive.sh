#!/usr/bin/env bash
# Phase B: upload keepalive notebook + create/update stop & start Jobs (UTC).
# Schedule: stop 23:50 UTC, start 00:50 UTC (overnight lake-refresh window).
set -euo pipefail

PROFILE="${DATABRICKS_CONFIG_PROFILE:-DEFAULT}"
USER_HOME="${DATABRICKS_USER_HOME:?Set DATABRICKS_USER_HOME to your Workspace user path, e.g. you@example.com}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NB_SRC="${ROOT}/notebooks/04_app_keepalive.py"
NB_WS="/Workspace/Users/${USER_HOME}/lark_genie_bot/notebooks/04_app_keepalive"
JOBS_DIR="${ROOT}/jobs"

echo "==> profile=${PROFILE}"
echo "==> notebook ${NB_SRC} -> ${NB_WS}"

databricks workspace mkdirs "$(dirname "${NB_WS}")" --profile "${PROFILE}"
# Import as SOURCE notebook (.py Databricks notebook format)
databricks workspace import "${NB_WS}" \
  --file "${NB_SRC}" \
  --language PYTHON \
  --format SOURCE \
  --overwrite \
  --profile "${PROFILE}"

upsert_job() {
  local json_file="$1"
  local name
  name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "${json_file}")"
  local existing_id=""
  existing_id="$(databricks jobs list --profile "${PROFILE}" -o json 2>/dev/null | python3 -c '
import json, sys
name = sys.argv[1]
raw = sys.stdin.read().strip() or "[]"
jobs = json.loads(raw)
# CLI may return a list or {"jobs": [...]}
if isinstance(jobs, dict):
    jobs = jobs.get("jobs") or []
for j in jobs:
    n = (j.get("settings") or {}).get("name") or j.get("name") or ""
    if n == name:
        print(j["job_id"])
        break
' "${name}" || true)"

  if [[ -n "${existing_id}" ]]; then
    echo "==> Updating job ${name} id=${existing_id}"
    local payload
    payload="$(python3 -c '
import json,sys
body=json.load(open(sys.argv[1]))
print(json.dumps({"job_id": int(sys.argv[2]), "new_settings": body}))
' "${json_file}" "${existing_id}")"
    databricks jobs reset --json "${payload}" --profile "${PROFILE}"
  else
    echo "==> Creating job ${name}"
    databricks jobs create --json @"${json_file}" --profile "${PROFILE}"
  fi
}

upsert_job "${JOBS_DIR}/keepalive_stop.job.json"
upsert_job "${JOBS_DIR}/keepalive_start.job.json"

echo "==> Done. Listed keep-alive jobs:"
databricks jobs list --profile "${PROFILE}" -o json | python3 - <<'PY'
import json, sys
raw = sys.stdin.read().strip() or "[]"
jobs = json.loads(raw)
if isinstance(jobs, dict):
    jobs = jobs.get("jobs") or []
for j in jobs:
    n = (j.get("settings") or {}).get("name") or ""
    if "lark-genie-bot-keepalive" not in n:
        continue
    s = j.get("settings") or {}
    sch = s.get("schedule") or {}
    print(
        f"  id={j['job_id']} name={n} "
        f"cron={sch.get('quartz_cron_expression')} "
        f"tz={sch.get('timezone_id')} pause={sch.get('pause_status')}"
    )
PY

echo ""
echo "Manual smoke (optional):"
echo "  databricks jobs run-now YOUR_KEEPALIVE_STOP_JOB_ID --profile ${PROFILE}   # stop"
echo "  databricks jobs run-now YOUR_KEEPALIVE_START_JOB_ID --profile ${PROFILE}   # start"
echo "NOTE: start clears in-memory state; Phase C restores unexpired OBO tokens from UC."
echo "Phase D: email on_failure → YOUR_DATABRICKS_USER@example.com; Lark DM via notify_open_id widget."
echo "Schedule (UTC): stop 23:50, start 00:50 (~60 min overnight window)."
