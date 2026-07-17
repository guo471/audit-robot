# Guobu Audit Report Redesign

## Scope

Redesign only the final combined XLSX/JSON report produced after the Guobu audit batch. Do not change model prompts, audit ordering, reason selection, SN matching, photo-authenticity rules, address rules, duplicate-image decisions, or backend order state.

The report must use the first blocking reason already selected by the audit pipeline. It must not infer additional reasons after the pipeline has stopped.

The final row `manual_flag` is the sole authority for whether an order is manual. Parse only explicit `是`/`否` values and explicit booleans. A contradiction between `manual_flag` and the reason fields is a validation error, not something the report may guess. A manual row uses only its final primary `manual_reason_code`; arrays, model prose, and image flags must not be scanned for additional reasons.

Implementation starts in a new project-local report module and writes to new output paths. The currently running batch must finish before its shared skill merger or wrapper is changed. A growing JSONL must never be presented as a final report.

## Detail Sheet

The `明细表` contains exactly these business columns, in order:

1. `订单号`
2. `是否转人工`
3. `原始流程状态`
4. `转人工原因`
5. `系统SN`
6. `模型SN`
7. `SN是否一致`
8. `SN具体差别`

Order IDs and SN values are stored as Excel text. Automatically passed rows have an empty manual reason.

### Standard reason wording

| Primary reason code | Display text |
|---|---|
| `PRODUCT_TYPE_MISMATCH` | `商品类型不一致` |
| `PRODUCT_PHOTO_INVALID` | `商品照片不符合要求` |
| `UNBOXING_PHOTO_INVALID` | `拆封/安装照片不符合要求` |
| `ACTIVATION_PHOTO_INVALID` | `激活照片不符合要求` |
| `SN_MISSING_IN_ACTIVATION_PHOTO` | `激活照片不符合要求` |
| `ADDRESS_TOO_COARSE` | `收货地址不符合要求` |
| `DUPLICATE_IMAGE_EVIDENCE` | `存在重复图片，不符合要求` |
| `NON_REAL_PHOTO_REVIEW` | `图片疑似非实拍` |
| `NON_REAL_PHOTO_STRONG_RISK` | `图片疑似非实拍` |
| `IMAGE_STRONG_RISK` | `图片疑似非实拍` |
| `SN_MISMATCH` | `SN不一致` |
| `INVOICE_ORANGE_WARNING` | `发票疑似已红冲` |
| `MODEL_UNCERTAIN` | `图片信息无法确认` |
| `PHOTO_AUTHENTICITY_SERVICE_FAILURE` | `审核服务异常` |
| `ARTIFACT_LOAD_FAILURE` | `审核服务异常` |
| `FFT_FAILURE` | `审核服务异常` |
| `SN_TRUNCATED_OBSCURED` | `SN不完整，无法识别` |
| `SN_NOT_FOUND` | `SN无法识别` |
| `SYSTEM_SN_MISSING` | `系统SN缺失` |
| `IMAGE_MISSING` | `图片缺失` |
| `FIELD_MISSING` | `订单信息缺失` |
| `PRODUCT_TYPE_MISSING` | `商品类型信息缺失` |
| `NON_REAL_PHOTO_FFT_RESCUE` | `图片疑似非实拍` |

An unknown non-empty reason code displays `图片信息无法确认`, while the original reason code and model text remain in the combined JSON.

`DUPLICATE_IMAGE_EVIDENCE` is displayed only when the audit pipeline has already emitted it. The report generator does not decide whether two or three images are duplicates. The business rule remains: two duplicate images do not block; all three required evidence images being duplicates blocks.

### SN status and difference

`SN是否一致` uses this deterministic priority: explicit `sn_match=true` is `是`; an empty system SN is `无系统SN`; a non-empty system SN with an empty observed SN is `未读取`; two available unequal values are `否`. A stage skipped by an earlier blocker is therefore `未读取`, not `否`.

When inconsistent, `SN具体差别` deterministically describes the raw normalized strings without treating visually similar characters as equal. It covers substitution, insertion, deletion, adjacent transposition, truncation, and missing model SN. Examples:

- `第3位不同：系统O，模型0`
- `模型末尾少读J`
- `模型第1位多读S`
- `字符顺序不同：系统HV，模型VH`
- `模型未读取到SN`

Difference classification order is fixed: missing value, exact equality, one adjacent transposition, equal-length substitutions, strict prefix/suffix truncation, one-character insertion/deletion, then a conservative general difference. The report may reuse the audit normalizer for display only and must never change `sn_match` or apply visual-character equivalence.

