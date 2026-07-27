# Independent Adversarial Fix Re-review

## Verdict

- Critical: 0
- Important: 2
- Original findings: 3 closed, 1 not closed
- Scope compliance: PASS for the adversarial fix scope. The observed fix is confined to the authorized timeout, reused-RunName, manifest/contract, and regression-test surfaces; no evidence was found that this fix changed forbidden address, R9, authenticity, duplicate, category, computer, SN, prompt-content, report-layout, dependency, or historical-report behavior.
- Code quality: FAIL / not ready. The nominal test suite passes, but the absolute deadline is not actually enforced on progressive HTTP I/O, and the new order-timeout exception is omitted from network-rerun selection.

## Findings

### Important 1: The absolute per-order/stage deadline is still not enforced during progressive HTTP response I/O

Files/lines:

- `tools/run_guobu_model_audit_v2.py:1233` creates a stage deadline.
- `tools/run_guobu_model_audit_v2.py:1252-1256` converts remaining time into connection/socket timeout values only once before connect/read.
- `tools/run_guobu_model_audit_v2.py:1258-1262` performs `getresponse()` and `response.read()` and returns without checking the absolute deadline again.
- `tests/test_guobu_v2_rules.py:541-592` only proves that the second connection receives a recomputed timeout value (`53.0`); it does not prove that a progressive multi-receive response cannot exceed the deadline.

Reproducible reasoning:

1. Python socket timeouts are inactivity limits for individual blocking operations, not a wall-clock deadline for the complete `HTTPResponse.read()` loop.
2. A local HTTP server was configured to return a valid JSON body one byte every 0.02 seconds, while `_post_chat_completion_json(..., read_timeout_sec=0.05)` was used.
3. Every byte arrived before the 0.05-second socket inactivity timeout, so no socket operation timed out.
4. The function returned success after 0.234 seconds, more than four times the declared 0.05-second absolute limit: `{"result":{"ok":true},"limit":0.05,"elapsed":0.234,"exceeded":true}`.
5. The same behavior scales to the 60-second production limit: a response that keeps making progress can exceed one order's deadline. The original timeout finding is therefore not closed.

Required direction: enforce/check the absolute deadline throughout request and response transfer, rather than relying on a single socket inactivity timeout assignment.

### Important 2: `OrderBudgetExceeded` becomes a per-order result but is not selected for the promised timeout/network rerun

Files/lines:

- `tools/run_guobu_model_audit_v2.py:1238` and `tools/run_guobu_model_audit_v2.py:1267` raise `OrderBudgetExceeded` when the deadline is exhausted.
- `tools/run_guobu_model_audit_v2.py:3254-3269` catches that exception inside `audit_task_path` and emits one `error_to_manual` result, so the batch itself continues.
- `tools/guobu_audit_contract.py:6-8` defines network failure markers but does not include `OrderBudgetExceeded` or its Chinese timeout reason.
- `tools/guobu_audit_contract.py:64-68` selects retries solely through those markers/`RemoteDisconnected`.
- `tools/run_guobu_audit_batch.ps1:345-356` uses that selector to determine `$retryCount`; an unrecognized timeout is therefore omitted from rerun.
- Existing selector coverage at `tests/test_guobu_audit_skill_report_integration.py:308-327` covers `TimeoutError`/connection failures, not `OrderBudgetExceeded`.

Reproducible reasoning:

Passing this exact current-result shape to `network_failure()` returned `False`:

```text
_error: tools.run_guobu_model_audit_v2.OrderBudgetExceeded: 模型审核超过每单60秒总期限，已转人工复核
manual_reason: OrderBudgetExceeded: 模型审核超过每单60秒总期限，已转人工复核
strategy: error_to_manual
network_failure(...) => False
```

Therefore timeout exceptions are isolated per order and do not abort the batch, but those orders silently skip the wrapper's timeout/network rerun. This is a new Important defect in the repaired timeout path.

## Original Four Findings

### 1. Absolute 60-second deadline through connect/retry/read: NOT CLOSED

The throttle wait is now inside the stage deadline (`tools/run_guobu_model_audit_v2.py:1251-1252`), and connect/read timeout values are recomputed. However, Important 1 proves the implementation still permits progressive I/O past the absolute deadline.

### 2. Reused RunName rejected before writes: CLOSED

`tools/run_guobu_audit_batch.ps1:268-285` checks every run-specific output/cache/retry/selection path, and the check runs before the first directory creation and manifest write at `tools/run_guobu_audit_batch.ps1:287-290`. The behavior test at `tests/test_guobu_audit_skill_report_integration.py:340-354` plants sentinel manifest bytes, observes rejection, and proves the bytes and report state remain unchanged.

### 3. Truthful dirty-worktree manifest and retry compatibility: CLOSED

`tools/run_guobu_audit_batch.ps1:145-175` hashes the declared runtime sources; `tools/run_guobu_audit_batch.ps1:191-223` records current Git dirty state plus runtime/prompt hashes; `tools/run_guobu_audit_batch.ps1:359-376` recomputes and validates them before starting the retry. `tools/guobu_audit_contract.py:10-62` enforces field and hash equality. Tests at `tests/test_guobu_audit_runtime_contract.py:74-89`, `tests/test_guobu_audit_skill_report_integration.py:130-194`, and `tests/test_guobu_audit_skill_report_integration.py:357-425` verify dirty-state truthfulness and runtime-drift rejection.

### 4. Home invoice priority regression: CLOSED

`tests/test_guobu_v2_rules.py:2965-2990` directly verifies that the verified-home activation fallback cannot clear `INVOICE_ORANGE_WARNING`. The focused test passes, and the adversarial fix made no production business-rule change for this item.

## Verification

- Repair-report focused tests: `7 passed in 13.08s`.
- Full affected suite (`test_guobu_v2_rules.py`, runtime contract, PowerShell integration, audit report): `420 passed in 36.85s`.
- `git diff --check` on the scoped files: exit 0; only existing LF-to-CRLF warnings were emitted.
- Runtime absolute-deadline repro: declared 0.05 seconds, returned success after 0.234 seconds.
- Runtime retry-classification repro: `network_failure(OrderBudgetExceeded result) == False`.

Passing tests do not close the two runtime gaps above because neither behavior is represented by the current suite.
