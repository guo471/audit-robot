# SN Similar-Character Review Plugin V2 Design

**Goal:** Add a second, mutually exclusive SN visual rereading prompt for controlled comparison with the existing plugin.

## Behavior

- Keep the base SN prompt unchanged in `off` mode.
- Keep existing `on` behavior byte-for-byte compatible as plugin v1.
- Add `v2` mode, which appends only the approved high-precision glyph prompt.
- Never append v1 and v2 together.
- Do not change SN candidate source priority, normalization, matching, uncertainty handling, retries, timeouts, photo authenticity, or compliance behavior.
- Keep the first SN read blind to `system_sn`; glyph rules are observation guidance, not equivalence or replacement rules.

## Interface

- Python CLI and `SN_CHAR_REVIEW_MODE`: `off|on|v2`, default `off`.
- Existing batch switch `-EnableSnCharReview`: select `on` (v1).
- New batch switch `-EnableSnCharReviewV2`: select `v2`.
- Supplying both switches fails before any audit call.
- `sn_only` rejects either character-review plugin because it uses a separate plain-text direct-OCR prompt.
- The shared auditing-skill wrapper exposes and forwards the v2 switch.
- Batch selection is explicit: `SN_CHAR_REVIEW_MODE` does not override absent wrapper switches.
- Result rows and run manifests record the exact selected mode.
- Plan output keeps the existing `snCharReview` boolean and adds `snCharReviewMode` for unambiguous experiments.

## Prompt Boundary

Plugin v2 only describes visible stroke structures for V/Y, 0/O/Q, 8/B, 5/S, 2/Z, 6/G, 1/I/L, J/U/L, and W/V/N. It requires rereading from the source image and defers unresolved characters to the base prompt's `SN_NOT_FOUND` behavior. It does not alter deterministic comparison or existing finite visual tolerance after model output.

## Acceptance

- `build_sn_prompt("off")` is exactly the base prompt.
- `build_sn_prompt("on")` is exactly base plus v1.
- `build_sn_prompt("v2")` is exactly base plus v2 and contains no v1 fragment.
- Invalid modes and simultaneous v1/v2 batch switches fail closed.
- `sn_only` plus v1 or v2 fails closed instead of producing misleading experiment metadata.
- Hybrid results and run manifests record `v2` when selected.
- Existing v1, label-authenticity, activation-evidence, and photo-authenticity tests remain green.
