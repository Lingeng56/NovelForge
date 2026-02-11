import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from novelforge.core.config import get_settings
from novelforge.core.auth import monthly_password_hash
from novelforge.core.llm import get_client
from novelforge.logic.architect import main_architect, outline_to_json
from novelforge.logic.outline_convert import convert_outline, rewrite_outline
from novelforge.logic.outline_pipeline import (
    merge_outline,
    split_full_novel,
    summarize_chapters_parallel_mp,
)


class CancelledError(Exception):
    pass


class Worker(QObject):
    finished = Signal(bool, str)
    log = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.fn(self.log.emit)
            self.finished.emit(True, "完成")
        except CancelledError as exc:
            self.finished.emit(True, str(exc))
        except Exception as exc:
            print(f"[app1] error: {exc}")
            self.finished.emit(False, str(exc))


class OutlineGeneratorApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NovelForge - App1 大纲生成器")
        self.setMinimumWidth(900)

        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.cancel_requested = False
        self.full_split_cache: list[dict] | None = None
        self.full_outline_cache: list[dict] | None = None
        self.full_merged_cache: dict | None = None

        root = QWidget()
        layout = QVBoxLayout(root)

        layout.addWidget(self._build_settings())
        layout.addWidget(self._build_tabs())
        layout.addWidget(self._build_log())

        self.setCentralWidget(root)

    def _build_settings(self) -> QWidget:
        box = QGroupBox("全局设置")
        grid = QGridLayout(box)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("ARK_API_KEY")
        self.api_key_input.setText(os.getenv("ARK_API_KEY", ""))

        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("ARK_BASE_URL (可选)")
        self.base_url_input.setText(os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))

        self.model_architect_input = QLineEdit()
        self.model_architect_input.setPlaceholderText("NOVELFORGE_MODEL_ARCHITECT")
        self.model_architect_input.setText(os.getenv("NOVELFORGE_MODEL_ARCHITECT", "doubao-seed-1-6-flash-250828"))

        self.model_convert_input = QLineEdit()
        self.model_convert_input.setPlaceholderText("NOVELFORGE_MODEL_CONVERT")
        self.model_convert_input.setText(os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228"))

        self.strict_mode = QCheckBox("严格模式 (NOVELFORGE_STRICT=1)")
        self.strict_mode.setChecked(os.getenv("NOVELFORGE_STRICT", "").strip().lower() in {"1", "true", "yes"})

        grid.addWidget(QLabel("API Key"), 0, 0)
        grid.addWidget(self.api_key_input, 0, 1)
        grid.addWidget(QLabel("Base URL"), 0, 2)
        grid.addWidget(self.base_url_input, 0, 3)
        grid.addWidget(QLabel("Architect 模型"), 1, 0)
        grid.addWidget(self.model_architect_input, 1, 1)
        grid.addWidget(QLabel("Convert 模型"), 1, 2)
        grid.addWidget(self.model_convert_input, 1, 3)
        grid.addWidget(self.strict_mode, 2, 0, 1, 2)

        return box

    def _build_tabs(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_idea_tab(), "Idea -> 大纲")
        tabs.addTab(self._build_full_novel_tab(), "完整小说 -> 大纲")
        tabs.addTab(self._build_docx_tab(), "DOCX -> 新大纲")
        return tabs

    def _build_idea_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.idea_input = QPlainTextEdit()
        self.idea_input.setPlaceholderText("输入一句灵感")
        if not self.idea_input.toPlainText().strip():
            self.idea_input.setPlainText("赛博朋克世界的修仙者")

        output_row = QHBoxLayout()
        self.idea_output_path = QLineEdit()
        self.idea_output_path.setText(str(Path.cwd() / "outline.json"))
        browse = QPushButton("选择输出文件")
        browse.clicked.connect(lambda: self._select_output(self.idea_output_path))
        output_row.addWidget(QLabel("输出 outline.json"))
        output_row.addWidget(self.idea_output_path)
        output_row.addWidget(browse)

        run_btn = QPushButton("生成大纲")
        run_btn.clicked.connect(self._run_idea)

        layout.addWidget(self.idea_input)
        layout.addLayout(output_row)
        layout.addWidget(run_btn)
        return widget

    def _build_full_novel_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        input_row = QHBoxLayout()
        self.full_input_path = QLineEdit()
        browse_in = QPushButton("选择输入文件")
        browse_in.clicked.connect(lambda: self._select_input(self.full_input_path))
        input_row.addWidget(QLabel("输入小说文件"))
        input_row.addWidget(self.full_input_path)
        input_row.addWidget(browse_in)

        title_row = QHBoxLayout()
        self.full_title = QLineEdit()
        self.full_title.setPlaceholderText("标题（可选）")
        title_row.addWidget(QLabel("标题"))
        title_row.addWidget(self.full_title)

        settings_row = QHBoxLayout()
        self.full_workers = QLineEdit()
        self.full_workers.setPlaceholderText("并行进程数")
        self.full_workers.setText(os.getenv("NOVELFORGE_CONVERT_WORKERS", "6"))
        settings_row.addWidget(QLabel("进程数"))
        settings_row.addWidget(self.full_workers)

        output_row = QHBoxLayout()
        self.full_output_path = QLineEdit()
        self.full_output_path.setText(str(Path.cwd() / "outline_from_full.json"))
        browse_out = QPushButton("选择输出文件")
        browse_out.clicked.connect(lambda: self._select_output(self.full_output_path))
        output_row.addWidget(QLabel("输出 outline.json"))
        output_row.addWidget(self.full_output_path)
        output_row.addWidget(browse_out)

        step_row = QHBoxLayout()
        split_btn = QPushButton("步骤1：分章节")
        split_btn.clicked.connect(self._run_full_split)
        outline_btn = QPushButton("步骤2：并行章纲")
        outline_btn.clicked.connect(self._run_full_outline)
        merge_btn = QPushButton("步骤3：合并")
        merge_btn.clicked.connect(self._run_full_merge)
        rewrite_btn = QPushButton("步骤4：重写规整")
        rewrite_btn.clicked.connect(self._run_full_rewrite)
        step_row.addWidget(split_btn)
        step_row.addWidget(outline_btn)
        step_row.addWidget(merge_btn)
        step_row.addWidget(rewrite_btn)

        run_btn = QPushButton("一键生成（完整流程）")
        run_btn.clicked.connect(self._run_full_novel)

        layout.addLayout(input_row)
        layout.addLayout(title_row)
        layout.addLayout(settings_row)
        layout.addLayout(output_row)
        layout.addLayout(step_row)
        layout.addWidget(run_btn)
        return widget

    def _build_docx_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        input_row = QHBoxLayout()
        self.docx_input_path = QLineEdit()
        browse_in = QPushButton("选择 DOCX")
        browse_in.clicked.connect(lambda: self._select_input(self.docx_input_path, "DOCX Files (*.docx)"))
        input_row.addWidget(QLabel("输入 DOCX"))
        input_row.addWidget(self.docx_input_path)
        input_row.addWidget(browse_in)

        title_row = QHBoxLayout()
        self.docx_title = QLineEdit()
        self.docx_title.setPlaceholderText("标题（可选）")
        title_row.addWidget(QLabel("标题"))
        title_row.addWidget(self.docx_title)

        output_row = QHBoxLayout()
        self.docx_output_path = QLineEdit()
        self.docx_output_path.setText(str(Path.cwd() / "outline_from_docx.json"))
        browse_out = QPushButton("选择输出文件")
        browse_out.clicked.connect(lambda: self._select_output(self.docx_output_path))
        output_row.addWidget(QLabel("输出 outline.json"))
        output_row.addWidget(self.docx_output_path)
        output_row.addWidget(browse_out)

        run_btn = QPushButton("生成大纲")
        run_btn.clicked.connect(self._run_docx)

        layout.addLayout(input_row)
        layout.addLayout(title_row)
        layout.addLayout(output_row)
        layout.addWidget(run_btn)
        return widget

    def _build_log(self) -> QWidget:
        box = QGroupBox("日志")
        layout = QVBoxLayout(box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)
        self.stop_button = QPushButton("停止当前任务")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._request_cancel)
        layout.addWidget(self.stop_button)
        return box

    def _select_input(self, target: QLineEdit, file_filter: str = "All Files (*)") -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择输入文件", "", file_filter)
        if path:
            target.setText(path)

    def _select_output(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", "outline.json", "JSON Files (*.json)")
        if path:
            target.setText(path)

    def _apply_settings(self) -> None:
        api_key = self.api_key_input.text().strip()
        base_url = self.base_url_input.text().strip()
        model_arch = self.model_architect_input.text().strip()
        model_convert = self.model_convert_input.text().strip()

        if api_key:
            os.environ["ARK_API_KEY"] = api_key
        if base_url:
            os.environ["ARK_BASE_URL"] = base_url
        if model_arch:
            os.environ["NOVELFORGE_MODEL_ARCHITECT"] = model_arch
        if model_convert:
            os.environ["NOVELFORGE_MODEL_CONVERT"] = model_convert
        if self.strict_mode.isChecked():
            os.environ["NOVELFORGE_STRICT"] = "1"
        else:
            os.environ.pop("NOVELFORGE_STRICT", None)

    def _start_worker(self, fn) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "提示", "已有任务在运行。")
            return
        self.cancel_requested = False
        self.log_view.clear()
        self.thread = QThread()
        self.worker = Worker(fn)
        self.worker.moveToThread(self.thread)
        self.worker.log.connect(self._append_log)
        self.worker.finished.connect(self._on_worker_finished)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()
        self.stop_button.setEnabled(True)

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def _on_worker_finished(self, ok: bool, message: str) -> None:
        self.stop_button.setEnabled(False)
        if ok:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "失败", message)

    def _request_cancel(self) -> None:
        if self.cancel_requested:
            return
        self.cancel_requested = True
        self._append_log("收到停止请求，等待当前步骤结束...")

    def _is_cancelled(self) -> bool:
        return self.cancel_requested

    def _run_idea(self) -> None:
        idea = self.idea_input.toPlainText().strip()
        output_path = self.idea_output_path.text().strip()
        if not idea:
            QMessageBox.warning(self, "提示", "请输入灵感。")
            return
        if not output_path:
            QMessageBox.warning(self, "提示", "请选择输出文件。")
            return

        def task(log):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")
            settings = get_settings()
            client = get_client(settings)
            log("开始生成大纲...")
            outline = main_architect(client, settings.model_architect, idea)
            if self._is_cancelled():
                raise CancelledError("已取消")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(outline_to_json(outline), encoding="utf-8")
            log(f"输出完成：{output_path}")

        self._start_worker(task)

    def _run_full_novel(self) -> None:
        input_path = self.full_input_path.text().strip()
        output_path = self.full_output_path.text().strip()
        title = self.full_title.text().strip() or None
        try:
            workers = int(self.full_workers.text().strip() or "6")
        except ValueError:
            QMessageBox.warning(self, "提示", "进程数必须是整数。")
            return
        if not input_path:
            QMessageBox.warning(self, "提示", "请选择输入文件。")
            return
        if not output_path:
            QMessageBox.warning(self, "提示", "请选择输出文件。")
            return

        def task(log):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")
            settings = get_settings()
            client = get_client(settings)
            log("开始分章节...")
            text = Path(input_path).read_text(encoding="utf-8")
            chapters = split_full_novel(text, limit=100)
            log(f"分章完成：{len(chapters)} 章")

            log("开始并行生成每章大纲...")
            def on_progress(done: int, total: int) -> None:
                pct = int(done / max(total, 1) * 100)
                log(f"进度：{done}/{total} ({pct}%)")

            outlines = summarize_chapters_parallel_mp(
                model=os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228"),
                chapters=chapters,
                max_workers=workers,
                progress_callback=on_progress,
                cancel_check=self._is_cancelled,
            )
            if self._is_cancelled():
                raise CancelledError("已取消")

            log("合并 outline...")
            merged = merge_outline(title or "未命名小说", outlines)
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if self._is_cancelled():
                raise CancelledError("已取消")

            log("开始重写规整...")
            rewrite_outline(
                str(out_path),
                str(out_path),
                client=client,
                model=os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228"),
                reasoning_effort="high",
                stream_output=False,
                max_tokens=32768,
            )
            log(f"输出完成：{output_path}")

        self._start_worker(task)

    def _run_full_split(self) -> None:
        input_path = self.full_input_path.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请选择输入文件。")
            return

        def task(log):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")
            log("开始分章节...")
            text = Path(input_path).read_text(encoding="utf-8")
            chapters = split_full_novel(text, limit=100)
            self.full_split_cache = chapters
            out_path = Path(self.full_output_path.text().strip() or "outline_from_full.json")
            cache_path = out_path.with_name("chapters_split.json")
            cache_path.write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"分章完成：{len(chapters)} 章")
            log(f"已保存：{cache_path}")

        self._start_worker(task)

    def _run_full_outline(self) -> None:
        input_path = self.full_input_path.text().strip()
        try:
            workers = int(self.full_workers.text().strip() or "6")
        except ValueError:
            QMessageBox.warning(self, "提示", "进程数必须是整数。")
            return
        if not input_path:
            QMessageBox.warning(self, "提示", "请选择输入文件。")
            return

        def task(log):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")
            chapters = self.full_split_cache
            if not chapters:
                text = Path(input_path).read_text(encoding="utf-8")
                chapters = split_full_novel(text, limit=100)
                self.full_split_cache = chapters
            log(f"开始并行生成章纲，章节数：{len(chapters)}")

            def on_progress(done: int, total: int) -> None:
                pct = int(done / max(total, 1) * 100)
                log(f"进度：{done}/{total} ({pct}%)")

            outlines = summarize_chapters_parallel_mp(
                model=os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228"),
                chapters=chapters,
                max_workers=workers,
                progress_callback=on_progress,
                cancel_check=self._is_cancelled,
            )
            self.full_outline_cache = outlines
            out_path = Path(self.full_output_path.text().strip() or "outline_from_full.json")
            cache_path = out_path.with_name("chapters_outlines.json")
            cache_path.write_text(json.dumps(outlines, ensure_ascii=False, indent=2), encoding="utf-8")
            log("章纲生成完成")
            log(f"已保存：{cache_path}")

        self._start_worker(task)

    def _run_full_merge(self) -> None:
        title = self.full_title.text().strip() or None
        outlines = self.full_outline_cache
        if not outlines:
            QMessageBox.warning(self, "提示", "请先完成“并行章纲”。")
            return

        def task(log):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")
            log("开始合并 outline...")
            merged = merge_outline(title or "未命名小说", outlines)
            self.full_merged_cache = merged
            out_path = Path(self.full_output_path.text().strip() or "outline_from_full.json")
            cache_path = out_path.with_name("outline_merged.json")
            cache_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"已保存：{cache_path}")

        self._start_worker(task)

    def _run_full_rewrite(self) -> None:
        output_path = self.full_output_path.text().strip()
        if not output_path:
            QMessageBox.warning(self, "提示", "请选择输出文件。")
            return

        def task(log):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")
            out_path = Path(output_path)
            if not out_path.exists():
                merged = self.full_merged_cache
                if not merged:
                    raise ValueError("请先完成“合并”。")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            log("开始重写规整...")
            settings = get_settings()
            client = get_client(settings)
            rewrite_outline(
                str(out_path),
                str(out_path),
                client=client,
                model=os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228"),
                reasoning_effort="high",
                stream_output=False,
                max_tokens=32768,
            )
            log(f"输出完成：{out_path}")

        self._start_worker(task)
    def _run_docx(self) -> None:
        input_path = self.docx_input_path.text().strip()
        output_path = self.docx_output_path.text().strip()
        title = self.docx_title.text().strip() or None
        if not input_path:
            QMessageBox.warning(self, "提示", "请选择输入 DOCX。")
            return
        if not output_path:
            QMessageBox.warning(self, "提示", "请选择输出文件。")
            return

        def task(log):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")
            client = None
            model = None
            try:
                settings = get_settings()
                client = get_client(settings)
                model = os.getenv("NOVELFORGE_MODEL_CONVERT", "doubao-seed-1-8-251228")
            except Exception:
                pass
            log("开始转换 DOCX 大纲...")
            convert_outline(
                input_path,
                output_path,
                title=title,
                client=client,
                model=model,
                mode="outline",
            )
            if self._is_cancelled():
                raise CancelledError("已取消")
            log(f"输出完成：{output_path}")

        self._start_worker(task)


def main() -> int:
    app = QApplication([])
    from PySide6.QtWidgets import QInputDialog
    pwd, ok = QInputDialog.getText(
        None,
        "访问验证",
        "密码",
        QLineEdit.Password,
    )
    if not ok or pwd != monthly_password_hash():
        QMessageBox.warning(None, "验证失败", "密码错误，程序即将退出。")
        return 1
    window = OutlineGeneratorApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
