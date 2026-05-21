# Audit Automation Integrated Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, secure, measurable audit automation backend that lets Yingdao call the existing OCR/rule engine, auto-approve only high-confidence orders, and send all uncertain orders to manual handling.

**Architecture:** Keep audit judgment inside a local Python service bound to `127.0.0.1`; keep Yingdao as replaceable hands/feet that only collects page fields/images and clicks approve/next. Add typed request/response models, privacy-safe reports, image role/category classification, fast/slow OCR paths, and a 60-second per-order timeout around the existing modules.

**Tech Stack:** Python 3.12, PaddleOCR/PaddlePaddle CPU, OpenCV, Pillow, FastAPI, Uvicorn, pytest, stdlib CSV/logging/pathlib/tempfile.

---

## Source Design

This plan implements:

- `C:\audit_robot\docs\superpowers\specs\2026-05-21-audit-automation-integrated-design.md`
- Existing code under `C:\audit_robot\run_audit.py`
- Existing modules under `C:\audit_robot\modules`

Current repository note: `C:\audit_robot` is not a git repository. Every task still includes a checkpoint step. If the user initializes git before execution, use the listed commit commands; otherwise run the listed file/status commands and continue without commit.

## File Structure

Create:

- `C:\audit_robot\tests\test_audit_models.py`  
  Validates request/response normalization and `skip` to `manual` compatibility.
- `C:\audit_robot\tests\test_privacy.py`  
  Validates no sensitive data is written to reports or logs.
- `C:\audit_robot\tests\test_report_writer.py`  
  Validates CSV append behavior and column names.
- `C:\audit_robot\tests\test_image_role.py`  
  Validates title-based image role grouping, including duplicate titles.
- `C:\audit_robot\tests\test_category_classifier.py`  
  Validates non-coupon 3C, national-subsidy 3C, home appliance, and unsupported categories.
- `C:\audit_robot\tests\test_address_checker_detail.py`  
  Validates small-range address rules.
- `C:\audit_robot\tests\test_audit_runner.py`  
  Validates fast/slow path orchestration using fake OCR/forensics dependencies.
- `C:\audit_robot\tests\test_audit_service.py`  
  Validates local service token, loopback-only config, and JSON response shape.
- `C:\audit_robot\modules\audit_models.py`  
  Dataclasses and normalization helpers for service request/response.
- `C:\audit_robot\modules\privacy.py`  
  Redaction, safe log/report field selection, temporary directory cleanup.
- `C:\audit_robot\modules\report_writer.py`  
  Privacy-safe CSV report append.
- `C:\audit_robot\modules\image_role.py`  
  Image role classification from page titles; groups duplicate-role images safely.
- `C:\audit_robot\modules\category_classifier.py`  
  Scene and product category classifier.
- `C:\audit_robot\modules\audit_runner.py`  
  Service-friendly audit orchestration with dependency injection, fast/slow path, and timeout.
- `C:\audit_robot\audit_service.py`  
  FastAPI HTTP service bound to local host.

Modify:

- `C:\audit_robot\requirements.txt`  
  Add FastAPI, Uvicorn, pytest, httpx.
- `C:\audit_robot\config.py`  
  Add service token/env config, timeout, report path, path flags, and privacy toggles.
- `C:\audit_robot\modules\__init__.py`  
  Export new modules as needed.
- `C:\audit_robot\modules\address_checker.py`  
  Add explicit small-range address helper without breaking existing `validate_address`.
- `C:\audit_robot\modules\rule_engine.py`  
  Normalize external decision to `manual`; keep `skip` internally compatible.
- `C:\audit_robot\run_audit.py`  
  Delegate shared orchestration to `modules.audit_runner`; preserve CLI behavior.

## Task 1: Test Harness And Dependencies

**Files:**

- Modify: `C:\audit_robot\requirements.txt`
- Create: `C:\audit_robot\tests\conftest.py`

- [ ] **Step 1: Add test and service dependencies**

Update `C:\audit_robot\requirements.txt` to contain:

```text
paddlepaddle>=3.0
paddleocr>=3.0
opencv-python>=4.8
numpy>=1.24
pillow>=10.0
piexif>=1.1
fastapi>=0.110
uvicorn>=0.27
pytest>=8.0
httpx>=0.27
```

- [ ] **Step 2: Create pytest path bootstrap**

Create `C:\audit_robot\tests\conftest.py`:

```python
# -*- coding: utf-8 -*-
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
```

- [ ] **Step 3: Run dependency-light pytest collection**

Run:

```powershell
python -m pytest --collect-only tests -q
```

Expected:

```text
no tests collected
```

If pytest is not installed, run:

```powershell
python -m pip install -r requirements.txt
python -m pytest --collect-only tests -q
```

Expected after install:

```text
no tests collected
```

- [ ] **Step 4: Checkpoint**

Run:

```powershell
git status --short
```

Expected if git is unavailable:

```text
fatal: not a git repository (or any of the parent directories): .git
```

If git is available:

```powershell
git add requirements.txt tests/conftest.py
git commit -m "test: add audit automation test harness"
```

## Task 2: Typed Models And Decision Normalization

**Files:**

- Create: `C:\audit_robot\modules\audit_models.py`
- Create: `C:\audit_robot\tests\test_audit_models.py`
- Modify: `C:\audit_robot\modules\__init__.py`

- [ ] **Step 1: Write failing tests for request and response normalization**

Create `C:\audit_robot\tests\test_audit_models.py`:

```python
# -*- coding: utf-8 -*-
from modules.audit_models import AuditImage, AuditRequest, AuditResponse, normalize_decision


def test_audit_request_keeps_jl_order_no_and_images():
    request = AuditRequest.from_dict({
        "jl_order_no": "JL123",
        "channel_order_no": "CH456",
        "scene_hint": "家电数码3C（国补2026）",
        "fields": {"sn": "SN001", "product_name": "手机"},
        "images": [{"title": "SN码采集照片", "path": "C:/tmp/1.jpg"}],
    })

    assert request.jl_order_no == "JL123"
    assert request.fields["sn"] == "SN001"
    assert request.images == [AuditImage(title="SN码采集照片", path="C:/tmp/1.jpg")]


def test_normalize_decision_maps_skip_to_manual():
    assert normalize_decision("skip") == "manual"
    assert normalize_decision("pass") == "pass"
    assert normalize_decision("engine_error") == "error"
    assert normalize_decision("unknown") == "manual"


def test_response_action_matches_decision():
    response = AuditResponse.manual(
        jl_order_no="JL123",
        scene="guobu_3c",
        path="slow",
        elapsed_sec=60.0,
        manual_reason="SN未高置信识别",
    )

    assert response.decision == "manual"
    assert response.action == "next"
    assert response.to_dict()["jl_order_no"] == "JL123"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_audit_models.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'modules.audit_models'
```

