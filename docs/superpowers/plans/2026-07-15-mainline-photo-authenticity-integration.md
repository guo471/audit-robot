# Mainline Photo Authenticity Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge per-image V4 authenticity observations directly into the existing second compliance model call, apply deterministic V4 rules and frozen FFT 0.995 rescue locally, expose off/shadow/enforce rollback modes, and validate the merged chain without changing backend order state.

**Architecture:** A new focused `tools/photo_authenticity_mainline.py` owns schema validation, deterministic V4 derivation, frozen artifact loading, FFT feature extraction, per-image evaluation, and order aggregation. `tools/run_guobu_model_audit_v2.py` only appends the prompt/schema, passes image IDs, invokes the module after compliance normalization, and writes evidence fields. The default mode is `off`; `shadow` records `would_manual` without changing the legacy decision; `enforce` may add authenticity reason codes.

**Tech Stack:** Python 3.11, Pillow, NumPy, scikit-learn, joblib, existing qwen-compatible chat-completions client, pytest, openpyxl reporting.

## Global Constraints

- Preserve all unrelated dirty-worktree changes; never reset or overwrite user edits.
- `PHOTO_AUTHENTICITY_MODE` defaults to `off`; merged-path tests may explicitly use `enforce`, and rollback is always `off`.
- Per the user's 2026-07-15 decision, do not add a second baseline compliance call. Reuse the extensive existing baseline results and test the directly merged single-call path.
- In `enforce`, any missing/invalid authenticity structure, frozen-artifact failure, FFT failure after retries, or final fallback failure must transfer an otherwise-pass order to manual review. `skip` is not permitted in `enforce`.
- Qwen calls keep `enable_thinking=false`.
- The merged compliance model supplies observations only; program code derives authenticity results.
- FFT input is decoded RGB pixels only; no filename, path, dimensions, format, order, product model, watermark text, or truth fields enter features.
- Frozen extractor is `fft-v1-512-ycbcr-5x53`, feature dimension 795, model SHA-256 `49352975e2ef36d3723cbe6fe028687a56101920fef50becc744c65b96aa512b`, threshold `0.995`.
- FFT may only perform `no_evidence -> manual_review`; it may not create high or modify existing V4 high/manual.
- Existing SN, address, category, duplicate-image, compliance, retry, and export behavior remains unchanged when mode is `off`.
- No backend order mutation is added.
- Every production-code behavior change follows RED-GREEN-REFACTOR.

---

### Task 1: Frozen Authenticity Domain Module

**Files:**
- Create: `tools/photo_authenticity_mainline.py`
- Create: `tests/test_photo_authenticity_mainline.py`
- Create: `photo_authenticity/models/releases/non-real-photo-v2/model.joblib`
- Create: `photo_authenticity/models/releases/non-real-photo-v2/metadata.json`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `PhotoAuthenticityConfig.from_env(env: Mapping[str, str]) -> PhotoAuthenticityConfig`
- Produces: `validate_image_observations(raw: Any, expected_image_ids: Sequence[str]) -> dict[str, ImageObservation]`
- Produces: `derive_v4_result(observation: ImageObservation) -> tuple[str, str]`
- Produces: `FrozenFFTRescue.load(artifact_dir: Path) -> FrozenFFTRescue`
- Produces: `FrozenFFTRescue.score(image_path: Path) -> tuple[float, dict[str, Any]]`
- Produces: `evaluate_authenticity_images(...) -> AuthenticityOrderResult`

- [ ] **Step 1: Write failing configuration and schema tests**

Add tests proving:

```python
def test_mode_defaults_off_and_rejects_unknown_value(monkeypatch): ...
def test_validator_requires_exact_unique_image_id_coverage(): ...
def test_validator_rejects_unknown_edge_owner_evidence_and_region(): ...
def test_off_mode_does_not_load_artifact_or_images(monkeypatch): ...
```

