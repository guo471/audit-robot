from __future__ import annotations

from pathlib import Path

import pytest
import torch
from PIL import Image

from photo_authenticity.config import PreprocessConfig
from photo_authenticity.dataset import ManifestDataset
from photo_authenticity.manifest import ManifestRow
from photo_authenticity.preprocessing import (
    ImageDecodeError,
    build_eval_transform,
    build_train_transform,
    decode_rgb,
    letterbox_center,
)


TEST_CONFIG = PreprocessConfig(
    image_size=224,
    fill_rgb=(11, 22, 33),
    mean=(0.485, 0.456, 0.406),
    std=(0.229, 0.224, 0.225),
    brightness=0.1,
    contrast=0.1,
    scale_min=0.95,
    perspective=0.03,
    gaussian_blur_probability=0.5,
    jpeg_quality_min=85,
)


@pytest.fixture
def oriented_jpeg(tmp_path) -> Path:
    image = Image.new("RGB", (20, 10), (200, 20, 30))
    exif = Image.Exif()
    exif[274] = 6
    path = tmp_path / "oriented.jpg"
    image.save(path, exif=exif)
    return path


def test_eval_preprocess_applies_exif_rgb_and_center_padding(oriented_jpeg) -> None:
    decoded = decode_rgb(oriented_jpeg)
    tensor = build_eval_transform(TEST_CONFIG)(decoded)

    assert decoded.mode == "RGB"
    assert decoded.size == (10, 20)
    assert tensor.shape == (3, 224, 224)
    assert tensor.dtype == torch.float32
    assert torch.isfinite(tensor).all()


def test_eval_transform_is_byte_deterministic(oriented_jpeg) -> None:
    transform = build_eval_transform(TEST_CONFIG)

    first = transform(decode_rgb(oriented_jpeg))
    second = transform(decode_rgb(oriented_jpeg))

    assert torch.equal(first, second)


def test_seeded_train_transforms_reproduce(oriented_jpeg) -> None:
    first = build_train_transform(TEST_CONFIG, seed=20260713)(decode_rgb(oriented_jpeg))
    second = build_train_transform(TEST_CONFIG, seed=20260713)(decode_rgb(oriented_jpeg))

    assert torch.equal(first, second)
    assert first.shape == (3, 224, 224)


def test_letterbox_preserves_aspect_and_centers_fill() -> None:
    image = Image.new("RGB", (40, 20), (255, 0, 0))

    output = letterbox_center(image, 100, (1, 2, 3))

    assert output.size == (100, 100)
    assert output.getpixel((50, 0)) == (1, 2, 3)
    assert output.getpixel((50, 50)) == (255, 0, 0)


def test_dataset_uses_same_transform_for_both_classes(tmp_path) -> None:
    real_path = tmp_path / "real.png"
    non_real_path = tmp_path / "non-real.png"
    Image.new("RGB", (10, 10), (1, 2, 3)).save(real_path)
    Image.new("RGB", (10, 10), (4, 5, 6)).save(non_real_path)
    rows = [
        ManifestRow("R", str(real_path), "a" * 64, "real", "weak_label", "g1", "", "test"),
        ManifestRow("N", str(non_real_path), "b" * 64, "non_real", "confirmed", "g2", "", "test"),
    ]
    transform = build_eval_transform(TEST_CONFIG)
    dataset = ManifestDataset(rows, transform)

    assert dataset.transform is transform
    assert dataset[0][1:] == (0, "R")
    assert dataset[1][1:] == (1, "N")


def test_corrupt_image_raises_typed_decode_error(tmp_path) -> None:
    path = tmp_path / "corrupt.jpg"
    path.write_bytes(b"broken")

    with pytest.raises(ImageDecodeError):
        decode_rgb(path)
