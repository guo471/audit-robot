# Guobu Full Audit Chain Documentation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a source-grounded, Chinese-language flowchart atlas for the complete Guobu order collection, audit, retry, and reporting chain without changing business code.

**Architecture:** Read the production entry points and deterministic rule functions, separate the active production mainline from optional plugins and compatibility paths, then express the system as multiple focused Mermaid diagrams. Generate a standalone navigable HTML companion that renders the same major diagrams and links every section through a sticky table of contents.

**Tech Stack:** Markdown, Mermaid flowcharts, standalone HTML/CSS/JavaScript, existing PowerShell and Python source files as the source of truth.

## Global Constraints

- Do not modify audit code, prompts, runtime configuration, collected tasks, or historical reports.
- Do not include API keys, access tokens, phone numbers, identity-card numbers, customer names, or concrete addresses.
- Translate every reason code and technical authenticity label into concise Chinese.
- Mark production defaults, optional plugins, disabled paths, and legacy compatibility paths explicitly.
- Treat local deterministic code as the final decision authority where it overrides model output.

---

### Task 1: Source Map

**Files:**
- Read: `tools/guobu_one_click_collect.js`
- Read: `tools/run_guobu_audit_batch.ps1`
- Read: `tools/run_guobu_model_audit_v2.py`
- Read: `tools/photo_authenticity_mainline.py`
- Read: `tools/guobu_audit_contract.py`
- Read: `tools/guobu_audit_report.py`
- Read: `modules/category_classifier.py`

- [x] Identify the active collection, audit, retry, and report entry points.
- [x] Record current defaults and plugin states.
- [x] Record all deterministic decision functions and output contracts.

### Task 2: Mermaid Atlas

**Files:**
- Create: `docs/国补审核完整链路流程图.md`

- [x] Add the system overview and input/output contract.
- [x] Add collection and batch preflight diagrams.
- [x] Add per-order, precheck, SN, category, and compliance diagrams.
- [x] Add authenticity R1-R10 and failure-closed diagrams.
- [x] Add timeout, retry, merge, report, and reason-code reference sections.

### Task 3: Navigable HTML

**Files:**
- Create: `docs/国补审核完整链路流程图.html`

- [x] Add sticky section navigation and readable print styles.
- [x] Render the major Mermaid diagrams.
- [x] Include the complete Chinese reason-code and runtime-state tables.

### Task 4: Verification

**Files:**
- Verify: `docs/国补审核完整链路流程图.md`
- Verify: `docs/国补审核完整链路流程图.html`

- [x] Check Markdown fence balance and Mermaid block count.
- [x] Check HTML section links and Mermaid containers.
- [x] Scan for secrets and sensitive sample data.
- [x] Confirm only documentation files changed.