- [ ] **Step 2: Run RED configuration/schema tests**

Run:

```powershell
python -m pytest tests/test_photo_authenticity_mainline.py -k "mode or validator or off" -q
```

Expected: FAIL because the module and interfaces do not exist.

- [ ] **Step 3: Implement immutable config and strict observation contracts**

Implement exact enums from the approved design and preserve observations by `image_id`. Validation returns no partial success: any missing, duplicate, extra, or invalid entry raises `PhotoAuthenticitySchemaError` containing the affected IDs.

- [ ] **Step 4: Write failing deterministic V4 rule tests**

Cover external carrier, external UI, product-screen UI exemption, printed/nested carrier, cross-object moire region count, two/one carrier boundaries, abrupt-cutoff plus outer optics, weak-only manual, and no-evidence.

- [ ] **Step 5: Run RED V4 rule tests**

Run the exact tests and confirm they fail because `derive_v4_result` is absent.

- [ ] **Step 6: Implement deterministic V4 derivation**

Port the frozen rule semantics without allowing the model `result` field to control the result.

- [ ] **Step 7: Write failing artifact and FFT invariant tests**

Tests must assert metadata/model SHA verification, 795 input features, threshold 0.995, deterministic output, `no_evidence -> manual_review` only, two-attempt local failure behavior, and forbidden metadata independence.

- [ ] **Step 8: Run RED artifact tests**

Expected: FAIL because no artifact loader/extractor exists.

- [ ] **Step 9: Copy and verify frozen artifact, implement FFT runtime**

Copy model bytes and metadata from the validated worktree artifact. Implement the existing Y/Cb/Cr full+2x2 FFT extractor and verify the model SHA at load time. Add `scikit-learn>=1.4` and `joblib>=1.3` to requirements; do not add Torch/Torchvision.

- [ ] **Step 10: Run Task 1 GREEN tests**

```powershell
python -m pytest tests/test_photo_authenticity_mainline.py -q
```

Expected: all Task 1 tests pass.

- [ ] **Step 11: Commit Task 1 files only**

```powershell
git add tools/photo_authenticity_mainline.py tests/test_photo_authenticity_mainline.py requirements.txt photo_authenticity/models/releases/non-real-photo-v2/model.joblib photo_authenticity/models/releases/non-real-photo-v2/metadata.json
git commit -m "feat: add frozen photo authenticity gate"
```

### Task 2: Merge Per-Image Observations into Compliance Prompt and Normalization

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `tests/test_guobu_v2_rules.py`
- Modify: `tests/test_guobu_adversarial_policy.py`

**Interfaces:**
- Consumes Task 1 observation enums and validator.
- Produces: `PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM: str`
- Produces: `compliance_prompt_for_category(category, *, include_photo_authenticity=False) -> str`
- Produces: `_normalize_photo_authenticity_observations(compliance, expected_image_ids) -> dict[str, ImageObservation]`

- [ ] **Step 1: Write failing prompt inclusion tests**

Assert old prompt bytes are unchanged when `include_photo_authenticity=False`, merged prompt contains the approved per-image task/schema exactly once when true, and all category prompts include the same addendum.

- [ ] **Step 2: Run prompt tests and confirm RED**

Expected: FAIL because the optional argument/addendum does not exist.

- [ ] **Step 3: Implement prompt addendum and optional prompt construction**

Do not rewrite existing category rules. Append the authenticity section after the existing schema constraints and require `photo_authenticity_by_image` keyed by existing input `image_id`.

- [ ] **Step 4: Write failing normalization tests**

Cover exact coverage for 3 and 6 images, order-independent image IDs, duplicates, missing/extra IDs, null/invalid values, and the requirement that cross-object evidence regions belong to one image record.

- [ ] **Step 5: Implement normalization adapter**

Delegate strict validation to Task 1 and store a JSON-serializable normalized structure in compliance output. Do not change existing compliance manual codes at this stage.

