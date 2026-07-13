# Single-Image Non-Real Audit Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated single-image vision audit agent, run every image under `非实拍样本`, and report high-risk, review, no-evidence, and execution-failure rates.

**Architecture:** Keep the frozen Chinese prompt in a standalone text asset. A focused Python module validates model JSON and derives the result mechanically from evidence codes. A CLI discovers every image without deduplication, calls the existing OpenAI-compatible vision endpoint one image at a time with caching, writes JSONL/CSV/JSON summaries, and never imports or changes the production Guobu audit flow.

**Tech Stack:** Python 3.11+, standard library HTTP/JSON/CSV/base64, pytest, existing OpenAI-compatible `VISION_API_BASE_URL` and `VISION_API_KEY` environment variables.

## Global Constraints

- Process one image per model request and do not use order metadata or neighboring images.
- Include all image files recursively under the requested sample root; do not deduplicate.
- Use three results only: `high_risk_non_real`, `manual_review`, `no_evidence`.
- Treat model, network, decoding, and schema failures as `execution_failure`, never as `no_evidence`.
- Do not modify `tools/run_guobu_model_audit_v2.py` or `COMPLIANCE_PROMPT`.
- Do not write sample counts, filenames, merchants, or watermark contents into the prompt.
- Default to `qwen3.7-plus`, `workers=1`, `enable_thinking=false`, and JSON response format.

---

### Task 1: Frozen Prompt and Result Contract

**Files:**
- Create: `photo_authenticity/prompts/non_real_photo_auditor_v1.txt`
- Create: `tools/non_real_photo_agent_v1.py`
- Test: `tests/test_non_real_photo_agent_v1.py`

**Interfaces:**
- Produces: `load_prompt(path: Path) -> str`
- Produces: `validate_and_normalize(raw: dict[str, Any]) -> dict[str, Any]`
- Produces: fixed evidence-code constants used by the batch runner.

- [ ] **Step 1: Write failing contract tests**

Add tests proving that direct evidence forces `high_risk_non_real`, two distinct supporting families force high risk, one supporting family forces `manual_review`, weak-only evidence forces `no_evidence`, unknown codes fail validation, contradictory model results are overwritten by the mechanical decision, and missing arrays fail validation.

- [ ] **Step 2: Run the contract tests and confirm failure**

Run: `python -m pytest tests/test_non_real_photo_agent_v1.py -v`

Expected: collection failure because `tools.non_real_photo_agent_v1` does not exist.

- [ ] **Step 3: Add the frozen Prompt 1 text**

Write the approved task boundary, region localization, direct evidence, supporting evidence, weak evidence, product-screen exemption, decision table, and strict JSON schema exactly once. Require JSON only and forbid unsupported evidence codes.

- [ ] **Step 4: Implement minimal schema validation**

Implement enum validation, list validation, evidence de-duplication, result derivation, a reason length cap, and explicit `ValueError` messages. Keep the module free of network calls.

- [ ] **Step 5: Run contract tests**

Run: `python -m pytest tests/test_non_real_photo_agent_v1.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit the contract**

Commit only the prompt, module, and contract test with message `feat: add non-real photo audit agent v1`.

### Task 2: Single-Image Model Call and Cache

**Files:**
- Modify: `tools/non_real_photo_agent_v1.py`
- Test: `tests/test_non_real_photo_agent_v1.py`

**Interfaces:**
- Produces: `encode_image(path: Path) -> str`
- Produces: `call_agent(image_path: Path, prompt: str, config: AgentConfig) -> AgentCallResult`
- Consumes: `validate_and_normalize` from Task 1.

- [ ] **Step 1: Write failing transport tests**

Use monkeypatching to verify one image per request, `response_format={"type":"json_object"}`, `enable_thinking=false` for qwen, local image base64 encoding, cache-key inclusion of model/prompt/image bytes, cache hits avoiding HTTP, and invalid model JSON becoming an execution failure.

- [ ] **Step 2: Run transport tests and confirm failure**

Run: `python -m pytest tests/test_non_real_photo_agent_v1.py -k "request or cache or failure" -v`

Expected: failures for missing transport interfaces.

- [ ] **Step 3: Implement the transport**

Use `http.client` with bearer authentication, configurable connect/read timeouts, one connection retry, strict response parsing, SHA-256 cache files, elapsed seconds, token usage, and no key logging.

- [ ] **Step 4: Run all agent tests**

Run: `python -m pytest tests/test_non_real_photo_agent_v1.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit transport support**

