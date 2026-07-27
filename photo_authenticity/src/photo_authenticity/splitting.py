from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold

from .manifest import ManifestRow


FORMAL_MINIMUMS = {"non_real": 14, "real": 20}


@dataclass(frozen=True)
class ExploratoryFold:
    index: int
    train_rows: tuple[ManifestRow, ...]
    validation_rows: tuple[ManifestRow, ...]

    @property
    def train_ids(self) -> tuple[str, ...]:
        return tuple(row.sample_id for row in self.train_rows)

    @property
    def validation_ids(self) -> tuple[str, ...]:
        return tuple(row.sample_id for row in self.validation_rows)


@dataclass(frozen=True)
class SplitPlan:
    exploratory_folds: tuple[ExploratoryFold, ...]
    formal_locked: tuple[ManifestRow, ...]
    formal_status: str
    manifest_sha256: str
    exploratory: bool
    shortage_counts: dict[str, int]
    seed: int
    folds: int
    development_rows: tuple[ManifestRow, ...]

    def exploratory_training_rows(self) -> tuple[ManifestRow, ...]:
        return self.development_rows

    def formal_evaluation_rows(self) -> tuple[ManifestRow, ...]:
        return self.formal_locked


def _manifest_hash(rows: Sequence[ManifestRow]) -> str:
    serialized = json.dumps(
        [asdict(row) for row in sorted(rows, key=lambda item: item.sample_id)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _stable_group_order(groups: set[str], seed: int) -> list[str]:
    return sorted(
        groups,
        key=lambda group: hashlib.sha256(f"{seed}:{group}".encode("utf-8")).hexdigest(),
    )


def _select_locked_groups(rows: Sequence[ManifestRow], seed: int) -> set[str]:
    by_group: dict[str, list[ManifestRow]] = {}
    for row in rows:
        by_group.setdefault(row.source_group, []).append(row)
    eligible_groups = {
        group
        for group, members in by_group.items()
        if members and all(member.label_status == "confirmed" for member in members)
    }
    ordered = _stable_group_order(eligible_groups, seed)
    selected: set[str] = set()
    counts = {"non_real": 0, "real": 0}
    for label in ("non_real", "real"):
        for group in ordered:
            if counts[label] >= FORMAL_MINIMUMS[label]:
                break
            if group in selected:
                continue
            matching = sum(member.label == label for member in by_group[group])
            if matching:
                selected.add(group)
                for member in by_group[group]:
                    if member.label in counts:
                        counts[member.label] += 1
    if any(counts[label] < minimum for label, minimum in FORMAL_MINIMUMS.items()):
        raise ValueError("confirmed rows cannot form a group-isolated formal lock")
    return selected


def create_splits(
    rows: Sequence[ManifestRow],
    seed: int = 20260713,
    folds: int = 5,
    previous_plan: SplitPlan | None = None,
) -> SplitPlan:
    eligible = sorted(
        (
            row
            for row in rows
            if row.label in {"real", "non_real"} and row.label_status in {"confirmed", "weak_label"}
        ),
        key=lambda row: row.sample_id,
    )
    confirmed_counts = {
        label: sum(row.label == label and row.label_status == "confirmed" for row in eligible)
        for label in FORMAL_MINIMUMS
    }
    shortages = {
        label: max(0, minimum - confirmed_counts[label])
        for label, minimum in FORMAL_MINIMUMS.items()
    }
    runnable = not any(shortages.values())
    locked: tuple[ManifestRow, ...] = ()
    locked_groups: set[str] = set()
    if runnable:
        if previous_plan is not None and previous_plan.formal_locked:
            locked_ids = {row.sample_id for row in previous_plan.formal_locked}
            current_by_id = {row.sample_id: row for row in eligible}
            if not locked_ids.issubset(current_by_id):
                raise ValueError("previous formal lock member is missing")
            locked = tuple(sorted((current_by_id[item] for item in locked_ids), key=lambda row: row.sample_id))
            locked_groups = {row.source_group for row in locked}
        else:
            locked_groups = _select_locked_groups(eligible, seed)
            locked = tuple(
                row
                for row in eligible
                if row.source_group in locked_groups and row.label_status == "confirmed"
            )

    development = tuple(row for row in eligible if row.source_group not in locked_groups)
    if not development:
        raise ValueError("no rows remain for exploratory development")
    labels = np.asarray([1 if row.label == "non_real" else 0 for row in development], dtype=np.int64)
    groups = np.asarray([row.source_group for row in development], dtype=object)
    splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    exploratory_folds: list[ExploratoryFold] = []
    for index, (train_indices, validation_indices) in enumerate(
        splitter.split(np.zeros(len(development)), labels, groups), start=1
    ):
        exploratory_folds.append(
            ExploratoryFold(
                index=index,
                train_rows=tuple(development[item] for item in train_indices),
                validation_rows=tuple(development[item] for item in validation_indices),
            )
        )

    plan = SplitPlan(
        exploratory_folds=tuple(exploratory_folds),
        formal_locked=locked,
        formal_status="runnable" if runnable else "not_runnable_insufficient_confirmed_data",
        manifest_sha256=_manifest_hash(rows),
        exploratory=any(row.label_status == "weak_label" for row in development),
        shortage_counts=shortages,
        seed=seed,
        folds=folds,
        development_rows=development,
    )
    assert_no_group_leakage(plan)
    return plan


def assert_no_group_leakage(split_plan: SplitPlan) -> None:
    locked_groups = {row.source_group for row in split_plan.formal_locked}
    development_groups = {row.source_group for row in split_plan.development_rows}
    if locked_groups & development_groups:
        raise AssertionError("source_group crosses formal and exploratory sets")

    validation_group_occurrences: dict[str, int] = {}
    for fold in split_plan.exploratory_folds:
        train_groups = {row.source_group for row in fold.train_rows}
        validation_groups = {row.source_group for row in fold.validation_rows}
        if train_groups & validation_groups:
            raise AssertionError(f"source_group leakage in fold {fold.index}")
        for group in validation_groups:
            validation_group_occurrences[group] = validation_group_occurrences.get(group, 0) + 1
    if any(count != 1 for count in validation_group_occurrences.values()):
        raise AssertionError("each development source_group must validate exactly once")
