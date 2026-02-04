import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelforge.core.config import get_settings


class TestConfig(unittest.TestCase):
    def test_get_settings_validates_models(self):
        html = (
            '<a href="https://console.volcengine.com/ark/model/detail?Id=doubao-seed-1-8-251228">x</a>'
            ' doubao-seed-1-8-251228'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-list.html"
            path.write_text(html, encoding="utf-8")

            env = {
                "ARK_API_KEY": "test-key",
                "NOVELFORGE_MODEL_LIST_FILE": str(path),
                "NOVELFORGE_MODEL_ARCHITECT": "doubao-seed-1-8-251228",
                "NOVELFORGE_MODEL_EDITOR": "doubao-seed-1-8-251228",
                "NOVELFORGE_MODEL_MIMIC": "doubao-seed-1-8-251228",
                "NOVELFORGE_MODEL_GHOSTWRITER": "doubao-seed-1-8-251228",
            }
            with patch_env(env):
                settings = get_settings()

        self.assertEqual(settings.model_architect, "doubao-seed-1-8-251228")
        self.assertTrue(settings.model_list_path.endswith("model-list.html"))

    def test_get_settings_requires_key(self):
        with patch_env({"ARK_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                get_settings()


class patch_env:
    def __init__(self, updates):
        self.updates = updates
        self.originals = {}

    def __enter__(self):
        for key, value in self.updates.items():
            self.originals[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.originals.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
