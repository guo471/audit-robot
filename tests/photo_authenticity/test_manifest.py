from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PIL import Image

from photo_authenticity.manifest import (
    MANIFEST_COLUMNS,
    build_manifest,
    validate_manifest,
)


def _image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (16, 12), color).save(path)


def _candidate_csv(path: Path, image_paths: dict[str, Path]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "path", "order_id", "kind"])
        writer.writeheader()
        for sample_id, image_path in image_paths.items():
            writer.writerow(
                {
                    "sample_id": sample_id,
                    "path": image_path,
                    "order_id": f"order-{sample_id}",
                    "kind": "baseline_candidate",
                }
            )
    return path


@pytest.fixture
def synthetic_sources(tmp_path):
    non_real = tmp_path / "non_real"
    candidates = tmp_path / "candidates"
    non_real.mkdir()
    candidates.mkdir()
    _image(non_real / "screen.png", (255, 0, 0))
    paths: dict[str, Path] = {}
    for index, sample_id in enumerate(("S001", "S002", "S034", "S036"), start=1):
        image_path = candidates / f"{sample_id}.png"
        _image(image_path, (index, index * 2, index * 3))
        paths[sample_id] = image_path
    return non_real, _candidate_csv(tmp_path / "candidates.csv", paths)


def test_manifest_applies_approved_overrides(synthetic_sources, tmp_path) -> None:
    result = build_manifest(*synthetic_sources, tmp_path / "manifest-v1.csv")
    rows = {row.sample_id: row for row in result.rows}

    assert rows["S002"].label_status == "excluded"
    assert rows["S034"].label_status == "excluded"
    assert (rows["S036"].label, rows["S036"].label_status) == ("non_real", "confirmed")
    assert rows["S001"].label_status == "weak_label"
    assert tuple(result.output_path.read_text(encoding="utf-8").splitlines()[0].split(",")) == MANIFEST_COLUMNS


def test_manifest_full_approved_counts(tmp_path) -> None:
    non_real = tmp_path / "non_real"
    candidates = tmp_path / "candidates"
    non_real.mkdir()
    candidates.mkdir()
    for index in range(69):
        _image(non_real / f"NR{index:03d}.png", (index, index + 1, index + 2))
    candidate_paths: dict[str, Path] = {}
    for index in range(1, 101):
        sample_id = f"S{index:03d}"
        image_path = candidates / f"{sample_id}.png"
        _image(image_path, (index, (index * 3) % 256, (index * 7) % 256))
        candidate_paths[sample_id] = image_path

    result = build_manifest(
        non_real,
        _candidate_csv(tmp_path / "candidates.csv", candidate_paths),
        tmp_path / "manifest.csv",
    )

    source_non_real = [row for row in result.rows if row.kind == "non_real_source" and row.label_status == "confirmed"]
    weak_real = [row for row in result.rows if row.label == "real" and row.label_status == "weak_label"]
    assert len(source_non_real) == 69
    assert len(weak_real) == 97
    assert {row.sample_id for row in result.rows if row.label_status == "excluded"} >= {"S002", "S034"}
    assert next(row for row in result.rows if row.sample_id == "S036").label == "non_real"


def test_manifest_excludes_corrupt_and_duplicate_images(tmp_path) -> None:
    non_real = tmp_path / "non_real"
    candidates = tmp_path / "candidates"
    non_real.mkdir()
    candidates.mkdir()
    canonical = non_real / "canonical.png"
    _image(canonical, (12, 34, 56))
    duplicate = candidates / "S001.png"
    duplicate.write_bytes(canonical.read_bytes())
    corrupt = candidates / "S003.png"
    corrupt.write_bytes(b"not an image")

    result = build_manifest(
        non_real,
        _candidate_csv(tmp_path / "candidates.csv", {"S001": duplicate, "S003": corrupt}),
        tmp_path / "manifest.csv",
    )
    rows = {row.sample_id: row for row in result.rows}

    assert rows["S001"].label_status == "excluded"
    assert rows["S001"].exclusion_reason.startswith("duplicate_sha:")
    assert rows["S003"].label_status == "excluded"
    assert rows["S003"].exclusion_reason == "image_decode_failed"


def test_validate_manifest_reports_duplicate_id_and_missing_file(synthetic_sources, tmp_path) -> None:
    result = build_manifest(*synthetic_sources, tmp_path / "manifest.csv")
    rows = list(csv.DictReader(result.output_path.open(encoding="utf-8", newline="")))
    rows.append({**rows[0], "path": str(tmp_path / "missing.png")})
    invalid = tmp_path / "invalid.csv"
    with invalid.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    validation = validate_manifest(invalid)

    assert validation.ok is False
    assert any("duplicate sample_id" in error for error in validation.errors)
    assert any("missing file" in error for error in validation.errors)


def test_legacy_combined_manifest_preserves_rows_and_does_not_readd_source_directory(tmp_path) -> None:
    non_real = tmp_path / "non_real"
    candidates = tmp_path / "candidates"
    non_real.mkdir()
    candidates.mkdir()
    first = non_real / "first.png"
    second = non_real / "second.png"
    duplicate = non_real / "duplicate.png"
    _image(first, (10, 20, 30))
    _image(second, (40, 50, 60))
    duplicate.write_bytes(first.read_bytes())
    paths = {
        "S001": (first, "non_real"),
        "S005": (second, "non_real"),
    }
    for sample_id in ("S002", "S034", "S036", "S004"):
        path = candidates / f"{sample_id}.png"
        _image(path, (int(sample_id[1:]) % 255, 1, 2))
        paths[sample_id] = (path, "real")
    legacy = tmp_path / "legacy.csv"
    with legacy.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", "label", "path", "sha256", "order_id", "product_type", "role", "kind"],
        )
        writer.writeheader()
        for sample_id, (path, label) in paths.items():
            writer.writerow(
                {
                    "sample_id": sample_id,
                    "label": label,
                    "path": path,
                    "sha256": "ignored-and-recomputed",
                    "order_id": f"order-{sample_id}" if label == "real" else "",
                    "product_type": "",
                    "role": "",
                    "kind": "legacy",
                }
            )

    result = build_manifest(non_real, legacy, tmp_path / "manifest.csv")
    rows = {row.sample_id: row for row in result.rows}

    assert len(result.rows) == 6
    assert set(rows) == set(paths)
    assert sum(row.label == "non_real" and row.label_status == "confirmed" for row in result.rows) == 3
    assert rows["S004"].label_status == "weak_label"
    assert rows["S002"].label_status == rows["S034"].label_status == "excluded"
