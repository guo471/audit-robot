# Digital Activation And Exact Duplicate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a switchable ordinary-3C activation-evidence policy and make exact local evidence the only authority for three-photo duplicate blocking.

**Architecture:** Reuse the existing compliance model call and append a focused prompt only for ordinary 3C. Normalize its per-image identity observations in local code and enforce a category-specific gate after first-stage SN verification. Compute exact duplicate buckets from task image files before trusting any model duplicate claim.

**Tech Stack:** Python 3, pytest, PowerShell batch wrapper, SHA256 from `hashlib`.

## Global Constraints

- Do not add a model call.
- Do not change address, category, home-appliance, computer, product-photo, unboxing, R9, or authenticity-threshold behavior.
- Plugin off must preserve the current activation prompt and decision path.
- Duplicate blocking must not use fuzzy image similarity.

---

### Task 1: Regression Tests

**Files:**
- Modify: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- Consumes: existing `compliance_prompt_for_category`, `precheck_task`, and `enforce_photo_noncompliance_manual`.
- Produces: failing tests for the approved prompt, activation gate, and exact duplicate policy.

- [ ] Add prompt-scope and CLI switch tests.
- [ ] Add phone/tablet and watch structured-evidence tests.
- [ ] Add missing-schema, external-source, and same-image binding tests.
- [ ] Add two-same, four-all-different, and four-with-three-identical file tests.
- [ ] Run the focused tests and confirm they fail for missing behavior.

### Task 2: Activation Prompt And Gate

**Files:**
- Create: `prompts/digital_activation_evidence_review.txt`
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `tools/run_guobu_audit_batch.ps1`

**Interfaces:**
- Produces: `resolve_digital_activation_evidence_mode`, `read_digital_activation_evidence_prompt`, strict per-image observation normalization, and a deterministic activation reason helper.

- [ ] Add the independent `on|off` resolver and prompt loader.
- [ ] Append the plugin only for `ordinary_3c`.
- [ ] Validate same-image screen and identity evidence.
- [ ] Execute the gate in the verified-SN path for hybrid and v2 modes.
- [ ] Expose the switch in CLI, batch plan output, and result rows.
- [ ] Run focused activation tests until green.

### Task 3: Exact Duplicate Authority

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- Produces: exact image identities and a trusted `at least three distinct image IDs` decision.

- [ ] Compute SHA256 from existing local image files with URL fallback only when no local file exists.
- [ ] Use exact buckets in precheck and post-model enforcement.
- [ ] Remove model boolean/free-text authority over `DUPLICATE_IMAGE_EVIDENCE`.
- [ ] Keep all non-duplicate reasons independent.
- [ ] Run focused duplicate tests until green.

### Task 4: Verification And Adversarial Review

**Files:**
- Test: `tests/test_guobu_v2_rules.py`
- Test: `tests/test_guobu_adversarial_policy.py`
- Test: `tests/test_photo_authenticity_mainline.py`
- Test: `tests/test_guobu_audit_skill_report_integration.py`

**Interfaces:**
- Consumes: completed implementation.
- Produces: fresh test evidence and three-order regression output.

- [ ] Run focused and related pytest suites.
- [ ] Run the three real order tasks with a unique run name.
- [ ] Verify none of the three receives `DUPLICATE_IMAGE_EVIDENCE`.
- [ ] Dispatch a read-only code reviewer and address any blocker without widening scope.
