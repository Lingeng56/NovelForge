import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelforge.core.models import load_model_ids


class TestModelParsing(unittest.TestCase):
    def test_parses_model_ids_from_html(self):
        html = (
            '<a href="https://console.volcengine.com/ark/model/detail?Id=doubao-seed-1-8-251228">x</a>'
            ' doubao-seed-1-6-flash-250828 deepseek-v3-1 kimi-k2'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model-list.html"
            path.write_text(html, encoding="utf-8")
            model_ids = load_model_ids(path)

        self.assertIn("doubao-seed-1-8-251228", model_ids)
        self.assertIn("doubao-seed-1-6-flash-250828", model_ids)
        self.assertIn("deepseek-v3-1", model_ids)
        self.assertIn("kimi-k2", model_ids)

    def test_raises_on_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_model_ids(Path("/no/such/file.html"))


if __name__ == "__main__":
    unittest.main()
