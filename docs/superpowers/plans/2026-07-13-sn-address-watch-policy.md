# SN, Address, and Watch Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Intercept clear device-screen SN conflicts, watches without screen identity evidence, and home-appliance unboxing photos without packaging, while accepting rural addresses ending in a numbered group.

**Architecture:** Keep the existing two-stage hybrid audit and model-call count. Enrich the existing SN and compliance response contracts, then use deterministic local rules for source precedence and watch activation evidence. Leave photo-authenticity behavior unchanged.

**Tech Stack:** Python 3, pytest, existing Qwen vision audit prompts and deterministic rule engine.

## Global Constraints

- Do not add a model call or enable targeted SN review.
- Do not change photo-authenticity rules in this task.
- A clear device-screen SN conflict overrides matching packaging evidence.
- A watch pairing page or device name without SN/IMEI is not valid activation identity evidence.
- A home-appliance unboxing photo must show identifiable outer packaging.
- Addresses ending in a numeric or Chinese-numeral group such as `1组` or `一组` are precise enough.

---

### Task 1: Add regression coverage

**Files:**
- Modify: `tests/test_guobu_v2_rules.py`
- Modify: `tests/test_guobu_adversarial_policy.py`

**Interfaces:**
- Consumes: `is_address_precise_enough`, `_normalize_sn_result`, `enforce_photo_noncompliance_manual`.
- Produces: failing tests for the three requested policy changes and a positive phone guard case.

- [ ] Add address assertions for `结沙拉康一组` and `龙仁乡1组`.
- [ ] Add an SN candidate test where a screen SN conflicts with the system while packaging matches.
- [ ] Add a watch test where the screen is a pairing page and only packaging contains an SN.
- [ ] Run the focused tests and confirm they fail for the missing behavior.

### Task 2: Implement source-aware SN and watch rules

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`

**Interfaces:**
- Consumes: model-provided `sn_candidates` and `activation_screen` fields.
- Produces: deterministic `SN_MISMATCH` and `ACTIVATION_PHOTO_INVALID` outcomes without another model request.

- [ ] Expand the existing SN prompt to return all readable SN candidates with their evidence source.
- [ ] Make `_normalize_sn_result` reject a clear `DEVICE_SCREEN` or `SCREEN` SN that differs from the system, even when packaging matches.
- [ ] Expand the existing compliance schema to request structured activation-screen fields.
- [ ] Treat watch pairing/device-name screens without screen SN/IMEI identity as invalid activation evidence.
- [ ] Run focused tests and confirm they pass.

### Task 3: Implement numbered-group address acceptance

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`

**Interfaces:**
- Consumes: raw address text.
- Produces: `True` for addresses ending in `1组`, `一组`, and equivalent numbered-group forms.

- [ ] Add a constrained numbered-group expression before the generic marker check.
- [ ] Keep existing address behavior outside this requested boundary.
- [ ] Run address tests and confirm they pass.

### Task 4: Require packaging in home-appliance unboxing photos

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- Consumes: model-provided `package_visible` for the unboxing group.
- Produces: `UNBOXING_PHOTO_INVALID` when a home-appliance order has no identifiable outer packaging.

- [ ] Add a failing decision test for the original refrigerator evidence pattern.
- [ ] Require `package_visible` in the existing compliance schema and home-appliance prompt.
- [ ] Fail closed when the field is false or missing for a home-appliance decision.
- [ ] Run focused tests and confirm they pass.

### Task 5: Verify original orders

**Files:**
- Read: `data/guobu_api_fail_20260713_164332/tasks/*.json`
- Generate: a new isolated model-audit result directory.

**Interfaces:**
- Consumes: the original Apple Watch, Honor, address, and OPPO task JSON files.
- Produces: a fresh result showing requested bad cases are manual and the OPPO guard remains automatic.

- [ ] Run the focused pytest suites.
- [ ] Run the relevant original tasks with `qwen3.7-plus`, hybrid mode, one worker, and targeted SN review disabled.
- [ ] Report each order's final decision and reason code.
