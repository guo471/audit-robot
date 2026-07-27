from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from torch import nn
from torchvision.models import MobileNet_V3_Large_Weights, mobilenet_v3_large


@dataclass(frozen=True)
class TrainableSummary:
    stage: str
    trainable_names: tuple[str, ...]
    trainable_parameter_count: int


def build_model(weights: Literal["imagenet", "none"]) -> nn.Module:
    if weights == "imagenet":
        selected_weights = MobileNet_V3_Large_Weights.DEFAULT
    elif weights == "none":
        selected_weights = None
    else:
        raise ValueError("weights must be 'imagenet' or 'none'")
    model = mobilenet_v3_large(weights=selected_weights)
    final = model.classifier[-1]
    if not isinstance(final, nn.Linear):
        raise TypeError("unexpected MobileNetV3 classifier layout")
    model.classifier[-1] = nn.Linear(final.in_features, 2)
    return model


def set_training_stage(
    model: nn.Module, stage: Literal["head", "tail"], tail_blocks: int = 1
) -> TrainableSummary:
    if stage not in {"head", "tail"}:
        raise ValueError("stage must be 'head' or 'tail'")
    if tail_blocks not in {1, 2}:
        raise ValueError("tail_blocks must be 1 or 2")
    if not hasattr(model, "features") or not hasattr(model, "classifier"):
        raise TypeError("model must expose features and classifier modules")

    for parameter in model.parameters():
        parameter.requires_grad = False
    for parameter in model.classifier.parameters():
        parameter.requires_grad = True
    if stage == "tail":
        feature_blocks = list(model.features.children())
        for block in feature_blocks[-tail_blocks:]:
            for parameter in block.parameters():
                parameter.requires_grad = True

    names = tuple(name for name, parameter in model.named_parameters() if parameter.requires_grad)
    count = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    return TrainableSummary(stage, names, count)
