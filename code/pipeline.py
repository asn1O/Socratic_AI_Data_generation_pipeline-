"""Generate Socratic conversation data by retrying model selection and validating model output."""

import argparse
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

# Conversation shape requirements, matching the generation prompt's spec.
MIN_TURNS = 12
MAX_TURNS = 18
VALID_STATES = {"Diagnosis", "Support", "Synthesis"}

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
    """Retry model selection until a usable model is available."""
    while True:
        selected = key_manager.get_key()
        if selected is not None:
            return selected

        logger.warning(
            "No available model. Retrying in %s seconds.",
            RETRY_WAIT_SECONDS,
        )
        time.sleep(RETRY_WAIT_SECONDS)


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------

# Matches a tutor turn's full "text" field: reasoning, then state, then
# response, in that exact order, with no other content in between.
_TUTOR_TAG_PATTERN = re.compile(
    r"^<reasoning>.*?</reasoning><state>(Diagnosis|Support|Synthesis)</state>"
    r"<response>.*?</response>$",
    re.DOTALL,
)

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
    well-formed <reasoning><state><response> tags, and a Synthesis-then-student
    ending."""
    if not isinstance(payload, dict):
        logger.debug("Rejected payload: not a JSON object")
        return False

    conversation = payload.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        logger.debug("Rejected payload: missing or empty 'conversation' array")
        return False

    turn_count = len(conversation)
    if not (MIN_TURNS <= turn_count <= MAX_TURNS):
        logger.debug(
            "Rejected payload: turn count %s outside [%s, %s]",
            turn_count, MIN_TURNS, MAX_TURNS,
        )
        return False

    expected_speaker = "student"
    for i, item in enumerate(conversation, start=1):
        if not isinstance(item, dict):
            logger.debug("Rejected payload: turn %s is not an object", i)
            return False
        if not {"turn", "speaker", "text"}.issubset(item):
            logger.debug("Rejected payload: turn %s missing required keys", i)
            return False
        if not isinstance(item["text"], str) or not item["text"].strip():
            logger.debug("Rejected payload: turn %s has empty/non-string text", i)
            return False

        speaker = item.get("speaker")
        if speaker != expected_speaker:
            logger.debug(
                "Rejected payload: speaker alternation broken at turn %s "
                "(expected %s, got %s)",
                i, expected_speaker, speaker,
            )
            return False

        if speaker == "tutor":
            match = _TUTOR_TAG_PATTERN.match(item["text"].strip())
            if not match:
                logger.debug(
                    "Rejected payload: tutor turn %s missing/malformed "
                    "<reasoning><state><response> tags", i,
                )
                return False

        expected_speaker = "tutor" if expected_speaker == "student" else "student"

    # Conversation must end on a tutor Synthesis turn followed by the
    # student's own explanation — never the tutor speaking last.
    last_tutor, last_student = conversation[-2], conversation[-1]
    if last_student.get("speaker") != "student":
        logger.debug("Rejected payload: final turn is not the student")
        return False
    if last_tutor.get("speaker") != "tutor":
        logger.debug("Rejected payload: second-to-last turn is not the tutor")
        return False

    final_match = _TUTOR_TAG_PATTERN.match(last_tutor["text"].strip())
    if not final_match or final_match.group(1) != "Synthesis":
        logger.debug("Rejected payload: final tutor turn is not Synthesis")
        return False

    return True


def _parse_valid_json(reply: str) -> Dict[str, Any]:
    """Parse a model reply and ensure it matches the expected conversation shape."""
    cleaned = _strip_wrappers(reply)
    if not cleaned:
        raise ValueError("empty response")

    payload = _extract_first_json_object(cleaned)

    if not _is_valid_conversation_payload(payload):
        raise ValueError("conversation payload failed structural validation")

    return payload


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def _save_payload(
    output_dir: Path,
    model: Any,
    total_tokens: int,
    payload: Dict[str, Any],
) -> Path:
    """Append a validated conversation, with generation metadata, to the dataset file."""
    dataset_path = output_dir / "dataset.jsonl"
    record = {
        "provider": model.provider,
        "model": model.model_name,
        "total_tokens": total_tokens,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with dataset_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return dataset_path


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

    try:
        for iteration in range(1, iterations + 1):
            selected = _get_model_with_retry(key_manager)
            if selected is None:
                logger.error("Could not select a model during iteration %s.", iteration)
                break

            model, model_index = selected
            logger.info(
                "SELECTED provider=%s model=%s",
                model.provider,
                model.model_name,
            )

            try:
                prompt = DatasetPromptGenerator().generate_prompt()
                success, reply, total_tokens = model.call_model(prompt)
            except Exception as exc:
                logger.error(
                    "MODEL_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    f"unexpected exception: {exc}",
                )
                continue

            if not success or reply is None:
                logger.error(
                    "MODEL_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    "model call failed or returned no response",
                )
                continue

            try:
                payload = _parse_valid_json(reply)
            except ValueError as exc:
                logger.error(
                    "JSON_INVALID provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    str(exc),
                )
                continue

            try:
                dataset_path = model.write_response(payload)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.error(
                    "SAVE_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    f"unable to write payload: {exc}",
                )
                continue
             
            print(f"Valid payload for provider={model.provider} model={model.model_name}: {payload}")
            saved_count += 1
            logger.info(
                "SUCCESS provider=%s model=%s total_tokens=%s turns=%s path=%s",
                model.provider,
                model.model_name,
                total_tokens,
                len(payload.get("conversation", [])),
                dataset_path,
            )

            try:
                if total_tokens > 0:
                    key_manager.reduce_key_usage(model_index, int(total_tokens))
            except Exception as exc:
                # Data is already saved — this only affects usage bookkeeping,
                # so log and move on rather than treating it as a fatal error
                # for this iteration.
                logger.error(
                    "USAGE_UPDATE_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    f"unable to reduce usage: {exc}",
                )

    except KeyboardInterrupt:
        logger.warning(
            "Interrupted by user after %s successful write(s). Exiting cleanly.",
            saved_count,
        )
        return saved_count

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