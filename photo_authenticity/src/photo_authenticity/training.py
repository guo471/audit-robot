from __future__ import annotations

import csv
import hashlib
import json
import platform
import random
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np
import torch
import torchvision
from torch import nn
from torch.utils.data import DataLoader

from .config import AppConfig
from .dataset import ManifestDataset
from .hashing import sha256_file
from .metrics import binary_metrics
from .modeling import build_model, set_training_stage
from .preprocessing import build_eval_transform, build_train_transform
from .splitting import ExploratoryFold, SplitPlan


@dataclass(frozen=True)
class StageResult:
    stage: str
    epochs_ran: int
    best_non_real_recall: float
    best_validation_loss: float


@dataclass(frozen=True)
class FoldResult:
    best_checkpoint: Path
    best_non_real_recall: float
    best_validation_loss: float
    stage_results: tuple[StageResult, ...]


@dataclass(frozen=True)
class CrossValidationResult:
    fold_results: tuple[FoldResult, ...]
    metadata_path: Path
    oof_predictions_path: Path
    split_sha256: str
    exploratory: bool


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def compute_class_weights(labels: Sequence[int]) -> torch.Tensor:
    counts = torch.bincount(torch.as_tensor(labels, dtype=torch.long), minlength=2).float()
    if torch.any(counts == 0):
        raise ValueError("both classes are required to compute class weights")
    inverse = counts.reciprocal()
    return inverse / inverse.mean()


def _labels_from_loader(loader: DataLoader) -> list[int]:
    dataset = loader.dataset
    if isinstance(dataset, ManifestDataset):
        return [1 if row.label == "non_real" else 0 for row in dataset.rows]
    tensors = getattr(dataset, "tensors", None)
    if tensors is not None and len(tensors) >= 2:
        return [int(value) for value in tensors[1].tolist()]
    labels: list[int] = []
    for batch in loader:
        labels.extend(int(value) for value in batch[1].tolist())
    return labels


def _epoch(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    optimizer: torch.optim.Optimizer | None,
    device: torch.device,
) -> tuple[float, list[int], list[int]]:
    training = optimizer is not None
    model.train(training)
    losses: list[float] = []
    labels: list[int] = []
    predictions: list[int] = []
    for batch in loader:
        inputs = batch[0].to(device)
        targets = batch[1].to(device)
        if optimizer is not None:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training):
            logits = model(inputs)
            loss = criterion(logits, targets)
            if optimizer is not None:
                loss.backward()
                optimizer.step()
        losses.append(float(loss.detach().cpu()))
        labels.extend(int(value) for value in targets.detach().cpu().tolist())
        predictions.extend(int(value) for value in logits.argmax(dim=1).detach().cpu().tolist())
    if not losses:
        raise ValueError("loader must contain at least one batch")
    return float(np.mean(losses)), labels, predictions


