# Task 3 Report: UTF-8, Runtime, and Rerun Configuration Contract

## Changed Files

- `.gitignore`
- `tools/run_guobu_audit_batch.ps1`
- `tools/guobu_audit_contract.py`
- `tests/test_guobu_audit_runtime_contract.py`
- `tests/test_guobu_audit_skill_report_integration.py`
- `.superpowers/sdd/task-3-report.md`

## RED Evidence

Command:

```powershell
python -m pytest tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py -q
```

Observed expected RED:

```text
7 failed, 11 passed
```

Expected failures covered missing UTF-8 JSON helpers, missing manifest drift validation, missing top-level runtime ignores, missing `pythonPath`/manifest PlanOnly fields, missing explicit PowerShell UTF-8 handling, and missing `-PythonExe` support.

## GREEN Evidence

Command:

```powershell
python -m pytest tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py -q
```

Result:

```text
18 passed in 23.93s
```

## Manifest Fields

The run manifest written as UTF-8 JSON contains:

- `created_at_utc`
- `run_name`
- `model`
- `mode`
- `workers`
- `targeted_sn_review`
- `sn_char_review_mode`
- `sn_label_auth_review_mode`
- `digital_activation_evidence_mode`
- `photo_authenticity_mode`
- `order_timeout_seconds`
- `git_commit`
- `python_path`
- `python_version`
- `cv2_version`
- `prompt_sha256.sn_similar_char_review.txt`
- `prompt_sha256.sn_label_authenticity_review.txt`
- `prompt_sha256.digital_activation_evidence_review.txt`

Network retry writes the same in-memory manifest and validates behavior-affecting fields before merge. Drift in `sn_char_review_mode` or prompt SHA-256 fails with a field-specific error.

## Smoke Tests

PowerShell PlanOnly/preflight smoke command:

```powershell
$tasks = Join-Path $env:TEMP 'audit_robot_task3_planonly_tasks'; if (-not (Test-Path -LiteralPath $tasks)) { New-Item -ItemType Directory -Force -Path $tasks | Out-Null }; [System.IO.File]::WriteAllText((Join-Path $tasks 'one.json'), '{"channel_order_no":"plan-only-1"}', [System.Text.UTF8Encoding]::new($false)); powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\run_guobu_audit_batch.ps1 -ProjectRoot . -TasksDir $tasks -RunName task3_planonly_smoke -PlanOnly
```

Result:

```text
exit 0
pythonPath: C:\Users\HUAWEI\Desktop\audit_robot\.venv-photo-auth\Scripts\python.exe
pythonVersion: 3.11.9
cv2Version: 5.0.0
order_timeout_seconds: 60
```

No external model API was called.

## Concerns

- Worktree had extensive pre-existing user/task changes before this task; no files were reverted and no commit was made.
- PowerShell reported CRLF normalization warnings in git diff output; no line-ending-only rewrite was intentionally performed.
