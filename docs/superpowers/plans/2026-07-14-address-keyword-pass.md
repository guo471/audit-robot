# Address Keyword Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow home-appliance addresses containing `商贸`, `京东家电`, or `楼` to pass the deterministic address precision precheck.

**Architecture:** Extend the existing `is_address_precise_enough` pure function with an explicit allowlist checked before minimum-length and generic precision rules. Keep the address decision in the deterministic precheck so model prompts, cost, latency, SN review, and photo compliance remain unchanged.

**Tech Stack:** Python 3, pytest

## Global Constraints

- Match any occurrence of `商贸`, `京东家电`, or `楼` in the address.
- A keyword match only passes address precision; all later SN and photo checks still run.
- Do not modify model prompts, model parameters, SN rules, or photo rules.

---

### Task 1: Address Keyword Allowlist

**Files:**
- Modify: `tests/test_guobu_v2_rules.py`
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `docs/guobu-audit-project-memory.md`

**Interfaces:**
- Consumes: `is_address_precise_enough(address: str | None) -> bool` and `precheck_task(task: dict[str, Any]) -> dict[str, Any]`
- Produces: the same interfaces with the three new keyword pass cases

- [ ] **Step 1: Write failing function and precheck tests**

```python
def test_address_precision_accepts_business_keywords_anywhere():
    for address in ("某某商贸", "北京市某路京东家电配送点", "幸福楼三层"):
        assert is_address_precise_enough(address)


def test_home_appliance_precheck_accepts_address_keyword_and_continues():
    task = {
        "channel_order_no": "1",
        "fields": {
            "product_type": "电冰箱",
            "system_sn": "ABC123",
            "is_home_appliance": True,
            "address": "某某商贸",
        },
        "images": [
            {"title": "product", "source_url": "a"},
            {"title": "unboxing", "source_url": "b"},
            {"title": "SN photo", "source_url": "c"},
        ],
    }

    result = precheck_task(task)

    assert result["manual_required"] is False
    assert result["address_ok"] is True
    assert result["activation_images"]
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `python -m pytest tests/test_guobu_v2_rules.py -k "address_precision_accepts_business_keywords_anywhere or home_appliance_precheck_accepts_address_keyword_and_continues" -q`

Expected: FAIL because `某某商贸` and the other new keyword-only addresses are not accepted by the current function.

- [ ] **Step 3: Add the minimal deterministic allowlist**

```python
ADDRESS_PASS_KEYWORDS = ("商贸", "京东家电", "楼")


def is_address_precise_enough(address: str | None) -> bool:
    text = str(address or "").strip()
    if any(keyword in text for keyword in ADDRESS_PASS_KEYWORDS):
        return True
    # Existing rules continue unchanged.
```

- [ ] **Step 4: Update the current business memory**

Add: `家电地址包含 商贸、京东家电、楼 任一关键词时视为粒度合格；仅放行地址预检，仍继续 SN 和全部照片审核。`

- [ ] **Step 5: Run focused and full rule tests**

Run: `python -m pytest tests/test_guobu_v2_rules.py -q`

Expected: all tests pass with no failures.

- [ ] **Step 6: Review the scoped diff**

Run: `git diff -- tools/run_guobu_model_audit_v2.py tests/test_guobu_v2_rules.py docs/guobu-audit-project-memory.md`

Expected: only the allowlist, its tests, and current memory wording change.
