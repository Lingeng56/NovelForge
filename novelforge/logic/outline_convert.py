from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..schemas.structure import ChapterNode, NovelOutline, VolumeNode, WorldSetting
from .chapter_split import split_chapters


def _strict_mode() -> bool:
    return os.getenv("NOVELFORGE_STRICT", "").strip().lower() in {"1", "true", "yes"}


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


def _normalize_field_text(value: str) -> str:
    text = value.strip()
    if text.endswith("。"):
        return text
    return text


def _clean_trailing_page_number(text: str) -> str:
    return text.rstrip().rstrip("0123456789").rstrip()


def _parse_three_part_outline(text: str, title: str | None) -> Dict | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    chapter_heading = re.compile(r"^第[\d一二三四五六七八九十百千]+章(?:[:：].*)?$")
    chapter_heading_with_title = re.compile(r"^第[\d一二三四五六七八九十百千]+章[:：].*")
    current = None
    chapters: List[Dict] = []
    current_field = None

    def flush_current():
        nonlocal current
        if not current:
            return
        for key in ("core_plot", "key_interactions", "scene_details"):
            current[key] = _clean_trailing_page_number(current.get(key, "").strip())
        for key, value in list(current.get("extra_sections", {}).items()):
            current["extra_sections"][key] = _clean_trailing_page_number(value.strip())
        current["summary"] = current.get("core_plot", "")
        chapters.append(current)
        current = None

    def _chinese_to_int(value: str) -> int:
        if value.isdigit():
            return int(value)
        nums = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if value == "十":
            return 10
        if value.startswith("十"):
            return 10 + nums.get(value[1:], 0)
        if value.endswith("十"):
            return nums.get(value[0], 0) * 10
        if "十" in value:
            left, right = value.split("十", 1)
            return nums.get(left, 0) * 10 + nums.get(right, 0)
        if value == "百":
            return 100
        if value.endswith("百"):
            return nums.get(value[0], 0) * 100
        if "百" in value:
            left, right = value.split("百", 1)
            base = nums.get(left, 0) * 100
            return base + _chinese_to_int(right)
        return nums.get(value, 0)

    def _extract_chapter_num(line: str, fallback: int) -> int:
        match = re.match(r"^第([\d一二三四五六七八九十百千]+)章", line)
        if not match:
            return fallback
        raw = match.group(1)
        return _chinese_to_int(raw)

    def start_chapter(line: str, idx: int):
        title_part = ""
        if "：" in line:
            title_part = line.split("：", 1)[1].strip()
        elif ":" in line:
            title_part = line.split(":", 1)[1].strip()
        if not title_part:
            title_part = f"第{idx}章"
        return {
            "chapter_num": _extract_chapter_num(line, idx),
            "title": title_part,
            "summary": "",
            "core_plot": "",
            "key_interactions": "",
            "scene_details": "",
            "extra_sections": {},
        }

    idx = 0
    for line in lines:
        if chapter_heading.match(line):
            flush_current()
            idx += 1
            current = start_chapter(line, idx)
            current_field = None
            continue

        if current is None:
            continue

        normalized = line.replace("*", "")
        if "：" in normalized:
            label = normalized.split("：", 1)[0].strip()
        elif ":" in normalized:
            label = normalized.split(":", 1)[0].strip()
        else:
            label = ""
        label = label.replace(" ", "")
        if label and len(label) > 12:
            label = ""
        if label.startswith("核心剧情") or label in {"剧情", "主线", "情节", "事件"}:
            current_field = "core_plot"
            parts = normalized.split("：", 1) if "：" in normalized else normalized.split(":", 1)
            current[current_field] = _normalize_field_text(parts[1] if len(parts) > 1 else "")
            continue
        if label.startswith("关键人物互动") or label in {"人物互动", "人物关系", "互动", "对话"}:
            current_field = "key_interactions"
            parts = normalized.split("：", 1) if "：" in normalized else normalized.split(":", 1)
            current[current_field] = _normalize_field_text(parts[1] if len(parts) > 1 else "")
            continue
        if label.startswith("场景细节") or label in {"场景", "环境", "氛围", "细节"}:
            current_field = "scene_details"
            parts = normalized.split("：", 1) if "：" in normalized else normalized.split(":", 1)
            current[current_field] = _normalize_field_text(parts[1] if len(parts) > 1 else "")
            continue
        if label:
            parts = normalized.split("：", 1) if "：" in normalized else normalized.split(":", 1)
            value = _normalize_field_text(parts[1] if len(parts) > 1 else "")
            current_field = f"extra:{label}"
            current["extra_sections"][label] = value
            continue

        if current_field:
            if current_field.startswith("extra:"):
                key = current_field.split(":", 1)[1]
                current["extra_sections"][key] = (current["extra_sections"].get(key, "") + "\n" + line).strip()
            else:
                current[current_field] = (current[current_field] + "\n" + line).strip()
        else:
            current_field = "core_plot"
            current[current_field] = (current[current_field] + "\n" + line).strip()

    flush_current()
    if not chapters:
        return None

    chapter_map = {c["chapter_num"]: c for c in chapters}
    max_chapter_num = max(chapter_map.keys(), default=0)
    target_max = max(100, max_chapter_num)
    normalized_chapters: List[Dict] = []
    for cnum in range(1, target_max + 1):
        if cnum in chapter_map:
            normalized_chapters.append(chapter_map[cnum])
        else:
            normalized_chapters.append(
                {
                    "chapter_num": cnum,
                    "title": f"第{cnum}章",
                    "summary": "",
                    "core_plot": "",
                    "key_interactions": "",
                    "scene_details": "",
                }
            )

    volumes: List[Dict] = []
    for i in range(0, len(normalized_chapters), 25):
        volume_num = i // 25 + 1
        volumes.append(
            {
                "volume_num": volume_num,
                "title": f"第{volume_num}卷",
                "core_objective": "",
                "chapters": normalized_chapters[i : i + 25],
            }
        )

    return {
        "title": title or "未命名小说",
        "setting": {"power_system": "", "world_rules": "", "main_conflict": ""},
        "volumes": volumes,
    }


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
    try:
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
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_convert] summarize_chunk error: {exc}")
        return {
            "summary": "",
            "characters": [],
            "locations": [],
            "factions": [],
            "items": [],
        }


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
        "5) 每章必须包含：chapter_num、title、core_plot、key_interactions、scene_details。\n"
        "   core_plot：一句话概括核心剧情。\n"
        "   key_interactions：一句话概括关键人物互动。\n"
        "   scene_details：一句话概括场景细节。\n"
        "6) 人名、地名、势力名需要全局去重与统一。\n"
        "7) 只输出 JSON，不要额外文本或标点。\n\n"
        f"小说标题（可选，若为空请自行生成）：{title or ''}\n\n"
        f"实体列表：\n{json.dumps(entities, ensure_ascii=False)}\n\n"
        f"全书摘要：\n{summary_text}\n"
    )
    try:
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
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_convert] generate_outline_from_summaries error: {exc}")
        return {
            "title": title or "未命名小说",
            "setting": {"power_system": "", "world_rules": "", "main_conflict": ""},
            "volumes": [],
        }


