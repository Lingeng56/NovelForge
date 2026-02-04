from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

from openai import OpenAI

from ..core.llm import call_json, call_text
from ..core.memory import ChapterMemory, MemoryStore, NovelMemory, build_context
from ..schemas.execution import SceneBeat
from ..schemas.structure import ChapterNode, NovelOutline, VolumeNode, WorldSetting
from ..schemas.world_state import WorldState


def _strict_mode() -> bool:
    return os.getenv("NOVELFORGE_STRICT", "").strip().lower() in {"1", "true", "yes"}


def _extract_scene_beats(client: OpenAI, model: str, chapter_summary: str) -> List[SceneBeat]:
    prompt = (
        "你是网文分镜规划助手。"
        "返回 JSON，键为 beats，包含6个对象。"
        "每个对象必须包含：location、characters（数组）、action_description、mood、estimated_word_count。\n\n"
        f"章节摘要：{chapter_summary}\n\n"
        "仅输出 JSON，不要额外文字或推理。"
    )
    data = call_json(client, model, prompt)
    beats = []
    for raw in data.get("beats", [])[:6]:
        beats.append(
            SceneBeat(
                location=str(raw.get("location", "")),
                characters=[str(c) for c in raw.get("characters", [])],
                action_description=str(raw.get("action_description", "")),
                mood=str(raw.get("mood", "")),
                estimated_word_count=int(raw.get("estimated_word_count", 800)),
            )
        )
    return beats


def _summarize_chapter(client: OpenAI, model: str, chapter_text: str) -> Dict[str, str]:
    prompt = (
        "请将以下章节内容总结为120-180字。"
        "并原样提取首段与末段。"
        "返回 JSON，键为：summary、first_paragraph、last_paragraph。\n\n"
        f"章节正文：\n{chapter_text}\n\n"
        "仅输出 JSON，不要额外文字或推理。"
    )
    return call_json(client, model, prompt)


def _extract_world_updates(
    client: OpenAI,
    model: str,
    world_state: WorldState,
    chapter_text: str,
) -> Dict[str, List[str]]:
    prompt = (
        "从章节正文中提取新增的实体信息。"
        "返回 JSON，键为：characters、locations、factions、items。"
        "只列出当前世界状态中不存在的新实体。\n\n"
        f"当前世界状态：\n{json.dumps(world_state.to_dict(), ensure_ascii=False)}\n\n"
        f"章节正文：\n{chapter_text}\n\n"
        "仅输出 JSON，不要额外文字或推理。"
    )
    return call_json(client, model, prompt)


def _summary_file(base_dir: Path, novel_id: str) -> Path:
    return base_dir / f"global_summary_{novel_id}.txt"


def _character_state_file(base_dir: Path, novel_id: str) -> Path:
    return base_dir / f"character_state_{novel_id}.txt"


def _load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _save_text(path: Path, text: str) -> None:
    path.write_text(text.strip(), encoding="utf-8")


def _update_global_summary(
    client: OpenAI,
    model: str,
    previous_summary: str,
    chapter_summary: str,
) -> str:
    prompt = (
        "你是网文编辑，请根据本章摘要更新前文摘要。\n"
        "要求：精简、客观，保留关键人物关系、世界设定变化、核心冲突推进。\n\n"
        f"已有前文摘要：\n{previous_summary}\n\n"
        f"本章摘要：\n{chapter_summary}\n\n"
        "请输出更新后的前文摘要，纯文本，不要解释。"
    )
    return call_text(client, model, prompt)


def _update_character_state(
    client: OpenAI,
    model: str,
    previous_state: str,
    chapter_summary: str,
    first_paragraph: str,
    last_paragraph: str,
) -> str:
    prompt = (
        "你是角色状态维护助手，请基于本章内容更新角色状态。\n"
        "要求：只保留主要角色，状态包括身体/心理/关键关系变化；新增人物请添加，淡出人物可移除。\n\n"
        f"已有角色状态：\n{previous_state}\n\n"
        f"本章摘要：\n{chapter_summary}\n\n"
        f"本章首段：\n{first_paragraph}\n\n"
        f"本章末段：\n{last_paragraph}\n\n"
        "请输出更新后的角色状态，纯文本，不要解释。"
    )
    return call_text(client, model, prompt)


def _load_chapter_text(output_dir: str | None, novel_title: str, chapter_num: int) -> str:
    if not output_dir or chapter_num <= 0:
        return ""
    safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in novel_title).strip()
    if not safe_title:
        safe_title = "Untitled"
    path = Path(output_dir) / safe_title / f"chapter-{chapter_num:03d}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


