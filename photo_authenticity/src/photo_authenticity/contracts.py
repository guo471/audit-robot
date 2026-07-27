from __future__ import annotations

from enum import StrEnum
from typing import Literal


Decision = Literal["low_risk_candidate", "manual_review"]
RunMode = Literal["offline_shadow"]


class ReasonCode(StrEnum):
    NONE = "none"
    ENVIRONMENT_INVALID = "environment_invalid"
    MODEL_MISSING = "model_missing"
    MODEL_HASH_MISMATCH = "model_hash_mismatch"
    THRESHOLD_MISMATCH = "threshold_mismatch"
    PREPROCESS_FAILED = "preprocess_failed"
    IMAGE_CORRUPT = "image_corrupt"
    INFERENCE_ERROR = "inference_error"
    TIMEOUT = "timeout"
    SELF_TEST_FAILED = "self_test_failed"
    INPUT_COUNT_INVALID = "input_count_invalid"
    LOG_WRITE_FAILED = "log_write_failed"
