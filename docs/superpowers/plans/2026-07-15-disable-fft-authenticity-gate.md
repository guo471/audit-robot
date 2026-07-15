# Disable FFT Authenticity Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disable FFT order intervention by default while keeping all structured strong and weak evidence routed to manual review.

**Architecture:** Extend `PhotoAuthenticityConfig` with an explicit disabled-by-default FFT switch. `evaluate_authenticity_images` returns `no_evidence` immediately for evidence-free images when FFT is disabled, without artifact or image IO; the frozen FFT path remains available only through an explicit environment opt-in.

**Tech Stack:** Python 3.11, pytest, existing Pillow/NumPy/joblib frozen FFT runtime.

## Global Constraints

- `PHOTO_AUTHENTICITY_FFT_ENABLED` defaults to `false`.
- Disabled FFT performs zero artifact loads, zero FFT image reads, and zero FFT scoring.
- Any valid strong or weak structured evidence transfers an otherwise-pass order to manual review.
- Model prose does not override structured evidence.
- Bare `abrupt_cutoff` without `EDGE_CUTOFF` or `OUTER_PLANE_OPTICS` does not transfer an order.
- Existing `off / shadow / enforce` behavior and backend read-only behavior remain unchanged.
- Every behavior change follows RED-GREEN-REFACTOR.

---

### Task 1: Disable FFT by Default and Preserve Evidence Routing

**Files:**
- Modify: `tools/photo_authenticity_mainline.py`
- Modify: `tests/test_photo_authenticity_mainline.py`
- Modify: `tests/test_guobu_v2_rules.py`
- Modify: `docs/guobu-audit-project-memory.md`

**Interfaces:**
- Produces: `PhotoAuthenticityConfig.fft_enabled: bool`
- Consumes: `PHOTO_AUTHENTICITY_FFT_ENABLED=true|false`

- [ ] **Step 1: Write failing configuration and zero-IO tests**

Add tests asserting the default is disabled, accepted true/false values are parsed strictly, and an evidence-free image in enforce mode does not call `FrozenFFTRescue.load`, inspect the image path, or create a manual result.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
.venv-photo-auth\Scripts\python.exe -m pytest tests/test_photo_authenticity_mainline.py -k "fft_disabled or fft_enabled_config" -q
```

Expected: FAIL because `fft_enabled` does not exist and the current evaluator loads/scores FFT.

- [ ] **Step 3: Implement the disabled-by-default switch**

Add strict boolean parsing in `PhotoAuthenticityConfig.from_env`. In `evaluate_authenticity_images`, after `derive_v4_result` returns `no_evidence`, return an `AuthenticityImageResult` with `result="no_evidence"`, the derived rule, no score, and no artifact/image access when `fft_enabled` is false.

- [ ] **Step 4: Write failing evidence-conflict and crop-boundary tests**

Parameterize all weak evidence codes with `reason="正常实拍"` and assert `manual_review`. Add a bare `abrupt_cutoff` observation with no evidence and assert `no_evidence`; retain the existing `abrupt_cutoff + OUTER_PLANE_OPTICS` high-risk assertion.

- [ ] **Step 5: Implement minimal cutoff rule correction**

Remove bare `abrupt_cutoff` from the R9 fallback condition. Evidence-backed `EDGE_CUTOFF`, any other weak evidence, and effective strong evidence remain manual.

- [ ] **Step 6: Preserve explicit FFT opt-in**

Update existing FFT tests to pass `PHOTO_AUTHENTICITY_FFT_ENABLED=true` and verify the frozen threshold and failure behavior remain unchanged when explicitly enabled.

- [ ] **Step 7: Run GREEN regression suites**

```powershell
.venv-photo-auth\Scripts\python.exe -m pytest tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py tests/test_guobu_adversarial_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Update project memory and commit**

Document that FFT is disabled by default after confirmed real-photo false positives, while structured strong/weak evidence remains enforceable.

```powershell
git add tools/photo_authenticity_mainline.py tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py docs/guobu-audit-project-memory.md docs/superpowers/specs/2026-07-15-disable-fft-authenticity-gate-design.md docs/superpowers/plans/2026-07-15-disable-fft-authenticity-gate.md
git commit -m "fix: disable fft authenticity intervention"
```
