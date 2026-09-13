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
                "definition": "Tutor explains directly and gives explicit guidance; in at least half of tutor turns, state something outright rather than asking a question.",
            },
            {
                "value": "medium",
                "definition": "Tutor mixes direct explanation with guiding questions; roughly half of tutor turns are pure questions, the rest pair a brief statement with a question.",
            },
            {
                "value": "high",
                "definition": "Tutor almost never states things directly; at least 90% of turns are phrased purely as a question, even when the student is struggling.",
            },
        ],
        "student_level": [
            {
                "value": "beginner",
                "definition": "Student is unfamiliar with the topic. On several turns, expect the student to express genuine non-understanding rather than attempting an answer at all (e.g. \"I don't really get what that means\", \"I'm lost on this part\") — this is expected and realistic, not a gap to fill with a guess. This is distinct from a mistake: it carries no claim, right or wrong. When mistake_frequency does require an explicit wrong claim, keep it conceptual or definitional — misunderstanding what a term or basic relationship means.",
            },
            {
                "value": "intermediate",
                "definition": "Student has partial understanding. Some turns can express uncertainty about a specific step (e.g. \"I think it's this, but I'm not totally sure\") without committing to a firm right or wrong claim — this is fine and doesn't need to resolve into an answer immediately. When mistake_frequency does require an explicit wrong claim, it should be procedural or in execution — the core idea is right but misapplied to this case — not a misunderstanding of the concept itself.",
            },
            {
                "value": "advanced",
                "definition": "Student reasons correctly most of the time. Any confusion is brief and tied to a specific edge case, not general uncertainty about the topic. When mistake_frequency does require an explicit wrong claim, it must be an edge-case error, overgeneralization, or minor slip (e.g. sign error, boundary condition) — never a basic conceptual misunderstanding.",
            },
        ],
        "hint_frequency": [
            {
                "value": "low",
                "definition": "Governs how much a question reveals, not whether it's a question: avoid handing the student a comparison, mechanism, or vocabulary term they haven't produced themselves. On a new sub-point, ask an open question first; only add a hint if the student's prior attempt on that same sub-point was wrong or stuck. Hints appear in at most ~1 in 4 tutor turns.",
            },
            {
                "value": "medium",
                "definition": "Governs how much a question reveals, not whether it's a question. Offer a hint (a comparison, partial mechanism, or narrowed answer space) once the student has struggled on a sub-point at least once. Hints appear in roughly half of tutor turns.",
            },
            {
                "value": "high",
                "definition": "The tutor may offer a hint (a comparison, partial mechanism, or narrowed answer space) from the student's first attempt at a sub-point, without waiting for a struggle — used in most tutor turns. Never reveal the full solution outright.",
            },
        ],
        "mistake_frequency": [
            {
                "value": "low",
                "definition": "Student is mostly correct throughout; at most one explicit wrong claim across the whole conversation. Expressing uncertainty is fine and does not count — the student must actually assert something incorrect for it to count.",
            },
            {
                "value": "medium",
                "definition": "Student explicitly asserts exactly two incorrect claims at different points, each identified and corrected before the next mistake appears. Hedging does not count as a mistake.",
            },
            {
                "value": "high",
                "definition": "Student explicitly asserts three or more incorrect claims. At least one misconception should partially resurface in a related form even after being corrected once, so the pattern feels realistic rather than a checklist. Hedging does not count as a mistake.",
            },
        ],
        "verbosity": [
            {
                "value": "low",
                "definition": "Both tutor and student give short, concise responses — roughly 5-20 words per visible turn (the tutor's internal reasoning field stays terse regardless of this setting).",
            },
            {
                "value": "medium",
                "definition": "Moderate length responses with some explanation — roughly 20-45 words per visible turn.",
            },
            {
                "value": "high",
                "definition": "Longer, more detailed responses — roughly 45-90 words per visible turn — but still natural spoken language, not essay-style prose.",
            },
        ],
        "difficulty": [
            {
                "value": "easy",
                "definition": "Problem is straightforward: the solution takes 1-2 dependent reasoning steps, one concept, and no plausible wrong path a student would realistically take.",
            },
            {
                "value": "medium",
                "definition": "Problem requires moderate multi-step reasoning: 3-4 dependent steps, or one genuinely non-obvious step, or a plausible distractor a student could latch onto.",
            },
            {
                "value": "hard",
                "definition": "Problem requires multi-step, non-obvious, or tricky reasoning: 5+ dependent steps, or two or more interacting concepts, or at least one built-in trap — a plausible-looking wrong path a competent student could actually take. Student turns should show visible effort even if mistake_frequency is low; a hard problem should never look as smoothly solved as an easy one.",
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
