#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

cd "${PROJECT_ROOT}"

if [[ -f "${ENV_FILE}" ]]; then
  printf 'Env file already exists: %s\n' "${ENV_FILE}"
  read -r -p "Overwrite it? Type YES to continue: " confirm
  [[ "${confirm}" == "YES" ]] || exit 0
fi

read_value() {
  local name="$1"
  local prompt="$2"
  local default="${3:-}"
  local value
  if [[ -n "${default}" ]]; then
    read -r -p "${prompt} [${default}]: " value
    value="${value:-${default}}"
  else
    read -r -p "${prompt}: " value
  fi
  [[ -n "${value}" ]] || fail "${name} cannot be empty"
  printf '%s\n' "${value}"
}

read_secret_value() {
  local name="$1"
  local prompt="$2"
  local value
  read -r -s -p "${prompt}: " value
  printf '\n'
  [[ -n "${value}" ]] || fail "${name} cannot be empty"
  printf '%s\n' "${value}"
}

print_step "Collect production configuration"
vision_base="$(read_value VISION_API_BASE_URL 'Model API base URL')"
vision_key="$(read_secret_value VISION_API_KEY 'Model API key')"
vision_model="$(read_value VISION_MODEL_NAME 'Model name' 'qwen3.7-plus')"
collector_base="$(read_value GUOBU_COLLECTOR_BASE_URL 'Backend collector base URL' 'https://approval.jhddsz.com')"
approval_base="$(read_value GUOBU_APPROVAL_BASE_URL 'Backend approval base URL' "${collector_base}")"
auth_token="$(read_secret_value GUOBU_AUTH_TOKEN 'Backend Authorization token')"
approval_token="$(read_secret_value MACHINE_APPROVAL_AUTH_TOKEN 'Machine approval Authorization token')"

umask 077
cat > "${ENV_FILE}" <<EOF
VISION_API_BASE_URL=$(shell_quote "${vision_base}")
VISION_API_KEY=$(shell_quote "${vision_key}")
VISION_MODEL_NAME=$(shell_quote "${vision_model}")

GUOBU_COLLECTOR_BASE_URL=$(shell_quote "${collector_base}")
GUOBU_APPROVAL_BASE_URL=$(shell_quote "${approval_base}")
GUOBU_AUTH_TOKEN=$(shell_quote "${auth_token}")
MACHINE_APPROVAL_AUTH_TOKEN=$(shell_quote "${approval_token}")

GUOBU_AUDIT_STATE_DIR='/var/lib/audit_robot/state'
GUOBU_AUDIT_TEMP_DIR='/tmp/audit_robot_guobu'
GUOBU_AUDIT_LEASE_SECONDS='3600'
GUOBU_POLL_INTERVAL_SECONDS='600'
GUOBU_PENDING_HEARTBEAT_THRESHOLD='5'
GUOBU_PAGE_SIZE='20'
GUOBU_MAX_FETCH_PAGES='0'
GUOBU_EXIT_NONZERO_ON_ERRORS='true'
GUOBU_RUN_USER='auditrobot'

SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE='true'
EOF

chmod 600 "${ENV_FILE}"
print_step "Env file created"
printf 'Created: %s\n' "${ENV_FILE}"
echo "Next: bash deploy/linux/preflight.sh"
