#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

ENV_FILE="${GUOBU_AUTO_AUDIT_ENV_FILE:-${PROJECT_ROOT}/.env}"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 2
fi

GUOBU_AUTO_AUDIT_ENV_FILE="${ENV_FILE}" bash tools/start_guobu_linux_auto_audit.sh --preflight-only

python_bin="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
if [[ ! -x "${python_bin}" ]]; then
  python_bin="python3"
fi

"${python_bin}" - <<'PY'
import importlib

for name in ("zxingcpp", "cv2", "joblib", "sklearn", "numpy", "PIL"):
    importlib.import_module(name)

print("runtime_imports_ok=true")
PY
