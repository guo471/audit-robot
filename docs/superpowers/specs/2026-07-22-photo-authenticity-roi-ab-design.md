# Photo Authenticity ROI A/B Test Design

## Goal

Compare two reversible shadow-only approaches for recovering non-real-photo false negatives while preserving the current `qwen3.7-plus` model and all existing model variables. Select the approach with the best verified balance of safety, recall, latency, and cost.

## Scope

This experiment addresses only missed external display/photo-carrier evidence. It does not modify SN, address, product-type, activation, duplicate-image, or other audit rules. Candidate signals may only change an experimental shadow decision from `no_evidence` to `manual_review`; they may never cause automatic rejection or release an order already intercepted by the baseline.

The current six-order development set is:

- Positive: `481172702361737769779288`
- Positive: `481173139331953737728049`
- Positive: `481173341410837793996882`
- Positive: `481173353533504628326480`
- Positive: `491169415245669236736019`
- Negative control: `481173405563867555430460`

These six orders are excluded from blind scoring.

## Frozen Runtime

- Model: explicit `qwen3.7-plus`
- Mode: `hybrid`
- Workers: `1`
- SN policy: `v1`
- `SN_CHAR_REVIEW_MODE=off`
- `SN_LABEL_AUTH_REVIEW_MODE=off`
- `DIGITAL_ACTIVATION_EVIDENCE_MODE=on`
- Do not set or change `VISION_MODEL_NAME`.
- The shared baseline call is byte-identical for both arms. Candidate A and B use identical model request parameters, timeout/retry policy, montage bytes, and `detail=high` whenever both trigger. Candidate montage bytes intentionally differ from baseline source-image bytes; this is the tested ROI intervention, not a model-name or decoding-parameter change.
- A cached result is reusable only when model, prompt hash, input image SHA-256, and decoding parameters match exactly.
- The experiment uses an experiment-local cache fingerprint over normalized request fields and image bytes; it does not trust the production client's path-based cache key.

Before running candidates, freeze the actual production authenticity addendum from `tools/run_guobu_model_audit_v2.py`. The standalone `photo_authenticity/prompts/non_real_photo_auditor_v4.txt` is not treated as the production source until the source-of-truth drift is resolved.

## Available Data

The local labeled libraries contain:

- Real-photo library: 385 labeled image files grouped into 197 order IDs by `实拍图样本/manifest.csv`.
- Non-real-photo library: 394 labeled image files grouped into 154 independent `Rxxx` records.

All candidate and blind splits are order/record level, never image level. Before splitting, remove exact SHA-256 duplicates and near duplicates identified by pHash plus manual review. The same source image, crop, compression variant, or augmented copy may not appear in more than one split.

## Dataset Split

1. Development gate: the six named orders above. Used only for initial functionality and regression tests.
2. Calibration set: 40 non-real records and 40 real orders, selected with a fixed random seed after deduplication. This is the only set allowed for geometry threshold calibration.
3. Blind set: every remaining qualified labeled record, with a minimum of 50 non-real records and 100 real orders.
4. Stability subset: 30 blind orders selected before unblinding and run three times with fresh candidate caches.
5. Out-of-time subset: where source dates exist, reserve at least 20% from the newest available time window and report it separately.

Two reviewers independently verify the blind labels without seeing source-directory labels or candidate outputs. Source-directory labels are provenance only and do not count as either review. Disagreements go to a third reviewer. Ambiguous samples are excluded from real/non-real primary metrics and retained in a separate uncertainty set.

The data steward writes separate runtime bundles and a sealed label key before any blind calls. A runtime bundle contains only opaque sample/asset IDs, dimensions, byte lengths, hashes, and content-addressed assets; it contains no label, source group, path, order ID, provenance, partition, or date. The runner cannot accept or read the label key. Calibration features and blind results are generated without labels; only separate calibration/scoring processes may open the relevant sealed labels.

## Baseline

The six development orders use their frozen full-payload production authenticity observations and current local adjudicator records. The image-only real/non-real libraries cannot reconstruct the complete category payload, so their shared baseline is explicitly `isolated_authenticity`: the frozen runtime addendum plus an output-only wrapper and the current local adjudicator. Store complete order-level baseline records including image SHA-256, prompt hash, raw structured observations, final image decisions, token usage, latency, errors, and cache status.

Image-only results must record `production_equivalent=false`. They can select the stronger experiment arm for continued shadow work, but cannot support a production-rollout recommendation until a separate labeled full-payload replay passes the same gates.

The same baseline output feeds both candidate trigger policies. A and B must not issue different baseline calls.

## Candidate A: GeometryScout

Candidate A scans every source image with deterministic relative geometry. It does not classify or adjudicate.

- G1: outer bands between 0.5% and 12% of image depth with high edge connectivity, long straight inner boundary, and adjacent luminance discontinuity.
- G2: opposing near-parallel boundaries, or a partial outer boundary covering at least 25% of the corresponding side. A single partial boundary also requires an independent outer-plane optical signal.
- G3: a three- or four-sided nested rectangle or perspective quadrilateral enclosing 20% to 95% of image area.
- Repeated compatible geometry across images in the same order raises candidate confidence but never directly changes the decision.

