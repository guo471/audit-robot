from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from tools.evaluate_local_photo_features import extract_local_features


class LocalPhotoFeatureEvalTest(unittest.TestCase):
    def test_features_do_not_depend_on_filename(self):
        pixels = np.zeros((96, 128, 3), dtype=np.uint8)
        pixels[:, :, 0] = np.arange(128, dtype=np.uint8)[None, :]
        pixels[:, :, 1] = np.arange(96, dtype=np.uint8)[:, None]
        pixels[20:80, 30:100, 2] = 180

        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "A_non_real_hint.png"
            second = Path(tmp) / "B_real_hint.png"
            Image.fromarray(pixels).save(first)
            Image.fromarray(pixels).save(second)

            first_features = extract_local_features(first).metrics
            second_features = extract_local_features(second).metrics

        self.assertEqual(first_features.keys(), second_features.keys())
        for key in first_features:
            self.assertAlmostEqual(first_features[key], second_features[key], places=6, msg=key)


if __name__ == "__main__":
    unittest.main()