DEFAULT_LEADIN = "你是网文代笔作者。请写完整一章（3000-5000字）。"

DEFAULT_PROMPT_TAIL_SUMMARY = (
    "这是第 {chapter_num} 章，不是第一章。\n"
    "章节标题：{chapter_title}\n"
    "章节摘要：{chapter_summary}\n\n"
    "前文摘要：\n{global_summary}\n\n"
    "角色状态：\n{character_state}\n\n"
    "已知实体：{world_state}\n\n"
    "最近摘要：\n{recent_summaries}\n\n"
    "上一章摘要：\n{prev_summary}\n\n"
    "前三章摘要（若有）：\n{prev_summaries}\n\n"
    "上一章末段：\n{prev_last_paragraph}\n\n"
    "上一章正文（若本地存在）：\n{previous_text}\n\n"
    "下一章摘要（若有）：\n{next_summary}\n\n"
    "后三章摘要（若有）：\n{next_summaries}\n\n"
    "下一章首段（若有）：\n{next_first_paragraph}\n\n"
    "下一章正文（若本地存在）：\n{next_text}\n\n"
    "若有上一章内容，请自然承接，不要像第一章那样重新介绍主角或世界。\n"
    "人物/地点/势力/物品名称尽量不要使用英文，优先使用中文。"
)

DEFAULT_PROMPT_TAIL_THREE_PART = (
    "这是第 {chapter_num} 章，不是第一章。\n"
    "章节标题：{chapter_title}\n"
    "核心剧情：{core_plot}\n"
    "关键人物互动：{key_interactions}\n"
    "场景细节：{scene_details}\n\n"
    "其他要点：\n{extra_sections}\n\n"
    "前文摘要：\n{global_summary}\n\n"
    "角色状态：\n{character_state}\n\n"
    "已知实体：{world_state}\n\n"
    "最近摘要：\n{recent_summaries}\n\n"
    "上一章摘要：\n{prev_summary}\n\n"
    "前三章摘要（若有）：\n{prev_summaries}\n\n"
    "上一章末段：\n{prev_last_paragraph}\n\n"
    "上一章正文（若本地存在）：\n{previous_text}\n\n"
    "下一章摘要（若有）：\n{next_summary}\n\n"
    "后三章摘要（若有）：\n{next_summaries}\n\n"
    "下一章首段（若有）：\n{next_first_paragraph}\n\n"
    "下一章正文（若本地存在）：\n{next_text}\n\n"
    "若有上一章内容，请自然承接，不要像第一章那样重新介绍主角或世界。\n"
    "人物/地点/势力/物品名称尽量不要使用英文，优先使用中文。"
)


def stream_chapter(
    client: OpenAI,
    model: str,
    chapter_node: ChapterNode,
    memory: NovelMemory,
    stream_handler,
    output_dir: str | None,
    novel_title: str,
    global_summary: str,
    character_state: str,
    base_leadin: str = DEFAULT_LEADIN,
    prev_window: int = 3,
    next_window: int = 3,
) -> str:
    context = build_context(
        memory,
        chapter_node.chapter_num,
        prev_window=prev_window,
        next_window=next_window,
    )
    previous_text = _load_chapter_text(output_dir, novel_title, chapter_node.chapter_num - 1)
    next_text = _load_chapter_text(output_dir, novel_title, chapter_node.chapter_num + 1)
    core_plot = chapter_node.core_plot.strip()
    key_interactions = chapter_node.key_interactions.strip()
    scene_details = chapter_node.scene_details.strip()
    extra_sections = ""
    if chapter_node.extra_sections:
        extra_sections = "\n".join(
            f"{k}：{v}" for k, v in chapter_node.extra_sections.items() if v
        )
    use_three_part = any([core_plot, key_interactions, scene_details, extra_sections])
    if use_three_part:
        if not core_plot:
            core_plot = "（未提供，请结合上下文合理补全核心剧情）"
        if not key_interactions:
            key_interactions = "（未提供，请结合上下文合理补全关键人物互动）"
        if not scene_details:
            scene_details = "（未提供，请结合上下文合理补全场景细节）"
        if not extra_sections:
            extra_sections = "（未提供）"
    if use_three_part:
        prompt_tail = DEFAULT_PROMPT_TAIL_THREE_PART
    else:
        prompt_tail = DEFAULT_PROMPT_TAIL_SUMMARY

    prompt = base_leadin.strip() + "\n" + prompt_tail.format(
        chapter_num=chapter_node.chapter_num,
        chapter_title=chapter_node.title,
        chapter_summary=chapter_node.summary,
        core_plot=core_plot,
        key_interactions=key_interactions,
        scene_details=scene_details,
        extra_sections=extra_sections,
        global_summary=global_summary,
        character_state=character_state,
        world_state=context["world_state"],
        recent_summaries=context["recent_summaries"],
        prev_summary=context["prev_summary"],
        prev_summaries=context["prev_summaries"],
        prev_last_paragraph=context["prev_last_paragraph"],
        previous_text=previous_text,
        next_summary=context["next_summary"],
        next_summaries=context["next_summaries"],
        next_first_paragraph=context["next_first_paragraph"],
        next_text=next_text,
    )

    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )

    output = []
    with stream:
        for chunk in stream:
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                text = delta.content
                output.append(text)
                stream_handler(text)

    return "".join(output)


