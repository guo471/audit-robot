# Task 1 Report: Home-appliance activation fallback

## Changed lines
- `tools/run_guobu_model_audit_v2.py:408`: clarified the home-appliance prompt: after first-stage SN verification, no screen-on or boot evidence is required.
- `tools/run_guobu_model_audit_v2.py:2402`: added the `verified_home_activation_fallback` category guard.
- `tools/run_guobu_model_audit_v2.py:2415`: clear model-returned `ACTIVATION_PHOTO_INVALID` for verified home appliances when the activation gate has no failure.
- `tools/run_guobu_model_audit_v2.py:2439`: prevent verified home appliances from generating `ACTIVATION_PHOTO_INVALID` merely from `activation_photo_ok=false`.
- `tests/test_guobu_v2_rules.py:62`: updated the frozen hash for the home-appliance prompt only.
- `tests/test_guobu_v2_rules.py:2706`: added the verified-home regression test for `activation_photo_ok=false`.
- `tests/test_guobu_v2_rules.py:2748`: added paired ordinary-3C/computer boundary coverage that still expects `ACTIVATION_PHOTO_INVALID`.
- `tests/test_guobu_v2_rules.py:3556`: added prompt-contract assertions and checked that ordinary-3C/computer prompts do not contain the home fallback wording.

## RED
- Command: `python -m pytest tests/test_guobu_v2_rules.py::test_verified_home_appliance_ignores_activation_photo_false_after_sn_verified -q`
- Result: failed as expected, `assert result["manual_required"] is False` failed because current result was `True`.

## GREEN
- Command: `python -m pytest tests/test_guobu_v2_rules.py::test_verified_home_appliance_ignores_activation_photo_false_after_sn_verified -q`
- Result: `1 passed in 0.85s`.

## Broader tests
- Command: `python -m pytest tests/test_guobu_v2_rules.py::test_verified_home_appliance_ignores_activation_photo_false_after_sn_verified tests/test_guobu_v2_rules.py::test_verified_non_home_activation_photo_false_still_blocks tests/test_guobu_v2_rules.py::test_verified_home_appliance_without_visible_packaging_is_manual tests/test_guobu_v2_rules.py::test_verified_home_appliance_without_packaging_passes_strict_home_scene_gate tests/test_guobu_v2_rules.py::test_verified_home_appliance_without_packaging_requires_complete_home_scene_gate tests/test_guobu_v2_rules.py::test_no_box_home_scene_gate_does_not_apply_to_3c_or_computer tests/test_guobu_v2_rules.py::test_home_scene_gate_does_not_override_higher_priority_photo_failures tests/test_guobu_v2_rules.py::test_verified_watch_pairing_screen_cannot_use_package_sn_as_identity tests/test_guobu_v2_rules.py::test_verified_watch_about_screen_with_serial_remains_valid tests/test_guobu_v2_rules.py::test_verified_phone_imei_screen_is_not_treated_as_sn_conflict tests/test_guobu_v2_rules.py::test_compliance_prompt_contains_split_category_rules_and_invoice_warning tests/test_guobu_v2_rules.py::test_plugin_off_compliance_prompts_match_frozen_exact_duplicate_policy_baselines -q`
- Result: `18 passed in 0.85s`.
- Command: `python -m pytest tests/test_guobu_v2_rules.py -q`
- Result: `227 passed in 1.33s`.

## Concerns
- Worktree already contained many unrelated dirty changes before this task; none were reverted or committed.
- Scope was limited to the two owned files plus this report file.
