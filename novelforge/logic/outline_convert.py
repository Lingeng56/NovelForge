from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..schemas.structure import ChapterNode, NovelOutline, VolumeNode, WorldSetting
from .chapter_split import split_chapters


def _read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        xml = zf.read("word/document.xml")
    tree = ElementTree.fromstring(xml)
    # WordprocessingML namespace
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for p in tree.findall(".//w:p", ns):
        texts = []
        for t in p.findall(".//w:t", ns):
            if t.text:
                texts.append(t.text)
        paragraph = "".join(texts).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return "\n".join(paragraphs)


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _read_docx_text(path)
    return path.read_text(encoding="utf-8").strip()


def _chunk_text(text: str, chunk_size: int = 100000, overlap_ratio: float = 0.1) -> List[str]:
    if not text:
        return []
    overlap = int(chunk_size * overlap_ratio)
    if overlap < 0:
        overlap = 0
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _summarize_chunk(client, model: str, chunk: str, idx: int, total: int) -> Dict:
    prompt = (
        "你是小说内容压缩助手。请将以下小说片段压缩为结构化摘要，"
        "用于后续生成全书大纲。\n\n"
        "要求输出 JSON，包含键：summary、characters、locations、factions、items。\n"
        "summary：200-400字概括本片段核心剧情与人物关系。\n"
        "characters/locations/factions/items：只列出本片段出现的重要实体。\n"
        "只输出 JSON，不要解释。\n\n"
        f"片段（{idx}/{total}）：\n{chunk}\n"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是人工智能助手"},
            {"role": "user", "content": prompt},
        ],
        reasoning_effort="high",
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def _merge_entities(entity_lists: List[List[str]]) -> List[str]:
    seen = set()
    merged = []
    for items in entity_lists:
        for item in items or []:
            name = str(item).strip()
            if name and name not in seen:
                merged.append(name)
                seen.add(name)
    return merged


def _llm_generate_outline_from_summaries(
    client,
    model: str,
    summaries: List[str],
    entities: Dict[str, List[str]],
    title: str | None,
) -> Dict:
    summary_text = "\n\n".join(summaries)
    prompt = (
        "你是小说大纲整理专家。请基于以下全书摘要与实体列表，"
        "端到端生成严格符合 outline.json 的 JSON（不要解释）。\n\n"
        "要求：\n"
        "1) 输出必须包含：title、setting、volumes。\n"
        "2) setting 必须包含：power_system、world_rules、main_conflict（若无则合理补全）。\n"
        "3) volumes 为数组；必须生成 1-100 章；按每25章划分为1卷。\n"
        "4) 每卷必须包含：volume_num、title、core_objective、chapters。\n"
        "5) 每章必须包含：chapter_num、title、summary；summary 为一句话概括。\n"
        "6) 人名、地名、势力名需要全局去重与统一。\n"
        "7) 只输出 JSON，不要额外文本或标点。\n\n"
        f"小说标题（可选，若为空请自行生成）：{title or ''}\n\n"
        f"实体列表：\n{json.dumps(entities, ensure_ascii=False)}\n\n"
        f"全书摘要：\n{summary_text}\n"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是人工智能助手"},
            {"role": "user", "content": prompt},
        ],
        reasoning_effort="high",
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def _summarize_chapter_flash(client, model: str, chapter: Dict, idx: int) -> Dict:
    title = chapter.get("title", "").strip()
    content = chapter.get("content", "").strip()
    prompt = (
        "你是小说大纲整理助手。请基于以下单章正文输出本章大纲。\n"
        "只输出 JSON，必须包含：chapter_num、title、summary。\n"
        "如果标题为空，请生成简短章名；summary 用一句话概括。\n\n"
        f"章号：{idx}\n"
        f"章名：{title}\n"
        f"正文：\n{content}\n"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是人工智能助手"},
            {"role": "user", "content": prompt},
        ],
    )
    data = json.loads(response.choices[0].message.content or "{}")
    return {
        "chapter_num": int(data.get("chapter_num", idx)),
        "title": str(data.get("title", title or f"第{idx}章")),
        "summary": str(data.get("summary", "")).strip(),
    }


def _outline_from_full_novel_parallel(client, model: str, text: str, title: str | None) -> Dict:
    chapters = split_chapters(text)
    if not chapters:
        raise ValueError("无法从正文中分章，请检查文本格式。")

    max_workers = int(os.getenv("NOVELFORGE_CONVERT_WORKERS", "6"))
    results: Dict[int, Dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_summarize_chapter_flash, client, model, chap, idx): idx
            for idx, chap in enumerate(chapters, start=1)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()

    chapters_out = [results[i] for i in sorted(results.keys())]

    volumes: List[Dict] = []
    for i in range(0, len(chapters_out), 25):
        volume_num = i // 25 + 1
        volumes.append(
            {
                "volume_num": volume_num,
                "title": f"第{volume_num}卷",
                "core_objective": "",
                "chapters": chapters_out[i : i + 25],
            }
        )

    outline = {
        "title": title or "未命名小说",
        "setting": {
            "power_system": "",
            "world_rules": "",
            "main_conflict": "",
        },
        "volumes": volumes,
    }
    if os.getenv("NOVELFORGE_CONVERT_NORMALIZE", "1").strip() in {"1", "true", "yes"}:
        try:
            outline = _normalize_outline_entities(client, model, outline)
        except Exception:
            pass
    return outline


