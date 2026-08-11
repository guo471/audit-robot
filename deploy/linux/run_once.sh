#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

cd "${PROJECT_ROOT}"
require_env_file
ensure_env_readable_by_run_user

print_step "Run one audit loop"
run_as_runtime_user bash tools/start_guobu_linux_auto_audit.sh --once
