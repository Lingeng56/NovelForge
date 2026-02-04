from __future__ import annotations

import json
import os
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..logic.chapter_split import split_chapters
from tqdm import tqdm


def _strict_mode() -> bool:
    return os.getenv("NOVELFORGE_STRICT", "").strip().lower() in {"1", "true", "yes"}


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


def _normalize_outline_payload(payload, idx: int, title: str) -> Dict:
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        payload = {}
    return {
        "chapter_num": int(payload.get("chapter_num", idx)),
        "title": str(payload.get("title", title or f"第{idx}章")),
        "core_plot": str(payload.get("core_plot", "")).strip(),
        "key_interactions": str(payload.get("key_interactions", "")).strip(),
        "scene_details": str(payload.get("scene_details", "")).strip(),
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
        data = _extract_json_any(response.choices[0].message.content or "{}")
        return _normalize_outline_payload(data, idx, title)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_pipeline] chapter {idx} failed: {exc}")
        return {
            "chapter_num": idx,
            "title": title or f"第{idx}章",
            "core_plot": "",
            "key_interactions": "",
            "scene_details": "",
        }


def _mp_worker(args: Dict) -> Dict:
    from openai import OpenAI

    api_key = os.getenv("ARK_API_KEY")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    model = args["model"]
    idx = args["idx"]
    title = args["title"]
    content = args["content"]
    if not api_key:
        if _strict_mode():
            raise RuntimeError("ARK_API_KEY is not set.")
        return {
            "chapter_num": idx,
            "title": title or f"第{idx}章",
            "core_plot": "",
            "key_interactions": "",
            "scene_details": "",
        }

    client = OpenAI(base_url=base_url, api_key=api_key)
    prev_title = args.get("prev_title", "")
    prev_snippet = args.get("prev_snippet", "")
    next_title = args.get("next_title", "")
    next_snippet = args.get("next_snippet", "")
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
        data = _extract_json_any(response.choices[0].message.content or "{}")
        return _normalize_outline_payload(data, idx, title)
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_pipeline] mp_worker error: {exc}")
        return {
            "chapter_num": idx,
            "title": title or f"第{idx}章",
            "core_plot": "",
            "key_interactions": "",
            "scene_details": "",
        }


def split_full_novel(text: str, limit: int = 100) -> List[Dict]:
    return split_chapters(text, limit=limit)

def _snippet(text: str, limit: int = 200) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    return compact[:limit]


def summarize_chapters_parallel(client, model: str, chapters: List[Dict], max_workers: int = 6) -> List[Dict]:
    results: Dict[int, Dict] = {}
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
    return [results[i] for i in sorted(results.keys())]


def summarize_chapters_parallel_mp(
    model: str,
    chapters: List[Dict],
    max_workers: int = 6,
    progress_callback=None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> List[Dict]:
    tasks = []
    for idx, chap in enumerate(chapters, start=1):
        tasks.append(
            {
                "idx": idx,
                "title": chap.get("title", ""),
                "content": chap.get("content", ""),
                "model": model,
                "prev_title": chapters[idx - 2].get("title", "") if idx > 1 else "",
                "prev_snippet": _snippet(chapters[idx - 2].get("content", "")) if idx > 1 else "",
                "next_title": chapters[idx].get("title", "") if idx < len(chapters) else "",
                "next_snippet": _snippet(chapters[idx].get("content", "")) if idx < len(chapters) else "",
            }
        )

    results: Dict[int, Dict] = {}
    total = len(tasks)
    completed = 0
    try:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=max_workers) as pool:
            for result in tqdm(pool.imap_unordered(_mp_worker, tasks), total=total):
                if cancel_check and cancel_check():
                    pool.terminate()
                    pool.join()
                    return [results[i] for i in sorted(results.keys())]
                results[result["chapter_num"]] = result
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)
        return [results[i] for i in sorted(results.keys())]
    except Exception as exc:
        if _strict_mode():
            raise
        print(f"[outline_pipeline] mp failed, fallback to threads: {exc}")
        results = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_mp_worker, task): task["idx"] for task in tasks}
            for fut in as_completed(futures):
                if cancel_check and cancel_check():
                    break
                result = fut.result()
                results[result["chapter_num"]] = result
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)
        return [results[i] for i in sorted(results.keys())]


def merge_outline(title: str, chapters: List[Dict], author_style: Dict[str, str] | None = None) -> Dict:
    volumes: List[Dict] = []
    for i in range(0, len(chapters), 25):
        volume_num = i // 25 + 1
        volumes.append(
            {
                "volume_num": volume_num,
                "title": f"第{volume_num}卷",
                "core_objective": "",
                "chapters": chapters[i : i + 25],
            }
        )
    return {
        "title": title or "未命名小说",
        "author_style": author_style or {},
        "setting": {
            "power_system": "",
            "world_rules": "",
            "main_conflict": "",
        },
        "volumes": volumes,
    }
