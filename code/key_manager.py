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

# Import Model from model.py, work whether package import or direct script
try:
    from .model import Model  # when used as package (code.key)
except Exception:
    from model import Model  # when executed from the code/ dir directly


class KeyManager:
    def __init__(self, models_path: str = "code/model.json"):
        self.models_path = models_path
        self.keys: List[Model] = []
        self._load_models()

    def _load_models(self) -> None:
        if not os.path.exists(self.models_path):
            raise FileNotFoundError(f"models file not found: {self.models_path}")
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
            model.rpd_left = model.rpd
            model.tpd_left = model.tpd
            model.last_use = None
            return True
        return False

    def get_key(self, min_tpd_needed: int = 1) -> Optional[Tuple[Model, int]]:
        """Return first available (model, index) that can satisfy min_tpd_needed.

        If a key is not currently ok but can be reset (24h passed), reset and use it.
        Returns None if no key is available.
        """
        for idx, m in enumerate(self.keys):
            # If not okay, attempt reset based on last_use
            if not m.ok_to_use:
                if self._maybe_reset(m):
                    # persist reset immediately
                    self._write_models()
            # re-check availability
            if m.rpd_left > 0 and m.tpd_left >= min_tpd_needed:
                return m, idx
        return None

    def reduce_key_usage(self, index: int, tpd_used: int) -> None:
        """Reduce usage counters after a successful API call.

        - rpd_left reduced by 1
        - tpd_left reduced by tpd_used
        - if limits reached, set last_use to now
        - write back to file
        """
        if index < 0 or index >= len(self.keys):
            raise IndexError("key index out of range")
        m = self.keys[index]
        m.rpd_left = max(0, m.rpd_left - 1)
        m.tpd_left = max(0, m.tpd_left - tpd_used)
        if m.rpd_left == 0 or m.tpd_left == 0:
            m.last_use = datetime.now(timezone.utc)
        self._write_models()

    def test_models(self, timeout: int = 10) -> List[Tuple[Model, bool]]:
        """Test all models currently loaded in self.keys.

        Calls each Model.test_connection(timeout) and returns a list of tuples
        (Model, success_bool). Model.test_connection already prints detailed
        provider/model-prefixed messages; this method also prints a concise
        summary line per model.
        """
        results: List[Tuple[Model, bool]] = []
        for m in self.keys:
            try:
                ok = m.test_connection(timeout=timeout)
            except Exception as e:
                # Guard against unexpected Model.test_connection errors
                try:
                    from urllib.parse import urlparse
                    provider = urlparse(m.url or "").netloc or m.url
                except Exception:
                    provider = m.url
                print(f"[provider={provider} model={m.model_name}] Test raised exception: {e}")
                ok = False
            results.append((m, ok))
            status = "SUCCESS" if ok else "FAIL"
            try:
                from urllib.parse import urlparse
                provider = urlparse(m.url or "").netloc or m.url
            except Exception:
                provider = m.url
            print(f"[provider={provider} model={m.model_name}] Test result: {status}")
        return results

    def test_individual_model(self, index: int, timeout: int = 10) -> bool:
        """Test a single model by index. Prints model and provider, then displays reply."""
        if index < 0 or index >= len(self.keys):
            raise IndexError("key index out of range")
        m = self.keys[index]
        try:
            from urllib.parse import urlparse
            provider = urlparse(m.url or "").netloc or m.url
        except Exception:
            provider = m.url
        print(f"Testing model '{m.model_name}' on provider '{provider}'")
        try:
            ok, reply = m.test_connection_with_reply(timeout=timeout)
        except Exception as e:
            print(f"[provider={provider} model={m.model_name}] Test raised exception: {e}")
            return False
        if reply is not None:
            print("Reply:\n" + str(reply))
        status = "SUCCESS" if ok else "FAIL"
        print(f"[provider={provider} model={m.model_name}] Test result: {status}")
        return ok


if __name__ == "__main__":
    # simple interactive runner when executed directly
    mgr = KeyManager(models_path="code/model.json")
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
            mgr.test_individual_model(idx)
        except Exception as e:
            print(f"Error while testing model at index {idx}: {e}")
            continue
