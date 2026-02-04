from dataclasses import dataclass, field
from typing import Dict, List


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
    core_plot: str
    key_interactions: str
    scene_details: str
    extra_sections: Dict[str, str] = field(default_factory=dict)


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
                            "core_plot": chapter.core_plot,
                            "key_interactions": chapter.key_interactions,
                            "scene_details": chapter.scene_details,
                            "extra_sections": chapter.extra_sections,
                        }
                        for chapter in volume.chapters
                    ],
                }
                for volume in self.volumes
            ],
        }
