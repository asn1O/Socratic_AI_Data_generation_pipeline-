import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.pipeline import (
    _classify_failure_reason,
    _configure_model_logging,
    _count_model_conversations,
    _format_duration,
    _is_valid_conversation_payload,
    _new_stats,
    _parse_valid_json,
    _run_model_worker,
    _structural_failure_reason,
    load_models,
    normalize_iterations,
    run_pipeline,
    save_models,
)


class FakeModel:
    provider = "test"
    model_name = "model"
    consecutive_failures = 0
    expired = False
    wait_1_min = True

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
            "wait_1_min": True,
        }]
        with tempfile.NamedTemporaryFile("w", suffix=".json") as file:
            json.dump(model_data, file)
            file.flush()
            with patch.dict(os.environ, {"TEST_API_KEY": "resolved-secret"}):
                models = load_models(file.name)

        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].model_name, "m1")
        self.assertEqual(models[0].api_key, "resolved-secret")
        self.assertTrue(models[0].wait_1_min)

    def test_save_models_persists_worker_state(self):
        model_data = [{
            "provider": "P1",
            "api_key": "TEST_API_KEY",
            "model_name": "m1",
            "url": "https://example.com/v1",
            "wait_1_min": False,
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
        self.assertFalse(persisted[0]["wait_1_min"])

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
        model.wait_1_min = True
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}), \
                    patch("src.pipeline.time.sleep") as sleep:
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

    def test_worker_skips_sleep_when_wait_1_min_is_false(self):
        model = FakeModel()
        model.wait_1_min = False
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}), \
                    patch("src.pipeline.time.sleep") as sleep:
                model_logger = _configure_model_logging(model, directory)
                worker_stats = _run_model_worker(model, 3, model_logger)

        self.assertEqual(worker_stats["saved"], 0)
        self.assertEqual(worker_stats["attempts"], 3)
        self.assertEqual(worker_stats["failed"], 3)
        self.assertEqual(sleep.call_count, 0)

    def test_worker_expires_model_after_three_api_failures(self):
        class FailingModel(FakeModel):
            def call_model(self, prompt):
                return False, None, 0, "HTTP_500"

        model = FailingModel()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}), \
                    patch("src.pipeline.time.sleep"):
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

    def test_worker_updates_passed_in_stats_in_place(self):
        model = FakeModel()
        custom_stats = _new_stats()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}), \
                    patch("src.pipeline.time.sleep"):
                model_logger = _configure_model_logging(model, directory)
                result_stats = _run_model_worker(
                    model, 2, model_logger, stats=custom_stats
                )

        self.assertIs(result_stats, custom_stats)
        self.assertEqual(custom_stats["attempts"], 2)
        self.assertEqual(custom_stats["failed"], 2)

    def test_worker_respects_stop_event_before_starting(self):
        model = FakeModel()
        stop_event = threading.Event()
        stop_event.set()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}):
                model_logger = _configure_model_logging(model, directory)
                worker_stats = _run_model_worker(
                    model, 10, model_logger, stop_event=stop_event
                )

        self.assertEqual(worker_stats["attempts"], 0)

    def test_worker_wakes_and_exits_when_stop_event_signaled_during_sleep(self):
        stop_event = threading.Event()

        class SignalingModel(FakeModel):
            def call_model(self, prompt):
                stop_event.set()
                return False, None, 0, "HTTP_500"

        model = SignalingModel()
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATASET_GENERATOR_ID": "worker_01"}):
                model_logger = _configure_model_logging(model, directory)
                worker_stats = _run_model_worker(
                    model, 10, model_logger, stop_event=stop_event
                )

        self.assertEqual(worker_stats["attempts"], 1)

    def test_run_pipeline_handles_keyboard_interrupt_and_persists_stats(self):
        model_data = [{
            "provider": "P1",
            "api_key": "TEST_API_KEY",
            "model_name": "m1",
            "url": "https://example.com/v1",
            "wait_1_min": True,
        }]
        with tempfile.NamedTemporaryFile("w", suffix=".json") as file:
            json.dump(model_data, file)
            file.flush()

            with patch.dict(os.environ, {"TEST_API_KEY": "resolved-secret"}), \
                    patch("src.pipeline.save_models") as mock_save_models, \
                    patch("src.pipeline._count_model_conversations", return_value=0), \
                    patch("src.pipeline.ThreadPoolExecutor") as mock_executor_cls:

                mock_executor = MagicMock()
                mock_executor_cls.return_value = mock_executor

                def mock_submit(fn, model, iters, logger, ws, stop_event):
                    ws["attempts"] = 5
                    ws["saved"] = 3
                    ws["failed"] = 2
                    mock_future = MagicMock()
                    mock_future.result.side_effect = KeyboardInterrupt()
                    return mock_future

                mock_executor.submit.side_effect = mock_submit

                with tempfile.TemporaryDirectory() as temp_dir:
                    saved = run_pipeline(
                        iterations=10,
                        models_path=file.name,
                        data_dir=temp_dir,
                    )

                self.assertEqual(saved, 3)
                mock_save_models.assert_called_once()
                mock_executor.shutdown.assert_called_with(wait=False, cancel_futures=True)


