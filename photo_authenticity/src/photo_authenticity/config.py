from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ReasonCode, RunMode


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PreprocessConfig:
    image_size: int
    fill_rgb: tuple[int, int, int]
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    brightness: float = 0.0
    contrast: float = 0.0
    scale_min: float = 1.0
    perspective: float = 0.0
    gaussian_blur_probability: float = 0.0
    jpeg_quality_min: int = 100


@dataclass(frozen=True)
class TrainingConfig:
    head_epochs: int
    tail_epochs: int
    head_learning_rate: float
    tail_learning_rate: float
    tail_blocks: int
    batch_size: int = 8
    early_stopping_patience: int = 2


@dataclass(frozen=True)
class ThresholdPolicy:
    low_risk_threshold: float
    risk_threshold: float
    minimum_non_real_recall: float


@dataclass(frozen=True)
class RuntimeLimits:
    max_order_seconds: float
    intra_op_threads: int


@dataclass(frozen=True)
class AppConfig:
    mode: RunMode
    seed: int
    preprocess: PreprocessConfig
    training: TrainingConfig
    thresholds: ThresholdPolicy
    runtime: RuntimeLimits


@dataclass(frozen=True)
class EnvironmentCheck:
    ok: bool
    reason_code: ReasonCode
    detail: str


def check_environment(
    python_version: tuple[int, int, int], mode: str, network_enabled: bool
) -> EnvironmentCheck:
    valid = python_version[:2] == (3, 11) and mode == "offline_shadow" and not network_enabled
    if valid:
        return EnvironmentCheck(True, ReasonCode.NONE, "Python 3.11 offline shadow environment")
    return EnvironmentCheck(False, ReasonCode.ENVIRONMENT_INVALID, "Python 3.11, offline_shadow, and disabled network are required")


def _tuple(values: Any, length: int, name: str, cast: type) -> tuple:
    if not isinstance(values, list) or len(values) != length:
        raise ConfigError(f"{name} must contain {length} values")
    return tuple(cast(value) for value in values)


def load_config(path: Path) -> AppConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        mode = raw["mode"]
        if mode != "offline_shadow":
            raise ConfigError("mode must be offline_shadow")

        preprocess_raw = raw["preprocess"]
        training_raw = raw["training"]
        threshold_raw = raw["thresholds"]
        runtime_raw = raw["runtime"]
        low = float(threshold_raw["low_risk_threshold"])
        risk = float(threshold_raw["risk_threshold"])
        recall = float(threshold_raw["minimum_non_real_recall"])
        if not (0.0 <= low < risk <= 1.0):
            raise ConfigError("thresholds must satisfy 0 <= low_risk < risk <= 1")
        if not 0.0 <= recall <= 1.0:
            raise ConfigError("minimum_non_real_recall must be in [0, 1]")

        preprocess = PreprocessConfig(
            image_size=int(preprocess_raw["image_size"]),
            fill_rgb=_tuple(preprocess_raw["fill_rgb"], 3, "fill_rgb", int),
            mean=_tuple(preprocess_raw["mean"], 3, "mean", float),
            std=_tuple(preprocess_raw["std"], 3, "std", float),
            brightness=float(preprocess_raw.get("brightness", 0.0)),
            contrast=float(preprocess_raw.get("contrast", 0.0)),
            scale_min=float(preprocess_raw.get("scale_min", 1.0)),
            perspective=float(preprocess_raw.get("perspective", 0.0)),
            gaussian_blur_probability=float(preprocess_raw.get("gaussian_blur_probability", 0.0)),
            jpeg_quality_min=int(preprocess_raw.get("jpeg_quality_min", 100)),
        )
        training = TrainingConfig(
            head_epochs=int(training_raw["head_epochs"]),
            tail_epochs=int(training_raw["tail_epochs"]),
            head_learning_rate=float(training_raw["head_learning_rate"]),
            tail_learning_rate=float(training_raw["tail_learning_rate"]),
            tail_blocks=int(training_raw["tail_blocks"]),
            batch_size=int(training_raw.get("batch_size", 8)),
            early_stopping_patience=int(training_raw.get("early_stopping_patience", 2)),
        )
        thresholds = ThresholdPolicy(low, risk, recall)
        runtime = RuntimeLimits(
            max_order_seconds=float(runtime_raw["max_order_seconds"]),
            intra_op_threads=int(runtime_raw["intra_op_threads"]),
        )
        if preprocess.image_size <= 0 or training.tail_blocks not in (1, 2):
            raise ConfigError("image_size must be positive and tail_blocks must be 1 or 2")
        if runtime.max_order_seconds <= 0 or runtime.intra_op_threads <= 0:
            raise ConfigError("runtime limits must be positive")
        return AppConfig(mode, int(raw["seed"]), preprocess, training, thresholds, runtime)
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"invalid configuration: {exc}") from exc