def _normalize_outline_entities(client, model: str, outline: Dict) -> Dict:
    prompt = (
        "你是大纲清洗助手。请对以下 outline.json 做全局实体统一："
        "人名/地名/势力名如果存在多种写法，统一为一种写法。"
        "保持原有结构与章节编号不变，只做名称统一与轻微文字调整。"
        "输出必须是完整 JSON，不要解释。\n\n"
        f"{json.dumps(outline, ensure_ascii=False)}"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是人工智能助手"},
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    return json.loads(content)


def _llm_generate_outline(
    client,
    model: str,
    text: str,
    title: str | None,
    mode: str = "outline",
    reasoning_effort: str = "high",
    stream_output: bool = False,
    stream_handler=None,
    max_tokens: int | None = None,
) -> Dict:
    if mode == "novel":
        return _outline_from_full_novel_parallel(client, model, text, title)

    source_desc = "完整章纲文本"
    prompt = (
        "你是小说大纲整理专家。请基于下面提供的内容，"
        "端到端生成严格符合 outline.json 的 JSON（不要解释）。\n\n"
        "要求：\n"
        "1) 输出必须包含：title、setting、volumes。\n"
        "2) setting 必须包含：power_system、world_rules、main_conflict（若无则合理补全）。\n"
        "3) volumes 为数组；如果原文没有明确分卷，请按每25章划分为1卷。\n"
        "4) 每卷必须包含：volume_num、title、core_objective、chapters。\n"
        "5) 每章必须包含：chapter_num、title、summary；summary 为一句话概括。\n"
        "6) 人名、地名、势力名需要全局去重与统一。\n"
        "7) 只输出 JSON，不要额外文本或标点。\n\n"
        "输出格式示例：\n"
        "{\n"
        "  \"title\": \"示例小说\",\n"
        "  \"setting\": {\"power_system\":\"...\",\"world_rules\":\"...\",\"main_conflict\":\"...\"},\n"
        "  \"volumes\": [\n"
        "    {\n"
        "      \"volume_num\": 1,\n"
        "      \"title\": \"第一卷\",\n"
        "      \"core_objective\": \"...\",\n"
        "      \"chapters\": [\n"
        "        {\"chapter_num\": 1, \"title\": \"...\", \"summary\": \"...\"}\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"小说标题（可选，若为空请自行生成）：{title or ''}\n\n"
        f"{source_desc}：\n{text}\n\n"
        "仅输出 JSON。"
    )
    if stream_output:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是人工智能助手"},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "reasoning_effort": reasoning_effort,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        stream = client.chat.completions.create(**kwargs)
        chunks = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                text_chunk = delta.content
                chunks.append(text_chunk)
                if stream_handler:
                    stream_handler(text_chunk)
        content = "".join(chunks)
    else:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是人工智能助手"},
                {"role": "user", "content": prompt},
            ],
            "reasoning_effort": reasoning_effort,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
    return json.loads(content)