- [ ] **Step 3: Implement audit models**

Create `C:\audit_robot\modules\audit_models.py`:

```python
# -*- coding: utf-8 -*-
"""Typed request and response objects for the local audit service."""
from dataclasses import dataclass, field
from typing import Any


def normalize_decision(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized == "pass":
        return "pass"
    if normalized in {"engine_error", "error"}:
        return "error"
    return "manual"


@dataclass(frozen=True)
class AuditImage:
    title: str
    path: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditImage":
        return cls(
            title=str(data.get("title", "")).strip(),
            path=str(data.get("path", "")).strip(),
        )


@dataclass(frozen=True)
class AuditRequest:
    jl_order_no: str
    channel_order_no: str = ""
    scene_hint: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    images: list[AuditImage] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditRequest":
        return cls(
            jl_order_no=str(data.get("jl_order_no", "")).strip(),
            channel_order_no=str(data.get("channel_order_no", "")).strip(),
            scene_hint=str(data.get("scene_hint", "")).strip(),
            fields=dict(data.get("fields") or {}),
            images=[AuditImage.from_dict(item) for item in data.get("images", [])],
        )

    def system_data(self) -> dict[str, Any]:
        data = dict(self.fields)
        data.setdefault("jl_order_no", self.jl_order_no)
        data.setdefault("order_id", self.jl_order_no)
        if self.channel_order_no:
            data.setdefault("channel_order_no", self.channel_order_no)
        return data


@dataclass(frozen=True)
class AuditResponse:
    decision: str
    action: str
    jl_order_no: str
    scene: str
    path: str
    elapsed_sec: float
    manual_reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def pass_(
        cls,
        jl_order_no: str,
        scene: str,
        path: str,
        elapsed_sec: float,
        evidence: dict[str, Any] | None = None,
    ) -> "AuditResponse":
        return cls(
            decision="pass",
            action="approve",
            jl_order_no=jl_order_no,
            scene=scene,
            path=path,
            elapsed_sec=round(float(elapsed_sec), 3),
            manual_reason=None,
            evidence=evidence or {},
        )

    @classmethod
    def manual(
        cls,
        jl_order_no: str,
        scene: str,
        path: str,
        elapsed_sec: float,
        manual_reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> "AuditResponse":
        return cls(
            decision="manual",
            action="next",
            jl_order_no=jl_order_no,
            scene=scene,
            path=path,
            elapsed_sec=round(float(elapsed_sec), 3),
            manual_reason=manual_reason,
            evidence=evidence or {},
        )

    @classmethod
    def error(
        cls,
        jl_order_no: str,
        scene: str,
        elapsed_sec: float,
        manual_reason: str,
    ) -> "AuditResponse":
        return cls(
            decision="error",
            action="next",
            jl_order_no=jl_order_no,
            scene=scene,
            path="error",
            elapsed_sec=round(float(elapsed_sec), 3),
            manual_reason=manual_reason,
            evidence={},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "action": self.action,
            "jl_order_no": self.jl_order_no,
            "scene": self.scene,
            "path": self.path,
            "elapsed_sec": self.elapsed_sec,
            "manual_reason": self.manual_reason,
            "evidence": self.evidence,
        }
```

- [ ] **Step 4: Export new model objects**

Append to `C:\audit_robot\modules\__init__.py`:

```python
from .audit_models import AuditImage, AuditRequest, AuditResponse, normalize_decision
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_audit_models.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Checkpoint**

If git is available:

```powershell
git add modules/audit_models.py modules/__init__.py tests/test_audit_models.py
git commit -m "feat: add audit request response models"
```

## Task 3: Privacy Redaction And Safe Reports

**Files:**

- Create: `C:\audit_robot\modules\privacy.py`
- Create: `C:\audit_robot\modules\report_writer.py`
- Create: `C:\audit_robot\tests\test_privacy.py`
- Create: `C:\audit_robot\tests\test_report_writer.py`
- Modify: `C:\audit_robot\config.py`
- Modify: `C:\audit_robot\modules\__init__.py`

- [ ] **Step 1: Write failing privacy tests**

Create `C:\audit_robot\tests\test_privacy.py`:

```python
# -*- coding: utf-8 -*-
from modules.privacy import redact_text, safe_report_row


def test_redact_text_removes_id_phone_url_and_long_address():
    text = "身份证 440101199001011234 手机 13800138000 图片 https://x.test/a.jpg 地址 广东省广州市天河区某路88号1栋101"
    redacted = redact_text(text)

    assert "440101199001011234" not in redacted
    assert "13800138000" not in redacted
    assert "https://x.test/a.jpg" not in redacted
    assert "某路88号1栋101" not in redacted
    assert "[ID]" in redacted
    assert "[PHONE]" in redacted
    assert "[URL]" in redacted


def test_safe_report_row_keeps_order_result_and_reason_only():
    row = safe_report_row({
        "jl_order_no": "JL123",
        "channel_order_no": "CH456",
        "scene": "guobu_3c",
        "category": "3c",
        "decision": "manual",
        "path": "slow",
        "elapsed_sec": 60.0,
        "manual_reason": "身份证 440101199001011234 识别失败",
        "image_url": "https://x.test/a.jpg",
        "ocr_raw_text": "SN ABC",
        "address": "广东省广州市天河区某路88号1栋101",
    })

    assert row["jl_order_no"] == "JL123"
    assert row["decision"] == "manual"
    assert "440101199001011234" not in row["manual_reason"]
    assert "image_url" not in row
    assert "ocr_raw_text" not in row
    assert "address" not in row
```

- [ ] **Step 2: Write failing report writer tests**

Create `C:\audit_robot\tests\test_report_writer.py`:

```python
# -*- coding: utf-8 -*-
import csv
from modules.report_writer import append_report_row


