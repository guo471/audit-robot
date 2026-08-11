#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

print_step "Start ${SERVICE_NAME}"
run_sudo systemctl start "${SERVICE_NAME}"
run_sudo systemctl --no-pager status "${SERVICE_NAME}"
