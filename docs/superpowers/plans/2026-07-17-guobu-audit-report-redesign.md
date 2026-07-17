# Guobu Audit Report Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a compact, traceable Guobu audit XLSX/JSON report with fixed business reasons, deterministic SN differences, correct pass/fail metrics, billed token cost, and human-efficiency estimates.

**Architecture:** Add a project-local report module containing pure normalization, merge, accounting, and workbook functions. Keep the running audit and shared skill merger unchanged until the batch finishes; validate the new generator against fixtures and a copied completed JSONL before switching the wrapper.

**Tech Stack:** Python 3.11, standard library, openpyxl, pytest.

## Global Constraints

- Do not change model prompts, audit decisions, reason selection, or backend state.
- `manual_flag` is the sole authority; contradictions fail closed.
- Display only the final primary reason code.
- Exclude cached stages from billed tokens.
- Do not modify the shared merger while the current batch is running.
- Preserve exact text for order IDs and SNs.

---

### Task 1: Business reason and SN display helpers

**Files:**
- Create: `tools/guobu_audit_report.py`
- Create: `tests/test_guobu_audit_report.py`

**Interfaces:**
- Produces: `standard_reason(code: str) -> str`, `sn_display(row: dict) -> tuple[str, str]`, `parse_manual_flag(value: object) -> bool`.

- [ ] **Step 1: Write failing reason and flag tests**

```python
from tools.guobu_audit_report import parse_manual_flag, standard_reason

def test_reason_mapping_is_fixed():
    assert standard_reason("SN_MISMATCH") == "SN不一致"
    assert standard_reason("IMAGE_STRONG_RISK") == "图片疑似非实拍"
    assert standard_reason("UNKNOWN_CODE") == "图片信息无法确认"

def test_manual_flag_is_explicit():
    assert parse_manual_flag("是") is True
    assert parse_manual_flag("否") is False
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_guobu_audit_report.py -v`
Expected: FAIL because `tools.guobu_audit_report` does not exist.

- [ ] **Step 3: Implement the fixed mapping and strict flag parser**

```python
REASON_TEXT = {"SN_MISMATCH": "SN不一致", "IMAGE_STRONG_RISK": "图片疑似非实拍"}

def standard_reason(code: str) -> str:
    return REASON_TEXT.get(str(code or "").strip().upper(), "图片信息无法确认")

def parse_manual_flag(value: object) -> bool:
    if value is True or value == "是": return True
    if value is False or value == "否": return False
    raise ValueError(f"invalid manual_flag: {value!r}")
```

- [ ] **Step 4: Add failing SN tests for equal, missing, substitution, insertion, deletion, truncation, and transposition**

```python
def test_sn_transposition():
    assert sn_display({"system_sn": "ABHV12", "observed_sn": "ABVH12", "sn_match": False}) == (
        "否", "字符顺序不同：系统HV，模型VH"
    )
```

- [ ] **Step 5: Implement deterministic SN classification in the specified order and run tests**

Run: `python -m pytest tests/test_guobu_audit_report.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add tools/guobu_audit_report.py tests/test_guobu_audit_report.py
git commit -m "feat: add guobu report display helpers"
```

### Task 2: Fail-closed merge and accounting

**Files:**
- Modify: `tools/guobu_audit_report.py`
- Modify: `tests/test_guobu_audit_report.py`

**Interfaces:**
- Consumes: Task 1 helpers.
- Produces: `merge_attempts(first_items, retry_items, retry_ids=None) -> tuple[list[dict], dict]` and `build_summary(rows, accounting, prices) -> dict`.

- [ ] **Step 1: Write failing integrity tests**

```python
import pytest

def test_duplicate_first_id_fails():
    with pytest.raises(ValueError, match="duplicate first-run order ID"):
        merge_attempts([fixture("1"), fixture("1")], [])

def test_unknown_retry_id_fails():
    with pytest.raises(ValueError, match="unknown retry order ID"):
        merge_attempts([fixture("1")], [fixture("2")])
```

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `python -m pytest tests/test_guobu_audit_report.py -k 'duplicate or unknown_retry' -v`
Expected: FAIL because `merge_attempts` is missing.

- [ ] **Step 3: Implement ID validation, shared network detection, legal retry replacement, and reason/flag contradiction checks**

```python
def indexed(items, label):
    result = {}
    for item in items:
        order_id = str((item.get("row") or {}).get("id") or "")
        if not order_id or order_id in result:
            raise ValueError(f"invalid or duplicate {label} order ID: {order_id!r}")
        result[order_id] = item
    return result
```

- [ ] **Step 4: Write failing accounting tests for cached stages, first timeout plus retry, zero denominators, and unknown statuses**

