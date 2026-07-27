from pathlib import Path

import pytest
from PIL import Image, ImageEnhance

from tools.photo_authenticity_mainline import UniformScreenTextureDetector


ROOT = Path(__file__).resolve().parents[1]
TARGET_ORDERS = {
    "481172392891905019084893": [
        ROOT / "data/guobu_api_all_20260717_172949/images/481172392891905019084893/img_001.jpg",
        ROOT / "data/guobu_api_all_20260717_172949/images/481172392891905019084893/img_002.jpg",
    ],
    "481172616922923935334490": [
        ROOT / "data/guobu_api_all_20260717_172949/images/481172616922923935334490/img_001.jpg",
        ROOT / "data/guobu_api_all_20260717_172949/images/481172616922923935334490/img_002.jpg",
    ],
}
REAL_IMAGE_DIR = ROOT / "data/guobu_api_pass_20260714_131438/images"
HAS_ACCEPTANCE_DATA = REAL_IMAGE_DIR.is_dir() and all(
    path.is_file() for paths in TARGET_ORDERS.values() for path in paths
)


pytestmark = pytest.mark.skipif(
    not HAS_ACCEPTANCE_DATA,
    reason="local photo-authenticity acceptance corpus is unavailable",
)


def test_target_images_hit_and_confirmed_real_image_false_positive_rate_is_below_five_percent():
    detector = UniformScreenTextureDetector()
    target_paths = [path for paths in TARGET_ORDERS.values() for path in paths]
    real_paths = sorted(REAL_IMAGE_DIR.glob("*/*"))

    target_scores = [detector.score(path)[0] for path in target_paths]
    real_scores = [detector.score(path)[0] for path in real_paths]
    false_positives = sum(score >= detector.threshold for score in real_scores)

    assert len(real_paths) == 629
    assert all(score >= detector.threshold for score in target_scores)
    assert false_positives / len(real_paths) < 0.05


@pytest.mark.parametrize(
    "variant",
    ("jpeg70", "jpeg85", "resize75", "resize90", "crop2", "brightness85", "brightness115"),
)
def test_each_target_order_survives_common_image_processing(variant, tmp_path):
    detector = UniformScreenTextureDetector()

    for order_id, source_paths in TARGET_ORDERS.items():
        scores = []
        for index, source_path in enumerate(source_paths):
            with Image.open(source_path) as source:
                image = source.convert("RGB")
                output = tmp_path / f"{order_id}_{index}_{variant}.jpg"
                if variant.startswith("jpeg"):
                    image.save(output, quality=int(variant.removeprefix("jpeg")))
                elif variant.startswith("resize"):
                    ratio = int(variant.removeprefix("resize")) / 100
                    down = image.resize(
                        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                        Image.Resampling.LANCZOS,
                    )
                    down.resize(image.size, Image.Resampling.LANCZOS).save(output, quality=95)
                elif variant == "crop2":
                    dx = max(1, round(image.width * 0.02))
                    dy = max(1, round(image.height * 0.02))
                    image.crop((dx, dy, image.width - dx, image.height - dy)).save(output, quality=95)
                else:
                    factor = int(variant.removeprefix("brightness")) / 100
                    ImageEnhance.Brightness(image).enhance(factor).save(output, quality=95)
            scores.append(detector.score(output)[0])

        assert any(score >= detector.threshold for score in scores), (order_id, variant, scores)
