from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools.black_edge_shadow_detector import annotate_strong_candidates, scan_array, scan_image


def white_image(height=200, width=200):
    return np.full((height, width, 3), 245, dtype=np.uint8)


def test_full_bottom_band_is_a_strong_candidate():
    image = white_image()
    image[-24:, :] = 8

    result = scan_array(image)

    assert result.status == "strong_candidate"
    assert result.sides["bottom"].status == "strong_candidate"


def test_tapered_right_edge_is_detected_without_full_side_coverage():
    image = white_image()
    for row in range(30, 170):
        width = max(2, int((row - 30) * 0.20))
        image[row, -width:] = 5

    result = scan_array(image)

    assert result.status in {"strong_candidate", "uncertain_candidate"}
    assert result.sides["right"].status != "none"


def test_gradual_dark_shadow_is_not_strong():
    image = white_image()
    for depth in range(40):
        image[-depth - 1, :] = 180 - depth * 2

    result = scan_array(image)

    assert result.sides["bottom"].status != "strong_candidate"


def test_internal_product_frame_does_not_count_as_outer_edge():
    image = white_image()
    image[70:150, 70:150] = 5

    result = scan_array(image)

    assert result.status == "none"


def test_two_uncertain_sides_are_never_promoted_to_strong():
    image = white_image()
    for depth in range(20):
        value = min(140, 80 + depth * 4)
        image[-depth - 1, :] = value
        image[:, -depth - 1] = value

    result = scan_array(image)

    assert result.sides["bottom"].status == "uncertain_candidate"
    assert result.sides["right"].status == "uncertain_candidate"
    assert result.status == "uncertain_candidate"


def test_textured_dark_fabric_touching_edge_is_not_strong():
    image = white_image()
    rows, cols = np.indices((32, 200))
    textured = np.where((rows + cols) % 2 == 0, 8, 62).astype(np.uint8)
    image[-32:, :, :] = textured[..., None]

    result = scan_array(image)

    assert result.sides["bottom"].status != "strong_candidate"


def test_short_isolated_black_corner_is_not_a_candidate():
    image = white_image()
    image[-12:, -12:] = 4

    result = scan_array(image)

    assert result.status == "none"


def test_strong_candidate_exposes_source_normalized_region():
    image = white_image(height=240, width=320)
    image[-30:, 40:280] = 6

    result = scan_array(image)
    bottom = result.sides["bottom"]

    assert bottom.status == "strong_candidate"
    assert bottom.tangent_start_fraction == pytest.approx(40 / 320, abs=0.02)
    assert bottom.tangent_end_fraction == pytest.approx(280 / 320, abs=0.02)
    assert bottom.boundary_depth_fraction == pytest.approx(30 / 240, abs=0.02)


def test_annotation_keeps_full_scene_and_only_marks_candidate_boundary(tmp_path):
    source = tmp_path / "source.png"
    destination = tmp_path / "annotated.png"
    image = white_image(height=240, width=320)
    image[-30:, 40:280] = 6
    Image.fromarray(image).save(source)
    scan = scan_array(image)

    result_path = annotate_strong_candidates(source, destination, scan)

    assert result_path == destination
    with Image.open(source) as original, Image.open(destination) as annotated:
        assert annotated.size == original.size
        original_array = np.asarray(original.convert("RGB"))
        annotated_array = np.asarray(annotated.convert("RGB"))
    assert np.array_equal(annotated_array[80:160, 100:220], original_array[80:160, 100:220])
    assert not np.array_equal(annotated_array[205:215, 35:285], original_array[205:215, 35:285])


def test_scan_image_applies_exif_orientation_before_edge_detection(tmp_path):
    source = tmp_path / "rotated.jpg"
    image = Image.fromarray(white_image(height=120, width=200))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)

    result = scan_image(source)

    assert (result.width, result.height) == (120, 200)


def test_annotation_uses_the_same_exif_orientation_as_detection(tmp_path):
    source = tmp_path / "rotated.jpg"
    destination = tmp_path / "annotated.png"
    pixels = white_image(height=120, width=200)
    pixels[:, -24:] = 8
    image = Image.fromarray(pixels)
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    scan = scan_image(source)

    annotate_strong_candidates(source, destination, scan)

    with Image.open(destination) as annotated:
        assert annotated.size == (scan.width, scan.height)