```python
def test_cached_usage_is_not_billed():
    accounting = account_attempt(fixture_with_usage(cached=True, prompt=100, completion=20))
    assert accounting["billed_input_tokens"] == 0
    assert accounting["billed_output_tokens"] == 0
```

- [ ] **Step 5: Implement billed usage and effective elapsed accounting**

Cost must be `待配置` unless all required prices are supplied; cached-input tokens use their own configured rate.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_guobu_audit_report.py -v`
Expected: PASS.

```powershell
git add tools/guobu_audit_report.py tests/test_guobu_audit_report.py
git commit -m "feat: add fail-closed audit report accounting"
```

### Task 3: XLSX and combined JSON generation

**Files:**
- Modify: `tools/guobu_audit_report.py`
- Modify: `tests/test_guobu_audit_report.py`

**Interfaces:**
- Consumes: `merge_attempts`, `build_summary`.
- Produces: `write_report(rows, summary, audit_json, xlsx_path, json_path) -> None` and a CLI compatible with first/retry JSONL inputs.

- [ ] **Step 1: Write a failing workbook contract test**

```python
def test_workbook_has_exact_business_contract(tmp_path):
    output = tmp_path / "report.xlsx"
    write_report(rows=[display_row()], summary=summary_fixture(), audit_json={}, xlsx_path=output,
                 json_path=tmp_path / "report.json")
    wb = load_workbook(output, data_only=False)
    assert wb.sheetnames == ["明细表", "汇总表"]
    assert [c.value for c in wb["明细表"][1]] == [
        "订单号", "是否转人工", "原始流程状态", "转人工原因",
        "系统SN", "模型SN", "SN是否一致", "SN具体差别",
    ]
```

- [ ] **Step 2: Run workbook test and verify RED**

Run: `python -m pytest tests/test_guobu_audit_report.py -k workbook -v`
Expected: FAIL because `write_report` is missing.

- [ ] **Step 3: Implement workbook formatting, formulas/text safety, and trace JSON**

Write IDs/SNs as strings with `number_format='@'`; prefix formula-like external strings with an apostrophe; use Arial, frozen headers, filters, exact formats, and no extra business columns.

- [ ] **Step 4: Run all report tests and recalculate/scan the fixture workbook**

Run: `python -m pytest tests/test_guobu_audit_report.py -v`
Expected: PASS.

Run: `python C:\Users\HUAWEI\.codex\skills\z-excel-editor\scripts\recalc.py <fixture.xlsx> 30`
Expected: `status=success`, `total_errors=0`.

- [ ] **Step 5: Commit**

```powershell
git add tools/guobu_audit_report.py tests/test_guobu_audit_report.py
git commit -m "feat: generate compact guobu audit workbook"
```

### Task 4: Completed-batch validation and safe integration

**Files:**
- Modify after current batch completion: `C:/Users/HUAWEI/.codex/skills/auditing-guobu-orders/scripts/run_guobu_audit_batch.ps1`
- Test: `tests/test_guobu_audit_skill_network_markers.py`
- Output: a new non-overwriting XLSX/JSON path under `reports/model_audit/`.

**Interfaces:**
- Consumes: Task 3 CLI.
- Produces: future batch wrapper invokes the project-local generator only after both audit attempts complete.

- [ ] **Step 1: Confirm the current process has completed and its original combined files exist**

Run: `Get-CimInstance Win32_Process | Where-Object CommandLine -like '*guobu554_20260716*'`
Expected: no active audit process before shared integration.

- [ ] **Step 2: Generate a new report from completed JSONL without calling the model**

Run the new CLI with unique output names ending in `_business_report.xlsx` and `_business_report.json`.
Expected: detail count equals first-run unique count and source JSONL timestamps do not change.

- [ ] **Step 3: Validate business metrics against independent counts**

Check passed/failed numerators and denominators, manual/automatic totals, billed tokens, effective hours, and all reason mappings from the completed data.

- [ ] **Step 4: Send code and workbook evidence to `Code Reviewer`; resolve every P1/P2 finding**

Expected: reviewer reports no business-logic or accounting blockers.

- [ ] **Step 5: Add a wrapper-level failing test, switch the wrapper to the new generator, and verify the plan-only path**

Run: `python -m pytest tests/test_guobu_audit_skill_network_markers.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full relevant suite and commit**

Run: `python -m pytest tests/test_guobu_audit_report.py tests/test_guobu_audit_skill_network_markers.py -v`
Expected: PASS.

```powershell
git add tools/guobu_audit_report.py tests/test_guobu_audit_report.py tests/test_guobu_audit_skill_network_markers.py
git commit -m "feat: integrate compact guobu audit reports"
```

