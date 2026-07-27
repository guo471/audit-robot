# Guobu SN V2 Sidecar Design

## Goal

Build an isolated SN V2 implementation for A/B comparison without changing
`tools/run_guobu_model_audit_v2.py` or any non-SN policy.

## Boundaries

- V1 remains untouched and runnable.
- V2 has a name-based SN-only router with `HOME_APPLIANCE`, `PHONE`, `TABLET`,
  `WATCH`, `COMPUTER`, and `UNSUPPORTED` outcomes.
- Smart glasses use `HOME_APPLIANCE` only in the SN route. Watches and bands use
  `WATCH`. Cameras, headphones, and other unsupported digital products route to
  manual review.
- Each model request contains shared extraction rules, exactly one category
  rule, and one JSON schema. It never contains another category rule.
- The model never receives the system SN, its length, or a derived comparison
  hint. It reports evidence only.
- Local V2 code derives canonical values only from `raw_text`, applies the
  approved label whitelist, and owns all final comparisons and reason codes.
- Canonical comparison ignores case, spaces, and punctuation, but never
  substitutes, inserts, or deletes an alphanumeric character from `raw_text`.
- Evidence candidates must use `field_type=SN|SERIAL`, and every evidence
  `image_id` must belong to the activation-image input for the same order.
- Phone identity evidence can enable package fallback only when it comes from
  the device screen with an explicit matching IMEI/IMEI1/IMEI2/MEID/EID label.
- Identity completeness is checked locally: IMEI variants are 15 digits, MEID
  is 14 hexadecimal characters or 18 digits, and EID is 32 digits.
- `sn_readable` and `screen_identity_state` must agree with the submitted
  candidate evidence. Contradictory model output routes to manual review.
- Package fallback affects SN comparison only. Activation-photo compliance and
  authenticity remain independent and are not part of this sidecar.
- Existing confidence, authenticity, address, unboxing, duplicate-photo,
  timeout, reporting, and optional SN plugin behavior is not modified.

## Deliverables

- `tools/guobu_sn_policy_v2.py`: category routing, prompt construction, schema
  validation, canonicalization, and deterministic SN decisions.
- `tools/run_guobu_sn_v2.py`: standalone SN-only batch runner that reuses V1's
  transport and activation-image loading without modifying V1. Results are
  flushed incrementally in input order, and malformed tasks or worker failures
  are recorded as manual-review rows instead of terminating the batch.
- `tools/compare_guobu_sn_v1_v2.py`: deterministic row-by-ID comparison output.
  V1's `SN_ONLY_MATCH_NOT_FULL_AUDIT` wrapper is ignored when both versions
  report the same matching SN, so wrapper semantics do not create a false
  conclusion change.
- Focused tests for routing, prompt isolation, payload secrecy, all category
  decision branches, and comparison behavior.

## Decision Summary

- Home appliances: readable explicit body/package candidates; any exact match
  passes, all readable candidates mismatching yields `SN_MISMATCH`.
- Phone: a unique readable screen SN is authoritative. Otherwise package SN is
  allowed when screen SN evidence is unreadable or when a complete IMEI,
  IMEI1, IMEI2, MEID, or EID is visible.
- Tablet/watch: package SN is allowed only when screen SN evidence exists but is
  unreadable. Identity-only screens do not enable package fallback.
- Computer: only a unique readable screen/system-page SN is authoritative.
- Two distinct readable screen SN values yield `MODEL_UNCERTAIN`.
- Package-only tablet/watch/computer evidence yields `SN_NOT_FOUND` with the
  Chinese reason `未找到该品类要求的有效屏幕SN`.
