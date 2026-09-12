"""Generate Socratic conversation data by retrying model selection and validating model output."""

import argparse
from collections import Counter, defaultdict
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .key_manager import KeyManager
except ImportError:  # pragma: no cover
    from key_manager import KeyManager

try:
    from .prompt_generator import DatasetPromptGenerator
except ImportError:  # pragma: no cover
    from prompt_generator import DatasetPromptGenerator

DEFAULT_ITERATIONS = 50
RETRY_WAIT_SECONDS = 5
MAX_MODEL_SELECTION_ATTEMPTS = 12

# Conversation shape requirements, matching the generation prompt's spec.
MIN_TURNS = 12
MAX_TURNS = 18
VALID_STATES = {"Diagnosis", "Support", "Synthesis", "Closing"}
FAILURE_CATEGORIES = (
    "dead_model_404", "empty_response", "invalid_json_syntax",
    "turn_count_out_of_range", "speaker_alternation_broken",
    "malformed_tutor_tags", "missing_problem_field", "final_turn_not_closing",
    "unexpected_exception", "other",
)
CONFIG_PARAMETERS = (
    "socratic_level", "student_level", "hint_frequency", "mistake_frequency",
    "verbosity", "difficulty",
)

logger = logging.getLogger("pipeline")
logger.setLevel(logging.INFO)
logger.propagate = False

log_dir = Path("data")
log_dir.mkdir(parents=True, exist_ok=True)

