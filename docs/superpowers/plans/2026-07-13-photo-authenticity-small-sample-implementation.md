# Photo Authenticity Small-Sample Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在完全离线的影子模式中，建立可复现的小样本非实拍分类训练、正式与探索性隔离评估、ONNX 导出、三图 fail-closed 推理及增量重训流程。

**Architecture:** 所有新增实现封装在独立的 `photo_authenticity` Python 3.11 包中，训练侧使用 PyTorch/torchvision MobileNetV3-Large，运行侧仅使用 ONNX Runtime、NumPy、Pillow 和 OpenCV。数据清单是唯一事实源；模型、阈值和元数据作为哈希绑定的发布包一起验收，任何缺失、错配、异常或超时都只能返回 `manual_review`。

**Tech Stack:** Python 3.11 compatible syntax, PyTorch, torchvision, NumPy, Pillow, OpenCV, scikit-learn, ONNX, ONNX Runtime, pytest, standard-library `csv/json/hashlib/tomllib`.

## Global Constraints

- 本轮只创建或修改 `photo_authenticity/`、`tests/photo_authenticity/` 和本计划文件；不得修改根 `requirements.txt`、`modules/`、现有生产入口、生产配置、提示词或阈值。
- 不读取、格式化、清理或还原无关脏文件；不执行 `git add`、`git commit`、`git checkout`、`git reset` 或任何提交动作。
- 运行模式固定为 `offline_shadow`；公开状态只能是 `low_risk_candidate` 或 `manual_review`，不得产生正式通过或驳回。
- `S002`、`S034` 固定为 `unknown/excluded`；`S036` 固定为 `non_real/confirmed`。97 张其余实拍候选固定为 `real/weak_label`，可参与探索性训练，但不得进入正式验证、锁定测试或正式效果验收。
- 69 张唯一非实拍样本固定为 `non_real/confirmed`；按 SHA-256 和视觉相似度保守建立 `source_group`，凡非直接业务键或 SHA 相等得出的关系必须记录为 `inferred_visual` 并保留证据。
- 训练数据可为 `confirmed` 或 `weak_label`；任何使用了 `weak_label` 的指标、阈值或模型必须标记 `exploratory=true`，对外只能称“探索性结果”。正式评估只允许 `confirmed`。
- 正式锁定集至少需要 14 张 `non_real/confirmed` 和 20 张 `real/confirmed`；数量不足时正式验收状态必须为 `not_runnable_insufficient_confirmed_data`，不得用弱标签补足。
- 数据、日志、权重、ONNX 和报告仅保存在本机 `photo_authenticity/` 下；运行时不得调用网络 API。ImageNet 权重只允许由后续 DevOps 阶段从 torchvision 官方地址下载并缓存。
- 当前机器只有 Python 3.13 且没有 `py` 启动器。本计划中的代码保持 Python 3.11 兼容，环境创建和依赖安装由后续 DevOps 阶段完成；实现阶段不得修改当前 3.13 环境。
- 后续命令统一从仓库根目录执行，并显式设置 `$env:PA_PYTHON='C:\Users\HUAWEI\Desktop\audit_robot\.venv-photo-auth\Scripts\python.exe'`；不得使用 `python` 或 `py` 的隐式解析。
- 所有随机过程使用配置中的固定种子 `20260713`；同一 `source_group` 永远不得跨训练、验证、锁定测试或挑战集。
- FFT、边缘均匀度和清晰度只作为报告诊断字段，不参与模型输入、阈值或裁决。
- 模型、阈值、预处理契约、manifest 哈希和日志 schema 任一缺失或错配，均 fail closed 到 `manual_review`，并记录稳定的 `reason_code`。

---

## Planned File Map

```text
photo_authenticity/
  .gitignore                         # 排除本地输入、缓存、日志、权重、ONNX 和生成报告
  README.md                          # 离线影子运行手册和限制
  pyproject.toml                     # 包元数据、pytest 配置、Python >=3.11,<3.14
  requirements-train.txt             # 训练/导出依赖，独立于根 requirements.txt
  requirements-runtime.txt           # ONNX 离线推理最小依赖
  requirements-dev.txt               # pytest 与静态检查依赖
  configs/base.toml                  # 随机种子、预处理、训练、阈值、超时配置
  configs/paths.example.toml         # 本地输入与产物目录契约，不含真实数据
  data/README.md                     # staging、manifest 版本和数据不出机规则
  models/README.md                   # 发布包组成及哈希验收规则
  reports/README.md                  # 报告分类和探索性声明规则
  scripts/check_environment.ps1      # 只接受显式 Python 3.11 路径
  src/photo_authenticity/__init__.py
  src/photo_authenticity/cli.py
  src/photo_authenticity/config.py
  src/photo_authenticity/contracts.py
  src/photo_authenticity/hashing.py
  src/photo_authenticity/manifest.py
  src/photo_authenticity/grouping.py
  src/photo_authenticity/splitting.py
  src/photo_authenticity/preprocessing.py
  src/photo_authenticity/dataset.py
  src/photo_authenticity/modeling.py
  src/photo_authenticity/training.py
  src/photo_authenticity/metrics.py
  src/photo_authenticity/thresholds.py
  src/photo_authenticity/reporting.py
  src/photo_authenticity/artifacts.py
  src/photo_authenticity/onnx_export.py
  src/photo_authenticity/inference.py
  src/photo_authenticity/benchmark.py
  src/photo_authenticity/incremental.py
tests/photo_authenticity/
  conftest.py
  test_environment.py
  test_manifest.py
  test_grouping.py
  test_splitting.py
  test_preprocessing.py
  test_training.py
  test_thresholds_reporting.py
  test_onnx_export.py
  test_inference.py
  test_benchmark.py
  test_incremental.py
  test_cli_smoke.py
```

