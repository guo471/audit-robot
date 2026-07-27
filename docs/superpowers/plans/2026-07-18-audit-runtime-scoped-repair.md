# Audit Runtime Scoped Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed home-appliance false interception, enforce one 60-second per-order budget, and make UTF-8/runtime/rerun configuration deterministic without changing unrelated audit rules.

**Architecture:** Keep the existing two-stage audit pipeline. Make the final activation fallback category-aware, centralize the per-order deadline, and harden only the PowerShell orchestration contract around encoding, interpreter selection, dependencies, and rerun configuration.

**Tech Stack:** Python 3, pytest, PowerShell, JSON/JSONL, OpenCV (`cv2`), Git.

## Global Constraints

- Do not change address, R9, photo-authenticity, duplicate-photo, category, computer, or SN-normalization rules.
- Do not add second-stage SN equality checking; first-stage SN remains authoritative.
- Preserve early return when an earlier stage already requires manual review.
- Do not update or rewrite old reports.
- One order has one absolute 60-second model budget; retries may only consume remaining time.
- Preserve all pre-existing working-tree changes and do not commit or revert unrelated files.
- Add a failing regression test before each production-code change.

---

### Task 1: Home-appliance activation fallback

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Test: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- Consumes: `enforce_photo_noncompliance_manual(decision, ...)` and `_is_home_appliance_decision(decision)`.
- Produces: category-scoped activation fallback behavior; ordinary 3C and computer behavior remains unchanged.

- [ ] Add a regression test where a verified home appliance has valid product/unboxing/authenticity evidence but model `activation_photo_ok=false`; expect no `ACTIVATION_PHOTO_INVALID`.
- [ ] Run the new test and confirm it fails for the shared fallback at the current line near 2437.
- [ ] Add paired tests proving the same false field still blocks ordinary 3C and computer decisions.
- [ ] Make the smallest category guard in `enforce_photo_noncompliance_manual` and clarify only the home-appliance prompt contract that no screen-on evidence is required.
- [ ] Run the focused home/ordinary-3C/computer tests.
- [ ] Run existing address, R9, authenticity, duplicate-photo, computer, and prompt-boundary regression tests.

### Task 2: One 60-second order deadline

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Test: `tests/test_guobu_v2_rules.py`
- Test: `tests/test_guobu_audit_report.py`

**Interfaces:**
- Consumes: `ORDER_TIMEOUT_SEC`, `_stage_timeout_from_budget`, `call_model_with_retry`.
- Produces: no model call or retry extends an order beyond the single 60-second deadline.

- [ ] Add fake-clock tests for first call, follow-up call, connect retry, and timeout accounting.
- [ ] Confirm at least one new test fails on a path that can use an independent retry timeout.
- [ ] Make every audit mode calculate call/retry time from the same remaining order budget; remove independent time extensions without changing the 5-second connect sub-timeout.
- [ ] Verify timeout output remains a manual result with a Chinese 60-second timeout reason.
- [ ] Run focused timeout and report-accounting tests, then the full Guobu rule/report suites.

### Task 3: UTF-8, runtime, and rerun configuration contract

**Files:**
- Modify: `tools/run_guobu_audit_batch.ps1`
- Modify: `tools/guobu_audit_contract.py`
- Modify: `tests/test_guobu_v2_rules.py` or create one focused batch-contract test only if required by the existing test layout.
- Modify: `.gitignore` only if runtime/output directories are not already excluded.

**Interfaces:**
- Consumes: batch arguments, resolved environment modes, generated JSON report.
- Produces: explicit UTF-8 JSON parsing, explicit Python interpreter, dependency preflight, and a run manifest checked before retry-result merging.

- [ ] Add a failing UTF-8 round-trip test containing Chinese manual reasons through JSON parsing and report generation.
- [ ] Replace implicit PowerShell decoding with explicit UTF-8 decoding and set PowerShell/Python stream encodings.
- [ ] Add a `PythonExe` parameter resolved to the project runtime; preflight Python, `cv2`, required prompt files, and fail before auditing with a clear Chinese error.
- [ ] Add a run manifest containing model, audit mode, resolved plugin modes, authenticity mode, prompt hashes, timeout, Git commit, Python version/path, and cv2 version.
- [ ] Ensure timeout/network retry work inherits the original manifest and reject merging results whose behavior-affecting fields differ.
- [ ] Add tests for accepted identical manifests and rejected `sn_char_review_mode`/prompt-hash drift.
- [ ] Verify `.venv-photo-auth`, reports, data, and temp outputs remain untracked; prompt source files remain trackable.
- [ ] Run focused contract/UTF-8 tests and all affected Guobu tests.

### Task 4: Independent adversarial reviews

**Files:**
- No production edits by reviewers.

- [ ] Business-boundary reviewer verifies only the approved home-appliance exception changed and all excluded rules are byte/diff unaffected or behaviorally covered.
- [ ] Code-quality reviewer verifies deadline correctness, UTF-8 behavior, manifest completeness, runtime preflight, and test evidence.
- [ ] Resolve every Critical or Important finding with a failing test and minimal patch, then repeat both reviews.
- [ ] Run the final affected test suite and report changed files, tests, residual risks, and untouched scopes.
