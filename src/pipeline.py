"""
================================================================================
SOCRATIC AI DATA GENERATION PIPELINE: ORCHESTRATOR & CLI ENTRYPOINT
================================================================================

Role & Purpose:
    This module serves as the primary execution coordinator and CLI entry point
    for the Socratic AI Data Generation Pipeline (Sections 2 and 5.3 of
    PIPELINE_TECHNICAL_REPORT.md). It partitions generation workloads across
    configured models, manages concurrent worker execution with dedicated threads,
    traps interruption signals for clean persistence, and displays the final
    telemetry summary.

Architecture & Stage Breakdown:
    - Stage 1 (Configuration & Setup):
      `load_models()`, `save_models()`, and `normalize_iterations()` load model
      endpoints from models/models.json, resolve provider API keys via .env, and
      evenly partition requested attempt counts across all active models.

    - Stage 3 (Concurrency & Orchestration):
      `run_pipeline()` launches a concurrent ThreadPoolExecutor allocating exactly
      one worker thread per model. A shared threading.Event (`stop_event`) traps
      SIGINT / KeyboardInterrupt signals, allowing running calls to complete or
      safely abort without corrupting JSON shards.

    - Stage 4 (Validation & Sanitization):
      Implemented in `src/validator.py` and re-exported here for compatibility.
      Enforces turn bounds (12-18), strict speaker alternation, tutor reasoning
      and state tags, the Synthesis->Closing ending sequence, and parameter leakage.

    - Stage 6 (Telemetry & Reporting):
      Implemented in `src/telemetry.py` and re-exported here for compatibility.
      Accumulates thread-local counters into run-level statistics, logging detailed
      distribution tables and failure reasons at completion.

Usage:
    Command-line execution:
        python -m src.pipeline models/models.json 50
        python -m src.pipeline models/models.json 10 --data-dir data/test_run
================================================================================
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

try:
    from .model import Model
except ImportError:  # pragma: no cover
    from model import Model

try:
    from .prompt_generator import DatasetPromptGenerator
except ImportError:  # pragma: no cover
    from prompt_generator import DatasetPromptGenerator

# Re-export Stage 4 validation constants and functions for backward compatibility
try:
    from .validator import (
        CONFIG_PARAMETERS,
        FAILURE_CATEGORIES,
        MAX_TURNS,
        MIN_TURNS,
        VALID_STATES,
        _classify_failure_reason,
        _extract_first_json_object,
        _is_valid_conversation_payload,
        _parse_valid_json,
        _strip_wrappers,
        _structural_failure_reason,
        _validate_conversation_payload,
        check_config_leakage,
        validate_conversation_entry,
        validate_dataset,
        validate_file,
    )
except ImportError:  # pragma: no cover
    from validator import (
        CONFIG_PARAMETERS,
        FAILURE_CATEGORIES,
        MAX_TURNS,
        MIN_TURNS,
        VALID_STATES,
        _classify_failure_reason,
        _extract_first_json_object,
        _is_valid_conversation_payload,
        _parse_valid_json,
        _strip_wrappers,
        _structural_failure_reason,
        _validate_conversation_payload,
        check_config_leakage,
        validate_conversation_entry,
        validate_dataset,
        validate_file,
    )

# Re-export Stage 6 telemetry and reporting functions for backward compatibility
try:
    from .telemetry import (
        _configure_logging,
        _configure_model_logging,
        _count_model_conversations,
        _format_duration,
        _format_model_summary,
        _format_run_summary,
        _generator_id,
        _merge_stats,
        _new_stats,
    )
except ImportError:  # pragma: no cover
    from telemetry import (
        _configure_logging,
        _configure_model_logging,
        _count_model_conversations,
        _format_duration,
        _format_model_summary,
        _format_run_summary,
        _generator_id,
        _merge_stats,
        _new_stats,
    )

# Re-export Stage 3 worker loop and pacing helpers for backward compatibility
try:
    from .worker import (
        MODEL_WAIT_SECONDS,
        _run_model_worker,
        _sleep_or_stop,
    )
except ImportError:  # pragma: no cover
    from worker import (
        MODEL_WAIT_SECONDS,
        _run_model_worker,
        _sleep_or_stop,
    )

DEFAULT_ITERATIONS = 50

logger = logging.getLogger("pipeline")
logger.setLevel(logging.INFO)
logger.propagate = False


# ------------------------------------------------------------------------------
# Stage 1: Configuration, Partitioning & Model Persistence
# ------------------------------------------------------------------------------

def load_models(models_path: str) -> List[Model]:
    """Load the configured models from JSON into an array of Model instances."""
    path = Path(models_path)
    if not path.is_file():
        raise FileNotFoundError(f"models file not found: {models_path}")

    load_dotenv()
    with path.open("r", encoding="utf-8") as file:
        model_data = json.load(file)

    if not isinstance(model_data, list):
        raise ValueError("models file must contain a JSON array")

    return [Model.from_dict(entry) for entry in model_data]


def save_models(models_path: str, models: List[Model]) -> None:
    """Persist model runtime state (token counts, failure state) back to disk."""
    path = Path(models_path)
    with path.open("w", encoding="utf-8") as file:
        json.dump([model.to_dict() for model in models], file, indent=2)
        file.write("\n")


def normalize_iterations(iterations: int, model_count: int) -> Tuple[int, int]:
    """Round total iterations up to an even split and return its per-model share."""
    if iterations < 0:
        raise ValueError("iterations must be non-negative")
    if model_count <= 0:
        raise ValueError("model_count must be greater than zero")

    per_model = (iterations + model_count - 1) // model_count
    return per_model * model_count, per_model


# ------------------------------------------------------------------------------
# Stage 3: Top-Level Multi-Threaded Pipeline Orchestration
# ------------------------------------------------------------------------------

def run_pipeline(
    iterations: int = DEFAULT_ITERATIONS,
    models_path: Optional[str] = None,
    data_dir: str = "data",
) -> int:
    """Run an evenly divided total number of attempts across the configured models."""
    load_dotenv()
    _configure_logging(models_path, data_dir)
    if models_path is None:
        raise ValueError("models_path is required")

    models = load_models(models_path)
    run_iterations, iterations_per_model = (
        normalize_iterations(iterations, len(models)) if models else (0, 0)
    )

    if not models:
        logger.error("No models found in %s.", models_path)
        return 0

    output_dir = Path(data_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_count = 0
    stats = _new_stats()
    started_at = time.monotonic()

    def print_summary() -> None:
        for model_stats in stats["models"].values():
            model_stats["total_conversations"] = _count_model_conversations(model_stats["model"])
        summary = _format_run_summary(stats, time.monotonic() - started_at)
        print(summary)
        logger.info(summary)

    stop_event = threading.Event()
    worker_stats_list = [_new_stats() for _ in models]
    for model, ws in zip(models, worker_stats_list):
        model_key = f"{model.provider} / {model.model_name}"
        ws["models"][model_key]["model"] = model

    interrupted = False
    executor = ThreadPoolExecutor(max_workers=len(models))
    try:
        futures = [
            executor.submit(
                _run_model_worker,
                model,
                iterations_per_model,
                _configure_model_logging(model, data_dir, models_path),
                ws,
                stop_event,
            )
            for model, ws in zip(models, worker_stats_list)
        ]
        for future in futures:
            future.result()

    except KeyboardInterrupt:
        interrupted = True
        logger.warning("Interrupted by user. Signalling workers to stop...")
        stop_event.set()
        executor.shutdown(wait=False, cancel_futures=True)
    finally:
        stop_event.set()
        executor.shutdown(wait=False, cancel_futures=True)
        for ws in worker_stats_list:
            _merge_stats(stats, ws)
        save_models(models_path, models)
        saved_count = stats["saved"]
        if interrupted:
            logger.warning(
                "Interrupted by user after %s successful write(s). Exiting cleanly.",
                saved_count,
            )
        print_summary()

    return saved_count


# ------------------------------------------------------------------------------
# CLI Runner
# ------------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Socratic conversation data from available models."
    )
    parser.add_argument(
        "models_path",
        type=str,
        help="Path to the JSON file that lists the models (e.g. models/models.json).",
    )
    parser.add_argument(
        "iterations",
        type=int,
        help="Number of model attempts to run.",
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
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)