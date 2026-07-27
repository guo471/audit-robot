# Third Read-only Re-review

## Verdict

**NEEDS WORK**

- Critical: 0
- Important: 2
- Closed: progressive-body wall deadline; `OrderBudgetExceeded` retry selection
- Not closed: throttle wait within the hard deadline; concurrent same-`RunName` atomic reservation
- No additional Critical/Important was found within the explicitly limited review surface.

## Important Findings

### Important 1: Throttle waiting is accounted for logically but can still overrun the hard wall deadline

Files/lines:

- `tools/run_guobu_model_audit_v2.py:1198-1205` holds the throttle lock and executes an unbounded `time.sleep(wait_sec)`.
- `tools/run_guobu_model_audit_v2.py:1281-1283` starts the deadline timer before throttling.
- `tools/run_guobu_model_audit_v2.py:1295-1296` waits first and only checks the expired deadline after the full throttle sleep.
- `tools/run_guobu_model_audit_v2.py:1249-1256` cannot interrupt this wait because no active connection exists yet for the timer to close.
- `tests/test_guobu_v2_rules.py:530-541` tests throttle duration in isolation but does not combine throttle waiting with a shorter stage deadline.

Runtime reproduction:

1. Set `MODEL_REQUEST_BUFFER_SEC=0.20` and `_last_model_request_at` to the current monotonic time.
2. Call `_post_chat_completion_json(..., read_timeout_sec=0.05)` against a local listener.
3. Result: `OrderBudgetExceeded` was raised and no request started, but elapsed wall time was `0.203s`, over four times the `0.05s` hard deadline.

Therefore throttle time is deducted before the network call, but the function does not return at the deadline. At production scale, a request entering throttle near the end of its order budget can exceed the 60-second wall limit by up to the remaining throttle sleep. The previous absolute-timeout Important is not fully closed.

### Important 2: Directory creation is not an atomic same-RunName reservation on Windows

Files/lines:

- `tools/run_guobu_audit_batch.ps1:268-285` performs a non-atomic preflight path scan.
- `tools/run_guobu_audit_batch.ps1:287-292` attempts to reserve the run with `New-Item -ItemType Directory` and assumes one concurrent creator must fail.
- `tests/test_guobu_audit_skill_report_integration.py:357-450` barriers two processes immediately before that operation and asserts only one runner starts.

Runtime reproduction:

- The focused concurrency test failed twice consecutively.
- First run: one process failed only later during combined-report generation instead of failing with the expected `RunName` reservation error.
- Second run: `runner-invocations.txt` contained `['one', 'two']`, proving both processes passed the alleged reservation and launched the audit runner.

Windows directory creation is idempotent and does not provide `FileMode.CreateNew`-style exclusive ownership under this race. The concurrent same-`RunName` issue is not closed; the reservation must use an actually exclusive primitive.

## Closed Checks

### Progressive slow body wall deadline: CLOSED

- `tools/run_guobu_model_audit_v2.py:1258-1278` reads real `HTTPResponse` bodies incrementally and checks remaining time between chunks.
- `tools/run_guobu_model_audit_v2.py:1281-1283` starts a wall timer; after connection registration at `tools/run_guobu_model_audit_v2.py:1298`, expiry closes the active connection.
- The real localhost slow-body test at `tests/test_guobu_v2_rules.py:598-636` passed.
- Independent scaled reproduction: a body sending one byte every `0.02s` under a `0.05s` limit raised `OrderBudgetExceeded` after `0.078s`, instead of completing after the full progressive body as in the prior `0.234s` reproduction.

Normal scheduler/connection-close latency remains, but progressive body transfer no longer extends for the complete response duration.

### OrderBudgetExceeded retry selection: CLOSED

- `tools/guobu_audit_contract.py:6-9` includes `orderbudgetexceeded` in retry markers.
- `tools/guobu_audit_contract.py:65-69` scans `_error`, manual reason fields, strategy, and row error.
- Tests at `tests/test_guobu_audit_runtime_contract.py:92-119` prove the timeout result is selected and an unrelated business-manual result is not.
- Independent direct call returned `network_failure(OrderBudgetExceeded item) == True`.

## Verification Evidence

Focused command from the fix report was run fresh:

```text
5 passed, 1 failed
FAILED tests/test_guobu_audit_skill_report_integration.py::test_concurrent_same_run_name_atomically_reserves_single_runner
```

The concurrency test was then rerun alone and failed again:

```text
runner-invocations.txt == ['one', 'two']
1 failed
```

Independent runtime measurements:

```json
{
  "progressive_body": {"outcome": "OrderBudgetExceeded", "limit": 0.05, "elapsed": 0.078},
  "throttle": {"outcome": "OrderBudgetExceeded", "limit": 0.05, "elapsed": 0.203, "request_started": false},
  "retry_selected": true
}
```

No source or test file was modified during this review.
