from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from PIL import Image, ImageOps

from .manifest import ManifestRow


@dataclass(frozen=True)
class VisualFingerprint:
    dhash_hex: str
    width: int
    height: int
    hsv_histogram: tuple[float, ...]


@dataclass(frozen=True)
class VisualComparison:
    accepted: bool
    dhash_distance: int
    histogram_correlation: float
    aspect_delta: float


@dataclass(frozen=True)
class GroupingResult:
    rows: tuple[ManifestRow, ...]
    evidence: tuple[dict[str, object], ...]


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def fingerprint_image(path: Path) -> VisualFingerprint:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        gray = np.asarray(image.resize((9, 8), Image.Resampling.LANCZOS).convert("L"))
        bits = gray[:, 1:] > gray[:, :-1]
        hash_value = 0
        for bit in bits.ravel():
            hash_value = (hash_value << 1) | int(bit)

        rgb = np.asarray(image)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        histogram = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
        histogram = cv2.normalize(histogram, None, norm_type=cv2.NORM_L1).ravel()
    return VisualFingerprint(
        dhash_hex=f"{hash_value:016x}",
        width=width,
        height=height,
        hsv_histogram=tuple(float(value) for value in histogram),
    )


def compare_fingerprints(
    left: VisualFingerprint,
    right: VisualFingerprint,
    max_dhash_distance: int = 4,
    min_histogram_correlation: float = 0.98,
    max_aspect_delta: float = 0.01,
) -> VisualComparison:
    dhash_distance = (int(left.dhash_hex, 16) ^ int(right.dhash_hex, 16)).bit_count()
    left_histogram = np.asarray(left.hsv_histogram, dtype=np.float64)
    right_histogram = np.asarray(right.hsv_histogram, dtype=np.float64)
    if left_histogram.size != right_histogram.size or left_histogram.size < 2:
        correlation = float("-inf")
    elif np.array_equal(left_histogram, right_histogram):
        correlation = 1.0
    else:
        correlation = float(np.corrcoef(left_histogram, right_histogram)[0, 1])
        if not np.isfinite(correlation):
            correlation = float("-inf")
    left_aspect = left.width / left.height
    right_aspect = right.width / right.height
    aspect_delta = abs(left_aspect - right_aspect)
    accepted = (
        dhash_distance <= max_dhash_distance
        and correlation >= min_histogram_correlation
        and aspect_delta <= max_aspect_delta
    )
    return VisualComparison(accepted, dhash_distance, correlation, aspect_delta)


def _stable_group_id(rows: Sequence[ManifestRow], members: Sequence[int]) -> str:
    member_hashes = sorted(rows[index].sha256 for index in members)
    digest = hashlib.sha256("\n".join(member_hashes).encode("ascii")).hexdigest()
    return f"sg_{digest[:16]}"


def _replace_row(row: ManifestRow, **updates: str) -> ManifestRow:
    values = asdict(row)
    values.update(updates)
    return ManifestRow(**values)


def cluster_source_groups(
    rows: Sequence[ManifestRow],
    max_dhash_distance: int = 4,
    min_histogram_correlation: float = 0.98,
    max_aspect_delta: float = 0.01,
) -> GroupingResult:
    if not rows:
        return GroupingResult((), ())

    disjoint = _DisjointSet(len(rows))
    first_by_order: dict[str, int] = {}
    first_by_sha: dict[str, int] = {}
    for index, row in enumerate(rows):
        if row.order_id:
            if row.order_id in first_by_order:
                disjoint.union(index, first_by_order[row.order_id])
            else:
                first_by_order[row.order_id] = index
    for index, row in enumerate(rows):
        if row.sha256:
            if row.sha256 in first_by_sha:
                disjoint.union(index, first_by_sha[row.sha256])
            else:
                first_by_sha[row.sha256] = index

    components: dict[int, list[int]] = {}
    for index in range(len(rows)):
        components.setdefault(disjoint.find(index), []).append(index)
    clusters = sorted(
        (sorted(members, key=lambda item: (rows[item].sha256, rows[item].sample_id)) for members in components.values()),
        key=lambda members: tuple((rows[item].sha256, rows[item].sample_id) for item in members),
    )
    fingerprints: dict[int, VisualFingerprint] = {}
    evidence: list[dict[str, object]] = []
    inferred_members: set[int] = set()

    def get_fingerprint(index: int) -> VisualFingerprint:
        if index not in fingerprints:
            fingerprints[index] = fingerprint_image(Path(rows[index].path))
        return fingerprints[index]

    while True:
        merged = False
        for left_position in range(len(clusters)):
            for right_position in range(left_position + 1, len(clusters)):
                left_members = clusters[left_position]
                right_members = clusters[right_position]
                comparisons: list[VisualComparison] = []
                for left_index in left_members:
                    for right_index in right_members:
                        comparison = compare_fingerprints(
                            get_fingerprint(left_index),
                            get_fingerprint(right_index),
                            max_dhash_distance,
                            min_histogram_correlation,
                            max_aspect_delta,
                        )
                        comparisons.append(comparison)
                        evidence.append(
                            {
                                "left_sample_id": rows[left_index].sample_id,
                                "right_sample_id": rows[right_index].sample_id,
                                "dhash_distance": comparison.dhash_distance,
                                "histogram_correlation": comparison.histogram_correlation,
                                "aspect_delta": comparison.aspect_delta,
                                "accepted": comparison.accepted,
                            }
                        )
                if comparisons and all(item.accepted for item in comparisons):
                    combined = sorted(
                        left_members + right_members,
                        key=lambda item: (rows[item].sha256, rows[item].sample_id),
                    )
                    inferred_members.update(combined)
                    clusters[left_position] = combined
                    del clusters[right_position]
                    merged = True
                    break
            if merged:
                break
        if not merged:
            break

    updated: dict[int, ManifestRow] = {}
    for members in clusters:
        group_id = _stable_group_id(rows, members)
        is_inferred = any(index in inferred_members for index in members)
        member_evidence = [
            item
            for item in evidence
            if item["accepted"]
            and item["left_sample_id"] in {rows[index].sample_id for index in members}
            and item["right_sample_id"] in {rows[index].sample_id for index in members}
        ]
        if is_inferred:
            max_distance = max(int(item["dhash_distance"]) for item in member_evidence)
            min_correlation = min(float(item["histogram_correlation"]) for item in member_evidence)
            max_aspect = max(float(item["aspect_delta"]) for item in member_evidence)
            basis = "inferred_visual"
            detail = (
                f"dhash_distance={max_distance};histogram_correlation={min_correlation:.6f};"
                f"aspect_delta={max_aspect:.6f}"
            )
        else:
            order_ids = {rows[index].order_id for index in members if rows[index].order_id}
            hashes = {rows[index].sha256 for index in members}
            if len(members) > 1 and len(order_ids) == 1:
                basis = "order_id"
                detail = f"order_id={next(iter(order_ids))}"
            elif len(members) > 1 and len(hashes) == 1:
                basis = "exact_sha"
                detail = f"sha256={next(iter(hashes))}"
            else:
                original = rows[members[0]]
                basis = original.source_group_basis or "singleton"
                detail = original.source_group_evidence or f"sample_id={original.sample_id}"
        for index in members:
            updated[index] = _replace_row(
                rows[index],
                source_group=group_id,
                source_group_basis=basis,
                source_group_evidence=detail,
            )

    return GroupingResult(tuple(updated[index] for index in range(len(rows))), tuple(evidence))
