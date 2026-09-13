"""KeyManager: load models from a JSON file, pick an available key, and decrement usage.

Behavior based on the provided pseudo files:
- Constructor reads a JSON array of model dicts and builds Model objects
- get_key traverses models; if exhausted, tries to reset based on last_use (24h window)
- reduce_key_usage(index, tpd_used) reduces rpd_left by 1 and tpd_left by tpd_used, sets last_use when exhausted, and writes back to JSON

This implementation writes updates back to the same JSON file.
"""
from datetime import datetime, timezone, timedelta
import json
from typing import List, Optional, Tuple
import os
from dotenv import load_dotenv

# Import Model from model.py, work whether package import or direct script
try:
    from .model import Model  # when used as package (code.key)
except Exception:
    from model import Model  # when executed from the code/ dir directly


class KeyManager:
    def __init__(self, models_path: str):
        self.models_path = models_path
        self.keys: List[Model] = []
        self.last_used_index = -1
        self._load_models()

    def _load_models(self) -> None:
        if not os.path.exists(self.models_path):
            raise FileNotFoundError(f"models file not found: {self.models_path}")
        load_dotenv()
        with open(self.models_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.keys = [Model.from_dict(d) for d in data]

    def _write_models(self) -> None:
        with open(self.models_path, "w", encoding="utf-8") as fh:
            json.dump([k.to_dict() for k in self.keys], fh, indent=2)

    def _maybe_reset(self, model: Model) -> bool:
        """Reset counters if last_use is older than 24 hours. Returns True if reset performed."""
        if not model.last_use:
            return False
        now = datetime.now(timezone.utc)
        if now - model.last_use >= timedelta(days=1):
            model.reset_quota()
            return True
        return False

    def _mark_expired_if_needed(self, model: Model) -> bool:
        """Return True when a model is unavailable due to cooldown or expiration."""
        now = datetime.now(timezone.utc)

        if model.cooldown_until is not None:
            if now < model.cooldown_until:
                return True

            model.cooldown_until = None
            model.expired = False
            self._write_models()

        if model.expired:
            if self._maybe_reset(model):
                self._write_models()
                return False
            return True

        return False

    def get_key(self, min_tpd_needed: int = 1) -> Optional[Tuple[Model, int]]:
        """Return the next usable key in round-robin order, skipping expired keys."""
        if not self.keys:
            return None

        start = (self.last_used_index + 1) % len(self.keys)
        for offset in range(len(self.keys)):
            idx = (start + offset) % len(self.keys)
            m = self.keys[idx]

            if self._maybe_reset(m):
                self._write_models()

            if self._mark_expired_if_needed(m):
                continue

            if m.consecutive_failures >= 3:
                continue

            if m.tpd_left is not None and m.tpd_left < min_tpd_needed:
                continue

            if m.rpd_left <= 0:
                continue

            self.last_used_index = idx
            return m, idx

        return None

    def reduce_key_usage(self, index: int, tpd_used: int) -> None:
        """Reduce usage counters after a successful API call and persist them."""
        if index < 0 or index >= len(self.keys):
            raise IndexError("key index out of range")

        m = self.keys[index]
        if m.rpd_left > 0:
            m.rpd_left = max(0, m.rpd_left - 1)

        if m.tpd_left is not None:
            m.tpd_left = max(0, m.tpd_left - max(0, tpd_used))

        m.last_use = datetime.now(timezone.utc)

        self._write_models()

    def increment_model_failure(self, index: int) -> None:
        """Increment consecutive failure counter for a model and persist."""
        if index < 0 or index >= len(self.keys):
            raise IndexError("key index out of range")

        m = self.keys[index]
        m.increment_consecutive_failures()
        self._write_models()

    def reset_model_failure_counter(self, index: int) -> None:
        """Reset consecutive failure counter for a model after successful use and persist."""
        if index < 0 or index >= len(self.keys):
            raise IndexError("key index out of range")

        m = self.keys[index]
        m.reset_consecutive_failures()
        self._write_models()

    def reset_all_failure_counters(self) -> None:
        """Reset consecutive failure counters for all models at the start of iterations."""
        for m in self.keys:
            m.reset_consecutive_failures()
        self._write_models()

if __name__ == "__main__":
    # simple interactive runner when executed directly
    import argparse

    parser = argparse.ArgumentParser(description="Inspect models from a model configuration file.")
    parser.add_argument("models_path", help="Path to the JSON file that lists the models.")
    args = parser.parse_args()
    mgr = KeyManager(models_path=args.models_path)
    try:
        from .key_manager_test import KeyManagerTest
    except Exception:
        from key_manager_test import KeyManagerTest
    tester = KeyManagerTest(mgr)
    total = len(mgr.keys)
    print(f"Loaded {total} model(s). Enter an index to test, or -1 to exit.")

    while True:
        try:
            raw = input(f"Enter index (0-{total-1}) or -1 to exit: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if raw == "":
            continue

        try:
            idx = int(raw)
        except ValueError:
            print("Please enter a valid integer.")
            continue

        if idx == -1:
            print("Exiting.")
            break

        if idx < 0 or idx >= total:
            print(f"Index out of range. Enter between 0 and {total-1}, or -1 to exit.")
            continue

        # call the tester
        try:
            tester.test_individual_model(idx)
        except Exception as e:
            print(f"Error while testing model at index {idx}: {e}")
            continue