if not logger.handlers:
    file_handler = logging.FileHandler(log_dir / "pipeline.log", mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(stream_handler)


# --------------------------------------------------------------------------
# Model selection
# --------------------------------------------------------------------------

def _get_model_with_retry(key_manager: KeyManager) -> Optional[Tuple[Any, int]]:
    """Retry model selection up to the configured limit."""
    for attempt in range(MAX_MODEL_SELECTION_ATTEMPTS):
        selected = key_manager.get_key()
        if selected is not None:
            return selected

        if attempt == MAX_MODEL_SELECTION_ATTEMPTS - 1:
            logger.error(
                "No available model after %s attempts.",
                MAX_MODEL_SELECTION_ATTEMPTS,
            )
            return None

        logger.warning(
            "No available model. Retrying in %s seconds.",
            RETRY_WAIT_SECONDS,
        )
        time.sleep(RETRY_WAIT_SECONDS)


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------

# Some models (e.g. gemma) wrap visible reasoning in different tag names.
# Strip any of these blocks before hunting for the real JSON payload, so a
# JSON-like snippet inside the model's scratch-work doesn't get mistaken
# for the actual output.
_THOUGHT_BLOCK_PATTERN = re.compile(
    r"(?is)<(think|thought|scratchpad|reflection)\b[^>]*>.*?</\1>"
)


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    """Locate and parse the first complete JSON object in `text`.

    Uses json.JSONDecoder.raw_decode instead of regex brace-matching, so a
    nested object (e.g. a "config" block inside the payload) is correctly
    tracked and does not get mistaken for the end of the outer object —
    which was the cause of the previous "Expecting ',' delimiter" failures
    on every model.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")

    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text, idx=start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc

    return payload


def _strip_wrappers(raw_reply: str) -> str:
    """Remove markdown fences and visible-reasoning blocks before parsing."""
    cleaned = (raw_reply or "").strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    cleaned = _THOUGHT_BLOCK_PATTERN.sub("", cleaned)
    return cleaned.strip()


# --------------------------------------------------------------------------
# Payload validation
# --------------------------------------------------------------------------

def _is_valid_conversation_payload(payload: Any) -> bool:
    """Check that the parsed JSON is a conversation matching the generation spec:
    correct turn count, strict student/tutor alternation, tutor turns carrying
    reasoning/state/response fields, and a Synthesis-then-student ending."""
    if not isinstance(payload, dict):
        logger.debug("Rejected payload: not a JSON object")
        return False

    conversation = payload.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        logger.debug("Rejected payload: missing or empty 'conversation' array")
        return False

    problem = payload.get("problem")
    if not isinstance(problem, str) or not problem.strip():
        logger.debug("Rejected payload: missing or empty 'problem' string")
        return False

    turn_count = len(conversation)
    if not (MIN_TURNS <= turn_count <= MAX_TURNS):
        logger.debug(
            "Rejected payload: turn count %s outside [%s, %s]",
            turn_count, MIN_TURNS, MAX_TURNS,
        )
        return False
    if turn_count % 2 != 0:
        logger.debug("Rejected payload: turn count %s is not even", turn_count)
        return False

    expected_speaker = "student"
    for i, item in enumerate(conversation, start=1):
        if not isinstance(item, dict):
            logger.debug("Rejected payload: turn %s is not an object", i)
            return False
        if not {"turn", "speaker"}.issubset(item):
            logger.debug("Rejected payload: turn %s missing required keys", i)
            return False

        speaker = item.get("speaker")
        if speaker != expected_speaker:
            logger.debug(
                "Rejected payload: speaker alternation broken at turn %s "
                "(expected %s, got %s)",
                i, expected_speaker, speaker,
            )
            return False

        if speaker == "student":
            if not isinstance(item.get("text"), str) or not item.get("text", "").strip():
                logger.debug("Rejected payload: turn %s has empty/non-string text", i)
                return False
        elif speaker == "tutor":
            # Tutor turns require reasoning, state, and response fields
            reasoning = item.get("reasoning")
            state = item.get("state")
            response = item.get("response")

            if state == "Closing" and i != turn_count:
                logger.debug("Rejected payload: Closing state appears before final turn")
                return False
            
            if not isinstance(reasoning, str) or not reasoning.strip():
                logger.debug("Rejected payload: tutor turn %s missing/empty reasoning", i)
                return False
            if state not in VALID_STATES:
                logger.debug("Rejected payload: tutor turn %s has invalid state", i)
                return False
            if not isinstance(response, str) or not response.strip():
                logger.debug("Rejected payload: tutor turn %s missing/empty response", i)
                return False

        expected_speaker = "tutor" if expected_speaker == "student" else "student"

    # Conversation must end with: student explaining the rule (second-to-last),
    # then tutor's Closing turn (final).
    student_explanation = conversation[-2]
    final_tutor = conversation[-1]
    
    if student_explanation.get("speaker") != "student":
        logger.debug("Rejected payload: second-to-last turn is not the student")
        return False
    if final_tutor.get("speaker") != "tutor":
        logger.debug("Rejected payload: final turn is not the tutor")
        return False
    if final_tutor.get("state") != "Closing":
        logger.debug("Rejected payload: final tutor turn is not Closing")
        return False

    return True


def _parse_valid_json(reply: str) -> Dict[str, Any]:
    """Parse a model reply and ensure it matches the expected conversation shape."""
    cleaned = _strip_wrappers(reply)
    if not cleaned:
        raise ValueError("empty response")

    payload = _extract_first_json_object(cleaned)

    if not _is_valid_conversation_payload(payload):
        raise ValueError(_structural_failure_reason(payload))

    return payload


def _structural_failure_reason(payload: Any) -> str:
    """Describe a failed structural check for stable failure reporting."""
    if not isinstance(payload, dict):
        return "conversation payload is not a JSON object"
    conversation = payload.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        return "conversation payload missing or empty conversation"
    problem = payload.get("problem")
    if not isinstance(problem, str) or not problem.strip():
        return "missing problem field"
    turn_count = len(conversation)
    if not (MIN_TURNS <= turn_count <= MAX_TURNS):
        return f"turn count {turn_count} outside [{MIN_TURNS}, {MAX_TURNS}]"
    if turn_count % 2 != 0:
        return f"turn count {turn_count} is not even"

    expected_speaker = "student"
    for turn_number, item in enumerate(conversation, start=1):
        if not isinstance(item, dict):
            return f"turn {turn_number} is not an object"
        if item.get("speaker") != expected_speaker:
            return f"speaker alternation broken at turn {turn_number}"
        
        speaker = item.get("speaker")
        if speaker == "student":
            if not isinstance(item.get("text"), str) or not item.get("text", "").strip():
                return f"student turn {turn_number} missing/empty text"
        elif speaker == "tutor":
            reasoning = item.get("reasoning")
            state = item.get("state")
            response = item.get("response")

            if state == "Closing" and turn_number != turn_count:
                return "Closing state appears before final turn"
            
            if not isinstance(reasoning, str) or not reasoning.strip():
                return f"tutor turn {turn_number} missing/empty reasoning"
            if state not in VALID_STATES:
                return f"tutor turn {turn_number} has invalid state"
            if not isinstance(response, str) or not response.strip():
                return f"tutor turn {turn_number} missing/empty response"
        
        expected_speaker = "tutor" if expected_speaker == "student" else "student"

    student_explanation = conversation[-2]
    final_tutor = conversation[-1]
    if student_explanation.get("speaker") != "student":
        return "second-to-last turn is not the student"
    if final_tutor.get("speaker") != "tutor":
        return "final turn is not the tutor"
    if final_tutor.get("state") != "Closing":
        return "final tutor turn is not Closing"
    return "conversation payload failed structural validation"


def _classify_failure_reason(reason: str) -> str:
    """Map variable failure text to a stable summary category."""
    normalized = reason.lower()
    if "404" in normalized or "not found" in normalized:
        return "dead_model_404"
    if "empty response" in normalized or "no response" in normalized or "no text" in normalized:
        return "empty_response"
    if "invalid json" in normalized or "jsondecodeerror" in normalized or "expecting " in normalized:
        return "invalid_json_syntax"
    if "turn count" in normalized:
        return "turn_count_out_of_range"
    if "speaker alternation" in normalized:
        return "speaker_alternation_broken"
    if ("malformed" in normalized and "tag" in normalized) or ("tutor" in normalized and ("reasoning" in normalized or "response" in normalized or "state" in normalized)):
        return "malformed_tutor_tags"
    if "missing" in normalized and "problem" in normalized:
        return "missing_problem_field"
    if "closing" in normalized and "final" in normalized:
        return "final_turn_not_closing"
    if "even" in normalized and "turn" in normalized:
        return "turn_count_out_of_range"
    if "unexpected exception" in normalized:
        return "unexpected_exception"
    return "other"


def _count_model_conversations(model: Any) -> int:
    """Count conversation records stored in a model's JSON set files."""
    model_path = getattr(model, "dest_write_path", None)
    if not model_path:
        return 0

    conversation_count = 0
    for response_path in Path(model_path).glob("*-set[[]*.json"):
        try:
            with response_path.open("r", encoding="utf-8") as file:
                responses = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Unable to count conversations in %s: %s", response_path, exc)
            continue

        if isinstance(responses, list):
            conversation_count += len(responses)

    return conversation_count


def _format_run_summary(stats: Dict[str, Any], duration_seconds: float) -> str:
    """Build the plain-text end-of-run report."""
    attempts = stats["attempts"]
    saved = stats["saved"]
    accept_rate = saved / attempts * 100 if attempts else 0.0
    lines = [
        "\n=== Run Statistics Summary ===",
        "Overall totals",
        f"  Attempts: {attempts}",
        f"  Saved: {saved}",
        f"  Failed: {stats['failed']}",
        f"  Accept rate: {accept_rate:.2f}%",
        f"  Duration (seconds): {duration_seconds:.2f}",
        "",
        "Per-model results",
        "  Provider / Model                         Attempts  Saved  Accept %  Avg tokens  Turns  Total convos",
        "  ----------------------------------------  --------  -----  --------  -----------  -----  Total convos",
    ]
    for model_key, model_stats in stats["models"].items():
        model_attempts = model_stats["attempts"]
        model_saved = model_stats["saved"]
        model_accept_rate = model_saved / model_attempts * 100 if model_attempts else 0.0
        token_values = model_stats["tokens"]
        average_tokens = sum(token_values) / len(token_values) if token_values else 0.0
        turns = model_stats["turns"]
        turn_range = f"{min(turns)}-{max(turns)}" if turns else "-"
        lines.append(
            f"  {model_key:<40} {model_attempts:>8}  {model_saved:>5}  "
            f"{model_accept_rate:>7.2f}%  {average_tokens:>11.2f}  {turn_range:>5}  "
            f"{model_stats['total_conversations']:>13}"
        )

    lines.extend(["", "Failure reasons", "  Category                         Count", "  ------------------------------  -----"])
    for category in FAILURE_CATEGORIES:
        lines.append(f"  {category:<30}  {stats['failures'][category]:>5}")

    turns = stats["turn_counts"]
    turn_summary = (
        f"min={min(turns)}, max={max(turns)}, avg={sum(turns) / len(turns):.2f}"
        if turns else "no saved conversations"
    )
    lines.extend(["", "Turn-count distribution", f"  {turn_summary}", "", "Domain coverage"])
    for domain, count in sorted(stats["domains"].items()):
        lines.append(f"  {domain:<30} {count:>5}")

    lines.extend(["", "Config parameter balance"])
    for parameter in CONFIG_PARAMETERS:
        lines.append(f"  {parameter}")
        for value, count in sorted(stats["config"][parameter].items()):
            lines.append(f"    {value:<26} {count:>5}")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def run_pipeline(
    iterations: int = DEFAULT_ITERATIONS,
    models_path: str = "code/model.json",
    data_dir: str = "data",
    manager: Optional[KeyManager] = None,
) -> int:
    """Run the pipeline for up to ``iterations`` attempts and return successful writes."""
    key_manager = manager or KeyManager(models_path=models_path)

    if not key_manager.keys:
        logger.error("No models found in %s.", models_path)
        return 0

    output_dir = Path(data_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_count = 0
    stats: Dict[str, Any] = {
        "attempts": 0,
        "saved": 0,
        "failed": 0,
        "failures": Counter(),
        "models": defaultdict(lambda: {
            "attempts": 0,
            "saved": 0,
            "tokens": [],
            "turns": [],
            "total_conversations": 0,
            "model": None,
        }),
        "turn_counts": [],
        "domains": Counter(),
        "config": defaultdict(Counter),
    }
    for model in key_manager.keys:
        model_key = f"{model.provider} / {model.model_name}"
        stats["models"][model_key]["model"] = model
    started_at = time.monotonic()

    def record_failure(model: Any, reason: str) -> None:
        stats["failed"] += 1
        stats["failures"][_classify_failure_reason(reason)] += 1

    def print_summary() -> None:
        for model_stats in stats["models"].values():
            model_stats["total_conversations"] = _count_model_conversations(model_stats["model"])
        summary = _format_run_summary(stats, time.monotonic() - started_at)
        print(summary)
        logger.info(summary)

    try:
        # Reset consecutive failure counters for all models at the start of iterations
        key_manager.reset_all_failure_counters()

        for iteration in range(1, iterations + 1):
            selected = _get_model_with_retry(key_manager)
            if selected is None:
                logger.error("Could not select a model during iteration %s.", iteration)
                break

            model, model_index = selected
            model_key = f"{model.provider} / {model.model_name}"
            stats["attempts"] += 1
            stats["models"][model_key]["attempts"] += 1
            logger.info(
                "SELECTED provider=%s model=%s",
                model.provider,
                model.model_name,
            )

            try:
                prompt, generated_record_json = DatasetPromptGenerator().generate_prompt()
                success, reply, total_tokens = model.call_model(prompt)
            except Exception as exc:
                record_failure(model, f"unexpected exception: {exc}")
                logger.error(
                    "MODEL_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    f"unexpected exception: {exc}",
                )
                key_manager.increment_model_failure(model_index)
                continue

            if not success or reply is None:
                record_failure(model, "model call failed or returned no response")
                logger.error(
                    "MODEL_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    "model call failed or returned no response",
                )
                key_manager.increment_model_failure(model_index)
                continue

            try:
                key_manager.reduce_key_usage(model_index, int(total_tokens or 0))
            except Exception as exc:
                logger.error(
                    "USAGE_UPDATE_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    f"unable to reduce usage: {exc}",
                )

            try:
                payload = _parse_valid_json(reply)
                generated_record = json.loads(generated_record_json)
                generated_record["problem"] = payload["problem"]
                generated_record["conversation"] = payload["conversation"]
            except (ValueError, TypeError, KeyError) as exc:
                record_failure(model, str(exc))
                try:
                    rejected_path = model.write_rejected_response(reply)
                    logger.info(
                        "REJECTED_RESPONSE_SAVED provider=%s model=%s path=%s",
                        model.provider,
                        model.model_name,
                        rejected_path,
                    )
                except OSError as write_exc:
                    logger.error(
                        "REJECTED_RESPONSE_SAVE_FAILURE provider=%s model=%s reason=%s",
                        model.provider,
                        model.model_name,
                        f"unable to write rejected response: {write_exc}",
                    )
                logger.error(
                    "JSON_INVALID provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    str(exc),
                )
                continue

            try:
                dataset_path = model.write_response(generated_record)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                record_failure(model, f"unable to write payload: {exc}")
                logger.error(
                    "SAVE_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    f"unable to write payload: {exc}",
                )
                continue
             
            # Reset consecutive failure counter on successful completion
            key_manager.reset_model_failure_counter(model_index)
            
            print(f"Valid payload for provider={model.provider} model={model.model_name}: {payload}")
            saved_count += 1
            stats["saved"] += 1
            model_stats = stats["models"][model_key]
            model_stats["saved"] += 1
            model_stats["tokens"].append(int(total_tokens or 0))
            turn_count = len(payload.get("conversation", []))
            model_stats["turns"].append(turn_count)
            stats["turn_counts"].append(turn_count)
            stats["domains"][generated_record.get("domain", "unknown")] += 1
            for parameter in CONFIG_PARAMETERS:
                value = generated_record.get("config", {}).get(parameter, "unknown")
                stats["config"][parameter][value] += 1
            logger.info(
                "SUCCESS provider=%s model=%s total_tokens=%s turns=%s path=%s",
                model.provider,
                model.model_name,
                total_tokens,
                len(payload.get("conversation", [])),
                dataset_path,
            )

    except KeyboardInterrupt:
        logger.warning(
            "Interrupted by user after %s successful write(s). Exiting cleanly.",
            saved_count,
        )
        print_summary()
        return saved_count

    print_summary()
    return saved_count


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Socratic conversation data from available models.")
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help="Number of model attempts to run. Default is 50.",
    )
    parser.add_argument(
        "--models-path",
        type=str,
        default="code/model.json",
        help="Path to the JSON file that lists all models.",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Directory used for generated data and logs.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    count = run_pipeline(
        iterations=args.iterations,
        models_path=args.models_path,
        data_dir=args.data_dir,
    )
    print(f"Saved {count} validated conversation(s).")