from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import joblib
import numpy as np
from PIL import Image, ImageOps


EDGE_NAMES = ("top", "right", "bottom", "left")
EDGE_VALUES = frozenset({"scene_continues", "carrier_boundary", "abrupt_cutoff", "not_visible", "uncertain"})
SCREEN_OWNERS = frozenset({"product_screen", "external_screen", "none", "uncertain"})
REGION_CODES = frozenset({"product_body", "product_screen", "package", "hand", "background", "image_edge", "unknown"})
STRONG_CODES = frozenset({
    "EXTERNAL_PHOTO_CARRIER", "PHOTO_VIEWER_UI", "PRINTED_PHOTO_CARRIER",
    "NESTED_IMAGE_BOUNDARY", "CROSS_OBJECT_MOIRE",
})
WEAK_CODES = frozenset({"EDGE_CUTOFF", "OUTER_PLANE_OPTICS", "PLANAR_APPEARANCE", "LOCAL_MOIRE", "UI_CANDIDATE"})
OBSERVATION_FIELDS = frozenset({"image_id", "edges", "screen_owner", "strong_evidence", "weak_evidence", "reason"})

EXPECTED_EXTRACTOR_VERSION = "fft-v1-512-ycbcr-5x53"
EXPECTED_FEATURE_DIMENSION = 795
EXPECTED_THRESHOLD = 0.995
EXPECTED_MODEL_SHA256 = "49352975e2ef36d3723cbe6fe028687a56101920fef50becc744c65b96aa512b"
EXPECTED_METADATA_SHA256 = "e8c4ba687b702535231d91e001c55f4e0750b07ab79e3cd4829ae1dea5f708cf"
EXPECTED_V4_PROMPT_SHA256 = "7d4e7224b38c5fc5cbb9293f6d72b091df39d4dc9efb2e983b0daa829a43ca77"
DEFAULT_ARTIFACT_DIR = Path("photo_authenticity/models/releases/non-real-photo-v2")
DEFAULT_LOCAL_TREE_ARTIFACT_PATH = Path("photo_authenticity/models/releases/non-real-local-tree-v1/tree.json")
EXPECTED_LOCAL_TREE_SHA256 = "5e3ea24636dcc0df2f9554a54325be240a6245dc2eb7347cbdf02079d4c618a8"
UNIFORM_SCREEN_TEXTURE_EXTRACTOR_VERSION = "uniform-orientation-v2-3x3-192"
UNIFORM_SCREEN_TEXTURE_THRESHOLD = 1.0
UNIFORM_SCREEN_TEXTURE_LIMITS = {
    "entropy_45_mean_min": 0.9694,
    "entropy_45_std_max": 0.021,
    "entropy_67_mean_min": 0.979,
    "entropy_67_std_max": 0.018,
}


class PhotoAuthenticitySchemaError(ValueError):
    """The merged model response is unusable as a complete per-image contract."""


def load_approved_v4_prompt(path: Path) -> str:
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_V4_PROMPT_SHA256:
        raise ValueError("approved V4 prompt hash mismatch")
    return raw.decode("utf-8")


@dataclass(frozen=True)
class PhotoAuthenticityConfig:
    mode: str
    artifact_dir: Path
    fft_enabled: bool = False
    sn_label_auth_review_enabled: bool = False
    local_tree_enabled: bool = False
    local_tree_artifact_path: Path = DEFAULT_LOCAL_TREE_ARTIFACT_PATH
    max_fft_attempts: int = 2

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "PhotoAuthenticityConfig":
        mode = str(env.get("PHOTO_AUTHENTICITY_MODE", "enforce")).strip().lower()
        if mode not in {"off", "shadow", "enforce"}:
            raise ValueError("PHOTO_AUTHENTICITY_MODE must be off, shadow, or enforce")
        fft_raw = str(env.get("PHOTO_AUTHENTICITY_FFT_ENABLED", "false")).strip().lower()
        if fft_raw in {"true", "1", "yes", "on"}:
            fft_enabled = True
        elif fft_raw in {"false", "0", "no", "off", ""}:
            fft_enabled = False
        else:
            raise ValueError("PHOTO_AUTHENTICITY_FFT_ENABLED must be true or false")
        marker_raw = str(env.get("SN_LABEL_AUTH_REVIEW_MODE", "off")).strip().lower()
        if marker_raw == "on":
            cross_surface_marker_enabled = True
        elif marker_raw in {"off", ""}:
            cross_surface_marker_enabled = False
        else:
            raise ValueError("SN_LABEL_AUTH_REVIEW_MODE must be on or off")
        tree_raw = str(env.get("PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED", "false")).strip().lower()
        if tree_raw in {"true", "1", "yes", "on"}:
            local_tree_enabled = True
        elif tree_raw in {"false", "0", "no", "off", ""}:
            local_tree_enabled = False
        else:
            raise ValueError("PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED must be true or false")
        artifact_dir = Path(env.get("PHOTO_AUTHENTICITY_ARTIFACT_DIR", str(DEFAULT_ARTIFACT_DIR)))
        local_tree_artifact_path = Path(
            env.get("PHOTO_AUTHENTICITY_LOCAL_TREE_ARTIFACT_PATH", str(DEFAULT_LOCAL_TREE_ARTIFACT_PATH))
        )
        return cls(
            mode=mode,
            artifact_dir=artifact_dir,
            fft_enabled=fft_enabled,
            sn_label_auth_review_enabled=cross_surface_marker_enabled,
            local_tree_enabled=local_tree_enabled,
            local_tree_artifact_path=local_tree_artifact_path,
        )


