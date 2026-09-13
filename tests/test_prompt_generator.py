import json
import unittest
from unittest.mock import patch

from code.prompt_generator import DatasetPromptGenerator


class DatasetPromptGeneratorTests(unittest.TestCase):
    def test_prompt_and_json_share_selected_metadata(self):
        with patch(
            "code.prompt_generator.random.choice",
            side_effect=lambda options: options[0],
        ):
            prompt, record_json = DatasetPromptGenerator().generate_prompt()

        record = json.loads(record_json)

        self.assertEqual(
            record["config"],
            {
                "socratic_level": "low",
                "student_level": "beginner",
                "hint_frequency": "low",
                "mistake_frequency": "low",
                "verbosity": "low",
                "difficulty": "easy",
            },
        )
        self.assertEqual(record["domain"], "quantitative_aptitude")
        self.assertEqual(record["topic"], "Number series")
        self.assertEqual(record["problem"], "")
        self.assertEqual(record["conversation"], [])

        for name, value in record["config"].items():
            self.assertIn(f"{name}: {value}", prompt)
        self.assertIn(f"domain: {record['domain']}", prompt)
        self.assertIn(f"topic: {record['topic']}", prompt)


if __name__ == "__main__":
    unittest.main()
