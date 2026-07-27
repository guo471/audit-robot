# Final Read-only Acceptance Review

## Verdict

**NEEDS WORK**

- Critical: 0
- Important: 1
- Throttle hard deadline: not fully closed under concurrent lock contention
- Windows `FileMode.CreateNew` RunName reservation: closed
- No additional Critical/Important was found within the two-item review scope.

## Important Finding

### Throttle deadline does not bound time spent acquiring the global throttle lock

Files/lines:

- `tools/run_guobu_model_audit_v2.py:1198` enters `_model_request_lock` with a blocking `with` and no deadline-aware acquisition timeout.
- `tools/run_guobu_model_audit_v2.py:1199-1212` checks and truncates throttle sleep only after the lock has been acquired.
- `tools/run_guobu_model_audit_v2.py:1290-1292` starts the deadline timer, but the timer can only set the expiration event or close an active connection.
- `tools/run_guobu_model_audit_v2.py:1304` can therefore remain blocked acquiring `_model_request_lock` after the stage deadline; no active connection exists for the timer to interrupt.
- `tests/test_guobu_v2_rules.py:544-562` covers one thread's own throttle sleep, but not waiting behind another worker that currently holds the global throttle lock.

Reproduction:

1. A helper thread acquired `_model_request_lock` for `0.20s`.
2. The audit thread called `_post_chat_completion_json(..., read_timeout_sec=0.05)` while that lock was held.
3. The call eventually raised `OrderBudgetExceeded` and did not start a connection, but returned after `0.201s` rather than near the `0.05s` deadline.

```json
{"outcome":"OrderBudgetExceeded","limit":0.05,"elapsed":0.201}
```

This is a production-relevant multi-worker path because `_wait_before_model_request` holds the same global lock while sleeping. The throttle fix bounds the sleep performed by the lock owner, but does not bound another order's lock-acquisition wait. The 60-second per-order hard wall deadline is therefore still not guaranteed.

## Closed Item

### Windows FileMode.CreateNew concurrent RunName reservation: CLOSED

- `tools/run_guobu_audit_batch.ps1:269-290` includes the reservation lock in the preflight path set.
- `tools/run_guobu_audit_batch.ps1:296-301` uses `FileMode.CreateNew`, `FileAccess.ReadWrite`, and `FileShare.None`, which provides exclusive creation semantics rather than idempotent directory creation.
- `tools/run_guobu_audit_batch.ps1:309-312` rechecks all persistent run paths after acquiring the lock and creates `$firstOut` before declaring the persistent reservation ready.
- `tools/run_guobu_audit_batch.ps1:315-322` closes the stream and removes the transient lock only after `$firstOut` exists, so subsequent launches fail closed on a persistent run path.
- `tests/test_guobu_audit_skill_report_integration.py:359-438` executes the same-name race three times and verifies exactly one process and one runner invocation succeeds.

Fresh focused verification passed all six parametrized/regression cases:

```text
......
6 passed in 13.33s
```

The focused command covered the throttle regression, legacy request spacing, three concurrent reservation repetitions, and sequential reused-RunName sentinel protection. The full suite was intentionally not repeated.

No source or test file was modified during this acceptance review.
