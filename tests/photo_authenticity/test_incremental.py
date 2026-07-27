from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from PIL import Image

from photo_authenticity.hashing import sha256_file
from photo_authenticity.incremental import (
    IncrementalPolicyError,
    PromotionPolicy,
    append_confirmed_samples,
    compare_releases,
)
from photo_authenticity.manifest import MANIFEST_COLUMNS, ManifestRow


def _image(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (8, 8), color).save(path)
    return path


def _write_manifest(path: Path, rows: list[ManifestRow]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    return path


@pytest.fixture
def previous(tmp_path):
    paths = [
        _image(tmp_path / "locked.png", (1, 2, 3)),
        _image(tmp_path / "challenge.png", (4, 5, 6)),
        _image(tmp_path / "development.png", (7, 8, 9)),
    ]
    rows = [
        ManifestRow(
            sample_id,
            str(path),
            sha256_file(path),
            label,
            "confirmed",
            group,
            "",
            "existing",
            split,
            "order_id",
            f"order_id={group}",
        )
        for sample_id, path, label, group, split in (
            ("LOCKED", paths[0], "non_real", "g-locked", "locked"),
            ("CHALLENGE", paths[1], "real", "g-challenge", "challenge"),
            ("DEV", paths[2], "real", "g-dev", "train"),
        )
    ]
    manifest = _write_manifest(tmp_path / "manifest-v1.csv", rows)
    return type("Previous", (), {"path": manifest, "rows": rows})()


@pytest.fixture
def additions(tmp_path):
    path = _image(tmp_path / "addition.png", (10, 11, 12))
    row = ManifestRow(
        "NEW",
        str(path),
        sha256_file(path),
        "non_real",
        "confirmed",
        "g-new-device",
        "",
        "human_labeled_addition",
        "",
        "order_id",
        "order_id=g-new-device",
    )
    csv_path = _write_manifest(tmp_path / "additions.csv", [row])
    return type("Additions", (), {"path": csv_path, "rows": [row]})()


def test_incremental_update_preserves_old_locked_and_challenge_memberships(
    previous, additions, tmp_path
) -> None:
    before_bytes = previous.path.read_bytes()

    result = append_confirmed_samples(
        previous.path, additions.path, tmp_path / "manifest-v2.csv"
    )
    before = {
        (row.sample_id, row.split)
        for row in previous.rows
        if row.split in {"locked", "challenge"}
    }
    after = {
        (row.sample_id, row.split)
        for row in result.rows
        if row.sample_id in {item[0] for item in before}
    }

    assert after == before
    assert previous.path.read_bytes() == before_bytes
    assert next(row for row in result.rows if row.sample_id == "NEW").split == "challenge_v1"
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["parent_manifest_sha256"] == sha256_file(previous.path)
    assert metadata["retraining_initialization"] == "official_imagenet"


@pytest.mark.parametrize("mutation", ["weak", "bad_hash", "missing_group_evidence"])
def test_incremental_rejects_unconfirmed_or_untraceable_additions(
    previous, additions, tmp_path, mutation
) -> None:
    row = additions.rows[0]
    values = asdict(row)
    if mutation == "weak":
        values["label_status"] = "weak_label"
    elif mutation == "bad_hash":
        values["sha256"] = "0" * 64
    else:
        values["source_group"] = ""
        values["source_group_basis"] = "inferred_visual"
        values["source_group_evidence"] = ""
    invalid = _write_manifest(tmp_path / f"{mutation}.csv", [ManifestRow(**values)])

    with pytest.raises(IncrementalPolicyError):
        append_confirmed_samples(previous.path, invalid, tmp_path / "output.csv")


def _report(release: str, **updates):
    report = {
        "release": release,
        "evaluation_population_hash": "e" * 64,
        "manifest_sha256": "m" * 64,
        "confirmed_non_real_recall": 0.95,
        "missed_confirmed_non_real_ids": ["N1"],
        "real_to_manual_review_rate": 0.20,
        "bundle_ok": True,
        "equivalence_ok": True,
        "hashes_valid": True,
        "cpu_p95_ms": 50.0,
        "cpu_max_ms": 70.0,
        "challenge_sets": ["locked-v1", "challenge-v1"],
        "label_status_counts": {"confirmed": 34, "weak_label": 0},
    }
    report.update(updates)
    return report


def test_release_comparison_allows_only_non_regressing_verified_candidate() -> None:
    old = _report("old-v1")
    new = _report(
        "new-v2",
        confirmed_non_real_recall=0.97,
        missed_confirmed_non_real_ids=[],
        real_to_manual_review_rate=0.22,
    )
    policy = PromotionPolicy(0.30, 80.0, 100.0)

    decision = compare_releases(old, new, policy)

    assert decision.eligible_for_shadow_replacement is True
    assert decision.reasons == ()
    assert (decision.old_release, decision.new_release) == ("old-v1", "new-v2")


@pytest.mark.parametrize(
    "updates,expected_reason",
    [
        ({"confirmed_non_real_recall": 0.90}, "non_real_recall_regressed"),
        ({"missed_confirmed_non_real_ids": ["N1", "N2"]}, "new_confirmed_non_real_miss"),
        ({"challenge_sets": ["locked-v1"]}, "evaluation_population_changed"),
        ({"label_status_counts": {"confirmed": 0, "weak_label": 97}}, "weak_label_only_evidence"),
        ({"hashes_valid": False}, "hash_verification_failed"),
    ],
)
def test_regression_missing_challenge_weak_only_and_hash_errors_block_replacement(
    updates, expected_reason
) -> None:
    decision = compare_releases(
        _report("old-v1"),
        _report("new-v2", **updates),
        PromotionPolicy(0.30, 80.0, 100.0),
    )

    assert decision.eligible_for_shadow_replacement is False
    assert expected_reason in decision.reasons
