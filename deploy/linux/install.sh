#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

cd "${PROJECT_ROOT}"

print_step "Install system packages"
if command -v apt-get >/dev/null 2>&1; then
  run_sudo apt-get update
  run_sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    nodejs \
    npm \
    git \
    curl \
    ca-certificates \
    build-essential \
    libglib2.0-0 \
    libgl1 \
    libgomp1
elif command -v dnf >/dev/null 2>&1; then
  run_sudo dnf install -y \
    python3 \
    python3-pip \
    nodejs \
    npm \
    git \
    curl \
    ca-certificates \
    gcc \
    gcc-c++ \
    make \
    glib2 \
    mesa-libGL \
    libgomp
elif command -v yum >/dev/null 2>&1; then
  run_sudo yum install -y \
    python3 \
    python3-pip \
    nodejs \
    npm \
    git \
    curl \
    ca-certificates \
    gcc \
    gcc-c++ \
    make \
    glib2 \
    mesa-libGL \
    libgomp
else
  fail "Unsupported Linux package manager. Install Python 3.11+, Node.js 18+, git, curl, build tools, libGL, glib2, and libgomp manually."
fi

print_step "Check Python version"
python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required")
print("python_version_ok=true")
PY

print_step "Create virtual environment and install Python dependencies"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r photo_authenticity/requirements-runtime.txt

print_step "Check Node.js version"
require_node18
npm --version

print_step "Create runtime user and directories"
ensure_run_user "${RUN_USER}"
run_sudo mkdir -p /var/lib/audit_robot/state /tmp/audit_robot_guobu
run_sudo chown -R "${RUN_USER}:${RUN_USER}" /var/lib/audit_robot /tmp/audit_robot_guobu
run_sudo chmod 750 /var/lib/audit_robot /var/lib/audit_robot/state
run_sudo chmod 750 /tmp/audit_robot_guobu

print_step "Install complete"
echo "Next: bash deploy/linux/configure_env.sh"