- [ ] **Step 6: Run focused and existing compliance tests**

```powershell
python -m pytest tests/test_guobu_v2_rules.py tests/test_guobu_adversarial_policy.py tests/test_photo_authenticity_mainline.py -q
```

- [ ] **Step 7: Commit Task 2 files only**

```powershell
git add tools/run_guobu_model_audit_v2.py tests/test_guobu_v2_rules.py tests/test_guobu_adversarial_policy.py
git commit -m "feat: request per-image authenticity observations"
```

### Task 3: Off, Shadow, Enforce, Fallback, and Order Aggregation

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `tools/photo_authenticity_mainline.py`
- Modify: `tests/test_photo_authenticity_mainline.py`
- Modify: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- Consumes normalized observations, compliance images, local paths, and frozen FFT runtime.
- Produces: `apply_photo_authenticity_gate(legacy_row, compliance, images, config, fallback) -> dict[str, Any]`
- Produces order reason codes `NON_REAL_PHOTO_STRONG_RISK`, `NON_REAL_PHOTO_REVIEW`, `NON_REAL_PHOTO_FFT_RESCUE`, `PHOTO_AUTHENTICITY_SERVICE_FAILURE`.

- [ ] **Step 1: Write failing off-mode regression tests**

Patch model responses with no authenticity field and assert `off` produces a row byte-for-byte equivalent for all existing columns, performs no artifact load, no FFT, and no fallback call.

- [ ] **Step 2: Write failing shadow/enforce aggregation tests**

Assert shadow records `would_manual` but preserves legacy `manual_flag`; enforce adds the correct reason code only for legacy-pass orders; legacy-manual orders skip the gate; V4 high/manual remain unchanged; FFT only scores N0 images.

- [ ] **Step 3: Run RED gate tests**

Expected: FAIL because the gate is not wired into `audit_task_hybrid`.

- [ ] **Step 4: Implement post-compliance gate wiring**

Read mode/config once per order invocation, include merged prompt only when mode is not off, map `compliance_images` by `image_id` to local paths, evaluate observations, run FFT on N0, and merge results after `_final_row` has built the legacy outcome.

- [ ] **Step 5: Write failing fallback tests**

Cover missing field, partial image coverage, invalid enum, at most one independent V4 fallback per order, multiple missing images, fallback cache identity, shadow failure behavior, enforce failure manual behavior, and no unbounded retries.

- [ ] **Step 6: Implement independent fallback adapter**

Reuse the approved single-image V4 prompt and existing HTTP client. Force `enable_thinking=false`, a distinct stage/cache key, and `PHOTO_AUTHENTICITY_MAX_FALLBACK_CALLS_PER_ORDER=1`.

- [ ] **Step 7: Run full gate regression tests**

```powershell
python -m pytest tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py tests/test_guobu_adversarial_policy.py -q
```

- [ ] **Step 8: Commit Task 3 files only**

```powershell
git add tools/photo_authenticity_mainline.py tools/run_guobu_model_audit_v2.py tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py
git commit -m "feat: add shadowable authenticity order gate"
```

### Task 4: Reporting, CLI Configuration, and Rollback Evidence

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `tests/test_guobu_v2_rules.py`
- Modify: `docs/guobu-audit-project-memory.md`
- Modify: `docs/non-real-photo-detection-handoff-20260715.md`

**Interfaces:**
- Produces the approved nine order-level evidence columns.
- Produces summary counters for mode, would-manual orders, strong/manual/FFT/failure reasons, fallback calls, added latency, and tokens.

- [ ] **Step 1: Write failing output-column tests**

Assert Excel/CSV/JSON rows contain all approved authenticity columns in shadow and enforce, while off-mode values are empty/default and existing column order remains stable before the appended columns.

- [ ] **Step 2: Write failing environment/CLI tests**

