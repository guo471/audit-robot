from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, UnidentifiedImageError

from .hashing import sha256_file


MANIFEST_COLUMNS = (
    "sample_id",
    "path",
    "sha256",
    "label",
    "label_status",
    "source_group",
    "order_id",
    "kind",
    "split",
    "source_group_basis",
    "source_group_evidence",
    "exclusion_reason",
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VALID_LABELS = {"real", "non_real", "unknown"}
VALID_STATUSES = {"confirmed", "weak_label", "excluded"}
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ManifestRow:
    sample_id: str
    path: str
    sha256: str
    label: str
    label_status: str
    source_group: str
    order_id: str
    kind: str
    split: str = ""
    source_group_basis: str = ""
    source_group_evidence: str = ""
    exclusion_reason: str = ""


@dataclass(frozen=True)
class ManifestBuildResult:
    rows: tuple[ManifestRow, ...]
    output_path: Path
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ManifestValidationResult:
    ok: bool
    rows: tuple[ManifestRow, ...]
    errors: tuple[str, ...]


def _decodable(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return False


def _resolved_candidate_path(value: str, csv_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = csv_path.parent / path
    return path.resolve()


def _initial_row(
    *, sample_id: str, path: Path, label: str, status: str, order_id: str, kind: str
) -> ManifestRow:
    exists = path.is_file()
    digest = sha256_file(path) if exists else ""
    exclusion_reason = ""
    if not exists:
        status = "excluded"
        exclusion_reason = "missing_file"
    elif not _decodable(path):
        status = "excluded"
        exclusion_reason = "image_decode_failed"
    basis = "order_id" if order_id else "exact_sha"
    group_key = order_id or digest or sample_id
    return ManifestRow(
        sample_id=sample_id,
        path=str(path),
        sha256=digest,
        label=label,
        label_status=status,
        source_group=f"raw:{group_key}",
        order_id=order_id,
        kind=kind,
        source_group_basis=basis,
        source_group_evidence=f"{basis}={group_key}",
        exclusion_reason=exclusion_reason,
    )


def _exclude_duplicate(rows: Iterable[ManifestRow]) -> tuple[ManifestRow, ...]:
    canonical_by_sha: dict[str, str] = {}
    output: list[ManifestRow] = []
    for row in rows:
        canonical = canonical_by_sha.get(row.sha256) if row.sha256 else None
        if canonical is not None:
            values = asdict(row)
            values["label_status"] = "excluded"
            values["exclusion_reason"] = f"duplicate_sha:{canonical}"
            output.append(ManifestRow(**values))
        else:
            if row.sha256:
                canonical_by_sha[row.sha256] = row.sample_id
            output.append(row)
    return tuple(output)


def build_manifest(
    non_real_dir: Path, real_candidates_csv: Path, output_csv: Path
) -> ManifestBuildResult:
    source_dir = non_real_dir.expanduser().resolve()
    candidates_path = real_candidates_csv.expanduser().resolve()
    rows: list[ManifestRow] = []
    errors: list[str] = []

    with candidates_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"sample_id", "path"}.issubset(reader.fieldnames):
            raise ValueError("candidate CSV requires sample_id and path columns")
        candidate_items = list(reader)
        combined_manifest = "label" in reader.fieldnames and any(
            (item.get("label") or "").strip() == "non_real" for item in candidate_items
        )

    source_paths = sorted(
        (path for path in source_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda path: path.as_posix().lower(),
    )
    if not combined_manifest:
        for index, path in enumerate(source_paths, start=1):
            rows.append(
                _initial_row(
                    sample_id=f"NR{index:06d}",
                    path=path.resolve(),
                    label="non_real",
                    status="confirmed",
                    order_id="",
                    kind="non_real_source",
                )
            )

    seen_candidate_ids: set[str] = set()
    for line_number, item in enumerate(candidate_items, start=2):
        sample_id = (item.get("sample_id") or "").strip()
        if not sample_id or sample_id in seen_candidate_ids:
            errors.append(f"line {line_number}: duplicate or empty sample_id {sample_id!r}")
            continue
        seen_candidate_ids.add(sample_id)
        original_label = (item.get("label") or "").strip()
        if sample_id in {"S002", "S034"}:
            label, status, approved_reason = "unknown", "excluded", "approved_override_excluded"
        elif sample_id == "S036":
            label, status, approved_reason = "non_real", "confirmed", ""
        elif combined_manifest and original_label == "non_real":
            label, status, approved_reason = "non_real", "confirmed", ""
        else:
            label, status, approved_reason = "real", "weak_label", ""
        row = _initial_row(
            sample_id=sample_id,
            path=_resolved_candidate_path(item.get("path") or "", candidates_path),
            label=label,
            status=status,
            order_id=(item.get("order_id") or "").strip(),
            kind=(item.get("kind") or ("non_real_source" if label == "non_real" else "baseline_candidate")).strip(),
        )
        if approved_reason and not row.exclusion_reason:
            values = asdict(row)
            values["exclusion_reason"] = approved_reason
            row = ManifestRow(**values)
        rows.append(row)

    if combined_manifest:
        directory_hashes = {sha256_file(path) for path in source_paths}
        manifest_non_real_hashes = {
            row.sha256
            for row in rows
            if row.label == "non_real" and row.sample_id != "S036" and row.sha256
        }
        if directory_hashes != manifest_non_real_hashes:
            errors.append(
                "combined manifest non-real SHA coverage differs from source directory: "
                f"directory={len(directory_hashes)},manifest={len(manifest_non_real_hashes)}"
            )

    final_rows = _exclude_duplicate(rows)
    output = output_csv.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(row) for row in final_rows)
    return ManifestBuildResult(final_rows, output, tuple(errors))


def validate_manifest(path: Path) -> ManifestValidationResult:
    errors: list[str] = []
    rows: list[ManifestRow] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != MANIFEST_COLUMNS:
            errors.append("manifest columns do not match the required schema")
            return ManifestValidationResult(False, (), tuple(errors))
        for line_number, item in enumerate(reader, start=2):
            row = ManifestRow(**{column: item.get(column, "") for column in MANIFEST_COLUMNS})
            rows.append(row)
            prefix = f"line {line_number} ({row.sample_id})"
            if row.sample_id in seen:
                errors.append(f"{prefix}: duplicate sample_id")
            seen.add(row.sample_id)
            if row.label not in VALID_LABELS:
                errors.append(f"{prefix}: invalid label")
            if row.label_status not in VALID_STATUSES:
                errors.append(f"{prefix}: invalid label_status")
            if not SHA_PATTERN.fullmatch(row.sha256):
                errors.append(f"{prefix}: invalid sha256")
            image_path = Path(row.path)
            if not image_path.is_file():
                errors.append(f"{prefix}: missing file")
            elif row.label_status != "excluded" and not _decodable(image_path):
                errors.append(f"{prefix}: image decode failed")
            elif SHA_PATTERN.fullmatch(row.sha256) and sha256_file(image_path) != row.sha256:
                errors.append(f"{prefix}: sha256 mismatch")
    return ManifestValidationResult(not errors, tuple(rows), tuple(errors))
