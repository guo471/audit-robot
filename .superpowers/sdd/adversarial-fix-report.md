# Adversarial Fix Report

## Scope

Fixed all four findings from `.superpowers/sdd/adversarial-fix-brief.md` without committing and without changing prompts, audit decision rules outside the requested timeout/manifest paths, report layout, old reports, dependencies, or unrelated files.

## Changed Files

- `tools/run_guobu_model_audit_v2.py`
- `tools/run_guobu_audit_batch.ps1`
- `tools/guobu_audit_contract.py`
- `tests/test_guobu_v2_rules.py`
- `tests/test_guobu_audit_runtime_contract.py`
- `tests/test_guobu_audit_skill_report_integration.py`
- `.superpowers/sdd/adversarial-fix-report.md`

## RED/GREEN Evidence

### Finding 1: Absolute 60-second deadline through connect/retry/read

RED:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_retries_connect_failure_with_one_absolute_stage_deadline -q -p no:cacheprovider
FAILED: assert [60.0] == [53.0]
```

GREEN:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_retries_connect_failure_with_one_absolute_stage_deadline -q -p no:cacheprovider
1 passed
```

Fix:

- `_post_chat_completion_json` now converts the effective stage timeout to one absolute deadline.
- Connect timeout is recomputed before each attempt as `min(5, remaining)`.
- Socket read timeout is recomputed immediately before read.
- Exhausted deadline raises `OrderBudgetExceeded` with the existing order-timeout reason.

### Finding 2: Reused RunName rejected before writes

RED:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_reused_run_name_is_rejected_before_mutating_old_manifest -q -p no:cacheprovider
FAILED: assert completed.returncode != 0
```

GREEN:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_reused_run_name_is_rejected_before_mutating_old_manifest -q -p no:cacheprovider
1 passed
```

Fix:

- Non-PlanOnly wrapper now rejects existing run-specific output/cache/retry/selection paths before `New-Item`, manifest writes, retry directory deletion, or report mutation.
- `PlanOnly` remains read-only and can inspect an existing run name.
- Existing stale retry directory behavior was updated to the stricter contract: reject instead of deleting old run-scoped retry data.

### Finding 3: Truthful manifest in dirty worktree

RED:

```text
python -m pytest tests/test_guobu_audit_runtime_contract.py::test_manifest_compatibility_rejects_dirty_worktree_drift tests/test_guobu_audit_runtime_contract.py::test_manifest_compatibility_rejects_runtime_hash_drift -q -p no:cacheprovider
FAILED: DID NOT RAISE ValueError

python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_plan_preflights_project_python_and_exposes_run_manifest -q -p no:cacheprovider
FAILED: assert 'git_worktree_dirty' in manifest

python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_runtime_hash_drift_is_rejected_before_network_retry -q -p no:cacheprovider
FAILED: assert completed.returncode != 0
```

GREEN:

```text
python -m pytest tests/test_guobu_audit_runtime_contract.py::test_manifest_compatibility_rejects_dirty_worktree_drift tests/test_guobu_audit_runtime_contract.py::test_manifest_compatibility_rejects_runtime_hash_drift -q -p no:cacheprovider
2 passed

python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_plan_preflights_project_python_and_exposes_run_manifest tests/test_guobu_audit_skill_report_integration.py::test_runtime_hash_drift_is_rejected_before_network_retry -q -p no:cacheprovider
2 passed
```

Fix:

- Manifest now includes `git_worktree_dirty` and `runtime_sha256`.
- Runtime hash covers wrapper, model runner, contract validator, business report generator, selector, photo-authenticity mainline, and audit/category runtime modules.
- Contract compatibility now compares dirty state, runtime hashes, and prompt hashes.
- Retry path recomputes prompt/runtime hashes and dirty state before network retry, validates the candidate retry manifest before starting retry, then writes the retry manifest.

### Finding 4: Home invoice priority test

Evidence:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_verified_home_activation_fallback_does_not_clear_invoice_orange_warning -q -p no:cacheprovider
1 passed
```

Fix:

- Added a direct regression assertion that verified-home activation fallback does not clear `INVOICE_ORANGE_WARNING`.
- No production business logic was changed for this test-only finding. The new assertion passed immediately because the existing implementation already preserved invoice priority.

## Final Verification

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_retries_connect_failure_with_one_absolute_stage_deadline tests/test_guobu_audit_skill_report_integration.py::test_reused_run_name_is_rejected_before_mutating_old_manifest tests/test_guobu_audit_runtime_contract.py::test_manifest_compatibility_rejects_dirty_worktree_drift tests/test_guobu_audit_runtime_contract.py::test_manifest_compatibility_rejects_runtime_hash_drift tests/test_guobu_audit_skill_report_integration.py::test_plan_preflights_project_python_and_exposes_run_manifest tests/test_guobu_audit_skill_report_integration.py::test_runtime_hash_drift_is_rejected_before_network_retry tests/test_guobu_v2_rules.py::test_verified_home_activation_fallback_does_not_clear_invoice_orange_warning -q -p no:cacheprovider
7 passed in 8.44s

python -m pytest tests/test_guobu_v2_rules.py tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py tests/test_guobu_audit_report.py -q -p no:cacheprovider
420 passed in 46.28s
```

PowerShell PlanOnly/preflight smoke without model API:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_guobu_audit_batch.ps1 -ProjectRoot <repo> -TasksDir <temp tasks> -RunName smoke_planonly -PlanOnly
{"runName":"smoke_planonly","gitWorktreeDirty":true,"runtimeHashCount":16,"promptHashCount":3}
```

Diff check:

```text
git diff --check -- tools/run_guobu_model_audit_v2.py tools/run_guobu_audit_batch.ps1 tools/guobu_audit_contract.py tests/test_guobu_v2_rules.py tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py
exit 0
```

## Concerns

- The worktree remains dirty by design and is now recorded truthfully in PlanOnly and run manifests.
- Finding 4 was a test coverage finding; the direct regression assertion did not fail against current production code, so no production change was made.
