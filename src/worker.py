"""
================================================================================
STAGE 3: WORKER LIFECYCLE, INFERENCE ENGINE & CIRCUIT BREAKER
================================================================================

Role & Purpose:
    This module implements the isolated worker thread execution loop for an
    individual language model. Each model configured in models/models.json is
    assigned its own dedicated worker thread running `_run_model_worker`, as
    specified in Section 5.3 of PIPELINE_TECHNICAL_REPORT.md.

What happens in this file:
    1. Single-Model Loop:
       - Runs for the model's partitioned share of iterations.
       - Safely checks `stop_event` at turn boundaries and during sleep intervals
         to support clean cancellation on KeyboardInterrupt (SIGINT).

    2. Prompt Generation & Model Inference:
       - Invokes `DatasetPromptGenerator().generate_prompt()` to sample a
         pedagogical profile (6 hyperparameters, 1 domain, 1 subtopic) and
         hydrate `dataset_generation_prompt.md`.
       - Calls `model.call_model(prompt)` via the universal OpenAI-compatible
         adapter.

    3. Circuit Breaking:
       - Tracks `consecutive_failures`.
       - If an endpoint fails 3 consecutive times (e.g. 404, 500, network error),
         the worker trips the circuit breaker: flags `model.expired = True`,
         logs `MODEL_EXPIRED`, and cleanly terminates without halting other
         workers.

    4. Rate-Limit Throttling:
       - For models configured with `"wait_1_min": true` (e.g. quota-constrained
         free tier endpoints), the worker pauses for 60 seconds (`MODEL_WAIT_SECONDS`)
         between consecutive requests using interrupt-aware sleep.

    5. Payload Routing & Persistence:
       - Passes replies to `_parse_valid_json()` for sanitization, stream decoding,
         and structural/leakage verification.
       - Valid payloads -> saved via `model.write_response()` into sharded sets
         (`data/<Provider_Model>/<WorkerID>-set[N].json`).
       - Malformed / rejected payloads -> saved via `model.write_rejected_response()`
         into `data/rejected_responses/` for offline inspection and prompt tuning.

    6. Local Telemetry Recording:
       - Updates thread-local metrics for token usage, turn counts, domain
         coverage, and failure classifications without lock contention.
================================================================================
"""

import json
import logging
import sys
import threading
import time
from typing import Any, Dict, Optional

try:
    from .prompt_generator import DatasetPromptGenerator
except ImportError:  # pragma: no cover
    from prompt_generator import DatasetPromptGenerator

try:
    from .validator import (
        CONFIG_PARAMETERS,
        _classify_failure_reason,
        _parse_valid_json,
    )
except ImportError:  # pragma: no cover
    from validator import (
        CONFIG_PARAMETERS,
        _classify_failure_reason,
        _parse_valid_json,
    )

try:
    from .telemetry import _format_model_summary, _new_stats
except ImportError:  # pragma: no cover
    from telemetry import _format_model_summary, _new_stats

logger = logging.getLogger("pipeline")
MODEL_WAIT_SECONDS = 60


def _sleep_or_stop(seconds: float, stop_event: Optional[threading.Event]) -> bool:
    """Sleep for the specified seconds, or return True immediately if stop_event is set."""
    if stop_event is not None:
        return stop_event.wait(timeout=seconds)

    # Support testing environments that patch src.pipeline.time.sleep
    pipeline_mod = sys.modules.get("src.pipeline") or sys.modules.get("pipeline")
    if pipeline_mod and hasattr(pipeline_mod, "time") and hasattr(pipeline_mod.time, "sleep"):
        pipeline_mod.time.sleep(seconds)
    else:
        time.sleep(seconds)
    return False


