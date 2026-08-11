#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

cd "${PROJECT_ROOT}"
require_env_file
ensure_env_readable_by_run_user

print_step "Check package tree"
scan_package_tree "${PROJECT_ROOT}"

print_step "Check env file permissions"
assert_env_permissions

print_step "Check runtime directory permissions"
run_as_runtime_user bash -c 'source deploy/linux/lib/common.sh; load_env_file; assert_runtime_dirs_writable_by_run_user'

print_step "Run startup preflight"
run_as_runtime_user bash tools/start_guobu_linux_auto_audit.sh --preflight-only

print_step "Check Node.js version"
require_node18

print_step "Import runtime modules"
run_as_runtime_user bash -c 'source deploy/linux/lib/common.sh; load_env_file; python_bin="$(detect_python)"; "${python_bin}" -c "import importlib; [importlib.import_module(name) for name in (\"zxingcpp\", \"cv2\", \"joblib\", \"sklearn\", \"numpy\", \"PIL\")]; print(\"runtime_imports_ok=true\")"'

echo "runtime_dirs_writable=true"

print_step "Preflight complete"
echo "Next: bash deploy/linux/run_once.sh"
