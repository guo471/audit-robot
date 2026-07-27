# Black Edge Shadow Detector Design

## Goal

Build an isolated, image-only shadow detector that identifies geometric dark bands touching the four outer image edges. It must evaluate every image, report evidence without changing the production audit decision, and measure recall on known non-real samples against false-positive pressure on confirmed real samples.

## Scope

- Scan image pixels only; do not call the vision model.
- Do not modify prompts, R1-R10, SN rules, image-role rules, or production runners.
- Do not classify a detected band as proof of an external screen. Return `strong_candidate`, `uncertain_candidate`, or `none`.
- Scan all images supplied by the evaluator, including product, unboxing, and activation images.
- Add the five known missed edge cases without modifying the source sample directories.

## Detection contract

`scan_image(path) -> ImageScan` returns the image path, dimensions, one result for each side, and the highest image-level status.

Each side result records:

- `status`: `strong_candidate`, `uncertain_candidate`, or `none`;
- `dark_run_fraction`: longest connected near-dark run along the outer edge;
- `band_depth_fraction`: estimated inward depth of the dark band;
- `contrast`: brightness jump at the inner boundary;
- `boundary_fit`: whether the inner boundary is approximately straight or linearly tapered;
- `reason`: short machine-readable explanation.

The first implementation uses relative geometry so the same thresholds work across image sizes. A strong candidate needs an outer-touching dark run, a clear inner brightness transition, and a straight or linearly tapered boundary. A paired two-side/L-shape candidate may be strong even when each side is shorter. Dark pixels without a boundary, a short isolated corner, or a gradual textured shadow remain uncertain or none.

## Evaluation

- Positive set: `非实拍样本/非实拍样本` plus the five known missed edge images.
- Real control set: images listed by `实拍图样本/manifest.csv`.
- The original directories remain read-only for the evaluator.
- Report image-level and order-level counts separately. Extra known images are excluded from the library denominator and shown as a development gate.
- Produce JSON and CSV results, aggregate confusion metrics, and annotated images only for candidates.

## Rollback

The detector is not imported by production code and has no production mode. Removing the new detector, tests, and report directory restores the prior behavior exactly.