class ConversationValidationTests(unittest.TestCase):
    def _create_valid_payload(self):
        turns = []
        for i in range(1, 13):
            if i % 2 == 1:
                turns.append({"turn": i, "speaker": "student", "text": f"Student turn {i}"})
            else:
                if i == 10:
                    state = "Synthesis"
                elif i == 12:
                    state = "Closing"
                else:
                    state = "Diagnosis"
                turns.append({
                    "turn": i,
                    "speaker": "tutor",
                    "reasoning": f"Reasoning for turn {i}",
                    "state": state,
                    "response": f"Tutor response {i}",
                })
        return {
            "problem": "Solve for x: 2x + 4 = 10",
            "conversation": turns,
        }

    def test_valid_payload_passes_validation(self):
        payload = self._create_valid_payload()
        self.assertTrue(_is_valid_conversation_payload(payload))
        parsed = _parse_valid_json(json.dumps(payload))
        self.assertEqual(parsed["problem"], payload["problem"])

    def test_broken_turn_numbering_rejected(self):
        payload = self._create_valid_payload()
        # Duplicate turn 2 (turn 3 skipped)
        payload["conversation"][2]["turn"] = 2
        self.assertFalse(_is_valid_conversation_payload(payload))
        reason = _structural_failure_reason(payload)
        self.assertIn("turn numbering broken", reason)
        self.assertEqual(_classify_failure_reason(reason), "turn_numbering_broken")

        with self.assertRaises(ValueError) as ctx:
            _parse_valid_json(json.dumps(payload))
        self.assertIn("turn numbering broken", str(ctx.exception))

    def test_second_last_tutor_not_synthesis_rejected(self):
        payload = self._create_valid_payload()
        # Change second-to-last tutor turn (conversation[-3]) from Synthesis to Support
        payload["conversation"][-3]["state"] = "Support"
        self.assertFalse(_is_valid_conversation_payload(payload))
        reason = _structural_failure_reason(payload)
        self.assertIn("second-to-last tutor turn is not Synthesis", reason)
        self.assertEqual(_classify_failure_reason(reason), "second_last_not_synthesis")

        with self.assertRaises(ValueError) as ctx:
            _parse_valid_json(json.dumps(payload))
        self.assertIn("second-to-last tutor turn is not Synthesis", str(ctx.exception))

    def test_final_tutor_not_closing_rejected(self):
        payload = self._create_valid_payload()
        # Change final tutor turn (conversation[-1]) from Closing to Synthesis
        payload["conversation"][-1]["state"] = "Synthesis"
        self.assertFalse(_is_valid_conversation_payload(payload))
        reason = _structural_failure_reason(payload)
        self.assertIn("final tutor turn is not Closing", reason)
        self.assertEqual(_classify_failure_reason(reason), "final_turn_not_closing")


if __name__ == "__main__":
    unittest.main()
