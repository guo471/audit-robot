# Digital Activation And Exact Duplicate Design

## Scope

This change contains two independent policies:

1. A switchable ordinary-3C activation-evidence prompt plugin backed by deterministic local validation.
2. An exact duplicate-photo rule backed only by local file identity.

No address, category, home-appliance, computer, product-photo, unboxing, R9, or photo-authenticity threshold policy changes are allowed.

## Activation Evidence

The environment and CLI switch is `DIGITAL_ACTIVATION_EVIDENCE_MODE=on|off`. It defaults to `on` and is appended only to the `ordinary_3c` compliance prompt. Turning it off preserves the current prompt and decision path.

The model reports one `activation_identity_by_image` item for each activation/SN image. Each item binds `image_id`, `screen_on`, `screen_source`, `page_type`, and `identity_fields` to the same image. Identity fields contain `field_type`, `raw_value`, `readable`, and `complete`.

For phones and tablets, one readable and complete `SN`, `SERIAL_NUMBER`, `IMEI1`, or `IMEI2` on a powered-on product screen satisfies the activation-photo form. `*#06#`, settings, about-device, and device-information pages are valid. This does not replace the existing first-stage system-SN consistency check.

For watches and bands, only a readable and complete `SN` or `SERIAL_NUMBER` on the product's powered-on screen is valid. IMEI, pairing pages, device names, QR codes, and model numbers are not valid watch identity evidence.

An affirmative external display, album, screenshot, nested-image, collage, or edit source cannot pass even when an identity value is readable. Missing or malformed structured evidence fails closed. When the plugin is on, legacy free-text fields cannot create an automatic pass.

## Exact Duplicate Photos

`DUPLICATE_IMAGE_EVIDENCE` is locally authoritative. The model may describe suspected duplicates for diagnostics, but its boolean and free text cannot trigger the reason code.

Images are grouped by SHA256 of the local file bytes. When a local file is unavailable, an identical source URL may be used as a conservative fallback identity. A duplicate block requires one identity bucket containing at least three distinct `image_id` values. Therefore:

- Two identical photos never trigger the duplicate rule.
- Four photos with three byte-identical files trigger the rule.
- Different angle, position, crop, perspective, lighting, background, or screen content does not trigger the rule.
- No perceptual hash, SSIM, histogram, crop matching, or similarity threshold is used.

If the duplicate rule does not trigger, all other compliance rules continue normally.

## Acceptance

- The three reported orders do not receive `DUPLICATE_IMAGE_EVIDENCE`.
- A synthetic four-image order with three identical files does receive it.
- Plugin off leaves the current ordinary-3C prompt and legacy activation gate unchanged.
- Plugin on affects only phones, tablets, watches, and bands within `ordinary_3c`.
- Home appliances remain exempt from powered-on-screen requirements.
- No new model call is added.