Generated files are written only under ignored subdirectories `photo_authenticity/data/input/`, `data/manifests/`, `data/splits/`, `models/runs/`, `models/releases/`, `reports/generated/`, and `reports/logs/`. Tests create synthetic images under pytest's temporary directory; no test reads project production data.

## Task 1: Isolated Package, Configuration, and Environment Gate

**Files:**
- Create: `photo_authenticity/.gitignore`
- Create: `photo_authenticity/pyproject.toml`
- Create: `photo_authenticity/requirements-train.txt`
- Create: `photo_authenticity/requirements-runtime.txt`
- Create: `photo_authenticity/requirements-dev.txt`
- Create: `photo_authenticity/configs/base.toml`
- Create: `photo_authenticity/configs/paths.example.toml`
- Create: `photo_authenticity/scripts/check_environment.ps1`
- Create: `photo_authenticity/src/photo_authenticity/__init__.py`
- Create: `photo_authenticity/src/photo_authenticity/config.py`
- Create: `photo_authenticity/src/photo_authenticity/contracts.py`
- Create: `tests/photo_authenticity/conftest.py`
- Create: `tests/photo_authenticity/test_environment.py`

**Interfaces:**
- `load_config(path: Path) -> AppConfig`
- `check_environment(python_version: tuple[int, int, int], mode: str, network_enabled: bool) -> EnvironmentCheck`
- `Decision = Literal['low_risk_candidate', 'manual_review']`
- `RunMode = Literal['offline_shadow']`
- `ReasonCode` enum includes `NONE`, `ENVIRONMENT_INVALID`, `MODEL_MISSING`, `MODEL_HASH_MISMATCH`, `THRESHOLD_MISMATCH`, `PREPROCESS_FAILED`, `IMAGE_CORRUPT`, `INFERENCE_ERROR`, `TIMEOUT`, `SELF_TEST_FAILED`, `INPUT_COUNT_INVALID`, and `LOG_WRITE_FAILED`.

- [x] **Step 1: Write the failing environment tests**

```python
def test_environment_accepts_only_python_311_offline_shadow():
    ok = check_environment((3, 11, 9), "offline_shadow", False)
    wrong_python = check_environment((3, 13, 0), "offline_shadow", False)
    online = check_environment((3, 11, 9), "offline_shadow", True)
    assert ok.ok is True
    assert wrong_python.reason_code == ReasonCode.ENVIRONMENT_INVALID
    assert online.reason_code == ReasonCode.ENVIRONMENT_INVALID
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_environment.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'photo_authenticity'`.

- [x] **Step 3: Implement the minimal package and immutable configuration**

Define frozen dataclasses for `AppConfig`, preprocessing size/fill, training stages, threshold policy and runtime limits. `base.toml` must contain `mode='offline_shadow'`, `seed=20260713`, `image_size=224`, ImageNet mean/std, `low_risk_threshold`, `risk_threshold`, `max_order_seconds`, and `intra_op_threads`; reject any mode other than `offline_shadow`, thresholds outside `[0,1]`, or `low_risk_threshold >= risk_threshold`. The PowerShell script must take mandatory `-PythonExe`, resolve it, run `& $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"`, parse the output, require major/minor `3.11`, and never invoke `py`.

- [x] **Step 4: Run the focused test and package metadata check**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_environment.py -q`

Expected: `3 passed` covering accepted 3.11, rejected 3.13 and rejected network-enabled mode.

Run: `& $env:PA_PYTHON -m pip install --dry-run -e .\photo_authenticity --no-deps`

Expected: exit code 0 and package requirement reports `Python >=3.11,<3.14`; no root file changes.

## Task 2: Versioned Manifest and Approved Label Policy

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/hashing.py`
- Create: `photo_authenticity/src/photo_authenticity/manifest.py`
- Create: `tests/photo_authenticity/test_manifest.py`

**Interfaces:**
- `sha256_file(path: Path) -> str`
- `ManifestRow(sample_id, path, sha256, label, label_status, source_group, order_id, kind, split, source_group_basis, source_group_evidence, exclusion_reason)`
- `build_manifest(non_real_dir: Path, real_candidates_csv: Path, output_csv: Path) -> ManifestBuildResult`
- `validate_manifest(path: Path) -> ManifestValidationResult`
- CSV columns are exactly `sample_id,path,sha256,label,label_status,source_group,order_id,kind,split,source_group_basis,source_group_evidence,exclusion_reason`.

- [x] **Step 1: Write the failing approved-label test**

