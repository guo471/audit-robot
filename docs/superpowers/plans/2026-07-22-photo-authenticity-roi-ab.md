# Photo Authenticity ROI A/B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a reversible shadow-only A/B comparison of GeometryScout and baseline-gated ROI review on the local real/non-real photo libraries without changing production rules or model variables.

**Architecture:** New experiment-only tools build a deduplicated order-level dataset, seal labels separately from a content-addressed runtime manifest, generate deterministic relative-geometry candidates and order-level evidence montages, call the existing `qwen3.7-plus` client through an experiment-local byte-hashed cache, and emit paired JSON/XLSX scorecards. Candidate A and B consume the same frozen baseline records; model output is observation-only and deterministic local policies can only propose experimental `manual_review`. Generated artifacts live under a unique report directory and never enter the production audit path.

**Tech Stack:** Python 3.11, Pillow, NumPy, OpenCV (`cv2`), existing `tools.run_guobu_model_audit_v2` HTTP/cache helpers, pytest, openpyxl.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-22-photo-authenticity-roi-ab-design.md` exactly.
- Pass `--model qwen3.7-plus` explicitly; do not set or modify `VISION_MODEL_NAME`.
- Preserve `SN_POLICY_VERSION=v1`, `SN_CHAR_REVIEW_MODE=off`, `SN_LABEL_AUTH_REVIEW_MODE=off`, and `DIGITAL_ACTIVATION_EVIDENCE_MODE=on` in recorded manifests.
- Do not modify `tools/photo_authenticity_mainline.py`, `tools/run_guobu_model_audit_v2.py`, production prompts, or unrelated rules.
- Candidate outputs are shadow-only and may only propose `manual_review`; automatic rejection is forbidden.
- Use order/record IDs only for grouping and evaluation. Geometry and decision code must not consume IDs, labels, source folder names, or absolute paths.
- The runner accepts only an anonymous runtime bundle and must reject fields named `label`, `group_id`, `provenance`, `path`, `source_path`, `order_id`, `source_order_id`, or `partition` anywhere in its input.
- The image-only libraries use a frozen `isolated_authenticity` baseline because their records cannot reconstruct the full production category payload. Reports must mark `production_equivalent=false`; this experiment cannot recommend production rollout without a separate full-payload replay.
- Freeze and hash the normalized model request, client-code SHA-256, geometry config, prompts, opaque-arm mapping, scorecard config, and pricing manifest before paid calls.
- Fail closed at the experiment boundary: preserve the baseline decision, record the candidate error, and stop candidate calls when a budget gate trips.

---

### Task 1: Frozen Order-Level Dataset Manifest

**Files:**
- Create: `tools/photo_auth_roi_ab_dataset.py`
- Create: `tests/test_photo_auth_roi_ab_dataset.py`

**Interfaces:**
- Produces: `RuntimeItem`, `LabelRecord`, `build_dataset_manifests(...)`, `write_dataset_manifests(...)`, anonymous `runtime_manifest.json`, and sealed `label_key.json` consumed only by the report task.
- Consumes: `实拍图样本/manifest.csv`, `非实拍样本/非实拍样本`, the six development order image directories, two independent blind-label review files, and an optional near-duplicate decisions file. Source-directory labels are provenance only and do not count as either independent review.

- [ ] **Step 1: Write failing parsing, grouping, and leakage tests**

```python
def test_real_manifest_groups_images_by_complete_order_id(tmp_path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        '序号,文件名,订单号,图片分组,图片ID,原始路径,来源URL\n'
        '1,a.jpg,481173000000000000000001,商品照片,img_001,C:/a.jpg,\n'
        '2,b.jpg,481173000000000000000001,激活照片,img_002,C:/b.jpg,\n',
        encoding="utf-8-sig",
    )
    items = load_real_items(manifest, root=tmp_path)
    assert [(item.group_id, [image.image_id for image in item.images]) for item in items] == [
        ("481173000000000000000001", ["img_001", "img_002"]),
    ]


def test_split_is_group_level_deterministic_and_excludes_development(tmp_path):
    items = fixture_items(real=12, non_real=12)
    split = split_items(items, seed=20260722, development_ids={"real-0", "non-real-0"})
    assert split == split_items(items, seed=20260722, development_ids={"real-0", "non-real-0"})
    assert not ({"real-0", "non-real-0"} & set(split.partitions["blind"]))
    assert component_ids(split, "calibration").isdisjoint(component_ids(split, "blind"))


def test_runtime_manifest_contains_no_label_or_source_identity(tmp_path):
    runtime_path, label_path = build_fixture_manifests(tmp_path)
    runtime_text = runtime_path.read_text(encoding="utf-8")
    assert all(name not in runtime_text for name in (
        '"label"', '"group_id"', '"provenance"', '"source_path"', 'R001', '实拍图样本', '非实拍样本',
    ))
    assert label_path.parent != runtime_path.parent


