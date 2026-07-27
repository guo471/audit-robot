### Task 2 Report: One 60-second order deadline

#### Affected call paths
- `audit_task_fast`: `fast_sn` and optional `fast_sn_review` now share one order deadline. Retry uses the smaller of 15 seconds and remaining order time.
- `audit_task_v2`: `sn` and `compliance` now share one order deadline. The compliance call uses only remaining time.
- `audit_task_hybrid`: existing staged early returns remain; all model stages now pass the same absolute deadline, and timeout manual reasons are Chinese and mention 60 seconds.
- `audit_task_sn_only`: direct SN OCR call is bounded by the same 60-second order deadline.
- `_post_chat_completion_json`: the 5-second connection timeout remains capped inside the stage/order deadline.

#### RED
Command:
`python -m pytest tests/test_guobu_v2_rules.py -q -k "v2_compliance_call_uses_remaining_order_budget or fast_retry_uses_only_remaining_order_budget or hybrid_order_budget_stops_before_compliance_when_time_is_used"`

Result:
- 3 failed.
- `audit_task_v2` requested `("compliance", 60)` after 59 seconds instead of the remaining 1 second.
- `audit_task_fast` retry requested `("fast_sn", 15)` after 59 seconds instead of the remaining 1 second.
- Hybrid timeout manual reason was English: `order exceeded 60 second model budget`.

#### GREEN
Command:
`python -m pytest tests/test_guobu_v2_rules.py -q -k "v2_compliance_call_uses_remaining_order_budget or fast_retry_uses_only_remaining_order_budget or hybrid_order_budget_stops_before_compliance_when_time_is_used"`

Result:
- 3 passed.

Focused timeout command:
`python -m pytest tests/test_guobu_v2_rules.py -q -k "timeout or retry or order_budget"`

Result:
- 7 passed, 222 deselected.

#### Broader tests
Command:
`python -m pytest tests/test_guobu_v2_rules.py -q`

Result:
- 229 passed.

#### Concerns
- Existing dirty-worktree edits in `tools/run_guobu_model_audit_v2.py` and `tests/test_guobu_v2_rules.py` were preserved; no revert or commit was made.
- `tests/test_guobu_audit_report.py` was not run after the latest instruction to write the report immediately after the focused timeout tests and rules suite. This task did not change report accounting code.
