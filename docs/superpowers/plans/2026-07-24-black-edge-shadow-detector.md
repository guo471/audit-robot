# Black Edge Shadow Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a standalone pixel/geometry shadow detector and evaluate it on the known missed black-edge images, the confirmed non-real library, and the confirmed real-photo control library.

**Architecture:** `tools/black_edge_shadow_detector.py` contains pure image analysis and a small CLI. `tests/test_black_edge_shadow_detector.py` covers synthetic geometry and negative cases. `tools/run_black_edge_shadow_eval.py` builds an evaluation manifest from the two labeled libraries plus explicit extra images and writes JSON/CSV metrics. No production module imports the detector.

**Tech Stack:** Python 3, Pillow, NumPy, pytest, standard library CSV/JSON.

## Global Constraints

- Do not modify production prompts, `tools/photo_authenticity_mainline.py`, R1-R10, SN rules, or audit runners.
- Do not copy or rewrite the original sample directories.
- The detector is shadow-only and must never emit an automatic rejection.
- All images supplied to the evaluator are scanned, regardless of image role.
- Candidate statuses are `strong_candidate`, `uncertain_candidate`, and `none`.

### Task 1: Define the detector contract with failing tests

**Files:**
- Create: `tests/test_black_edge_shadow_detector.py`
- Create: `tools/black_edge_shadow_detector.py`

**Interfaces:**
- `scan_array(image: np.ndarray, config: DetectorConfig | None = None) -> ImageScan`
- `scan_image(path: Path, config: DetectorConfig | None = None) -> ImageScan`
- `ImageScan.to_dict() -> dict[str, object]`

- [ ] **Step 1: Write failing tests for a rectangular bottom band, a tapered side wedge, a gradual shadow, and an internal product frame.**

```python
def test_full_bottom_band_is_a_strong_candidate():
    image = white_image()
    image[-24:, :] = 8
    result = scan_array(image)
    assert result.status == "strong_candidate"
    assert result.sides["bottom"].status == "strong_candidate"


def test_tapered_right_edge_is_detected_without_full_side_coverage():
    image = white_image()
    for row in range(30, 170):
        width = max(2, int((row - 30) * 0.20))
        image[row, -width:] = 5
    result = scan_array(image)
    assert result.status in {"strong_candidate", "uncertain_candidate"}
    assert result.sides["right"].status != "none"


def test_gradual_dark_shadow_is_not_strong():
    image = white_image()
    for depth in range(40):
        image[-depth - 1, :] = 180 - depth * 2
    result = scan_array(image)
    assert result.sides["bottom"].status != "strong_candidate"


def test_internal_product_frame_does_not_count_as_outer_edge():
    image = white_image()
    image[70:150, 70:150] = 5
    result = scan_array(image)
    assert result.status == "none"
```

- [ ] **Step 2: Run the new tests and verify they fail because the detector module is missing.**

Run: `pytest tests/test_black_edge_shadow_detector.py -q`

Expected: collection failure showing that `tools.black_edge_shadow_detector` is not available.

### Task 2: Implement the minimal deterministic detector

**Files:**
- Modify: `tools/black_edge_shadow_detector.py`
- Test: `tests/test_black_edge_shadow_detector.py`

**Interfaces:**
- `DetectorConfig` stores relative edge depth, luminance, continuity, contrast, and fit thresholds.
- `scan_array` converts RGB/RGBA arrays to grayscale, evaluates top/right/bottom/left independently, combines paired adjacent sides, and returns a serializable result.

- [ ] **Step 1: Add the dataclasses and grayscale normalization.**
- [ ] **Step 2: Add outer-edge run detection and inner-boundary contrast measurement.**
- [ ] **Step 3: Add linear boundary-fit scoring for straight and tapered bands.**
- [ ] **Step 4: Add strong/uncertain/none classification without any business decision.**
- [ ] **Step 5: Run the focused tests and confirm they pass.**

Run: `pytest tests/test_black_edge_shadow_detector.py -q`

Expected: all focused tests pass.

### Task 3: Add the shadow evaluation runner

**Files:**
- Create: `tools/run_black_edge_shadow_eval.py`
- Create: `tests/test_black_edge_shadow_eval.py`

**Interfaces:**
- `build_manifest(real_manifest, non_real_root, extra_positive_paths) -> list[dict[str, str]]`
- `run_evaluation(manifest, output_dir) -> dict[str, object]`

- [ ] **Step 1: Write failing tests for manifest labels, extra-image handling, and metric calculation.**
- [ ] **Step 2: Run the focused evaluation tests and verify the expected missing-function failures.**
- [ ] **Step 3: Implement manifest loading without changing source directories.**
- [ ] **Step 4: Implement JSON/CSV result output, positive recall, real-control candidate rate, and development-gate results.**
- [ ] **Step 5: Run the focused evaluation tests and confirm they pass.**

### Task 4: Run the real mixed-sample shadow test

**Files:**
- Create: `reports/black_edge_shadow_20260724/` generated outputs only.

- [ ] **Step 1: Run the evaluator over 335 non-real images, 376 real-control images, and the five known missed edge images.**

Run: `python tools/run_black_edge_shadow_eval.py --real-manifest "实拍图样本/manifest.csv" --non-real-root "非实拍样本/非实拍样本" --extra-positive ... --output-dir "reports/black_edge_shadow_20260724"`

- [ ] **Step 2: Verify the development gate catches at least four of the five known missed images and emits no automatic rejection field.**
- [ ] **Step 3: Report non-real candidate recall, real-control candidate rate, candidate details, and any false-positive examples.**

### Task 5: Regression verification

**Files:**
- No production files modified.

- [ ] **Step 1: Run the focused detector and evaluator tests.**
- [ ] **Step 2: Run the existing photo-authenticity unit tests.**
- [ ] **Step 3: Check `git diff` and confirm only the new detector, tests, plan/spec, and generated report files changed.**
