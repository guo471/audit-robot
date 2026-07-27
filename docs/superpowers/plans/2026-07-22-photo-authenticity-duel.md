# Photo Authenticity Duel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare the prompt-side edge-observation candidate and the narrow product-screen optics exemption, score them impartially, and retain a verified rollback path without changing unrelated audit rules.

**Architecture:** All experiments run outside the production decision path. A dedicated offline evaluator replays saved observations for candidate B, while a process-local wrapper swaps only the photo-authenticity addendum for candidate A. Production files remain unchanged until the judge selects a deployable candidate.

**Tech Stack:** Python 3.11, pytest, existing Guobu hybrid audit runner, JSON/JSONL reports, qwen3.7-plus.

## Global Constraints

- Only address non-real-photo false negatives and real-photo weak-evidence false positives.
- Keep `SN_POLICY_VERSION=v1`, `SN_CHAR_REVIEW_MODE=off`, and `SN_LABEL_AUTH_REVIEW_MODE=off`.
- Do not change category, activation-photo business, address, timeout, retry, or reporting rules.
- Merge and compare only by complete order ID and image ID, never by row position.
- Run one candidate at a time and stop on any hard-gate failure.
- Treat `481173405563867555430460` as uncertain, not a confirmed positive.
- Do not deploy a candidate merely because its source flow status is passed.

---

### Task 1: Freeze Baseline And Rollback State

**Files:**
- Create: `reports/photo_authenticity_duel_20260722/manifest.json`
- Create: isolated snapshot worktree under `.worktrees/photo-authenticity-duel-20260722`

**Interfaces:**
- Consumes: current dirty working tree, two combined JSON files, the 127-order workbook, and six-order edge review workbook.
- Produces: immutable hashes, canonical order sets, environment switches without secret values, and the exact snapshot commit used by later tasks.

- [ ] Record SHA-256 hashes and row/ID counts for all four baseline artifacts.
- [ ] Record the current hashes of `tools/photo_authenticity_mainline.py`, `tools/run_guobu_model_audit_v2.py`, and `tests/test_photo_authenticity_mainline.py`.
- [ ] Create a non-destructive Git snapshot with `git stash create`, then create the isolated worktree from that commit.
- [ ] Copy only required untracked prompt/tool files into the isolated worktree.
- [ ] Run `pytest tests/test_photo_authenticity_mainline.py -q`; expected result is 83 passing tests.
- [ ] Verify the main worktree hashes are unchanged.

### Task 2: Candidate B Offline Replay

**Files:**
- Create: `tools/photo_authenticity_duel.py`
- Create: `tests/test_photo_authenticity_duel.py`
- Create: `reports/photo_authenticity_duel_20260722/candidate_b_replay.json`

**Interfaces:**
- Consumes: final-attempt `photo_authenticity_by_image` observations from both combined JSON files.
- Produces: per-image and per-order old/new decisions for `R10_PRODUCT_SCREEN_OUTER_OPTICS_EXEMPT`.

- [ ] Write failing tests proving the candidate does not yet exempt product-screen-only `OUTER_PLANE_OPTICS` and rejects every failed guard.
- [ ] Run the focused tests and confirm the expected red failure.
- [ ] Implement the experimental predicate only in `tools/photo_authenticity_duel.py`:

```python
not effective_strong
and set(weak) == {"OUTER_PLANE_OPTICS"}
and observation.screen_owner == "product_screen"
and set(weak["OUTER_PLANE_OPTICS"].regions) == {"product_screen"}
and all(edge == "scene_continues" for edge in observation.edges.values())
```

- [ ] Run focused tests; expected result is green with R7 and R8 precedence unchanged.
- [ ] Replay both JSON files by complete order ID and emit the changed set with original status, old/new rule, and raw evidence.
- [ ] Verify the 1000-order changed set is exactly 12, all 12 source-passed, and none of the 25 source-failed orders change.
- [ ] Verify the 764-order changed set is exactly 6 and flag `481173343149167892889692` for manual labeling.
- [ ] Build contact sheets for all 18 changed orders and record human authenticity labels before candidate B can score as deployable.

### Task 3: Candidate A Prompt Shadow

**Files:**
- Create: `tools/run_photo_authenticity_prompt_shadow.py`
- Modify: `tests/test_photo_authenticity_duel.py`
- Create: `reports/photo_authenticity_duel_20260722/prompt_shadow/*`

**Interfaces:**
- Consumes: the unchanged production addendum and an experiment-only edge addendum.
- Produces: baseline and candidate raw observations without modifying the production constant on disk.

- [ ] Write failing tests for variant selection, exact baseline preservation, edge instructions, schema preservation, and forced `SN_LABEL_AUTH_REVIEW_MODE=off`.
- [ ] Run focused tests and confirm the expected red failures.
- [ ] Implement a wrapper that imports `tools.run_guobu_model_audit_v2`, replaces `PHOTO_AUTHENTICITY_COMPLIANCE_ADDENDUM` in process memory only, strips the experiment argument, and calls the existing `main()`.
- [ ] Append exactly this experiment-only observation block; do not alter the schema or enumeration list:

