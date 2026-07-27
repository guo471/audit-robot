from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .hashing import sha256_file
from .manifest import MANIFEST_COLUMNS, ManifestRow, validate_manifest


class IncrementalPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class IncrementalManifestResult:
    rows: tuple[ManifestRow, ...]
    output_manifest: Path
    metadata_path: Path
    parent_manifest_sha256: str


@dataclass(frozen=True)
class PromotionPolicy:
    max_real_to_manual_review_rate: float
    max_cpu_p95_ms: float
    max_cpu_ms: float


@dataclass(frozen=True)
class PromotionDecision:
    eligible_for_shadow_replacement: bool
    reasons: tuple[str, ...]
    old_release: str
    new_release: str


def _read_validated_manifest(path: Path, name: str) -> tuple[ManifestRow, ...]:
    validation = validate_manifest(path)
    if not validation.ok:
        raise IncrementalPolicyError(f"invalid {name}: {'; '.join(validation.errors)}")
    return validation.rows


def _next_challenge_version(rows: tuple[ManifestRow, ...]) -> str:
    versions = [
        int(match.group(1))
        for row in rows
        if (match := re.fullmatch(r"challenge_v(\d+)", row.split))
    ]
    return f"challenge_v{max(versions, default=0) + 1}"


def append_confirmed_samples(
    previous_manifest: Path,
    additions_csv: Path,
    output_manifest: Path,
) -> IncrementalManifestResult:
    previous_path = previous_manifest.resolve()
    additions_path = additions_csv.resolve()
    output_path = output_manifest.resolve()
    if output_path in {previous_path, additions_path}:
        raise IncrementalPolicyError("incremental output must be a new file")
    parent_hash = sha256_file(previous_path)
    previous_rows = _read_validated_manifest(previous_path, "previous manifest")
    additions = _read_validated_manifest(additions_path, "additions")
    if not additions:
        raise IncrementalPolicyError("at least one confirmed addition is required")

    seen_ids = {row.sample_id for row in previous_rows}
    seen_hashes = {row.sha256 for row in previous_rows}
    split_by_group: dict[str, str] = {}
    for row in previous_rows:
        existing = split_by_group.setdefault(row.source_group, row.split)
        if existing != row.split:
            raise IncrementalPolicyError("previous source_group crosses split memberships")
    challenge_split = _next_challenge_version(previous_rows)
    accepted: list[ManifestRow] = []
    for row in additions:
        if row.label_status != "confirmed" or row.label not in {"real", "non_real"}:
            raise IncrementalPolicyError("additions require human-confirmed real/non_real labels")
        if not row.source_group:
            raise IncrementalPolicyError("addition requires an explicit source_group")
        if row.source_group_basis == "inferred_visual" and not row.source_group_evidence:
            raise IncrementalPolicyError("inferred grouping requires visual evidence")
        if not row.source_group_basis:
            raise IncrementalPolicyError("addition requires source_group_basis")
        if row.sample_id in seen_ids:
            raise IncrementalPolicyError(f"duplicate sample_id: {row.sample_id}")
        if row.sha256 in seen_hashes:
            raise IncrementalPolicyError(f"duplicate sha256: {row.sample_id}")
        if sha256_file(Path(row.path)) != row.sha256:
            raise IncrementalPolicyError(f"sha256 mismatch: {row.sample_id}")
        assigned_split = split_by_group.get(row.source_group, challenge_split)
        values = asdict(row)
        values["split"] = assigned_split
        updated = ManifestRow(**values)
        accepted.append(updated)
        seen_ids.add(row.sample_id)
        seen_hashes.add(row.sha256)
        split_by_group[row.source_group] = assigned_split

    all_rows = previous_rows + tuple(accepted)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(row) for row in all_rows)
    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    metadata_path.write_text(
        json.dumps(
            {
                "parent_manifest": str(previous_path),
                "parent_manifest_sha256": parent_hash,
                "output_manifest": str(output_path),
                "output_manifest_sha256": sha256_file(output_path),
                "added_sample_ids": [row.sample_id for row in accepted],
                "challenge_split": challenge_split,
                "retraining_initialization": "official_imagenet",
                "continue_previous_checkpoint": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return IncrementalManifestResult(all_rows, output_path, metadata_path, parent_hash)


def _load_report(report: Mapping[str, object] | Path) -> dict[str, object]:
    if isinstance(report, Path):
        value = json.loads(report.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        return value
    return dict(report)


def compare_releases(
    old_report: Mapping[str, object] | Path,
    new_report: Mapping[str, object] | Path,
    policy: PromotionPolicy,
) -> PromotionDecision:
    old = _load_report(old_report)
    new = _load_report(new_report)
    reasons: list[str] = []
    required = {
        "release",
        "evaluation_population_hash",
        "manifest_sha256",
        "confirmed_non_real_recall",
        "missed_confirmed_non_real_ids",
        "real_to_manual_review_rate",
        "bundle_ok",
        "equivalence_ok",
        "hashes_valid",
        "cpu_p95_ms",
        "cpu_max_ms",
        "challenge_sets",
        "label_status_counts",
    }
    missing = sorted((required - old.keys()) | (required - new.keys()))
    if missing:
        reasons.append("missing_required_metric")

    if old.get("evaluation_population_hash") != new.get("evaluation_population_hash"):
        reasons.append("evaluation_population_changed")
    old_challenges = old.get("challenge_sets")
    new_challenges = new.get("challenge_sets")
    if not old_challenges or old_challenges != new_challenges:
        reasons.append("evaluation_population_changed")

    try:
        if float(new["confirmed_non_real_recall"]) < float(old["confirmed_non_real_recall"]):
            reasons.append("non_real_recall_regressed")
        old_misses = set(old["missed_confirmed_non_real_ids"])
        new_misses = set(new["missed_confirmed_non_real_ids"])
        if new_misses - old_misses:
            reasons.append("new_confirmed_non_real_miss")
        if float(new["real_to_manual_review_rate"]) > policy.max_real_to_manual_review_rate:
            reasons.append("real_manual_review_rate_exceeded")
        if float(new["cpu_p95_ms"]) > policy.max_cpu_p95_ms:
            reasons.append("cpu_p95_exceeded")
        if float(new["cpu_max_ms"]) > policy.max_cpu_ms:
            reasons.append("cpu_max_exceeded")
    except (KeyError, TypeError, ValueError):
        reasons.append("missing_required_metric")

    if not all(
        report.get(field) is True
        for report in (old, new)
        for field in ("bundle_ok", "equivalence_ok", "hashes_valid")
    ):
        reasons.append("hash_verification_failed")
    status_counts = new.get("label_status_counts")
    if not isinstance(status_counts, dict) or int(status_counts.get("confirmed", 0)) <= 0:
        reasons.append("weak_label_only_evidence")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return PromotionDecision(
        eligible_for_shadow_replacement=not unique_reasons,
        reasons=unique_reasons,
        old_release=str(old.get("release", "unknown")),
        new_release=str(new.get("release", "unknown")),
    )
