from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from tools import eval_non_real_prompt_qwen as qwen_eval


class EvalNonRealPromptQwenTest(unittest.TestCase):
    def _write_image(self, path: Path) -> None:
        pixels = np.zeros((24, 32, 3), dtype=np.uint8)
        pixels[:, :, 0] = 120
        pixels[4:20, 8:24, 1] = 200
        Image.fromarray(pixels).save(path)

    def test_request_body_uses_anonymous_sample_id_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "A_non_real_filename_hint.png"
            self._write_image(path)

            body = qwen_eval._build_request_body("prompt", "sample_000123", path)
            text_payload = body["messages"][1]["content"][0]["text"]
            serialized = str(body)

        self.assertEqual(text_payload, '{"sample_id": "sample_000123"}')
        self.assertNotIn("A_non_real_filename_hint", serialized)
        self.assertNotIn("image_id", serialized)

    def test_cache_key_ignores_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "A_hint.png"
            second = Path(tmp) / "B_hint.png"
            self._write_image(first)
            self._write_image(second)

            first_cache = qwen_eval._json_cache_path(Path(tmp), "prompt", first)
            second_cache = qwen_eval._json_cache_path(Path(tmp), "prompt", second)

        self.assertEqual(first_cache.name, second_cache.name)

    def test_final_result_is_derived_from_observations_not_model_result(self):
        parsed = {
            "result": "high_risk_non_real",
            "edges": {
                "top": "scene_continues",
                "right": "scene_continues",
                "bottom": "scene_continues",
                "left": "scene_continues",
            },
            "screen_owner": "none",
            "strong_evidence": [],
            "weak_evidence": [],
            "reason": "no visible secondary carrier",
        }

        result, rule, payload = qwen_eval._derive_from_model_observations(parsed, "sample_000001")

        self.assertEqual(result, "no_evidence")
        self.assertEqual(rule, "R9")
        self.assertEqual(payload["image_id"], "sample_000001")

    def test_schema_failure_row_is_safe_and_scored(self):
        result, rule, payload = qwen_eval._failure_row("sample_000999", ValueError("boom"))

        self.assertEqual(result, "manual_review")
        self.assertEqual(rule, "R7")
        self.assertEqual(payload["screen_owner"], "uncertain")
        self.assertEqual(payload["reason"], "schema_error: ValueError")


if __name__ == "__main__":
    unittest.main()
