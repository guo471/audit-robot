"""Privacy helpers for safe audit reports."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from config import TEMP_DIR


SAFE_REPORT_COLUMNS = [
    "jl_order_no",
    "channel_order_no",
    "scene",
    "category",
    "decision",
    "path",
    "elapsed_sec",
    "manual_reason",
    "sn_match",
    "image_roles_ok",
    "real_photo_pass",
    "id_name_match",
    "id_valid",
    "address_detail_ok",
]

URL_RE = re.compile(r"https?://[^\s,;\uFF0C\uFF1B\u3002)\uFF09]+", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
ADDRESS_RE = re.compile(
    r"[\u4e00-\u9fff]{2,}(?:\u7701|\u5e02|\u81ea\u6cbb\u533a|\u533a|\u53bf|"
    r"\u9547|\u4e61|\u8857\u9053|\u8def|\u8857|\u5df7|\u6751|\u53f7)"
    r"[\u4e00-\u9fffA-Za-z0-9\-#\u680b\u5ea7\u5355\u5143\u5ba4\u697c\u5c42"
    r"\u5e62\u53f7]+"
)


def redact_text(value: Any) -> str:
    """Redact sensitive values from free text."""
    if value is None:
        return ""

    text = str(value)
    text = URL_RE.sub("[URL]", text)
    text = PHONE_RE.sub("[PHONE]", text)
    text = ID_RE.sub("[ID]", text)
    text = ADDRESS_RE.sub("[ADDRESS]", text)
    return text


def safe_report_row(data: dict[str, Any]) -> dict[str, Any]:
    """Return only report-safe columns, with sensitive manual notes redacted."""
    row: dict[str, Any] = {}
    for column in SAFE_REPORT_COLUMNS:
        value = data.get(column)
        if value is None:
            row[column] = ""
        elif column == "manual_reason":
            row[column] = redact_text(value)
        else:
            row[column] = value
    return row


def remove_temp_dir(path: str | Path) -> None:
    """Delete a temporary directory only when it is inside the project temp root."""
    temp_path = Path(path).resolve()
    temp_root = TEMP_DIR.resolve()
    if temp_path == temp_root or temp_root not in temp_path.parents:
        raise ValueError(f"Refusing to delete non-child temp directory: {temp_path}")
    if temp_path.exists() and temp_path.is_dir():
        shutil.rmtree(temp_path)
