from __future__ import annotations

import re
from pathlib import Path
from typing import List, Dict


_HEADING_PATTERNS = [
    re.compile(r"^\s*第\s*(\d+)\s*[章节回卷篇幕].*", re.IGNORECASE),
    re.compile(r"^\s*chapter\s*(\d+).*", re.IGNORECASE),
]


def _is_heading(line: str) -> bool:
    return any(pat.match(line) for pat in _HEADING_PATTERNS)


def _extract_heading(line: str) -> str:
    return line.strip()


def split_by_headings(text: str) -> List[Dict]:
    lines = [line.rstrip() for line in text.splitlines()]
    chapters: List[Dict] = []
    current = None

    def flush():
        nonlocal current
        if current is None:
            return
        content = "\n".join(current["lines"]).strip()
        chapters.append(
            {
                "title": current["title"],
                "content": content,
            }
        )
        current = None

    for line in lines:
        if _is_heading(line):
            flush()
            current = {"title": _extract_heading(line), "lines": []}
            continue
        if current is None:
            # Skip preface until first heading
            continue
        current["lines"].append(line)

    flush()
    return chapters


def split_by_length(text: str, target_len: int = 3500, max_len: int = 4500) -> List[Dict]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if len(paragraphs) <= 1:
        # Fallback to hard slicing when no usable paragraph breaks exist
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + max_len, len(text))
            if end - start < target_len and end != len(text):
                end = min(start + target_len, len(text))
            chunks.append(text[start:end].strip())
            start = end
        paragraphs = [c for c in chunks if c]
    chapters: List[Dict] = []
    buffer = []
    length = 0
    index = 1

    for para in paragraphs:
        if length + len(para) > max_len and buffer:
            chapters.append(
                {
                    "title": f"第{index}章",
                    "content": "\n\n".join(buffer).strip(),
                }
            )
            buffer = [para]
            length = len(para)
            index += 1
            continue

        buffer.append(para)
        length += len(para)
        if length >= target_len:
            chapters.append(
                {
                    "title": f"第{index}章",
                    "content": "\n\n".join(buffer).strip(),
                }
            )
            buffer = []
            length = 0
            index += 1

    if buffer:
        chapters.append(
            {
                "title": f"第{index}章",
                "content": "\n\n".join(buffer).strip(),
            }
        )

    return chapters


def split_chapters(text: str, target_len: int = 3500, max_len: int = 4500, limit: int | None = None) -> List[Dict]:
    chapters = split_by_headings(text)
    if chapters:
        if limit is not None:
            return chapters[:limit]
        return chapters
    chapters = split_by_length(text, target_len=target_len, max_len=max_len)
    if limit is not None:
        return chapters[:limit]
    return chapters


def split_file_to_dir(input_path: str, out_dir: str, target_len: int = 3500, max_len: int = 4500) -> List[Path]:
    text = Path(input_path).read_text(encoding="utf-8")
    chapters = split_chapters(text, target_len=target_len, max_len=max_len)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    written = []
    for idx, chap in enumerate(chapters, start=1):
        filename = out_path / f"chapter-{idx:03d}.txt"
        content = f"{chap['title']}\n\n{chap['content']}\n"
        filename.write_text(content, encoding="utf-8")
        written.append(filename)
    return written
