# AI Audit Shadow Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable Guobu audit shadow-run pipeline: import sampled orders, call a pluggable multimodal model adapter, apply local conservative rules, and export a judgment table with channel order number, manual flag, and manual reason.

**Architecture:** The first milestone is local and does not depend on backend engineering support. It uses JSON task files and SQLite as the task queue, a versioned prompt file, a model adapter boundary, a strict JSON schema validator, a local rule decider, and CSV judgment reports. Browser collection and formal backend task-table integration are separate follow-up tasks that consume the same task schema.

**Tech Stack:** Python 3.12, SQLite stdlib, `dataclasses`, `json`, `csv`, `httpx` for optional OpenAI-compatible vision API calls, `pytest` for tests. No new required runtime dependency for the core MVP.

## Global Constraints

- 首期目标：国补家电数码/3C 影子试跑，不改订单状态，不自动点击通过，不自动驳回。
- 最终目标：通过后台数据库/API 任务表接入，实现高置信订单自动通过，疑单转人工并给出原因。
- 核心原则：模型负责提取证据，本地规则负责裁决；宁可多转人工，不允许误通过。
- 首期范围：国补家电数码/3C；非发券身份证场景后置。
- 数据边界：国补商品/SN 图片可以调用国内多模态 API；身份证图片不得上传云端。
- 判断表最小列：渠道订单号、是否转人工、转人工原因。
- 任何采集失败、模型 JSON 异常、字段缺失，默认转人工。
- 每次模型调用必须记录模型名称、模型版本、Prompt 版本、耗时、成本和输出摘要。
- 首期每天处理 100-200 单，累计先跑 200-500 单形成第一轮评估。
- 不改现有本地 OCR 主链路；新能力以 `shadow_*` 模块隔离，避免破坏当前审核引擎。

---

## Scope Check

The requirements document covers three subsystems:

1. Local shadow-run MVP.
2. Browser/manual sample collection.
3. Formal backend task-table integration.

This plan makes the local shadow-run MVP the executable core. Browser collection is implemented after the queue and report pipeline exist. Backend integration is planned as a contract task, because production database type, backend framework, and frontend codebase are outside this repository.

## File Structure

- Create `modules/shadow_models.py`: dataclasses, reason codes, task/evidence/result normalization.
- Create `modules/shadow_queue.py`: SQLite schema, enqueue, claim, complete, fail, list completed tasks.
- Create `modules/model_schema.py`: strict validation for multimodal model JSON output.
- Create `modules/shadow_decider.py`: conservative local rule engine for `pass_candidate` vs `manual`.
- Create `modules/shadow_report.py`: CSV judgment table writer with Chinese business columns.
- Create `modules/vision_adapters.py`: model adapter protocol, fixture adapter, OpenAI-compatible HTTP adapter.
- Create `tools/import_shadow_tasks.py`: import task JSON files into SQLite.
- Create `tools/run_shadow_audit.py`: process pending tasks and write results.
- Create `tools/export_shadow_report.py`: export completed results to CSV.
- Create `prompts/guobu_v20260628.json`: versioned prompt config.
- Create `docs/backend/ai_audit_task_contract.md`: formal backend table/API contract.
- Create tests:
  - `tests/test_shadow_models.py`
  - `tests/test_shadow_queue.py`
  - `tests/test_model_schema.py`
  - `tests/test_shadow_decider.py`
  - `tests/test_shadow_report.py`
  - `tests/test_shadow_runner.py`

## Task Dependencies

```text
Task 1 shadow models
  -> Task 2 queue
  -> Task 3 schema validator
  -> Task 4 decider
  -> Task 5 report
  -> Task 6 import/run/export CLI
  -> Task 7 prompt and fixture sample workflow
  -> Task 8 browser collector scaffold
  -> Task 9 backend contract
```

### Task 1: Shadow Data Contracts

**Files:**
- Create: `modules/shadow_models.py`
- Test: `tests/test_shadow_models.py`

**Interfaces:**
- Produces: `ShadowImage`, `ShadowTask`, `SnCandidate`, `ImageRisk`, `ModelEvidence`, `DecisionResult`
- Produces: `REASON_CODES: dict[str, str]`
- Produces: `ShadowTask.from_dict(data: dict) -> ShadowTask`
- Produces: `DecisionResult.to_record() -> dict[str, object]`
- Consumed by: queue, schema validator, decider, report, runner

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shadow_models.py`:

```python
# -*- coding: utf-8 -*-
from modules.shadow_models import DecisionResult, ShadowTask


def test_shadow_task_from_requirement_json():
    task = ShadowTask.from_dict(
        {
            "task_id": "20260628-000001",
            "channel_order_no": "QD202606280001",
            "scene": "guobu",
            "fields": {
                "product_type": "手机",
                "product_name": "iPhone",
                "brand": "Apple",
                "model": "17 Pro Max",
                "system_sn": "ABC123",
            },
            "images": [
                {
                    "image_id": "img_001",
                    "title": "SN码采集/激活照片",
                    "local_path": "data/images/20260628-000001/img_001.jpg",
                }
            ],
            "source": {"collector": "manual"},
        }
    )

    assert task.task_id == "20260628-000001"
    assert task.channel_order_no == "QD202606280001"
    assert task.system_sn == "ABC123"
    assert task.images[0].path == "data/images/20260628-000001/img_001.jpg"
    assert task.image_count == 1


def test_decision_result_to_record_uses_chinese_manual_flag():
    result = DecisionResult.manual(
        task_id="20260628-000002",
        channel_order_no="QD202606280002",
        reason_code="SN_NOT_FOUND",
        evidence={"sn_found": False},
    )

    record = result.to_record()

    assert record["channel_order_no"] == "QD202606280002"
    assert record["是否转人工"] == "是"
    assert record["转人工原因"] == "SN_NOT_FOUND"
    assert record["decision"] == "manual"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_shadow_models.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'modules.shadow_models'`.

- [ ] **Step 3: Implement the data contracts**

Create `modules/shadow_models.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