@dataclass(frozen=True)
class Evidence:
    code: str
    regions: tuple[str, ...]


@dataclass(frozen=True)
class ImageObservation:
    image_id: str
    edges: dict[str, str]
    screen_owner: str
    strong_evidence: tuple[Evidence, ...]
    weak_evidence: tuple[Evidence, ...]
    reason: str


@dataclass(frozen=True)
class AuthenticityImageResult:
    image_id: str
    result: str
    rule: str
    status: str = "ok"
    score: float | None = None
    rescued_by_fft: bool = False
    evidence_summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class AuthenticityOrderResult:
    mode: str
    would_manual: bool
    image_results: dict[str, AuthenticityImageResult]
    service_failure: bool = False


def _schema_error(image_id: Any, message: str) -> PhotoAuthenticitySchemaError:
    return PhotoAuthenticitySchemaError(f"image_id={image_id!r}: {message}")


def _validate_evidence(image_id: str, value: Any, field: str, allowed: frozenset[str]) -> tuple[Evidence, ...]:
    if not isinstance(value, list):
        raise _schema_error(image_id, f"{field} must be an array")
    merged: dict[str, list[str]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"code", "regions"}:
            raise _schema_error(image_id, f"{field} entries require exactly code and regions")
        code, regions = item["code"], item["regions"]
        if not isinstance(code, str) or code not in allowed:
            raise _schema_error(image_id, f"unknown {field} code {code!r}")
        if not isinstance(regions, list) or not all(isinstance(region, str) for region in regions):
            raise _schema_error(image_id, f"{field} regions must be a string array")
        unknown = set(regions) - REGION_CODES
        if unknown:
            raise _schema_error(image_id, f"unknown regions {sorted(unknown)}")
        target = merged.setdefault(code, [])
        target.extend(region for region in regions if region not in target)
    return tuple(Evidence(code, tuple(regions)) for code, regions in merged.items())


def _validate_one(item: Any) -> ImageObservation:
    hint = item.get("image_id") if isinstance(item, dict) else "unknown"
    if not isinstance(item, dict) or set(item) != OBSERVATION_FIELDS:
        keys = set(item) if isinstance(item, dict) else set()
        raise _schema_error(hint, f"invalid fields; missing={sorted(OBSERVATION_FIELDS - keys)}, extra={sorted(keys - OBSERVATION_FIELDS)}")
    image_id = item["image_id"]
    if not isinstance(image_id, str) or not image_id:
        raise _schema_error(image_id, "image_id must be non-empty text")
    edges = item["edges"]
    if not isinstance(edges, dict) or set(edges) != set(EDGE_NAMES):
        raise _schema_error(image_id, "edges must contain exactly top, right, bottom, and left")
    if any(not isinstance(value, str) or value not in EDGE_VALUES for value in edges.values()):
        raise _schema_error(image_id, "unknown edge value")
    if not isinstance(item["screen_owner"], str) or item["screen_owner"] not in SCREEN_OWNERS:
        raise _schema_error(image_id, "unknown screen_owner")
    if not isinstance(item["reason"], str):
        raise _schema_error(image_id, "reason must be text")
    return ImageObservation(
        image_id=image_id,
        edges={side: edges[side] for side in EDGE_NAMES},
        screen_owner=item["screen_owner"],
        strong_evidence=_validate_evidence(image_id, item["strong_evidence"], "strong_evidence", STRONG_CODES),
        weak_evidence=_validate_evidence(image_id, item["weak_evidence"], "weak_evidence", WEAK_CODES),
        reason=item["reason"].strip()[:160] or "Model supplied no reason",
    )