def test_append_report_row_writes_header_and_safe_values(tmp_path):
    report = tmp_path / "audit_report.csv"

    append_report_row(report, {
        "jl_order_no": "JL123",
        "channel_order_no": "CH456",
        "scene": "guobu_3c",
        "category": "3c",
        "decision": "manual",
        "path": "slow",
        "elapsed_sec": 60.0,
        "manual_reason": "图片 https://x.test/a.jpg SN未识别",
        "sn_match": False,
        "image_roles_ok": True,
        "real_photo_pass": True,
        "id_name_match": None,
        "id_valid": None,
        "address_detail_ok": None,
    })

    with report.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["jl_order_no"] == "JL123"
    assert rows[0]["decision"] == "manual"
    assert "https://x.test" not in rows[0]["manual_reason"]
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_privacy.py tests/test_report_writer.py -q
```

Expected:

```text
ModuleNotFoundError
```

- [ ] **Step 4: Add config values**

Append to `C:\audit_robot\config.py`:

```python

# --- 本地服务与安全 ---
AUDIT_SERVICE_HOST = "127.0.0.1"
AUDIT_SERVICE_PORT = 8765
AUDIT_SERVICE_TOKEN_ENV = "AUDIT_SERVICE_TOKEN"
AUDIT_DEFAULT_TOKEN = "local-dev-token-change-me"
AUDIT_ORDER_TIMEOUT_SEC = 60

# --- 报表 ---
REPORTS_DIR = PROJECT_DIR / "reports"
AUDIT_REPORT_PATH = REPORTS_DIR / "audit_report.csv"
```

- [ ] **Step 5: Implement privacy helpers**

Create `C:\audit_robot\modules\privacy.py`:

```python
# -*- coding: utf-8 -*-
"""Privacy helpers for logs and reports."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any


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


def redact_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"\b1[3-9]\d{9}\b", "[PHONE]", text)
    text = re.sub(r"\b\d{17}[\dXx]\b", "[ID]", text)
    text = re.sub(
        r"(地址|住址)[：:\s]*[^\s,，;；]{8,}",
        r"\1[ADDRESS]",
        text,
    )
    text = re.sub(
        r"[\u4e00-\u9fa5]{2,}(省|市|区|县|镇|乡|街道|路|村|组|号|栋|单元|室)[^\s,，;；]{6,}",
        "[ADDRESS]",
        text,
    )
    return text


def safe_report_row(data: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for column in SAFE_REPORT_COLUMNS:
        value = data.get(column)
        if column == "manual_reason":
            row[column] = redact_text(value)
        else:
            row[column] = "" if value is None else value
    return row


def remove_temp_dir(path: str | Path | None) -> None:
    if not path:
        return
    target = Path(path)
    if target.exists() and target.is_dir():
        shutil.rmtree(target)
```

- [ ] **Step 6: Implement report writer**

Create `C:\audit_robot\modules\report_writer.py`:

```python
# -*- coding: utf-8 -*-
"""CSV report writing with privacy-safe columns."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .privacy import SAFE_REPORT_COLUMNS, safe_report_row


def append_report_row(report_path: str | Path, data: dict[str, Any]) -> None:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = safe_report_row(data)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SAFE_REPORT_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
```

- [ ] **Step 7: Export privacy and report helpers**

Append to `C:\audit_robot\modules\__init__.py`:

```python
from .privacy import redact_text, safe_report_row, remove_temp_dir
from .report_writer import append_report_row
```

- [ ] **Step 8: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_privacy.py tests/test_report_writer.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 9: Checkpoint**

If git is available:

```powershell
git add config.py modules/privacy.py modules/report_writer.py modules/__init__.py tests/test_privacy.py tests/test_report_writer.py
git commit -m "feat: add privacy safe reporting"
```

## Task 4: Image Role Classification

**Files:**

- Create: `C:\audit_robot\modules\image_role.py`
- Create: `C:\audit_robot\tests\test_image_role.py`
- Modify: `C:\audit_robot\modules\__init__.py`

- [ ] **Step 1: Write failing image role tests**

Create `C:\audit_robot\tests\test_image_role.py`:

```python
# -*- coding: utf-8 -*-
from modules.audit_models import AuditImage
from modules.image_role import classify_image_role, group_images_by_role


def test_classify_known_titles():
    assert classify_image_role("二代居民身份证人像面") == "id_front"
    assert classify_image_role("二代居民身份证国徽面") == "id_back"
    assert classify_image_role("商品照片") == "product_photo"
    assert classify_image_role("拆封照片") == "unboxing_photo"
    assert classify_image_role("SN码采集/激活照片") == "activation_photo"


def test_group_duplicate_titles_keeps_all_images():
    images = [
        AuditImage(title="商品照片", path="C:/tmp/a.jpg"),
        AuditImage(title="商品照片", path="C:/tmp/b.jpg"),
        AuditImage(title="SN码采集照片", path="C:/tmp/c.jpg"),
    ]

    grouped = group_images_by_role(images)

    assert [img.path for img in grouped["product_photo"]] == ["C:/tmp/a.jpg", "C:/tmp/b.jpg"]
    assert [img.path for img in grouped["activation_photo"]] == ["C:/tmp/c.jpg"]
    assert grouped["unknown"] == []


def test_unknown_title_goes_to_unknown():
    grouped = group_images_by_role([AuditImage(title="其他材料", path="C:/tmp/x.jpg")])

    assert grouped["unknown"][0].path == "C:/tmp/x.jpg"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_image_role.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'modules.image_role'
```

- [ ] **Step 3: Implement image role classifier**

Create `C:\audit_robot\modules\image_role.py`:

```python
# -*- coding: utf-8 -*-
"""Classify audit images by page title."""
from __future__ import annotations

from collections import defaultdict

from .audit_models import AuditImage


ROLE_KEYS = [
    "id_front",
    "id_back",
    "product_photo",
    "unboxing_photo",
    "activation_photo",
    "unknown",
]