REASON_CODES: dict[str, str] = {
    "FIELD_MISSING": "页面关键字段缺失",
    "SYSTEM_SN_MISSING": "系统SN缺失",
    "IMAGE_MISSING": "商品/SN相关图片缺失",
    "IMAGE_DOWNLOAD_FAILED": "图片下载失败",
    "MODEL_JSON_INVALID": "模型输出格式异常",
    "MODEL_TIMEOUT": "模型调用超时",
    "MODEL_ERROR": "模型接口错误",
    "SN_NOT_FOUND": "模型未识别到SN",
    "SN_LOW_CONFIDENCE": "SN置信度不足",
    "SN_MISMATCH": "SN与系统不一致",
    "SN_CONFLICT": "多张图识别出冲突SN",
    "IMAGE_STRONG_RISK": "图片存在强截图/翻拍/P图/拼图风险",
    "UNSUPPORTED_CATEGORY": "品类暂不支持",
    "COLLECTOR_ERROR": "采集器异常",
    "PASS_CANDIDATE": "证据链完整，可作为自动通过候选",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_sn(value: Any) -> str:
    return _text(value).replace(" ", "").replace("-", "").upper()


@dataclass(frozen=True)
class ShadowImage:
    image_id: str
    title: str
    path: str

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int) -> "ShadowImage":
        return cls(
            image_id=_text(data.get("image_id")) or f"img_{index + 1:03d}",
            title=_text(data.get("title")),
            path=_text(data.get("local_path") or data.get("path") or data.get("file_path")),
        )


@dataclass(frozen=True)
class ShadowTask:
    task_id: str
    channel_order_no: str
    scene: str
    fields: dict[str, Any] = field(default_factory=dict)
    images: list[ShadowImage] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShadowTask":
        fields = dict(data.get("fields") or {})
        images = [
            ShadowImage.from_dict(image, index)
            for index, image in enumerate(data.get("images") or [])
        ]
        return cls(
            task_id=_text(data.get("task_id")),
            channel_order_no=_text(data.get("channel_order_no")),
            scene=_text(data.get("scene")) or "guobu",
            fields=fields,
            images=images,
            source=dict(data.get("source") or {}),
        )

    @property
    def system_sn(self) -> str:
        return normalize_sn(
            self.fields.get("system_sn")
            or self.fields.get("sn")
            or self.fields.get("SN")
            or self.fields.get("serial_no")
        )

    @property
    def image_count(self) -> int:
        return len(self.images)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "channel_order_no": self.channel_order_no,
            "scene": self.scene,
            "fields": dict(self.fields),
            "images": [asdict(image) for image in self.images],
            "source": dict(self.source),
        }


@dataclass(frozen=True)
class SnCandidate:
    image_id: str
    value: str
    confidence: str
    evidence_text: str = ""

    @property
    def normalized_value(self) -> str:
        return normalize_sn(self.value)


@dataclass(frozen=True)
class ImageRisk:
    image_id: str
    risk: str
    risk_level: str
    reason: str = ""


