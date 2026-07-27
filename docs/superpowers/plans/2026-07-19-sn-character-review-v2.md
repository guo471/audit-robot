# SN Similar-Character Review Plugin V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a mutually exclusive high-precision SN glyph rereading plugin v2 without changing existing audit decisions or plugin v1 behavior.

**Architecture:** Store v2 in a separate prompt file and select exactly one fragment in `build_sn_prompt`. Extend the existing mode value through the Python CLI and PowerShell wrapper, preserving `on` as v1 and recording the exact mode in rows and manifests.

**Tech Stack:** Python 3.11, PowerShell, pytest.

## Global Constraints

- Do not modify the base SN prompt or plugin v1 text.
- Do not change matching, normalization, candidate precedence, retries, timeouts, or compliance logic.
- Keep `off` as the default.
- Use TDD and verify the failing tests before production edits.

---

### Task 1: Prompt Selection Contract

**Files:**
- Create: `prompts/sn_similar_char_review_v2.txt`
- Modify: `tools/run_guobu_model_audit_v2.py`
- Test: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- Consumes: `SN_CHAR_REVIEW_MODE=off|on|v2`
- Produces: `build_sn_prompt(mode: str | None) -> str`

- [ ] Add failing tests for exact `off`, `on`, and `v2` prompt composition, mutual exclusion, CLI acceptance, hybrid prompt use, and result traceability.
- [ ] Run the focused tests and confirm they fail because `v2` is unsupported.
- [ ] Add the approved v2 prompt file and the minimal selector branch.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Batch A/B Interface And Manifest

**Files:**
- Modify: `tools/run_guobu_audit_batch.ps1`
- Test: `tests/test_guobu_audit_skill_report_integration.py`

**Interfaces:**
- Consumes: `-EnableSnCharReview` or `-EnableSnCharReviewV2`
- Produces: plan fields `snCharReview`, `snCharReviewMode`; manifest field `sn_char_review_mode`

- [ ] Add failing tests for v2 plan selection, v1 compatibility, prompt hashing, and rejection of both switches.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Add the v2 switch, fail-closed conflict check, exact mode propagation, and prompt hash.
- [ ] Reject character-review plugins in `sn_only` and forward v2 through the installed shared skill wrapper.
- [ ] Run the integration tests and confirm they pass.

### Task 3: Adversarial Verification

**Files:**
- Verify only; no planned production changes.

**Interfaces:**
- Consumes: completed implementation and git diff
- Produces: evidence that only prompt selection and traceability changed

- [ ] Run the focused v2 and wrapper tests.
- [ ] Run the full related Guobu test modules.
- [ ] Ask `Prompt Engineer`, `Code Reviewer`, and `QA Automation Engineer` to independently challenge prompt consistency, scope, and A/B traceability.
- [ ] Correct any substantiated issue and rerun affected tests.
