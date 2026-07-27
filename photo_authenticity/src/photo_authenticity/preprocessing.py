from __future__ import annotations

import io
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import torch
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from .config import PreprocessConfig


MAX_DECODE_PIXELS = 50_000_000


class ImageDecodeError(ValueError):
    pass


def decode_rgb(path: Path) -> Image.Image:
    try:
        with Image.open(path) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > MAX_DECODE_PIXELS:
                raise ImageDecodeError("image dimensions are invalid or oversized")
            opened.load()
            return ImageOps.exif_transpose(opened).convert("RGB")
    except ImageDecodeError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ImageDecodeError(f"unable to decode image: {path}") from exc


def letterbox_center(
    image: Image.Image, size: int, fill_rgb: tuple[int, int, int]
) -> Image.Image:
    if size <= 0 or image.width <= 0 or image.height <= 0:
        raise ValueError("image and output dimensions must be positive")
    scale = min(size / image.width, size / image.height)
    resized_width = max(1, min(size, round(image.width * scale)))
    resized_height = max(1, min(size, round(image.height * scale)))
    resized = image.resize((resized_width, resized_height), Image.Resampling.BICUBIC)
    output = Image.new("RGB", (size, size), fill_rgb)
    output.paste(resized, ((size - resized_width) // 2, (size - resized_height) // 2))
    return output


def _normalized_tensor(image: Image.Image, config: PreprocessConfig) -> torch.Tensor:
    array = np.asarray(image, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(np.ascontiguousarray(array.transpose(2, 0, 1)))
    mean = torch.tensor(config.mean, dtype=torch.float32).view(3, 1, 1)
    std = torch.tensor(config.std, dtype=torch.float32).view(3, 1, 1)
    return (tensor - mean) / std


@dataclass
class _EvalTransform:
    config: PreprocessConfig

    def __call__(self, image: Image.Image) -> torch.Tensor:
        rgb = image.convert("RGB")
        return _normalized_tensor(
            letterbox_center(rgb, self.config.image_size, self.config.fill_rgb), self.config
        )


class _TrainTransform:
    def __init__(self, config: PreprocessConfig, seed: int) -> None:
        self.config = config
        self._random = random.Random(seed)

    def _perspective(self, image: Image.Image) -> Image.Image:
        magnitude = self.config.perspective
        if magnitude <= 0:
            return image
        width, height = image.size
        dx = magnitude * width
        dy = magnitude * height
        source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
        target = np.float32(
            [
                [self._random.uniform(0, dx), self._random.uniform(0, dy)],
                [width - 1 - self._random.uniform(0, dx), self._random.uniform(0, dy)],
                [width - 1 - self._random.uniform(0, dx), height - 1 - self._random.uniform(0, dy)],
                [self._random.uniform(0, dx), height - 1 - self._random.uniform(0, dy)],
            ]
        )
        matrix = cv2.getPerspectiveTransform(source, target)
        warped = cv2.warpPerspective(
            np.asarray(image),
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=self.config.fill_rgb,
        )
        return Image.fromarray(warped, mode="RGB")

    def __call__(self, image: Image.Image) -> torch.Tensor:
        augmented = image.convert("RGB")
        if self.config.brightness:
            factor = self._random.uniform(1 - self.config.brightness, 1 + self.config.brightness)
            augmented = ImageEnhance.Brightness(augmented).enhance(factor)
        if self.config.contrast:
            factor = self._random.uniform(1 - self.config.contrast, 1 + self.config.contrast)
            augmented = ImageEnhance.Contrast(augmented).enhance(factor)
        if self.config.scale_min < 1.0:
            scale = self._random.uniform(self.config.scale_min, 1.0)
            augmented = augmented.resize(
                (max(1, round(augmented.width * scale)), max(1, round(augmented.height * scale))),
                Image.Resampling.BICUBIC,
            )
        augmented = self._perspective(augmented)
        if self._random.random() < self.config.gaussian_blur_probability:
            augmented = augmented.filter(ImageFilter.GaussianBlur(self._random.uniform(0.1, 1.0)))
        if self.config.jpeg_quality_min < 100:
            buffer = io.BytesIO()
            quality = self._random.randint(self.config.jpeg_quality_min, 100)
            augmented.save(buffer, format="JPEG", quality=quality)
            buffer.seek(0)
            with Image.open(buffer) as jpeg:
                augmented = jpeg.convert("RGB").copy()
        return _EvalTransform(self.config)(augmented)


def build_eval_transform(config: PreprocessConfig) -> Callable[[Image.Image], torch.Tensor]:
    return _EvalTransform(config)


def build_train_transform(
    config: PreprocessConfig, seed: int
) -> Callable[[Image.Image], torch.Tensor]:
    return _TrainTransform(config, seed)
