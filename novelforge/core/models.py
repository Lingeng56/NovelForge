from __future__ import annotations

import re
from pathlib import Path
from typing import Set

_MODEL_ID_PATTERN = re.compile(r"model/detail\?Id=([a-zA-Z0-9\-_.]+)")
_EXTRA_PATTERNS = [
    re.compile(r"\bdoubao-[a-z0-9\-]+\b"),
    re.compile(r"\bdeepseek-[a-z0-9\-]+\b"),
    re.compile(r"\bkimi-[a-z0-9\-]+\b"),
]


def load_model_ids(path: Path) -> Set[str]:
    if not path.exists():
        raise FileNotFoundError(f"Model list file not found: {path}")

    text = path.read_text(encoding="utf-8")
    model_ids = set(_MODEL_ID_PATTERN.findall(text))
    for pattern in _EXTRA_PATTERNS:
        model_ids.update(pattern.findall(text))

    if not model_ids:
        raise ValueError(f"No model ids parsed from: {path}")

    return model_ids
