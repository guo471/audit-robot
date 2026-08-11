#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

cd "${PROJECT_ROOT}"
require_env_file
load_env_file

print_step "Check package tree"
scan_package_tree "${PROJECT_ROOT}"

print_step "Check env file permissions"
assert_env_permissions

print_step "Run startup preflight"
GUOBU_AUTO_AUDIT_ENV_FILE="${ENV_FILE}" bash tools/start_guobu_linux_auto_audit.sh --preflight-only

print_step "Check Node.js version"
require_node18

print_step "Import runtime modules"
python_bin="$(detect_python)"
"${python_bin}" - <<'PY'
import importlib

for name in ("zxingcpp", "cv2", "joblib", "sklearn", "numpy", "PIL"):
    importlib.import_module(name)

print("runtime_imports_ok=true")
PY

print_step "Check runtime directory write permissions"
state_dir="${GUOBU_AUDIT_STATE_DIR:-/var/lib/audit_robot/state}"
temp_dir="${GUOBU_AUDIT_TEMP_DIR:-/tmp/audit_robot_guobu}"
mkdir -p "${state_dir}" "${temp_dir}"
state_probe="${state_dir}/.preflight_write_test"
temp_probe="${temp_dir}/.preflight_write_test"
printf 'ok\n' > "${state_probe}"
printf 'ok\n' > "${temp_probe}"
rm -f "${state_probe}" "${temp_probe}"
echo "runtime_dirs_writable=true"

print_step "Preflight complete"
echo "Next: bash deploy/linux/run_once.sh"
