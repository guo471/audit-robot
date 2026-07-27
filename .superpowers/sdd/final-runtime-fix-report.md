# Final Runtime Fix Report

## Scope

Fixed only the two Important findings in `.superpowers/sdd/final-runtime-fix-brief.md`. No business logic, prompt content, report layout, old reports, dependencies, external model APIs, or commits were used.

## Changed Files

- `tools/run_guobu_model_audit_v2.py`
- `tools/run_guobu_audit_batch.ps1`
- `tests/test_guobu_v2_rules.py`
- `tests/test_guobu_audit_skill_report_integration.py`
- `.superpowers/sdd/final-runtime-fix-report.md`

## RED/GREEN Evidence

### 1. Deadline-aware throttle

RED:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_throttle_respects_stage_deadline -q -p no:cacheprovider
FAILED: elapsed was about 0.20s under a 0.05s stage deadline
```

GREEN:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_throttle_respects_stage_deadline -q -p no:cacheprovider
1 passed
```

Regression coverage:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_model_request_buffer_waits_until_three_seconds_after_previous_request -q -p no:cacheprovider
1 passed
```

Fix:

- `_wait_before_model_request` now accepts an optional absolute stage deadline.
- When a throttle wait cannot complete before that deadline, it sleeps only up to the remaining deadline and raises `OrderBudgetExceeded`.
- Existing no-deadline 3-second spacing behavior is preserved.
- `_post_chat_completion_json` passes the stage deadline into the throttle path.

### 2. True atomic RunName reservation on Windows

RED:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner -q -p no:cacheprovider
FAILED: wrapper did not contain FileMode.CreateNew reservation semantics
```

GREEN:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner -q -p no:cacheprovider
3 passed in 13.45s
```

Sequential sentinel protection:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_reused_run_name_is_rejected_before_mutating_old_manifest -q -p no:cacheprovider
1 passed
```

Fix:

- Wrapper preflight now includes a run-specific reservation lock path.
- Non-PlanOnly execution reserves the run using `[System.IO.File]::Open(..., [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)`.
- After acquiring the lock, the wrapper rechecks run-specific output/cache/retry/report paths before creating first output/cache/manifest.
- The lock is only removed after `$firstOut` exists, so future launches fail closed via an existing persistent run-specific path.
- PlanOnly remains read-only.

## Final Verification

Focused final runtime tests:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_throttle_respects_stage_deadline tests/test_guobu_v2_rules.py::test_model_request_buffer_waits_until_three_seconds_after_previous_request tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner tests/test_guobu_audit_skill_report_integration.py::test_reused_run_name_is_rejected_before_mutating_old_manifest -q -p no:cacheprovider
6 passed in 13.84s
```

Additional focused compatibility after test-double signature update:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_retries_connect_failure_with_one_absolute_stage_deadline tests/test_guobu_v2_rules.py::test_chat_completion_enforces_stage_deadline_during_progressive_body -q -p no:cacheprovider
2 passed in 1.45s

python -m pytest tests/test_guobu_v2_rules.py::test_chat_completion_throttle_respects_stage_deadline tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner -q -p no:cacheprovider
4 passed in 14.69s
```

Full four affected suites:

```text
python -m pytest tests/test_guobu_v2_rules.py tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py tests/test_guobu_audit_report.py -q -p no:cacheprovider
427 passed in 47.95s
```

PlanOnly smoke without external API:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_guobu_audit_batch.ps1 -ProjectRoot <repo> -TasksDir <temp tasks> -RunName final_runtime_smoke_planonly -PlanOnly
{"runName":"final_runtime_smoke_planonly","gitWorktreeDirty":true,"runtimeHashCount":16,"promptHashCount":3}
```

Atomic smoke without external API:

```text
python -m pytest tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner -q -p no:cacheprovider
3 passed in 13.45s
```

Scoped diff check:

```text
git diff --check -- tools/run_guobu_model_audit_v2.py tools/run_guobu_audit_batch.ps1 tests/test_guobu_v2_rules.py tests/test_guobu_audit_skill_report_integration.py
exit 0
```

Only Git LF-to-CRLF warnings were emitted.

## Concerns

- The worktree remains intentionally dirty with prior user/task changes.
- The full-suite count increased to 427 because the concurrency regression now runs three repetitions and the throttle deadline regression was added.
