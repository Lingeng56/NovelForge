from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class WorldState:
    characters: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    factions: List[str] = field(default_factory=list)
    items: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, List[str]]:
        return {
            "characters": list(self.characters),
            "locations": list(self.locations),
            "factions": list(self.factions),
            "items": list(self.items),
        }

    def update_from(self, update: Dict[str, List[str]]) -> None:
        self.characters = _merge_unique(self.characters, update.get("characters", []))
        self.locations = _merge_unique(self.locations, update.get("locations", []))
        self.factions = _merge_unique(self.factions, update.get("factions", []))
        self.items = _merge_unique(self.items, update.get("items", []))


def _merge_unique(existing: List[str], incoming: List[str]) -> List[str]:
    seen = {name.strip() for name in existing if name.strip()}
    merged = list(existing)
    for name in incoming:
        normalized = str(name).strip()
        if normalized and normalized not in seen:
            merged.append(normalized)
            seen.add(normalized)
    return merged
