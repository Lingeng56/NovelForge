import json
import os
import threading
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import streamlit as st

from novelforge.core.config import get_settings
from novelforge.core.llm import get_client
from novelforge.core.memory import MemoryStore
from novelforge.logic.architect import main_architect, outline_to_json
from novelforge.logic.ghostwriter import generate_chapter, load_outline, DEFAULT_LEADIN
from novelforge.logic.chapter_split import split_file_to_dir
from novelforge.logic.outline_pipeline import (
    merge_outline,
    split_full_novel,
    summarize_chapters_parallel_mp,
)
from novelforge.logic.outline_convert import convert_outline, rewrite_outline


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _strict_mode() -> bool:
    return os.getenv("NOVELFORGE_STRICT", "").strip().lower() in {"1", "true", "yes"}


def project_paths(project_dir: Path) -> Dict[str, Path]:
    return {
        "root": project_dir,
        "input": project_dir / "input",
        "output": project_dir / "output",
        "memory": project_dir / "memory",
        "outline": project_dir / "outline.json",
        "status": project_dir / "status.json",
    }


def init_project(project_dir: Path) -> None:
    paths = project_paths(project_dir)
    for key in ("root", "input", "output", "memory"):
        ensure_dir(paths[key])
    if not paths["status"].exists():
        paths["status"].write_text(
            json.dumps(
                {
                    "state": "idle",
                    "current": 0,
                    "total": 0,
                    "message": "",
                    "updated_at": "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def read_status(project_dir: Path) -> Dict:
    path = project_paths(project_dir)["status"]
    if not path.exists():
        return {"state": "idle", "current": 0, "total": 0, "message": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(project_dir: Path, data: Dict) -> None:
    path = project_paths(project_dir)["status"]
    data = {**data, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_api_key(api_key: str) -> None:
    if api_key:
        os.environ["ARK_API_KEY"] = api_key


def _monthly_password_hash() -> str:
    month_str = datetime.now().strftime("%Y%m")
    raw = f"{month_str}56666"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _current_month() -> str:
    return datetime.now().strftime("%Y%m")


def require_app_auth() -> bool:
    current_month = _current_month()
    if st.session_state.get("auth_month") != current_month:
        st.session_state["auth_ok"] = False
        st.session_state["outline_auth_ok"] = False
        st.session_state["auth_month"] = current_month

    st.session_state.setdefault("auth_ok", False)
    if st.session_state["auth_ok"]:
        return True
    st.subheader("访问验证")
    pwd = st.text_input("密码", type="password")
    if st.button("验证"):
        if pwd == _monthly_password_hash():
            st.session_state["auth_ok"] = True
            st.session_state["auth_month"] = current_month
            st.success("验证通过")
            return True
        st.error("密码错误")
    return False


def require_outline_auth() -> bool:
    current_month = _current_month()
    if st.session_state.get("auth_month") != current_month:
        st.session_state["auth_ok"] = False
        st.session_state["outline_auth_ok"] = False
        st.session_state["auth_month"] = current_month

    st.session_state.setdefault("outline_auth_ok", False)
    if st.session_state["outline_auth_ok"]:
        return True
    st.warning("该操作需要二次验证。")
    pwd = st.text_input("二次验证密码", type="password", key="outline_auth_pwd")
    if st.button("二次验证"):
        if pwd == _monthly_password_hash():
            st.session_state["outline_auth_ok"] = True
            st.session_state["auth_month"] = current_month
            st.success("二次验证通过")
            return True
        st.error("二次验证失败")
    return False


def outline_exists(project_dir: Path) -> bool:
    return project_paths(project_dir)["outline"].exists()


def load_outline_or_none(project_dir: Path):
    outline_path = project_paths(project_dir)["outline"]
    if not outline_path.exists():
        return None
    return load_outline(str(outline_path))


def parse_range(value: str) -> Optional[tuple[int, int]]:
    if not value:
        return None
    if "-" not in value:
        raise ValueError("chapter-range must be in the form start-end")
    start_str, end_str = value.split("-", 1)
    start = int(start_str)
    end = int(end_str)
    if start <= 0 or end <= 0 or end < start:
        raise ValueError("range must be positive and end >= start")
    return start, end


def generate_in_background(project_dir: Path, chapter_numbers: list[int]) -> None:
    try:
        settings = get_settings()
        client = get_client(settings)
        outline = load_outline_or_none(project_dir)
        if outline is None:
            write_status(project_dir, {"state": "error", "message": "outline.json not found", "current": 0, "total": 0})
            return

        chapter_map = {c.chapter_num: c for v in outline.volumes for c in v.chapters}
        memory_store = MemoryStore(project_paths(project_dir)["memory"])
        novel_title = outline.title or "Untitled"

        total = len(chapter_numbers)
        write_status(project_dir, {"state": "running", "current": 0, "total": total, "message": ""})

        base_leadin = st.session_state.get("base_leadin", DEFAULT_LEADIN)
        prev_window = int(st.session_state.get("prev_window", 3))
        next_window = int(st.session_state.get("next_window", 3))

        for idx, chapter_num in enumerate(chapter_numbers, start=1):
            if st.session_state.get("stop_generation"):
                write_status(project_dir, {"state": "stopped", "current": idx - 1, "total": total, "message": "stopped by user"})
                return
            chapter_node = chapter_map.get(chapter_num)
            if not chapter_node:
                write_status(project_dir, {"state": "error", "current": idx - 1, "total": total, "message": f"chapter {chapter_num} not found"})
                return

            def _stream_handler(_text: str) -> None:
                return

            result = generate_chapter(
                client,
                settings.model_ghostwriter,
                chapter_node,
                memory_store,
                st.session_state.get("novel_id", "default"),
                _stream_handler,
                str(project_paths(project_dir)["output"]),
                novel_title,
                base_leadin=base_leadin,
                prev_window=prev_window,
                next_window=next_window,
            )

            safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in novel_title).strip() or "Untitled"
            base_dir = project_paths(project_dir)["output"] / safe_title
            base_dir.mkdir(parents=True, exist_ok=True)
            filename = f"chapter-{chapter_node.chapter_num:03d}.md"
            path = base_dir / filename
            content = f"# {chapter_node.title}\n\n{result['chapter_text']}\n"
            path.write_text(content, encoding="utf-8")

            write_status(project_dir, {"state": "running", "current": idx, "total": total, "message": f"generated {filename}"})

        write_status(project_dir, {"state": "completed", "current": total, "total": total, "message": "done"})
    except Exception as exc:
        write_status(project_dir, {"state": "error", "current": 0, "total": 0, "message": str(exc)})


st.set_page_config(page_title="NovelForge", layout="wide")
st.title("NovelForge 控制台")

if not require_app_auth():
    st.stop()

with st.sidebar:
    st.header("全局设置")
    api_key = st.text_input("ARK_API_KEY", type="password")
    model_architect = st.text_input("大纲模型", value=os.getenv("NOVELFORGE_MODEL_ARCHITECT", "doubao-seed-1-6-flash-250828"))
    model_convert = st.text_input("转换模型", value=os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228"))
    model_ghostwriter = st.text_input("写作模型", value=os.getenv("NOVELFORGE_MODEL_GHOSTWRITER", "doubao-seed-1-6-flash-250828"))
    st.session_state["novel_id"] = st.text_input("小说标识（Novel ID）", value=st.session_state.get("novel_id", "default"))

set_api_key(api_key)

st.session_state.setdefault("project_dir", str(Path.cwd() / "projects" / "demo"))

project_tab, outline_tab, outline_gen_tab, generate_tab, review_tab, monitor_tab = st.tabs(
    ["项目", "大纲管理", "小说大纲生成", "生成", "审阅", "监控"]
)

with project_tab:
    st.subheader("创建 / 选择项目")
    project_dir_input = st.text_input("项目目录", value=st.session_state["project_dir"])
    if st.button("创建 / 加载项目"):
        project_dir = Path(project_dir_input)
        init_project(project_dir)
        st.session_state["project_dir"] = str(project_dir)
        st.success(f"项目就绪：{project_dir}")

    st.caption("项目结构：input/ output/ memory/ outline.json status.json")

with outline_tab:
    st.subheader("大纲管理")
    project_dir = Path(st.session_state["project_dir"])
    init_project(project_dir)
    if not require_outline_auth():
        st.stop()

    st.markdown("**A. 从灵感生成**")
    idea = st.text_area("灵感", "赛博朋克世界的修仙者")
    if st.button("生成大纲"):
        try:
            os.environ["NOVELFORGE_MODEL_ARCHITECT"] = model_architect
            settings = get_settings()
            client = get_client(settings)
            outline = main_architect(client, settings.model_architect, idea)
            project_paths(project_dir)["outline"].write_text(outline_to_json(outline), encoding="utf-8")
            st.success("outline.json 已保存。")
        except Exception as exc:
            st.error(str(exc))
            if _strict_mode():
                raise
            print(f"[streamlit_app] error: {exc}")

    st.markdown("**B. 从文件转换**")
    uploaded = st.file_uploader("上传大纲文件（.docx/.txt/.md）")
    if uploaded is not None and st.button("转换上传文件"):
        if not require_outline_auth():
            st.stop()
        dest = project_paths(project_dir)["input"] / uploaded.name
        dest.write_bytes(uploaded.read())
        try:
            os.environ["NOVELFORGE_MODEL_CONVERT"] = model_convert
            settings = get_settings()
            client = get_client(settings)
            convert_outline(
                str(dest),
                str(project_paths(project_dir)["outline"]),
                client=client,
                model=model_convert,
                title=None,
                reasoning_effort="high",
                stream_output=False,
                max_tokens=65536,
            )
            st.success("outline.json 已保存。")
        except Exception as exc:
            st.error(str(exc))
            if _strict_mode():
                raise
            print(f"[streamlit_app] error: {exc}")

    st.markdown("**C. 从文本转换**")
    text_input = st.text_area("粘贴大纲文本")
    if st.button("转换文本"):
        if not require_outline_auth():
            st.stop()
        dest = project_paths(project_dir)["input"] / "outline_text.txt"
        dest.write_text(text_input, encoding="utf-8")
        try:
            os.environ["NOVELFORGE_MODEL_CONVERT"] = model_convert
            settings = get_settings()
            client = get_client(settings)
            convert_outline(
                str(dest),
                str(project_paths(project_dir)["outline"]),
                client=client,
                model=model_convert,
                title=None,
                reasoning_effort="high",
                stream_output=False,
                max_tokens=65536,
            )
            st.success("outline.json 已保存。")
        except Exception as exc:
            st.error(str(exc))
            if _strict_mode():
                raise
            print(f"[streamlit_app] error: {exc}")

    st.markdown("**D. 上传 outline.json**")
    outline_upload = st.file_uploader("上传 outline.json", type=["json"], key="outline_json_upload")
    if outline_upload is not None and st.button("保存上传的 outline.json"):
        try:
            data = json.loads(outline_upload.read().decode("utf-8"))
        except Exception:
            st.error("上传文件不是有效的 JSON。")
            st.stop()
        if not isinstance(data, dict) or "volumes" not in data or "setting" not in data or "title" not in data:
            st.error("JSON 格式不符合 outline.json 要求。必须包含 title/setting/volumes。")
            st.stop()
        try:
            project_paths(project_dir)["outline"].write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            st.success("outline.json 已保存。")
        except Exception as e:
            st.error(str(e))
            if _strict_mode():
                raise
            print(f"[streamlit_app] error: {e}")

    st.markdown("**大纲预览**")
    outline_path = project_paths(project_dir)["outline"]
    if outline_path.exists():
        st.json(json.loads(outline_path.read_text(encoding="utf-8")))
    else:
        st.info("项目中尚无 outline.json。")

with outline_gen_tab:
    st.subheader("小说大纲生成（分步骤）")
    project_dir = Path(st.session_state["project_dir"])
    init_project(project_dir)
    if not require_outline_auth():
        st.stop()

    default_outline_path = str(project_paths(project_dir)["outline"])
    outline_path_input = st.text_input("outline 输出路径", value=default_outline_path)
    outline_path = Path(outline_path_input).expanduser()

    st.markdown("**步骤 1：上传完整小说并分章节**")
    full_upload = st.file_uploader("上传完整小说（.txt）", type=["txt"], key="full_novel_upload_steps")
    if full_upload is not None and st.button("执行分章节", key="step_split_btn"):
        raw_text = full_upload.read().decode("utf-8")
        chapters = split_full_novel(raw_text, limit=100)
        st.session_state["split_chapters"] = chapters
        st.success(f"分章节完成：{len(chapters)} 章")
        st.info("已保存分章结果，可在下方输入序号查看。")

    chapters = st.session_state.get("split_chapters", [])
    if chapters:
        view_index = st.number_input("查看分章序号", min_value=1, max_value=len(chapters), value=1, step=1)
        st.markdown(f"**第 {view_index} 章**")
        st.text(chapters[view_index - 1].get("title", ""))
        st.markdown(chapters[view_index - 1].get("content", ""))

    st.markdown("**步骤 2：并行生成每章大纲**")
    if st.button("执行并行生成", key="step_outline_btn"):
        chapters = st.session_state.get("split_chapters", [])
        if not chapters:
            st.error("请先完成分章节。")
            st.stop()
        flash_model = os.getenv("NOVELFORGE_MODEL_FLASH", "doubao-seed-1-6-flash-250828")
        max_workers = int(os.getenv("NOVELFORGE_CONVERT_WORKERS", "6"))
        progress_bar = st.progress(0)
        status = st.empty()

        def on_progress(done: int, total: int) -> None:
            pct = int(done / max(total, 1) * 100)
            progress_bar.progress(pct)
            status.text(f"已完成 {done}/{total} 章（{pct}%）")

        outlines = summarize_chapters_parallel_mp(
            flash_model,
            chapters,
            max_workers=max_workers,
            progress_callback=on_progress,
        )
        progress_bar.progress(100)
        st.session_state["chapter_outlines"] = outlines
        st.success("并行大纲生成完成")
        st.json(outlines[:3])

    st.markdown("**步骤 3：合并为大纲文件**")
    outline_title = st.text_input("大纲标题（可选）", value="")
    if st.button("合并并保存", key="step_merge_btn"):
        outlines = st.session_state.get("chapter_outlines", [])
        if not outlines:
            st.error("请先生成每章大纲。")
            st.stop()
        merged = merge_outline(outline_title, outlines)
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        outline_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        st.success("大纲文件已保存。")
        st.json(merged["volumes"][:1])

    st.markdown("**步骤 4：重写规整（解决缺失与对齐）**")
    if st.button("执行重写规整", key="step_rewrite_btn"):
        if not outline_path.exists():
            st.error("请先生成 outline.json。")
        else:
            try:
                settings = get_settings()
                client = get_client(settings)
                stream_box = st.expander("重写输出（流式）", expanded=False)
                stream_placeholder = stream_box.empty()
                stream_chunks: list[str] = []

                def stream_handler(text: str) -> None:
                    stream_chunks.append(text)
                    stream_placeholder.text("".join(stream_chunks))

                rewrite_outline(
                    str(outline_path),
                    str(outline_path),
                    client=client,
                    model="doubao-seed-1-8-251228",
                    reasoning_effort="high",
                    stream_output=True,
                    stream_handler=stream_handler,
                    max_tokens=32768,
                )
                st.success("大纲文件已重写规整。")
            except Exception as exc:
                st.error(str(exc))
                if _strict_mode():
                    raise
                print(f"[streamlit_app] error: {exc}")


with generate_tab:
    st.subheader("生成章节")
    project_dir = Path(st.session_state["project_dir"])
    init_project(project_dir)

    chapter = st.number_input("单章生成", min_value=1, value=1, step=1)
    chapter_range = st.text_input("章节范围（如 1-5）")
    prev_window = st.number_input("前文摘要窗口（章数）", min_value=0, value=3, step=1)
    next_window = st.number_input("后文摘要窗口（章数）", min_value=0, value=3, step=1)
    base_leadin = st.text_input("写作指令首句（可修改）", value=DEFAULT_LEADIN)

    col_start, col_stop = st.columns(2)
    with col_start:
        if st.button("开始生成"):
            outline = load_outline_or_none(project_dir)
            if outline is None:
                st.error("未找到 outline.json。")
            else:
                if chapter_range.strip():
                    start, end = parse_range(chapter_range.strip())
                    chapter_numbers = list(range(start, end + 1))
                else:
                    chapter_numbers = [int(chapter)]
                st.session_state["stop_generation"] = False
                st.session_state["base_leadin"] = base_leadin
                st.session_state["prev_window"] = int(prev_window)
                st.session_state["next_window"] = int(next_window)
                thread = threading.Thread(
                    target=generate_in_background,
                    args=(project_dir, chapter_numbers),
                    daemon=True,
                )
                thread.start()
                st.success("已在后台开始生成。")

    with col_stop:
        if st.button("停止生成"):
            st.session_state["stop_generation"] = True
            st.warning("已发送停止信号。")

with review_tab:
    st.subheader("审阅章节")
    project_dir = Path(st.session_state["project_dir"])
    output_root = project_paths(project_dir)["output"]
    if output_root.exists():
        chapters = sorted(output_root.rglob("chapter-*.md"))
        if chapters:
            selected = st.selectbox("选择章节", chapters)
            content = selected.read_text(encoding="utf-8")
            st.markdown(content)
        else:
            st.info("暂无已生成章节。")
    else:
        st.info("未找到输出目录。")

with monitor_tab:
    st.subheader("生成监控")
    project_dir = Path(st.session_state["project_dir"])
    status = read_status(project_dir)
    st.write(status)
    total = max(1, int(status.get("total", 0)))
    current = int(status.get("current", 0))
    st.progress(min(current / total, 1.0))

    mem_dir = project_paths(project_dir)["memory"]
    summary_path = mem_dir / f"global_summary_{st.session_state.get('novel_id','default')}.txt"
    state_path = mem_dir / f"character_state_{st.session_state.get('novel_id','default')}.txt"
    if summary_path.exists():
        st.markdown("**前文摘要**")
        st.text(summary_path.read_text(encoding="utf-8"))
    if state_path.exists():
        st.markdown("**角色状态**")
        st.text(state_path.read_text(encoding="utf-8"))
