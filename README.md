# NovelForge

Minimal scaffolding to start Milestone 1 (The Architect) using Volcengine Ark with the OpenAI SDK.

## Setup

1) Set environment variables:

```bash
export ARK_API_KEY="<your-volcengine-ark-key>"
```

Optional overrides:

```bash
export ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
export NOVELFORGE_MODEL_ARCHITECT="doubao-seed-1-8-251228"
export NOVELFORGE_MODEL_LIST_FILE="model-list.html"
export NOVELFORGE_CACHE=1
export NOVELFORGE_CACHE_DIR=".novelforge-cache"
export NOVELFORGE_CACHE_BYPASS=1  # set to bypass cache once
```

2) Install dependencies (example):

```bash
pip install -r requirements-runtime.txt
```

## Usage

Generate a 100-chapter outline from a one-line idea:

```bash
python -m novelforge.cli architect --idea "赛博朋克世界的修仙者" --out outline.json
```

Stream-generate a chapter with continuity memory (no vector DB), saving to a folder named by the novel title:

```bash
python -m novelforge.cli ghostwrite --outline outline.json --chapter 1 --novel-id demo --output-dir .
```

Generate a range of chapters (e.g. 1-5):

```bash
python -m novelforge.cli ghostwrite --outline outline.json --chapter-range 1-5 --novel-id demo --output-dir .
```

Memory files are stored under `.novelforge-memory/novel_<id>.json`.
Additional continuity files are stored under `.novelforge-memory/`:
- `global_summary_<id>.txt`
- `character_state_<id>.txt`

## Streamlit Web UI

```bash
streamlit run streamlit_app.py
```

List available model IDs parsed from `model-list.html`:

```bash
python -m novelforge.cli models
```

Convert an outline file (docx/txt/md) to `outline.json` (LLM-based parsing):

```bash
python -m novelforge.cli convert-outline --input /mnt/d/workspace/港综：悍匪系统章纲1-100章.docx --out outline.json --title "港综：悍匪系统"
```

Stream JSON output while converting:

```bash
python -m novelforge.cli convert-outline --input /mnt/d/workspace/港综：悍匪系统章纲1-100章.docx --out outline.json --title "港综：悍匪系统" --stream
```

Override convert model:

```bash
export NOVELFORGE_MODEL_CONVERT="doubao-seed-1-8-251228"
```

## Notes

- The outline pipeline calls the model four times (setting + 4 volumes).
- Chapter counts are normalized to exactly 25 per volume (100 total).