def classify_image_role(title: str) -> str:
    text = (title or "").lower()
    if "身份证" in title and ("人像" in title or "正面" in title):
        return "id_front"
    if "身份证" in title and ("国徽" in title or "反面" in title):
        return "id_back"
    if "拆封" in title or "开箱" in title:
        return "unboxing_photo"
    if "激活" in title or "sn" in text or "序列号" in title or "采集" in title:
        return "activation_photo"
    if "商品" in title or "新物" in title or "产品" in title:
        return "product_photo"
    return "unknown"


def group_images_by_role(images: list[AuditImage]) -> dict[str, list[AuditImage]]:
    grouped: dict[str, list[AuditImage]] = {key: [] for key in ROLE_KEYS}
    buckets: defaultdict[str, list[AuditImage]] = defaultdict(list)
    for image in images:
        buckets[classify_image_role(image.title)].append(image)
    for key in ROLE_KEYS:
        grouped[key] = list(buckets.get(key, []))
    return grouped


def required_roles_present(grouped: dict[str, list[AuditImage]], scene: str) -> bool:
    if scene == "no_coupon":
        return bool(grouped.get("id_front")) and bool(grouped.get("id_back")) and (
            bool(grouped.get("product_photo")) or bool(grouped.get("activation_photo"))
        )
    return bool(grouped.get("product_photo")) and bool(grouped.get("unboxing_photo")) and bool(grouped.get("activation_photo"))
```

- [ ] **Step 4: Export image role helpers**

Append to `C:\audit_robot\modules\__init__.py`:

```python
from .image_role import classify_image_role, group_images_by_role, required_roles_present
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_image_role.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Checkpoint**

If git is available:

```powershell
git add modules/image_role.py modules/__init__.py tests/test_image_role.py
git commit -m "feat: classify audit image roles"
```

## Task 5: Category Classification

**Files:**

- Create: `C:\audit_robot\modules\category_classifier.py`
- Create: `C:\audit_robot\tests\test_category_classifier.py`
- Modify: `C:\audit_robot\modules\__init__.py`

- [ ] **Step 1: Write failing category tests**

Create `C:\audit_robot\tests\test_category_classifier.py`:

```python
# -*- coding: utf-8 -*-
from modules.category_classifier import classify_audit_category


def test_classifies_non_coupon_3c():
    result = classify_audit_category("非发券审核", {"product_type": "手机数码", "product_name": "华为手机"})

    assert result.scene == "no_coupon"
    assert result.category == "3c"
    assert result.supported is True


def test_classifies_guobu_3c():
    result = classify_audit_category("家电数码3C（国补2026）", {"product_type": "3C", "product_name": "笔记本电脑"})

    assert result.scene == "guobu"
    assert result.category == "3c"
    assert result.supported is True


def test_classifies_guobu_home_appliance():
    result = classify_audit_category("家电数码3C（国补2026）", {"product_type": "家电", "product_name": "海尔冰箱"})

    assert result.scene == "guobu"
    assert result.category == "home_appliance"
    assert result.supported is True


def test_unsupported_car_goes_manual_category():
    result = classify_audit_category("汽车审核", {"product_name": "车辆置换"})

    assert result.scene == "unsupported"
    assert result.category == "unsupported"
    assert result.supported is False
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_category_classifier.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'modules.category_classifier'
```

- [ ] **Step 3: Implement category classifier**

Create `C:\audit_robot\modules\category_classifier.py`:

```python
# -*- coding: utf-8 -*-
"""Scene and product category classification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CategoryResult:
    scene: str
    category: str
    supported: bool
    reason: str = ""


HOME_APPLIANCE_KEYWORDS = ("家电", "冰箱", "电视", "洗衣机", "空调", "热水器", "电器")
THREE_C_KEYWORDS = ("3c", "手机", "电脑", "笔记本", "平板", "数码", "相机", "耳机", "手表")
CAR_KEYWORDS = ("汽车", "车辆", "行驶证", "车架号", "车牌")


def _joined_text(scene_hint: str, fields: dict[str, Any]) -> str:
    parts = [scene_hint]
    for key in ("product_type", "type", "product_name", "brand", "model", "activity_name"):
        value = fields.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).lower()


def classify_audit_category(scene_hint: str, fields: dict[str, Any]) -> CategoryResult:
    text = _joined_text(scene_hint, fields)
    if any(keyword in text for keyword in CAR_KEYWORDS):
        return CategoryResult(scene="unsupported", category="unsupported", supported=False, reason="汽车审核首期不支持")

    is_guobu = "国补" in text or "家电数码3c" in text
    is_no_coupon = "非发券" in text or not is_guobu

    if any(keyword in text for keyword in HOME_APPLIANCE_KEYWORDS):
        return CategoryResult(scene="guobu" if is_guobu else "no_coupon", category="home_appliance", supported=is_guobu)

    if any(keyword in text for keyword in THREE_C_KEYWORDS):
        return CategoryResult(scene="guobu" if is_guobu else "no_coupon", category="3c", supported=True)

    return CategoryResult(
        scene="guobu" if is_guobu else ("no_coupon" if is_no_coupon else "unsupported"),
        category="unsupported",
        supported=False,
        reason="品类未命中首期支持范围",
    )
```

- [ ] **Step 4: Export category classifier**

Append to `C:\audit_robot\modules\__init__.py`:

```python
from .category_classifier import CategoryResult, classify_audit_category
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_category_classifier.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 6: Checkpoint**

If git is available:

```powershell
git add modules/category_classifier.py modules/__init__.py tests/test_category_classifier.py
git commit -m "feat: classify audit scenes and categories"
```

## Task 6: Address Detail Rule

**Files:**

- Modify: `C:\audit_robot\modules\address_checker.py`
- Create: `C:\audit_robot\tests\test_address_checker_detail.py`

- [ ] **Step 1: Write failing address tests**

Create `C:\audit_robot\tests\test_address_checker_detail.py`:

```python
# -*- coding: utf-8 -*-
from modules.address_checker import AddressChecker


def test_detail_address_accepts_building_room():
    result = AddressChecker.is_small_range_address("广东省广州市天河区某路88号1栋2单元101室")

    assert result["status"] == "pass"


def test_detail_address_accepts_village_group():
    result = AddressChecker.is_small_range_address("湖南省长沙市望城区某镇某村三组12号")

    assert result["status"] == "pass"


def test_coarse_address_requires_manual():
    result = AddressChecker.is_small_range_address("广东省广州市天河区某街道")

    assert result["status"] == "review"
    assert "地址不够细" in result["message"]


