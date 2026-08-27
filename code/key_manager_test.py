"""Utilities for testing the models managed by KeyManager."""
from typing import List, Tuple
from urllib.parse import urlparse

try:
    from .key_manager import KeyManager
    from .model import Model
except Exception:
    from key_manager import KeyManager
    from model import Model


class KeyManagerTest:
    def __init__(self, manager: KeyManager):
        self.manager = manager

    def test_models(self) -> List[Tuple[Model, bool]]:
        """Test all models currently loaded in the manager."""
        results: List[Tuple[Model, bool]] = []
        for model in self.manager.keys:
            try:
                ok, reply, total_tokens = model.test_connection_with_reply()
            except Exception as error:
                try:
                    provider = urlparse(model.url or "").netloc or model.url
                except Exception:
                    provider = model.url
                print(f"[provider={provider} model={model.model_name}] Test raised exception: {error}")
                ok = False
                reply = None
                total_tokens = None
            results.append((model, ok))
            status = "SUCCESS" if ok else "FAIL"
            try:
                provider = urlparse(model.url or "").netloc or model.url
            except Exception:
                provider = model.url
            if reply is not None:
                print("Reply:\n" + str(reply))
                print(f"Tokens used: {total_tokens}")
            print(f"[provider={provider} model={model.model_name}] Test result: {status}")
        return results

    def test_individual_model(self, index: int) -> bool:
        """Test a single model by index."""
        if index < 0 or index >= len(self.manager.keys):
            raise IndexError("key index out of range")
        model = self.manager.keys[index]
        try:
            provider = urlparse(model.url or "").netloc or model.url
        except Exception:
            provider = model.url
        print(f"Testing model '{model.model_name}' on provider '{provider}'")
        try:
            # ok, reply, total_tokens = model.test_connection_with_reply()
            ok, reply, total_tokens = model.test_socratic()
        except Exception as error:
            print(f"[provider={provider} model={model.model_name}] Test raised exception: {error}")
            return False
        if reply is not None:
            print("Reply:\n" + str(reply))
            print(f"Tokens used: {total_tokens}")
        status = "SUCCESS" if ok else "FAIL"
        print(f"[provider={provider} model={model.model_name}] Test result: {status}")
        return ok
