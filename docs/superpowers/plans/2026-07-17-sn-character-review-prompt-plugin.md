# SN Character Review Prompt Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an off-by-default, reversible SN similar-character prompt fragment for controlled A/B tests.

**Architecture:** Store the fragment separately from the base prompt and compose the effective prompt at runtime. Thread a two-value mode through the CLI and batch wrapper, and record it in result rows without changing audit decisions.

**Tech Stack:** Python 3.11, argparse, PowerShell, pytest.

## Global Constraints

- `off` must preserve the current SN prompt exactly.
- `on` only appends the reviewed character-rereading fragment.
- SN comparison remains strict; no fuzzy matching is allowed.
- Existing user changes in the dirty worktree must be preserved.

---

### Task 1: Prompt Composition And CLI Contract

**Files:**
- Create: `prompts/sn_similar_char_review.txt`
- Modify: `tools/run_guobu_model_audit_v2.py`
- Test: `tests/test_guobu_v2_rules.py`

**Interfaces:**
- Produces: `build_sn_prompt(mode: str) -> str`
- Produces: CLI namespace field `sn_char_review_mode`

- [ ] Write tests proving `off` returns the unchanged base prompt, `on` appends the fragment, and invalid modes fail.
- [ ] Run the focused tests and confirm they fail because the feature does not exist.
- [ ] Add the fragment, prompt builder, and CLI option with environment fallback.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Runtime And Batch Traceability

**Files:**
- Modify: `tools/run_guobu_model_audit_v2.py`
- Modify: `tools/run_guobu_audit_batch.ps1`
- Test: `tests/test_guobu_v2_rules.py`
- Test: `tests/test_guobu_audit_skill_report_integration.py`

**Interfaces:**
- Consumes: `build_sn_prompt(mode: str) -> str`
- Produces: result field `sn_char_review_mode`
- Produces: PowerShell switch `-EnableSnCharReview`

- [ ] Write tests proving model calls receive the composed prompt and rows preserve the configured mode.
- [ ] Write an integration assertion proving the batch plan and command expose the switch.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Thread the mode through runtime calls and the batch wrapper without changing any decision logic.
- [ ] Run focused and complete SN-related test suites.

