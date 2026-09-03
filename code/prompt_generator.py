from pathlib import Path
import json
import random
from typing import Dict, List, Optional, Tuple


class DatasetPromptGenerator:
    """Build dataset-generation prompts from the Markdown template."""

    PARAMETERS: Dict[str, List[Dict[str, str]]] = {
        "socratic_level": [
            {
                "value": "low",
                "definition": "Tutor explains more and gives direct guidance; fewer probing questions.",
            },
            {
                "value": "medium",
                "definition": "Balanced mix of guiding questions and occasional direct hints.",
            },
            {
                "value": "high",
                "definition": "Tutor relies almost entirely on questions; avoids direct guidance even when the student struggles.",
            },
        ],
        "student_level": [
            {
                "value": "beginner",
                "definition": "Student is unfamiliar with the topic; frequent confusion and basic conceptual mistakes.",
            },
            {
                "value": "intermediate",
                "definition": "Student has partial understanding; occasional errors, mostly in execution not concept.",
            },
            {
                "value": "advanced",
                "definition": "Student reasons correctly most of the time; only minor slips or edge-case confusion.",
            },
        ],
        "hint_frequency": [
            {
                "value": "low",
                "definition": "Tutor rarely gives hints; relies almost entirely on questions to move the student forward.",
            },
            {
                "value": "medium",
                "definition": "Tutor balances hints and questions roughly evenly.",
            },
            {
                "value": "high",
                "definition": "Tutor gives frequent directional hints, though never the full solution.",
            },
        ],
        "mistake_frequency": [
            {
                "value": "low",
                "definition": "Student is mostly correct throughout, with rare small errors.",
            },
            {
                "value": "medium",
                "definition": "Student makes occasional incorrect steps that need correcting.",
            },
            {
                "value": "high",
                "definition": "Student frequently makes incorrect assumptions or reasoning errors before finding the right path.",
            },
        ],
        "verbosity": [
            {
                "value": "low",
                "definition": "Both tutor and student give short, concise responses.",
            },
            {
                "value": "medium",
                "definition": "Moderate length responses with some explanation.",
            },
            {
                "value": "high",
                "definition": "Longer, more detailed reasoning in responses.",
            },
        ],
        "difficulty": [
            {
                "value": "easy",
                "definition": "Problem is straightforward, solvable in a few simple steps.",
            },
            {
                "value": "medium",
                "definition": "Problem requires moderate multi-step reasoning.",
            },
            {
                "value": "hard",
                "definition": "Problem requires multi-step, non-obvious, or tricky logical reasoning.",
            },
        ],
    }

    DOMAINS: Dict[str, List[str]] = {
        "quantitative_aptitude": [
            "Number series",
            "Ratio and proportion",
            "Percentages",
            "Time and work",
            "Speed, time and distance",
            "Probability",
            "Permutations and combinations",
            "Mixtures and alligation",
        ],
        "logic_and_puzzles": [
            "Blood relations",
            "Direction sense",
            "Coding-decoding",
            "Seating arrangement",
            "Syllogisms",
            "Logical sequences",
            "Venn diagram reasoning",
            "Clock and calendar puzzles",
        ],
        "physics": [
            "Newton's laws in everyday scenarios",
            "Series vs parallel circuits",
            "Buoyancy and density",
            "Levers and pulleys",
            "Heat transfer",
            "Projectile motion basics",
        ],
        "chemistry": [
            "Balancing chemical equations",
            "Acids and bases",
            "States of matter and phase changes",
            "Periodic table trends",
            "Basic stoichiometry",
        ],
        "biology": [
            "Photosynthesis",
            "Punnett squares / genetics",
            "Human circulatory system",
            "Food chains and ecosystems",
            "Cell structure",
        ],
        "economics_and_finance": [
            "Supply and demand",
            "Simple vs compound interest",
            "Inflation basics",
            "Opportunity cost",
            "Personal budgeting",
            "Elasticity of demand",
        ],
        "statistics_and_data": [
            "Mean, median, mode",
            "Basic probability distributions",
            "Correlation vs causation",
            "Reading and interpreting graphs",
            "Sampling bias",
        ],
        "language_and_grammar": [
            "Parts of speech",
            "Sentence structure and clauses",
            "Active vs passive voice",
            "Common grammar errors",
            "Analogies and vocabulary in context",
        ],
        "history_and_social_studies": [
            "Cause-and-effect in historical events",
            "Comparing political systems",
            "Timeline reasoning",
            "Primary vs secondary sources",
        ],
        "ethics_and_critical_thinking": [
            "Logical fallacies",
            "Ethical dilemmas (trolley-problem style)",
            "Argument structure: premises and conclusions",
            "Cost-benefit reasoning",
        ],
        "everyday_reasoning": [
            "Unit conversions",
            "Scaling a recipe",
            "Map reading and distance estimation",
            "Comparing loan/interest offers",
            "Trip budget planning",
        ],
        "computer_science_light": [
            "Tracing a simple algorithm by hand",
            "Binary number conversion",
            "Boolean logic",
            "Big-O intuition via analogies",
        ],
    }

    def __init__(self, template_path: Optional[str] = None):
        self.template_path = (
            Path(template_path)
            if template_path
            else Path(__file__).resolve().parent.parent / "dataset_generation_prompt.md"
        )

    def generate_prompt(self) -> Tuple[str, str]:
        """Return the populated prompt and its matching empty dataset record."""
        template = self.template_path.read_text(encoding="utf-8")
        replacements: Dict[str, str] = {}
        config: Dict[str, str] = {}

        for name, options in self.PARAMETERS.items():
            selected = random.choice(options)
            replacements[name.upper()] = selected["value"]
            replacements[f"{name.upper()}_DEF"] = selected["definition"]
            config[name] = selected["value"]

        domain = random.choice(list(self.DOMAINS))
        replacements["DOMAIN"] = domain
        topic = random.choice(self.DOMAINS[domain])
        replacements["TOPIC"] = topic

        for placeholder, value in replacements.items():
            template = template.replace(f"<<{placeholder}>>", value)

        dataset_record = {
            "config": config,
            "domain": domain,
            "topic": topic,
            "problem": "",
            "conversation": [],
        }

        return template, json.dumps(dataset_record, indent=2)


def main() -> None:
    """Generate and print one prompt when this module is run directly."""
    print(DatasetPromptGenerator().generate_prompt())


if __name__ == "__main__":
    main()