def generate_chapter(
    client: OpenAI,
    model: str,
    chapter_node: ChapterNode,
    memory_store: MemoryStore,
    novel_id: str,
    stream_handler,
    output_dir: str | None,
    novel_title: str,
    base_leadin: str = DEFAULT_LEADIN,
    prev_window: int = 3,
    next_window: int = 3,
) -> Dict[str, str]:
    memory = memory_store.load(novel_id)
    base_dir = memory_store.base_dir
    summary_path = _summary_file(base_dir, novel_id)
    state_path = _character_state_file(base_dir, novel_id)
    global_summary = _load_text(summary_path)
    character_state = _load_text(state_path)

    chapter_text = ""
    try:
        chapter_text = stream_chapter(
            client,
            model,
            chapter_node,
            memory,
            stream_handler,
            output_dir,
            novel_title,
            global_summary,
            character_state,
            base_leadin=base_leadin,
            prev_window=prev_window,
            next_window=next_window,
        )
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[ghostwriter] stream_chapter error: {exc}")
        chapter_text = ""

    summary = ""
    first_paragraph = ""
    last_paragraph = ""
    try:
        summary_data = _summarize_chapter(client, model, chapter_text)
        summary = str(summary_data.get("summary", ""))
        first_paragraph = str(summary_data.get("first_paragraph", ""))
        last_paragraph = str(summary_data.get("last_paragraph", ""))
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[ghostwriter] summarize_chapter error: {exc}")
        summary = ""
        first_paragraph = ""
        last_paragraph = ""

    try:
        if summary:
            global_summary = _update_global_summary(client, model, global_summary, summary)
            _save_text(summary_path, global_summary)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[ghostwriter] update_global_summary error: {exc}")
        pass

    try:
        character_state = _update_character_state(
            client, model, character_state, summary, first_paragraph, last_paragraph
        )
        _save_text(state_path, character_state)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[ghostwriter] update_character_state error: {exc}")
        pass

    try:
        updates = _extract_world_updates(client, model, memory.world_state, chapter_text)
        memory.world_state.update_from(updates)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[ghostwriter] extract_world_updates error: {exc}")
        pass

    try:
        memory.upsert_chapter(
            ChapterMemory(
                chapter_num=chapter_node.chapter_num,
                summary=summary,
                first_paragraph=first_paragraph,
                last_paragraph=last_paragraph,
            )
        )
        memory_store.save(memory)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[ghostwriter] memory_save error: {exc}")
        pass
    return {
        "chapter_text": chapter_text,
        "summary": summary,
        "first_paragraph": first_paragraph,
        "last_paragraph": last_paragraph,
    }


def load_outline(path: str) -> NovelOutline:
    data = json.loads(open(path, "r", encoding="utf-8").read())
    setting_data = data.get("setting", {}) or {}
    setting = WorldSetting(
        power_system=str(setting_data.get("power_system", "")),
        world_rules=str(setting_data.get("world_rules", "")),
        main_conflict=str(setting_data.get("main_conflict", "")),
    )
    outline = NovelOutline(
        title=data.get("title", ""),
        setting=setting,
        volumes=[],
    )
    for v in data.get("volumes", []):
        chapters = []
        for c in v.get("chapters", []):
                chapters.append(
                    ChapterNode(
                        chapter_num=int(c.get("chapter_num", 0)),
                        title=str(c.get("title", "")),
                        summary=str(c.get("summary", "")),
                        core_plot=str(c.get("core_plot", "")),
                        key_interactions=str(c.get("key_interactions", "")),
                        scene_details=str(c.get("scene_details", "")),
                        extra_sections=dict(c.get("extra_sections", {}) or {}),
                    )
                )
        outline.volumes.append(
            VolumeNode(
                volume_num=int(v.get("volume_num", 0)),
                title=str(v.get("title", "")),
                core_objective=str(v.get("core_objective", "")),
                chapters=chapters,
            )
        )
    return outline
