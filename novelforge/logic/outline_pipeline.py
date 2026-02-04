from __future__ import annotations

import json
import os
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

from ..logic.chapter_split import split_chapters
from tqdm import tqdm


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
        "summary": str(payload.get("summary", "")).strip(),
    }


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
        print(f"[outline_pipeline] chapter {idx} failed: {exc}")
        return {
            "chapter_num": idx,
            "title": title or f"第{idx}章",
            "summary": "",
        }


def _mp_worker(args: Dict) -> Dict:
    from openai import OpenAI

    api_key = os.getenv("ARK_API_KEY")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not set.")

    client = OpenAI(base_url=base_url, api_key=api_key)
    model = args["model"]
    idx = args["idx"]
    title = args["title"]
    content = args["content"]

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
    data = _extract_json_any(response.choices[0].message.content or "{}")
    return _normalize_outline_payload(data, idx, title)


def split_full_novel(text: str, limit: int = 100) -> List[Dict]:
    return split_chapters(text, limit=limit)


def summarize_chapters_parallel(client, model: str, chapters: List[Dict], max_workers: int = 6) -> List[Dict]:
    results: Dict[int, Dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_summarize_chapter_flash, client, model, chap, idx): idx
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
) -> List[Dict]:
    tasks = []
    for idx, chap in enumerate(chapters, start=1):
        tasks.append(
            {
                "idx": idx,
                "title": chap.get("title", ""),
                "content": chap.get("content", ""),
                "model": model,
            }
        )

    results: Dict[int, Dict] = {}
    total = len(tasks)
    completed = 0
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=max_workers) as pool:
        for result in tqdm(pool.imap_unordered(_mp_worker, tasks), total=total):
            results[result["chapter_num"]] = result
            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    return [results[i] for i in sorted(results.keys())]


def merge_outline(title: str, chapters: List[Dict]) -> Dict:
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
        "setting": {
            "power_system": "",
            "world_rules": "",
            "main_conflict": "",
        },
        "volumes": volumes,
    }
