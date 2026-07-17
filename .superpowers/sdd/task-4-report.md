# Task 4 completed-data validation

Status: `DONE_WITH_CONCERNS`

## Scope and inputs

- Validation date: 2026-07-17 (Asia/Shanghai)
- Read-only completed source: `C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit\guobu_652_all_first_round.jsonl`
- Offline generator: `C:\Users\HUAWEI\Desktop\audit_robot\.worktrees\guobu-report-redesign\tools\guobu_audit_report.py`
- XLSX output: `C:\Users\HUAWEI\Desktop\audit_robot\.worktrees\guobu-report-redesign\.superpowers\sdd\task-4-output\guobu-652-offline-20260717-114814.xlsx`
- Trace JSON output: `C:\Users\HUAWEI\Desktop\audit_robot\.worktrees\guobu-report-redesign\.superpowers\sdd\task-4-output\guobu-652-offline-20260717-114814.json`
- The generator was invoked without retry inputs, pricing arguments, model access, or API access.
- The active 554-order JSONL was not read as final. No main-workspace report and no shared skill file was modified.

## Offline invocation

```powershell
python tools\guobu_audit_report.py `
  --first-jsonl 'C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit\guobu_652_all_first_round.jsonl' `
  --output-xlsx '.superpowers\sdd\task-4-output\guobu-652-offline-20260717-114814.xlsx' `
  --output-json '.superpowers\sdd\task-4-output\guobu-652-offline-20260717-114814.json'
```

Exit code was 0. Pricing is explicitly `null`; workbook/JSON cost is `待配置`.

## Independent validation

The source JSONL was independently parsed rather than trusting the generated summary.

| Check | Independent result | Generated result | Status |
|---|---:|---:|---|
| Source rows | 652 | 652 detail rows | PASS |
| Unique order IDs | 652 | 652 detail rows | PASS |
| Manual rows | 124 | 124 | PASS |
| Automatic rows | 528 | 528 | PASS |
| Manual + automatic | 652 | 652 | PASS |
| `未通过` manual numerator | 15 | 15 | PASS |
| `未通过` denominator | 15 | 15 | PASS |
| `已通过` manual numerator | 109 | 109 | PASS |
| `已通过` denominator | 637 | 637 | PASS |
| First-attempt elapsed seconds | 14588.24000000001 | 14588.24000000001 | PASS |
| Effective hours | 4.052288888888892 | 4.052288888888892 | PASS |
| Billed non-cached input tokens | 7,825,152 | 7,825,152 | PASS |
| Billed cached input tokens | 0 | 0 | PASS |
| Billed output tokens | 636,497 | 636,497 | PASS |

Workbook structure and safety checks passed:

- Exactly two sheets: `明细表`, `汇总表`.
- Exactly eight required detail headers in the required order.
- All non-empty order IDs, system SNs, and model SNs round-trip as Excel text with `@` number format.
- UTF-8 JSON contains valid Chinese.
- No `??` strings were found in workbook cells.
- No Excel error values or error tokens were found; nine intended formula cells were present.
- Summary numerators/denominators and JSON rates match the independent source counts.

## Usage coverage concern

Raw usage is available for 642 of 652 source rows (1,816 usage objects). Ten source rows have no raw usage object, so token accounting coverage is 98.47% by row, not 100%. There were no stage-cache objects in this historical source. The independently recoverable non-cached totals exactly match the trace/accounting totals, but the ten unavailable rows mean the totals must be described as "provider usage available in source," not guaranteed full-batch provider usage.

Elapsed coverage is complete at the row field level: all 652 first attempts were included. This source has no retry input, so retry elapsed/token contribution is zero.

## Automated tests

Command:

```powershell
python -m pytest tests\test_guobu_audit_report.py -q
```

Result: `99 passed in 3.30s` (exit code 0). `pytest` was initially absent from the local Python 3.11 environment and was installed as a test runner prerequisite. Validation exposed no generator bug, so no test or production code was changed and no code commit was created. Validated code HEAD: `c2e260814b7fa3a8d53ec1f527772b3d63e0b035`.

## Proposed post-554 invocation

Do not execute this until both the 554 batch and its original report have completed successfully. Replace the three bracketed values only with the immutable completed 554 artifacts selected during cutover; leave pricing arguments absent until verified prices exist.

```powershell
python 'C:\Users\HUAWEI\Desktop\audit_robot\tools\guobu_audit_report.py' `
  --first-jsonl 'C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit\[COMPLETED_554_FIRST].jsonl' `
  --retry-jsonl 'C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit\[COMPLETED_554_NETWORK_RETRY].jsonl' `
  --retry-selection-json 'C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit\[COMPLETED_554_RETRY_SELECTION].json' `
  --output-xlsx 'C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit\guobu_554_compact_postcutover.xlsx' `
  --output-json 'C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit\guobu_554_compact_postcutover.json'
```

