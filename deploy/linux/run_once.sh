#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

cd "${PROJECT_ROOT}"
require_env_file

print_step "Run one audit loop"
GUOBU_AUTO_AUDIT_ENV_FILE="${ENV_FILE}" bash tools/start_guobu_linux_auto_audit.sh --once
