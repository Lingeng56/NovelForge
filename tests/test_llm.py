import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelforge.core.llm import _extract_json


class TestExtractJson(unittest.TestCase):
    def test_extracts_plain_json(self):
        text = '{"a": 1, "b": "x"}'
        self.assertEqual(_extract_json(text), {"a": 1, "b": "x"})

    def test_extracts_json_with_noise(self):
        text = 'prefix text {"a": 2} suffix text'
        self.assertEqual(_extract_json(text), {"a": 2})

    def test_extracts_first_json_object(self):
        text = 'noise {"a": 1} more {"b": 2}'
        self.assertEqual(_extract_json(text), {"a": 1})

    def test_raises_when_no_json(self):
        with self.assertRaises(ValueError):
            _extract_json("no json here")


if __name__ == "__main__":
    unittest.main()
