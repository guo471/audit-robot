from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import torch
from PIL import Image
from torch.utils.data import Dataset

from .manifest import ManifestRow
from .preprocessing import decode_rgb


class ManifestDataset(Dataset[tuple[torch.Tensor, int, str]]):
    def __init__(
        self,
        rows: Sequence[ManifestRow],
        transform: Callable[[Image.Image], torch.Tensor],
    ) -> None:
        self.rows = tuple(rows)
        self.transform = transform

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int, str]:
        row = self.rows[index]
        label = 1 if row.label == "non_real" else 0
        return self.transform(decode_rgb(Path(row.path))), label, row.sample_id
