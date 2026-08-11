#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

ENV_FILE="${GUOBU_AUTO_AUDIT_ENV_FILE:-${PROJECT_ROOT}/.env}"
if [[ -f "${ENV_FILE}" ]]; then
  COMMON_FILE="${PROJECT_ROOT}/deploy/linux/lib/common.sh"
  if [[ ! -f "${COMMON_FILE}" ]]; then
    echo "Missing safe env loader: ${COMMON_FILE}" >&2
    exit 2
  fi
  # shellcheck disable=SC1090
  source "${COMMON_FILE}"
  load_env_file
fi

export SN_POLICY_VERSION=v2
export SN_BARCODE_MODE=enforce
export SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE=true
export DIGITAL_ACTIVATION_EVIDENCE_MODE=on
export PHOTO_AUTHENTICITY_MODE=enforce
export PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true
export PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED=false
export PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED=false

if [[ -z "${PYTHON_BIN:-}" && -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
STATE_DIR="${GUOBU_AUDIT_STATE_DIR:-${PROJECT_ROOT}/data/audit_state}"
TEMP_DIR="${GUOBU_AUDIT_TEMP_DIR:-/tmp/audit_robot_guobu}"
POLL_INTERVAL_SECONDS="${GUOBU_POLL_INTERVAL_SECONDS:-600}"
PENDING_HEARTBEAT_THRESHOLD="${GUOBU_PENDING_HEARTBEAT_THRESHOLD:-5}"
AUDIT_LEASE_SECONDS="${GUOBU_AUDIT_LEASE_SECONDS:-3600}"
PAGE_SIZE="${GUOBU_PAGE_SIZE:-20}"
MAX_FETCH_PAGES="${GUOBU_MAX_FETCH_PAGES:-0}"

MODE_ARGS=()
if [[ "${1:-}" == "--once" ]]; then
  MODE_ARGS+=(--once)
  shift
fi
if [[ "${GUOBU_EXIT_NONZERO_ON_ERRORS:-false}" == "true" ]]; then
  MODE_ARGS+=(--exit-nonzero-on-errors)
fi

"${PYTHON_BIN}" -m tools.guobu_linux_auto_audit --preflight-only >/dev/null

exec "${PYTHON_BIN}" -m tools.guobu_linux_auto_audit \
  "${MODE_ARGS[@]}" \
  --state-dir "${STATE_DIR}" \
  --temp-dir "${TEMP_DIR}" \
  --poll-interval-seconds "${POLL_INTERVAL_SECONDS}" \
  --pending-heartbeat-threshold "${PENDING_HEARTBEAT_THRESHOLD}" \
  --audit-lease-seconds "${AUDIT_LEASE_SECONDS}" \
  --page-size "${PAGE_SIZE}" \
  --max-fetch-pages "${MAX_FETCH_PAGES}" \
  "$@"
