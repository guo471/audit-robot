### Task 2: One 60-second order deadline

Goal: Every audit mode must use one absolute 60-second model budget per order. Multiple calls and quick retries share remaining time; no retry adds time after the deadline. The 5-second connection sub-timeout remains bounded inside the order deadline.

Files you own for this task only:
- `tools/run_guobu_model_audit_v2.py`
- `tests/test_guobu_v2_rules.py`
- `tests/test_guobu_audit_report.py` only if report accounting needs a regression test

Required TDD sequence:
1. Inspect all `call_model_with_retry` call sites and identify any fast/v2/sn-only path that can consume `MODEL_TIMEOUT_SEC + MODEL_RETRY_TIMEOUT_SEC` independently of an order deadline.
2. Add fake-clock/call-capture tests demonstrating the selected path can exceed or request time outside the 60-second total budget; run and record RED.
3. Implement the smallest shared remaining-budget behavior. Hybrid behavior and existing early returns must remain intact.
4. Do not set every socket timeout to 60 blindly: connect timeout may remain 5 seconds, but retries/calls cannot extend the absolute order deadline.
5. Ensure timeout results remain manual and use a clear Chinese reason mentioning 60 seconds; do not alter old reports.
6. Run focused timeout tests and the existing report-accounting tests.

Forbidden changes:
- No home, address, R9, authenticity, duplicate, category, computer, SN comparison/normalization, prompt, PowerShell, runtime, manifest, or report-layout changes.
- Preserve existing dirty-worktree edits; do not revert or commit anything.

Report to `.superpowers/sdd/task-2-report.md` with affected call paths, RED command/result, GREEN command/result, broader tests, and concerns. Return only a short status summary.
