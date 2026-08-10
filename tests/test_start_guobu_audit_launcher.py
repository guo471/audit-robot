import json
import subprocess
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "tools" / "start_guobu_audit.ps1"
BATCH = PROJECT_ROOT / "tools" / "run_guobu_audit_batch.ps1"
FIXED_PYTHON = r"C:\Users\guoru\AppData\Local\Programs\Python\Python314\python.exe"


def launcher_text() -> str:
    return LAUNCHER.read_text(encoding="utf-8-sig")


def batch_text() -> str:
    return BATCH.read_text(encoding="utf-8-sig")


def make_task_dir(tmp_path: Path) -> Path:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.json").write_text(
        json.dumps(
            {
                "task_id": "guobu-api-one",
                "channel_order_no": "one",
                "fields": {"status": "passed", "flow_status": "passed"},
                "image_groups": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tasks


def test_launcher_centralizes_runtime_secret_entry_and_modes():
    source = launcher_text()

    assert "[Alias(\"dry-run\")]" in source
    assert "[Alias(\"run\")]" in source
    assert "[Alias(\"resume\")]" in source
    assert FIXED_PYTHON in source
    assert "run_with_local_vision_secrets.ps1" in source
    assert "-CommandArgs" not in source
    assert "PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true" not in source
    assert "PHOTO_AUTHENTICITY_MODE=enforce" not in source
    assert ".complete" in source


def test_batch_wrapper_accepts_short_cache_and_temp_roots_from_launcher():
    source = batch_text()

    assert "[string]$CacheRoot" in source
    assert "[string]$TempRoot" in source
    assert "cacheRoot" in source
    assert "tempRoot" in source


def test_dry_run_writes_manifest_and_does_not_start_model(tmp_path):
    tasks = make_task_dir(tmp_path)
    run_name = f"pytest_launcher_dry_run_{uuid.uuid4().hex[:8]}"

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "--dry-run",
            "-TasksDir",
            str(tasks),
            "-RunName",
            run_name,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "dry-run"
    assert payload["taskCount"] == 1
    assert payload["wouldStartModel"] is False
    assert payload["pythonPath"] == FIXED_PYTHON
    assert payload["snBarcodeMode"] == "enforce"
    assert payload["photoAuthenticityNewRuleEnabled"] is True
    assert Path(payload["launcherManifest"]).exists()
    assert not Path(payload["completeMarker"]).exists()


def test_runname_collision_is_rejected_before_model(tmp_path):
    tasks = make_task_dir(tmp_path)
    report_root = PROJECT_ROOT / "reports" / "model_audit"
    existing = report_root / "pytest_launcher_collision_first"
    existing.mkdir(parents=True, exist_ok=True)

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "--dry-run",
            "-TasksDir",
            str(tasks),
            "-RunName",
            "pytest_launcher_collision",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )

    assert completed.returncode != 0
    assert "RunName" in completed.stderr
    assert "already exists" in completed.stderr


def test_resume_rejects_dry_run_manifest_without_first_run_manifest(tmp_path):
    tasks = make_task_dir(tmp_path)
    run_name = f"pytest_launcher_resume_gate_{uuid.uuid4().hex[:8]}"

    dry_run = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "--dry-run",
            "-TasksDir",
            str(tasks),
            "-RunName",
            run_name,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    assert dry_run.returncode == 0, dry_run.stderr

    resume = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "--resume",
            "-TasksDir",
            str(tasks),
            "-RunName",
            run_name,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )

    assert resume.returncode != 0
    assert "first run manifest is missing" in resume.stderr
