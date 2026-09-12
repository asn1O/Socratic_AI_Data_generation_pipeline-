import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code.pipeline import (
    MAX_MODEL_SELECTION_ATTEMPTS,
    _count_model_conversations,
    _get_model_with_retry,
    run_pipeline,
)


class FakeModel:
    provider = "test"
    model_name = "model"

    def call_model(self, prompt):
        return True, "not valid conversation JSON", 123

    def write_rejected_response(self, response):
        return Path("rejected.txt")


class FakeManager:
    def __init__(self):
        self.model = FakeModel()
        self.keys = [self.model]
        self.reduced_usage = []

    def get_key(self):
        return self.model, 0

    def reduce_key_usage(self, index, tokens):
        self.reduced_usage.append((index, tokens))

    def reset_all_failure_counters(self):
        pass


class PipelineQuotaAccountingTests(unittest.TestCase):
    def test_count_model_conversations_across_set_files(self):
        with tempfile.TemporaryDirectory() as directory:
            model_directory = Path(directory) / "model"
            model_directory.mkdir()
            (model_directory / "worker-set[0].json").write_text(
                json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8"
            )
            (model_directory / "worker-set[1].json").write_text(
                json.dumps([{"id": 3}]), encoding="utf-8"
            )

            class ModelWithOutputPath:
                dest_write_path = str(model_directory)

            self.assertEqual(_count_model_conversations(ModelWithOutputPath()), 3)

    def test_model_selection_stops_after_twelve_attempts(self):
        class UnavailableManager:
            def __init__(self):
                self.selection_attempts = 0

            def get_key(self):
                self.selection_attempts += 1
                return None

        manager = UnavailableManager()
        with patch("code.pipeline.time.sleep") as sleep:
            selected = _get_model_with_retry(manager)

        self.assertIsNone(selected)
        self.assertEqual(manager.selection_attempts, MAX_MODEL_SELECTION_ATTEMPTS)
        self.assertEqual(sleep.call_count, MAX_MODEL_SELECTION_ATTEMPTS - 1)

    def test_invalid_response_still_reduces_quota(self):
        manager = FakeManager()
        with tempfile.TemporaryDirectory() as directory:
            result = run_pipeline(iterations=1, manager=manager, data_dir=directory)

        self.assertEqual(result, 0)
        self.assertEqual(manager.reduced_usage, [(0, 123)])


if __name__ == "__main__":
    unittest.main()