def _extract_json_any(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = text.find(open_char)
        while start != -1:
            depth = 0
            for i in range(start, len(text)):
                ch = text[i]
                if ch == open_char:
                    depth += 1
                elif ch == close_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            start = text.find(open_char, start + 1)

    raise ValueError("No JSON object or array found in model output.")


def _normalize_outline_100(outline: Dict) -> Dict:
    setting = outline.get("setting") or {}
    setting.setdefault("power_system", "")
    setting.setdefault("world_rules", "")
    setting.setdefault("main_conflict", "")

    volumes = outline.get("volumes") or []
    chapter_map: Dict[int, Dict] = {}
    volume_titles: Dict[int, str] = {}
    volume_objectives: Dict[int, str] = {}

    for vol in volumes:
        vnum = int(vol.get("volume_num", 0) or 0)
        if vnum:
            volume_titles[vnum] = str(vol.get("title", "")) or f"第{vnum}卷"
            volume_objectives[vnum] = str(vol.get("core_objective", "")) or ""
        for ch in vol.get("chapters", []) or []:
            try:
                cnum = int(ch.get("chapter_num", 0) or 0)
            except (TypeError, ValueError):
                continue
            if cnum <= 0 or cnum > 100:
                continue
            chapter_map[cnum] = {
                "chapter_num": cnum,
                "title": str(ch.get("title", "") or f"第{cnum}章"),
                "summary": str(ch.get("summary", "")).strip(),
            }

    normalized_chapters = []
    for cnum in range(1, 101):
        chapter = chapter_map.get(cnum)
        if not chapter:
            chapter = {
                "chapter_num": cnum,
                "title": f"第{cnum}章",
                "summary": "",
            }
        normalized_chapters.append(chapter)

    normalized_volumes: List[Dict] = []
    for i in range(0, 100, 25):
        volume_num = i // 25 + 1
        normalized_volumes.append(
            {
                "volume_num": volume_num,
                "title": volume_titles.get(volume_num, f"第{volume_num}卷"),
                "core_objective": volume_objectives.get(volume_num, ""),
                "chapters": normalized_chapters[i : i + 25],
            }
        )

    return {
        "title": outline.get("title") or "未命名小说",
        "setting": setting,
        "volumes": normalized_volumes,
    }


def rewrite_outline(
    input_path: str,
    output_path: str,
    client,
    model: str = "doubao-seed-1-8-251228",
    reasoning_effort: str = "high",
    stream_output: bool = False,
    stream_handler=None,
    max_tokens: int | None = None,
) -> Dict:
    print('Rewriting Outline...')
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    prompt = (
        "你是小说大纲重写助手。请对以下 outline.json 做重写：\n"
        "1) 保持剧情主线与关键事件大致不变。\n"
        "2) 所有人名、地名、势力名必须更换为新的中文名称，且全局统一。\n"
        "3) 规整为 1-100 章，编号连续。\n"
        "4) 共 4 卷，每卷 25 章。\n"
        "5) 输出必须是完整 outline.json，且只输出 JSON。\n\n"
        f"{json.dumps(data, ensure_ascii=False)}"
    )
    if stream_output:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是人工智能助手"},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "reasoning_effort": reasoning_effort,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        stream = client.chat.completions.create(**kwargs)
        chunks = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                text_chunk = delta.content
                chunks.append(text_chunk)
                if stream_handler:
                    stream_handler(text_chunk)
        content = "".join(chunks)
    else:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是人工智能助手"},
                {"role": "user", "content": prompt},
            ],
            "reasoning_effort": reasoning_effort,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""

    outline = _extract_json_any(content)
    outline = _normalize_outline_100(outline)
    Path(output_path).write_text(
        json.dumps(outline, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return outline


def _assign_volumes(chapters: List[Dict], volume_titles: Dict[int, str]) -> List[VolumeNode]:
    volumes: Dict[int, VolumeNode] = {}

    has_explicit_volume = any(ch.get("volume_num") for ch in chapters) or bool(volume_titles)

    def get_volume_num(chapter_num: int, explicit: int | None) -> int:
        if explicit:
            return explicit
        if not has_explicit_volume:
            return 1
        return ((chapter_num - 1) // 25) + 1

    for chapter in chapters:
        chapter_num = int(chapter["chapter_num"])
        volume_num = get_volume_num(chapter_num, chapter.get("volume_num"))
        if volume_num not in volumes:
            title = volume_titles.get(volume_num, f"第{volume_num}卷")
            volumes[volume_num] = VolumeNode(
                volume_num=volume_num,
                title=title,
                core_objective="",
                chapters=[],
            )
        title = str(chapter.get("title", "")).strip() or f"第{chapter_num}章"
        summary = str(chapter.get("summary", "")).strip()
        volumes[volume_num].chapters.append(
            ChapterNode(
                chapter_num=chapter_num,
                title=title,
                summary=summary,
            )
        )

    return [volumes[k] for k in sorted(volumes.keys())]


def convert_outline(
    input_path: str,
    output_path: str,
    title: str | None = None,
    client=None,
    model: str | None = None,
    mode: str = "outline",
    reasoning_effort: str = "high",
    stream_output: bool = False,
    stream_handler=None,
    max_tokens: int | None = None,
) -> NovelOutline:
    path = Path(input_path)
    text = _read_text(path)

    if client is None or model is None:
        raise ValueError("LLM client/model required for outline conversion.")

    outline_data = _llm_generate_outline(
        client,
        model,
        text,
        title,
        mode=mode,
        reasoning_effort=reasoning_effort,
        stream_output=stream_output,
        stream_handler=stream_handler,
        max_tokens=max_tokens,
    )

    Path(output_path).write_text(
        json.dumps(outline_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    setting_data = outline_data.get("setting", {}) or {}
    setting = WorldSetting(
        power_system=str(setting_data.get("power_system", "")),
        world_rules=str(setting_data.get("world_rules", "")),
        main_conflict=str(setting_data.get("main_conflict", "")),
    )
    volumes = []
    for v in outline_data.get("volumes", []):
        chapters = []
        for c in v.get("chapters", []):
            chapters.append(
                ChapterNode(
                    chapter_num=int(c.get("chapter_num", 0)),
                    title=str(c.get("title", "")),
                    summary=str(c.get("summary", "")),
                )
            )
        volumes.append(
            VolumeNode(
                volume_num=int(v.get("volume_num", 0)),
                title=str(v.get("title", "")),
                core_objective=str(v.get("core_objective", "")),
                chapters=chapters,
            )
        )

    return NovelOutline(
        title=str(outline_data.get("title", title or path.stem)),
        setting=setting,
        volumes=volumes,
    )
