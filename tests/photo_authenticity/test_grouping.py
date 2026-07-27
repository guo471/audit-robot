from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import photo_authenticity.grouping as grouping
from photo_authenticity.grouping import (
    VisualFingerprint,
    cluster_source_groups,
    compare_fingerprints,
)
from photo_authenticity.hashing import sha256_file
from photo_authenticity.manifest import ManifestRow


def _row(sample_id: str, path: Path) -> ManifestRow:
    return ManifestRow(
        sample_id=sample_id,
        path=str(path),
        sha256=sha256_file(path),
        label="non_real",
        label_status="confirmed",
        source_group=f"raw:{sample_id}",
        order_id="",
        kind="test",
    )


@pytest.fixture
def visual_variants(tmp_path):
    pixels = np.zeros((64, 64, 3), dtype=np.uint8)
    pixels[:, :32] = (240, 20, 20)
    pixels[:, 32:] = (20, 220, 40)
    base = tmp_path / "base.png"
    variant = tmp_path / "variant.png"
    unrelated = tmp_path / "unrelated.png"
    Image.fromarray(pixels).save(base, compress_level=0)
    Image.fromarray(pixels).save(variant, compress_level=9)
    Image.fromarray(pixels[:, ::-1].copy()).save(unrelated)
    return [_row("A", base), _row("B", variant), _row("C", unrelated)]


def test_visual_relation_is_grouped_and_marked_as_inferred(visual_variants) -> None:
    result = cluster_source_groups(visual_variants)
    a, b, unrelated = result.rows

    assert a.source_group == b.source_group
    assert b.source_group_basis == "inferred_visual"
    assert "dhash_distance=" in b.source_group_evidence
    assert unrelated.source_group != a.source_group


def test_group_ids_are_deterministic_for_shuffled_input(visual_variants) -> None:
    forward = cluster_source_groups(visual_variants)
    reverse = cluster_source_groups(list(reversed(visual_variants)))

    assert {row.sample_id: row.source_group for row in forward.rows} == {
        row.sample_id: row.source_group for row in reverse.rows
    }


def test_visual_threshold_boundaries_are_conservative() -> None:
    histogram = tuple(float(value) for value in range(16))
    base = VisualFingerprint("0000000000000000", 100, 100, histogram)
    distance_four = VisualFingerprint("000000000000000f", 100, 100, histogram)
    distance_five = VisualFingerprint("000000000000001f", 100, 100, histogram)
    wrong_aspect = VisualFingerprint("0000000000000000", 102, 100, histogram)

    assert compare_fingerprints(base, distance_four).accepted is True
    assert compare_fingerprints(base, distance_five).accepted is False
    assert compare_fingerprints(base, wrong_aspect).accepted is False

    x = np.arange(32, dtype=np.float64)
    x -= x.mean()
    z = np.sin(np.arange(32, dtype=np.float64))
    z -= z.mean()
    z -= x * np.dot(x, z) / np.dot(x, x)
    z *= np.linalg.norm(x) / np.linalg.norm(z)
    y = 0.97 * x + np.sqrt(1.0 - 0.97**2) * z
    correlation_097 = VisualFingerprint("0000000000000000", 100, 100, tuple(y))

    comparison = compare_fingerprints(
        VisualFingerprint("0000000000000000", 100, 100, tuple(x)), correlation_097
    )
    assert comparison.histogram_correlation == pytest.approx(0.97, abs=1e-6)
    assert comparison.accepted is False


def test_complete_linkage_prevents_transitive_visual_chain(monkeypatch, tmp_path) -> None:
    rows = []
    fingerprints = {
        "A.png": VisualFingerprint("0000000000000000", 100, 100, (0.0, 1.0, 2.0)),
        "B.png": VisualFingerprint("000000000000000f", 100, 100, (0.0, 1.0, 2.0)),
        "C.png": VisualFingerprint("00000000000000f0", 100, 100, (0.0, 1.0, 2.0)),
    }
    for name in fingerprints:
        path = tmp_path / name
        Image.new("RGB", (4, 4), (len(rows), 0, 0)).save(path)
        rows.append(_row(name[0], path))
    monkeypatch.setattr(grouping, "fingerprint_image", lambda path: fingerprints[Path(path).name])

    result = cluster_source_groups(rows)
    group_sizes = {}
    for row in result.rows:
        group_sizes[row.source_group] = group_sizes.get(row.source_group, 0) + 1

    assert sorted(group_sizes.values()) == [1, 2]
    assert any(not item["accepted"] for item in result.evidence)


def test_order_id_and_exact_sha_take_precedence(visual_variants) -> None:
    a, b, c = visual_variants
    ordered = [replace(a, order_id="same-order"), replace(c, order_id="same-order")]
    exact = replace(b, sha256=a.sha256)

    order_result = cluster_source_groups(ordered)
    sha_result = cluster_source_groups([a, exact])

    assert len({row.source_group for row in order_result.rows}) == 1
    assert all(row.source_group_basis == "order_id" for row in order_result.rows)
    assert len({row.source_group for row in sha_result.rows}) == 1
    assert all(row.source_group_basis == "exact_sha" for row in sha_result.rows)