```python
def test_manifest_applies_approved_overrides(synthetic_sources, tmp_path):
    result = build_manifest(*synthetic_sources, tmp_path / "manifest-v1.csv")
    rows = {row.sample_id: row for row in result.rows}
    assert rows["S002"].label_status == "excluded"
    assert rows["S034"].label_status == "excluded"
    assert (rows["S036"].label, rows["S036"].label_status) == ("non_real", "confirmed")
    assert rows["S001"].label_status == "weak_label"
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_manifest.py::test_manifest_applies_approved_overrides -q`

Expected: FAIL with import error for `photo_authenticity.manifest`.

- [x] **Step 3: Implement manifest construction and validation**

Use `csv.DictReader/DictWriter`, resolved local paths and streaming SHA-256. Enforce labels `real|non_real|unknown`, statuses `confirmed|weak_label|excluded`, unique `sample_id`, valid 64-character lowercase SHA, and existing decodable files. Apply overrides before general rules: `S002/S034 -> unknown/excluded`, `S036 -> non_real/confirmed`, all other baseline candidates `real/weak_label`; unique files from the non-real source become `non_real/confirmed`. Exact SHA duplicates stay in the manifest for traceability but receive `excluded` plus `exclusion_reason=duplicate_sha:<canonical_sample_id>` and cannot train or evaluate.

- [x] **Step 4: Add corruption and count tests, then run green**

The synthetic full-count test must generate 69 unique non-real files and 100 candidate rows, then assert 69 source non-real confirmed records, exactly 97 `real/weak_label`, exactly two excluded approved IDs, and `S036` as confirmed non-real. A corrupt file test must assert `label_status=excluded` and `exclusion_reason=image_decode_failed`.

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_manifest.py -q`

Expected: all manifest tests PASS; invalid enum, duplicate ID, missing file and corrupt image are reported without silently admitting the row.

## Task 3: Conservative SHA and Visual `source_group` Clustering

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/grouping.py`
- Create: `tests/photo_authenticity/test_grouping.py`

**Interfaces:**
- `VisualFingerprint(dhash_hex: str, width: int, height: int, hsv_histogram: tuple[float, ...])`
- `fingerprint_image(path: Path) -> VisualFingerprint`
- `cluster_source_groups(rows: Sequence[ManifestRow], max_dhash_distance: int = 4, min_histogram_correlation: float = 0.98, max_aspect_delta: float = 0.01) -> GroupingResult`
- `GroupingResult.rows` returns updated rows; `GroupingResult.evidence` is JSON-serializable and records every accepted/rejected comparison.

- [x] **Step 1: Write the failing conservative-clustering test**

```python
def test_visual_relation_is_grouped_and_marked_as_inferred(visual_variants):
    result = cluster_source_groups(visual_variants)
    a, b, unrelated = result.rows
    assert a.source_group == b.source_group
    assert b.source_group_basis == "inferred_visual"
    assert "dhash_distance=" in b.source_group_evidence
    assert unrelated.source_group != a.source_group
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_grouping.py::test_visual_relation_is_grouped_and_marked_as_inferred -q`

Expected: FAIL with import error for `photo_authenticity.grouping`.

- [x] **Step 3: Implement deterministic conservative grouping**

Group first by non-empty business `order_id` (`source_group_basis=order_id`), then exact SHA (`exact_sha`), then visual inference only when all three conditions pass: dHash Hamming distance `<=4`, HSV histogram correlation `>=0.98`, and aspect-ratio delta `<=0.01`. Use complete-linkage admission: a candidate may join an inferred group only if it passes against every member, preventing transitive similarity chains. Stable group IDs are `"sg_" + sha256("\n".join(sorted(member_sha256))).hexdigest()[:16]`. Every visual member stores `inferred_visual` and metric evidence；不得把推断关系称为确认关系。

- [x] **Step 4: Test boundary, determinism and no-chain behavior**

Add tests for shuffled input producing identical IDs, distance 5 not grouping, correlation 0.97 not grouping, and A~B/B~C while A!~C yielding no three-member group.

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_grouping.py -q`

Expected: all grouping tests PASS and inferred relations remain explicitly marked.

## Task 4: Group-Isolated Exploration and Formal Splits

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/splitting.py`
- Create: `tests/photo_authenticity/test_splitting.py`

**Interfaces:**
- `create_splits(rows: Sequence[ManifestRow], seed: int = 20260713, folds: int = 5) -> SplitPlan`
- `SplitPlan.exploratory_folds`, `SplitPlan.formal_locked`, `SplitPlan.formal_status`, `SplitPlan.manifest_sha256`, `SplitPlan.exploratory`
- `assert_no_group_leakage(split_plan: SplitPlan) -> None`

- [x] **Step 1: Write the failing weak-label isolation test**

```python
def test_weak_labels_train_exploratorily_but_never_enter_formal_sets(manifest_rows):
    plan = create_splits(manifest_rows)
    assert any(row.label_status == "weak_label" for row in plan.exploratory_training_rows())
    assert all(row.label_status == "confirmed" for row in plan.formal_evaluation_rows())
    assert plan.exploratory is True
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_splitting.py::test_weak_labels_train_exploratorily_but_never_enter_formal_sets -q`

