from pathlib import Path
import unittest

from tools.non_real_tree_classifier import run_evaluation


class NonRealTreeClassifierTest(unittest.TestCase):
    def test_tree_classifier_meets_current_sample_counts(self):
        summary = run_evaluation(Path("reports/non_real_prompt_eval/local_features_v2.jsonl"))

        self.assertEqual(summary["non_real"]["count"], 404)
        self.assertEqual(summary["non_real"]["hit_count"], 404)
        self.assertEqual(summary["real"]["count"], 610)
        self.assertEqual(summary["real"]["false_positive_count"], 4)


if __name__ == "__main__":
    unittest.main()