@dataclass(frozen=True)
class ModelEvidence:
    schema_version: str
    sn_candidates: list[SnCandidate]
    image_risks: list[ImageRisk]
    conflicts: list[str] = field(default_factory=list)
    product_evidence: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult:
    task_id: str
    channel_order_no: str
    decision: str
    manual_required: bool
    manual_reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    model_name: str = ""
    model_version: str = ""
    prompt_version: str = ""
    cost_cny: float = 0.0
    elapsed_ms: int = 0

    @classmethod
    def pass_candidate(
        cls,
        task_id: str,
        channel_order_no: str,
        evidence: dict[str, Any],
    ) -> "DecisionResult":
        return cls(
            task_id=task_id,
            channel_order_no=channel_order_no,
            decision="pass_candidate",
            manual_required=False,
            manual_reason="",
            evidence=dict(evidence),
        )

    @classmethod
    def manual(
        cls,
        task_id: str,
        channel_order_no: str,
        reason_code: str,
        evidence: dict[str, Any],
    ) -> "DecisionResult":
        return cls(
            task_id=task_id,
            channel_order_no=channel_order_no,
            decision="manual",
            manual_required=True,
            manual_reason=reason_code,
            evidence=dict(evidence),
        )

    def with_runtime(
        self,
        *,
        model_name: str = "",
        model_version: str = "",
        prompt_version: str = "",
        cost_cny: float = 0.0,
        elapsed_ms: int = 0,
    ) -> "DecisionResult":
        return DecisionResult(
            task_id=self.task_id,
            channel_order_no=self.channel_order_no,
            decision=self.decision,
            manual_required=self.manual_required,
            manual_reason=self.manual_reason,
            evidence=dict(self.evidence),
            model_name=model_name,
            model_version=model_version,
            prompt_version=prompt_version,
            cost_cny=cost_cny,
            elapsed_ms=elapsed_ms,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "channel_order_no": self.channel_order_no,
            "渠道订单号": self.channel_order_no,
            "是否转人工": "是" if self.manual_required else "否",
            "转人工原因": self.manual_reason,
            "decision": self.decision,
            "manual_required": int(self.manual_required),
            "manual_reason": self.manual_reason,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "cost_cny": self.cost_cny,
            "elapsed_ms": self.elapsed_ms,
            "evidence": dict(self.evidence),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_shadow_models.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add modules/shadow_models.py tests/test_shadow_models.py
git commit -m "feat: add shadow audit data contracts"
```

### Task 2: SQLite Shadow Task Queue

**Files:**
- Create: `modules/shadow_queue.py`
- Test: `tests/test_shadow_queue.py`

**Interfaces:**
- Consumes: `ShadowTask`, `DecisionResult`
- Produces: `init_db(db_path: str | Path) -> None`
- Produces: `enqueue_task(db_path: str | Path, task: ShadowTask, collection_status: str = "ok") -> None`
- Produces: `claim_next_task(db_path: str | Path) -> ShadowTask | None`
- Produces: `complete_task(db_path: str | Path, result: DecisionResult) -> None`
- Produces: `fail_task(db_path: str | Path, task_id: str, error_message: str) -> None`
- Produces: `list_completed_results(db_path: str | Path) -> list[DecisionResult]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shadow_queue.py`:

```python
# -*- coding: utf-8 -*-
from modules.shadow_models import DecisionResult, ShadowTask
from modules.shadow_queue import (
    claim_next_task,
    complete_task,
    enqueue_task,
    init_db,
    list_completed_results,
)


def sample_task() -> ShadowTask:
    return ShadowTask.from_dict(
        {
            "task_id": "20260628-000001",
            "channel_order_no": "QD202606280001",
            "scene": "guobu",
            "fields": {"system_sn": "ABC123"},
            "images": [{"image_id": "img_001", "title": "SN照片", "local_path": "a.jpg"}],
        }
    )


def test_enqueue_claim_complete_round_trip(tmp_path):
    db_path = tmp_path / "shadow.db"
    init_db(db_path)
    enqueue_task(db_path, sample_task())

    claimed = claim_next_task(db_path)

    assert claimed is not None
    assert claimed.task_id == "20260628-000001"

    complete_task(
        db_path,
        DecisionResult.pass_candidate(
            task_id=claimed.task_id,
            channel_order_no=claimed.channel_order_no,
            evidence={"sn_match": True},
        ),
    )
    results = list_completed_results(db_path)

    assert len(results) == 1
    assert results[0].decision == "pass_candidate"
    assert results[0].manual_required is False


def test_enqueue_ignores_duplicate_channel_order(tmp_path):
    db_path = tmp_path / "shadow.db"
    init_db(db_path)
    enqueue_task(db_path, sample_task())
    enqueue_task(db_path, sample_task())

    first = claim_next_task(db_path)
    second = claim_next_task(db_path)

    assert first is not None
    assert second is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_shadow_queue.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'modules.shadow_queue'`.

- [ ] **Step 3: Implement the SQLite queue**

Create `modules/shadow_queue.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .shadow_models import DecisionResult, ShadowTask


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT UNIQUE NOT NULL,
  channel_order_no TEXT NOT NULL,
  scene TEXT NOT NULL,
  fields_json TEXT NOT NULL,
  images_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  collection_status TEXT NOT NULL DEFAULT 'ok',
  model_name TEXT,
  model_version TEXT,
  prompt_version TEXT,
  decision TEXT,
  manual_required INTEGER,
  manual_reason TEXT,
  evidence_json TEXT,
  cost_cny REAL,
  elapsed_ms INTEGER,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_audit_task_channel_order_no
ON audit_task(channel_order_no);

CREATE INDEX IF NOT EXISTS idx_audit_task_status_created
ON audit_task(status, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


def enqueue_task(db_path: str | Path, task: ShadowTask, collection_status: str = "ok") -> None:
    init_db(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO audit_task (
                task_id, channel_order_no, scene, fields_json, images_json,
                status, collection_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                task.task_id,
                task.channel_order_no,
                task.scene,
                json.dumps(task.fields, ensure_ascii=False),
                json.dumps([image.__dict__ for image in task.images], ensure_ascii=False),
                collection_status,
                now,
                now,
            ),
        )


def _row_to_task(row: sqlite3.Row) -> ShadowTask:
    return ShadowTask.from_dict(
        {
            "task_id": row["task_id"],
            "channel_order_no": row["channel_order_no"],
            "scene": row["scene"],
            "fields": json.loads(row["fields_json"]),
            "images": json.loads(row["images_json"]),
        }
    )


def claim_next_task(db_path: str | Path) -> ShadowTask | None:
    init_db(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
            FROM audit_task
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE audit_task SET status = 'processing', updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        conn.commit()
        return _row_to_task(row)


def complete_task(db_path: str | Path, result: DecisionResult) -> None:
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE audit_task
            SET status = 'done',
                model_name = ?,
                model_version = ?,
                prompt_version = ?,
                decision = ?,
                manual_required = ?,
                manual_reason = ?,
                evidence_json = ?,
                cost_cny = ?,
                elapsed_ms = ?,
                error_message = NULL,
                updated_at = ?
            WHERE task_id = ?
            """,
            (
                result.model_name,
                result.model_version,
                result.prompt_version,
                result.decision,
                int(result.manual_required),
                result.manual_reason,
                json.dumps(result.evidence, ensure_ascii=False),
                result.cost_cny,
                result.elapsed_ms,
                now,
                result.task_id,
            ),
        )


def fail_task(db_path: str | Path, task_id: str, error_message: str) -> None:
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE audit_task
            SET status = 'failed', error_message = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (error_message, now, task_id),
        )


def list_completed_results(db_path: str | Path) -> list[DecisionResult]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM audit_task
            WHERE status = 'done'
            ORDER BY updated_at ASC, id ASC
            """
        ).fetchall()
    results: list[DecisionResult] = []
    for row in rows:
        results.append(
            DecisionResult(
                task_id=row["task_id"],
                channel_order_no=row["channel_order_no"],
                decision=row["decision"] or "manual",
                manual_required=bool(row["manual_required"]),
                manual_reason=row["manual_reason"] or "",
                evidence=json.loads(row["evidence_json"] or "{}"),
                model_name=row["model_name"] or "",
                model_version=row["model_version"] or "",
                prompt_version=row["prompt_version"] or "",
                cost_cny=float(row["cost_cny"] or 0),
                elapsed_ms=int(row["elapsed_ms"] or 0),
            )
        )
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_shadow_queue.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add modules/shadow_queue.py tests/test_shadow_queue.py
git commit -m "feat: add sqlite shadow audit queue"
```

### Task 3: Model JSON Schema Validator

**Files:**
- Create: `modules/model_schema.py`
- Test: `tests/test_model_schema.py`

**Interfaces:**
- Consumes: `ModelEvidence`, `SnCandidate`, `ImageRisk`
- Produces: `ModelSchemaError(ValueError)`
- Produces: `parse_model_evidence(raw: object) -> ModelEvidence`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_model_schema.py`:

```python
# -*- coding: utf-8 -*-
import pytest

from modules.model_schema import ModelSchemaError, parse_model_evidence


def test_parse_valid_model_evidence():
    evidence = parse_model_evidence(
        {
            "schema_version": "1.0",
            "sn_candidates": [
                {
                    "image_id": "img_001",
                    "value": "ABC123",
                    "confidence": "high",
                    "evidence_text": "SN ABC123",
                }
            ],
            "image_risks": [
                {
                    "image_id": "img_001",
                    "risk": "none",
                    "risk_level": "none",
                    "reason": "",
                }
            ],
            "conflicts": [],
            "product_evidence": {"brand_seen": True},
            "summary": "ok",
        }
    )

    assert evidence.sn_candidates[0].value == "ABC123"
    assert evidence.image_risks[0].risk_level == "none"


def test_reject_natural_language_output():
    with pytest.raises(ModelSchemaError, match="model output must be a JSON object"):
        parse_model_evidence("SN一致，可以通过")


def test_reject_invalid_confidence():
    with pytest.raises(ModelSchemaError, match="invalid confidence"):
        parse_model_evidence(
            {
                "sn_candidates": [{"image_id": "img_001", "value": "ABC123", "confidence": "sure"}],
                "image_risks": [],
                "conflicts": [],
            }
        )


def test_reject_invalid_risk_level():
    with pytest.raises(ModelSchemaError, match="invalid risk_level"):
        parse_model_evidence(
            {
                "sn_candidates": [],
                "image_risks": [{"image_id": "img_001", "risk": "screen", "risk_level": "bad"}],
                "conflicts": [],
            }
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_model_schema.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'modules.model_schema'`.

- [ ] **Step 3: Implement schema validation**

Create `modules/model_schema.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from .shadow_models import ImageRisk, ModelEvidence, SnCandidate


VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_RISK_LEVEL = {"none", "weak", "strong"}


class ModelSchemaError(ValueError):
    pass


def _require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelSchemaError(f"{name} must be a JSON object")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ModelSchemaError(f"{name} must be a list")
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_model_evidence(raw: object) -> ModelEvidence:
    if not isinstance(raw, dict):
        raise ModelSchemaError("model output must be a JSON object")

    sn_values = _require_list(raw.get("sn_candidates"), "sn_candidates")
    risk_values = _require_list(raw.get("image_risks"), "image_risks")
    conflict_values = _require_list(raw.get("conflicts"), "conflicts")

    sn_candidates: list[SnCandidate] = []
    for item in sn_values:
        data = _require_dict(item, "sn_candidate")
        confidence = _text(data.get("confidence"))
        if confidence not in VALID_CONFIDENCE:
            raise ModelSchemaError(f"invalid confidence: {confidence}")
        sn_candidates.append(
            SnCandidate(
                image_id=_text(data.get("image_id")),
                value=_text(data.get("value")),
                confidence=confidence,
                evidence_text=_text(data.get("evidence_text")),
            )
        )

    image_risks: list[ImageRisk] = []
    for item in risk_values:
        data = _require_dict(item, "image_risk")
        risk_level = _text(data.get("risk_level"))
        if risk_level not in VALID_RISK_LEVEL:
            raise ModelSchemaError(f"invalid risk_level: {risk_level}")
        image_risks.append(
            ImageRisk(
                image_id=_text(data.get("image_id")),
                risk=_text(data.get("risk")),
                risk_level=risk_level,
                reason=_text(data.get("reason")),
            )
        )

    product_evidence = raw.get("product_evidence") or {}
    if not isinstance(product_evidence, dict):
        raise ModelSchemaError("product_evidence must be a JSON object")

    return ModelEvidence(
        schema_version=_text(raw.get("schema_version")) or "1.0",
        sn_candidates=sn_candidates,
        image_risks=image_risks,
        conflicts=[_text(value) for value in conflict_values if _text(value)],
        product_evidence=product_evidence,
        summary=_text(raw.get("summary")),
        raw=dict(raw),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_model_schema.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add modules/model_schema.py tests/test_model_schema.py
git commit -m "feat: validate vision model evidence schema"
```

### Task 4: Conservative Shadow Rule Decider

**Files:**
- Create: `modules/shadow_decider.py`
- Test: `tests/test_shadow_decider.py`

**Interfaces:**
- Consumes: `ShadowTask`, `ModelEvidence`, `DecisionResult`
- Produces: `decide_shadow_task(task: ShadowTask, evidence: ModelEvidence | None, collection_status: str = "ok", model_error: str = "") -> DecisionResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shadow_decider.py`:

```python
# -*- coding: utf-8 -*-
from modules.model_schema import parse_model_evidence
from modules.shadow_decider import decide_shadow_task
from modules.shadow_models import ShadowTask


def task(system_sn="ABC123", images=True):
    return ShadowTask.from_dict(
        {
            "task_id": "20260628-000001",
            "channel_order_no": "QD202606280001",
            "scene": "guobu",
            "fields": {"system_sn": system_sn, "product_type": "手机"},
            "images": [{"image_id": "img_001", "title": "SN照片", "local_path": "a.jpg"}] if images else [],
        }
    )


def evidence(sn="ABC123", confidence="high", risk_level="none", conflicts=None):
    return parse_model_evidence(
        {
            "sn_candidates": [{"image_id": "img_001", "value": sn, "confidence": confidence}],
            "image_risks": [{"image_id": "img_001", "risk": "none", "risk_level": risk_level}],
            "conflicts": conflicts or [],
        }
    )


def test_pass_candidate_when_high_confidence_sn_matches_and_no_risk():
    result = decide_shadow_task(task(), evidence())

    assert result.decision == "pass_candidate"
    assert result.manual_required is False
    assert result.evidence["sn_match"] is True


def test_manual_when_system_sn_missing():
    result = decide_shadow_task(task(system_sn=""), evidence())

    assert result.decision == "manual"
    assert result.manual_reason == "SYSTEM_SN_MISSING"


def test_manual_when_sn_not_found():
    result = decide_shadow_task(
        task(),
        parse_model_evidence({"sn_candidates": [], "image_risks": [], "conflicts": []}),
    )

    assert result.manual_reason == "SN_NOT_FOUND"


def test_manual_when_sn_low_confidence():
    result = decide_shadow_task(task(), evidence(confidence="medium"))

    assert result.manual_reason == "SN_LOW_CONFIDENCE"


def test_manual_when_sn_mismatch():
    result = decide_shadow_task(task(), evidence(sn="XYZ789"))

    assert result.manual_reason == "SN_MISMATCH"


def test_manual_when_strong_image_risk():
    result = decide_shadow_task(task(), evidence(risk_level="strong"))

    assert result.manual_reason == "IMAGE_STRONG_RISK"


def test_manual_when_model_error():
    result = decide_shadow_task(task(), None, model_error="timeout")

    assert result.manual_reason == "MODEL_ERROR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_shadow_decider.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'modules.shadow_decider'`.

- [ ] **Step 3: Implement the decider**

Create `modules/shadow_decider.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from .shadow_models import DecisionResult, ModelEvidence, ShadowTask, normalize_sn


def _base_evidence(task: ShadowTask) -> dict[str, object]:
    return {
        "field_ok": bool(task.channel_order_no and task.system_sn),
        "sn_found": False,
        "sn_match": False,
        "sn_conflict": False,
        "strong_image_risk": False,
        "model_json_valid": False,
        "model_timeout": False,
        "image_count": task.image_count,
    }


def _manual(task: ShadowTask, reason: str, evidence: dict[str, object]) -> DecisionResult:
    return DecisionResult.manual(
        task_id=task.task_id,
        channel_order_no=task.channel_order_no,
        reason_code=reason,
        evidence=evidence,
    )


def decide_shadow_task(
    task: ShadowTask,
    evidence: ModelEvidence | None,
    collection_status: str = "ok",
    model_error: str = "",
) -> DecisionResult:
    result_evidence = _base_evidence(task)

    if collection_status != "ok":
        return _manual(task, collection_status, result_evidence)
    if not task.channel_order_no:
        return _manual(task, "FIELD_MISSING", result_evidence)
    if not task.system_sn:
        return _manual(task, "SYSTEM_SN_MISSING", result_evidence)
    if not task.images:
        return _manual(task, "IMAGE_MISSING", result_evidence)
    if model_error:
        result_evidence["model_error"] = model_error
        return _manual(task, "MODEL_ERROR", result_evidence)
    if evidence is None:
        return _manual(task, "MODEL_JSON_INVALID", result_evidence)

    result_evidence["model_json_valid"] = True

    if evidence.conflicts:
        result_evidence["sn_conflict"] = True
        result_evidence["conflicts"] = evidence.conflicts
        return _manual(task, "SN_CONFLICT", result_evidence)

    strong_risks = [risk for risk in evidence.image_risks if risk.risk_level == "strong"]
    if strong_risks:
        result_evidence["strong_image_risk"] = True
        result_evidence["strong_risk_reasons"] = [risk.reason for risk in strong_risks]
        return _manual(task, "IMAGE_STRONG_RISK", result_evidence)

    if not evidence.sn_candidates:
        return _manual(task, "SN_NOT_FOUND", result_evidence)

    high_candidates = [
        candidate for candidate in evidence.sn_candidates
        if candidate.confidence == "high" and candidate.normalized_value
    ]
    result_evidence["sn_found"] = bool(high_candidates)
    result_evidence["model_sn_values"] = [candidate.normalized_value for candidate in evidence.sn_candidates]

    if not high_candidates:
        return _manual(task, "SN_LOW_CONFIDENCE", result_evidence)

    system_sn = normalize_sn(task.system_sn)
    matching = [candidate for candidate in high_candidates if candidate.normalized_value == system_sn]
    conflicting = [candidate for candidate in high_candidates if candidate.normalized_value != system_sn]

    if conflicting and not matching:
        return _manual(task, "SN_MISMATCH", result_evidence)
    if conflicting and matching:
        result_evidence["sn_conflict"] = True
        return _manual(task, "SN_CONFLICT", result_evidence)

    result_evidence["sn_match"] = True
    result_evidence["matched_sn"] = system_sn
    return DecisionResult.pass_candidate(
        task_id=task.task_id,
        channel_order_no=task.channel_order_no,
        evidence=result_evidence,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_shadow_decider.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add modules/shadow_decider.py tests/test_shadow_decider.py
git commit -m "feat: add conservative shadow audit decider"
```

### Task 5: Judgment CSV Report

**Files:**
- Create: `modules/shadow_report.py`
- Test: `tests/test_shadow_report.py`

**Interfaces:**
- Consumes: `DecisionResult`
- Produces: `SHADOW_REPORT_COLUMNS: list[str]`
- Produces: `write_shadow_report(report_path: str | Path, results: Iterable[DecisionResult]) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shadow_report.py`:

```python
# -*- coding: utf-8 -*-
import csv

from modules.shadow_models import DecisionResult
from modules.shadow_report import write_shadow_report


def test_write_shadow_report_with_required_business_columns(tmp_path):
    report_path = tmp_path / "judgment.csv"
    write_shadow_report(
        report_path,
        [
            DecisionResult.pass_candidate(
                task_id="20260628-000001",
                channel_order_no="QD202606280001",
                evidence={"sn_match": True, "matched_sn": "ABC123"},
            ).with_runtime(model_name="fixture", prompt_version="guobu_v20260628", elapsed_ms=12),
            DecisionResult.manual(
                task_id="20260628-000002",
                channel_order_no="QD202606280002",
                reason_code="SN_NOT_FOUND",
                evidence={"sn_found": False},
            ),
        ],
    )

    with report_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    assert rows[0]["渠道订单号"] == "QD202606280001"
    assert rows[0]["是否转人工"] == "否"
    assert rows[0]["转人工原因"] == ""
    assert rows[1]["是否转人工"] == "是"
    assert rows[1]["转人工原因"] == "SN_NOT_FOUND"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_shadow_report.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'modules.shadow_report'`.

- [ ] **Step 3: Implement report writer**

Create `modules/shadow_report.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .shadow_models import DecisionResult


SHADOW_REPORT_COLUMNS = [
    "渠道订单号",
    "是否转人工",
    "转人工原因",
    "AI判断",
    "系统SN",
    "模型识别SN",
    "图片风险",
    "模型名称",
    "模型版本",
    "Prompt版本",
    "耗时ms",
    "单单成本元",
]


def _report_row(result: DecisionResult) -> dict[str, object]:
    evidence = result.evidence
    model_sn_values = evidence.get("model_sn_values") or []
    if isinstance(model_sn_values, list):
        model_sn = "|".join(str(value) for value in model_sn_values)
    else:
        model_sn = str(model_sn_values)
    return {
        "渠道订单号": result.channel_order_no,
        "是否转人工": "是" if result.manual_required else "否",
        "转人工原因": result.manual_reason,
        "AI判断": result.decision,
        "系统SN": evidence.get("matched_sn", ""),
        "模型识别SN": model_sn,
        "图片风险": "strong" if evidence.get("strong_image_risk") else "none",
        "模型名称": result.model_name,
        "模型版本": result.model_version,
        "Prompt版本": result.prompt_version,
        "耗时ms": result.elapsed_ms,
        "单单成本元": result.cost_cny,
    }


def write_shadow_report(report_path: str | Path, results: Iterable[DecisionResult]) -> None:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SHADOW_REPORT_COLUMNS)
        writer.writeheader()
        for result in results:
            writer.writerow(_report_row(result))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_shadow_report.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add modules/shadow_report.py tests/test_shadow_report.py
git commit -m "feat: add shadow judgment csv report"
```

### Task 6: Model Adapters and Batch Runner

**Files:**
- Create: `modules/vision_adapters.py`
- Create: `tools/import_shadow_tasks.py`
- Create: `tools/run_shadow_audit.py`
- Create: `tools/export_shadow_report.py`
- Test: `tests/test_shadow_runner.py`

**Interfaces:**
- Consumes: queue, schema validator, decider, report
- Produces: `VisionAdapter` protocol
- Produces: `FixtureVisionAdapter(fixtures_dir: str | Path)`
- Produces: `OpenAICompatibleVisionAdapter(api_base_url: str, api_key: str, model_name: str, prompt: str)`
- Produces: CLI import/run/export workflow

- [ ] **Step 1: Write the failing runner test**

Create `tests/test_shadow_runner.py`:

```python
# -*- coding: utf-8 -*-
import json

from modules.shadow_queue import enqueue_task, init_db, list_completed_results
from modules.shadow_models import ShadowTask
from modules.vision_adapters import FixtureVisionAdapter
from tools.run_shadow_audit import run_pending_tasks


def test_run_pending_tasks_with_fixture_adapter(tmp_path):
    db_path = tmp_path / "shadow.db"
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    init_db(db_path)
    task = ShadowTask.from_dict(
        {
            "task_id": "20260628-000001",
            "channel_order_no": "QD202606280001",
            "scene": "guobu",
            "fields": {"system_sn": "ABC123"},
            "images": [{"image_id": "img_001", "title": "SN照片", "local_path": "a.jpg"}],
        }
    )
    enqueue_task(db_path, task)
    (fixtures_dir / "20260628-000001.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "sn_candidates": [{"image_id": "img_001", "value": "ABC123", "confidence": "high"}],
                "image_risks": [{"image_id": "img_001", "risk": "none", "risk_level": "none"}],
                "conflicts": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    processed = run_pending_tasks(
        db_path=db_path,
        adapter=FixtureVisionAdapter(fixtures_dir),
        prompt_version="guobu_v20260628",
        limit=10,
    )
    results = list_completed_results(db_path)

    assert processed == 1
    assert results[0].decision == "pass_candidate"
    assert results[0].model_name == "fixture"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest tests/test_shadow_runner.py -v
```

Expected: FAIL with missing `modules.vision_adapters` or `tools.run_shadow_audit`.

- [ ] **Step 3: Implement vision adapters**

Create `modules/vision_adapters.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Protocol

import httpx

from .shadow_models import ShadowTask


class VisionAdapter(Protocol):
    model_name: str
    model_version: str

    def analyze(self, task: ShadowTask) -> tuple[dict, int, float]:
        """Return raw model JSON, elapsed_ms, cost_cny."""


class FixtureVisionAdapter:
    model_name = "fixture"
    model_version = "local"

    def __init__(self, fixtures_dir: str | Path):
        self.fixtures_dir = Path(fixtures_dir)

    def analyze(self, task: ShadowTask) -> tuple[dict, int, float]:
        path = self.fixtures_dir / f"{task.task_id}.json"
        started = time.monotonic()
        raw = json.loads(path.read_text(encoding="utf-8"))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return raw, elapsed_ms, 0.0


def _image_to_data_url(path: str) -> str:
    image_path = Path(path)
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class OpenAICompatibleVisionAdapter:
    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
        prompt: str,
        model_version: str = "",
        timeout_sec: float = 30.0,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.model_version = model_version or model_name
        self.prompt = prompt
        self.timeout_sec = timeout_sec

    def analyze(self, task: ShadowTask) -> tuple[dict, int, float]:
        started = time.monotonic()
        content: list[dict] = [
            {
                "type": "text",
                "text": json.dumps(task.to_jsonable(), ensure_ascii=False),
            }
        ]
        for image in task.images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_to_data_url(image.path)},
                }
            )

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout_sec) as client:
            response = client.post(f"{self.api_base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        content_text = data["choices"][0]["message"]["content"]
        raw = json.loads(content_text)
        usage = data.get("usage") or {}
        cost_cny = float(usage.get("cost_cny") or 0)
        return raw, elapsed_ms, cost_cny
```

- [ ] **Step 4: Implement import CLI**

Create `tools/import_shadow_tasks.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path

from modules.shadow_models import ShadowTask
from modules.shadow_queue import enqueue_task, init_db


def import_tasks(db_path: str | Path, tasks_dir: str | Path) -> int:
    init_db(db_path)
    count = 0
    for path in sorted(Path(tasks_dir).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        enqueue_task(db_path, ShadowTask.from_dict(data))
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--tasks-dir", required=True)
    args = parser.parse_args()
    count = import_tasks(args.db, args.tasks_dir)
    print(f"imported {count} task files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Implement runner CLI**

Create `tools/run_shadow_audit.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
from pathlib import Path

from modules.model_schema import ModelSchemaError, parse_model_evidence
from modules.shadow_decider import decide_shadow_task
from modules.shadow_queue import claim_next_task, complete_task, fail_task, init_db
from modules.vision_adapters import FixtureVisionAdapter, OpenAICompatibleVisionAdapter, VisionAdapter


def run_pending_tasks(
    db_path: str | Path,
    adapter: VisionAdapter,
    prompt_version: str,
    limit: int = 50,
) -> int:
    init_db(db_path)
    processed = 0
    while processed < limit:
        task = claim_next_task(db_path)
        if task is None:
            break
        try:
            raw, elapsed_ms, cost_cny = adapter.analyze(task)
            evidence = parse_model_evidence(raw)
            result = decide_shadow_task(task, evidence).with_runtime(
                model_name=adapter.model_name,
                model_version=adapter.model_version,
                prompt_version=prompt_version,
                cost_cny=cost_cny,
                elapsed_ms=elapsed_ms,
            )
            complete_task(db_path, result)
        except ModelSchemaError as exc:
            result = decide_shadow_task(task, None, model_error=str(exc)).with_runtime(
                model_name=adapter.model_name,
                model_version=adapter.model_version,
                prompt_version=prompt_version,
            )
            complete_task(db_path, result)
        except Exception as exc:
            fail_task(db_path, task.task_id, str(exc))
        processed += 1
    return processed


def _load_prompt(prompt_path: str | Path) -> tuple[str, str]:
    import json

    data = json.loads(Path(prompt_path).read_text(encoding="utf-8"))
    return str(data["version"]), str(data["system_prompt"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--adapter", choices=["fixture", "openai-compatible"], required=True)
    parser.add_argument("--fixtures-dir")
    parser.add_argument("--prompt", default="prompts/guobu_v20260628.json")
    args = parser.parse_args()

    prompt_version, prompt = _load_prompt(args.prompt)
    if args.adapter == "fixture":
        if not args.fixtures_dir:
            raise SystemExit("--fixtures-dir is required for fixture adapter")
        adapter: VisionAdapter = FixtureVisionAdapter(args.fixtures_dir)
    else:
        adapter = OpenAICompatibleVisionAdapter(
            api_base_url=os.environ["VISION_API_BASE_URL"],
            api_key=os.environ["VISION_API_KEY"],
            model_name=os.environ["VISION_MODEL_NAME"],
            prompt=prompt,
        )

    processed = run_pending_tasks(args.db, adapter, prompt_version, args.limit)
    print(f"processed {processed} task(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Implement export CLI**

Create `tools/export_shadow_report.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path

from modules.shadow_queue import list_completed_results
from modules.shadow_report import write_shadow_report


def export_report(db_path: str | Path, report_path: str | Path) -> int:
    results = list_completed_results(db_path)
    write_shadow_report(report_path, results)
    return len(results)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    count = export_report(args.db, args.out)
    print(f"exported {count} result(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run test to verify it passes**

Run:

```powershell
pytest tests/test_shadow_runner.py -v
```

Expected: PASS.

- [ ] **Step 8: Run all shadow tests**

Run:

```powershell
pytest tests/test_shadow_models.py tests/test_shadow_queue.py tests/test_model_schema.py tests/test_shadow_decider.py tests/test_shadow_report.py tests/test_shadow_runner.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add modules/vision_adapters.py tools/import_shadow_tasks.py tools/run_shadow_audit.py tools/export_shadow_report.py tests/test_shadow_runner.py
git commit -m "feat: add shadow audit batch runner"
```

### Task 7: Prompt File and Local Fixture Workflow

**Files:**
- Create: `prompts/guobu_v20260628.json`
- Create: `data/sample_tasks/20260628-000001.json`
- Create: `data/sample_fixtures/20260628-000001.json`
- Modify: `.gitignore`
- Test: manual command workflow

**Interfaces:**
- Consumes: Task 6 CLI
- Produces: repeatable local smoke run without real API access

- [ ] **Step 1: Create prompt config**

Create `prompts/guobu_v20260628.json`:

```json
{
  "version": "guobu_v20260628",
  "scene": "guobu",
  "system_prompt": "你是国补家电数码/3C审核图片证据提取器。只输出JSON，不输出Markdown。你只负责提取证据，不负责决定是否通过。字段必须包含schema_version、sn_candidates、product_evidence、image_risks、conflicts、summary。sn_candidates中的confidence只能是high、medium、low。image_risks中的risk_level只能是none、weak、strong。如果看不清SN，不要猜测，返回空sn_candidates或low置信度。发现截图、翻拍、拼图、P图、网图等明确风险时，risk_level返回strong。"
}
```

- [ ] **Step 2: Create sample task**

Create `data/sample_tasks/20260628-000001.json`:

```json
{
  "task_id": "20260628-000001",
  "channel_order_no": "QD202606280001",
  "scene": "guobu",
  "fields": {
    "product_type": "手机",
    "product_name": "iPhone 17 Pro Max",
    "brand": "Apple",
    "model": "17 Pro Max",
    "system_sn": "ABC123",
    "imei1": "",
    "imei2": "",
    "barcode": "",
    "address": ""
  },
  "images": [
    {
      "image_id": "img_001",
      "title": "SN码采集/激活照片",
      "local_path": "data/sample_images/20260628-000001/img_001.jpg"
    }
  ],
  "source": {
    "collector": "manual",
    "collected_at": "2026-06-28T14:00:00+08:00"
  }
}
```

- [ ] **Step 3: Create sample fixture output**

Create `data/sample_fixtures/20260628-000001.json`:

```json
{
  "schema_version": "1.0",
  "sn_candidates": [
    {
      "image_id": "img_001",
      "value": "ABC123",
      "confidence": "high",
      "evidence_text": "图片中可见SN: ABC123"
    }
  ],
  "product_evidence": {
    "brand_seen": true,
    "model_seen": false,
    "barcode_seen": false,
    "package_or_product_seen": true,
    "activation_or_sn_label_seen": true
  },
  "image_risks": [
    {
      "image_id": "img_001",
      "risk": "none",
      "risk_level": "none",
      "reason": ""
    }
  ],
  "conflicts": [],
  "summary": "SN一致，未发现明显翻拍、截图、拼图或P图风险"
}
```

- [ ] **Step 4: Keep generated runtime files out of git**

Modify `.gitignore` by adding:

```gitignore
data/shadow.db
data/reports/
data/images/
data/sample_images/
```

- [ ] **Step 5: Run local smoke workflow**

Run:

```powershell
python tools/import_shadow_tasks.py --db data/shadow.db --tasks-dir data/sample_tasks
python tools/run_shadow_audit.py --db data/shadow.db --adapter fixture --fixtures-dir data/sample_fixtures --limit 10
python tools/export_shadow_report.py --db data/shadow.db --out data/reports/shadow_judgment.csv
```

Expected output:

```text
imported 1 task files
processed 1 task(s)
exported 1 result(s)
```

Expected report:

```text
渠道订单号,是否转人工,转人工原因,AI判断,...
QD202606280001,否,,pass_candidate,...
```

- [ ] **Step 6: Commit**

```powershell
git add prompts/guobu_v20260628.json data/sample_tasks/20260628-000001.json data/sample_fixtures/20260628-000001.json .gitignore
git commit -m "chore: add guobu shadow audit prompt and sample workflow"
```

### Task 8: Selector-Driven Browser Collector Scaffold

**Files:**
- Create: `tools/collect_shadow_tasks_playwright.py`
- Create: `docs/collector-selector-guide.md`
- Test: `tests/test_collector_selector_mapping.py`

**Interfaces:**
- Produces: `build_task_from_mapping(raw: dict[str, str], images: list[dict[str, str]]) -> dict`
- Consumes: import CLI from Task 6

**Important boundary:** Real production CSS selectors are not known from the repository. This task creates a selector-driven collector and a tested mapping layer. Before live collection, an operator must inspect the actual audit page and supply selectors in a JSON config. The collector must fail fast if any required selector is missing.

- [ ] **Step 1: Write mapping test**

Create `tests/test_collector_selector_mapping.py`:

```python
# -*- coding: utf-8 -*-
from tools.collect_shadow_tasks_playwright import build_task_from_mapping


def test_build_task_from_mapping():
    task = build_task_from_mapping(
        {
            "channel_order_no": "QD202606280001",
            "product_type": "手机",
            "product_name": "iPhone",
            "brand": "Apple",
            "model": "17 Pro Max",
            "system_sn": "ABC123",
        },
        [{"image_id": "img_001", "title": "SN照片", "local_path": "data/images/1.jpg"}],
    )

    assert task["channel_order_no"] == "QD202606280001"
    assert task["scene"] == "guobu"
    assert task["fields"]["system_sn"] == "ABC123"
    assert task["images"][0]["title"] == "SN照片"
```

- [ ] **Step 2: Implement collector mapping and guarded CLI**

Create `tools/collect_shadow_tasks_playwright.py`:

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_FIELDS = ["channel_order_no", "product_type", "product_name", "brand", "model", "system_sn"]


def build_task_from_mapping(raw: dict[str, str], images: list[dict[str, str]]) -> dict:
    task_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return {
        "task_id": task_id,
        "channel_order_no": raw["channel_order_no"],
        "scene": "guobu",
        "fields": {
            "product_type": raw.get("product_type", ""),
            "product_name": raw.get("product_name", ""),
            "brand": raw.get("brand", ""),
            "model": raw.get("model", ""),
            "system_sn": raw.get("system_sn", ""),
            "imei1": raw.get("imei1", ""),
            "imei2": raw.get("imei2", ""),
            "barcode": raw.get("barcode", ""),
            "address": raw.get("address", ""),
        },
        "images": images,
        "source": {"collector": "playwright", "collected_at": datetime.now(timezone.utc).isoformat()},
    }


def validate_selector_config(config: dict) -> None:
    fields = config.get("fields") or {}
    missing = [field for field in REQUIRED_FIELDS if not fields.get(field)]
    if missing:
        raise ValueError(f"missing required selectors: {', '.join(missing)}")
    if not config.get("image_cards"):
        raise ValueError("missing image_cards selector")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selectors", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    config = json.loads(Path(args.selectors).read_text(encoding="utf-8"))
    validate_selector_config(config)
    raise SystemExit(
        "Selector config is valid. Live Playwright navigation should be added after actual page selectors are confirmed."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write selector guide**

Create `docs/collector-selector-guide.md`:

```markdown
# 国补影子试跑采集器选择器配置指南

首期采集器不得点击通过、驳回或修改订单状态。采集器只允许读取字段、读取图片标题、下载图片、生成任务 JSON。

必须确认的页面字段：

- 渠道订单号
- 类型/品类
- 商品名称
- 品牌
- 规格型号
- 系统 SN
- 图片标题
- 图片 URL 或下载按钮

选择器配置结构：

```json
{
  "fields": {
    "channel_order_no": "CSS selector for channel order number",
    "product_type": "CSS selector for product type",
    "product_name": "CSS selector for product name",
    "brand": "CSS selector for brand",
    "model": "CSS selector for model",
    "system_sn": "CSS selector for system SN"
  },
  "image_cards": "CSS selector for each image card",
  "image_title": "CSS selector relative to image card",
  "image_download": "CSS selector or attribute for image URL"
}
```

如果任一必填字段选择器无法稳定定位，停止批量采集。不得在字段错位时继续跑批。
```

- [ ] **Step 4: Run mapping test**

Run:

```powershell
pytest tests/test_collector_selector_mapping.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add tools/collect_shadow_tasks_playwright.py docs/collector-selector-guide.md tests/test_collector_selector_mapping.py
git commit -m "feat: add selector-driven shadow collector scaffold"
```

### Task 9: Formal Backend Integration Contract

**Files:**
- Create: `docs/backend/ai_audit_task_contract.md`

**Interfaces:**
- Produces: backend table SQL, status transitions, worker claim contract, result payload
- Consumed by: backend developers outside this repo

- [ ] **Step 1: Create backend contract document**

Create `docs/backend/ai_audit_task_contract.md`:

```markdown
# AI Audit Task Backend Integration Contract

## Purpose

This contract describes the formal backend integration after the local Guobu shadow-run pipeline proves reliable. The backend writes audit tasks; the AI worker claims, evaluates, and writes back evidence. The initial production rollout must display AI results only. Automatic approval remains disabled until business approval.

## MySQL Table

```sql
CREATE TABLE ai_audit_task (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  channel_order_no VARCHAR(64) NOT NULL,
  scene VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  fields_json JSON NOT NULL,
  images_json JSON NOT NULL,
  ai_decision VARCHAR(32),
  manual_required BOOLEAN,
  manual_reason VARCHAR(255),
  evidence_json JSON,
  model_name VARCHAR(64),
  model_version VARCHAR(64),
  prompt_version VARCHAR(64),
  cost_cny DECIMAL(10,4),
  elapsed_ms INT,
  locked_by VARCHAR(64),
  locked_at DATETIME,
  heartbeat_at DATETIME,
  error_message VARCHAR(512),
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uk_channel_order_no (channel_order_no),
  INDEX idx_status_created (status, created_at),
  INDEX idx_locked_at (locked_at)
);
```

## Status Flow

```text
pending -> processing -> done
pending -> processing -> failed
processing -> pending  when heartbeat is stale
```

## Worker Claim

For MySQL 8:

```sql
START TRANSACTION;

SELECT id
FROM ai_audit_task
WHERE status = 'pending'
ORDER BY created_at ASC
LIMIT 1
FOR UPDATE SKIP LOCKED;

UPDATE ai_audit_task
SET status = 'processing',
    locked_by = 'worker-01',
    locked_at = NOW(),
    heartbeat_at = NOW(),
    updated_at = NOW()
WHERE id = :id;

COMMIT;
```

## Result Payload

```json
{
  "ai_decision": "pass_candidate",
  "manual_required": false,
  "manual_reason": "",
  "evidence_json": {
    "sn_match": true,
    "matched_sn": "ABC123",
    "strong_image_risk": false,
    "model_json_valid": true
  },
  "model_name": "qwen-vl",
  "model_version": "example-version",
  "prompt_version": "guobu_v20260628",
  "cost_cny": 0.02,
  "elapsed_ms": 3200
}
```

## Safety Requirements

- The backend must not auto-reject orders from AI output.
- The first production integration must only display AI result and reason.
- The automatic approval switch defaults to disabled.
- A stale `processing` task with no heartbeat for 5 minutes returns to `pending`.
- Any worker exception writes `failed` and `error_message`.
- The frontend should show `manual_reason` next to the audit order so reviewers know why it is a疑单.
```

- [ ] **Step 2: Commit**

```powershell
git add docs/backend/ai_audit_task_contract.md
git commit -m "docs: add backend ai audit task contract"
```

## Verification Commands

Run these after all local MVP tasks:

```powershell
pytest tests/test_shadow_models.py tests/test_shadow_queue.py tests/test_model_schema.py tests/test_shadow_decider.py tests/test_shadow_report.py tests/test_shadow_runner.py tests/test_collector_selector_mapping.py -v
python tools/import_shadow_tasks.py --db data/shadow.db --tasks-dir data/sample_tasks
python tools/run_shadow_audit.py --db data/shadow.db --adapter fixture --fixtures-dir data/sample_fixtures --limit 10
python tools/export_shadow_report.py --db data/shadow.db --out data/reports/shadow_judgment.csv
```

Expected:

```text
all pytest tests pass
imported 1 task files
processed 1 task(s)
exported 1 result(s)
```

## Execution Notes

- Do not connect real model APIs until the fixture workflow passes.
- Do not build live Playwright selectors until a human has inspected the actual page and recorded stable selectors.
- Do not save downloaded production images in git.
- Do not upload身份证 images to model APIs.
- If one `pass_candidate` is later found wrong during manual review, tighten the decider first; do not loosen Prompt to chase pass rate.

## Self-Review

- Spec coverage: local task schema, SQLite queue, model adapter, schema validation, local裁决,判断表, sample workflow, collector scaffold, backend contract, costs and model version tracking are covered.
- Placeholder scan: no unfinished placeholder markers or undefined function references remain in executable task steps.
- Type consistency: `ShadowTask`, `ModelEvidence`, `DecisionResult`, `VisionAdapter`, `run_pending_tasks`, and queue function names are consistent across tasks.
