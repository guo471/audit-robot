from __future__ import annotations

import json
import math
import multiprocessing as mp
import queue
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import onnxruntime as ort

from .artifacts import preprocess_contract_hash, verify_release_bundle
from .config import PreprocessConfig
from .contracts import Decision, ReasonCode
from .hashing import sha256_file
from .preprocessing import ImageDecodeError, build_eval_transform, decode_rgb
from .thresholds import FrozenThresholds, classify_score


@dataclass(frozen=True)
class ImageDecision:
    decision: Decision
    score: float | None
    elapsed_ms: float
    reason_code: ReasonCode
    model_version: str


@dataclass(frozen=True)
class OrderDecision:
    decision: Decision
    images: tuple[ImageDecision, ...]
    elapsed_ms: float
    reason_code: ReasonCode
    mode: str = "offline_shadow"


@dataclass(frozen=True)
class StartupResult:
    ok: bool
    reason_code: ReasonCode
    predictor: ShadowPredictor | None
    detail: str = ""
    decision: Decision = "manual_review"


def _preprocess_from_json(value: object) -> PreprocessConfig:
    if not isinstance(value, dict):
        raise ValueError("preprocess metadata must be an object")
    data = dict(value)
    for name in ("fill_rgb", "mean", "std"):
        if name in data:
            data[name] = tuple(data[name])
    return PreprocessConfig(**data)


def _risk_from_logits(logits: np.ndarray) -> float:
    values = np.asarray(logits, dtype=np.float64)
    if values.shape != (1, 2) or not np.isfinite(values).all():
        raise ValueError("runtime returned invalid logits")
    shifted = values[0] - np.max(values[0])
    probabilities = np.exp(shifted) / np.exp(shifted).sum()
    score = float(probabilities[1])
    if not math.isfinite(score):
        raise ValueError("runtime returned a non-finite score")
    return score