def test_runner_contract_rejects_recursive_label_fields(tmp_path):
    runtime = valid_runtime_manifest()
    runtime["items"][0]["images"][0]["source_path"] = "real/a.jpg"
    with pytest.raises(ValueError, match="forbidden runtime field"):
        validate_runtime_manifest(runtime)
```

- [ ] **Step 2: Run the dataset tests and verify RED**

Run: `python -m pytest -q tests/test_photo_auth_roi_ab_dataset.py`

Expected: FAIL during collection because `tools.photo_auth_roi_ab_dataset` does not exist.

- [ ] **Step 3: Implement strict manifest types and loaders**

```python
@dataclass(frozen=True)
class RuntimeImage:
    asset_id: str
    ordinal: int
    sha256: str
    phash: str
    byte_length: int
    media_type: str
    width: int
    height: int


@dataclass(frozen=True)
class RuntimeItem:
    sample_id: str
    images: tuple[RuntimeImage, ...]


@dataclass(frozen=True)
class LabelRecord:
    sample_id: str
    group_id: str
    label: Literal["real", "non_real", "uncertain"]
    provenance: str
    source_date: str | None
    reviewer_1: str
    reviewer_2: str
    adjudicator: str | None


@dataclass(frozen=True)
class SealedDatasetIndex:
    seed: int
    partitions: dict[str, tuple[str, ...]]
    stability_ids: tuple[str, ...]
    oot_ids: tuple[str, ...]
    oot_status: Literal["available", "not_available"]
```

Implement SHA-256, perceptual hash, exact duplicate grouping, deterministic random splitting, anonymous IDs, minimum-count checks, and rejection of missing/unreadable images. Write a separate runtime bundle for each partition. Each bundle contains only `runtime_manifest.json` and `assets/<asset_id>.bin`; the manifest stores no path or partition, and `RuntimeAssetStore` derives the fixed asset location from a validated opaque ID. Near-duplicate pHash pairs use stable pair IDs and transitive components; unresolved/conflicting components are excluded until explicitly resolved in an input decisions JSON, and a component can never cross partitions. Two separately supplied reviewers independently label blind/OOT samples without seeing source labels; disagreements require a third adjudication record and unresolved samples become `uncertain`. Source-directory labels remain provenance and are checked against, not substituted for, the two reviews. Partition membership, source identity, dates, review records, and OOT status exist only in the sealed label index. If dates are unavailable, record `oot_status=not_available` with a nonempty reason rather than silently omitting OOT metrics.

- [ ] **Step 4: Add a CLI that writes but never overwrites a completed manifest**

```python
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-manifest", required=True, type=Path)
    parser.add_argument("--non-real-root", required=True, type=Path)
    parser.add_argument("--development-json", required=True, type=Path)
    parser.add_argument("--reviewer-1-labels", required=True, type=Path)
    parser.add_argument("--reviewer-2-labels", required=True, type=Path)
    parser.add_argument("--adjudication-labels", type=Path)
    parser.add_argument("--near-duplicate-decisions", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sealed-label-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=20260722)
    args = parser.parse_args(argv)
    if (args.output_dir / "runtime_manifest.json").exists():
        parser.error("dataset manifest already exists; choose a new output directory")
    if args.output_dir.resolve() == args.sealed_label_dir.resolve():
        parser.error("runtime and sealed label directories must differ")
    write_dataset_manifests(build_dataset_manifests(args), args.output_dir, args.sealed_label_dir)
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest -q tests/test_photo_auth_roi_ab_dataset.py`

Expected: PASS.

```powershell
git add tools/photo_auth_roi_ab_dataset.py tests/test_photo_auth_roi_ab_dataset.py
git commit -m "test: freeze photo authenticity AB dataset"
```

### Task 2: Deterministic GeometryScout and Evidence Montages

**Files:**
- Create: `tools/photo_auth_roi_geometry.py`
- Create: `tests/test_photo_auth_roi_geometry.py`

**Interfaces:**
- Produces: `GeometryConfig`, `GeometryCandidate`, `scan_image(bytes, config)`, and `build_order_montage(...)`.
- Consumes: unlabeled image bytes only. IDs and labels are excluded from scan inputs.

- [ ] **Step 1: Write RED tests for G1, G2, G3, and hard negatives**

```python
def test_g1_detects_relative_full_width_bottom_band(tmp_path):
    path = synthetic_scene(tmp_path, size=(800, 1200), bottom_band_ratio=0.025)
    candidates = scan_image(path.read_bytes(), GeometryConfig.defaults())
    assert any(item.kind == "G1" and item.side == "bottom" for item in candidates)


def test_g2_requires_pair_or_independent_outer_optics(tmp_path):
    paired = synthetic_scene(tmp_path, left_band_ratio=0.01, right_band_ratio=0.008)
    single = synthetic_scene(tmp_path, left_band_ratio=0.01)
    assert any(item.kind == "G2" for item in scan_image(paired.read_bytes(), GeometryConfig.defaults()))
    assert not any(item.kind == "G2" for item in scan_image(single.read_bytes(), GeometryConfig.defaults()))


