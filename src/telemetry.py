"""
================================================================================
STAGE 6: AUDITING, TELEMETRY, LOGGING & STATISTICAL REPORTING
================================================================================

Role & Purpose:
    This module manages runtime quota telemetry, thread-safe statistics
    accumulation, structured per-model file logging, and generation of
    plain-text summary reports as specified in Section 2 (Stage 6) of
    PIPELINE_TECHNICAL_REPORT.md.

What happens in this file:
    1. Generator Identification:
       - Reads DATASET_GENERATOR_ID from environment to name log files and
         differentiate parallel worker nodes.

    2. Logging Infrastructure:
       - `_configure_logging`: Configures central stdout stream and run summary
         log file under `<data_dir>/logs/<worker_id>.<models_file>.log`.
       - `_configure_model_logging`: Sets up dedicated, non-propagating file
         loggers for each model: `<data_dir>/logs/<worker_id>.<models_file>.<model_stem>.log`.

    3. Local & Global Statistics Lifecycle:
       - `_new_stats`: Factory for an isolated statistics dictionary owned by a
         single model worker thread (prevents lock contention during generation).
       - `_merge_stats`: Combines all completed worker statistics into the global
         run statistics dictionary after execution completes or on interrupt.

    4. Reporting & Formatting:
       - `_format_duration`: Converts execution seconds into 'HH:MM:SS'.
       - `_format_model_summary`: Generates per-model final summary with
         acceptance rate, average tokens, turn distribution, and failure counts.
       - `_format_run_summary`: Builds the end-of-run tabular report covering
         global totals, per-model metrics, failure category frequencies,
         turn count statistics, domain coverage, and parameter balance.

    5. Disk Inspection:
       - `_count_model_conversations`: Scans stored shard JSON files
         (`*-set[*.json`) to tally historical conversations saved on disk.
================================================================================
"""

from collections import Counter, defaultdict
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from .validator import CONFIG_PARAMETERS, FAILURE_CATEGORIES
except ImportError:  # pragma: no cover
    from validator import CONFIG_PARAMETERS, FAILURE_CATEGORIES

logger = logging.getLogger("pipeline")
logger.setLevel(logging.INFO)
logger.propagate = False


def _generator_id() -> str:
    """Return the worker identifier used to distinguish generated files and logs."""
    return os.getenv("DATASET_GENERATOR_ID", "unknown").strip() or "unknown"


def _configure_logging(models_path: Optional[str], data_dir: str) -> None:
    """Configure the central run summary log with generator identifier and models config filename."""
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    models_tag = f".{Path(models_path).name}" if models_path else ""
    log_path = log_dir / f"{_generator_id()}{models_tag}.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == log_path.resolve()
        for handler in logger.handlers
    ):
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if not any(
        type(handler) is logging.StreamHandler for handler in logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)


def _configure_model_logging(
    model: Any,
    data_dir: str,
    models_path: Optional[str] = None,
) -> logging.Logger:
    """Configure a file logger dedicated to one model, generator, and models config file."""
    models_tag = f".{Path(models_path).name}" if models_path else ""
    model_logger = logging.getLogger(
        f"pipeline.model.{_generator_id()}{models_tag}.{model.provider}.{model.model_name}"
    )
    model_logger.setLevel(logging.INFO)
    model_logger.propagate = False

    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    model_stem = f"{model.provider}_{model.model_name}".replace("/", "_").replace(":", "_")
    log_path = log_dir / f"{_generator_id()}{models_tag}.{model_stem}.log"

    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == log_path.resolve()
        for handler in model_logger.handlers
    ):
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        model_logger.addHandler(file_handler)

    return model_logger


def _new_stats() -> Dict[str, Any]:
    """Create an empty statistics object for a worker or combined run."""
    return {
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


def _merge_stats(combined: Dict[str, Any], worker_stats: Dict[str, Any]) -> None:
    """Merge one worker's local statistics into the main-process totals."""
    combined["attempts"] += worker_stats["attempts"]
    combined["saved"] += worker_stats["saved"]
    combined["failed"] += worker_stats["failed"]
    combined["failures"].update(worker_stats["failures"])
    combined["turn_counts"].extend(worker_stats["turn_counts"])
    combined["domains"].update(worker_stats["domains"])
    for parameter, values in worker_stats["config"].items():
        combined["config"][parameter].update(values)

    for model_key, worker_model_stats in worker_stats["models"].items():
        model_stats = combined["models"][model_key]
        model_stats["attempts"] += worker_model_stats["attempts"]
        model_stats["saved"] += worker_model_stats["saved"]
        model_stats["tokens"].extend(worker_model_stats["tokens"])
        model_stats["turns"].extend(worker_model_stats["turns"])
        model_stats["total_conversations"] = worker_model_stats["total_conversations"]
        model_stats["model"] = worker_model_stats["model"]


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


def _format_duration(duration_seconds: float) -> str:
    """Format elapsed time as zero-padded hours, minutes, and seconds."""
    total_seconds = max(0, int(duration_seconds))
    hours, remaining_seconds = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remaining_seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_model_summary(model: Any, stats: Dict[str, Any]) -> str:
    """Build the completion summary written to an individual model log."""
    model_key = f"{model.provider} / {model.model_name}"
    model_stats = stats["models"][model_key]
    attempts = model_stats["attempts"]
    saved = model_stats["saved"]
    accept_rate = saved / attempts * 100 if attempts else 0.0
    tokens = model_stats["tokens"]
    turns = model_stats["turns"]
    turn_range = f"{min(turns)}-{max(turns)}" if turns else "-"
    failure_lines = [
        f"  {category}: {stats['failures'][category]}"
        for category in FAILURE_CATEGORIES
        if stats["failures"][category]
    ]
    failures = "\n".join(failure_lines) if failure_lines else "  none"
    return "\n".join([
        "MODEL_SUMMARY",
        f"  provider_model: {model_key}",
        f"  attempts: {attempts}",
        f"  saved: {saved}",
        f"  failed: {stats['failed']}",
        f"  accept_rate: {accept_rate:.2f}%",
        f"  average_tokens: {sum(tokens) / len(tokens) if tokens else 0.0:.2f}",
        f"  turn_range: {turn_range}",
        f"  expired: {model.expired}",
        "  failure_categories:",
        failures,
    ])


def _format_run_summary(stats: Dict[str, Any], duration_seconds: float) -> str:
    """Build the plain-text end-of-run summary report."""
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
        f"  Duration (h:m:s): {_format_duration(duration_seconds)}",
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
