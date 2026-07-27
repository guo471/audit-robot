# Guobu SN V2 Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone SN V2 engine and V1/V2 comparison workflow while leaving V1 unchanged.

**Architecture:** A pure policy module owns category routing, prompt assembly, evidence validation, and deterministic decisions. A thin standalone runner reuses the existing model transport but sends a V2 payload with no system-SN-derived data. A pure comparison module joins results by order ID.

**Tech Stack:** Python 3, pytest, existing Guobu model transport.

## Global Constraints

- Do not modify `tools/run_guobu_model_audit_v2.py`.
- Do not modify non-SN prompts or business policies.
- Write and run failing tests before each implementation slice.
- V2 model payloads must contain no system SN, system SN length, or comparison hint.

---

### Task 1: Pure SN V2 Policy

**Files:**
- Create: `tests/test_guobu_sn_policy_v2.py`
- Create: `tools/guobu_sn_policy_v2.py`

- [ ] Write routing, prompt isolation, canonicalization, and decision-matrix tests.
- [ ] Run the focused test and verify failure because the V2 module is absent.
- [ ] Implement the minimum pure policy API.
- [ ] Run the focused test and verify all cases pass.

### Task 2: Standalone Runner

**Files:**
- Create: `tests/test_run_guobu_sn_v2.py`
- Create: `tools/run_guobu_sn_v2.py`

- [ ] Write tests proving model payload secrecy and standalone result shape.
- [ ] Run the focused test and verify failure because the runner is absent.
- [ ] Implement the runner using dependency injection for the model caller.
- [ ] Run the focused test and verify all cases pass.

### Task 3: V1/V2 Comparison

**Files:**
- Create: `tests/test_compare_guobu_sn_v1_v2.py`
- Create: `tools/compare_guobu_sn_v1_v2.py`

- [ ] Write tests for ID-based joins, missing rows, and changed decisions.
- [ ] Run the focused test and verify failure because the comparer is absent.
- [ ] Implement JSONL loading and CSV/JSON comparison output.
- [ ] Run all focused tests and the existing SN regression suite.