def test_g3_detects_nested_frame_but_not_product_phone_outline(tmp_path):
    nested = synthetic_nested_frame(tmp_path, outside_scene=True)
    product = synthetic_product_phone(tmp_path, continuous_scene=True)
    assert any(item.kind == "G3" for item in scan_image(nested.read_bytes(), GeometryConfig.defaults()))
    assert not any(item.kind == "G3" for item in scan_image(product.read_bytes(), GeometryConfig.defaults()))
```

- [ ] **Step 2: Run geometry tests and verify RED**

Run: `python -m pytest -q tests/test_photo_auth_roi_geometry.py`

Expected: FAIL during collection because the geometry module does not exist.

- [ ] **Step 3: Implement relative geometry with serializable evidence**

```python
@dataclass(frozen=True)
class GeometryCandidate:
    kind: Literal["G1", "G2", "G3"]
    confidence: Literal["low", "medium", "high"]
    normalized_box: tuple[float, float, float, float]
    side: str | None
    signals: tuple[str, ...]


@dataclass(frozen=True)
class GeometryConfig:
    outer_depth_min: float = 0.005
    outer_depth_max: float = 0.12
    partial_side_min: float = 0.25
    nested_area_min: float = 0.20
    nested_area_max: float = 0.95
```

Use EXIF-transposed RGB input, longest-side normalization, grayscale/HSV contrast, Canny edges, probabilistic Hough lines, contour quadrilaterals, and normalized coordinates. Keep thresholds in `GeometryConfig`; do not branch on dimensions, filenames, paths, or labels.

- [ ] **Step 4: Implement one deterministic montage format for both candidates**

```python
def build_order_montage(
    samples: Sequence[tuple[RuntimeImage, bytes, Sequence[GeometryCandidate]]],
    arm_candidate_refs: Mapping[str, Sequence[str]],
    output_path: Path,
    *,
    size: tuple[int, int] = (2048, 2048),
) -> OrderMontageManifest:
    """Render all overviews plus the deterministic union of both arms' ROIs."""
```

The order manifest records every asset exactly once, source SHA-256, montage SHA-256, stable panel IDs, normalized crop boxes, candidate kinds, renderer hash, and dimensions. Before rendering, both local selectors run on the complete unlabeled bundle. The 2048x2048 sRGB montage contains overviews for all order images and the deterministic union of the candidates that actually triggered either arm, ranked only by frozen geometry score, kind, and image ordinal. The union may contain at most six ROIs; if it or the order's image count exceeds capacity, both arms symmetrically mark that order as a candidate error instead of truncating. Text annotations contain only ordinal, panel ID, candidate kind, and coordinates, never sample/order IDs, labels, or source names. If both arms trigger an order, they receive the identical montage SHA. Each arm makes at most one model call per order, and its response must reference only panels present in this manifest.

- [ ] **Step 5: Add development-image regression tests**

Assert candidate generation on the five named positives and absence of a high-confidence combined signal on the negative-control images. Keep these tests separate from blind metrics and use repository-relative image fixtures resolved from `data/guobu_api_all_20260721_161849/images`.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest -q tests/test_photo_auth_roi_geometry.py`

Expected: PASS.

```powershell
git add tools/photo_auth_roi_geometry.py tests/test_photo_auth_roi_geometry.py
git commit -m "feat: add photo carrier geometry scout"
```

### Task 3: Frozen Candidate Policies and Structured Review Schemas

**Files:**
- Create: `prompts/photo_auth_roi_reviewer_a.txt`
- Create: `prompts/photo_auth_roi_reviewer_b.txt`
- Create: `tools/photo_auth_roi_review.py`
- Create: `tests/test_photo_auth_roi_review.py`

**Interfaces:**
- Produces: `select_candidate_a(...)`, `select_candidate_b(...)`, `validate_review_response(...)`, `derive_candidate_a_decision(...)`, and `derive_candidate_b_decision(...)`.
- Consumes: frozen baseline observations, geometry candidates, and structured qwen review output.

- [ ] **Step 1: Write RED policy tests**

