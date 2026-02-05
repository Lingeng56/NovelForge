import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import load_model_ids


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model_architect: str
    model_editor: str
    model_mimic: str
    model_ghostwriter: str
    model_list_path: str


def _validate_model(model_id: str, model_ids: set[str]) -> None:
    if model_id not in model_ids:
        sample = ", ".join(sorted(model_ids)[:8])
        raise ValueError(
            f"Unknown model id '{model_id}'. Check model-list.html. Examples: {sample}"
        )


def get_settings() -> Settings:
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not set. Export it before running.")

    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").strip()
    model_list_path = os.getenv("NOVELFORGE_MODEL_LIST_FILE", "model-list.html").strip()
    resolved_model_list = Path(model_list_path)
    if not resolved_model_list.exists():
        project_root = Path(__file__).resolve().parents[2]
        resolved_model_list = project_root / model_list_path
    if not resolved_model_list.exists() and hasattr(sys, "_MEIPASS"):
        resolved_model_list = Path(sys._MEIPASS) / model_list_path

    model_ids: set[str] | None
    try:
        model_ids = load_model_ids(resolved_model_list)
    except FileNotFoundError:
        model_ids = None
    model_architect = os.getenv("NOVELFORGE_MODEL_ARCHITECT", "doubao-seed-1-6-flash-250828").strip()
    model_editor = os.getenv("NOVELFORGE_MODEL_EDITOR", "doubao-seed-1-6-flash-250828").strip()
    model_mimic = os.getenv("NOVELFORGE_MODEL_MIMIC", "doubao-seed-1-6-flash-250828").strip()
    model_ghostwriter = os.getenv("NOVELFORGE_MODEL_GHOSTWRITER", "doubao-seed-1-6-flash-250828").strip()

    if model_ids:
        _validate_model(model_architect, model_ids)
        _validate_model(model_editor, model_ids)
        _validate_model(model_mimic, model_ids)
        _validate_model(model_ghostwriter, model_ids)

    return Settings(
        api_key=api_key,
        base_url=base_url,
        model_architect=model_architect,
        model_editor=model_editor,
        model_mimic=model_mimic,
        model_ghostwriter=model_ghostwriter,
        model_list_path=str(resolved_model_list),
    )