class ShadowPredictor:
    def __init__(
        self,
        session: ort.InferenceSession,
        preprocess: PreprocessConfig,
        thresholds: FrozenThresholds,
        model_version: str,
    ) -> None:
        self._session = session
        self._preprocess = preprocess
        self._transform = build_eval_transform(preprocess)
        self._thresholds = thresholds
        self._model_version = model_version
        self._input_name = session.get_inputs()[0].name

    @classmethod
    def start(cls, release_dir: Path, intra_op_threads: int) -> StartupResult:
        verification = verify_release_bundle(release_dir)
        if not verification.ok or verification.release is None:
            return StartupResult(False, verification.reason_code, None, "; ".join(verification.errors))
        release = verification.release
        if intra_op_threads <= 0:
            return StartupResult(False, ReasonCode.ENVIRONMENT_INVALID, None, "thread count must be positive")
        try:
            metadata = json.loads(release.metadata_path.read_text(encoding="utf-8"))
            threshold_data = json.loads(release.thresholds_path.read_text(encoding="utf-8"))
            preprocess = _preprocess_from_json(metadata.get("preprocess"))
            if preprocess_contract_hash(preprocess) != release.preprocessing_contract_hash:
                return StartupResult(False, ReasonCode.THRESHOLD_MISMATCH, None, "preprocess hash mismatch")
            thresholds = FrozenThresholds(
                low_risk=float(threshold_data["low_risk"]),
                risk=float(threshold_data["risk"]),
                model_sha256=str(threshold_data["model_sha256"]),
                exploratory=bool(threshold_data["exploratory"]),
                selection_scope=str(threshold_data["selection_scope"]),
            )
            self_test = metadata["self_test"]
            expected_score = float(self_test["expected_non_real_risk"])
            tolerance = float(self_test["absolute_tolerance"])
            options = ort.SessionOptions()
            options.intra_op_num_threads = intra_op_threads
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                str(release.model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            predictor = cls(
                session,
                preprocess,
                thresholds,
                str(metadata["model_version"]),
            )
            tensor = np.zeros(
                (1, 3, preprocess.image_size, preprocess.image_size), dtype=np.float32
            )
            score = _risk_from_logits(session.run(None, {predictor._input_name: tensor})[0])
            if (
                not math.isfinite(expected_score)
                or tolerance < 0
                or abs(score - expected_score) > tolerance
            ):
                return StartupResult(False, ReasonCode.SELF_TEST_FAILED, None, "self-test score mismatch")
            return StartupResult(True, ReasonCode.NONE, predictor)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            return StartupResult(False, ReasonCode.THRESHOLD_MISMATCH, None, str(exc))
        except Exception as exc:
            return StartupResult(False, ReasonCode.INFERENCE_ERROR, None, str(exc))

    def predict_image(self, path: Path) -> ImageDecision:
        started = time.perf_counter_ns()
        try:
            image = decode_rgb(path)
            tensor = self._transform(image).unsqueeze(0).numpy()
        except ImageDecodeError:
            return ImageDecision(
                "manual_review",
                None,
                (time.perf_counter_ns() - started) / 1_000_000,
                ReasonCode.IMAGE_CORRUPT,
                self._model_version,
            )
        except Exception:
            return ImageDecision(
                "manual_review",
                None,
                (time.perf_counter_ns() - started) / 1_000_000,
                ReasonCode.PREPROCESS_FAILED,
                self._model_version,
            )
        try:
            if self._session is None:
                raise RuntimeError("runtime session unavailable")
            logits = self._session.run(None, {self._input_name: tensor})[0]
            score = _risk_from_logits(logits)
            return ImageDecision(
                classify_score(score, self._thresholds),
                score,
                (time.perf_counter_ns() - started) / 1_000_000,
                ReasonCode.NONE,
                self._model_version,
            )
        except Exception:
            return ImageDecision(
                "manual_review",
                None,
                (time.perf_counter_ns() - started) / 1_000_000,
                ReasonCode.INFERENCE_ERROR,
                self._model_version,
            )


def _order_worker(
    output_queue: mp.Queue,
    release_dir: str,
    image_paths: tuple[str, ...],
    intra_op_threads: int,
) -> None:
    startup = ShadowPredictor.start(Path(release_dir), intra_op_threads)
    if not startup.ok or startup.predictor is None:
        output_queue.put(
            {
                "decision": "manual_review",
                "images": [],
                "reason_code": startup.reason_code.value,
            }
        )
        return
    decisions = tuple(startup.predictor.predict_image(Path(path)) for path in image_paths)
    all_low = all(item.decision == "low_risk_candidate" for item in decisions)
    reason = next(
        (item.reason_code for item in decisions if item.reason_code != ReasonCode.NONE),
        ReasonCode.NONE,
    )
    output_queue.put(
        {
            "decision": "low_risk_candidate" if all_low else "manual_review",
            "images": [
                {
                    **asdict(item),
                    "reason_code": item.reason_code.value,
                }
                for item in decisions
            ],
            "reason_code": reason.value,
        }
    )


def _manual_order(reason: ReasonCode, elapsed_ms: float = 0.0) -> OrderDecision:
    return OrderDecision("manual_review", (), elapsed_ms, reason)


def _decode_worker_payload(payload: object, elapsed_ms: float) -> OrderDecision:
    if not isinstance(payload, dict):
        raise ValueError("worker payload is not an object")
    if set(payload) != {"decision", "images", "reason_code"}:
        raise ValueError("worker payload schema mismatch")
    decision = payload["decision"]
    if decision not in {"low_risk_candidate", "manual_review"}:
        raise ValueError("worker decision invalid")
    raw_images = payload["images"]
    if not isinstance(raw_images, list):
        raise ValueError("worker image payload invalid")
    images = tuple(
        ImageDecision(
            decision=item["decision"],
            score=item["score"],
            elapsed_ms=float(item["elapsed_ms"]),
            reason_code=ReasonCode(item["reason_code"]),
            model_version=str(item["model_version"]),
        )
        for item in raw_images
    )
    if images and len(images) != 3:
        raise ValueError("worker must return exactly three image decisions")
    return OrderDecision(decision, images, elapsed_ms, ReasonCode(payload["reason_code"]))


def _write_log(log_path: Path, release_dir: Path, result: OrderDecision) -> None:
    verification = verify_release_bundle(release_dir)
    release = verification.release
    release_json = release_dir.resolve() / "release.json"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "mode": "offline_shadow",
        "release_sha256": sha256_file(release_json) if release_json.is_file() else None,
        "model_sha256": release.model_sha256 if release else None,
        "thresholds_sha256": release.thresholds_sha256 if release else None,
        "image_scores": [item.score for item in result.images] if result.images else [None, None, None],
        "image_reason_codes": [item.reason_code.value for item in result.images],
        "decision": result.decision,
        "elapsed_ms": result.elapsed_ms,
        "reason_code": result.reason_code.value,
    }
    path = log_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def predict_order_isolated(
    release_dir: Path,
    image_paths: Sequence[Path],
    timeout_seconds: float,
    log_path: Path,
    *,
    intra_op_threads: int = 1,
    worker_target: Callable[..., None] | None = None,
) -> OrderDecision:
    started = time.perf_counter_ns()
    if len(image_paths) != 3:
        result = _manual_order(ReasonCode.INPUT_COUNT_INVALID)
    elif timeout_seconds <= 0:
        result = _manual_order(ReasonCode.TIMEOUT)
    else:
        context = mp.get_context("spawn")
        output_queue = context.Queue(maxsize=1)
        target = worker_target or _order_worker
        process = context.Process(
            target=target,
            args=(
                output_queue,
                str(release_dir.resolve()),
                tuple(str(path.resolve()) for path in image_paths),
                intra_op_threads,
            ),
        )
        process.start()
        process.join(timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join()
            result = _manual_order(
                ReasonCode.TIMEOUT, (time.perf_counter_ns() - started) / 1_000_000
            )
        elif process.exitcode != 0:
            result = _manual_order(
                ReasonCode.INFERENCE_ERROR,
                (time.perf_counter_ns() - started) / 1_000_000,
            )
        else:
            try:
                payload = output_queue.get(timeout=0.5)
                result = _decode_worker_payload(
                    payload, (time.perf_counter_ns() - started) / 1_000_000
                )
            except (queue.Empty, KeyError, TypeError, ValueError):
                result = _manual_order(
                    ReasonCode.INFERENCE_ERROR,
                    (time.perf_counter_ns() - started) / 1_000_000,
                )
        output_queue.close()
        output_queue.join_thread()
    try:
        _write_log(log_path, release_dir, result)
    except Exception:
        return OrderDecision(
            "manual_review", result.images, result.elapsed_ms, ReasonCode.LOG_WRITE_FAILED
        )
    return result
