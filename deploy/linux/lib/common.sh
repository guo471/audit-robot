#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${GUOBU_AUTO_AUDIT_ENV_FILE:-${PROJECT_ROOT}/.env}"
SERVICE_NAME="${GUOBU_SYSTEMD_SERVICE_NAME:-guobu-auto-audit}"
RUN_USER="${GUOBU_RUN_USER:-auditrobot}"
ALLOWED_ENV_KEYS=(
  VISION_API_BASE_URL
  VISION_API_KEY
  VISION_MODEL_NAME
  GUOBU_COLLECTOR_BASE_URL
  GUOBU_APPROVAL_BASE_URL
  GUOBU_AUTH_TOKEN
  MACHINE_APPROVAL_AUTH_TOKEN
  GUOBU_AUDIT_STATE_DIR
  GUOBU_AUDIT_TEMP_DIR
  GUOBU_AUDIT_LEASE_SECONDS
  GUOBU_POLL_INTERVAL_SECONDS
  GUOBU_PENDING_HEARTBEAT_THRESHOLD
  GUOBU_PAGE_SIZE
  GUOBU_MAX_FETCH_PAGES
  GUOBU_EXIT_NONZERO_ON_ERRORS
  GUOBU_RUN_USER
  PYTHON_BIN
  SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE
)

print_step() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit "${2:-2}"
}

require_env_file() {
  [[ -f "${ENV_FILE}" ]] || fail "Missing env file: ${ENV_FILE}. Run bash deploy/linux/configure_env.sh first."
}

