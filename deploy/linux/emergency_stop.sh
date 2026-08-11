#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

print_step "Emergency stop"
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
  run_sudo systemctl stop "${SERVICE_NAME}" || true
  run_sudo systemctl disable "${SERVICE_NAME}" || true
fi

cat <<'EOF'
Stopped local systemd service if it existed.

If XXL-JOB is used, disable the XXL-JOB task in the scheduler console.
This package intentionally does not switch business rules to off by editing .env,
because the production launcher fixes enforce policy for safety and traceability.
Business-rule rollback must use a reviewed rollback package or owner-approved script.
EOF
