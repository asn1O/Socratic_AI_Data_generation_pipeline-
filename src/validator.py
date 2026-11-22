"""
================================================================================
STAGE 4: PARSING, SANITIZATION, STRICT VALIDATION & LEAKAGE DETECTION
================================================================================

Role & Purpose:
    This module encapsulates all parsing, sanitization, and verification logic
    for generated Socratic tutoring dialogues as specified in Section 5.4 and
    Section 5.5 of PIPELINE_TECHNICAL_REPORT.md.

What happens in this file:
    1. Text Sanitization:
       - Removes model reasoning scratchpad blocks (<think>, <thought>,
         <scratchpad>, <reflection>) produced by reasoning models (e.g. DeepSeek,
         Gemma, Qwen) so internal thoughts do not corrupt JSON decoding.
       - Strips Markdown code blocks (```json ... ```).

    2. Robust Stream JSON Decoding:
       - Uses `json.JSONDecoder().raw_decode()` starting from the first '{' brace.
       - Unlike naive regex brace-matching, this correctly handles nested objects
         like `{"config": {...}}` without early termination errors.

    3. Hyperparameter & Config Leakage Detection:
       - Scans dialogue turns and tutor reasoning traces for accidental leakage
         of system prompt instructions, parameter names (e.g. 'socratic_level',
         'hint_frequency', 'student_level'), or persona directives (e.g.
         'beginner student', 'maintain the easy difficulty').

    4. Pedagogical & Structural Validation:
       - Enforces finite state machine and pedagogical invariants:
         * Turn count bound: 12-18 turns (strictly even).
         * Strict speaker alternation: Student -> Tutor -> Student ...
         * Turn numbering: Sequential 1..N.
         * Tutor schema: Must include non-empty reasoning, valid state
           ('Diagnosis', 'Support', 'Synthesis', 'Closing'), and response.
         * Closing invariant: 'Closing' state cannot appear before turn N.
         * Terminal sequence: Turn N-2 (Tutor) must be 'Synthesis', Turn N-1
           (Student) must be the explanation, Turn N (Tutor) must be 'Closing'.

    5. Offline Dataset Audit & Cleanup:
       - Provides CLI and helper functions (`validate_dataset`, `validate_file`)
         to scan saved JSON datasets on disk, report violations, and optionally
         purge invalid records.
================================================================================
"""

import argparse
from collections import Counter
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("pipeline.validator")

# Conversation shape requirements matching generation prompt specification
MIN_TURNS = 12
MAX_TURNS = 18
VALID_STATES = {"Diagnosis", "Support", "Synthesis", "Closing"}
VALID_SPEAKERS = {"student", "tutor"}

FAILURE_CATEGORIES = (
    "dead_model_404",
    "empty_response",
    "invalid_json_syntax",
    "turn_count_out_of_range",
    "speaker_alternation_broken",
    "turn_numbering_broken",
    "malformed_tutor_tags",
    "missing_problem_field",
    "second_last_not_synthesis",
    "final_turn_not_closing",
    "config_leakage",
    "unexpected_exception",
    "other",
)

CONFIG_PARAMETERS = (
    "socratic_level",
    "student_level",
    "hint_frequency",
    "mistake_frequency",
    "verbosity",
    "difficulty",
)

# ------------------------------------------------------------------------------
# 1. Regex Patterns for Sanitization and Leakage Detection
# ------------------------------------------------------------------------------

# Strips visible model reasoning traces
_THOUGHT_BLOCK_PATTERN = re.compile(
    r"(?is)<(think|thought|scratchpad|reflection)\b[^>]*>.*?</\1>"
)

# Detects direct hyperparameter or persona directive leakage into conversation text
_DIRECT_PARAM_PATTERNS = [
    re.compile(r"\b(socratic_level|hint_frequency|mistake_frequency|student_level)\b", re.IGNORECASE),
    re.compile(r"\b(socratic\s+level|hint\s+frequency|mistake\s+frequency|student\s+level)\b", re.IGNORECASE),
    re.compile(r"\b(beginner|intermediate|advanced)\s+(student|persona|level)\b", re.IGNORECASE),
    re.compile(r"\bas\s+per\s+the\s+.*?(level|frequency|config|socratic|verbosity)\b", re.IGNORECASE),
    re.compile(r"\bverbosity\b", re.IGNORECASE),
]

