# Second Re-review Runtime Fix Brief

Fix only the three remaining runtime findings. Strict TDD, no business/prompt/report-layout changes, no external model API, no old-report writes, no commits.

## 1. Hard deadline for progressive HTTP I/O

Files: `tools/run_guobu_model_audit_v2.py`, `tests/test_guobu_v2_rules.py`.

- Add a real or faithful response test where valid JSON bytes arrive frequently enough to avoid an inactivity timeout but the total transfer exceeds the declared stage deadline; current code must first reproduce success after deadline.
- Enforce one wall-clock stage deadline across throttle, connect, request, response headers, and body. A socket inactivity timeout alone is insufficient.
- Use only standard library mechanisms. A deadline timer may close the active connection, and body reads must check the deadline between available chunks (`HTTPResponse.read1` where appropriate). Any deadline-triggered close/incomplete result must become `OrderBudgetExceeded`, not JSON/connection noise.
- Cancel deadline resources reliably in `finally`; no leaked non-daemon threads/timers.
- Keep maximum connect sub-timeout 5 seconds and existing ordinary connection retry behavior.

## 2. OrderBudgetExceeded must enter network retry selection

Files: `tools/guobu_audit_contract.py`, focused selector/contract test in existing test layout.

- Add a failing test with the actual `audit_task_path` result shape containing `_error: ...OrderBudgetExceeded...` and the Chinese 60-second reason; `network_failure()` must return true after the fix.
- Do not broaden retry selection to unrelated manual/business failures.

## 3. Atomic RunName reservation

Files: `tools/run_guobu_audit_batch.ps1`, `tests/test_guobu_audit_skill_report_integration.py`.

- Keep the existing preflight check, then atomically reserve the run by creating the first run directory without `-Force`. Only one concurrent process can succeed.
- Shared parent directories may be created first; no run-specific manifest/cache/report/retry write may occur before successful reservation.
- Add a concurrency behavior test launching two same-RunName processes through a no-model stub/preflight path; assert exactly one reserves/runs and the other fails without overwriting the winner's sentinel/manifest.
- PlanOnly remains read-only.

## Verification

- Run focused RED/GREEN tests for all three findings.
- Run the four affected suites from the prior report; expected count will exceed 420.
- Run PowerShell PlanOnly and atomic-reservation smoke without external API.
- Run scoped `git diff --check`.
- Write `.superpowers/sdd/second-rereview-fix-report.md` with evidence and changed files.