Commit the module and tests with message `feat: add cached vision call for non-real agent`.

### Task 3: Recursive Batch Runner and Reports

**Files:**
- Create: `tools/run_non_real_photo_agent_v1.py`
- Test: `tests/test_run_non_real_photo_agent_v1.py`

**Interfaces:**
- Produces: `discover_images(root: Path) -> list[Path]`
- Produces: `run_batch(...) -> BatchSummary`
- Consumes: `call_agent` from Task 2.

- [ ] **Step 1: Write failing batch tests**

Create temporary nested directories and verify recursive discovery includes `.jpg`, `.jpeg`, `.png`, `.webp`, and `.bmp`, preserves every file without deduplication, sorts by relative path, resumes from JSONL, separates execution failures, and computes all rates with the original discovered count as denominator.

- [ ] **Step 2: Run batch tests and confirm failure**

Run: `python -m pytest tests/test_run_non_real_photo_agent_v1.py -v`

Expected: collection failure because the runner does not exist.

- [ ] **Step 3: Implement CLI and report writers**

Support `--images-dir`, `--out-dir`, `--run-name`, `--model`, `--cache-dir`, `--timeout-sec`, and `--limit`. Write per-image JSONL immediately, then CSV, detailed JSON, and summary JSON. Print progress without exposing credentials.

- [ ] **Step 4: Run batch tests**

Run: `python -m pytest tests/test_run_non_real_photo_agent_v1.py -v`

Expected: all tests pass.

- [ ] **Step 5: Run focused regression tests**

Run: `python -m pytest tests/test_non_real_photo_agent_v1.py tests/test_run_non_real_photo_agent_v1.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit the runner**

Commit runner and tests with message `feat: add batch runner for non-real photo agent`.

### Task 4: Preflight and Full 394-Image Evaluation

**Files:**
- Create at runtime: `reports/non_real_photo_agent_v1/<run-name>/...`

**Interfaces:**
- Consumes: CLI from Task 3.
- Produces: JSONL, CSV, detailed JSON, summary JSON, and cached model responses.

- [ ] **Step 1: Verify credentials without printing secrets**

Confirm `VISION_API_BASE_URL` and `VISION_API_KEY` are present. Stop before network calls if either is missing.

- [ ] **Step 2: Verify discovered count**

Run the CLI in discovery-only mode or call `discover_images` and confirm the count is 394. If the directory changes, report the actual count and continue only with the user's current full directory.

- [ ] **Step 3: Run a two-image smoke test**

Run with `--limit 2`, model `qwen3.7-plus`, one worker, a unique run name, and a fresh cache directory.

Expected: two parseable results or clearly separated execution failures.

- [ ] **Step 4: Run the complete directory**

Run synchronously with one worker. Report progress at intervals no longer than 60 seconds. Do not abort because individual images time out; record failures and continue.

- [ ] **Step 5: Validate the final report**

Confirm detail rows equal discovered images; `high_risk_non_real + manual_review + no_evidence + execution_failure = total`; every successful row has valid evidence codes; and no failed row is counted as `no_evidence`.

- [ ] **Step 6: Report interception metrics**

Report high-risk hit rate, manual-review hit rate, total interception rate, no-evidence miss rate, execution-failure rate, elapsed time, and token usage. List representative missed filenames for the next Prompt 2 iteration.

### Task 5: Final Verification

**Files:**
- Modify only if verification exposes defects in the new standalone files.

**Interfaces:**
- Consumes all prior tasks.
- Produces a verified handoff without production integration.

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_non_real_photo_agent_v1.py tests/test_run_non_real_photo_agent_v1.py -v`

Expected: all pass.

- [ ] **Step 2: Run existing relevant regression tests**

Run: `python -m pytest tests/test_guobu_v2_rules.py tests/photo_authenticity -v`

Expected: all pass; any pre-existing unrelated failure is documented separately.

- [ ] **Step 3: Confirm isolation**

Run: `git diff -- tools/run_guobu_model_audit_v2.py`

Expected: no diff.

- [ ] **Step 4: Commit final fixes if needed**

Commit only verified standalone-agent fixes with message `fix: harden non-real photo agent v1`.
