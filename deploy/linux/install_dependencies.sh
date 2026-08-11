#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y \
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
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r photo_authenticity/requirements-runtime.txt

node --version
npm --version
python --version
