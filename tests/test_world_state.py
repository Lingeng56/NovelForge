import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelforge.schemas.world_state import WorldState


class TestWorldState(unittest.TestCase):
    def test_update_merges_unique(self):
        state = WorldState(
            characters=["林野"],
            locations=["霓虹城"],
            factions=[],
            items=["Compiler模块"],
        )
        update = {
            "characters": ["林野", "陈墨"],
            "locations": ["霓虹城", "旧楼"],
            "factions": ["盘古科技"],
            "items": ["Compiler模块", "源码碎片"],
        }
        state.update_from(update)
        self.assertEqual(state.characters, ["林野", "陈墨"])
        self.assertEqual(state.locations, ["霓虹城", "旧楼"])
        self.assertEqual(state.factions, ["盘古科技"])
        self.assertEqual(state.items, ["Compiler模块", "源码碎片"])


if __name__ == "__main__":
    unittest.main()
