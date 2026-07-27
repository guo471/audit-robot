# Photo Authenticity Offline Shadow Runbook

This package is an offline shadow evaluator. It never produces a production approval or rejection. Public decisions are limited to `low_risk_candidate` and `manual_review`; there is a prohibition on production integration.

## Environment ownership

DevOps owns creation and maintenance of the isolated Python 3.11 environment, and environment installation is deferred to that DevOps process. The machine's Python 3.13 installation is not used or modified. The only permitted network operation is DevOps provisioning of official torchvision ImageNet weights; later reruns use the local cache. No image or manifest may be uploaded.

## Staging contract

- `data/input/non_real/`: confirmed non-real source images.
- `data/input/real_candidates.csv`: local baseline candidates with `sample_id,path,order_id,kind`.
- Generated manifests, splits, runs, releases, reports and JSONL logs remain under `photo_authenticity/`.

## Exact command sequence

```powershell
$env:PA_PYTHON='C:\Users\HUAWEI\Desktop\audit_robot\.venv-photo-auth\Scripts\python.exe'
& $env:PA_PYTHON -m photo_authenticity.cli check-env --config .\photo_authenticity\configs\base.toml
& $env:PA_PYTHON -m photo_authenticity.cli build-manifest --non-real-dir .\photo_authenticity\data\input\non_real --real-candidates .\photo_authenticity\data\input\real_candidates.csv --output .\photo_authenticity\data\manifests\manifest-v1.csv
& $env:PA_PYTHON -m photo_authenticity.cli group-sources --manifest .\photo_authenticity\data\manifests\manifest-v1.csv --output .\photo_authenticity\data\manifests\manifest-v1-grouped.csv --evidence .\photo_authenticity\reports\generated\grouping-v1.json
& $env:PA_PYTHON -m photo_authenticity.cli split --manifest .\photo_authenticity\data\manifests\manifest-v1-grouped.csv --output .\photo_authenticity\data\splits\split-v1.json
& $env:PA_PYTHON -m photo_authenticity.cli train --config .\photo_authenticity\configs\base.toml --split .\photo_authenticity\data\splits\split-v1.json --run-dir .\photo_authenticity\models\runs\run-v1
& $env:PA_PYTHON -m photo_authenticity.cli freeze-thresholds --predictions .\photo_authenticity\models\runs\run-v1\oof-predictions.csv --output .\photo_authenticity\models\runs\run-v1\thresholds.json
& $env:PA_PYTHON -m photo_authenticity.cli evaluate --run-dir .\photo_authenticity\models\runs\run-v1 --split .\photo_authenticity\data\splits\split-v1.json --output-dir .\photo_authenticity\reports\generated\run-v1
& $env:PA_PYTHON -m photo_authenticity.cli export-onnx --run-dir .\photo_authenticity\models\runs\run-v1 --release-dir .\photo_authenticity\models\releases\release-v1
& $env:PA_PYTHON -m photo_authenticity.cli verify-release --release .\photo_authenticity\models\releases\release-v1
& $env:PA_PYTHON -m photo_authenticity.cli infer-order --release .\photo_authenticity\models\releases\release-v1 .\photo_authenticity\data\input\shadow_order\1.jpg .\photo_authenticity\data\input\shadow_order\2.jpg .\photo_authenticity\data\input\shadow_order\3.jpg
& $env:PA_PYTHON -m photo_authenticity.cli benchmark --release .\photo_authenticity\models\releases\release-v1 --orders .\photo_authenticity\data\input\benchmark_orders.csv --output .\photo_authenticity\reports\generated\benchmark-v1.json
```

## Interpretation and rollback

Any result involving `weak_label` is `exploratory=true` and must be described only as exploratory. Formal evaluation remains `not_runnable_insufficient_confirmed_data` until at least 14 confirmed non-real and 20 confirmed real samples exist. Model, threshold, preprocessing, manifest and release hashes must verify before inference. Timeout, self-test, image, runtime or logging errors always mean `manual_review`.

Run the CPU benchmark on the target host with fixed thread count and report P50/P95/max without a latency promise. Incremental retraining appends human-confirmed rows, preserves locked/challenge memberships and restarts from official ImageNet initialization. Rollback means selecting the previous hash-verified shadow release; it never changes production behavior.
