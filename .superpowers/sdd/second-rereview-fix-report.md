# Second Re-review Fix Report

## Scope

Fixed only the three runtime findings listed in `.superpowers/sdd/second-rereview-fix-brief.md`. No business decision rules, prompt content, report layout, old reports, dependencies, external model APIs, commits, or unrelated files were changed.

## Changed Files

- `tools/run_guobu_model_audit_v2.py`
- `tools/guobu_audit_contract.py`
- `tools/run_guobu_audit_batch.ps1`
- `tests/test_guobu_v2_rules.py`
- `tests/test_guobu_audit_runtime_contract.py`
- `tests/test_guobu_audit_skill_report_integration.py`
- `.superpowers/sdd/second-rereview-fix-report.md`

## RED/GREEN Evidence

### 1. Hard deadline for progressive HTTP I/O

RED:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_enforces_stage_deadline_during_progressive_body -q -p no:cacheprovider
FAILED: DID NOT RAISE OrderBudgetExceeded
```

GREEN:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_enforces_stage_deadline_during_progressive_body -q -p no:cacheprovider
1 passed

python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_retries_connect_failure_with_one_absolute_stage_deadline -q -p no:cacheprovider
1 passed
```

Fix:

- `_post_chat_completion_json` now starts one wall-clock deadline timer for the full stage.
- The timer closes the active connection at deadline and is cancelled in `finally`.
- Response body reads use `HTTPResponse.read1()` where available and check remaining deadline between chunks.
- Deadline-triggered close, timeout, or incomplete read is converted to `OrderBudgetExceeded`.
- Existing maximum 5-second connect sub-timeout and ordinary connect retry behavior are preserved.

### 2. `OrderBudgetExceeded` enters retry selection

RED:

```text
python -m pytest tests/test_guobu_audit_runtime_contract.py::test_network_failure_selects_order_budget_exceeded_timeout_result tests/test_guobu_audit_runtime_contract.py::test_network_failure_ignores_unrelated_manual_result -q -p no:cacheprovider
FAILED: assert False is True
```

GREEN:

```text
python -m pytest tests/test_guobu_audit_runtime_contract.py::test_network_failure_selects_order_budget_exceeded_timeout_result tests/test_guobu_audit_runtime_contract.py::test_network_failure_ignores_unrelated_manual_result -q -p no:cacheprovider
2 passed
```

Fix:

- `network_failure()` now treats `OrderBudgetExceeded` and the Chinese per-order 60-second timeout reason as retryable timeout markers.
- The unrelated manual/business failure regression remains false.

### 3. Atomic RunName reservation

RED:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner -q -p no:cacheprovider
FAILED: runner-invocations.txt contained both workers
```

GREEN:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner -q -p no:cacheprovider
1 passed

python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_reused_run_name_is_rejected_before_mutating_old_manifest -q -p no:cacheprovider
1 passed
```

Fix:

- Wrapper keeps the existing preflight path scan.
- It then creates shared parent directories only.
- It atomically reserves the run with `New-Item -ItemType Directory -Path $firstOut -ErrorAction Stop` and no `-Force`.
- Run-specific cache creation and first manifest write happen only after successful reservation.

## Final Verification

Focused tests:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_enforces_stage_deadline_during_progressive_body tests/test_guobu_v2_rules.py::test_chat_completion_retries_connect_failure_with_one_absolute_stage_deadline tests/test_guobu_audit_runtime_contract.py::test_network_failure_selects_order_budget_exceeded_timeout_result tests/test_guobu_audit_runtime_contract.py::test_network_failure_ignores_unrelated_manual_result tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner tests/test_guobu_audit_skill_report_integration.py::test_reused_run_name_is_rejected_before_mutating_old_manifest -q -p no:cacheprovider
6 passed in 6.92s
```

Four affected suites:

```text
python -m pytest tests/test_guobu_v2_rules.py tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py tests/test_guobu_audit_report.py -q -p no:cacheprovider
424 passed in 41.50s
```

PowerShell PlanOnly smoke without external API:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_guobu_audit_batch.ps1 -ProjectRoot <repo> -TasksDir <temp tasks> -RunName second_rereview_smoke_planonly -PlanOnly
{"runName":"second_rereview_smoke_planonly","gitWorktreeDirty":true,"runtimeHashCount":16,"promptHashCount":3}
```

Atomic reservation smoke without external API:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner -q -p no:cacheprovider
1 passed
```

Scoped diff check:

```text
git diff --check -- tools/run_guobu_model_audit_v2.py tools/guobu_audit_contract.py tools/run_guobu_audit_batch.ps1 tests/test_guobu_v2_rules.py tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py
exit 0
```

Only Git LF-to-CRLF warnings were emitted.

## Concerns

- The worktree remains intentionally dirty and includes prior user/task changes.
- `git diff --stat` reflects cumulative uncommitted changes, not only this narrow runtime pass.
