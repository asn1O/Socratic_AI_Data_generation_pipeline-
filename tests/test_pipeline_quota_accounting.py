import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code.pipeline import (
    _count_model_conversations,
    _configure_model_logging,
    _format_duration,
    _run_model_worker,
    load_models,
    normalize_iterations,
    save_models,
)


class FakeModel:
    provider = "test"
    model_name = "model"
    consecutive_failures = 0
    expired = False

    def call_model(self, prompt):
        return True, "not valid conversation JSON", 123, None

    def write_rejected_response(self, response):
        return Path("rejected.txt")


class PipelineQuotaAccountingTests(unittest.TestCase):
    def test_model_log_filename_contains_generator_and_model(self):
        model = FakeModel()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}):
                model_logger = _configure_model_logging(model, directory)

            log_path = Path(directory) / "logs" / "worker_01.test_model.log"
            self.assertTrue(log_path.exists())
            self.assertEqual(len(model_logger.handlers), 1)
            for handler in model_logger.handlers:
                model_logger.removeHandler(handler)
                handler.close()

    def test_normalize_iterations_rounds_total_up_to_model_count(self):
        self.assertEqual(normalize_iterations(50, 3), (51, 17))
        self.assertEqual(normalize_iterations(6, 3), (6, 2))

    def test_load_models_returns_models_from_json_array(self):
        model_data = [{
            "provider": "P1",
            "api_key": "TEST_API_KEY",
            "model_name": "m1",
            "url": "https://example.com/v1",
            "rpd": 1,
            "rpd_left": 1,
            "tpd": None,
            "tpd_left": None,
            "last_use": None,
        }]
        with tempfile.NamedTemporaryFile("w", suffix=".json") as file:
            json.dump(model_data, file)
            file.flush()
            with patch.dict(os.environ, {"TEST_API_KEY": "resolved-secret"}):
                models = load_models(file.name)

        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].model_name, "m1")
        self.assertEqual(models[0].api_key, "resolved-secret")

    def test_save_models_persists_worker_state(self):
        model_data = [{
            "provider": "P1",
            "api_key": "TEST_API_KEY",
            "model_name": "m1",
            "url": "https://example.com/v1",
            "rpd": 1,
            "rpd_left": 1,
            "tpd": None,
            "tpd_left": None,
            "last_use": None,
        }]
        with tempfile.NamedTemporaryFile("w", suffix=".json") as file:
            json.dump(model_data, file)
            file.flush()
            with patch.dict(os.environ, {"TEST_API_KEY": "resolved-secret"}):
                models = load_models(file.name)
            models[0].expired = True
            save_models(file.name, models)
            persisted = json.loads(Path(file.name).read_text(encoding="utf-8"))

        self.assertTrue(persisted[0]["expired"])

    def test_format_duration_as_hours_minutes_and_seconds(self):
        self.assertEqual(_format_duration(3661.9), "01:01:01")

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

    def test_worker_runs_requested_attempts_without_model_selection(self):
        model = FakeModel()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}), \
                    patch("code.pipeline.time.sleep") as sleep:
                model_logger = _configure_model_logging(model, directory)
                worker_stats = _run_model_worker(model, 3, model_logger)
            log_text = (
                Path(directory) / "logs" / "worker_01.test_model.log"
            ).read_text(encoding="utf-8")

        self.assertEqual(worker_stats["saved"], 0)
        self.assertEqual(worker_stats["attempts"], 3)
        self.assertEqual(worker_stats["failed"], 3)
        self.assertEqual(sleep.call_count, 3)
        sleep.assert_called_with(60)
        self.assertIn("MODEL_SUMMARY", log_text)
        self.assertIn("attempts: 3", log_text)
        self.assertIn("failed: 3", log_text)

    def test_worker_expires_model_after_three_api_failures(self):
        class FailingModel(FakeModel):
            def call_model(self, prompt):
                return False, None, 0, "HTTP_500"

        model = FailingModel()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}), \
                    patch("code.pipeline.time.sleep"):
                model_logger = _configure_model_logging(model, directory)
                worker_stats = _run_model_worker(model, 10, model_logger)
            log_text = (
                Path(directory) / "logs" / "worker_01.test_model.log"
            ).read_text(encoding="utf-8")

        self.assertEqual(worker_stats["attempts"], 3)
        self.assertEqual(worker_stats["failed"], 3)
        self.assertTrue(model.expired)
        self.assertEqual(model.consecutive_failures, 3)
        self.assertEqual(log_text.count("error_code=HTTP_500"), 3)


if __name__ == "__main__":
    unittest.main()
