# External Carrier Region Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off experiment that marks only high-precision outer-edge dark-band candidates on the full image and asks the existing compliance model call to confirm whether the marked region belongs to an external display.

**Architecture:** Reuse `PHOTO_AUTH_EDGE_MAPPING_MODE=off|on` as the rollback switch. Extend the standalone pixel detector to return source-image candidate geometry and create full-scene annotated copies only for strong candidates. The hybrid runner sends those copies, candidate metadata, and a concise classifier instruction in the existing compliance call; the existing authenticity schema and R1-R10 remain unchanged.

**Tech Stack:** Python 3, Pillow, NumPy, pytest, existing Guobu hybrid audit runner.

## Global Constraints

- `PHOTO_AUTH_EDGE_MAPPING_MODE=off` must not scan images, create files, mutate payloads, replace model images, append the plugin prompt, or change cache keys and decisions.
- Do not add a model call.
- Do not modify SN, address, category, activation, duplicate-image, or existing authenticity R1-R10 decisions.
- Only a strong local geometry candidate may be marked; uncertain candidates never become strong by pair count.
- The model must classify the marked original region, not the added marker itself.
- Clothing, trousers, shadows, dark backgrounds, product bodies, product screens, bezels, packaging, and ordinary crop boundaries are explicit negative classes.
- Local or annotation failure must preserve baseline images and payload for that image.
- The experiment remains default off after implementation.

---

### Task 1: Freeze the rollback and geometry contracts

**Files:**
- Modify: `tests/test_black_edge_shadow_detector.py`
- Modify: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- `scan_array(image) -> ImageScan` exposes strong candidate side geometry.
- `prepare_photo_auth_edge_mapping_inputs(images, payload, mode, output_dir) -> tuple[list, dict]` returns model inputs without mutating callers.

- [ ] Write failing tests proving the plugin-off path is an exact identity path and does not call the detector.
- [ ] Write failing tests for true outer-edge contact, abrupt straight inner boundary, low texture, and no uncertain-pair promotion.
- [ ] Write failing negatives for fabric texture, gradual shadow, internal bezel, black object, and short isolated corner.
- [ ] Run focused tests and confirm failures are caused by missing behavior.

### Task 2: Implement precision-first candidate geometry and annotation

**Files:**
- Modify: `tools/black_edge_shadow_detector.py`
- Test: `tests/test_black_edge_shadow_detector.py`

**Interfaces:**
- `SideEvidence` includes normalized tangent start/end and source-space boundary depth.
- `annotate_strong_candidates(source, destination, scan) -> Path` retains the full scene and draws only thin machine markers outside/along candidate boundaries.

- [ ] Require an actual outermost-edge run, sustained straight boundary, abrupt transition, and low-texture dark component.
- [ ] Remove image-level promotion from two uncertain sides.
- [ ] Add full-scene annotation with deterministic output.
- [ ] Run focused tests and confirm all detector tests pass.

### Task 3: Integrate with the existing compliance call

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `prompts/photo_auth_edge_mapping_review.txt`
- Test: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- `prepare_photo_auth_edge_mapping_inputs(...)` lazily imports the detector only when mode is on.
- Payload field `photo_auth_edge_candidates` records only strong sides and machine marker semantics.

- [ ] When on, scan every compliance image and replace only candidate image URLs with annotated local copies.
- [ ] Keep image IDs and full scene context unchanged; clear remote URL only on annotated copies.
- [ ] Add concise Chinese prompt instructions for marked-region ownership classification using the existing schema.
- [ ] Keep the existing compliance call count unchanged.
- [ ] Preserve the original image and payload on per-image scan or annotation failure.
- [ ] Run focused runner tests and confirm they pass.

### Task 4: Regression and evidence-based experiment

**Files:**
- Create: `reports/photo_auth_edge_region_20260724/` generated diagnostics only.

- [ ] Run detector and Guobu rule tests.
- [ ] Run the existing photo-authenticity regression suite.
- [ ] Run the enabled plugin on the five known missed orders and at least 15 previously passed controls.
- [ ] Repeat model-sensitive cases three times without reusing model cache.
- [ ] Report target hits, new control interventions, model calls, latency, and token changes.
- [ ] If controls show any new unsupported intervention or target behavior is unstable, leave the plugin off.

### Task 5: Adversarial review and rollback verification

**Files:**
- No new production files.

- [ ] Business reviewer verifies all negative classes and evidence boundaries.
- [ ] Code reviewer verifies off-path identity, cache separation, failure behavior, concurrency, and unrelated diffs.
- [ ] Run a final explicit off-mode test and compare its prompt, payload, image list, and decision with baseline.
- [ ] Keep `PHOTO_AUTH_EDGE_MAPPING_MODE` default value as `off`.
