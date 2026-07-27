### Task 1: Home-appliance activation fallback

Goal: A home appliance whose SN was already verified must not be intercepted merely because the compliance model returned `activation_photo_ok=false`; ordinary 3C and computer behavior must stay unchanged.

Files you own for this task only:
- `tools/run_guobu_model_audit_v2.py`
- `tests/test_guobu_v2_rules.py`

Required TDD sequence:
1. Add one regression test reproducing the verified-home decision with valid product/unboxing/authenticity evidence and `activation_photo_ok=false`; expect no `ACTIVATION_PHOTO_INVALID`.
2. Run that exact test and record the expected failure.
3. Add paired assertions/tests proving ordinary 3C and computer still return `ACTIVATION_PHOTO_INVALID` for the equivalent decision.
4. Implement the smallest category guard in `enforce_photo_noncompliance_manual`.
5. Clarify only the home-appliance prompt contract: no screen-on evidence is required after first-stage SN verification. Do not touch other category prompts.
6. Run the focused tests and existing home/ordinary-3C/computer boundary tests.

Forbidden changes:
- No address, R9, authenticity, duplicate, category, computer, SN normalization, second-stage SN comparison, timeout, PowerShell, report, or old-report changes.
- Do not reformat or refactor unrelated code.
- Preserve all existing dirty-worktree edits; do not revert or commit anything.

Report to `.superpowers/sdd/task-1-report.md` with changed lines, RED command/result, GREEN command/result, broader tests, and concerns. Return only a short status summary.
