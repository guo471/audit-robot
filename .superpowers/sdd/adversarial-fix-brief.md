# Adversarial Review Fix Brief

Fix all review findings in one bounded pass. Strict TDD: each finding needs a failing regression test before production changes. Preserve every existing user/task edit and do not commit.

## Finding 1: Absolute 60-second deadline through connect/retry/read

Files:
- `tools/run_guobu_model_audit_v2.py`
- `tests/test_guobu_v2_rules.py`

Required behavior:
- `_post_chat_completion_json` must treat its effective stage timeout as one total deadline covering every connect attempt, request, and response read.
- Before each connect attempt and before socket read, recompute remaining time; use `min(5 seconds, remaining)` for connect and remaining time for read.
- If no time remains, raise the existing order-budget timeout type/reason so outer per-order handling can continue the batch.
- `call_model_with_retry` continues to recompute remaining absolute order time before its model retry.
- Replace any existing test that expects a second connection to retain a full 60-second read timeout with an absolute-deadline test using a fake clock.
- Do not change audit decisions, prompts, or the 5-second maximum connect sub-timeout.

## Finding 2: Reused RunName must be rejected before any write

Files:
- `tools/run_guobu_audit_batch.ps1`
- `tests/test_guobu_audit_skill_report_integration.py`

Required behavior:
- Before `New-Item`, manifest writes, retry-directory deletion, or any report mutation, reject if any run-specific output/cache/retry/selection path for the requested `RunName` already exists.
- `PlanOnly` remains read-only and may inspect an existing name.
- Add a behavior test that plants a sentinel old manifest, invokes a real non-API preflight path with the same RunName, expects failure, and proves sentinel bytes are unchanged.
- Do not read or update historical report contents.

## Finding 3: Truthful manifest in a dirty worktree

Files:
- `tools/run_guobu_audit_batch.ps1`
- `tools/guobu_audit_contract.py`
- `tests/test_guobu_audit_runtime_contract.py`
- `tests/test_guobu_audit_skill_report_integration.py`

Required behavior:
- Add `git_worktree_dirty` and `runtime_sha256` to the manifest and compatibility contract.
- `runtime_sha256` must hash the critical runtime sources used by the audit entry: batch wrapper, model runner, contract validator, report generator, selector, photo-authenticity mainline, and imported audit/category modules that affect decisions. Keep prompt hashes separately.
- Recompute current prompt/runtime hashes and dirty state before a network retry, validate compatibility before running the retry, then write the retry manifest. Do not merely reuse stale first-run hash values.
- Add tests proving dirty state/runtime hash drift is rejected and PlanOnly exposes truthful fields.
- Do not fail merely because the worktree is dirty; record it truthfully.

## Finding 4: Home invoice priority test

Files:
- `tests/test_guobu_v2_rules.py`

Required behavior:
- Add a direct regression assertion that verified-home activation fallback cannot clear `INVOICE_ORANGE_WARNING`.
- Test only; production business logic should not need change.

## Verification and Report

- Run focused RED/GREEN tests for all four findings.
- Run: `tests/test_guobu_v2_rules.py`, `tests/test_guobu_audit_runtime_contract.py`, `tests/test_guobu_audit_skill_report_integration.py`, `tests/test_guobu_audit_report.py`.
- Run PowerShell PlanOnly/preflight smoke without model API.
- Run `git diff --check` on changed files.
- Write `.superpowers/sdd/adversarial-fix-report.md` containing RED/GREEN evidence, changed files, exact tests, and concerns.
- Forbidden: address/R9/authenticity/duplicate/category/computer/SN logic changes, prompt content changes, report layout changes, old report writes, new dependencies, broad refactors.
