from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool
    bypass: bool
    cache_dir: Path


def get_cache_config() -> CacheConfig:
    enabled = os.getenv("NOVELFORGE_CACHE", "1").strip().lower() in {"1", "true", "yes"}
    bypass = os.getenv("NOVELFORGE_CACHE_BYPASS", "").strip().lower() in {"1", "true", "yes"}
    cache_dir = Path(os.getenv("NOVELFORGE_CACHE_DIR", ".novelforge-cache")).resolve()
    return CacheConfig(enabled=enabled, bypass=bypass, cache_dir=cache_dir)


def _hash_key(model: str, prompt: str) -> str:
    digest = hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()
    return digest


def load_cached_text(model: str, prompt: str) -> Optional[str]:
    cfg = get_cache_config()
    if not cfg.enabled or cfg.bypass:
        return None
    key = _hash_key(model, prompt)
    path = cfg.cache_dir / f"{key}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("text")


def store_cached_text(model: str, prompt: str, text: str) -> None:
    cfg = get_cache_config()
    if not cfg.enabled or cfg.bypass:
        return
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    key = _hash_key(model, prompt)
    path = cfg.cache_dir / f"{key}.json"
    payload = {
        "model": model,
        "prompt": prompt,
        "text": text,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
