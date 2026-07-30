# -*- coding: utf-8 -*-
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def run_powershell(script_name, *args):
    assert POWERSHELL, "PowerShell is required for local secret entry tests"
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_DIR / "tools" / script_name),
            *map(str, args),
        ],
        cwd=str(PROJECT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def test_run_with_local_secrets_loads_only_required_runtime_env(tmp_path):
    secrets = tmp_path / "vision.env"
    secret_value = "sk-" + "test-secret-should-not-print"
    secrets.write_text(
        "\n".join(
            [
                "VISION_API_BASE_URL=https://offline.invalid/compatible-mode/v1",
                f"VISION_API_KEY={secret_value}",
                "VISION_MODEL_NAME=test-model",
                "OPENAI_API_KEY=must-not-be-loaded",
            ]
        ),
        encoding="utf-8",
    )

    result = run_powershell(
        "run_with_local_vision_secrets.ps1",
        "-SecretsPath",
        secrets,
        "-Command",
        (
            f"if ($env:VISION_API_KEY -ne '{secret_value}') {{ exit 2 }}; "
            "if ($env:OPENAI_API_KEY -eq 'must-not-be-loaded') { exit 3 }; "
            "Write-Output ('ok:' + $env:VISION_MODEL_NAME)"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "ok:test-model" in result.stdout
    assert secret_value not in result.stdout
    assert secret_value not in result.stderr
    assert "must-not-be-loaded" not in result.stdout


def test_run_with_local_secrets_fails_without_required_key(tmp_path):
    secrets = tmp_path / "vision.env"
    secrets.write_text(
        "VISION_API_BASE_URL=https://offline.invalid/compatible-mode/v1\n",
        encoding="utf-8",
    )

    result = run_powershell(
        "run_with_local_vision_secrets.ps1",
        "-SecretsPath",
        secrets,
        "-Command",
        "Write-Output should-not-run",
    )

    assert result.returncode != 0
    assert "VISION_API_KEY" in (result.stderr + result.stdout)
    assert "should-not-run" not in result.stdout


def test_install_local_vision_secrets_from_clipboard_text_masks_values(tmp_path):
    secrets = tmp_path / "vision.env"
    secret_value = "sk-" + "test-secret-should-not-print"
    clipboard_text = "\n".join(
        [
            "地址: https://offline.invalid/compatible-mode/v1",
            f"密钥: {secret_value}",
        ]
    )

    result = run_powershell(
        "install_local_vision_secrets_from_clipboard.ps1",
        "-SecretsPath",
        secrets,
        "-ClipboardText",
        clipboard_text,
        "-Model",
        "test-model",
    )

    assert result.returncode == 0, result.stderr
    content = secrets.read_text(encoding="utf-8")
    assert "VISION_API_BASE_URL=https://offline.invalid/compatible-mode/v1" in content
    assert f"VISION_API_KEY={secret_value}" in content
    assert "VISION_MODEL_NAME=test-model" in content
    assert secret_value not in result.stdout
    assert "offline.invalid/compatible-mode/v1" not in result.stdout