assert_env_permissions() {
  require_env_file
  local mode owner current_user
  mode="$(stat -c '%a' "${ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${ENV_FILE}")"
  owner="$(stat -c '%U' "${ENV_FILE}" 2>/dev/null || stat -f '%Su' "${ENV_FILE}")"
  current_user="$(id -un)"
  if [[ "${mode}" != "600" && "${mode}" != "400" ]]; then
    fail "Unsafe env file mode ${mode}. Run: chmod 600 ${ENV_FILE}"
  fi
  if [[ "${owner}" != "${current_user}" && "${owner}" != "${RUN_USER}" && "${EUID:-$(id -u)}" -ne 0 ]]; then
    fail "Unsafe env file owner ${owner}. Expected ${current_user} or ${RUN_USER}."
  fi
}

load_env_file() {
  assert_env_permissions
  local line key value allowed
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line//[[:space:]]/}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" == *'$('* || "${line}" == *'`'* || "${line}" == *';'* || "${line}" == *'&&'* || "${line}" == *'||'* ]] && fail "Unsafe syntax in env file"
    [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || fail "Invalid env line: ${line%%=*}"
    key="${line%%=*}"
    value="${line#*=}"
    allowed=false
    for allowed_key in "${ALLOWED_ENV_KEYS[@]}"; do
      if [[ "${key}" == "${allowed_key}" ]]; then
        allowed=true
        break
      fi
    done
    [[ "${allowed}" == "true" ]] || fail "Unsupported env key: ${key}"
    if [[ "${value}" =~ ^\'.*\'$ ]]; then
      value="${value:1:${#value}-2}"
      value="${value//\\\'/\'}"
    elif [[ "${value}" =~ ^\".*\"$ ]]; then
      value="${value:1:${#value}-2}"
    fi
    [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || fail "Invalid newline in env value: ${key}"
    export "${key}=${value}"
  done < "${ENV_FILE}"
  for required_key in VISION_API_BASE_URL VISION_API_KEY GUOBU_COLLECTOR_BASE_URL GUOBU_AUTH_TOKEN MACHINE_APPROVAL_AUTH_TOKEN; do
    [[ -n "${!required_key:-}" ]] || fail "Missing required env key: ${required_key}"
  done
}

detect_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
  elif [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/.venv/bin/python"
  else
    printf 'python3\n'
  fi
}

require_node18() {
  command -v node >/dev/null 2>&1 || fail "Node.js is missing. Install Node.js 18+."
  node - <<'JS'
const major = Number(process.versions.node.split('.')[0]);
if (major < 18) {
  console.error(`Node.js 18+ is required, current=${process.version}`);
  process.exit(2);
}
console.log(`node_version_ok=${process.version}`);
JS
}

run_sudo() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    fail "This step needs root permission, but sudo is not available. Ask the server administrator to run it."
  fi
}

run_as_runtime_user() {
  local user="${GUOBU_RUN_USER:-${RUN_USER}}"
  if [[ "$(id -un)" == "${user}" ]]; then
    "$@"
  elif [[ "${EUID:-$(id -u)}" -eq 0 ]] && command -v runuser >/dev/null 2>&1; then
    runuser -u "${user}" -- env \
      GUOBU_AUTO_AUDIT_ENV_FILE="${ENV_FILE}" \
      PYTHON_BIN="${PYTHON_BIN:-}" \
      "$@"
  elif [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    sudo -u "${user}" env \
      GUOBU_AUTO_AUDIT_ENV_FILE="${ENV_FILE}" \
      PYTHON_BIN="${PYTHON_BIN:-}" \
      "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "${user}" env \
      GUOBU_AUTO_AUDIT_ENV_FILE="${ENV_FILE}" \
      PYTHON_BIN="${PYTHON_BIN:-}" \
      "$@"
  else
    fail "Must run as ${user}, or run with sudo so the script can switch user safely."
  fi
}

ensure_run_user() {
  local user="${1:-${RUN_USER}}"
  if id "${user}" >/dev/null 2>&1; then
    return
  fi
  if command -v useradd >/dev/null 2>&1; then
    run_sudo useradd --system --home-dir "${PROJECT_ROOT}" --shell /usr/sbin/nologin "${user}"
  elif command -v adduser >/dev/null 2>&1; then
    run_sudo adduser --system --home "${PROJECT_ROOT}" --no-create-home --disabled-login "${user}"
  else
    fail "Cannot create runtime user ${user}; useradd/adduser is missing."
  fi
}

ensure_env_readable_by_run_user() {
  require_env_file
  local user="${GUOBU_RUN_USER:-${RUN_USER}}"
  ensure_run_user "${user}"
  if [[ "$(id -un)" == "${user}" ]]; then
    assert_env_permissions
    return
  fi
  if [[ "${EUID:-$(id -u)}" -eq 0 ]] || command -v sudo >/dev/null 2>&1; then
    run_sudo chown "${user}:${user}" "${ENV_FILE}"
    run_sudo chmod 600 "${ENV_FILE}"
  else
    fail "Env file must be readable by ${user}. Run: sudo chown ${user}:${user} ${ENV_FILE} && sudo chmod 600 ${ENV_FILE}"
  fi
}

assert_runtime_dirs_writable_by_run_user() {
  local user="${GUOBU_RUN_USER:-${RUN_USER}}"
  local state_dir="${GUOBU_AUDIT_STATE_DIR:-/var/lib/audit_robot/state}"
  local temp_dir="${GUOBU_AUDIT_TEMP_DIR:-/tmp/audit_robot_guobu}"
  local needs_fix=false
  ensure_run_user "${user}"
  if ! run_as_runtime_user test -d "${state_dir}" >/dev/null 2>&1; then
    needs_fix=true
  elif ! run_as_runtime_user test -w "${state_dir}" >/dev/null 2>&1; then
    needs_fix=true
  fi
  if ! run_as_runtime_user test -d "${temp_dir}" >/dev/null 2>&1; then
    needs_fix=true
  elif ! run_as_runtime_user test -w "${temp_dir}" >/dev/null 2>&1; then
    needs_fix=true
  fi
  if [[ "${needs_fix}" == "true" ]]; then
    if [[ "$(id -un)" == "${user}" ]]; then
      fail "Runtime dirs are not ready for ${user}. Run bash deploy/linux/install.sh as root or sudo-capable user first."
    fi
    run_sudo mkdir -p "${state_dir}" "${temp_dir}"
    run_sudo chown -R "${user}:${user}" "${state_dir}" "${temp_dir}"
    run_sudo chmod 750 "${state_dir}" "${temp_dir}"
  fi
  run_as_runtime_user test -w "${state_dir}" || fail "State dir is not writable by ${user}: ${state_dir}"
  run_as_runtime_user test -w "${temp_dir}" || fail "Temp dir is not writable by ${user}: ${temp_dir}"
}

shell_quote() {
  local value="${1-}"
  printf "'%s'" "${value//\'/\'\\\'\'}"
}

scan_package_tree() {
  local root="${1:-${PROJECT_ROOT}}"
  local hit
  hit="$(find "${root}" \
    -path '*/.git' -o \
    -path '*/.worktrees' -o \
    -path '*/.venv' -o \
    -path '*/node_modules' -o \
    -path '*/.pytest_cache' -o \
    -path '*/__pycache__' -o \
    -name '.env' -o \
    -name '*.sqlite' -o \
    -name '*.db' -o \
    -name '*.log' -o \
    -name '*.pyc' -o \
    -name '*.bundle' -o \
    -name '*.zip' \
    2>/dev/null | while IFS= read -r candidate; do
      [[ "${candidate}" == "${PROJECT_ROOT}/.env" ]] && continue
      printf '%s\n' "${candidate}"
    done | head -n 1)"
  [[ -z "${hit}" ]] || fail "Forbidden package artifact found: ${hit}"
}
