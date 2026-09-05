import tempfile
import unittest
from pathlib import Path

from code.pipeline import run_pipeline


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


class PipelineQuotaAccountingTests(unittest.TestCase):
    def test_invalid_response_still_reduces_quota(self):
        manager = FakeManager()
        with tempfile.TemporaryDirectory() as directory:
            result = run_pipeline(iterations=1, manager=manager, data_dir=directory)

        self.assertEqual(result, 0)
        self.assertEqual(manager.reduced_usage, [(0, 123)])


if __name__ == "__main__":
    unittest.main()
