from __future__ import annotations

from photo_authenticity.manifest import ManifestRow
from photo_authenticity.splitting import assert_no_group_leakage, create_splits


def _rows(label: str, status: str, count: int, prefix: str) -> list[ManifestRow]:
    return [
        ManifestRow(
            sample_id=f"{prefix}{index:03d}",
            path=f"C:/synthetic/{prefix}{index:03d}.png",
            sha256=f"{index + (1000 if prefix.startswith('R') else 0):064x}",
            label=label,
            label_status=status,
            source_group=f"sg_{prefix}{index:03d}",
            order_id="",
            kind="synthetic",
        )
        for index in range(count)
    ]


def test_weak_labels_train_exploratorily_but_never_enter_formal_sets() -> None:
    manifest_rows = _rows("non_real", "confirmed", 20, "N") + _rows("real", "weak_label", 25, "RW")

    plan = create_splits(manifest_rows)

    assert any(row.label_status == "weak_label" for row in plan.exploratory_training_rows())
    assert all(row.label_status == "confirmed" for row in plan.formal_evaluation_rows())
    assert plan.exploratory is True
    assert plan.formal_status == "not_runnable_insufficient_confirmed_data"
    assert plan.shortage_counts == {"non_real": 0, "real": 20}


def test_five_group_folds_are_reproducible_and_leak_free() -> None:
    rows = _rows("non_real", "confirmed", 20, "N") + _rows("real", "weak_label", 25, "RW")

    first = create_splits(rows)
    shuffled = create_splits(list(reversed(rows)))

    assert len(first.exploratory_folds) == 5
    assert first.manifest_sha256 == shuffled.manifest_sha256
    assert [fold.validation_ids for fold in first.exploratory_folds] == [
        fold.validation_ids for fold in shuffled.exploratory_folds
    ]
    assert_no_group_leakage(first)


def test_runnable_formal_lock_contains_only_confirmed_minimums() -> None:
    rows = (
        _rows("non_real", "confirmed", 24, "N")
        + _rows("real", "confirmed", 30, "RC")
        + _rows("real", "weak_label", 10, "RW")
    )

    plan = create_splits(rows)
    locked = plan.formal_evaluation_rows()

    assert plan.formal_status == "runnable"
    assert sum(row.label == "non_real" for row in locked) >= 14
    assert sum(row.label == "real" for row in locked) >= 20
    assert all(row.label_status == "confirmed" for row in locked)
    assert_no_group_leakage(plan)


def test_previous_locked_membership_is_immutable() -> None:
    rows = _rows("non_real", "confirmed", 24, "N") + _rows("real", "confirmed", 30, "RC")
    previous = create_splits(rows)
    additions = _rows("real", "confirmed", 1, "NEW")

    current = create_splits(rows + additions, previous_plan=previous)

    assert {row.sample_id for row in current.formal_locked} == {
        row.sample_id for row in previous.formal_locked
    }
    assert all(row.sample_id != "NEW000" for row in current.formal_locked)
