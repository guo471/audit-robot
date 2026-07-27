# Guobu One Click Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable one-click Guobu collector entry that does not depend on the currently visible page JavaScript staying responsive.

**Architecture:** A small Node.js wrapper reads explicit config/CLI filters, opens a temporary CDP target only to obtain the existing logged-in token, probes the API total, then delegates collection and validation to the proven `guobu-examine-api-collector` PowerShell wrapper. The wrapper never prints or saves the token.

**Tech Stack:** Node.js 24, PowerShell, existing Guobu collector skill scripts, pytest contract tests.

## Global Constraints

- Collection is read-only and must not modify backend approval/rejection state.
- Do not print or persist backend authorization token.
- Use the backend time fields that match the UI “审核时间” result count. Probe confirmed `approvalStartTime/approvalEndTime` returns the visible 19-order batch while `checkStartTime/checkEndTime` returns 1.
- Detail API must remain mandatory so `goodsPhoto/unsealingPhoto/activatePhoto` groups are complete.
- Default current batch is failed orders from UI “审核时间” `2026-07-24 00:00:00` to `2026-07-24 15:59:36`, expected total 19.

---

### Task 1: One-click wrapper and config

**Files:**
- Create: `config/guobu_collect_one_click.json`
- Create: `tools/guobu_one_click_collect.js`
- Test: `tests/test_guobu_one_click_collect.py`

**Interfaces:**
- Consumes: `C:\Users\HUAWEI\.codex\skills\guobu-examine-api-collector\scripts\collect_guobu_filtered.ps1`
- Produces: CLI command `node tools/guobu_one_click_collect.js`

- [x] **Step 1: Write failing tests**

Tests assert the wrapper uses the API time fields that match the visible 19-order batch, defaults to the 19-order batch, and dry-run output contains no token-like fields.

- [x] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_guobu_one_click_collect.py -q`
Expected: FAIL because `tools/guobu_one_click_collect.js` does not exist.

- [x] **Step 3: Write minimal implementation**

Create a Node.js wrapper that supports `--dry-run`, `--probe-only`, config defaults, fresh CDP token extraction, API total guard, and delegation to the proven collector wrapper.

- [x] **Step 4: Run tests**

Run: `python -m pytest tests/test_guobu_one_click_collect.py -q`
Expected: PASS.

- [x] **Step 5: Probe and collect**

Run: `node tools/guobu_one_click_collect.js --probe-only`
Expected: API total is 19.

Run: `node tools/guobu_one_click_collect.js`
Expected: task count 19, no detail errors, no required photo-group errors.