def validate_image_observations(raw: Any, expected_image_ids: Sequence[str]) -> dict[str, ImageObservation]:
    expected = list(expected_image_ids)
    if len(expected) != len(set(expected)) or not all(isinstance(value, str) and value for value in expected):
        raise PhotoAuthenticitySchemaError("expected_image_ids must be unique non-empty strings")
    if not isinstance(raw, list):
        raise PhotoAuthenticitySchemaError(f"affected_ids={expected}: photo_authenticity_by_image must be an array")
    parsed = [_validate_one(item) for item in raw]
    ids = [item.image_id for item in parsed]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    missing = sorted(set(expected) - set(ids))
    extra = sorted(set(ids) - set(expected))
    if duplicates or missing or extra:
        raise PhotoAuthenticitySchemaError(
            f"image coverage mismatch; duplicate={duplicates}, missing={missing}, extra={extra}"
        )
    by_id = {item.image_id: item for item in parsed}
    return {image_id: by_id[image_id] for image_id in expected}


def derive_v4_result(observation: ImageObservation) -> tuple[str, str]:
    strong = {item.code: item for item in observation.strong_evidence}
    weak = {item.code: item for item in observation.weak_evidence}
    ui = strong.get("PHOTO_VIEWER_UI")
    product_ui_exempt = (
        observation.screen_owner == "product_screen" and ui is not None
        and bool(ui.regions) and set(ui.regions) == {"product_screen"}
    )
    effective_strong = set(strong) - ({"PHOTO_VIEWER_UI"} if product_ui_exempt else set())
    if "EXTERNAL_PHOTO_CARRIER" in effective_strong:
        return "high_risk_non_real", "R1"
    if "PHOTO_VIEWER_UI" in effective_strong and observation.screen_owner == "external_screen":
        return "high_risk_non_real", "R2"
    if product_ui_exempt and not effective_strong and not weak and all(value == "scene_continues" for value in observation.edges.values()):
        return "no_evidence", "R3"
    local_moire = weak.get("LOCAL_MOIRE")
    product_screen_local_moire_exempt = (
        observation.screen_owner == "product_screen"
        and not effective_strong
        and set(weak) == {"LOCAL_MOIRE"}
        and local_moire is not None
        and bool(local_moire.regions)
        and set(local_moire.regions) == {"product_screen"}
        and all(value == "scene_continues" for value in observation.edges.values())
    )
    if product_screen_local_moire_exempt:
        return "no_evidence", "R10_PRODUCT_SCREEN_LOCAL_MOIRE_EXEMPT"
    if effective_strong & {"PRINTED_PHOTO_CARRIER", "NESTED_IMAGE_BOUNDARY"}:
        return "high_risk_non_real", "R4"
    moire = strong.get("CROSS_OBJECT_MOIRE")
    moire_regions = set(moire.regions) - {"unknown"} if moire else set()
    non_screen_moire_regions = moire_regions - {"product_screen"}
    if len(non_screen_moire_regions) >= 2 or (
        "product_screen" in moire_regions and bool(non_screen_moire_regions)
    ):
        return "high_risk_non_real", "R5"
    carrier_edges = sum(value == "carrier_boundary" for value in observation.edges.values())
    if carrier_edges >= 2:
        return "high_risk_non_real", "R6"
    if carrier_edges == 1:
        return "manual_review", "R7"
    if "abrupt_cutoff" in observation.edges.values() and "OUTER_PLANE_OPTICS" in weak:
        return "high_risk_non_real", "R8"
    outer_plane_optics = weak.get("OUTER_PLANE_OPTICS")
    product_screen_outer_optics_exempt = (
        not effective_strong
        and set(weak) == {"OUTER_PLANE_OPTICS"}
        and observation.screen_owner == "product_screen"
        and outer_plane_optics is not None
        and set(outer_plane_optics.regions) == {"product_screen"}
        and all(value == "scene_continues" for value in observation.edges.values())
    )
    if product_screen_outer_optics_exempt:
        return "no_evidence", "R10_PRODUCT_SCREEN_OUTER_OPTICS_EXEMPT"
    if effective_strong or weak:
        return "manual_review", "R9"
    return "no_evidence", "R9"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _letterbox(image: Image.Image, size: int = 512) -> np.ndarray:
    image = ImageOps.exif_transpose(image).convert("RGB")
    scale = size / max(image.size)
    resized = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    return np.asarray(canvas, dtype=np.float32) / 255.0


