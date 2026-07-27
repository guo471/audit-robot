from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from photo_authenticity.config import ConfigError, check_environment, load_config
from photo_authenticity.contracts import ReasonCode


def test_environment_accepts_only_python_311_offline_shadow() -> None:
    ok = check_environment((3, 11, 9), "offline_shadow", False)
    wrong_python = check_environment((3, 13, 0), "offline_shadow", False)
    online = check_environment((3, 11, 9), "offline_shadow", True)

    assert ok.ok is True
    assert ok.reason_code == ReasonCode.NONE
    assert wrong_python.reason_code == ReasonCode.ENVIRONMENT_INVALID
    assert online.reason_code == ReasonCode.ENVIRONMENT_INVALID


def test_load_config_builds_immutable_nested_configuration(tmp_path) -> None:
    path = tmp_path / "base.toml"
    path.write_text(
        """
mode = "offline_shadow"
seed = 20260713

[preprocess]
image_size = 224
fill_rgb = [0, 0, 0]
mean = [0.485, 0.456, 0.406]
std = [0.229, 0.224, 0.225]

[training]
head_epochs = 2
tail_epochs = 2
head_learning_rate = 0.001
tail_learning_rate = 0.0001
tail_blocks = 1

[thresholds]
low_risk_threshold = 0.2
risk_threshold = 0.7
minimum_non_real_recall = 0.9

[runtime]
max_order_seconds = 5.0
intra_op_threads = 1
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.mode == "offline_shadow"
    assert config.seed == 20260713
    assert config.preprocess.image_size == 224
    with pytest.raises(FrozenInstanceError):
        config.seed = 1


def test_load_config_rejects_invalid_mode_and_threshold_order(tmp_path) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text(
        """
mode = "production"
seed = 20260713

[preprocess]
image_size = 224
fill_rgb = [0, 0, 0]
mean = [0.485, 0.456, 0.406]
std = [0.229, 0.224, 0.225]

[training]
head_epochs = 1
tail_epochs = 1
head_learning_rate = 0.001
tail_learning_rate = 0.0001
tail_blocks = 1

[thresholds]
low_risk_threshold = 0.8
risk_threshold = 0.7
minimum_non_real_recall = 0.9

[runtime]
max_order_seconds = 5.0
intra_op_threads = 1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(path)
