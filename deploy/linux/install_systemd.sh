#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

cd "${PROJECT_ROOT}"
require_env_file

service_file="/etc/systemd/system/${SERVICE_NAME}.service"
python_bin="$(detect_python)"
load_env_file
runtime_user="${GUOBU_RUN_USER:-${RUN_USER}}"

print_step "Install systemd service: ${SERVICE_NAME}"
ensure_run_user "${runtime_user}"
run_sudo mkdir -p "${GUOBU_AUDIT_STATE_DIR:-/var/lib/audit_robot/state}" "${GUOBU_AUDIT_TEMP_DIR:-/tmp/audit_robot_guobu}"
run_sudo chown -R "${runtime_user}:${runtime_user}" "${GUOBU_AUDIT_STATE_DIR:-/var/lib/audit_robot/state}" "${GUOBU_AUDIT_TEMP_DIR:-/tmp/audit_robot_guobu}"
run_sudo chown "${runtime_user}:${runtime_user}" "${ENV_FILE}"
run_sudo chmod 600 "${ENV_FILE}"

tmp_service="$(mktemp)"
cat > "${tmp_service}" <<EOF
[Unit]
Description=Guobu Auto Audit Loop
After=network-online.target
Wants=network-online.target
StartLimitBurst=3
StartLimitIntervalSec=300

[Service]
Type=simple
User=${runtime_user}
Group=${runtime_user}
WorkingDirectory=${PROJECT_ROOT}
ExecStartPre=/bin/bash ${PROJECT_ROOT}/deploy/linux/preflight.sh
ExecStart=/bin/bash ${PROJECT_ROOT}/tools/start_guobu_linux_auto_audit.sh
Restart=always
RestartSec=10
Environment=GUOBU_AUTO_AUDIT_ENV_FILE=${ENV_FILE}
Environment=GUOBU_RUN_USER=${runtime_user}
Environment=PYTHON_BIN=${python_bin}
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

run_sudo cp "${tmp_service}" "${service_file}"
rm -f "${tmp_service}"
run_sudo systemctl daemon-reload
run_sudo systemctl enable "${SERVICE_NAME}"

print_step "Systemd service installed"
printf 'Service: %s\n' "${SERVICE_NAME}"
echo "Next: bash deploy/linux/start.sh"