Expected: FAIL with import error for `photo_authenticity.splitting`.

- [x] **Step 3: Implement grouped fixed-seed splitting**

Use `StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=20260713)` for eligible exploratory development rows. Reserve formal locked rows before folds, selecting only confirmed labels by stable seeded group ordering. Require at least 14 confirmed non-real and 20 confirmed real for a runnable formal lock; otherwise set `formal_status=not_runnable_insufficient_confirmed_data`, emit no formal scores, and retain the exact shortage counts. Never split a group and never mutate an existing locked-set membership when an earlier split plan is supplied.

- [x] **Step 4: Run leakage, shortage and lock-stability tests**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_splitting.py -q`

Expected: all tests PASS; the approved initial data reports formal evaluation unavailable if 20 confirmed real samples do not exist, while five exploratory group folds remain reproducible.

## Task 5: Orientation-Safe Preprocessing and Symmetric Augmentation

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/preprocessing.py`
- Create: `photo_authenticity/src/photo_authenticity/dataset.py`
- Create: `tests/photo_authenticity/test_preprocessing.py`

**Interfaces:**
- `decode_rgb(path: Path) -> PIL.Image.Image`
- `letterbox_center(image: Image.Image, size: int, fill_rgb: tuple[int, int, int]) -> Image.Image`
- `build_eval_transform(config: PreprocessConfig) -> Callable`
- `build_train_transform(config: PreprocessConfig, seed: int) -> Callable`
- `ManifestDataset(rows, transform) -> Dataset[tuple[Tensor, int, str]]`

- [x] **Step 1: Write the failing shape and orientation test**

```python
def test_eval_preprocess_applies_exif_rgb_and_center_padding(oriented_jpeg):
    tensor = build_eval_transform(TEST_CONFIG)(decode_rgb(oriented_jpeg))
    assert tensor.shape == (3, 224, 224)
    assert tensor.dtype == torch.float32
    assert torch.isfinite(tensor).all()
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_preprocessing.py::test_eval_preprocess_applies_exif_rgb_and_center_padding -q`

Expected: FAIL with import error for `photo_authenticity.preprocessing`.

- [x] **Step 3: Implement one shared preprocessing contract**

Apply `ImageOps.exif_transpose`, convert to RGB, preserve aspect ratio, resize with bicubic interpolation and center-pad to 224 square before ImageNet normalization. Training augmentation is class-independent and limited to configured brightness/contrast, scale, perspective, Gaussian blur and in-memory JPEG recompression; validation/export/inference have no random augmentation. Raise typed `ImageDecodeError` on truncated, oversized or undecodable images.

- [x] **Step 4: Verify deterministic evaluation and symmetric augmentation**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_preprocessing.py -q`

Expected: all tests PASS; repeated eval tensors are byte-identical, seeded train transforms reproduce, real and non-real rows use the same transform object, and corrupt files raise `ImageDecodeError`.

## Task 6: Two-Stage MobileNetV3-Large Training and Five-Fold CV

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/modeling.py`
- Create: `photo_authenticity/src/photo_authenticity/training.py`
- Create: `photo_authenticity/src/photo_authenticity/metrics.py`
- Create: `tests/photo_authenticity/test_training.py`

**Interfaces:**
- `build_model(weights: Literal['imagenet','none']) -> nn.Module`
- `set_training_stage(model: nn.Module, stage: Literal['head','tail']) -> TrainableSummary`
- `compute_class_weights(labels: Sequence[int]) -> Tensor`
- `train_fold(model, loaders, config, run_dir: Path) -> FoldResult`
- `cross_validate(split_plan: SplitPlan, config: AppConfig, run_dir: Path) -> CrossValidationResult`

- [x] **Step 1: Write the failing stage-freezing test**

```python
def test_training_stages_freeze_then_unfreeze_tail():
    model = build_model(weights="none")
    head = set_training_stage(model, "head")
    assert head.trainable_names and all(name.startswith("classifier") for name in head.trainable_names)
    tail = set_training_stage(model, "tail")
    assert any(name.startswith("features.16") for name in tail.trainable_names)
    assert tail.trainable_parameter_count > head.trainable_parameter_count
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_training.py::test_training_stages_freeze_then_unfreeze_tail -q`

Expected: FAIL with import error for `photo_authenticity.modeling`.

- [x] **Step 3: Implement the two-stage protocol**

Replace MobileNetV3-Large's classifier output with two logits ordered `[real, non_real]`. Stage 1 freezes `features`; stage 2 unfreezes only the configured last one or two feature blocks and uses the lower configured learning rate. Use class-weighted cross entropy derived from each fold's training labels, early stopping on validation non-real recall then validation loss, fixed seeds for Python/NumPy/PyTorch/DataLoader, CPU as the default device, and no synthetic moire samples. `weights='imagenet'` may use only torchvision's official `MobileNet_V3_Large_Weights.DEFAULT` cache/download path.

- [x] **Step 4: Persist reproducibility metadata and run synthetic training test**