def train_fold(
    model: nn.Module,
    loaders: Mapping[str, DataLoader],
    config: AppConfig,
    run_dir: Path,
) -> FoldResult:
    if set(loaders) < {"train", "validation"}:
        raise ValueError("train and validation loaders are required")
    seed_everything(config.seed)
    device = torch.device("cpu")
    model.to(device)
    class_weights = compute_class_weights(_labels_from_loader(loaders["train"])).to(device)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    output_dir = run_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = output_dir / "best.pt"
    best_recall = -1.0
    best_loss = float("inf")
    stage_results: list[StageResult] = []

    stages = (
        ("head", config.training.head_epochs, config.training.head_learning_rate),
        ("tail", config.training.tail_epochs, config.training.tail_learning_rate),
    )
    for stage, epochs, learning_rate in stages:
        set_training_stage(model, stage, config.training.tail_blocks)
        optimizer = torch.optim.AdamW(
            (parameter for parameter in model.parameters() if parameter.requires_grad),
            lr=learning_rate,
        )
        stage_best_recall = -1.0
        stage_best_loss = float("inf")
        stale_epochs = 0
        epochs_ran = 0
        for _ in range(epochs):
            _epoch(model, loaders["train"], criterion, optimizer, device)
            validation_loss, labels, predictions = _epoch(
                model, loaders["validation"], criterion, None, device
            )
            recall = binary_metrics(labels, predictions).non_real_recall
            epochs_ran += 1
            stage_improved = recall > stage_best_recall or (
                recall == stage_best_recall and validation_loss < stage_best_loss
            )
            if stage_improved:
                stage_best_recall = recall
                stage_best_loss = validation_loss
                stale_epochs = 0
            else:
                stale_epochs += 1
            overall_improved = recall > best_recall or (
                recall == best_recall and validation_loss < best_loss
            )
            if overall_improved:
                best_recall = recall
                best_loss = validation_loss
                torch.save(
                    {
                        "model_state_dict": model.state_dict(),
                        "model_architecture": "mobilenet_v3_large",
                        "output_order": ["real", "non_real"],
                        "stage": stage,
                    },
                    checkpoint,
                )
            if stale_epochs >= config.training.early_stopping_patience:
                break
        stage_results.append(
            StageResult(stage, epochs_ran, stage_best_recall, stage_best_loss)
        )
    return FoldResult(checkpoint, best_recall, best_loss, tuple(stage_results))


def _default_loader_factory(
    fold: ExploratoryFold, config: AppConfig, seed: int
) -> dict[str, DataLoader]:
    generator = torch.Generator().manual_seed(seed)
    train_dataset = ManifestDataset(
        fold.train_rows, build_train_transform(config.preprocess, seed)
    )
    validation_dataset = ManifestDataset(
        fold.validation_rows, build_eval_transform(config.preprocess)
    )
    return {
        "train": DataLoader(
            train_dataset,
            batch_size=config.training.batch_size,
            shuffle=True,
            num_workers=0,
            generator=generator,
        ),
        "validation": DataLoader(
            validation_dataset,
            batch_size=config.training.batch_size,
            shuffle=False,
            num_workers=0,
        ),
    }


def _split_hash(split_plan: SplitPlan) -> str:
    payload = {
        "seed": split_plan.seed,
        "manifest_sha256": split_plan.manifest_sha256,
        "formal_locked": sorted(row.sample_id for row in split_plan.formal_locked),
        "folds": [
            {
                "index": fold.index,
                "train": sorted(fold.train_ids),
                "validation": sorted(fold.validation_ids),
            }
            for fold in split_plan.exploratory_folds
        ],
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _git_state() -> tuple[str | None, bool]:
    repository = Path(__file__).resolve().parents[3]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        scoped = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                "photo_authenticity",
                "tests/photo_authenticity",
                "docs/superpowers/plans/2026-07-13-photo-authenticity-small-sample-implementation.md",
            ],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return head, bool(scoped.strip())
    except (OSError, subprocess.SubprocessError):
        return None, True


def _validation_predictions(
    model: nn.Module,
    loader: DataLoader,
    fold: ExploratoryFold,
) -> list[dict[str, object]]:
    checkpoint_rows = {row.sample_id: row for row in fold.validation_rows}
    fallback_ids = iter(row.sample_id for row in fold.validation_rows)
    output: list[dict[str, object]] = []
    model.eval()
    with torch.no_grad():
        for batch in loader:
            logits = model(batch[0].to("cpu"))
            scores = torch.softmax(logits, dim=1)[:, 1].detach().cpu().tolist()
            labels = [int(value) for value in batch[1].tolist()]
            sample_ids = list(batch[2]) if len(batch) > 2 else [next(fallback_ids) for _ in scores]
            for sample_id, label, score in zip(sample_ids, labels, scores):
                row = checkpoint_rows[str(sample_id)]
                expected_label = 1 if row.label == "non_real" else 0
                if label != expected_label:
                    raise ValueError(f"validation label mismatch for {sample_id}")
                output.append(
                    {
                        "sample_id": row.sample_id,
                        "label": row.label,
                        "label_status": row.label_status,
                        "score": float(score),
                        "decision": "",
                        "source_group": row.source_group,
                        "scope": "exploratory_cv",
                        "split": "validation",
                        "manifest_sha256": "",
                        "model_sha256": "",
                        "threshold_sha256": "",
                        "fold": fold.index,
                    }
                )
    if len(output) != len(fold.validation_rows):
        raise ValueError(f"fold {fold.index} OOF prediction count mismatch")
    return output


