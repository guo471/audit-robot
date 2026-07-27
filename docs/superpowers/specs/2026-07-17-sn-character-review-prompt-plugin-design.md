# SN Character Review Prompt Plugin Design

**Goal:** Add a reversible prompt fragment for A/B testing stricter visual rereading of commonly confused SN characters.

## Behavior

- Keep the existing SN prompt byte-for-byte unchanged when the plugin is off.
- When enabled, append one fixed reviewed fragment to the SN prompt.
- The fragment only asks the model to reread visually similar characters from the source image.
- Do not change SN normalization, exact matching, candidate precedence, model calls, retries, timeouts, or photo-compliance behavior.
- Do not introduce fuzzy matching, edit-distance matching, prefix/suffix matching, or visual-character equivalence.

## Interface

- Python CLI: `--sn-char-review-mode off|on`, default `off` or `SN_CHAR_REVIEW_MODE`.
- Batch wrapper: `-EnableSnCharReview` passes `--sn-char-review-mode on`; otherwise it passes `off` behavior.
- Result rows record `sn_char_review_mode` for experiment traceability.
- The batch plan records whether the plugin is enabled.

## A/B Contract

- Use identical tasks and model settings.
- Use unique run names and separate cache directories.
- Compare the 55 confirmed false-positive samples against true SN-mismatch controls.
- Acceptance requires fewer false-positive interceptions and zero newly released true mismatches.