def test_village_without_group_or_number_requires_manual():
    result = AddressChecker.is_small_range_address("湖南省长沙市望城区某镇某村")

    assert result["status"] == "review"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_address_checker_detail.py -q
```

Expected:

```text
AttributeError: type object 'AddressChecker' has no attribute 'is_small_range_address'
```

- [ ] **Step 3: Add small-range address method**

Add this method inside `AddressChecker` in `C:\audit_robot\modules\address_checker.py`:

```python
    @staticmethod
    def is_small_range_address(address: str | None) -> dict:
        """Return pass only for clearly detailed home-appliance addresses."""
        text = (address or "").strip()
        if len(text) < 12:
            return {"status": "review", "message": "地址不够细：长度不足"}

        building_markers = ("号", "栋", "幢", "单元", "室", "房", "门牌")
        village_markers = ("组", "队", "社", "屯", "号")

        has_building_detail = any(marker in text for marker in building_markers)
        has_village = "村" in text
        has_village_detail = has_village and any(marker in text for marker in village_markers)

        if has_building_detail or has_village_detail:
            return {"status": "pass", "message": "地址达到小范围粒度"}

        coarse_markers = ("省", "市", "区", "县", "镇", "乡", "街道", "村")
        if any(marker in text for marker in coarse_markers):
            return {"status": "review", "message": "地址不够细：仅到行政区划或村级"}

        return {"status": "review", "message": "地址不够细：缺少门牌/楼栋/村组信息"}
```

- [ ] **Step 4: Route existing validation through strict helper for explicit address**

In `AddressChecker.validate_address`, before OCR-text fallback decisions, add:

```python
        if system_address:
            detail_result = AddressChecker.is_small_range_address(system_address)
            if detail_result["status"] != "pass":
                return detail_result
```

Keep existing OCR-based behavior below this block so old callers still work when `system_address` is absent.

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_address_checker_detail.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 6: Run old rule tests if present**

Run:

```powershell
python -m pytest tests -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 7: Checkpoint**

If git is available:

```powershell
git add modules/address_checker.py tests/test_address_checker_detail.py
git commit -m "feat: add strict home appliance address rule"
```

## Task 7: Audit Runner With Fast Path, Slow Path, And Timeout

**Files:**

- Create: `C:\audit_robot\modules\audit_runner.py`
- Create: `C:\audit_robot\tests\test_audit_runner.py`
- Modify: `C:\audit_robot\modules\__init__.py`

- [ ] **Step 1: Write failing orchestration tests using fake dependencies**

Create `C:\audit_robot\tests\test_audit_runner.py`:

```python
# -*- coding: utf-8 -*-
from modules.audit_models import AuditImage, AuditRequest
from modules.audit_runner import AuditDependencies, audit_request


class FakeOCR:
    def __init__(self, fast_texts, slow_texts=None):
        self.fast_texts = fast_texts
        self.slow_texts = slow_texts if slow_texts is not None else fast_texts
        self.tiled_calls = 0

    def extract_text_enhanced(self, path):
        return self.fast_texts

    def extract_text_tiled(self, path):
        self.tiled_calls += 1
        return self.slow_texts

    def extract_text(self, path):
        return self.fast_texts


class FakeForensics:
    def __init__(self, status="pass"):
        self.status = status

    def full_analysis(self, path):
        return {"status": self.status, "message": "ok"}


def make_request(scene_hint="家电数码3C（国补2026）"):
    return AuditRequest(
        jl_order_no="JL123",
        scene_hint=scene_hint,
        fields={"sn": "SN001", "product_type": "3C", "product_name": "手机"},
        images=[
            AuditImage(title="商品照片", path="C:/tmp/product.jpg"),
            AuditImage(title="拆封照片", path="C:/tmp/unbox.jpg"),
            AuditImage(title="SN码采集照片", path="C:/tmp/sn.jpg"),
        ],
    )


def test_fast_path_passes_when_sn_and_roles_match():
    deps = AuditDependencies(ocr=FakeOCR(["SN001"]), forensics=FakeForensics())

    response = audit_request(make_request(), deps=deps, timeout_sec=60)

    assert response.decision == "pass"
    assert response.path == "fast"
    assert response.evidence["sn_match"] is True


def test_slow_path_uses_tiled_ocr_when_fast_sn_misses():
    fake_ocr = FakeOCR(["NOISE"], slow_texts=["SN001"])
    deps = AuditDependencies(ocr=fake_ocr, forensics=FakeForensics())

    response = audit_request(make_request(), deps=deps, timeout_sec=60)

    assert response.decision == "pass"
    assert response.path == "slow"
    assert fake_ocr.tiled_calls >= 1


def test_unknown_image_role_goes_manual():
    request = AuditRequest(
        jl_order_no="JL123",
        scene_hint="家电数码3C（国补2026）",
        fields={"sn": "SN001", "product_type": "3C", "product_name": "手机"},
        images=[AuditImage(title="其他材料", path="C:/tmp/x.jpg")],
    )
    deps = AuditDependencies(ocr=FakeOCR(["SN001"]), forensics=FakeForensics())

    response = audit_request(request, deps=deps, timeout_sec=60)

    assert response.decision == "manual"
    assert "图片角色" in response.manual_reason


def test_forensics_risk_goes_manual():
    deps = AuditDependencies(ocr=FakeOCR(["SN001"]), forensics=FakeForensics(status="suspicious"))

    response = audit_request(make_request(), deps=deps, timeout_sec=60)

    assert response.decision == "manual"
    assert "图片风险" in response.manual_reason
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_audit_runner.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'modules.audit_runner'
```

- [ ] **Step 3: Implement audit runner**

Create `C:\audit_robot\modules\audit_runner.py`:

```python
# -*- coding: utf-8 -*-
"""Service-friendly audit orchestration with fast and slow paths."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit_models import AuditRequest, AuditResponse
from .category_classifier import classify_audit_category
from .code_extractor import CodeExtractor
from .image_forensics import ImageForensics
from .image_role import group_images_by_role, required_roles_present
from .ocr_engine import OCREngine


@dataclass
class AuditDependencies:
    ocr: Any | None = None
    forensics: Any | None = None

    def get_ocr(self) -> Any:
        if self.ocr is None:
            self.ocr = OCREngine()
        return self.ocr

    def get_forensics(self) -> Any:
        if self.forensics is None:
            self.forensics = ImageForensics()
        return self.forensics


def _elapsed(start: float) -> float:
    return time.monotonic() - start


def _timed_out(start: float, timeout_sec: int) -> bool:
    return _elapsed(start) >= timeout_sec


def _image_paths(images) -> list[Path]:
    return [Path(image.path) for image in images]


def _all_forensics_pass(forensics_results: list[dict[str, Any]]) -> bool:
    return all(result.get("status") == "pass" for result in forensics_results)


def _match_sn(texts: list[Any], sn: str) -> dict[str, Any]:
    return CodeExtractor.match_system_sn(texts, sn)


def audit_request(
    request: AuditRequest,
    deps: AuditDependencies | None = None,
    timeout_sec: int = 60,
) -> AuditResponse:
    start = time.monotonic()
    deps = deps or AuditDependencies()
    category = classify_audit_category(request.scene_hint, request.fields)
    scene = category.scene if category.supported else "unsupported"

    if not request.jl_order_no:
        return AuditResponse.manual("", scene, "fast", _elapsed(start), "缺少嘉联订单号")

    if not category.supported:
        return AuditResponse.manual(request.jl_order_no, scene, "fast", _elapsed(start), category.reason or "品类不支持")

    grouped = group_images_by_role(request.images)
    if not required_roles_present(grouped, category.scene):
        return AuditResponse.manual(request.jl_order_no, category.scene, "fast", _elapsed(start), "图片角色不完整或无法识别")

    sn = str(request.fields.get("sn", "")).strip()
    if not sn:
        return AuditResponse.manual(request.jl_order_no, category.scene, "fast", _elapsed(start), "页面SN为空")

    ocr = deps.get_ocr()
    forensics = deps.get_forensics()
    paths = _image_paths(request.images)

    forensics_results = [forensics.full_analysis(path) for path in paths]
    if not _all_forensics_pass(forensics_results):
        return AuditResponse.manual(request.jl_order_no, category.scene, "fast", _elapsed(start), "图片风险未通过")

    fast_texts: list[Any] = []
    for path in paths:
        if _timed_out(start, timeout_sec):
            return AuditResponse.manual(request.jl_order_no, category.scene, "fast", timeout_sec, "单单超时")
        fast_texts.extend(ocr.extract_text_enhanced(path))

    sn_result = _match_sn(fast_texts, sn)
    evidence = {
        "sn_match": bool(sn_result.get("sn_match")),
        "image_roles_ok": True,
        "real_photo_pass": True,
        "id_name_match": None,
        "id_valid": None,
        "address_detail_ok": None,
    }
    if sn_result.get("sn_match"):
        return AuditResponse.pass_(request.jl_order_no, category.scene, "fast", _elapsed(start), evidence)

    slow_texts = list(fast_texts)
    for path in paths:
        if _timed_out(start, timeout_sec):
            return AuditResponse.manual(request.jl_order_no, category.scene, "slow", timeout_sec, "单单超时")
        slow_texts.extend(ocr.extract_text_tiled(path))

    sn_result = _match_sn(slow_texts, sn)
    evidence["sn_match"] = bool(sn_result.get("sn_match"))
    if sn_result.get("sn_match"):
        return AuditResponse.pass_(request.jl_order_no, category.scene, "slow", _elapsed(start), evidence)

    return AuditResponse.manual(request.jl_order_no, category.scene, "slow", _elapsed(start), "SN未高置信识别")
```

- [ ] **Step 4: Export audit runner**

Append to `C:\audit_robot\modules\__init__.py`:

```python
from .audit_runner import AuditDependencies, audit_request
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_audit_runner.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 6: Checkpoint**

If git is available:

```powershell
git add modules/audit_runner.py modules/__init__.py tests/test_audit_runner.py
git commit -m "feat: add fast slow audit runner"
```

## Task 8: Local HTTP Audit Service

**Files:**

- Create: `C:\audit_robot\audit_service.py`
- Create: `C:\audit_robot\tests\test_audit_service.py`

- [ ] **Step 1: Write failing service tests**

Create `C:\audit_robot\tests\test_audit_service.py`:

```python
# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient

import audit_service