The report does not implement the Apple Watch leading-S pass rule. If the audit pipeline later marks such a row as matched, the report may describe it as `Apple Watch系统首S格式归一` only when an explicit structured reason is available.

## Summary Sheet

The `汇总表` shows:

- 样本总数
- 转人工总数
- 自动通过总数
- 未通过拦截率
- 已通过误判率
- 输入Token
- 输出Token
- Token总消耗
- Token预计成本
- 有效审核总用时（小时）
- 人工预计用时（小时）
- 模型每小时审核量
- 效率倍数
- 效率提升率
- 预计节省人工时间（小时）

Formulas and definitions:

- `未通过拦截率 = 原始流程状态为未通过且转人工的订单数 / 原始流程状态为未通过的订单数`
- `已通过误判率 = 原始流程状态为已通过且转人工的订单数 / 原始流程状态为已通过的订单数`
- Pending/reviewing/unknown statuses are excluded from both denominators.
- Normalize surrounding whitespace and then match only exact `已通过` and `未通过`. The summary displays each numerator, denominator, and rate. A zero denominator displays `无可计算样本`, never `0%`.
- `人工每小时审核量 = 550 / 7.5 = 73.3333`
- `人工预计用时 = 样本总数 / 人工每小时审核量`
- `模型每小时审核量 = 样本总数 / 有效审核总用时`
- `效率倍数 = 模型每小时审核量 / 人工每小时审核量`
- `效率提升率 = 效率倍数 - 1`
- `预计节省人工时间 = 人工预计用时 - 有效审核总用时`

Effective audit time is the sum of actual per-order processing time for every first attempt plus every legal network-rerun attempt, converted to hours. It retains the first failed attempt and excludes computer sleep and idle wall-clock time. It is an accumulated per-order processing duration, not wall-clock batch throughput; this limitation is displayed with the efficiency metrics and becomes material if workers exceed one.

Input and output tokens are summed from raw usage objects for actual API calls in all consumed attempts. Stages marked `*_cached=true` are excluded from current-batch billed tokens even if their cached payload retains usage. Logical token volume, if retained in JSON, is separate and must not be called billed cost. Provider-reported cached input tokens use a separate configurable price when applicable. Token cost uses configurable normal-input, cached-input, and output prices per one million tokens. If verified pricing is unavailable, the report shows `待配置` rather than inventing a cost.

## Merge integrity

- First-run order IDs are non-empty and unique.
- Rerun order IDs are non-empty and unique.
- Every rerun ID must belong to the first-run network-failure selection.
- Duplicate IDs, unknown rerun IDs, missing first-run rows, or malformed final flags stop report generation.
- Network failure detection checks the item-level `_error` and the row-level `manual_reason`, `manual_reason_cn`, and `strategy`, using the same markers as retry selection.
- When an explicit retry-selection file is available, it is the authoritative list and is cross-validated against detected failures.
- A rerun result replaces the final business result only for its matching legal network-failed first-run order.

## Combined JSON

The combined JSON retains enough audit data to trace:

- original and final reason codes and texts;
- first-run and network-rerun source;
- system and observed SN;
- per-attempt elapsed time;
- input, output, cached, and total token usage where available;
- pricing assumptions used by the workbook;
- summary metric numerators and denominators.

## Presentation

- Use Arial consistently.
- Freeze the header and enable filters on the detail sheet.
- Use restrained blue headers and alternating table rows.
- Wrap manual reasons and SN differences.
- Use text formatting for IDs/SNs, `0.0%` for rates, `0.00` for hours and cost, and `0.0x` for efficiency multiples.
- Strings originating from external data that begin with `=`, `+`, `-`, or `@` are written as text to prevent formula injection.
- The workbook must contain no formula errors or encoding damage.

## Verification

Automated tests must cover:

1. Every confirmed reason-code mapping and the unknown-code fallback.
2. Only the primary blocking reason is displayed.
3. Duplicate-image reporting does not create a duplicate decision.
4. SN equal, substitution, insertion, deletion, transposition, truncation, and missing values.
5. Passed/failed numerator and denominator calculations, including zero-denominator and unknown-status cases.
6. First-run plus legal network-rerun token and elapsed-time accounting without double counting, including cached stages.
7. Input/output pricing and unavailable-price behavior.
8. Exact detail headers, text order IDs, expected sheet names, row count, percentages, hours, and absence of Excel errors.
9. Regeneration from existing JSONL without any model API call.
10. Explicit final `manual_flag` authority, reason/flag contradictions, and one-primary-reason behavior.
11. Duplicate or empty first-run IDs, duplicate or unknown rerun IDs, and item-level `_error` detection.
12. Formula-injection strings, leading-zero SNs, and 20-plus-digit order IDs round-trip as exact text.
