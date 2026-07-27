from __future__ import annotations

import json
import csv
from pathlib import Path

import pytest
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from photo_authenticity.config import (
    AppConfig,
    PreprocessConfig,
    RuntimeLimits,
    ThresholdPolicy,
    TrainingConfig,
)
from photo_authenticity.manifest import ManifestRow
from photo_authenticity.modeling import build_model, set_training_stage
from photo_authenticity.splitting import create_splits
from photo_authenticity.training import compute_class_weights, cross_validate, train_fold


class TinyBackbone(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(*([nn.Identity() for _ in range(16)] + [nn.Linear(4, 4)]))
        self.classifier = nn.Sequential(nn.Linear(4, 2))

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.classifier(torch.relu(self.features(inputs)))


def _config() -> AppConfig:
    return AppConfig(
        mode="offline_shadow",
        seed=20260713,
        preprocess=PreprocessConfig(
            image_size=224,
            fill_rgb=(0, 0, 0),
            mean=(0.485, 0.456, 0.406),
            std=(0.229, 0.224, 0.225),
        ),
        training=TrainingConfig(
            head_epochs=1,
            tail_epochs=1,
            head_learning_rate=0.02,
            tail_learning_rate=0.005,
            tail_blocks=1,
            batch_size=4,
            early_stopping_patience=1,
        ),
        thresholds=ThresholdPolicy(0.2, 0.7, 0.9),
        runtime=RuntimeLimits(5.0, 1),
    )


def _loaders(seed: int = 20260713) -> dict[str, DataLoader]:
    generator = torch.Generator().manual_seed(seed)
    inputs = torch.randn(16, 4, generator=generator)
    labels = torch.tensor([0, 1] * 8, dtype=torch.long)
    dataset = TensorDataset(inputs, labels)
    return {
        "train": DataLoader(dataset, batch_size=4, shuffle=True, generator=generator),
        "validation": DataLoader(dataset, batch_size=4, shuffle=False),
    }


class _ValidationDataset(torch.utils.data.Dataset):
    def __init__(self, rows, seed: int) -> None:
        generator = torch.Generator().manual_seed(seed)
        self.inputs = torch.randn(len(rows), 4, generator=generator)
        self.rows = tuple(rows)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        row = self.rows[index]
        return self.inputs[index], 1 if row.label == "non_real" else 0, row.sample_id


def _fold_loaders(fold, seed: int) -> dict[str, DataLoader]:
    generator = torch.Generator().manual_seed(seed)
    train_inputs = torch.randn(len(fold.train_rows), 4, generator=generator)
    train_labels = torch.tensor(
        [1 if row.label == "non_real" else 0 for row in fold.train_rows], dtype=torch.long
    )
    return {
        "train": DataLoader(
            TensorDataset(train_inputs, train_labels),
            batch_size=4,
            shuffle=True,
            generator=generator,
        ),
        "validation": DataLoader(_ValidationDataset(fold.validation_rows, seed + 100), batch_size=4),
    }


def _manifest_rows() -> list[ManifestRow]:
    rows = []
    for index in range(10):
        rows.append(
            ManifestRow(
                f"N{index}",
                f"unused-N{index}.png",
                f"{index:064x}",
                "non_real",
                "confirmed",
                f"ng-{index}",
                "",
                "synthetic",
            )
        )
        rows.append(
            ManifestRow(
                f"R{index}",
                f"unused-R{index}.png",
                f"{index + 100:064x}",
                "real",
                "weak_label",
                f"rg-{index}",
                "",
                "synthetic",
            )
        )
    return rows


def test_training_stages_freeze_then_unfreeze_tail() -> None:
    model = build_model(weights="none")

    head = set_training_stage(model, "head")
    assert head.trainable_names and all(name.startswith("classifier") for name in head.trainable_names)
    assert model.classifier[-1].out_features == 2

    tail = set_training_stage(model, "tail")
    assert any(name.startswith("features.16") for name in tail.trainable_names)
    assert not any(name.startswith("features.15") for name in tail.trainable_names)
    assert tail.trainable_parameter_count > head.trainable_parameter_count


def test_class_weights_are_inverse_frequency_and_normalized() -> None:
    weights = compute_class_weights([0, 0, 0, 1])

    assert weights.dtype == torch.float32
    assert weights.tolist() == pytest.approx([0.5, 1.5])
    with pytest.raises(ValueError):
        compute_class_weights([0, 0])


def test_two_stage_cpu_smoke_writes_best_checkpoint(tmp_path) -> None:
    result = train_fold(TinyBackbone(), _loaders(), _config(), tmp_path / "fold-1")

    assert result.best_checkpoint.is_file()
    assert [stage.stage for stage in result.stage_results] == ["head", "tail"]
    assert all(stage.epochs_ran == 1 for stage in result.stage_results)
    assert 0.0 <= result.best_non_real_recall <= 1.0


def test_five_fold_cv_writes_reproducibility_metadata_without_images(tmp_path) -> None:
    plan = create_splits(_manifest_rows())
    manifest_path = tmp_path / "synthetic-manifest.csv"

    result = cross_validate(
        plan,
        _config(),
        tmp_path / "run",
        manifest_path=manifest_path,
        model_factory=TinyBackbone,
        loader_factory=lambda fold, config, seed: _fold_loaders(fold, seed),
    )
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    with result.oof_predictions_path.open("r", encoding="utf-8", newline="") as handle:
        oof_rows = list(csv.DictReader(handle))

    assert len(result.fold_results) == 5
    assert metadata["manifest_sha256"] == plan.manifest_sha256
    assert len(metadata["split_sha256"]) == 64
    assert metadata["seed"] == 20260713
    assert metadata["config"]["mode"] == "offline_shadow"
    assert metadata["exploratory"] is True
    assert metadata["manifest_path"] == str(manifest_path.resolve())
    assert metadata["versions"]["python"].startswith("3.11")
    assert "git_head" in metadata and "worktree_dirty" in metadata
    assert len(metadata["folds"]) == 5
    assert len(oof_rows) == len(plan.development_rows)
    assert len({row["sample_id"] for row in oof_rows}) == len(oof_rows)
    assert metadata["oof_predictions_sha256"] == __import__("hashlib").sha256(
        result.oof_predictions_path.read_bytes()
    ).hexdigest()
