### Task 3: UTF-8, runtime, and rerun configuration contract

Goal: Make the batch entry point deterministic on Windows: explicit UTF-8 JSON handling, explicit project Python with `cv2` preflight, and a manifest proving first run/retry use identical behavior-affecting configuration.

Files you own for this task only:
- `tools/run_guobu_audit_batch.ps1`
- `tools/guobu_audit_contract.py`
- Focused tests under `tests/` following the existing layout
- `.gitignore` only for exact top-level runtime/output paths

Known facts to preserve:
- `C:\Users\HUAWEI\Desktop\audit_robot\.venv-photo-auth\Scripts\python.exe` exists with Python 3.11.9 and cv2 5.0.0.
- Existing Python UTF-8 env vars do not control PowerShell `Get-Content` decoding.
- `prompts/` must stay trackable. Top-level `.venv-photo-auth/`, `reports/`, `temp/`, and `data/` are generated/runtime paths and currently are not ignored.
- Old reports must not be read, rewritten, or migrated.

Required TDD sequence:
1. Add a failing contract test for UTF-8 Chinese JSON round-trip and for manifest drift rejection (`sn_char_review_mode` and prompt hash at minimum).
2. Run focused tests and record RED.
3. Add an optional `PythonExe` parameter whose default resolves to the project `.venv-photo-auth\Scripts\python.exe`; validate it before creating report directories.
4. Use that interpreter for audit, selector, contract, and report commands. Preflight `cv2`, Python version/path, and required prompt files before auditing.
5. Decode the combined JSON with explicit UTF-8 via .NET/PowerShell and set PowerShell/Python stream encodings.
6. Generate a UTF-8 run manifest before the first audit containing model, mode, workers, targeted SN mode, resolved SN character mode, SN label authenticity mode, digital activation mode, photo-authenticity mode, prompt SHA-256 values, 60-second timeout, Git commit, Python path/version, and cv2 version.
7. First run and network retry use the same in-memory manifest. Validate compatibility before merging; differing behavior fields fail with a clear error. A later business rerun is a new run/report and is not merged.
8. Add exact top-level ignores for `.venv-photo-auth/`, `reports/`, `temp/`, and `data/`; do not ignore `prompts/`.
9. Run focused tests and PowerShell `-PlanOnly`/preflight smoke tests without calling the external model API or changing old reports.

Forbidden changes:
- No audit decision logic, prompts, home, address, R9, authenticity, duplicate, category, computer, SN comparison/normalization, timeout implementation, report layout, or old report changes.
- Do not add dependencies or a new framework. Do not commit or revert existing changes.

Report to `.superpowers/sdd/task-3-report.md` with changed files, RED/GREEN evidence, exact manifest fields, smoke-test commands, and concerns. Return only a short status summary.