def cross_validate(
    split_plan: SplitPlan,
    config: AppConfig,
    run_dir: Path,
    *,
    manifest_path: Path | None = None,
    model_factory: Callable[[], nn.Module] | None = None,
    loader_factory: Callable[[ExploratoryFold, AppConfig, int], Mapping[str, DataLoader]] | None = None,
) -> CrossValidationResult:
    seed_everything(config.seed)
    output_dir = run_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    create_model = model_factory or (lambda: build_model(weights="imagenet"))
    create_loaders = loader_factory or _default_loader_factory
    fold_results: list[FoldResult] = []
    oof_rows: list[dict[str, object]] = []
    for fold in split_plan.exploratory_folds:
        fold_seed = config.seed + fold.index
        seed_everything(fold_seed)
        model = create_model()
        loaders = create_loaders(fold, config, fold_seed)
        result = train_fold(
            model,
            loaders,
            config,
            output_dir / f"fold-{fold.index}",
        )
        fold_results.append(result)
        checkpoint = torch.load(result.best_checkpoint, map_location="cpu", weights_only=True)
        model.load_state_dict(checkpoint["model_state_dict"])
        oof_rows.extend(_validation_predictions(model, loaders["validation"], fold))

    split_sha256 = _split_hash(split_plan)
    checkpoint_hashes = [sha256_file(result.best_checkpoint) for result in fold_results]
    cv_model_sha256 = hashlib.sha256("\n".join(checkpoint_hashes).encode("ascii")).hexdigest()
    for row in oof_rows:
        row["manifest_sha256"] = split_plan.manifest_sha256
        row["model_sha256"] = cv_model_sha256
    oof_path = output_dir / "oof-predictions.csv"
    oof_columns = (
        "sample_id",
        "label",
        "label_status",
        "score",
        "decision",
        "source_group",
        "scope",
        "split",
        "manifest_sha256",
        "model_sha256",
        "threshold_sha256",
        "fold",
    )
    with oof_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=oof_columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(oof_rows, key=lambda row: str(row["sample_id"])))
    oof_sha256 = sha256_file(oof_path)
    git_head, worktree_dirty = _git_state()
    metadata = {
        "manifest_path": str(manifest_path.resolve()) if manifest_path is not None else None,
        "manifest_sha256": split_plan.manifest_sha256,
        "split_sha256": split_sha256,
        "seed": config.seed,
        "config": asdict(config),
        "versions": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "torchvision": torchvision.__version__,
            "numpy": np.__version__,
        },
        "git_head": git_head,
        "worktree_dirty": worktree_dirty,
        "worktree_dirty_scope": "photo_authenticity implementation paths",
        "exploratory": split_plan.exploratory,
        "cv_model_sha256": cv_model_sha256,
        "oof_predictions_path": str(oof_path),
        "oof_predictions_sha256": oof_sha256,
        "folds": [
            {
                "index": index,
                "best_checkpoint": str(result.best_checkpoint),
                "best_checkpoint_sha256": checkpoint_hashes[index - 1],
                "best_non_real_recall": result.best_non_real_recall,
                "best_validation_loss": result.best_validation_loss,
                "stages": [asdict(stage) for stage in result.stage_results],
            }
            for index, result in enumerate(fold_results, start=1)
        ],
    }
    metadata_path = output_dir / "run-metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return CrossValidationResult(
        tuple(fold_results), metadata_path, oof_path, split_sha256, split_plan.exploratory
    )