```text
填写screen_owner和evidence之前，必须按top、right、bottom、left顺序独立检查四侧；不得根据“整体像实拍”跳过边缘检查。

只有真实场景的纹理、透视或物体关系自然延伸到该侧图像外沿时，才输出scene_continues。若该侧存在近似等宽、笔直或规则弧形的深色带/黑边/显示区域边界，并使整张被展示内容在此终止，输出carrier_boundary；即使边带很窄、只覆盖该侧一部分，也不得输出scene_continues。

画面内部若出现包围另一幅完整内容的矩形屏幕或照片边界，不要求贴合上传图四边：明确时记录NESTED_IMAGE_BOUNDARY；能确认是外部显示设备时记录EXTERNAL_PHOTO_CARRIER并将screen_owner设为external_screen。

商品自身屏幕或机身黑边不属于外部载体：若边框只包围商品自身屏幕，且商品机身与环境关系连续，不得据此记录外部载体。边缘几何判断与LOCAL_MOIRE独立，不能因摩尔纹看似正常而把规则黑边改判为scene_continues。
```
- [ ] Build a canonical small sample: five confirmed positives, one uncertain order, and six manually confirmed product-screen/dark-edge negatives.
- [ ] Run preflight checks for exact task IDs, qwen3.7-plus, hybrid mode, workers=1, SN v1, and all optional SN plugins off.
- [ ] Run one fresh control round and one fresh candidate round using separate caches.
- [ ] Apply the early-stop gate. Stop if any confirmed positive remains fully missed, any negative gains strong external-carrier evidence, schema coverage is incomplete, or the uncertain order is asserted high-risk without evidence.
- [ ] Only if round one passes, run two more fresh rounds for each prompt variant.
- [ ] Verify candidate A reaches 15/15 order-level manual/high-risk results across three rounds and zero negative-control regressions.

### Task 4: Neutral Scoring And Selection

**Files:**
- Create: `reports/photo_authenticity_duel_20260722/final_scorecard.json`
- Create: `reports/photo_authenticity_duel_20260722/final_report.md`

**Interfaces:**
- Consumes: candidate B replay and human labels, candidate A raw rounds, token usage, elapsed time, changed sets, and stability results.
- Produces: an auditable score and deploy/no-deploy decision for each candidate.

- [ ] Score each candidate from 0 to 10 using frozen weights: effect 35%, safety 30%, stability 20%, cost 15%.
- [ ] Effect uses confirmed target corrections only; source flow status alone earns no correctness credit.
- [ ] Safety becomes zero if any confirmed non-real order is newly released or any confirmed real negative is newly blocked.
- [ ] Stability uses exact agreement across repeated candidate-A rounds and deterministic replay for candidate B.
- [ ] Cost includes incremental model calls, billed tokens, engineering surface, and rollback complexity.
- [ ] Have `Strategy Duel Agent` review the raw artifacts and score formula without seeing team ownership labels.
- [ ] Select the highest-scoring candidate per problem, not one global rule for both problems.
- [ ] If a candidate passes deployment gates, write its minimal production patch test-first in a separate commit; otherwise leave production unchanged.
- [ ] Re-run focused and full relevant tests, then verify the original main-worktree hashes or the selected minimal diff.

### Task 5: Apply The Selected Candidate R

**Files:**
- Modify: `tools/photo_authenticity_mainline.py:195-237`
- Modify: `tests/test_photo_authenticity_mainline.py`

**Interfaces:**
- Consumes: existing `ImageObservation` values and the frozen R predicate.
- Produces: one new production rule, `R10_PRODUCT_SCREEN_OUTER_OPTICS_EXEMPT`, with no changes to the prompt, model, SN, category, address, activation, timeout, retry, or reporting paths.

- [ ] Add a failing test for product-screen-only `OUTER_PLANE_OPTICS` with four continuous edges and no strong evidence; expected result is `no_evidence` with `R10_PRODUCT_SCREEN_OUTER_OPTICS_EXEMPT`.
- [ ] Add failing guard tests for external/uncertain/none owner, any non-product-screen region, empty regions, mixed weak evidence, any effective strong evidence, a non-continuous edge, R7, and R8; each must remain manual or high-risk.
- [ ] Run the focused tests and observe the expected RED failure before touching production code.
- [ ] Add the minimal predicate immediately after R8 and before the existing R9 fallback; do not alter existing R10 local-moire behavior.
- [ ] Run the focused authenticity tests and the Candidate B replay tests; expected result is all green, with replay order transitions 1000=12 and 764=6 and changed source-failed orders=0.
- [ ] Commit only this production rule and its tests as a separate rollback unit. Do not commit Candidate A wrapper, prompt artifacts, or experimental reports.
- [ ] Verify reverting this one commit restores the pre-candidate file hash and causes the new positive regression test to fail.

## Rollback Contract

- Before selection, rollback means deleting the isolated worktree and experiment outputs; production rule files remain byte-for-byte unchanged.
- After selection, each candidate is a separate commit. Reverting that single commit must restore the prior prompt/rule and its tests.
- Never combine candidate A and B in one production commit.
- Any added false negative, negative-control false positive, schema coverage error, or unexplained changed-set expansion triggers immediate rollback.
