import json
from typing import Dict, List, Tuple

from ..core.llm import call_json
from ..schemas.structure import ChapterNode, NovelOutline, VolumeNode, WorldSetting
from ..schemas.world_state import WorldState


def _normalize_chapters(raw_chapters: List[dict], chapter_offset: int) -> List[ChapterNode]:
    chapters: List[ChapterNode] = []
    for idx, raw in enumerate(raw_chapters[:25], start=1):
        title = str(raw.get("title", "Untitled"))
        summary = str(raw.get("summary", ""))
        chapter_num = chapter_offset + idx
        chapters.append(ChapterNode(chapter_num=chapter_num, title=title, summary=summary))

    while len(chapters) < 25:
        idx = len(chapters) + 1
        chapter_num = chapter_offset + idx
        chapters.append(
            ChapterNode(
                chapter_num=chapter_num,
                title=f"Chapter {chapter_num}",
                summary="Placeholder summary to reach 25 chapters.",
            )
        )
    return chapters


def generate_setting(client, model: str, idea: str) -> Tuple[str, WorldSetting]:
    prompt = (
        "你是网文策划编辑。请基于下面的灵感输出 JSON，包含键："
        "title, power_system, world_rules, main_conflict。每项尽量简洁（不超过50字）。\n\n"
        f"灵感：{idea}\n\n"
        "仅输出 JSON，不要额外文字或推理。"
    )
    data = call_json(client, model, prompt)

    title = str(data.get("title", "Untitled Novel"))
    setting = WorldSetting(
        power_system=str(data.get("power_system", "")),
        world_rules=str(data.get("world_rules", "")),
        main_conflict=str(data.get("main_conflict", "")),
    )
    return title, setting


def plan_volumes(client, model: str, idea: str, setting: WorldSetting) -> List[VolumeNode]:
    prompt = (
        "你是网文策划编辑。为 100 章网文规划 4 卷结构。\n"
        "返回 JSON，键为 volumes，包含 4 个对象，每个对象必须有："
        "volume_num(1-4)、title、core_objective。"
        "title不超过20字，core_objective不超过80字。\n\n"
        f"灵感：{idea}\n"
        "设定摘要：\n"
        f"- power_system：{setting.power_system}\n"
        f"- world_rules：{setting.world_rules}\n"
        f"- main_conflict：{setting.main_conflict}\n\n"
        "仅输出 JSON，不要额外文字或推理。"
    )
    data = call_json(client, model, prompt)
    volumes = []
    for raw in data.get("volumes", [])[:4]:
        volumes.append(
            VolumeNode(
                volume_num=int(raw.get("volume_num", len(volumes) + 1)),
                title=str(raw.get("title", "")),
                core_objective=str(raw.get("core_objective", "")),
                chapters=[],
            )
        )

    while len(volumes) < 4:
        idx = len(volumes) + 1
        volumes.append(VolumeNode(volume_num=idx, title=f"Volume {idx}", core_objective="", chapters=[]))

    return volumes


def _build_previous_context(volumes: List[VolumeNode]) -> str:
    if not volumes:
        return ""
    last_volume = volumes[-1]
    lines = [f"Previous volume {last_volume.volume_num}: {last_volume.title}"]
    for chapter in last_volume.chapters:
        lines.append(f"- {chapter.chapter_num}. {chapter.title}: {chapter.summary}")
    return "\n".join(lines)


def extract_world_updates(
    client,
    model: str,
    world_state: WorldState,
    chapters: List[ChapterNode],
    volume: VolumeNode,
) -> Dict[str, List[str]]:
    summaries = "\n".join(
        f"{c.chapter_num}. {c.title}: {c.summary}" for c in chapters
    )
    prompt = (
        "You are extracting world entities from novel chapter summaries. "
        "Return JSON with keys: characters, locations, factions, items. "
        "Each value is a list of strings. Only include new entities not already listed.\n\n"
        f"Current world state:\n{json.dumps(world_state.to_dict(), ensure_ascii=False)}\n\n"
        f"Volume {volume.volume_num} - {volume.title} summaries:\n{summaries}\n\n"
        "Return JSON only, no extra text. Do not include any reasoning."
    )
    return call_json(client, model, prompt)


def expand_chapters(
    client,
    model: str,
    idea: str,
    volume: VolumeNode,
    setting: WorldSetting,
    world_state: WorldState,
    previous_context: str,
) -> List[ChapterNode]:
    prompt = (
        "你是网文大纲扩写助手。针对指定卷，输出正好25章。\n"
        "返回 JSON，键为 chapters，包含25个对象，每个对象必须有：title、summary（50-80字）。\n\n"
        f"灵感：{idea}\n"
        "设定摘要：\n"
        f"- power_system：{setting.power_system}\n"
        f"- world_rules：{setting.world_rules}\n"
        f"- main_conflict：{setting.main_conflict}\n\n"
        f"已知实体（人物/地点/势力/物品）："
        f"{json.dumps(world_state.to_dict(), ensure_ascii=False)}\n\n"
        f"{previous_context}\n\n"
        f"卷信息：{volume.volume_num} - {volume.title}\n"
        f"本卷目标：{volume.core_objective}\n\n"
        "第25章必须是高潮。仅输出 JSON，不要额外文字或推理。"
    )
    data = call_json(client, model, prompt)
    raw_chapters = data.get("chapters", [])
    chapter_offset = (volume.volume_num - 1) * 25
    return _normalize_chapters(raw_chapters, chapter_offset)


def main_architect(client, model: str, idea: str) -> NovelOutline:
    title, setting = generate_setting(client, model, idea)
    volumes = plan_volumes(client, model, idea, setting)
    world_state = WorldState()

    for idx, volume in enumerate(volumes):
        previous_context = _build_previous_context(volumes[:idx])
        volume.chapters = expand_chapters(
            client,
            model,
            idea,
            volume,
            setting,
            world_state,
            previous_context,
        )
        updates = extract_world_updates(client, model, world_state, volume.chapters, volume)
        world_state.update_from(updates)

    return NovelOutline(title=title, setting=setting, volumes=volumes)


def outline_to_json(outline: NovelOutline) -> str:
    return json.dumps(outline.to_dict(), ensure_ascii=False, indent=2)