Each run writes manifest path/hash, split hash, seed, complete config, Python/library versions, code Git HEAD when available plus `worktree_dirty` boolean, fold metrics, best checkpoint and `exploratory` flag. Tests use `weights='none'`, a tiny mocked backbone and synthetic tensors, so CI does not download weights.

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_training.py -q`

Expected: all tests PASS; class weights are inverse-frequency normalized, stage transitions are exact, and a two-epoch CPU smoke run writes all required metadata without network access.

## Task 7: Validation-Only Threshold Freeze, Formal Guard, and Reports

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/thresholds.py`
- Create: `photo_authenticity/src/photo_authenticity/reporting.py`
- Create: `tests/photo_authenticity/test_thresholds_reporting.py`

**Interfaces:**
- `select_thresholds(validation_predictions, policy) -> ThresholdSelection`
- `classify_score(score: float, thresholds: FrozenThresholds) -> Decision`
- `evaluate_predictions(predictions, scope: Literal['exploratory_cv','formal_locked','challenge']) -> EvaluationResult`
- `write_evaluation_report(result, output_json: Path, output_md: Path) -> ReportPaths`

- [x] **Step 1: Write the failing threshold and formal-guard test**

```python
def test_gray_zone_and_weak_label_formal_guard():
    frozen = FrozenThresholds(low_risk=0.20, risk=0.70, model_sha256="a" * 64)
    assert classify_score(0.10, frozen) == "low_risk_candidate"
    assert classify_score(0.50, frozen) == "manual_review"
    assert classify_score(0.90, frozen) == "manual_review"
    with pytest.raises(FormalEvaluationPolicyError):
        evaluate_predictions([weak_label_prediction()], scope="formal_locked")
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_thresholds_reporting.py::test_gray_zone_and_weak_label_formal_guard -q`

Expected: FAIL with import error for `photo_authenticity.thresholds`.

- [x] **Step 3: Implement conservative validation-only selection**

Select thresholds only from out-of-fold validation predictions, never locked/challenge data. Enumerate observed score cut points and choose the lowest-risk policy satisfying the configured minimum validation non-real recall; ties choose the lower `low_risk` threshold and then lower coverage. If weak labels contributed, freeze metadata with `exploratory=true` and title reports `探索性结果（含 weak_label，不得用于正式效果验收）`. A score in `[low_risk,1]`, a non-finite score, or missing threshold always maps to `manual_review`.

- [x] **Step 4: Implement honest metrics and report assertions**

Report confusion matrix, non-real recall, real-to-manual-review rate, balanced accuracy, group counts, label-status counts, bootstrap intervals when calculable, all missed confirmed non-real sample IDs, manifest/model/threshold hashes and scope. If no runnable formal lock exists, write `formal_status=not_runnable_insufficient_confirmed_data` and no fabricated formal metric. Even zero misses must include the statement “仅代表当前小型锁定集，不代表生产零漏放”.

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_thresholds_reporting.py -q`

Expected: all tests PASS; weak labels cannot cross the formal guard and every miss appears in JSON and Markdown.

## Task 8: Hash-Bound Release Bundle and ONNX Equivalence

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/artifacts.py`
- Create: `photo_authenticity/src/photo_authenticity/onnx_export.py`
- Create: `tests/photo_authenticity/test_onnx_export.py`

**Interfaces:**
- `export_onnx(checkpoint: Path, output: Path, preprocess: PreprocessConfig, opset: int = 17) -> ExportResult`
- `verify_onnx_equivalence(torch_model, onnx_path: Path, tensors: Sequence[Tensor], atol: float = 1e-5, rtol: float = 1e-4) -> EquivalenceResult`
- `build_release_bundle(model_path, thresholds_path, metadata_path, release_dir) -> ReleaseManifest`
- `verify_release_bundle(release_dir: Path) -> BundleVerification`

- [x] **Step 1: Write the failing tamper-detection test**

```python
def test_release_bundle_rejects_model_threshold_hash_mismatch(valid_bundle):
    valid_bundle.model_path.write_bytes(valid_bundle.model_path.read_bytes() + b"tamper")
    result = verify_release_bundle(valid_bundle.root)
    assert result.ok is False
    assert result.reason_code == ReasonCode.MODEL_HASH_MISMATCH
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_onnx_export.py::test_release_bundle_rejects_model_threshold_hash_mismatch -q`

Expected: FAIL with import error for `photo_authenticity.artifacts`.

- [x] **Step 3: Implement deterministic export and bundle contract**

Export batch-dynamic NCHW float32 input and two-logit output at opset 17, calculate `non_real_risk=softmax(logits)[1]` consistently in both runtimes, run `onnx.checker`, then compare PyTorch and CPU ONNX Runtime over fixed synthetic and held-out validation tensors. Release `release.json` binds model SHA-256, threshold-file SHA-256, metadata SHA-256, manifest SHA-256, preprocessing contract hash, model version, output order, mode and exploratory status.

- [x] **Step 4: Run equivalence, corruption and metadata mismatch tests**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_onnx_export.py -q`

Expected: all tests PASS; max absolute/relative differences meet tolerance, corrupt ONNX fails verification, and swapped thresholds fail before inference.

## Task 9: Fail-Closed Single-Image and Hard-Timeout Three-Image Inference

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/inference.py`
- Create: `tests/photo_authenticity/test_inference.py`