Before paid review calls, run both selectors across the complete unlabeled bundle. For each triggered order, create one deterministic 2048x2048 evidence montage containing overviews of every order image and the deterministic union of the candidates that triggered either arm, with context, enlarged ROI, candidate type, stable panel ID, and normalized coordinates. The union is capped at six; overflow symmetrically fails that order for both arms instead of truncating. Submit the montage in one isolated `qwen3.7-plus` review call. If both arms trigger the order, the montage bytes and SHA-256 must be identical.

The model returns observation fields and valid montage panel references only; it does not return the final decision. Deterministic local Candidate A code changes the shadow result to `manual_review` only when the reviewer confirms an external carrier and points to the candidate region, or when the reviewer returns `uncertain` and deterministic geometry contains G1, paired G2, G3, or two independent signals.

## Candidate B: Baseline-Gated ROI Review

Candidate B uses a narrower selector:

- Trigger when the baseline result is `R10_PRODUCT_SCREEN_LOCAL_MOIRE_EXEMPT` and relative geometry finds an outer band, partial straight boundary, or central nested rectangle.
- Trigger when the baseline is R9/no-evidence with owner `none` or `uncertain` and relative geometry finds opposing boundaries.

For every triggered order, use the same deterministic order montage format and renderer as Candidate A. Each arm submits at most one isolated `qwen3.7-plus` review call per order.

The reviewer must output:

```json
{
  "panel_id": "F1",
  "outermost_layer": "direct_scene | product_device | external_display | uncertain",
  "pattern": "full_edge_band | opposite_edge_bands | partial_edge_with_outer_optics | central_nested_frame | none | uncertain",
  "visible_sides": ["bottom"],
  "content_terminates_at_boundary": true,
  "outer_plane_optics": false,
  "evidence_refs": ["F1"],
  "reason": "visible evidence only"
}
```

The model does not choose Candidate B's result. Deterministic local Candidate B code changes the shadow result to `manual_review` only for one of these frozen combinations:

- `full_edge_band` and `content_terminates_at_boundary=true`
- `opposite_edge_bands` with at least two visible sides
- `partial_edge_with_outer_optics` and `outer_plane_optics=true`
- `central_nested_frame` and `outermost_layer=external_display`

`uncertain` is frozen as `manual_review` for the candidate shadow result, never as high risk or automatic rejection.

## Failure Behavior

The experiment exposes independent `off | shadow` modes only. `enforce` and all production enablement are outside this experiment.

- Local image decode failure: record candidate service failure and preserve the baseline decision.
- Optional review timeout or schema failure: retry once, then record service failure and preserve the baseline decision during shadow.
- Never silently convert a service failure into either confirmed real or confirmed non-real.
- Compute each arm's trigger rate over the complete bundle before any paid candidate call; an arm above 10% is ineligible and makes no candidate calls. After at least 20 measured calls, stop only the affected arm when rolling review failure exceeds 2% or the configured token/latency budget is exceeded.
- `off` must bypass all candidate work and reproduce the baseline output.
- Candidate budgets and stop states are independent. A stopped arm is ineligible and cannot prevent the other arm from completing.

## Metrics

The primary unit is an order/record, not an image.

- Non-real safe-interception recall: proportion changed to `manual_review` or already safely intercepted by the baseline.
- Real-order direct-pass rate and new manual-review rate.
- Automatic rejection count caused by candidates; required value is zero.
- Trigger rate, review-call rate, schema/error rate, and three-run decision consistency.
- Total and per-order input, cached-input, output, and image tokens.
- Candidate local CPU time, review latency, and end-to-end p50/p95 latency increment.
- Incremental human workload from new manual-review decisions.

Report Wilson 95% intervals for proportions and paired bootstrap or McNemar comparisons against baseline and between candidates.

## Hard Gates

Development gate before blind execution:

- Each candidate safely intercepts at least four of five positive development orders in every run.
- Target is five of five.
- The negative-control order remains direct pass in all three runs.
- No candidate automatic rejection.
- Shadow, failure injection, and off-mode rollback tests pass.

Blind gate for recommendation:

- Zero candidate automatic rejections on real orders.
- Real-order new manual-review rate no greater than 5%; target no greater than 1%.
- Non-real safe-interception recall improves by at least 10 percentage points over the paired baseline.
- Candidate error rate below 1%.
- Trigger rate no greater than 10%.
- Total token increase no greater than 3%.
- Rollback reproduces the baseline.

If the total score difference is below three points or paired confidence intervals overlap materially, report no clear winner. A design that only passes the six development orders is not eligible for production recommendation.

## Outputs

- Frozen dataset manifest with anonymous sample IDs, group assignment, hashes, and label provenance.
- Candidate A and B version manifests with prompt/config hashes.
- Per-order baseline/A/B JSONL results.
- Comparison JSON and XLSX with effectiveness, risk, time, cost, and error metrics.
- A neutral scorecard applying risk 35, effect 35, time 15, and cost 15.
- Rollback instructions and an explicit recommendation of `continue shadow`, `no clear winner`, or `candidate X preferred for controlled rollout`.

## Non-Goals

- No training of a new neural model.
- No change to existing production model variables.
- No automatic rejection from candidate evidence.
- No production enablement during this experiment.
- No modification of unrelated audit rules.