def _summarize_chapter_flash(
    client,
    model: str,
    chapter: Dict,
    idx: int,
    prev_title: str = "",
    prev_snippet: str = "",
    next_title: str = "",
    next_snippet: str = "",
) -> Dict:
    title = chapter.get("title", "").strip()
    content = chapter.get("content", "").strip()
    context_block = ""
    if prev_title or next_title:
        context_block = (
            "\n\n相邻章节参考（用于上下文一致性）：\n"
            f"- 上一章标题：{prev_title}\n"
            f"- 上一章片段：{prev_snippet}\n"
            f"- 下一章标题：{next_title}\n"
            f"- 下一章片段：{next_snippet}\n"
        )
    prompt = (
        "你是小说大纲整理助手。请基于以下单章正文输出本章大纲。\n"
        "只输出 JSON，必须包含：chapter_num、title、core_plot、key_interactions、scene_details。\n"
        "如果标题为空，请生成简短章名。\n\n"
        f"章号：{idx}\n"
        f"章名：{title}\n"
        f"正文：\n{content}\n"
        f"{context_block}"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是人工智能助手"},
                {"role": "user", "content": prompt},
            ],
        )
        data = json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_convert] summarize_chapter_flash error: {exc}")
        data = {}
    return {
        "chapter_num": int(data.get("chapter_num", idx)),
        "title": str(data.get("title", title or f"第{idx}章")),
        "core_plot": str(data.get("core_plot", "")).strip(),
        "key_interactions": str(data.get("key_interactions", "")).strip(),
        "scene_details": str(data.get("scene_details", "")).strip(),
    }


