# Shadow Release Contract

A release contains `model.onnx`, `thresholds.json`, `metadata.json` and `release.json`. Verify all hashes, `[real, non_real]` output order, preprocessing contract, manifest binding, self-test and `offline_shadow` mode before use. A failed check returns `manual_review`. Rollback selects an older verified shadow release directory.
