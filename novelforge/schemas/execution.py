from dataclasses import dataclass
from typing import List


@dataclass
class SceneBeat:
    location: str
    characters: List[str]
    action_description: str
    mood: str
    estimated_word_count: int = 800
