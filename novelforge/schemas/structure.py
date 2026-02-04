from dataclasses import dataclass
from typing import List


@dataclass
class WorldSetting:
    power_system: str
    world_rules: str
    main_conflict: str


@dataclass
class ChapterNode:
    chapter_num: int
    title: str
    summary: str


@dataclass
class VolumeNode:
    volume_num: int
    title: str
    core_objective: str
    chapters: List[ChapterNode]


@dataclass
class NovelOutline:
    title: str
    setting: WorldSetting
    volumes: List[VolumeNode]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "setting": {
                "power_system": self.setting.power_system,
                "world_rules": self.setting.world_rules,
                "main_conflict": self.setting.main_conflict,
            },
            "volumes": [
                {
                    "volume_num": volume.volume_num,
                    "title": volume.title,
                    "core_objective": volume.core_objective,
                    "chapters": [
                        {
                            "chapter_num": chapter.chapter_num,
                            "title": chapter.title,
                            "summary": chapter.summary,
                        }
                        for chapter in volume.chapters
                    ],
                }
                for volume in self.volumes
            ],
        }