def _outline_from_full_novel_parallel(client, model: str, text: str, title: str | None) -> Dict:
    chapters = split_chapters(text)
    if not chapters:
        raise ValueError("无法从正文中分章，请检查文本格式。")

    max_workers = int(os.getenv("NOVELFORGE_CONVERT_WORKERS", "6"))
    results: Dict[int, Dict] = {}
    def _snippet(value: str, limit: int = 200) -> str:
        if not value:
            return ""
        compact = " ".join(value.split())
        return compact[:limit]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _summarize_chapter_flash,
                client,
                model,
                chap,
                idx,
                chapters[idx - 2].get("title", "") if idx > 1 else "",
                _snippet(chapters[idx - 2].get("content", "")) if idx > 1 else "",
                chapters[idx].get("title", "") if idx < len(chapters) else "",
                _snippet(chapters[idx].get("content", "")) if idx < len(chapters) else "",
            ): idx
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
        except Exception as exc:
            if _strict_mode():
                raise
            print(f"[outline_convert] normalize_outline_entities error: {exc}")
    return outline


def _normalize_outline_entities(client, model: str, outline: Dict) -> Dict:
    prompt = (
        "你是大纲清洗助手。请对以下 outline.json 做全局实体统一："
        "人名/地名/势力名如果存在多种写法，统一为一种写法。"
        "保持原有结构与章节编号不变，只做名称统一与轻微文字调整。"
        "输出必须是完整 JSON，不要解释。\n\n"
        f"{json.dumps(outline, ensure_ascii=False)}"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是人工智能助手"},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or ""
        return json.loads(content)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_convert] normalize_outline_entities error: {exc}")
        return outline


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
        "5) 每章必须包含：chapter_num、title、core_plot、key_interactions、scene_details。\n"
        "   core_plot：一句话概括核心剧情。\n"
        "   key_interactions：一句话概括关键人物互动。\n"
        "   scene_details：一句话概括场景细节。\n"
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
        "        {\"chapter_num\": 1, \"title\": \"...\", \"core_plot\": \"...\", \"key_interactions\": \"...\", \"scene_details\": \"...\"}\n"
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
        chunks = []
        try:
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    text_chunk = delta.content
                    chunks.append(text_chunk)
                    if stream_handler:
                        stream_handler(text_chunk)
        except Exception as exc:
            if _strict_mode():
                raise
            print(f"[outline_convert] llm_generate_outline stream error: {exc}")
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
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
        except Exception as exc:
            if _strict_mode():
                raise
            print(f"[outline_convert] llm_generate_outline error: {exc}")
            content = ""
    try:
        return json.loads(content)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_convert] llm_generate_outline json parse error: {exc}")
        return {
            "title": title or "未命名小说",
            "setting": {"power_system": "", "world_rules": "", "main_conflict": ""},
            "volumes": [],
        }


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
            "core_plot": str(ch.get("core_plot", "")).strip(),
            "key_interactions": str(ch.get("key_interactions", "")).strip(),
            "scene_details": str(ch.get("scene_details", "")).strip(),
            "extra_sections": dict(ch.get("extra_sections", {}) or {}),
        }

    normalized_chapters = []
    for cnum in range(1, 101):
        chapter = chapter_map.get(cnum)
        if not chapter:
            chapter = {
                "chapter_num": cnum,
                "title": f"第{cnum}章",
                "summary": "",
                "core_plot": "",
                "key_interactions": "",
                "scene_details": "",
                "extra_sections": {},
            }
        if not chapter.get("core_plot") and chapter.get("summary"):
            chapter["core_plot"] = chapter.get("summary", "")
        chapter.setdefault("extra_sections", {})
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
        "5) 每章必须包含：chapter_num、title、core_plot、key_interactions、scene_details。\n"
        "6) 输出必须是完整 outline.json，且只输出 JSON。\n\n"
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
        chunks = []
        try:
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    text_chunk = delta.content
                    chunks.append(text_chunk)
                    if stream_handler:
                        stream_handler(text_chunk)
        except Exception as exc:
            if _strict_mode():
                raise
            print(f"[outline_convert] rewrite_outline stream error: {exc}")
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
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
        except Exception as exc:
            if _strict_mode():
                raise
            print(f"[outline_convert] rewrite_outline error: {exc}")
            content = ""

    try:
        outline = _extract_json_any(content)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_convert] rewrite_outline json parse error: {exc}")
        outline = {
            "title": "未命名小说",
            "setting": {"power_system": "", "world_rules": "", "main_conflict": ""},
            "volumes": [],
        }
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
        core_plot = str(chapter.get("core_plot", "")).strip()
        if not core_plot and summary:
            core_plot = summary
        volumes[volume_num].chapters.append(
            ChapterNode(
                chapter_num=chapter_num,
                title=title,
                summary=summary,
                core_plot=core_plot,
                key_interactions=str(chapter.get("key_interactions", "")).strip(),
                scene_details=str(chapter.get("scene_details", "")).strip(),
                extra_sections=dict(chapter.get("extra_sections", {}) or {}),
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

    parsed_outline = _parse_three_part_outline(text, title or path.stem)
    if parsed_outline is not None:
        Path(output_path).write_text(
            json.dumps(parsed_outline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        setting_data = parsed_outline.get("setting", {}) or {}
        setting = WorldSetting(
            power_system=str(setting_data.get("power_system", "")),
            world_rules=str(setting_data.get("world_rules", "")),
            main_conflict=str(setting_data.get("main_conflict", "")),
        )
        volumes = []
        for v in parsed_outline.get("volumes", []):
            chapters = []
            for c in v.get("chapters", []):
                summary = str(c.get("summary", ""))
                core_plot = str(c.get("core_plot", ""))
                if not core_plot and summary:
                    core_plot = summary
                chapters.append(
                    ChapterNode(
                        chapter_num=int(c.get("chapter_num", 0)),
                        title=str(c.get("title", "")),
                        summary=summary,
                        core_plot=core_plot,
                        key_interactions=str(c.get("key_interactions", "")),
                        scene_details=str(c.get("scene_details", "")),
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
            title=str(parsed_outline.get("title", title or path.stem)),
            setting=setting,
            volumes=volumes,
        )

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
            summary = str(c.get("summary", ""))
            core_plot = str(c.get("core_plot", ""))
            if not core_plot and summary:
                core_plot = summary
            chapters.append(
                ChapterNode(
                    chapter_num=int(c.get("chapter_num", 0)),
                    title=str(c.get("title", "")),
                    summary=summary,
                    core_plot=core_plot,
                    key_interactions=str(c.get("key_interactions", "")),
                    scene_details=str(c.get("scene_details", "")),
                    extra_sections=dict(c.get("extra_sections", {}) or {}),
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
