from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..schemas.world_state import WorldState


@dataclass
class ChapterMemory:
    chapter_num: int
    summary: str
    first_paragraph: str
    last_paragraph: str


@dataclass
class NovelMemory:
    novel_id: str
    world_state: WorldState = field(default_factory=WorldState)
    chapters: List[ChapterMemory] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "novel_id": self.novel_id,
            "world_state": self.world_state.to_dict(),
            "chapters": [
                {
                    "chapter_num": c.chapter_num,
                    "summary": c.summary,
                    "first_paragraph": c.first_paragraph,
                    "last_paragraph": c.last_paragraph,
                }
                for c in self.chapters
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "NovelMemory":
        memory = cls(novel_id=str(data.get("novel_id", "default")))
        memory.world_state.update_from(data.get("world_state", {}))
        for raw in data.get("chapters", []):
            memory.chapters.append(
                ChapterMemory(
                    chapter_num=int(raw.get("chapter_num", 0)),
                    summary=str(raw.get("summary", "")),
                    first_paragraph=str(raw.get("first_paragraph", "")),
                    last_paragraph=str(raw.get("last_paragraph", "")),
                )
            )
        return memory

    def get_chapter(self, chapter_num: int) -> Optional[ChapterMemory]:
        for chapter in self.chapters:
            if chapter.chapter_num == chapter_num:
                return chapter
        return None

    def upsert_chapter(self, entry: ChapterMemory) -> None:
        for idx, chapter in enumerate(self.chapters):
            if chapter.chapter_num == entry.chapter_num:
                self.chapters[idx] = entry
                return
        self.chapters.append(entry)


class MemoryStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, novel_id: str) -> Path:
        return self.base_dir / f"novel_{novel_id}.json"

    def load(self, novel_id: str) -> NovelMemory:
        path = self._path(novel_id)
        if not path.exists():
            return NovelMemory(novel_id=novel_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        return NovelMemory.from_dict(data)

    def save(self, memory: NovelMemory) -> None:
        path = self._path(memory.novel_id)
        path.write_text(json.dumps(memory.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _summaries_for_range(memory: NovelMemory, start: int, end: int) -> str:
    lines = []
    for num in range(start, end + 1):
        chapter = memory.get_chapter(num)
        if chapter and chapter.summary:
            lines.append(f"{chapter.chapter_num}. {chapter.summary}")
    return "\n".join(lines)


def build_context(
    memory: NovelMemory,
    chapter_num: int,
    max_recent: int = 3,
    prev_window: int = 3,
    next_window: int = 3,
) -> Dict[str, str]:
    recent = memory.chapters[-max_recent:]
    recent_summaries = "\n".join(
        f"{c.chapter_num}. {c.summary}" for c in recent if c.summary
    )
    prev_start = max(1, chapter_num - prev_window)
    prev_end = chapter_num - 1
    next_start = chapter_num + 1
    next_end = chapter_num + next_window

    prev_summaries = _summaries_for_range(memory, prev_start, prev_end) if prev_end >= prev_start else ""
    next_summaries = _summaries_for_range(memory, next_start, next_end)

    prev_chapter = memory.get_chapter(chapter_num - 1)
    next_chapter = memory.get_chapter(chapter_num + 1)

    return {
        "recent_summaries": recent_summaries,
        "prev_summary": prev_chapter.summary if prev_chapter else "",
        "prev_summaries": prev_summaries,
        "prev_first_paragraph": prev_chapter.first_paragraph if prev_chapter else "",
        "prev_last_paragraph": prev_chapter.last_paragraph if prev_chapter else "",
        "next_summary": next_chapter.summary if next_chapter else "",
        "next_summaries": next_summaries,
        "next_first_paragraph": next_chapter.first_paragraph if next_chapter else "",
        "next_last_paragraph": next_chapter.last_paragraph if next_chapter else "",
        "world_state": json.dumps(memory.world_state.to_dict(), ensure_ascii=False),
    }