```python
def test_candidate_a_accepts_g1_pair_g2_g3_or_two_independent_signals():
    assert select_candidate_a([candidate("G1", "high")]).triggered
    assert select_candidate_a([paired_g2()]).triggered
    assert select_candidate_a([candidate("G3", "high")]).triggered
    assert not select_candidate_a([candidate("G2", "low")]).triggered


def test_candidate_b_is_strictly_baseline_gated():
    assert select_candidate_b(baseline(rule="R10_PRODUCT_SCREEN_LOCAL_MOIRE_EXEMPT"), [candidate("G1")]).triggered
    assert select_candidate_b(baseline(rule="R9", owner="none"), [paired_g2()]).triggered
    assert not select_candidate_b(baseline(rule="R9", owner="product_screen"), [paired_g2()]).triggered


def test_model_cannot_upgrade_candidate_b_without_frozen_evidence_combination():
    observation = valid_observation(pattern="full_edge_band", content_terminates_at_boundary=False)
    assert derive_candidate_b_decision(observation) == "no_evidence"


def test_candidate_a_requires_valid_montage_evidence_reference():
    observation = valid_observation(
        outermost_layer="external_display", evidence_refs=("missing-box",)
    )
    with pytest.raises(ValueError, match="evidence_refs"):
        derive_candidate_a_decision(observation, montage_manifest=valid_montage_manifest())


def test_candidate_outputs_never_include_automatic_rejection():
    assert set(allowed_shadow_decisions()) == {"manual_review", "no_evidence"}
```

- [ ] **Step 2: Run policy tests and verify RED**

Run: `python -m pytest -q tests/test_photo_auth_roi_review.py`

Expected: FAIL during collection because the review module does not exist.

- [ ] **Step 3: Implement exact response types and duplicate-safe validation**

```python
@dataclass(frozen=True)
class CarrierObservation:
    panel_id: str
    outermost_layer: Literal["direct_scene", "product_device", "external_display", "uncertain"]
    pattern: Literal[
        "full_edge_band", "opposite_edge_bands", "partial_edge_with_outer_optics",
        "central_nested_frame", "none", "uncertain",
    ]
    visible_sides: tuple[str, ...]
    content_terminates_at_boundary: bool
    outer_plane_optics: bool
    evidence_refs: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class CarrierReviewBatch:
    observations: tuple[CarrierObservation, ...]
```

Reject unknown fields, duplicate panel IDs, missing triggered-panel coverage, invalid side names, reasons over 120 characters, and evidence references absent from the frozen montage manifest. The model never supplies the decision. Local A policy emits `manual_review` only for confirmed `external_display` with a valid candidate-region reference, or `uncertain` plus G1, paired G2, G3, or two independent deterministic signals. Local B policy emits `manual_review` only for its four frozen evidence combinations; an `uncertain` observation is also frozen to `manual_review`. Every other observation maps to `no_evidence`. The final allowed set contains no automatic-rejection value.

- [ ] **Step 4: Write the two complete prompts**

Both prompts require visible evidence only, judge the outermost carrier before product screen ownership, distinguish product device borders from an external display, cite normalized candidate regions through `evidence_refs`, and return the exact observation-only JSON schema. Candidate A receives G1/G2/G3 metadata; Candidate B receives only its four frozen pattern combinations. Neither prompt contains a decision field or mentions labels, known orders, source folders, or expected answers.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest -q tests/test_photo_auth_roi_review.py`

Expected: PASS.

```powershell
git add prompts/photo_auth_roi_reviewer_a.txt prompts/photo_auth_roi_reviewer_b.txt tools/photo_auth_roi_review.py tests/test_photo_auth_roi_review.py
git commit -m "feat: define shadow ROI review policies"
```

### Task 4: Shared Baseline and Paired A/B Shadow Runner

**Files:**
- Create: `tools/run_photo_auth_roi_ab.py`
- Create: `tests/test_run_photo_auth_roi_ab.py`

**Interfaces:**
- Produces: one shared baseline JSONL, one opaque-arm JSONL per run, a shared `run_manifest.json`, and an encrypted/sealed arm mapping unavailable to the neutral scorer.
- Consumes: anonymous runtime manifest, geometry config, prompts, frozen request config, `tools.run_guobu_model_audit_v2.call_model_with_retry`, and API environment already present in the caller process. It never consumes the label key.

- [ ] **Step 1: Write RED tests for model/config preservation and paired calls**

```python
def test_runner_requires_explicit_qwen_and_preserves_model_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MODEL_NAME", "caller-value")
    with pytest.raises(SystemExit):
        main(["--model", "other-model", "--bundle", "fixture_bundle", "--out-dir", str(tmp_path)])
    assert os.environ["VISION_MODEL_NAME"] == "caller-value"


def test_candidates_share_one_baseline_and_only_triggered_orders_call_review(monkeypatch):
    calls = install_fake_model(monkeypatch)
    result = run_fixture(items=two_items_one_triggered(), candidates=("a", "b"))
    assert calls.baseline_order_ids == ["anon-1", "anon-2"]
    assert calls.a_review_order_ids == ["anon-1"]
    assert calls.b_review_order_ids == ["anon-1"]
    assert result.candidates["a"]["anon-2"].decision == "baseline"


def test_byte_hash_cache_rejects_same_path_with_changed_content(tmp_path):
    image = tmp_path / "sha256.bin"
    image.write_bytes(b"first")
    key_1 = experiment_cache_key(frozen_request(), [image])
    image.write_bytes(b"second")
    key_2 = experiment_cache_key(frozen_request(), [image])
    assert key_1 != key_2