Assert default off, explicit shadow/enforce, invalid mode failure before any model call, artifact path override, threshold locked to metadata unless test-only dependency injection is used, and rollback to off.

- [ ] **Step 3: Implement report columns and summary aggregation**

Append fields without renaming existing columns. JSON-encode per-image results deterministically. Count authenticity tokens separately from total tokens while still including them in the order total.

- [ ] **Step 4: Update operations documentation**

Document exact shadow command, off rollback command, enforce prerequisites, artifact hash, fallback behavior, and the fact that merged-prompt accuracy is not yet inherited from single-image V4.

- [ ] **Step 5: Run reporting regression tests**

```powershell
python -m pytest tests/test_guobu_v2_rules.py tests/test_guobu_adversarial_policy.py tests/test_photo_authenticity_mainline.py -q
```

- [ ] **Step 6: Commit Task 4 files only**

```powershell
git add tools/run_guobu_model_audit_v2.py tests/test_guobu_v2_rules.py docs/guobu-audit-project-memory.md docs/non-real-photo-detection-handoff-20260715.md
git commit -m "docs: expose authenticity shadow operations"
```

### Task 5: Full-Chain Merged Validation and Adversarial Review

**Files:**
- Create: `tools/evaluate_mainline_photo_authenticity_shadow.py`
- Create: `tests/test_mainline_photo_authenticity_shadow.py`
- Create: `reports/model_audit/photo_authenticity_shadow_<timestamp>/` (runtime artifact, do not commit caches or credentials)
- Modify: `docs/guobu-audit-project-memory.md`

**Interfaces:**
- Consumes the current retained 200 task files and merged final baseline spreadsheet/JSON.
- Produces a merged-path report for the retained orders without adding a second baseline model call and without mutating backend state.

- [ ] **Step 1: Write failing shadow evaluator tests**

Test exact legacy-pass selection, all-image coverage, no backend mutation, resume/cache handling, reuse of stored baseline results, per-order added latency/tokens, fallback counts, and acceptance gate calculation.

- [ ] **Step 2: Implement evaluator and dry-run mode**

Dry-run must list 169 orders and 527 images from the retained batch without calling the API.

- [ ] **Step 3: Run dry-run and verify retained-batch counts**

```powershell
python -m tools.evaluate_mainline_photo_authenticity_shadow --dry-run ...
```

Expected: 169 candidate orders, 527 images, zero writes outside the requested report directory.

- [ ] **Step 4: Run full shadow replay**

Use the user-level API environment variables, `qwen3.7-plus`, `enable_thinking=false`, the directly merged prompt, existing retained tasks, and a fresh cache directory. Do not issue a second baseline compliance call and do not modify backend state.

- [ ] **Step 5: Evaluate acceptance gates**

Report:

- schema success and image coverage;
- would-manual orders out of 169;
- added elapsed time and tokens;
- fallback call count and failure rate;
- exact strong/manual/FFT reason distribution;
- whether <=20 orders, <=12 minutes, <=300,000 tokens, <=1% schema/final failures all pass.

- [ ] **Step 6: Independent adversarial reviewer final gate**

The Agency `Reality Checker` reviews the complete chain and returns `APPROVED` or `NOT APPROVED`. Critical/Important technical findings must be fixed and re-reviewed. Any unresolved business-policy ambiguity is presented to the user before enforce mode is enabled.

- [ ] **Step 7: Preserve explicit rollback**

The user has authorized direct merged-path testing with human review as the safety net. Keep the default configuration `off`, require an explicit runtime mode to activate the gate, and document the one-step rollback to `off`.

- [ ] **Step 8: Commit evaluator/tests/docs, not runtime secrets or caches**

```powershell
git add tools/evaluate_mainline_photo_authenticity_shadow.py tests/test_mainline_photo_authenticity_shadow.py docs/guobu-audit-project-memory.md
git commit -m "test: validate merged authenticity shadow chain"
```
