# Final Runtime Fix Brief

Fix only the two remaining Important findings. Strict TDD. No business/prompt/report-layout/old-report/dependency changes, no external API, no commit.

## 1. Deadline-aware throttle

Files: `tools/run_guobu_model_audit_v2.py`, `tests/test_guobu_v2_rules.py`.

- Add a real-time regression where throttle needs about 0.20 seconds but the stage has about 0.05 seconds remaining; current code must first demonstrate elapsed time near 0.20.
- Extend `_wait_before_model_request` to accept the absolute stage deadline (or equivalent remaining budget). Sleep no longer than the remaining deadline, then raise `OrderBudgetExceeded` when the requested buffer cannot complete in time.
- Pass the stage deadline from `_post_chat_completion_json`.
- Preserve the existing 3-second spacing behavior when no deadline is supplied and preserve lock/thread safety.

## 2. True atomic RunName reservation on Windows

Files: `tools/run_guobu_audit_batch.ps1`, `tests/test_guobu_audit_skill_report_integration.py`.

- Do not rely on `New-Item` directory creation for exclusivity; Windows directory creation may be idempotent.
- Reserve a run-specific lock/sentinel using .NET `FileMode.CreateNew` with no sharing, which has atomic create-new semantics.
- Include the reservation path in preflight rejection. After acquiring it, recheck run-specific output paths before creating first output/cache/manifest so a process that passed the initial scan cannot race an existing run.
- Keep or safely release the reservation only after another persistent run-specific path guarantees future rejection; crashes must fail closed.
- Update the existing concurrent test so exactly one runner reaches the invocation marker reliably across repeated runs.
- PlanOnly remains read-only; sequential old-manifest sentinel protection remains unchanged.

## Verification

- Focused RED/GREEN for both findings, including at least three repetitions of the concurrency test.
- Full four affected suites; expected count remains at least 424.
- PlanOnly and atomic smoke, no external API.
- Scoped diff check.
- Write `.superpowers/sdd/final-runtime-fix-report.md` and stop.