def _spectrum_features(channel: np.ndarray) -> np.ndarray:
    centered = channel - float(channel.mean())
    magnitude = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(centered))))
    height, width = magnitude.shape
    yy, xx = np.indices((height, width), dtype=np.float32)
    cy, cx = (height - 1) / 2, (width - 1) / 2
    dx, dy = xx - cx, yy - cy
    radius = np.sqrt(dx * dx + dy * dy)
    normalized_radius = radius / max(1.0, float(radius.max()))
    theta = (np.arctan2(dy, dx) + math.pi) / (2 * math.pi)
    valid = radius > 1.5
    values = magnitude[valid]
    total = float(values.sum()) + 1e-12
    radial_edges = np.linspace(0, 1, 33)
    angular_edges = np.linspace(0, 1, 13)
    radial = [float(magnitude[(normalized_radius >= low) & (normalized_radius < high)].sum() / total) for low, high in zip(radial_edges[:-1], radial_edges[1:])]
    angular = [float(magnitude[valid & (theta >= low) & (theta < high)].sum() / total) for low, high in zip(angular_edges[:-1], angular_edges[1:])]
    high_frequency = [float(magnitude[normalized_radius >= cutoff].sum() / total) for cutoff in (0.25, 0.4, 0.6, 0.8)]
    probabilities = values / total
    entropy = float(-(probabilities * np.log(probabilities + 1e-12)).sum() / math.log(max(2, probabilities.size)))
    mean, std = float(values.mean()), float(values.std()) + 1e-12
    kurtosis = float(np.mean(((values - mean) / std) ** 4))
    peak_median = float(values.max() / (float(np.median(values)) + 1e-12))
    narrow = max(1, min(height, width) // 64)
    horizontal = float(magnitude[abs(dy) <= narrow].sum() / total)
    vertical = float(magnitude[abs(dx) <= narrow].sum() / total)
    return np.asarray(radial + angular + high_frequency + [entropy, kurtosis, peak_median, horizontal, vertical], dtype=np.float32)


def extract_fft_features(image: Image.Image) -> np.ndarray:
    rgb = _letterbox(image)
    ycbcr = np.asarray(Image.fromarray(np.uint8(np.clip(rgb * 255, 0, 255))).convert("YCbCr"), dtype=np.float32) / 255.0
    half = ycbcr.shape[0] // 2
    regions = [ycbcr, ycbcr[:half, :half], ycbcr[:half, half:], ycbcr[half:, :half], ycbcr[half:, half:]]
    vector = np.concatenate([_spectrum_features(region[:, :, channel]) for region in regions for channel in range(3)])
    if vector.shape != (EXPECTED_FEATURE_DIMENSION,):
        raise ValueError(f"FFT feature dimension mismatch: {vector.shape}")
    return vector


@dataclass(frozen=True)
class FrozenFFTRescue:
    metadata: dict[str, Any]
    scaler: Any
    classifier: Any

    @property
    def threshold(self) -> float:
        return float(self.metadata["threshold"])

    @property
    def feature_dimension(self) -> int:
        return int(self.metadata["feature_dimension"])

    @classmethod
    def load(cls, artifact_dir: Path) -> "FrozenFFTRescue":
        artifact_dir = Path(artifact_dir)
        metadata_path = artifact_dir / "metadata.json"
        if _sha256(metadata_path) != EXPECTED_METADATA_SHA256:
            raise ValueError("frozen metadata hash mismatch")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        model_path = artifact_dir / "model.joblib"
        actual_hash = _sha256(model_path)
        if actual_hash != EXPECTED_MODEL_SHA256 or metadata.get("model_sha256") != EXPECTED_MODEL_SHA256:
            raise ValueError("frozen model hash mismatch")
        if metadata.get("extractor_version") != EXPECTED_EXTRACTOR_VERSION:
            raise ValueError("frozen extractor version mismatch")
        if int(metadata.get("feature_dimension", -1)) != EXPECTED_FEATURE_DIMENSION:
            raise ValueError("frozen feature dimension mismatch")
        if float(metadata.get("threshold", -1)) != EXPECTED_THRESHOLD:
            raise ValueError("frozen threshold mismatch")
        if metadata.get("route_policy") != {"allowed_transition": "no_evidence->manual_review"}:
            raise ValueError("unsupported frozen route policy")
        payload = joblib.load(model_path)
        scaler, classifier = payload["scaler"], payload["classifier"]
        if int(scaler.n_features_in_) != EXPECTED_FEATURE_DIMENSION or int(classifier.n_features_in_) != EXPECTED_FEATURE_DIMENSION:
            raise ValueError("loaded model feature dimension mismatch")
        return cls(metadata=metadata, scaler=scaler, classifier=classifier)

    def score(self, image_path: Path) -> tuple[float, dict[str, Any]]:
        with Image.open(image_path) as source:
            vector = extract_fft_features(ImageOps.exif_transpose(source).convert("RGB"))
        score = float(self.classifier.predict_proba(self.scaler.transform(vector.reshape(1, -1)))[0, 1])
        return score, {
            "extractor_version": EXPECTED_EXTRACTOR_VERSION,
            "feature_dimension": EXPECTED_FEATURE_DIMENSION,
            "threshold": self.threshold,
            "model_sha256": EXPECTED_MODEL_SHA256,
        }


@dataclass(frozen=True)
class LocalTreeNonRealRescue:
    payload: dict[str, Any]
    artifact_sha256: str

    @property
    def threshold(self) -> float:
        return float(self.payload["leaf_positive_ratio"])

    @classmethod
    def load(cls, artifact_path: Path) -> "LocalTreeNonRealRescue":
        artifact_path = Path(artifact_path)
        raw = artifact_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != EXPECTED_LOCAL_TREE_SHA256:
            raise ValueError("local tree artifact hash mismatch")
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("model_type") != "local_tree_non_real_v1":
            raise ValueError("unsupported local tree model type")
        from tools.non_real_local_features import FEATURE_EXTRACTOR_VERSION, FEATURE_NAMES

        if payload.get("feature_extractor_version") != FEATURE_EXTRACTOR_VERSION:
            raise ValueError("unsupported local tree feature extractor version")
        if tuple(payload.get("feature_names") or ()) != FEATURE_NAMES:
            raise ValueError("unsupported local tree feature contract")
        if payload.get("route_policy") != {"allowed_transition": "no_evidence->high_risk_non_real"}:
            raise ValueError("unsupported local tree route policy")
        if float(payload.get("leaf_positive_ratio", -1)) != 0.3:
            raise ValueError("unsupported local tree threshold")
        if not isinstance(payload.get("tree"), dict):
            raise ValueError("local tree payload missing tree")
        return cls(payload=payload, artifact_sha256=digest)

    def score(self, image_path: Path) -> tuple[float, dict[str, Any]]:
        from tools.non_real_local_features import extract_features

        features = extract_features(Path(image_path))
        node = self.payload["tree"]
        while not bool(node.get("leaf")):
            metric = str(node.get("metric") or "")
            if metric not in features:
                raise ValueError(f"local tree feature missing: {metric}")
            node = node["left"] if float(features[metric]) <= float(node["threshold"]) else node["right"]
        total = int(node.get("pos", 0)) + int(node.get("neg", 0))
        ratio = (int(node.get("pos", 0)) / total) if total else 0.0
        return ratio, {
            "source": "local_image_engine",
            "detector": "local-tree-non-real-v1",
            "tree_sha256": self.artifact_sha256,
            "source_feature_jsonl_sha256": self.payload.get("source_feature_jsonl_sha256", ""),
            "threshold": self.threshold,
        }


def _load_cv2() -> Any:
    import cv2

    return cv2


def _orientation_entropy(tile: np.ndarray) -> float:
    cv2 = _load_cv2()
    gray = cv2.cvtColor(tile, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    high_pass = (
        cv2.GaussianBlur(gray, (0, 0), 0.55)
        - cv2.GaussianBlur(gray, (0, 0), 2.2)
    )
    gradient_x = cv2.Sobel(high_pass, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(high_pass, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.sqrt(gradient_x * gradient_x + gradient_y * gradient_y)
    angle = np.mod(np.arctan2(gradient_y, gradient_x), math.pi)
    low, high = np.quantile(magnitude, (0.35, 0.95))
    selected = (magnitude >= low) & (magnitude <= high)
    histogram, _ = np.histogram(
        angle[selected],
        bins=36,
        range=(0.0, math.pi),
        weights=magnitude[selected],
    )
    total = float(histogram.sum())
    if total <= 0:
        return 0.0
    probabilities = histogram / total
    return float(
        -(probabilities * np.log(probabilities + 1e-12)).sum()
        / math.log(len(probabilities))
    )


def _orientation_entropy_stats(image: np.ndarray, relative_scale: float) -> tuple[float, float]:
    cv2 = _load_cv2()
    height, width = image.shape[:2]
    base_scale = min(1.0, 2048.0 / max(height, width))
    scale = base_scale * relative_scale
    resized_width = max(192, round(width * scale))
    resized_height = max(192, round(height * scale))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(
        image,
        (resized_width, resized_height),
        interpolation=interpolation,
    )
    centers_y = np.unique(np.rint(np.linspace(96, resized_height - 96, 3)).astype(int))
    centers_x = np.unique(np.rint(np.linspace(96, resized_width - 96, 3)).astype(int))
    entropies = [
        _orientation_entropy(resized[center_y - 96:center_y + 96, center_x - 96:center_x + 96])
        for center_y in centers_y
        for center_x in centers_x
    ]
    values = np.asarray(entropies, dtype=np.float64)
    return float(values.mean()), float(values.std())


@dataclass(frozen=True)
class UniformScreenTextureDetector:
    threshold: float = UNIFORM_SCREEN_TEXTURE_THRESHOLD

    @property
    def feature_dimension(self) -> int:
        return 4

    def score(self, image_path: Path) -> tuple[float, dict[str, Any]]:
        cv2 = _load_cv2()
        encoded = np.fromfile(str(image_path), dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("local texture detector could not decode image")
        image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        entropy_45_mean, entropy_45_std = _orientation_entropy_stats(image, 0.45)
        entropy_67_mean, entropy_67_std = _orientation_entropy_stats(image, 0.67)
        metrics = {
            "entropy_45_mean": entropy_45_mean,
            "entropy_45_std": entropy_45_std,
            "entropy_67_mean": entropy_67_mean,
            "entropy_67_std": entropy_67_std,
        }
        margins = (
            entropy_45_mean / UNIFORM_SCREEN_TEXTURE_LIMITS["entropy_45_mean_min"],
            UNIFORM_SCREEN_TEXTURE_LIMITS["entropy_45_std_max"] / max(entropy_45_std, 1e-12),
            entropy_67_mean / UNIFORM_SCREEN_TEXTURE_LIMITS["entropy_67_mean_min"],
            UNIFORM_SCREEN_TEXTURE_LIMITS["entropy_67_std_max"] / max(entropy_67_std, 1e-12),
        )
        score = float(min(margins))
        return score, {
            "source": "local_image_engine",
            "detector": "uniform-screen-texture-v2",
            "extractor_version": UNIFORM_SCREEN_TEXTURE_EXTRACTOR_VERSION,
            "feature_dimension": self.feature_dimension,
            "threshold": self.threshold,
            "limits": dict(UNIFORM_SCREEN_TEXTURE_LIMITS),
            "metrics": metrics,
        }


def evaluate_authenticity_images(
    *,
    config: PhotoAuthenticityConfig,
    raw_observations: Any,
    expected_image_ids: Sequence[str],
    image_paths: Mapping[str, Path],
    rescue: FrozenFFTRescue | None = None,
    texture_detector: UniformScreenTextureDetector | None = None,
    local_tree: LocalTreeNonRealRescue | None = None,
    budget_available: Callable[[], bool] = lambda: True,
) -> AuthenticityOrderResult:
    if config.mode == "off":
        return AuthenticityOrderResult(mode="off", would_manual=False, image_results={})
    observations = validate_image_observations(raw_observations, expected_image_ids)
    loaded_rescue = rescue
    loaded_texture_detector = texture_detector
    loaded_local_tree = local_tree
    artifact_error: Exception | None = None
    local_tree_error: Exception | None = None
    results: dict[str, AuthenticityImageResult] = {}
    service_failure = False
    for image_id, observation in observations.items():
        result, rule = derive_v4_result(observation)
        if result != "no_evidence":
            results[image_id] = AuthenticityImageResult(image_id, result, rule)
            continue
        local_tree_no_hit: AuthenticityImageResult | None = None
        if config.local_tree_enabled:
            if not budget_available():
                service_failure = True
                local_tree_no_hit = AuthenticityImageResult(
                    image_id, "no_evidence", rule, status="local_tree_unavailable",
                    evidence_summary={"error": "order budget exhausted before local tree"},
                )
            else:
                image_path = Path(image_paths.get(image_id, Path()))
                if not image_path.is_file():
                    service_failure = True
                    local_tree_no_hit = AuthenticityImageResult(
                        image_id, "no_evidence", rule, status="local_tree_unavailable",
                        evidence_summary={"error": "image file missing for local tree"},
                    )
                elif loaded_local_tree is None and local_tree_error is None:
                    try:
                        loaded_local_tree = LocalTreeNonRealRescue.load(config.local_tree_artifact_path)
                    except Exception as exc:
                        local_tree_error = exc
                if local_tree_error is not None and local_tree_no_hit is None:
                    service_failure = True
                    local_tree_no_hit = AuthenticityImageResult(
                        image_id, "no_evidence", rule, status="local_tree_unavailable",
                        evidence_summary={"error": f"{type(local_tree_error).__name__}: {local_tree_error}"},
                    )
                if local_tree_no_hit is None:
                    try:
                        tree_score, tree_summary = loaded_local_tree.score(image_path)
                    except Exception as exc:
                        service_failure = True
                        local_tree_no_hit = AuthenticityImageResult(
                            image_id,
                            "no_evidence",
                            rule,
                            status="local_tree_unavailable",
                            evidence_summary={"error": f"{type(exc).__name__}: {exc}"},
                        )
                    else:
                        if tree_score >= loaded_local_tree.threshold:
                            results[image_id] = AuthenticityImageResult(
                                image_id,
                                "high_risk_non_real",
                                "LOCAL_TREE",
                                score=tree_score,
                                evidence_summary=tree_summary,
                            )
                            continue
                        local_tree_no_hit = AuthenticityImageResult(
                            image_id,
                            "no_evidence",
                            rule,
                            score=tree_score,
                            evidence_summary=tree_summary,
                        )
        if config.sn_label_auth_review_enabled:
            if not budget_available():
                service_failure = True
                results[image_id] = AuthenticityImageResult(
                    image_id, "manual_review", "ORDER_BUDGET_EXHAUSTED", status="order_budget_exhausted",
                )
                continue
            image_path = Path(image_paths.get(image_id, Path()))
            if not image_path.is_file():
                service_failure = True
                results[image_id] = AuthenticityImageResult(
                    image_id, "manual_review", "IMAGE_FILE_MISSING", status="image_file_missing",
                )
                continue
            if loaded_texture_detector is None:
                loaded_texture_detector = UniformScreenTextureDetector()
            try:
                texture_score, texture_summary = loaded_texture_detector.score(image_path)
            except Exception as exc:
                service_failure = True
                results[image_id] = AuthenticityImageResult(
                    image_id,
                    "manual_review",
                    "SN_LABEL_AUTH_LOCAL_FAILURE",
                    status="failed_after_retries",
                    evidence_summary={"error": f"{type(exc).__name__}: {exc}"},
                )
                continue
            texture_summary = dict(texture_summary)
            texture_summary.setdefault("source", "local_image_engine")
            if texture_score >= loaded_texture_detector.threshold:
                results[image_id] = AuthenticityImageResult(
                    image_id,
                    "high_risk_non_real",
                    "LOCAL_CROSS_SURFACE_TEXTURE",
                    score=texture_score,
                    evidence_summary=texture_summary,
                )
                continue
            if not config.fft_enabled:
                results[image_id] = AuthenticityImageResult(
                    image_id,
                    "no_evidence",
                    rule,
                    score=texture_score,
                    evidence_summary=texture_summary,
                )
                continue
        if not config.fft_enabled:
            results[image_id] = local_tree_no_hit or AuthenticityImageResult(image_id, "no_evidence", rule)
            continue
        if not budget_available():
            service_failure = True
            results[image_id] = AuthenticityImageResult(
                image_id, "manual_review", "ORDER_BUDGET_EXHAUSTED", status="order_budget_exhausted",
            )
            continue
        image_path = Path(image_paths.get(image_id, Path()))
        if not image_path.is_file():
            service_failure = True
            results[image_id] = AuthenticityImageResult(
                image_id, "manual_review", "IMAGE_FILE_MISSING", status="image_file_missing",
            )
            continue
        if loaded_rescue is None and artifact_error is None:
            try:
                loaded_rescue = FrozenFFTRescue.load(config.artifact_dir)
            except Exception as exc:
                artifact_error = exc
        if artifact_error is not None:
            service_failure = True
            results[image_id] = AuthenticityImageResult(
                image_id, "manual_review", "ARTIFACT_LOAD_FAILURE", status="artifact_load_failure",
                evidence_summary={"error": f"{type(artifact_error).__name__}: {artifact_error}"},
            )
            continue
        last_error: Exception | None = None
        for _ in range(config.max_fft_attempts):
            try:
                if not budget_available():
                    raise TimeoutError("order budget exhausted before FFT")
                score, summary = loaded_rescue.score(image_path)
                routed = "manual_review" if score >= loaded_rescue.threshold else "no_evidence"
                results[image_id] = AuthenticityImageResult(
                    image_id, routed, "FFT_RESCUE" if routed == "manual_review" else rule,
                    score=score, rescued_by_fft=routed == "manual_review", evidence_summary=summary,
                )
                break
            except Exception as exc:
                last_error = exc
        else:
            service_failure = True
            results[image_id] = AuthenticityImageResult(
                image_id, "manual_review", "FFT_FAILURE", status="failed_after_retries",
                evidence_summary={"error": f"{type(last_error).__name__}: {last_error}"},
            )
    return AuthenticityOrderResult(
        mode=config.mode,
        would_manual=any(item.result != "no_evidence" for item in results.values()),
        image_results=results,
        service_failure=service_failure,
    )


def _order_reason(result: AuthenticityOrderResult) -> str:
    values = tuple(result.image_results.values())
    if any(item.result == "high_risk_non_real" for item in values):
        return "NON_REAL_PHOTO_STRONG_RISK"
    if any(item.rescued_by_fft for item in values):
        return "NON_REAL_PHOTO_FFT_RESCUE"
    if any(item.result == "manual_review" and not str(item.status or "").startswith("failed") for item in values):
        return "NON_REAL_PHOTO_REVIEW"
    if result.service_failure:
        return "PHOTO_AUTHENTICITY_SERVICE_FAILURE"
    return "NON_REAL_PHOTO_REVIEW"


def _single_fallback_candidate(raw: Any, image_ids: Sequence[str]) -> tuple[str | None, list[dict[str, Any]]]:
    if not isinstance(raw, list):
        return (image_ids[0], []) if len(image_ids) == 1 else (None, [])
    valid: dict[str, dict[str, Any]] = {}
    invalid_ids: set[str] = set()
    for item in raw:
        image_id = str(item.get("image_id") or "") if isinstance(item, dict) else ""
        if image_id not in image_ids or image_id in valid:
            return None, []
        try:
            validate_image_observations([item], [image_id])
            valid[image_id] = item
        except PhotoAuthenticitySchemaError:
            invalid_ids.add(image_id)
    affected = [image_id for image_id in image_ids if image_id not in valid] + [x for x in invalid_ids if x in valid]
    affected = list(dict.fromkeys(affected))
    return (affected[0], list(valid.values())) if len(affected) == 1 else (None, list(valid.values()))


def apply_photo_authenticity_gate(
    *,
    legacy_row: dict[str, Any],
    compliance: Mapping[str, Any],
    images: Sequence[Mapping[str, Any]],
    config: PhotoAuthenticityConfig,
    fallback: Callable[[Mapping[str, Any]], Any] | None = None,
    evaluator: Callable[..., AuthenticityOrderResult] = evaluate_authenticity_images,
    budget_available: Callable[[], bool] = lambda: True,
) -> dict[str, Any]:
    """Apply authenticity only after the legacy order decision is final."""
    if config.mode == "off" or str(legacy_row.get("manual_flag")) == "是":
        return legacy_row

    image_ids = [str(item.get("image_id") or "") for item in images]
    image_paths = {str(item.get("image_id") or ""): Path(str(item.get("local_path") or "")) for item in images}
    raw = compliance.get("photo_authenticity_by_image")
    fallback_calls = 0
    try:
        result = evaluator(
            config=config, raw_observations=raw, expected_image_ids=image_ids, image_paths=image_paths,
            budget_available=budget_available,
        )
    except Exception as first_error:
        candidate_id, valid_raw = _single_fallback_candidate(raw, image_ids)
        image_by_id = {str(item.get("image_id") or ""): item for item in images}
        if fallback is not None and candidate_id is not None and budget_available():
            fallback_calls = 1
            try:
                raw = fallback(image_by_id[candidate_id])
                if isinstance(raw, Mapping):
                    raw = dict(raw)
                    raw.pop("result", None)
                    raw["image_id"] = candidate_id
                raw = valid_raw + [raw]
                result = evaluator(
                    config=config, raw_observations=raw, expected_image_ids=image_ids, image_paths=image_paths,
                    budget_available=budget_available,
                )
            except Exception as final_error:
                result = AuthenticityOrderResult(
                    mode=config.mode, would_manual=True, image_results={}, service_failure=True,
                )
                legacy_row["photo_authenticity_error"] = (
                    f"{type(first_error).__name__}: {first_error}; fallback: "
                    f"{type(final_error).__name__}: {final_error}"
                )
        else:
            result = AuthenticityOrderResult(mode=config.mode, would_manual=True, image_results={}, service_failure=True)
            legacy_row["photo_authenticity_error"] = f"{type(first_error).__name__}: {first_error}"

    legacy_row["photo_authenticity_mode"] = config.mode
    legacy_row["photo_authenticity_would_manual"] = result.would_manual
    legacy_row["photo_authenticity_service_failure"] = result.service_failure
    legacy_row["photo_authenticity_fallback_calls"] = fallback_calls
    legacy_row["photo_authenticity_image_results"] = {
        key: {
            "result": value.result, "rule": value.rule, "status": value.status,
            "score": value.score, "rescued_by_fft": value.rescued_by_fft,
            "evidence_summary": value.evidence_summary,
        }
        for key, value in result.image_results.items()
    }
    if config.mode == "enforce" and result.would_manual:
        code = _order_reason(result)
        legacy_row["manual_flag"] = "是"
        legacy_row["manual_reason_code"] = code
        legacy_row["manual_reason_cn"] = "图片真实性需要人工复核"
        legacy_row["manual_reason"] = code + ": 图片真实性检测命中或检测服务异常"
    return legacy_row
