import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelforge.core.cache import load_cached_text, store_cached_text


class TestCache(unittest.TestCase):
    def test_store_and_load_cached_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NOVELFORGE_CACHE_DIR"] = tmp
            os.environ["NOVELFORGE_CACHE"] = "1"
            os.environ.pop("NOVELFORGE_CACHE_BYPASS", None)

            model = "doubao-seed-1-8-251228"
            prompt = "test prompt"
            text = "result text"

            store_cached_text(model, prompt, text)
            cached = load_cached_text(model, prompt)
            self.assertEqual(cached, text)

            # Ensure file exists with expected structure
            files = list(Path(tmp).glob("*.json"))
            self.assertTrue(files)
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["model"], model)
            self.assertEqual(payload["prompt"], prompt)
            self.assertEqual(payload["text"], text)


if __name__ == "__main__":
    unittest.main()