**Interfaces:**
- `ShadowPredictor.start(release_dir: Path, intra_op_threads: int) -> StartupResult`
- `ShadowPredictor.predict_image(path: Path) -> ImageDecision`
- `predict_order_isolated(release_dir: Path, image_paths: Sequence[Path], timeout_seconds: float, log_path: Path) -> OrderDecision`
- `ImageDecision(decision, score, elapsed_ms, reason_code, model_version)`
- `OrderDecision(decision, images, elapsed_ms, reason_code, mode='offline_shadow')`

- [x] **Step 1: Write the failing fail-closed matrix test**

```python
@pytest.mark.parametrize("fault,reason", [
    ("missing_model", ReasonCode.MODEL_MISSING),
    ("corrupt_image", ReasonCode.IMAGE_CORRUPT),
    ("threshold_mismatch", ReasonCode.THRESHOLD_MISMATCH),
    ("runtime_error", ReasonCode.INFERENCE_ERROR),
    ("timeout", ReasonCode.TIMEOUT),
])
def test_every_fault_returns_manual_review(fault, reason, inference_fault_case):
    result = inference_fault_case(fault)
    assert result.decision == "manual_review"
    assert result.reason_code == reason
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_inference.py::test_every_fault_returns_manual_review -q`

Expected: FAIL with import error for `photo_authenticity.inference`.

- [x] **Step 3: Implement startup self-test and isolated order worker**

Startup verifies the complete release bundle, creates ONNX Runtime with configured CPU thread limits, and runs a bundled deterministic self-test tensor whose score must fall in the metadata tolerance. Three-image inference requires exactly three paths and runs in a spawned child process; parent enforces a hard deadline, terminates and joins the child on timeout, and returns `manual_review/TIMEOUT`. Any child crash, malformed payload, preprocessing error, non-finite score, model/threshold/log schema mismatch or exception returns `manual_review` without exposing a low-risk result.

- [x] **Step 4: Implement three-image aggregation and structured JSONL log**

Only three successful image decisions below the frozen low-risk threshold produce order `low_risk_candidate`; any other image decision makes the whole order `manual_review`. Log one local JSONL record with timestamp, schema version, `offline_shadow`, release/model/threshold hashes, three per-image scores or nulls, order decision, elapsed time and stable reason code. Logging failure itself must change the returned result to `manual_review/LOG_WRITE_FAILED`.

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_inference.py -q`

Expected: all tests PASS, including missing/corrupt model, corrupt image, startup self-test failure, threshold mismatch, one risky image, timeout, child crash, log failure and three valid low-risk images.

## Task 10: CPU Latency Benchmark and Diagnostic Reporting

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/benchmark.py`
- Create: `tests/photo_authenticity/test_benchmark.py`

**Interfaces:**
- `benchmark_orders(predictor_factory, orders, warmup: int, repetitions: int) -> BenchmarkResult`
- `BenchmarkResult(p50_ms, p95_ms, max_ms, successful_runs, manual_review_runs, environment, release_sha256)`
- `compute_diagnostics(path: Path) -> DiagnosticFeatures`

- [x] **Step 1: Write the failing percentile test**

```python
def test_benchmark_reports_three_image_p50_p95_and_max(fake_clock_predictor):
    result = benchmark_orders(fake_clock_predictor, THREE_IMAGE_ORDERS, warmup=1, repetitions=5)
    assert result.p50_ms == 30.0
    assert result.p95_ms == 48.0
    assert result.max_ms == 50.0
    assert result.successful_runs == 5
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_benchmark.py::test_benchmark_reports_three_image_p50_p95_and_max -q`

Expected: FAIL with import error for `photo_authenticity.benchmark`.

- [x] **Step 3: Implement measured order benchmark and diagnostics**

Warm up outside measured runs, measure complete three-image order calls with `perf_counter_ns`, use NumPy's documented linear percentile method, retain failures in counts, and report P50/P95/max only from completed measured calls. Record CPU/platform, thread count, Python and ONNX Runtime versions, release hash, warmup and repetition counts. Compute FFT energy ratio, edge uniformity and Laplacian sharpness only for diagnostics output; no diagnostic value may alter `decision`.

- [x] **Step 4: Verify percentile math and diagnostics-only behavior**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_benchmark.py -q`

Expected: all tests PASS; exact synthetic percentiles match, failures are counted, and changing diagnostics never changes predictor decisions.

## Task 11: Incremental Manifest, Immutable Challenge Sets, and Promotion Gate

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/incremental.py`
- Create: `tests/photo_authenticity/test_incremental.py`

**Interfaces:**
- `append_confirmed_samples(previous_manifest: Path, additions_csv: Path, output_manifest: Path) -> IncrementalManifestResult`
- `compare_releases(old_report, new_report, policy) -> PromotionDecision`
- `PromotionDecision(eligible_for_shadow_replacement, reasons, old_release, new_release)`

- [x] **Step 1: Write the failing immutable-challenge test**