If the completed batch has no legal network retries, omit all four lines containing `--retry-jsonl` and `--retry-selection-json`; do not create empty fake retry artifacts.

## Cutover checklist

1. Confirm the 554 audit process exited successfully and its original XLSX/JSON report finished and is readable.
2. Freeze and record the completed first JSONL, retry JSONL, and retry-selection JSON paths, sizes, modification times, and SHA-256 hashes.
3. Confirm the first JSONL line count and unique order count are both 554; never use a still-growing file.
4. Confirm retry selection and retry JSONL IDs match exactly, and every retry is a detected first-run network failure.
5. Copy the validated generator commit into the main branch through the normal reviewed integration path; do not edit the shared skill in place during the batch.
6. Run the exact invocation above with the frozen paths and new, non-colliding output names.
7. Repeat the independent count, status, elapsed, usage-coverage, UTF-8, sheet/header, text-cell, formula, and Excel-error checks documented here.
8. Compare the compact report against the preserved original report before changing any wrapper.
9. Switch the wrapper only after both reports and the independent validation are accepted; record the prior wrapper command and commit for rollback.

## Rollback checklist

1. Stop invoking the compact generator; do not delete or overwrite either the original or compact report artifacts.
2. Restore the recorded prior wrapper command/commit through a reviewed revert.
3. Run one completed fixture offline and verify the prior wrapper produces its expected original outputs.
4. Preserve the failed compact outputs, frozen source hashes, stderr, validation report, and code commit for diagnosis.
5. Do not rerun model auditing merely to repair report formatting; regenerate only from the frozen completed JSONL inputs.

## Concerns

- Raw token usage is absent for 10/652 historical rows; cost remains intentionally unconfigured.
- The exact active 554 completed artifact names do not yet exist as immutable final inputs, so the post-cutover command deliberately marks those three source names for replacement after completion. Executing it before that point is prohibited.

## Formal 554 integration regression (2026-07-17)

Formal integration exposed a retry-selection parser mismatch. The completed selection artifact is an object with `source_dirs`, numeric `requested`/`selected`, empty `missing`, an `orders` list, and `out_dir`; the generator previously recognized only a bare list or obsolete object keys and rejected the completed artifact before merge validation.

TDD evidence:

- RED command: `python -m pytest tests\test_guobu_audit_report.py -k 'completed_retry_selection_object or retry_selection_object_fails_closed or retry_selection_retains' -q`
- RED result: `1 failed, 5 passed, 99 deselected`; the CLI failed at `_retry_ids` with `ValueError: retry selection must be a JSON list` for the exact completed object shape.
- GREEN focused result: `6 passed, 99 deselected in 1.51s`.
- GREEN full result: `105 passed in 4.91s`.

The scoped parser fix now requires object selections to provide an `orders` list, an empty `missing` list, and non-negative integer-valued numeric `requested`/`selected` counts that equal each other and the number of orders. Bare-list selection artifacts remain compatible. Malformed objects and count mismatches fail closed. No main report was generated and the shared skill wrapper was not changed.
