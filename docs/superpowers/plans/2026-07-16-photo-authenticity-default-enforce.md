# Photo Authenticity Default Enforce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make photo-authenticity enforcement the default for the production `hybrid` audit path while preserving explicit `off` and `shadow` rollback modes, keeping FFT disabled, and disabling the extra single-image fallback call.

**Architecture:** Change the default at both configuration boundaries: `PhotoAuthenticityConfig.from_env()` for direct callers and the v2 CLI parser for command-line callers. Lock both boundaries with focused tests, then update the project memory to reflect the operational default.

**Tech Stack:** Python 3.11, argparse, pytest

## Global Constraints

- Missing `PHOTO_AUTHENTICITY_MODE` must resolve to `enforce`.
- Explicit `off` and `shadow` values must remain authoritative.
- Missing `PHOTO_AUTHENTICITY_FFT_ENABLED` must continue to resolve to false.
- Do not change prompt text, evidence definitions, manual-review logic, model call count, thinking mode, or backend order state.
- Keep the strict product-screen-only `LOCAL_MOIRE` exemption unchanged.
- Do not extend authenticity execution to `fast`, `v2`, or `sn_only`.
- Invalid or incomplete authenticity structure must route manual without another model call.

---

### Task 1: Configuration And CLI Defaults

**Files:**
- Modify: `tests/test_photo_authenticity_mainline.py`
- Modify: `tests/test_guobu_v2_rules.py`
- Modify: `tools/photo_authenticity_mainline.py`
- Modify: `tools/run_guobu_model_audit_v2.py`

**Interfaces:**
- Consumes: `PhotoAuthenticityConfig.from_env(env)` and `parse_args()`.
- Produces: default mode `enforce`; explicit `off` and `shadow` behavior unchanged.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_photo_authenticity_defaults_to_enforce_and_fft_off():
    config = PhotoAuthenticityConfig.from_env({})
    assert config.mode == "enforce"
    assert config.fft_enabled is False


def test_photo_authenticity_explicit_off_remains_available():
    config = PhotoAuthenticityConfig.from_env({"PHOTO_AUTHENTICITY_MODE": "off"})
    assert config.mode == "off"
```

- [ ] **Step 2: Write a failing CLI default test**

```python
def test_cli_defaults_photo_authenticity_to_enforce(monkeypatch):
    monkeypatch.delenv("PHOTO_AUTHENTICITY_MODE", raising=False)
    args = parse_args([])
    assert args.photo_authenticity_mode == "enforce"
```

- [ ] **Step 3: Run focused tests and verify RED**

Run: `python -m pytest tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py -q`

Expected: the new default assertions fail because the current default is `off`.

- [ ] **Step 4: Replace the fallback integration expectation**

Update the existing hybrid integration test so an incomplete `photo_authenticity_by_image` result makes the order manual, records zero fallback calls, and calls only `hybrid_sn` and `hybrid_compliance`.

- [ ] **Step 5: Change only the two defaults, CLI help text, and hybrid fallback argument**

```python
mode = str(env.get("PHOTO_AUTHENTICITY_MODE", "enforce")).strip().lower()
```

```python
default=os.environ.get("PHOTO_AUTHENTICITY_MODE", "enforce")
```

Update the argument help text from default `off` to default `enforce`; do not alter any evidence or gating code.

Pass no fallback callback from `audit_task_hybrid`; keep the shared gate's fail-closed behavior unchanged.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py -q`

Expected: PASS.

### Task 2: Documentation And Regression Verification

**Files:**
- Modify: `docs/guobu-audit-project-memory.md`

**Interfaces:**
- Consumes: verified defaults from Task 1.
- Produces: operational documentation stating default `enforce`, explicit `off` rollback, and default FFT disabled.

- [ ] **Step 1: Update the operational memory**

Document these exact settings:

```text
PHOTO_AUTHENTICITY_MODE=enforce (default)
PHOTO_AUTHENTICITY_MODE=off (explicit rollback)
PHOTO_AUTHENTICITY_FFT_ENABLED=false (default)
```

- [ ] **Step 2: Run the complete relevant test suite**

Run: `python -m pytest tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py tests/photo_authenticity -q`

Expected: PASS with no failures.

- [ ] **Step 3: Verify scope mechanically**

Run: `git diff --check && git diff --stat && git diff -- tools/photo_authenticity_mainline.py tools/run_guobu_model_audit_v2.py tests/test_photo_authenticity_mainline.py tests/test_guobu_v2_rules.py docs/guobu-audit-project-memory.md`

Expected: only default values, help text, focused tests, and memory documentation changed.

- [ ] **Step 4: Run adversarial review**

Ask reviewers to challenge these invariants: no prompt change, no FFT default change, no extra model call, explicit rollback works, unrelated dirty-worktree changes remain untouched.