def test_arm_budgets_and_randomized_order_are_independent():
    schedule = build_balanced_schedule(["anon-1", "anon-2"], arm_ids=("x7", "q2"), seed=20260722)
    assert schedule == build_balanced_schedule(["anon-1", "anon-2"], arm_ids=("x7", "q2"), seed=20260722)
    assert gate_state_for("x7") is not gate_state_for("q2")
```

- [ ] **Step 2: Run runner tests and verify RED**

Run: `python -m pytest -q tests/test_run_photo_auth_roi_ab.py`

Expected: FAIL during collection because the runner does not exist.

- [ ] **Step 3: Implement immutable run configuration and preflight**

```python
@dataclass(frozen=True)
class RunConfig:
    model: Literal["qwen3.7-plus"]
    arm_ids: tuple[str, str]
    mode: Literal["off", "shadow"]
    baseline_mode: Literal["frozen_production_record", "isolated_authenticity"]
    audit_mode: Literal["hybrid"] = "hybrid"
    max_trigger_rate: float = 0.10
    max_token_increase: float = 0.03
    max_mean_latency_increase: float = 0.05
    max_review_p95_sec: float = 15.0
    schedule_seed: int = 20260722
    workers: int = 1


@dataclass(frozen=True)
class RequestConfig:
    model: Literal["qwen3.7-plus"] = "qwen3.7-plus"
    response_format: Literal["json_object"] = "json_object"
    enable_thinking: Literal[False] = False
    baseline_detail: Literal["low"] = "low"
    montage_detail: Literal["high"] = "high"
    retry_count: Literal[1] = 1
    timeout_sec: float = production.MODEL_TIMEOUT_SEC
    retry_timeout_sec: float = production.MODEL_RETRY_TIMEOUT_SEC
```

`baseline_detail=low` reproduces the image-only baseline input, while `montage_detail=high` is the common ROI evidence transport used identically by both arms. It is an intentional input representation change, not a change to the model or decoding variables: model remains `qwen3.7-plus`, `enable_thinking=false`, and temperature/top-p/max-tokens remain omitted exactly as in the existing client.

Preflight checks API endpoint/key presence without printing values, refuses existing completed run directories, recursively rejects label/source fields, verifies all artifact hashes, records the production addendum and client-code SHA-256, and records but does not mutate the frozen environment settings. Development orders may consume frozen production records extracted from the completed full-payload audit. Image-only library records use `isolated_authenticity`, append the exact production addendum to a frozen output-only wrapper, and are explicitly recorded as `production_equivalent=false`; no report from that cohort may recommend production rollout.
The experiment CLI rejects `enforce`; production enablement is outside this plan.

- [ ] **Step 4: Reuse the existing model client**

Build the exact normalized request first, including model, prompt, payload, `response_format`, effective `enable_thinking`, detail, timeout, retry count, image-byte SHA-256, and production client-code SHA-256. Hash it for the experiment-local cache, then call `production.call_model_with_retry(...)` with `cache_dir=None`, explicit `model="qwen3.7-plus"`, opaque stage names, and the frozen details above. The exact baseline and arm prompts are written to the run manifest before calls begin. Cache hits are accepted only after all normalized request fields and byte hashes match; the path-based production cache key is not used by this experiment.

- [ ] **Step 5: Implement shadow-only aggregation and stop gates**

```python
def merge_shadow_result(baseline: BaselineDecision, derived: str | None, error: str | None) -> ShadowDecision:
    if error or derived is None:
        return ShadowDecision("baseline", baseline.result, error=error)
    if baseline.result != "no_evidence":
        return ShadowDecision("baseline", baseline.result)
    if derived not in {"manual_review", "no_evidence"}:
        raise ValueError("candidate cannot automatically reject")
    return ShadowDecision("candidate", derived)
