import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from code.key_manager import KeyManager
from code.model import Model


class KeyManagerRoundRobinTests(unittest.TestCase):
    def _remove_empty_model_dir(self, path):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()

    def _write_models(self, models):
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json")
        json.dump(models, tmp)
        tmp.close()
        for model in models:
            model_dir = Path("data") / (
                f"{model['provider']}_{model['model_name']}".replace("/", "_")
            )
            self.addCleanup(self._remove_empty_model_dir, model_dir)
        return tmp.name

    def test_get_key_round_robin_skips_expired_and_starts_after_last_used(self):
        models = [
            {
                "provider": "P1",
                "api_key": "k1",
                "model_name": "m1",
                "url": "https://example.com/v1",
                "rpd": 2,
                "rpd_left": 2,
                "tpd": 50000,
                "tpd_left": 50000,
                "last_use": None,
                "expired": False,
                "tier": 1,
            },
            {
                "provider": "P2",
                "api_key": "k2",
                "model_name": "m2",
                "url": "https://example.com/v1",
                "rpd": 2,
                "rpd_left": 2,
                "tpd": 50000,
                "tpd_left": 50000,
                "last_use": None,
                "expired": False,
                "tier": 1,
            },
            {
                "provider": "P3",
                "api_key": "k3",
                "model_name": "m3",
                "url": "https://example.com/v1",
                "rpd": 2,
                "rpd_left": 1,
                "tpd": 50000,
                "tpd_left": 50000,
                "last_use": None,
                "expired": True,
                "tier": 1,
            },
        ]

        path = self._write_models(models)
        self.addCleanup(Path(path).unlink, missing_ok=True)

        manager = KeyManager(path)
        manager.last_used_index = 0

        result = manager.get_key()

        self.assertIsNotNone(result)
        self.assertEqual(result[1], 1)
        self.assertEqual(manager.last_used_index, 1)

    def test_get_key_resets_expired_model_after_24_hours(self):
        stale_time = datetime.now(timezone.utc) - timedelta(days=2)
        models = [
            {
                "provider": "P1",
                "api_key": "k1",
                "model_name": "m1",
                "url": "https://example.com/v1",
                "rpd": 2,
                "rpd_left": 1,
                "tpd": 50000,
                "tpd_left": 3000,
                "last_use": stale_time.isoformat(),
                "expired": True,
                "tier": 1,
            }
        ]

        path = self._write_models(models)
        self.addCleanup(Path(path).unlink, missing_ok=True)

        manager = KeyManager(path)
        result = manager.get_key()

        self.assertIsNotNone(result)
        self.assertEqual(result[1], 0)
        self.assertFalse(manager.keys[0].expired)
        self.assertEqual(manager.keys[0].rpd_left, 2)
        self.assertEqual(manager.keys[0].tpd_left, 50000)

    def test_reduce_key_usage_does_not_expire_model_when_quota_is_low(self):
        models = [
            {
                "provider": "P1",
                "api_key": "k1",
                "model_name": "m1",
                "url": "https://example.com/v1",
                "rpd": 2,
                "rpd_left": 2,
                "tpd": 20000,
                "tpd_left": 12000,
                "last_use": None,
                "expired": False,
                "tier": 1,
            }
        ]

        path = self._write_models(models)
        self.addCleanup(Path(path).unlink, missing_ok=True)

        manager = KeyManager(path)
        manager.reduce_key_usage(0, 20000)

        self.assertFalse(manager.keys[0].expired)
        self.assertEqual(manager.keys[0].tpd_left, 0)
        self.assertIsNotNone(manager.keys[0].last_use)

    def test_get_key_skips_active_cooldown_and_releases_expired_cooldown(self):
        now = datetime.now(timezone.utc)
        models = [
            {
                "provider": "P1",
                "api_key": "k1",
                "model_name": "m1",
                "url": "https://example.com/v1",
                "rpd": 2,
                "rpd_left": 1,
                "tpd": 20000,
                "tpd_left": 5000,
                "last_use": now.isoformat(),
                "cooldown_until": (now + timedelta(hours=1)).isoformat(),
                "expired": True,
            },
            {
                "provider": "P2",
                "api_key": "k2",
                "model_name": "m2",
                "url": "https://example.com/v1",
                "rpd": 2,
                "rpd_left": 1,
                "tpd": 20000,
                "tpd_left": 5000,
                "last_use": now.isoformat(),
                "cooldown_until": (now - timedelta(seconds=1)).isoformat(),
                "expired": True,
            },
        ]

        path = self._write_models(models)
        self.addCleanup(Path(path).unlink, missing_ok=True)

        manager = KeyManager(path)
        result = manager.get_key()

        self.assertIsNotNone(result)
        model, index = result
        self.assertEqual(index, 1)
        self.assertIsNone(model.cooldown_until)
        self.assertFalse(model.expired)
        self.assertEqual(model.rpd_left, 1)
        self.assertEqual(model.tpd_left, 5000)

    def test_model_uses_retry_after_and_falls_back_to_24_hours(self):
        class Response:
            def __init__(self, headers):
                self.headers = headers

        class Error:
            def __init__(self, headers):
                self.response = Response(headers)

        model = Model(
            provider="test",
            api_key="key",
            model_name="model",
            url="https://example.com",
            rpd=10,
            rpd_left=10,
            tpd=None,
            tpd_left=None,
            last_use=None,
        )

        before = datetime.now(timezone.utc)
        model._set_rate_limit_cooldown(Error({"retry-after": "60"}))
        self.assertGreaterEqual(
            model.cooldown_until, before + timedelta(seconds=59)
        )
        self.assertLessEqual(
            model.cooldown_until, datetime.now(timezone.utc) + timedelta(seconds=60)
        )

        model._set_rate_limit_cooldown(Error({}))
        self.assertGreaterEqual(
            model.cooldown_until,
            datetime.now(timezone.utc) + timedelta(hours=23, minutes=59),
        )

    def test_model_tracks_dest_path_and_avg_tokens(self):
        model = KeyManager("code/model.json").keys[0]
        self.addCleanup(self._remove_empty_model_dir, Path(model.dest_write_path))

        self.assertEqual(model.dest_write_path, "data/GROQ_openai_gpt-oss-120b")
        self.assertEqual(model.no_of_replies, 5)
        self.assertEqual(model.avg_tokens_used, 4337.6)

        model.record_response(100)
        model.record_response(200)

        self.assertEqual(model.no_of_replies, 7)
        self.assertAlmostEqual(model.avg_tokens_used, 3141.142857142857)


if __name__ == "__main__":
    unittest.main()
