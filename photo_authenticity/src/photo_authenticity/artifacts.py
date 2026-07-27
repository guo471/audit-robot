from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import onnx

from .config import PreprocessConfig
from .contracts import ReasonCode
from .hashing import sha256_file


@dataclass(frozen=True)
class ReleaseManifest:
    root: Path
    model_path: Path
    thresholds_path: Path
    metadata_path: Path
    release_path: Path
    model_sha256: str
    thresholds_sha256: str
    metadata_sha256: str
    manifest_sha256: str
    preprocessing_contract_hash: str
    model_version: str
    exploratory: bool


@dataclass(frozen=True)
class BundleVerification:
    ok: bool
    reason_code: ReasonCode
    errors: tuple[str, ...]
    release: ReleaseManifest | None = None


def preprocess_contract_hash(preprocess: PreprocessConfig) -> str:
    payload = json.dumps(
        asdict(preprocess), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def build_release_bundle(
    model_path: Path,
    thresholds_path: Path,
    metadata_path: Path,
    release_dir: Path,
) -> ReleaseManifest:
    source_model = model_path.resolve()
    source_thresholds = thresholds_path.resolve()
    source_metadata = metadata_path.resolve()
    onnx.checker.check_model(onnx.load(source_model))
    model_hash = sha256_file(source_model)
    thresholds = _load_json(source_thresholds)
    metadata = _load_json(source_metadata)
    if thresholds.get("model_sha256") != model_hash:
        raise ValueError("thresholds are not bound to the ONNX model hash")
    required_metadata = {
        "manifest_sha256",
        "preprocessing_contract_hash",
        "model_version",
        "output_order",
        "mode",
        "exploratory",
    }
    if not required_metadata.issubset(metadata):
        raise ValueError("release metadata is incomplete")
    if metadata["mode"] != "offline_shadow":
        raise ValueError("release mode must be offline_shadow")
    if metadata["output_order"] != ["real", "non_real"]:
        raise ValueError("model output order must be [real, non_real]")
    if thresholds.get("exploratory") != metadata["exploratory"]:
        raise ValueError("threshold and metadata exploratory flags differ")

    root = release_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    bundled_model = root / "model.onnx"
    bundled_thresholds = root / "thresholds.json"
    bundled_metadata = root / "metadata.json"
    shutil.copyfile(source_model, bundled_model)
    shutil.copyfile(source_thresholds, bundled_thresholds)
    shutil.copyfile(source_metadata, bundled_metadata)
    threshold_hash = sha256_file(bundled_thresholds)
    metadata_hash = sha256_file(bundled_metadata)
    release_payload = {
        "schema_version": 1,
        "model_file": bundled_model.name,
        "model_sha256": model_hash,
        "thresholds_file": bundled_thresholds.name,
        "thresholds_sha256": threshold_hash,
        "metadata_file": bundled_metadata.name,
        "metadata_sha256": metadata_hash,
        "manifest_sha256": metadata["manifest_sha256"],
        "preprocessing_contract_hash": metadata["preprocessing_contract_hash"],
        "model_version": metadata["model_version"],
        "output_order": metadata["output_order"],
        "mode": metadata["mode"],
        "exploratory": metadata["exploratory"],
    }
    release_path = root / "release.json"
    release_path.write_text(
        json.dumps(release_payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ReleaseManifest(
        root,
        bundled_model,
        bundled_thresholds,
        bundled_metadata,
        release_path,
        model_hash,
        threshold_hash,
        metadata_hash,
        str(metadata["manifest_sha256"]),
        str(metadata["preprocessing_contract_hash"]),
        str(metadata["model_version"]),
        bool(metadata["exploratory"]),
    )


def _failure(reason: ReasonCode, message: str) -> BundleVerification:
    return BundleVerification(False, reason, (message,))


def verify_release_bundle(release_dir: Path) -> BundleVerification:
    root = release_dir.resolve()
    release_path = root / "release.json"
    try:
        release_data = _load_json(release_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _failure(ReasonCode.MODEL_MISSING, f"release manifest unavailable: {exc}")
    model = root / str(release_data.get("model_file", "model.onnx"))
    thresholds = root / str(release_data.get("thresholds_file", "thresholds.json"))
    metadata = root / str(release_data.get("metadata_file", "metadata.json"))
    if not model.is_file():
        return _failure(ReasonCode.MODEL_MISSING, "release model is missing")
    if sha256_file(model) != release_data.get("model_sha256"):
        return _failure(ReasonCode.MODEL_HASH_MISMATCH, "release model hash mismatch")
    if not thresholds.is_file() or sha256_file(thresholds) != release_data.get("thresholds_sha256"):
        return _failure(ReasonCode.THRESHOLD_MISMATCH, "threshold file missing or hash mismatch")
    if not metadata.is_file() or sha256_file(metadata) != release_data.get("metadata_sha256"):
        return _failure(ReasonCode.THRESHOLD_MISMATCH, "metadata file missing or hash mismatch")
    try:
        onnx.checker.check_model(onnx.load(model))
    except Exception as exc:
        return _failure(ReasonCode.MODEL_HASH_MISMATCH, f"invalid ONNX model: {exc}")
    try:
        threshold_data = _load_json(thresholds)
        metadata_data = _load_json(metadata)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _failure(ReasonCode.THRESHOLD_MISMATCH, f"invalid release JSON: {exc}")
    model_hash = str(release_data["model_sha256"])
    semantic_checks = (
        (threshold_data.get("model_sha256") == model_hash, "threshold model binding mismatch"),
        (threshold_data.get("selection_scope") == "oof_validation", "threshold scope mismatch"),
        (release_data.get("mode") == metadata_data.get("mode") == "offline_shadow", "mode mismatch"),
        (
            release_data.get("output_order")
            == metadata_data.get("output_order")
            == ["real", "non_real"],
            "output order mismatch",
        ),
        (
            release_data.get("manifest_sha256") == metadata_data.get("manifest_sha256"),
            "manifest hash mismatch",
        ),
        (
            release_data.get("preprocessing_contract_hash")
            == metadata_data.get("preprocessing_contract_hash"),
            "preprocessing contract mismatch",
        ),
        (
            release_data.get("model_version") == metadata_data.get("model_version"),
            "model version mismatch",
        ),
        (
            release_data.get("exploratory")
            == metadata_data.get("exploratory")
            == threshold_data.get("exploratory"),
            "exploratory flag mismatch",
        ),
    )
    for valid, message in semantic_checks:
        if not valid:
            return _failure(ReasonCode.THRESHOLD_MISMATCH, message)
    release = ReleaseManifest(
        root=root,
        model_path=model,
        thresholds_path=thresholds,
        metadata_path=metadata,
        release_path=release_path,
        model_sha256=model_hash,
        thresholds_sha256=str(release_data["thresholds_sha256"]),
        metadata_sha256=str(release_data["metadata_sha256"]),
        manifest_sha256=str(release_data["manifest_sha256"]),
        preprocessing_contract_hash=str(release_data["preprocessing_contract_hash"]),
        model_version=str(release_data["model_version"]),
        exploratory=bool(release_data["exploratory"]),
    )
    return BundleVerification(True, ReasonCode.NONE, (), release)