```

Complete the common baseline and both local selectors over the entire bundle before any candidate review call. Compute each arm's trigger rate once with the complete bundle denominator; an arm above 10% is ineligible and makes no paid candidate calls, while the other arm may continue. Use opaque arm IDs and a seeded balanced schedule so each eligible arm runs first equally often. Maintain token, latency, and error counters independently per arm. After at least 20 measured calls, stop only that arm's new calls when rolling candidate error rate exceeds 2%, measured total-token increase exceeds 3%, mean end-to-end latency increase exceeds 5%, or review p95 exceeds 15 seconds. Complete already-started calls, write `stopped_by_gate`, never rewrite prior rows, and mark the stopped arm hard-gate failed rather than scoring an incomplete sample as a winner.

- [ ] **Step 6: Add retry, schema, missing-image, and direct-script tests**

Verify one retry only, exact multi-image coverage with at most one review call per order per arm, baseline preservation after failure, independent arm gates, byte-hash cache invalidation, no API key in files/logs, `--help` execution, off-mode operation without importing/loading geometry or prompts, and no mutation of `VISION_MODEL_NAME` even when an exception escapes.

- [ ] **Step 7: Run tests and commit**

Run: `python -m pytest -q tests/test_run_photo_auth_roi_ab.py`

Expected: PASS.

```powershell
git add tools/run_photo_auth_roi_ab.py tests/test_run_photo_auth_roi_ab.py
git commit -m "feat: add paired photo authenticity shadow runner"
```

### Task 5: Neutral Scoring and XLSX Report

**Files:**
- Create: `configs/photo_auth_roi_ab_scorecard_v1.json`
- Create: `tools/photo_auth_roi_ab_report.py`
- Create: `tests/test_photo_auth_roi_ab_report.py`

**Interfaces:**
- Produces: `comparison.json`, `comparison.xlsx`, `final_scorecard.json`, and a blinded neutral-review package from completed JSONL files.
- Consumes: frozen labels only after baseline and both opaque-arm result files are complete and hashed, plus the sealed arm mapping only after neutral scores are locked.

- [ ] **Step 1: Write RED tests for order-level metrics and scoring**

```python
def test_report_scores_orders_not_images_and_pairs_against_baseline(tmp_path):
    report = build_report(fixture_results(one_multi_image_positive=True, one_real=True))
    assert report["baseline"]["order_count"] == 2
    assert report["arm_x7"]["safe_interception"]["denominator"] == 1
    assert report["arm_x7"]["real_new_manual"]["denominator"] == 1


def test_no_winner_when_score_gap_is_below_three():
    assert choose_winner({"a": 81.0, "b": 82.5}) == "no_clear_winner"


def test_scorecard_boundary_values_are_frozen():
    config = load_scorecard_config(Path("configs/photo_auth_roi_ab_scorecard_v1.json"))
    assert score(metrics_at_all_targets(), config)["total"] == 100.0
    assert score(metrics_at_all_fail_limits(), config)["eligible"] is False


def test_report_refuses_unsealed_or_early_labels(tmp_path):
    with pytest.raises(ValueError, match="result hashes must be frozen"):
        build_report(results=unfinished_results(), label_key=tmp_path / "label_key.json")
```

- [ ] **Step 2: Run report tests and verify RED**

Run: `python -m pytest -q tests/test_photo_auth_roi_ab_report.py`

Expected: FAIL during collection because the report module does not exist.

- [ ] **Step 3: Implement metrics, intervals, and frozen 35/35/15/15 scorecard**

Commit `photo_auth_roi_ab_scorecard_v1.json` before paid calls with schema version, weights, formulas, targets, fail limits, bootstrap seed, tie rule, and hard-gate override. Compute Wilson intervals, paired result tables, McNemar exact counts, bootstrap score intervals with seed 20260722, token/latency deltas, trigger/error rates, three-run consistency, and the frozen hard gates. Unknown or unmeasured fields score zero and are explicitly labeled `not_measured`.

The original neutral-judge rubric is preserved: Risk contains decision harm, failure safety, anti-patch/generalization, shadow/rollback, and audit/data security; Effect contains development acceptance, blind safe interception, real-order safety, stratified robustness, and stability; Time contains implementation time, online p95 latency, and added human time; Cost contains API/compute, human workload, and maintenance. The machine formulas are:

```text
risk_decision_harm = 6*I(auto_rejects=0) + 6*clamp(1-real_new_manual_rate/0.05)
risk_failure_safety = 3*clamp(1-error_rate/0.01) + 2*I(errors_preserve_baseline) + 2*I(independent_arm_gates)
risk_generalization = 3*I(no_identity_or_fixed_dimension_features) + 2*I(split_components_disjoint) + 2*I(oot_verified)
risk_shadow_rollback = 3*I(rollback_equal) + 3*I(production_files_unchanged)
risk_audit_security = 3*I(sealed_label_canary_verified)
risk = risk_decision_harm + risk_failure_safety + risk_generalization + risk_shadow_rollback + risk_audit_security

effect_development = 7*minimum_development_positive_recall
effect_blind = 13*clamp(recall_improvement/0.10)
effect_real_safety = 8*clamp(1-real_new_manual_rate/0.05)
effect_stratified = 4*clamp(minimum_stratum_recall_improvement/0.10)
effect_stability = 3*clamp(consistency/0.98)
effect = effect_development + effect_blind + effect_real_safety + effect_stratified + effect_stability

arm_engineering_hours = shared_engineering_hours/2 + arm_specific_engineering_hours
time = 6*clamp(1-arm_engineering_hours/48) + 5*clamp(1-review_p95_sec/15) + 4*clamp(1-new_manual_minutes_per_1000/40.91)

