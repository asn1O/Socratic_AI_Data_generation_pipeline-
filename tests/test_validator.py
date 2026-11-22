import json
from pathlib import Path
import tempfile
import unittest

from src.validator import (
    _classify_failure_reason,
    _extract_first_json_object,
    _is_valid_conversation_payload,
    _parse_valid_json,
    _strip_wrappers,
    _validate_conversation_payload,
    check_config_leakage,
    validate_dataset,
    validate_file,
)


class ValidatorTests(unittest.TestCase):
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
            "problem": "Solve for x: 3x - 6 = 12",
            "conversation": turns,
        }

    def test_strip_wrappers_removes_scratchpad_and_markdown(self):
        raw = "```json\n<think>Internal thought</think>{\"key\": \"val\"}\n```"
        cleaned = _strip_wrappers(raw)
        self.assertEqual(cleaned, '{"key": "val"}')

    def test_extract_first_json_object_handles_nested_objects(self):
        raw = 'Random prefix {"config": {"socratic_level": "high"}, "data": 42} suffix text'
        payload = _extract_first_json_object(raw)
        self.assertEqual(payload["data"], 42)
        self.assertEqual(payload["config"]["socratic_level"], "high")

    def test_check_config_leakage_detects_parameter_names(self):
        leaks = check_config_leakage("I must follow socratic_level and hint_frequency here.")
        self.assertTrue(len(leaks) > 0)
        self.assertIn("direct_parameter_or_persona_leak", leaks[0])

    def test_check_config_leakage_detects_difficulty_leak(self):
        leaks = check_config_leakage("Let's maintain the easy difficulty level for the student.")
        self.assertTrue(len(leaks) > 0)
        self.assertIn("difficulty_config_leak", leaks[0])

    def test_check_config_leakage_detects_persona_leak(self):
        leaks = check_config_leakage("The user is an advanced student.")
        self.assertTrue(len(leaks) > 0)
        self.assertIn("direct_parameter_or_persona_leak", leaks[0])

    def test_valid_payload_passes(self):
        payload = self._create_valid_payload()
        is_valid, reason = _validate_conversation_payload(payload)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_payload_with_config_leakage_in_reasoning_fails(self):
        payload = self._create_valid_payload()
        payload["conversation"][1]["reasoning"] = "Applying socratic_level guidelines to help."
        is_valid, reason = _validate_conversation_payload(payload)
        self.assertFalse(is_valid)
        self.assertIn("config leakage detected", reason)
        self.assertEqual(_classify_failure_reason(reason), "config_leakage")

    def test_closing_state_before_final_turn_fails(self):
        payload = self._create_valid_payload()
        payload["conversation"][1]["state"] = "Closing"
        is_valid, reason = _validate_conversation_payload(payload)
        self.assertFalse(is_valid)
        self.assertIn("Closing state appears before final turn", reason)

    def test_validate_file_and_dataset(self):
        valid_payload = self._create_valid_payload()
        invalid_payload = self._create_valid_payload()
        invalid_payload["conversation"][1]["reasoning"] = "Leaking socratic_level"

        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir) / "provider_model"
            model_dir.mkdir()
            json_path = model_dir / "worker_01-set[0].json"
            json_path.write_text(
                json.dumps([valid_payload, invalid_payload], indent=2),
                encoding="utf-8",
            )

            # Dry run: check without deleting
            total, invalid, folders, errors, files = validate_dataset(
                Path(temp_dir), delete_invalid=False
            )
            self.assertEqual(total, 2)
            self.assertEqual(invalid, 1)
            self.assertEqual(errors["config_leakage"], 1)

            # Delete invalid run
            total, invalid, folders, errors, files = validate_dataset(
                Path(temp_dir), delete_invalid=True
            )
            self.assertEqual(total, 2)
            self.assertEqual(invalid, 1)

            # Confirm file now has only 1 valid element
            remaining = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(len(remaining), 1)


if __name__ == "__main__":
    unittest.main()
