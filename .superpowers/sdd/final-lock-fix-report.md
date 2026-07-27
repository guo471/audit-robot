# Final Lock Fix Report

## Scope

Fixed only the final Important finding: `_wait_before_model_request` must not block indefinitely on `_model_request_lock` when an absolute stage deadline is supplied.

## Changed Files

- `tools/run_guobu_model_audit_v2.py`
- `tests/test_guobu_v2_rules.py`
- `.superpowers/sdd/final-lock-fix-report.md`

## RED/GREEN Evidence

RED:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_model_request_buffer_lock_wait_respects_stage_deadline -q -p no:cacheprovider
FAILED: elapsed was about 0.20s while deadline was about 0.05s
```

GREEN:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_model_request_buffer_lock_wait_respects_stage_deadline -q -p no:cacheprovider
1 passed
```

Regression coverage:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_model_request_buffer_waits_until_three_seconds_after_previous_request tests/test_guobu_v2_rules.py::test_chat_completion_throttle_respects_stage_deadline -q -p no:cacheprovider
2 passed
```

Focused final run:

```text
python -m pytest tests/test_guobu_v2_rules.py::test_model_request_buffer_lock_wait_respects_stage_deadline tests/test_guobu_v2_rules.py::test_model_request_buffer_waits_until_three_seconds_after_previous_request tests/test_guobu_v2_rules.py::test_chat_completion_throttle_respects_stage_deadline -q -p no:cacheprovider
3 passed in 0.88s
```

Full four affected suites:

```text
python -m pytest tests/test_guobu_v2_rules.py tests/test_guobu_audit_runtime_contract.py tests/test_guobu_audit_skill_report_integration.py tests/test_guobu_audit_report.py -q -p no:cacheprovider
428 passed in 53.14s
```

## Fix

- With `stage_deadline_at=None`, `_wait_before_model_request` still uses a normal blocking lock acquire and preserves the existing request-spacing behavior.
- With `stage_deadline_at` set, it computes the remaining wall-clock deadline before lock acquisition and uses `_model_request_lock.acquire(timeout=remaining)`.
- If the lock cannot be acquired before the deadline, it raises `OrderBudgetExceeded` without waiting for the holder thread to release the lock.
- Lock release remains protected by `finally` after successful acquisition.
