"""CSV report writer with privacy-safe rows."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .privacy import SAFE_REPORT_COLUMNS, safe_report_row


def append_report_row(report_path: str | Path, data: dict[str, Any]) -> None:
    """Append a privacy-safe row to a UTF-8 BOM CSV report."""
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new_file = not path.exists() or path.stat().st_size == 0

    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SAFE_REPORT_COLUMNS)
        if is_new_file:
            writer.writeheader()
        writer.writerow(safe_report_row(data))
