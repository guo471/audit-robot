from pathlib import Path

import numpy as np
from PIL import Image

from tools.run_black_edge_shadow_eval import build_manifest, compute_metrics, run_evaluation


def save_image(path: Path, value: int = 245):
    Image.fromarray(np.full((40, 40, 3), value, dtype=np.uint8)).save(path)


def test_build_manifest_labels_real_non_real_and_extra(tmp_path):
    real_dir = tmp_path / "real"
    non_real_dir = tmp_path / "non_real"
    real_dir.mkdir()
    non_real_dir.mkdir()
    real_image = real_dir / "real_001.jpg"
    non_real_image = non_real_dir / "R001_A.jpg"
    extra_image = tmp_path / "missed.jpg"
    save_image(real_image)
    save_image(non_real_image, 10)
    save_image(extra_image, 10)
    manifest = real_dir / "manifest.csv"
    manifest.write_text('"文件名","订单号"\n"real_001.jpg","ORDER1"\n', encoding="utf-8")

    rows = build_manifest(manifest, non_real_dir, [extra_image])

    assert {row["label"] for row in rows} == {"real", "non_real"}
    assert any(row["split"] == "development_extra" for row in rows)


def test_compute_metrics_reports_candidate_recall_and_real_candidate_rate():
    rows = [
        {"label": "non_real", "status": "strong_candidate", "split": "library"},
        {"label": "non_real", "status": "none", "split": "library"},
        {"label": "real", "status": "uncertain_candidate", "split": "library"},
        {"label": "real", "status": "none", "split": "library"},
    ]

    metrics = compute_metrics(rows)

    assert metrics["non_real_library"]["candidate_recall"] == 0.5
    assert metrics["real_library"]["candidate_rate"] == 0.5


def test_run_evaluation_writes_json_and_csv(tmp_path):
    image = tmp_path / "image.jpg"
    save_image(image)
    output = tmp_path / "out"

    summary = run_evaluation(
        [{"path": str(image), "label": "real", "split": "library", "group_id": "G1"}],
        output,
    )

    assert summary["image_count"] == 1
    assert (output / "results.jsonl").exists()
    assert (output / "results.csv").exists()
    assert (output / "summary.json").exists()