def test_health_returns_ok():
    client = TestClient(audit_service.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_audit_rejects_missing_token():
    client = TestClient(audit_service.app)

    response = client.post("/audit", json={"jl_order_no": "JL123", "fields": {}, "images": []})

    assert response.status_code == 401


def test_audit_accepts_token_and_returns_manual_for_missing_images(monkeypatch):
    monkeypatch.setenv("AUDIT_SERVICE_TOKEN", "secret")
    client = TestClient(audit_service.app)

    response = client.post(
        "/audit",
        headers={"X-Audit-Token": "secret"},
        json={
            "jl_order_no": "JL123",
            "scene_hint": "家电数码3C（国补2026）",
            "fields": {"sn": "SN001", "product_type": "3C", "product_name": "手机"},
            "images": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "manual"
    assert body["action"] == "next"
    assert body["jl_order_no"] == "JL123"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_audit_service.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'audit_service'
```

- [ ] **Step 3: Implement FastAPI service**

Create `C:\audit_robot\audit_service.py`:

```python
# -*- coding: utf-8 -*-
"""Local HTTP service for audit automation."""
from __future__ import annotations

import os
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from config import (
    AUDIT_DEFAULT_TOKEN,
    AUDIT_ORDER_TIMEOUT_SEC,
    AUDIT_SERVICE_HOST,
    AUDIT_SERVICE_PORT,
    AUDIT_SERVICE_TOKEN_ENV,
)
from modules.audit_models import AuditRequest, AuditResponse
from modules.audit_runner import AuditDependencies, audit_request


app = FastAPI(title="Local Audit Service", version="0.1.0")
DEPS = AuditDependencies()


def expected_token() -> str:
    return os.environ.get(AUDIT_SERVICE_TOKEN_ENV, AUDIT_DEFAULT_TOKEN)


def verify_token(x_audit_token: str | None) -> None:
    if not x_audit_token or x_audit_token != expected_token():
        raise HTTPException(status_code=401, detail="invalid audit token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/audit")
def audit(payload: dict[str, Any], x_audit_token: str | None = Header(default=None)) -> dict[str, Any]:
    verify_token(x_audit_token)
    started = time.monotonic()
    request = AuditRequest.from_dict(payload)
    try:
        response = audit_request(request, deps=DEPS, timeout_sec=AUDIT_ORDER_TIMEOUT_SEC)
    except Exception as exc:
        response = AuditResponse.error(
            jl_order_no=request.jl_order_no,
            scene="unknown",
            elapsed_sec=time.monotonic() - started,
            manual_reason=f"服务异常: {exc}",
        )
    return response.to_dict()


if __name__ == "__main__":
    uvicorn.run(app, host=AUDIT_SERVICE_HOST, port=AUDIT_SERVICE_PORT)
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_audit_service.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Manually smoke-test service startup**

Run:

```powershell
$env:AUDIT_SERVICE_TOKEN='secret'; python audit_service.py
```

Expected terminal line includes:

```text
Uvicorn running on http://127.0.0.1:8765
```

Stop with `Ctrl+C`.

- [ ] **Step 6: Checkpoint**

If git is available:

```powershell
git add audit_service.py tests/test_audit_service.py
git commit -m "feat: add local audit http service"
```

## Task 9: CLI Compatibility And Report Output

**Files:**

- Modify: `C:\audit_robot\run_audit.py`
- Create: `C:\audit_robot\tests\test_cli_compatibility.py`

- [ ] **Step 1: Write CLI compatibility test for request file output**

Create `C:\audit_robot\tests\test_cli_compatibility.py`:

```python
# -*- coding: utf-8 -*-
import json
import subprocess
import sys


def test_cli_returns_engine_error_for_missing_images(tmp_path):
    request_file = tmp_path / "request.json"
    request_file.write_text(
        json.dumps({
            "system_data": {"jl_order_no": "JL123", "sn": "SN001", "product_type": "3C"},
            "image_urls": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "run_audit.py", "--request_file", str(request_file)],
        cwd="C:/audit_robot",
        text=True,
        capture_output=True,
    )

    assert result.returncode == 3
    body = json.loads(result.stdout)
    assert body["decision"] == "engine_error"
```

- [ ] **Step 2: Run test and record current behavior**

Run:

```powershell
python -m pytest tests/test_cli_compatibility.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 3: Add optional report argument**

In `parse_args()` inside `C:\audit_robot\run_audit.py`, add:

```python
    parser.add_argument(
        "--report",
        help="将脱敏审核结果追加写入指定 CSV 报表",
    )
```

- [ ] **Step 4: Write report row after result creation**

Add imports near existing imports:

```python
from modules.report_writer import append_report_row
from modules.audit_models import normalize_decision
```

After `result = ...` is assigned and before `output_json = ...`, add:

```python
        result["decision"] = normalize_decision(result.get("decision"))
        if args.report:
            append_report_row(args.report, {
                "jl_order_no": system_data.get("jl_order_no") or system_data.get("order_id", ""),
                "channel_order_no": system_data.get("channel_order_no", ""),
                "scene": result.get("scene", args.scene),
                "category": system_data.get("product_type", ""),
                "decision": result.get("decision"),
                "path": result.get("path", ""),
                "elapsed_sec": result.get("elapsed_sec", ""),
                "manual_reason": result.get("manual_reason") or result.get("skip_reason"),
                "sn_match": result.get("codes", {}).get("sn", {}).get("sn_match", ""),
                "image_roles_ok": "",
                "real_photo_pass": result.get("image_forensics", {}).get("status") == "pass",
                "id_name_match": "",
                "id_valid": result.get("id_card", {}).get("is_valid", ""),
                "address_detail_ok": result.get("address_check", {}).get("status") == "pass" if result.get("address_check") else "",
            })
```

- [ ] **Step 5: Run CLI compatibility and report tests**

Run:

```powershell
python -m pytest tests/test_cli_compatibility.py tests/test_report_writer.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 6: Checkpoint**

If git is available:

```powershell
git add run_audit.py tests/test_cli_compatibility.py
git commit -m "feat: add cli report output"
```

## Task 10: Rule Engine Manual Semantics

**Files:**

- Modify: `C:\audit_robot\modules\rule_engine.py`
- Create: `C:\audit_robot\tests\test_rule_engine_manual.py`

- [ ] **Step 1: Write failing manual-normalization tests**

Create `C:\audit_robot\tests\test_rule_engine_manual.py`:

```python
# -*- coding: utf-8 -*-
from modules.audit_models import normalize_decision


def test_skip_is_external_manual():
    assert normalize_decision("skip") == "manual"


def test_error_is_external_error():
    assert normalize_decision("engine_error") == "error"
```

- [ ] **Step 2: Run normalization tests**

Run:

```powershell
python -m pytest tests/test_rule_engine_manual.py tests/test_audit_models.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Add helper to rule engine for future callers**

Inside `RuleEngine` in `C:\audit_robot\modules\rule_engine.py`, add:

```python
    @staticmethod
    def external_decision(decision: str | None) -> str:
        """Map internal rule decisions to external service decisions."""
        if decision == "pass":
            return "pass"
        if decision in ("engine_error", "error"):
            return "error"
        return "manual"
```

- [ ] **Step 4: Add direct test for rule engine helper**

Append to `C:\audit_robot\tests\test_rule_engine_manual.py`:

```python

from modules.rule_engine import RuleEngine


def test_rule_engine_external_decision_helper():
    assert RuleEngine.external_decision("pass") == "pass"
    assert RuleEngine.external_decision("skip") == "manual"
    assert RuleEngine.external_decision("review") == "manual"
    assert RuleEngine.external_decision("engine_error") == "error"
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_rule_engine_manual.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Checkpoint**

If git is available:

```powershell
git add modules/rule_engine.py tests/test_rule_engine_manual.py
git commit -m "feat: normalize external manual decisions"
```

## Task 11: End-To-End Local Smoke Test

**Files:**

- Create: `C:\audit_robot\temp\smoke_request.json` during test execution only.
- No permanent source file changes.

- [ ] **Step 1: Run full unit test suite**

Run:

```powershell
python -m pytest tests -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 2: Start local service**

Run:

```powershell
$env:AUDIT_SERVICE_TOKEN='secret'; python audit_service.py
```

Expected:

```text
Uvicorn running on http://127.0.0.1:8765
```

- [ ] **Step 3: In a second terminal, call health endpoint**

Run:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health'
```

Expected:

```text
status
------
ok
```

- [ ] **Step 4: Call audit endpoint with missing images**

Run:

```powershell
$body = @{
  jl_order_no = 'JL-SMOKE-001'
  scene_hint = '家电数码3C（国补2026）'
  fields = @{
    sn = 'SN001'
    product_type = '3C'
    product_name = '手机'
  }
  images = @()
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri 'http://127.0.0.1:8765/audit' -Method Post -Headers @{ 'X-Audit-Token' = 'secret' } -Body $body -ContentType 'application/json'
```

Expected response includes:

```text
decision      : manual
action        : next
jl_order_no   : JL-SMOKE-001
manual_reason : 图片角色不完整或无法识别
```

- [ ] **Step 5: Confirm unauthorized request is blocked**

Run:

```powershell
try {
  Invoke-RestMethod -Uri 'http://127.0.0.1:8765/audit' -Method Post -Body '{}' -ContentType 'application/json'
} catch {
  $_.Exception.Response.StatusCode.value__
}
```

Expected:

```text
401
```

- [ ] **Step 6: Stop local service**

Press `Ctrl+C` in the service terminal.

Expected:

```text
Application shutdown complete
```

## Task 12: Yingdao Integration Contract Document

**Files:**

- Create: `C:\audit_robot\docs\yingdao-integration-contract.md`

- [ ] **Step 1: Create integration contract**

Create `C:\audit_robot\docs\yingdao-integration-contract.md`:

```markdown
# 影刀接入本地审核服务约定

## 服务地址

`POST http://127.0.0.1:8765/audit`

请求头：

`X-Audit-Token: <本机密钥>`

## 影刀负责

- 读取页面字段。
- 读取嘉联订单号。
- 读取渠道订单号。
- 读取图片标题。
- 下载图片到本地临时目录。
- 调用本地审核服务。
- 当 `decision=pass` 时点击通过。
- 当 `decision=manual` 或 `decision=error` 时点击下一条或暂停，按运行配置执行。

## 影刀禁止

- 不调用云 OCR。
- 不上传身份证、订单、图片到第三方。
- 不自动驳回。
- 不保存图片 URL 到日志。
- 不保存 OCR 原文。

## 请求示例

```json
{
  "jl_order_no": "JL123",
  "channel_order_no": "CH456",
  "scene_hint": "家电数码3C（国补2026）",
  "fields": {
    "name": "张某",
    "product_type": "3C",
    "product_name": "手机",
    "brand": "品牌",
    "model": "型号",
    "sn": "SN001",
    "imei1": "",
    "imei2": "",
    "merchant": "门店",
    "delivery_method": "配送",
    "address": "不写入影刀日志"
  },
  "images": [
    {"title": "商品照片", "path": "C:/audit_robot/temp/order1/product.jpg"},
    {"title": "拆封照片", "path": "C:/audit_robot/temp/order1/unbox.jpg"},
    {"title": "SN码采集照片", "path": "C:/audit_robot/temp/order1/sn.jpg"}
  ]
}
```

## 响应处理

`decision=pass`：

- 点击通过。
- 记录嘉联订单号、耗时、path。

`decision=manual`：

- 点击下一条。
- 记录嘉联订单号、manual_reason、耗时、path。

`decision=error`：

- 首期建议暂停，人工查看服务状态。
- 如果配置为继续，则点击下一条。

## 首期超时

单单最大审核预算为 60 秒。超过 60 秒服务返回 `manual`，影刀不继续等待。
```

- [ ] **Step 2: Check document exists**

Run:

```powershell
Test-Path 'C:\audit_robot\docs\yingdao-integration-contract.md'
```

Expected:

```text
True
```

- [ ] **Step 3: Checkpoint**

If git is available:

```powershell
git add docs/yingdao-integration-contract.md
git commit -m "docs: add yingdao integration contract"
```

## Final Verification

- [ ] **Run all tests**

```powershell
python -m pytest tests -q
```

Expected:

```text
all tests pass
```

- [ ] **Verify no sensitive values in generated report sample**

Run the report tests and inspect the temporary CSV content produced by `test_report_writer.py`. The CSV must include `jl_order_no`, `decision`, `elapsed_sec`, and `manual_reason`; it must not include `image_url`, `ocr_raw_text`, or `address` columns.

- [ ] **Verify service host is loopback**

Run:

```powershell
Select-String -Path 'C:\audit_robot\config.py','C:\audit_robot\audit_service.py' -Pattern '0.0.0.0'
```

Expected: no matches.

- [ ] **Verify timeout is 60 seconds**

Run:

```powershell
Select-String -Path 'C:\audit_robot\config.py','C:\audit_robot\modules\audit_runner.py' -Pattern '60|AUDIT_ORDER_TIMEOUT_SEC'
```

Expected: config contains `AUDIT_ORDER_TIMEOUT_SEC = 60`, and service/runner uses that value.

## Rollout Procedure

1. Run all unit tests.
2. Start `audit_service.py` locally with `AUDIT_SERVICE_TOKEN`.
3. Give Yingdao only the service URL, token, request schema, and response handling contract.
4. Run observation mode for at least one day: service returns decisions, Yingdao does not click approve automatically.
5. Compare reports with manual review results.
6. Enable auto-approve only for `decision=pass`.
7. Keep `manual` and `error` as next-item or pause behavior, never auto-reject.
8. Review report daily for pass rate, timeout count, slow path count, and manual reason distribution.

## Plan Self-Review

- Spec coverage: business boundaries, local service, token, loopback binding, fast/slow path, 60-second timeout, privacy-safe logs, image role grouping, category classification, address detail rules, Yingdao contract, and Playwright-replaceable interface all map to tasks above.
- Placeholder scan: this plan contains concrete paths, commands, expected outputs, and code snippets for each implementation task.
- Type consistency: request/response fields use `jl_order_no`, `scene_hint`, `fields`, `images`, `decision`, `action`, `manual_reason`, `elapsed_sec`, and `evidence` consistently across model, runner, service, report, and Yingdao contract tasks.