def _run_model_worker(
    model: Any,
    iterations: int,
    model_logger: Optional[logging.Logger] = None,
    stats: Optional[Dict[str, Any]] = None,
    stop_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """Run one model worker and return statistics owned entirely by this worker."""
    model_key = f"{model.provider} / {model.model_name}"
    if stats is None:
        stats = _new_stats()
    stats["models"][model_key]["model"] = model
    model_logger = model_logger or logger

    for _ in range(iterations):
        if stop_event is not None and stop_event.is_set():
            break
        stats["attempts"] += 1
        stats["models"][model_key]["attempts"] += 1
        model_logger.info(
            "MODEL_START provider=%s model=%s",
            model.provider,
            model.model_name,
        )

        try:
            prompt, generated_record_json = DatasetPromptGenerator().generate_prompt()
        except Exception as exc:
            stats["failed"] += 1
            stats["failures"][_classify_failure_reason(f"unexpected exception: {exc}")] += 1
            model_logger.error(
                "MODEL_FAILURE provider=%s model=%s reason=%s",
                model.provider,
                model.model_name,
                f"unexpected exception: {exc}",
            )
            continue

        try:
            success, reply, total_tokens, error_code = model.call_model(prompt)
        except Exception as exc:
            stats["failed"] += 1
            stats["failures"][_classify_failure_reason(f"unexpected exception: {exc}")] += 1
            model_logger.error(
                "MODEL_FAILURE provider=%s model=%s error_code=%s reason=%s",
                model.provider,
                model.model_name,
                "UNEXPECTED_ERROR",
                f"unexpected exception: {exc}",
            )
            model.consecutive_failures += 1
            if model.consecutive_failures >= 3:
                model.expired = True
                model_logger.error(
                    "MODEL_EXPIRED provider=%s model=%s reason=three consecutive API failures",
                    model.provider,
                    model.model_name,
                )
            if _sleep_or_stop(MODEL_WAIT_SECONDS, stop_event):
                break
            if model.expired:
                break
            continue

        if not success or reply is None:
            stats["failed"] += 1
            stats["failures"][_classify_failure_reason("model call failed or returned no response")] += 1
            model.consecutive_failures += 1
            model_logger.error(
                "MODEL_FAILURE provider=%s model=%s error_code=%s reason=%s",
                model.provider,
                model.model_name,
                error_code or "UNKNOWN_ERROR",
                "model call failed or returned no response",
            )
            if model.consecutive_failures >= 3:
                model.expired = True
                model_logger.error(
                    "MODEL_EXPIRED provider=%s model=%s reason=three consecutive API failures",
                    model.provider,
                    model.model_name,
                )
                break
            if _sleep_or_stop(MODEL_WAIT_SECONDS, stop_event):
                break
            continue

        model.consecutive_failures = 0

        try:
            payload = _parse_valid_json(reply)
            generated_record = json.loads(generated_record_json)
            generated_record["problem"] = payload["problem"]
            generated_record["conversation"] = payload["conversation"]
        except (ValueError, TypeError, KeyError) as exc:
            stats["failed"] += 1
            stats["failures"][_classify_failure_reason(str(exc))] += 1
            try:
                rejected_path = model.write_rejected_response(reply)
                model_logger.info(
                    "REJECTED_RESPONSE_SAVED provider=%s model=%s path=%s",
                    model.provider,
                    model.model_name,
                    rejected_path,
                )
            except OSError as write_exc:
                model_logger.error(
                    "REJECTED_RESPONSE_SAVE_FAILURE provider=%s model=%s reason=%s",
                    model.provider,
                    model.model_name,
                    f"unable to write rejected response: {write_exc}",
                )
            model_logger.error(
                "JSON_INVALID provider=%s model=%s reason=%s",
                model.provider,
                model.model_name,
                str(exc),
            )
            if model.wait_1_min:
                if _sleep_or_stop(MODEL_WAIT_SECONDS, stop_event):
                    break
            continue

        try:
            dataset_path = model.write_response(generated_record)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            stats["failed"] += 1
            stats["failures"][_classify_failure_reason(f"unable to write payload: {exc}")] += 1
            model_logger.error(
                "SAVE_FAILURE provider=%s model=%s reason=%s",
                model.provider,
                model.model_name,
                f"unable to write payload: {exc}",
            )
            if model.wait_1_min:
                if _sleep_or_stop(MODEL_WAIT_SECONDS, stop_event):
                    break
            continue

        print(f"Valid payload for provider={model.provider} model={model.model_name} saved to {dataset_path}")
        turn_count = len(payload.get("conversation", []))
        stats["saved"] += 1
        model_stats = stats["models"][model_key]
        model_stats["saved"] += 1
        model_stats["tokens"].append(int(total_tokens or 0))
        model_stats["turns"].append(turn_count)
        stats["turn_counts"].append(turn_count)
        stats["domains"][generated_record.get("domain", "unknown")] += 1
        for parameter in CONFIG_PARAMETERS:
            value = generated_record.get("config", {}).get(parameter, "unknown")
            stats["config"][parameter][value] += 1
        model_logger.info(
            "SUCCESS provider=%s model=%s total_tokens=%s turns=%s path=%s",
            model.provider,
            model.model_name,
            total_tokens,
            turn_count,
            dataset_path,
        )
        if model.wait_1_min:
            if _sleep_or_stop(MODEL_WAIT_SECONDS, stop_event):
                break

    model_logger.info(_format_model_summary(model, stats))
    return stats
