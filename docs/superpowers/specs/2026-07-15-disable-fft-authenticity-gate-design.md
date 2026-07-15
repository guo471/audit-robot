# Disable FFT Authenticity Gate Design

Date: 2026-07-15

## Decision

Disable FFT as an order-decision input. Structured model evidence remains authoritative: any valid strong evidence or weak evidence transfers an otherwise-pass order to manual review. Model prose such as “实拍” cannot cancel populated evidence fields.

## Runtime behavior

- Add `PHOTO_AUTHENTICITY_FFT_ENABLED`; default is `false`.
- When disabled, no artifact is loaded, no image is decoded for FFT, no score is computed, and `no_evidence` remains `no_evidence`.
- Existing strong-evidence rules continue to produce `high_risk_non_real` or manual review.
- Any remaining valid strong evidence and any weak evidence produce `manual_review`.
- A bare `abrupt_cutoff` does not produce manual review. It requires `EDGE_CUTOFF` weak evidence; high risk still requires `abrupt_cutoff + OUTER_PLANE_OPTICS`.
- The FFT implementation, frozen artifact, report columns, and optional explicit enable switch remain for future experiments.
- No additional model call is introduced.

## Reporting and rollback

- With FFT disabled, `photo_authenticity_fft_count` is zero and image results contain no FFT score.
- Existing reason codes remain compatible.
- Explicitly setting `PHOTO_AUTHENTICITY_FFT_ENABLED=true` restores the frozen `0.995` FFT path for controlled testing only.

## Verification

- Test that default enforce mode does not load the FFT artifact or read image files.
- Test that explicit FFT enable preserves the frozen path.
- Test that every weak evidence code routes to manual review even when `reason` says the image is real.
- Test that a bare normal crop marked only as `abrupt_cutoff` does not route to manual review.
- Run the existing authenticity, Guobu rules, and adversarial suites.