```python
def test_incremental_update_preserves_old_locked_and_challenge_memberships(previous, additions):
    result = append_confirmed_samples(previous, additions, NEW_MANIFEST)
    before = {(r.sample_id, r.split) for r in previous.rows if r.split in {"locked", "challenge"}}
    after = {(r.sample_id, r.split) for r in result.rows if r.sample_id in {x[0] for x in before}}
    assert after == before
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_incremental.py::test_incremental_update_preserves_old_locked_and_challenge_memberships -q`

Expected: FAIL with import error for `photo_authenticity.incremental`.

- [x] **Step 3: Implement append-only data evolution**

Require every addition to have a computed SHA, explicit source group or marked inferred grouping evidence, and human-confirmed label before it can enter formal data. Never edit old manifest bytes; write a new version with parent-manifest hash. Preserve every old locked/challenge member and assign new device or capture-condition groups to a new versioned challenge split. Retraining always starts from official ImageNet initialization under the same two-stage protocol, not indefinite continuation from the previous task checkpoint.

- [x] **Step 4: Implement conservative shadow replacement comparison**

Compare old/new releases on identical old locked and all accumulated challenge sets. Eligibility requires no decrease in confirmed non-real recall, no new missed confirmed non-real IDs, real-to-manual-review rate within configured ceiling, valid bundle/equivalence checks, and CPU P95/max within limits. Any missing metric, changed evaluation population, weak-label-only comparison or hash mismatch returns ineligible.

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_incremental.py -q`

Expected: all tests PASS; regression, missing old challenge results and weak-only evidence each block replacement.

## Task 12: CLI, Operating Documentation, and Offline End-to-End Smoke Test

**Files:**
- Create: `photo_authenticity/src/photo_authenticity/cli.py`
- Create: `photo_authenticity/README.md`
- Create: `photo_authenticity/data/README.md`
- Create: `photo_authenticity/models/README.md`
- Create: `photo_authenticity/reports/README.md`
- Create: `tests/photo_authenticity/test_cli_smoke.py`

**Interfaces:**
- `main(argv: Sequence[str] | None = None) -> int`
- Subcommands: `check-env`, `build-manifest`, `group-sources`, `split`, `train`, `freeze-thresholds`, `evaluate`, `export-onnx`, `verify-release`, `infer-image`, `infer-order`, `benchmark`, `append-samples`, `compare-releases`.
- Every command writes machine-readable JSON to stdout, operational logs to local JSONL, uses exit `0` for completed operation, `2` for validation/config error, and `3` for fail-closed inference that completed with `manual_review`.

- [x] **Step 1: Write the failing offline CLI smoke test**

```python
def test_infer_order_cli_is_offline_and_fail_closed(cli_runner, valid_release, three_images, deny_network):
    result = cli_runner(["infer-order", "--release", str(valid_release), *map(str, three_images)])
    payload = json.loads(result.stdout)
    assert payload["mode"] == "offline_shadow"
    assert payload["decision"] in {"low_risk_candidate", "manual_review"}
    assert "approved" not in result.stdout.lower()
    assert deny_network.calls == []
```

- [x] **Step 2: Run the focused test and confirm red**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity/test_cli_smoke.py::test_infer_order_cli_is_offline_and_fail_closed -q`

Expected: FAIL because the CLI module/subcommand does not exist.

- [x] **Step 3: Implement CLI wiring with no production imports**

Wire each subcommand only to modules inside `photo_authenticity`. Before command execution, verify mode and config; inference commands catch all domain and unexpected exceptions at the CLI boundary and emit `manual_review` JSON. Tests inspect imported module paths and fail if CLI imports `modules`, a root production entry, HTTP clients, cloud SDKs or subprocesses other than the isolated local inference worker.

- [x] **Step 4: Write the exact operator runbook**

Document: DevOps-owned Python 3.11 environment creation; explicit `$env:PA_PYTHON`; official ImageNet cache/download allowance; offline reruns using cached weights; staging contract for `data/input/non_real/` and `data/input/real_candidates.csv`; command sequence; generated file locations; formal-vs-exploratory wording; model/threshold/hash verification; timeout and reason-code interpretation; CPU benchmark procedure; incremental retraining; rollback by selecting the previous verified shadow release; and the prohibition on production integration. State clearly that current Python 3.13 is not used and installation is deferred to DevOps.

The documented execution sequence must be exact:

```powershell
$env:PA_PYTHON='C:\Users\HUAWEI\Desktop\audit_robot\.venv-photo-auth\Scripts\python.exe'
& $env:PA_PYTHON -m photo_authenticity.cli check-env --config .\photo_authenticity\configs\base.toml
& $env:PA_PYTHON -m photo_authenticity.cli build-manifest --non-real-dir .\photo_authenticity\data\input\non_real --real-candidates .\photo_authenticity\data\input\real_candidates.csv --output .\photo_authenticity\data\manifests\manifest-v1.csv
& $env:PA_PYTHON -m photo_authenticity.cli group-sources --manifest .\photo_authenticity\data\manifests\manifest-v1.csv --output .\photo_authenticity\data\manifests\manifest-v1-grouped.csv --evidence .\photo_authenticity\reports\generated\grouping-v1.json
& $env:PA_PYTHON -m photo_authenticity.cli split --manifest .\photo_authenticity\data\manifests\manifest-v1-grouped.csv --output .\photo_authenticity\data\splits\split-v1.json
& $env:PA_PYTHON -m photo_authenticity.cli train --config .\photo_authenticity\configs\base.toml --split .\photo_authenticity\data\splits\split-v1.json --run-dir .\photo_authenticity\models\runs\run-v1
& $env:PA_PYTHON -m photo_authenticity.cli freeze-thresholds --predictions .\photo_authenticity\models\runs\run-v1\oof-predictions.csv --output .\photo_authenticity\models\runs\run-v1\thresholds.json
& $env:PA_PYTHON -m photo_authenticity.cli evaluate --run-dir .\photo_authenticity\models\runs\run-v1 --split .\photo_authenticity\data\splits\split-v1.json --output-dir .\photo_authenticity\reports\generated\run-v1
& $env:PA_PYTHON -m photo_authenticity.cli export-onnx --run-dir .\photo_authenticity\models\runs\run-v1 --release-dir .\photo_authenticity\models\releases\release-v1
& $env:PA_PYTHON -m photo_authenticity.cli verify-release --release .\photo_authenticity\models\releases\release-v1
& $env:PA_PYTHON -m photo_authenticity.cli infer-order --release .\photo_authenticity\models\releases\release-v1 .\photo_authenticity\data\input\shadow_order\1.jpg .\photo_authenticity\data\input\shadow_order\2.jpg .\photo_authenticity\data\input\shadow_order\3.jpg
& $env:PA_PYTHON -m photo_authenticity.cli benchmark --release .\photo_authenticity\models\releases\release-v1 --orders .\photo_authenticity\data\input\benchmark_orders.csv --output .\photo_authenticity\reports\generated\benchmark-v1.json
```

Expected operational outcome: commands produce local versioned artifacts; initial results containing 97 weak real labels are visibly exploratory; formal evaluation remains unavailable until confirmed real minimum is met; inference never emits a production approval.

- [x] **Step 5: Run complete verification without reading unrelated files**

Run: `& $env:PA_PYTHON -m pytest tests/photo_authenticity -q`

Expected: all `tests/photo_authenticity` tests PASS; no tests outside this directory are collected.

Run: `& $env:PA_PYTHON -m compileall -q photo_authenticity/src tests/photo_authenticity`

Expected: exit code 0 under Python 3.11, proving syntax compatibility for the supported interpreter.

Run: `git status --short -- photo_authenticity tests/photo_authenticity docs/superpowers/plans/2026-07-13-photo-authenticity-small-sample-implementation.md`

Expected: only planned new files appear in the scoped status output. Do not run unscoped `git status`, do not inspect unrelated paths, and do not commit.

## Implementation Review Gates

After each task, review only that task's listed files and run only its focused tests. At the end, verify these gates:

1. Scope gate: the scoped Git status contains no path outside `photo_authenticity/`, `tests/photo_authenticity/` and this plan.
2. Safety gate: fault-injection tests prove every model, threshold, preprocessing, logging, timeout and startup mismatch returns `manual_review`.
3. Label gate: exactly 97 approved real candidates are `weak_label`; `S002/S034` are excluded; `S036` is confirmed non-real; weak labels never enter formal metrics.
4. Leakage gate: no `source_group` crosses any split, inferred grouping remains marked, and locked/challenge memberships are immutable.
5. Reproducibility gate: manifest, split, model, thresholds, preprocessing and release hashes are mutually bound and recorded with seed/config/version metadata.
6. Evidence gate: reports never imply production readiness, never hide a confirmed non-real miss, and label all weak-label outcomes as exploratory.
7. Offline gate: no network API or cloud inference exists; only the separately authorized official ImageNet weight acquisition can use network during DevOps provisioning.
8. Environment gate: implementation and verification use the explicit Python 3.11 executable; no `py` launcher and no current Python 3.13 environment mutation.

## Plan Self-Review Record

- Design coverage: manifest, conservative deduplication/grouping, grouped split, preprocessing, MobileNetV3 training, validation-only thresholds, ONNX export/equivalence, fail-closed three-image inference, reports, CPU benchmark, incremental retraining and operating documentation each have a dedicated TDD task.
- Approved overrides: the plan explicitly supersedes the original confirmed-only training rule by admitting 97 `real/weak_label` samples to exploratory training only; formal acceptance remains confirmed-only. `S002/S034/S036` handling is exact.
- Risk controls: `offline_shadow`, local-only artifacts, fail-closed behavior, model/threshold/log binding and exploration wording are executable test assertions rather than prose-only requirements.
- Environment: all commands use an explicit future Python 3.11 interpreter; DevOps owns installation; no command depends on `py` or mutates Python 3.13.
- Scope: no root dependency file, `modules/`, production entry or unrelated dirty file is included. The plan contains no commit step.
- Interface consistency: shared names `ManifestRow`, `SplitPlan`, `FrozenThresholds`, `ReasonCode`, `ReleaseManifest`, `ImageDecision` and `OrderDecision` are introduced once and consumed consistently by later tasks.
- Placeholder scan: no deferred implementation marker or unspecified error-handling instruction remains; commands, expected outcomes and fail-closed reason paths are explicit.
