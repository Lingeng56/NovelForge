import argparse
import os
from pathlib import Path

from .core.config import get_settings
from .core.memory import MemoryStore
from .core.models import load_model_ids
from .core.llm import get_client
from .logic.architect import main_architect, outline_to_json
from .logic.ghostwriter import generate_chapter, load_outline
from .logic.outline_convert import convert_outline, rewrite_outline
from .logic.chapter_split import split_file_to_dir


def run_architect(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = get_client(settings)

    outline = main_architect(client, settings.model_architect, args.idea)
    output_json = outline_to_json(outline)

    if args.out:
        Path(args.out).write_text(output_json, encoding="utf-8")
    else:
        print(output_json)


def run_models(args: argparse.Namespace) -> None:
    model_list_path = Path(
        os.getenv("NOVELFORGE_MODEL_LIST_FILE", "model-list.html").strip()
    )
    if not model_list_path.exists():
        project_root = Path(__file__).resolve().parents[1]
        model_list_path = project_root / model_list_path
    model_ids = sorted(load_model_ids(model_list_path))
    if args.contains:
        model_ids = [mid for mid in model_ids if args.contains in mid]
    for mid in model_ids:
        print(mid)


def _parse_range(value: str) -> tuple[int, int]:
    if "-" not in value:
        raise ValueError("chapter-range must be in the form start-end, e.g. 1-5")
    start_str, end_str = value.split("-", 1)
    start = int(start_str)
    end = int(end_str)
    if start <= 0 or end <= 0 or end < start:
        raise ValueError("chapter-range must be positive and end >= start")
    return start, end


def run_ghostwriter(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = get_client(settings)
    outline = load_outline(args.outline)
    chapter_map = {}
    for volume in outline.volumes:
        for chapter in volume.chapters:
            chapter_map[chapter.chapter_num] = chapter

    if args.chapter_range:
        start, end = _parse_range(args.chapter_range)
        chapter_numbers = list(range(start, end + 1))
    else:
        if args.chapter is None:
            raise ValueError("Provide --chapter or --chapter-range.")
        chapter_numbers = [args.chapter]

    memory_store = MemoryStore(Path(args.memory_dir))

    def stream_handler(text: str) -> None:
        print(text, end="", flush=True)

    novel_title = outline.title or "Untitled"
    for chapter_num in chapter_numbers:
        chapter_node = chapter_map.get(chapter_num)
        if not chapter_node:
            raise ValueError(f"Chapter {chapter_num} not found in outline.")
        result = generate_chapter(
            client,
            settings.model_ghostwriter,
            chapter_node,
            memory_store,
            args.novel_id,
            stream_handler,
            args.output_dir,
            novel_title,
        )

        if args.output_dir:
            safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in novel_title).strip()
            if not safe_title:
                safe_title = "Untitled"
            base_dir = Path(args.output_dir) / safe_title
            base_dir.mkdir(parents=True, exist_ok=True)
            filename = f"chapter-{chapter_node.chapter_num:03d}.md"
            path = base_dir / filename
            content = f"# {chapter_node.title}\n\n{result['chapter_text']}\n"
            path.write_text(content, encoding="utf-8")


def run_convert(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = get_client(settings)
    convert_outline(
        args.input,
        args.out,
        title=args.title,
        client=client,
        model=os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228"),
        reasoning_effort="high",
        stream_output=args.stream,
        stream_handler=(lambda text: print(text, end="", flush=True)) if args.stream else None,
        max_tokens=65536,
    )


def run_rewrite(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = get_client(settings)
    rewrite_outline(
        args.input,
        args.out,
        client=client,
        model="doubao-seed-1-8-251228",
        reasoning_effort="high",
        stream_output=args.stream,
        stream_handler=(lambda text: print(text, end="", flush=True)) if args.stream else None,
        max_tokens=32768,
    )


def run_split(args: argparse.Namespace) -> None:
    split_file_to_dir(args.input, args.out_dir, target_len=args.target_len, max_len=args.max_len)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novelforge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    architect_parser = subparsers.add_parser("architect", help="Generate a 100-chapter outline")
    architect_parser.add_argument("--idea", required=True, help="One-line idea for the novel")
    architect_parser.add_argument("--out", help="Output JSON file path")
    architect_parser.set_defaults(func=run_architect)

    models_parser = subparsers.add_parser("models", help="List available model IDs")
    models_parser.add_argument("--contains", help="Filter model IDs by substring")
    models_parser.set_defaults(func=run_models)

    ghost_parser = subparsers.add_parser("ghostwrite", help="Stream-generate a chapter")
    ghost_parser.add_argument("--outline", required=True, help="Outline JSON path")
    ghost_parser.add_argument("--chapter", type=int, help="Chapter number to generate")
    ghost_parser.add_argument("--chapter-range", help="Chapter range to generate, e.g. 1-5")
    ghost_parser.add_argument("--novel-id", default="default", help="Novel id for memory continuity")
    ghost_parser.add_argument(
        "--memory-dir",
        default=".novelforge-memory",
        help="Directory for storing memory state",
    )
    ghost_parser.add_argument(
        "--output-dir",
        default=".",
        help="Base directory for saving chapters under a folder named by the novel title",
    )
    ghost_parser.set_defaults(func=run_ghostwriter)

    convert_parser = subparsers.add_parser("convert-outline", help="Convert outline file to outline.json")
    convert_parser.add_argument("--input", required=True, help="Input outline file (.docx/.txt/.md)")
    convert_parser.add_argument("--out", required=True, help="Output outline.json path")
    convert_parser.add_argument("--title", help="Optional override for novel title")
    convert_parser.add_argument("--stream", action="store_true", help="Stream output JSON to stdout")
    convert_parser.set_defaults(func=run_convert)

    rewrite_parser = subparsers.add_parser("rewrite-outline", help="Rewrite outline.json with new entities")
    rewrite_parser.add_argument("--input", required=True, help="Input outline.json path")
    rewrite_parser.add_argument("--out", required=True, help="Output outline.json path")
    rewrite_parser.add_argument("--stream", action="store_true", help="Stream output JSON to stdout")
    rewrite_parser.set_defaults(func=run_rewrite)

    split_parser = subparsers.add_parser("split-chapters", help="Split a novel text into chapters")
    split_parser.add_argument("--input", required=True, help="Input .txt file")
    split_parser.add_argument("--out-dir", required=True, help="Output directory for split chapters")
    split_parser.add_argument("--target-len", type=int, default=3500, help="Target length per chapter")
    split_parser.add_argument("--max-len", type=int, default=4500, help="Max length per chapter")
    split_parser.set_defaults(func=run_split)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