cost = 7*clamp(1-token_increase/0.03) + 5*clamp(1-new_manual_minutes_per_1000/40.91) + 3*clamp(1-maintenance_hours_per_month/4)
```

Here `clamp(x)=min(1,max(0,x))`; every `I(...)` is a verified boolean gate, and unknown fields score zero with `not_measured`. All rates use the complete matched blind denominator; consistency below 98% is also a hard-gate failure. `new_manual_minutes_per_1000 = new_real_manual_orders_per_1000 * 60 / 73.3333333333`, reusing the project's existing human-throughput assumption. Engineering hours come from the task ledger, with shared pipeline work split equally and arm-specific work recorded separately. Maintenance is observed monthly support effort; before it is measured that three-point subscore is zero for both arms. API prices are read only when all three existing Qwen price environment variables are valid; `pricing_manifest.json` records their redacted numeric values and hash. Missing prices produce `not_measured` currency fields, never invented cost, while token workload remains scored. Trigger rate remains a hard gate rather than a second cost score. Any hard-gate failure sets `eligible=false` regardless of diagnostic total. A score gap below 3 points or materially overlapping paired confidence intervals yields `no_clear_winner`.

- [ ] **Step 4: Create an existing-style workbook without formulas**

Use openpyxl with Arial, frozen header rows, autofilters, restrained fills, and separate sheets `Scorecard`, `Metrics`, `Order Results`, and `Run Manifest`. Keep opaque arm IDs in the neutral workbook; reveal A/B mapping only in a separately sealed appendix after neutral scoring is committed. Write computed values so data-only readers are complete; scan every cell for formula-error strings.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest -q tests/test_photo_auth_roi_ab_report.py`

Expected: PASS.

```powershell
git add configs/photo_auth_roi_ab_scorecard_v1.json tools/photo_auth_roi_ab_report.py tests/test_photo_auth_roi_ab_report.py
git commit -m "feat: score photo authenticity AB results"
```

### Task 6: End-to-End Verification Before Paid Calls

**Files:**
- Modify only if tests expose defects: files created in Tasks 1-5
- Generate: `reports/photo_auth_roi_ab_20260722_dryrun/`

**Interfaces:**
- Verifies all experiment components without network access.

- [ ] **Step 1: Run focused and regression tests**

Run:

```powershell
python -m pytest -q tests/test_photo_auth_roi_ab_dataset.py tests/test_photo_auth_roi_geometry.py tests/test_photo_auth_roi_review.py tests/test_run_photo_auth_roi_ab.py tests/test_photo_auth_roi_ab_report.py tests/test_photo_authenticity_mainline.py
python -m py_compile tools/photo_auth_roi_ab_dataset.py tools/photo_auth_roi_geometry.py tools/photo_auth_roi_review.py tools/run_photo_auth_roi_ab.py tools/photo_auth_roi_ab_report.py
git diff --check
```

Expected: all tests pass, compilation exits zero, and `git diff --check` reports no errors.

- [ ] **Step 2: Build and inspect the dataset manifest**

Create `reports/photo_auth_roi_ab_20260722_dryrun/development.json` from the frozen combined report with this exact schema:

```json
{
  "source_report": "reports/model_audit/guobu1000_20260721_v1_fixed_run02_combined.json",
  "positive_order_ids": [
    "481172702361737769779288",
    "481173139331953737728049",
    "481173341410837793996882",
    "481173353533504628326480",
    "491169415245669236736019"
  ],
  "negative_order_ids": [
    "481173405563867555430460"
  ]
}
```

The dataset builder resolves every development image from the final attempt's `task.image_groups`, requires exact order-ID coverage, and stores anonymous development IDs in the output manifest.

Run:

```powershell
python tools/photo_auth_roi_ab_dataset.py --real-manifest "实拍图样本/manifest.csv" --non-real-root "非实拍样本/非实拍样本" --development-json "reports/photo_auth_roi_ab_20260722_dryrun/development.json" --reviewer-1-labels "reports/photo_auth_roi_ab_20260722_labels/reviewer_1.json" --reviewer-2-labels "reports/photo_auth_roi_ab_20260722_labels/reviewer_2.json" --adjudication-labels "reports/photo_auth_roi_ab_20260722_labels/adjudication.json" --near-duplicate-decisions "reports/photo_auth_roi_ab_20260722_labels/near_duplicate_decisions.json" --output-dir "reports/photo_auth_roi_ab_20260722_dryrun/bundles" --sealed-label-dir "C:/Users/HUAWEI/Desktop/photo_auth_roi_ab_20260722_sealed" --seed 20260722
```

Expected: 197 real groups and 154 non-real groups before deduplication; development, calibration, blind, and OOT near-duplicate components are disjoint; blind retains at least 100 real and 50 non-real groups. Each runtime bundle passes the forbidden-field/canary scan, while only the separately sealed label key contains labels, source IDs, provenance, and partition membership.

- [ ] **Step 3: Run a fake-client end-to-end dry run**