_DIFFICULTY_LEAK_PATTERN = re.compile(
    r"\b(easy|medium|hard)\s+difficulty\b|\bdifficulty\s*:\s*(easy|medium|hard)\b|\bmaintain\s+the\s+['\"]?(easy|medium|hard)['\"]?\s+difficulty\b",
    re.IGNORECASE,
)


def _strip_wrappers(raw_reply: str) -> str:
    """Remove markdown fences and visible-reasoning blocks before parsing."""
    cleaned = (raw_reply or "").strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    cleaned = _THOUGHT_BLOCK_PATTERN.sub("", cleaned)
    return cleaned.strip()


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    """Locate and parse the first complete JSON object in `text`.

    Uses json.JSONDecoder.raw_decode instead of regex brace-matching, so a
    nested object (e.g. a 'config' block inside the payload) is correctly
    tracked and does not get mistaken for the end of the outer object.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")

    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text, idx=start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ValueError("parsed JSON is not an object")

    return payload


# ------------------------------------------------------------------------------
# 2. Config & Parameter Leakage Detection
# ------------------------------------------------------------------------------

def check_config_leakage(text: str, custom_parameters: Optional[Dict[str, Any]] = None) -> List[str]:
    """Check text (especially tutor reasoning and turns) for prompt configuration leakage.

    Returns a list of matched leakage descriptions.
    """
    if not text:
        return []

    flags = []
    for pattern in _DIRECT_PARAM_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            flags.append(f"direct_parameter_or_persona_leak: {matches}")

    difficulty_matches = _DIFFICULTY_LEAK_PATTERN.findall(text)
    if difficulty_matches:
        flags.append(f"difficulty_config_leak: {difficulty_matches}")

    if custom_parameters:
        standard_meta_keys = {
            "difficulty", "scenario_id", "persona_id", "persona_name",
            "learning_objective", "target_misconception", "expected_concepts",
            "problem", "question", "topic", "subtopic", "domain",
        }
        for param_name, param_val in custom_parameters.items():
            if param_name.lower() in standard_meta_keys:
                continue
            if param_name.lower() in text.lower():
                flags.append(f"custom_param_name_leak: {param_name}")
            if isinstance(param_val, str) and len(param_val) > 4 and param_val.lower() in text.lower():
                flags.append(f"custom_param_val_leak: {param_val}")

    return flags


# ------------------------------------------------------------------------------
# 3. Unified Structural and Pedagogical Invariants Validation
# ------------------------------------------------------------------------------

def _validate_conversation_payload(payload: Any) -> Tuple[bool, str]:
    """Single source of truth for conversational payload validation.

    Returns:
        (True, "") if payload satisfies all structural, pedagogical, and leakage rules.
        (False, failure_reason) with a detailed description if invalid.
    """
    if not isinstance(payload, dict):
        return False, "conversation payload is not a JSON object"

    conversation = payload.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        return False, "conversation payload missing or empty conversation"

    problem = payload.get("problem")
    if not isinstance(problem, str) or not problem.strip():
        return False, "missing problem field"

    turn_count = len(conversation)
    if not (MIN_TURNS <= turn_count <= MAX_TURNS):
        return False, f"turn count {turn_count} outside [{MIN_TURNS}, {MAX_TURNS}]"
    if turn_count % 2 != 0:
        return False, f"turn count {turn_count} is not even"

    expected_speaker = "student"
    for turn_number, item in enumerate(conversation, start=1):
        if not isinstance(item, dict):
            return False, f"turn {turn_number} is not an object"
        if not {"turn", "speaker"}.issubset(item):
            return False, f"turn {turn_number} missing required keys"
        if item.get("turn") != turn_number:
            return (
                False,
                f"turn numbering broken at turn {turn_number} (expected {turn_number}, got {item.get('turn')})",
            )

        speaker = item.get("speaker")
        speaker_normalized = str(speaker).strip().lower() if speaker else ""
        if speaker != expected_speaker and speaker_normalized != expected_speaker:
            return False, f"speaker alternation broken at turn {turn_number}"

        if expected_speaker == "student":
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                return False, f"student turn {turn_number} missing/empty text"
            leakage = check_config_leakage(text)
            if leakage:
                return False, f"student turn {turn_number} config leakage detected: {leakage}"
        elif expected_speaker == "tutor":
            reasoning = item.get("reasoning")
            state = item.get("state")
            response = item.get("response")

            # Check state validity (allow case-insensitive match against VALID_STATES)
            state_match = None
            if isinstance(state, str):
                for vs in VALID_STATES:
                    if state.strip().lower() == vs.lower():
                        state_match = vs
                        break

            if state_match == "Closing" and turn_number != turn_count:
                return False, "Closing state appears before final turn"

            if not isinstance(reasoning, str) or not reasoning.strip():
                return False, f"tutor turn {turn_number} missing/empty reasoning"
            if not state_match:
                return False, f"tutor turn {turn_number} has invalid state"
            if not isinstance(response, str) or not response.strip():
                return False, f"tutor turn {turn_number} missing/empty response"

            # Check for parameter leakage in reasoning or tutor response
            leakage = check_config_leakage(reasoning) or check_config_leakage(response)
            if leakage:
                return False, f"tutor turn {turn_number} config leakage detected: {leakage}"

        expected_speaker = "tutor" if expected_speaker == "student" else "student"

    # Verify deterministic pedagogical ending sequence:
    # 1. Turn N-2 (Tutor) in Synthesis state
    # 2. Turn N-1 (Student) final general explanation
    # 3. Turn N (Tutor) in Closing state
    second_last_tutor = conversation[-3]
    student_explanation = conversation[-2]
    final_tutor = conversation[-1]

    if str(second_last_tutor.get("speaker", "")).strip().lower() != "tutor":
        return False, "turn before student explanation is not the tutor"

    second_last_state = str(second_last_tutor.get("state", "")).strip().lower()
    if second_last_state != "synthesis":
        return (
            False,
            f"second-to-last tutor turn is not Synthesis (got {second_last_tutor.get('state')})",
        )

    if str(student_explanation.get("speaker", "")).strip().lower() != "student":
        return False, "second-to-last turn is not the student"

    if str(final_tutor.get("speaker", "")).strip().lower() != "tutor":
        return False, "final turn is not the tutor"

    final_state = str(final_tutor.get("state", "")).strip().lower()
    if final_state != "closing":
        return False, "final tutor turn is not Closing"

    return True, ""


def _is_valid_conversation_payload(payload: Any) -> bool:
    """Check that the parsed JSON matches the conversation generation spec."""
    is_valid, reason = _validate_conversation_payload(payload)
    if not is_valid:
        logger.debug("Rejected payload: %s", reason)
    return is_valid


def _structural_failure_reason(payload: Any) -> str:
    """Describe a failed structural check for stable failure reporting."""
    _, reason = _validate_conversation_payload(payload)
    return reason or "conversation payload failed structural validation"


def _parse_valid_json(reply: str) -> Dict[str, Any]:
    """Parse a model reply and ensure it matches the expected conversation shape."""
    cleaned = _strip_wrappers(reply)
    if not cleaned:
        raise ValueError("empty response")

    payload = _extract_first_json_object(cleaned)

    is_valid, reason = _validate_conversation_payload(payload)
    if not is_valid:
        raise ValueError(reason)

    return payload


def _classify_failure_reason(reason: str) -> str:
    """Map variable failure text to a stable summary category."""
    normalized = reason.lower()
    if "404" in normalized or "not found" in normalized:
        return "dead_model_404"
    if "empty response" in normalized or "no response" in normalized or "no text" in normalized:
        return "empty_response"
    if "invalid json" in normalized or "jsondecodeerror" in normalized or "expecting " in normalized:
        return "invalid_json_syntax"
    if "turn numbering" in normalized or "numbering broken" in normalized:
        return "turn_numbering_broken"
    if "second-to-last tutor turn is not synthesis" in normalized or (
        "synthesis" in normalized and ("second" in normalized or "tutor" in normalized)
    ):
        return "second_last_not_synthesis"
    if "turn count" in normalized or ("even" in normalized and "turn" in normalized):
        return "turn_count_out_of_range"
    if "speaker alternation" in normalized:
        return "speaker_alternation_broken"
    if "config leakage" in normalized or "parameter_leak" in normalized:
        return "config_leakage"
    if ("malformed" in normalized and "tag" in normalized) or (
        "tutor" in normalized and ("reasoning" in normalized or "response" in normalized or "state" in normalized)
    ):
        return "malformed_tutor_tags"
    if "missing" in normalized and "problem" in normalized:
        return "missing_problem_field"
    if "closing" in normalized and "final" in normalized:
        return "final_turn_not_closing"
    if "unexpected exception" in normalized:
        return "unexpected_exception"
    return "other"


# ------------------------------------------------------------------------------
# 4. Standalone Dataset Validation & Cleanup Utility
# ------------------------------------------------------------------------------

def validate_conversation_entry(entry: dict) -> Tuple[bool, str, str]:
    """Structurally validate an existing conversation record from a dataset file.

    Returns:
        (is_valid, error_detail, error_category)
    """
    is_valid, reason = _validate_conversation_payload(entry)
    if is_valid:
        return True, "", ""
    return False, reason, _classify_failure_reason(reason)


def validate_file(
    file_path: Path,
    error_stats: Counter,
    delete_invalid: bool = False,
) -> Tuple[int, int]:
    """Validate every conversation in a single JSON file.

    Returns:
        (total_count, invalid_count)
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            conversations = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Unable to read JSON file %s: %s", file_path, exc)
        return 0, 0

    if not isinstance(conversations, list):
        return 0, 0

    valid = []
    invalid_count = 0
    total_count = len(conversations)

    for index, element in enumerate(conversations):
        is_valid, error_detail, error_category = validate_conversation_entry(element)
        if is_valid:
            valid.append(element)
        else:
            invalid_count += 1
            error_stats[error_category] += 1
            action = "REMOVED" if delete_invalid else "INVALID"
            logger.warning("%s file=%s index=%d reason=%s", action, file_path, index, error_detail)

    if delete_invalid and invalid_count > 0:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(valid, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info("Rewrote %s (removed %d invalid records)", file_path, invalid_count)

    return total_count, invalid_count


def validate_dataset(
    data_dir: Path,
    delete_invalid: bool = False,
) -> Tuple[int, int, Dict[str, Dict[str, int]], Counter, int]:
    """Scan and validate all JSON conversation files under data_dir.

    Returns:
        (total_conversations, total_invalid, folder_stats, error_stats, file_count)
    """
    data_dir = Path(data_dir)
    json_files = sorted(
        [f for f in data_dir.rglob("*.json") if not f.name.endswith("flagged_config_leakage.json")]
    )
    total_conversations = 0
    total_invalid = 0
    error_stats = Counter()
    folder_stats: Dict[str, Dict[str, int]] = {}

    for file_path in json_files:
        try:
            rel_path = file_path.relative_to(data_dir)
            folder_name = rel_path.parts[0] if len(rel_path.parts) > 1 else "(root)"
        except ValueError:
            folder_name = file_path.parent.name

        if folder_name not in folder_stats:
            folder_stats[folder_name] = {"total": 0, "invalid": 0}

        file_total, file_invalid = validate_file(file_path, error_stats, delete_invalid)
        total_conversations += file_total
        total_invalid += file_invalid
        folder_stats[folder_name]["total"] += file_total
        folder_stats[folder_name]["invalid"] += file_invalid

    return total_conversations, total_invalid, folder_stats, error_stats, len(json_files)


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and sanitize Socratic conversation dataset files."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Path to directory containing conversation JSON files.",
    )
    parser.add_argument(
        "--delete-invalid",
        action="store_true",
        help="Permanently remove invalid/leaked conversations from disk files.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_cli_args()
    total, invalid, folders, errors, files = validate_dataset(
        args.data_dir,
        delete_invalid=args.delete_invalid,
    )
    print(f"\nScanned {files} files across {len(folders)} model directories.")
    print(f"Total conversations : {total}")
    print(f"Valid conversations : {total - invalid}")
    print(f"Invalid / Leaked    : {invalid}")
    if errors:
        print("\nFailure breakdown:")
        for cat, cnt in errors.most_common():
            print(f"  {cat:<30} {cnt:>5}")