Run the runner with a test-only injected fake client fixture, generate the XLSX report, reopen it with `data_only=True`, and verify all required sheets and values. The production CLI must not expose a fake-client option.

- [ ] **Step 4: Review and commit any test-only fixes**

```powershell
git add tools/photo_auth_roi_*.py tools/run_photo_auth_roi_ab.py tests/test_photo_auth_roi_*.py tests/test_run_photo_auth_roi_ab.py
git commit -m "test: verify photo authenticity AB pipeline"
```

Skip the commit when there are no changes.

### Task 7: Paid Development Gate, Calibration, and Blind A/B

**Files:**
- Generate: `reports/photo_auth_roi_ab_20260722_dev/`
- Generate: `reports/photo_auth_roi_ab_20260722_calibration/`
- Generate: `reports/photo_auth_roi_ab_20260722_blind/`

**Interfaces:**
- Produces the actual paired evidence used by the neutral judge.

- [ ] **Step 1: Verify paid-run preflight without exposing secrets**

Confirm `VISION_API_BASE_URL` and `VISION_API_KEY` are present using boolean-only output. Record the current value or absence of `VISION_MODEL_NAME` as a hash/redacted presence marker and verify the runner does not change it. Confirm the exact runtime-bundle, prompt, geometry, client-code, normalized-request, opaque-arm mapping, scorecard, pricing, and label-key commitment hashes. The runner process receives no label/source path and the scorer cannot open results until a `RUN_COMPLETE` lock containing all result hashes exists.

- [ ] **Step 2: Run the six-order development gate three times**

Run each repetition with fresh candidate caches and the same shared baseline cache:

```powershell
python tools/run_photo_auth_roi_ab.py --bundle reports/photo_auth_roi_ab_20260722_dryrun/bundles/development --run-config reports/photo_auth_roi_ab_20260722_dryrun/frozen_run_config.json --model qwen3.7-plus --mode shadow --workers 1 --out-dir reports/photo_auth_roi_ab_20260722_dev/run01
```

Repeat as `run02` and `run03`. Stop before calibration unless each candidate catches at least four of five positives per run, the negative control directly passes all three runs, there are no automatic rejections, and rollback/failure tests pass.

- [ ] **Step 3: Calibrate geometry on calibration labels only**

Run local selectors without labels and freeze anonymous geometry features first. A separate calibration scorer then opens only the calibration portion of the label key. Choose one shared `GeometryConfig` for both arms using a candidate-independent objective: maximize raw geometry coverage on non-real calibration components subject to raw geometry trigger rate on real components no greater than 10%, with a frozen search space, search budget, and seed. Write the chosen config, calibration metrics, runtime-bundle hash, and config SHA-256. Do not alter thresholds, prompts, score formulas, or schedules after blind result generation begins.

- [ ] **Step 4: Freeze artifacts and run the blind split**

Generate opaque arm IDs and seal their A/B mapping. Run both arms in the frozen balanced randomized order with shared baseline results and independent gate counters. Do not mount or read the blind label key until baseline and both arm JSONL files are complete, hashed, and locked. An arm stopped by a gate is ineligible; the other arm continues.

- [ ] **Step 5: Run stability repetitions**

Run the frozen 30-order stability subset three times with fresh candidate caches. Require order-level decision consistency of at least 98% and candidate error rate below 1%.

- [ ] **Step 6: Generate the neutral scorecard and workbook**

```powershell
python tools/photo_auth_roi_ab_report.py --run-lock reports/photo_auth_roi_ab_20260722_blind/RUN_COMPLETE --label-key "C:/Users/HUAWEI/Desktop/photo_auth_roi_ab_20260722_sealed/label_key.json" --baseline reports/photo_auth_roi_ab_20260722_blind/baseline.jsonl --arm-result reports/photo_auth_roi_ab_20260722_blind/arm_x7.jsonl --arm-result reports/photo_auth_roi_ab_20260722_blind/arm_q2.jsonl --stability-root reports/photo_auth_roi_ab_20260722_blind/stability --score-config configs/photo_auth_roi_ab_scorecard_v1.json --output-dir reports/photo_auth_roi_ab_20260722_blind/final
```

Expected: report contains complete order counts, Wilson intervals, paired comparisons, risk/effect/time/cost scores, hard-gate status, and one of the three allowed recommendations.

- [ ] **Step 7: Perform rollback proof**

Run the runner in `off` mode against the blind runtime bundle without importing geometry/review modules or loading prompts/candidate caches. Inject corrupt candidate config and module exceptions, then byte-compare normalized decisions with the shared baseline. Record zero candidate calls and identical baseline decisions.

- [ ] **Step 8: Final independent review**

Provide only the opaque-arm scorecard, sanitized run manifest, and normalized error counters to the neutral reviewer. Freeze the neutral verdict and its hash before revealing the sealed A/B mapping or technical prompt/config attachments. No production files are changed regardless of the result.
