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
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from novelforge.core.config import get_settings
from novelforge.core.auth import monthly_password_hash
from novelforge.core.llm import get_client
from novelforge.core.memory import MemoryStore
from novelforge.logic.ghostwriter import DEFAULT_LEADIN, generate_chapter, load_outline


class CancelledError(Exception):
    pass


class Worker(QObject):
    finished = Signal(bool, str)
    log = Signal(str)
    progress = Signal(int, int)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.fn(self.log.emit, self.progress.emit)
            self.finished.emit(True, "完成")
        except CancelledError as exc:
            self.finished.emit(True, str(exc))
        except Exception as exc:
            print(f"[app2] error: {exc}")
            self.finished.emit(False, str(exc))


class NovelWriterApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NovelForge - App2 小说生成器")
        self.setMinimumWidth(900)

        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.cancel_requested = False

        root = QWidget()
        layout = QVBoxLayout(root)

        layout.addWidget(self._build_settings())
        layout.addWidget(self._build_tabs())
        layout.addWidget(self._build_progress())
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

        self.model_ghostwriter_input = QLineEdit()
        self.model_ghostwriter_input.setPlaceholderText("NOVELFORGE_MODEL_GHOSTWRITER")
        self.model_ghostwriter_input.setText(os.getenv("NOVELFORGE_MODEL_GHOSTWRITER", "doubao-seed-1-6-flash-250828"))

        self.strict_mode = QCheckBox("严格模式 (NOVELFORGE_STRICT=1)")
        self.strict_mode.setChecked(os.getenv("NOVELFORGE_STRICT", "").strip().lower() in {"1", "true", "yes"})

        grid.addWidget(QLabel("API Key"), 0, 0)
        grid.addWidget(self.api_key_input, 0, 1)
        grid.addWidget(QLabel("Base URL"), 0, 2)
        grid.addWidget(self.base_url_input, 0, 3)
        grid.addWidget(QLabel("写作模型"), 1, 0)
        grid.addWidget(self.model_ghostwriter_input, 1, 1)
        grid.addWidget(self.strict_mode, 1, 2, 1, 2)
        return box

    def _build_tabs(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_write_tab(), "按大纲生成")
        return tabs

    def _build_write_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        outline_row = QHBoxLayout()
        self.outline_path = QLineEdit()
        browse_outline = QPushButton("选择 outline.json")
        browse_outline.clicked.connect(lambda: self._select_input(self.outline_path, "JSON Files (*.json)"))
        outline_row.addWidget(QLabel("大纲文件"))
        outline_row.addWidget(self.outline_path)
        outline_row.addWidget(browse_outline)

        output_row = QHBoxLayout()
        self.output_dir = QLineEdit()
        self.output_dir.setText(str(Path.cwd()))
        browse_out = QPushButton("选择输出目录")
        browse_out.clicked.connect(self._select_dir)
        output_row.addWidget(QLabel("输出目录"))
        output_row.addWidget(self.output_dir)
        output_row.addWidget(browse_out)

        memory_row = QHBoxLayout()
        self.memory_dir = QLineEdit()
        self.memory_dir.setText(str(Path.cwd() / ".novelforge-memory"))
        memory_row.addWidget(QLabel("记忆目录"))
        memory_row.addWidget(self.memory_dir)

        novel_id_row = QHBoxLayout()
        self.novel_id = QLineEdit()
        self.novel_id.setText("default")
        self.novel_id_auto = QCheckBox("使用大纲标题作为 Novel ID")
        self.novel_id_auto.setChecked(True)
        novel_id_row.addWidget(QLabel("Novel ID"))
        novel_id_row.addWidget(self.novel_id)
        novel_id_row.addWidget(self.novel_id_auto)

        chapter_row = QHBoxLayout()
        self.chapter_num = QLineEdit()
        self.chapter_num.setText("1")
        self.chapter_range = QLineEdit()
        self.chapter_range.setPlaceholderText("如 1-5")
        chapter_row.addWidget(QLabel("单章"))
        chapter_row.addWidget(self.chapter_num)
        chapter_row.addWidget(QLabel("范围"))
        chapter_row.addWidget(self.chapter_range)

        window_row = QHBoxLayout()
        self.prev_window = QLineEdit()
        self.prev_window.setText("3")
        self.next_window = QLineEdit()
        self.next_window.setText("3")
        window_row.addWidget(QLabel("前文摘要窗口"))
        window_row.addWidget(self.prev_window)
        window_row.addWidget(QLabel("后文摘要窗口"))
        window_row.addWidget(self.next_window)

        leadin_row = QHBoxLayout()
        self.base_leadin = QLineEdit()
        self.base_leadin.setText(DEFAULT_LEADIN)
        leadin_row.addWidget(QLabel("写作指令首句"))
        leadin_row.addWidget(self.base_leadin)

        run_row = QHBoxLayout()
        run_btn = QPushButton("开始生成")
        run_btn.clicked.connect(self._run_generate)
        run_row.addWidget(run_btn)

        layout.addLayout(outline_row)
        layout.addLayout(output_row)
        layout.addLayout(memory_row)
        layout.addLayout(novel_id_row)
        layout.addLayout(chapter_row)
        layout.addLayout(window_row)
        layout.addLayout(leadin_row)
        layout.addLayout(run_row)
        return widget

    def _build_progress(self) -> QWidget:
        box = QGroupBox("进度")
        layout = QVBoxLayout(box)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        return box

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

    def _select_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", "")
        if path:
            self.output_dir.setText(path)

    def _apply_settings(self) -> None:
        api_key = self.api_key_input.text().strip()
        base_url = self.base_url_input.text().strip()
        model_ghost = self.model_ghostwriter_input.text().strip()

        if api_key:
            os.environ["ARK_API_KEY"] = api_key
        if base_url:
            os.environ["ARK_BASE_URL"] = base_url
        if model_ghost:
            os.environ["NOVELFORGE_MODEL_GHOSTWRITER"] = model_ghost
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
        self.progress_bar.setValue(0)
        self.thread = QThread()
        self.worker = Worker(fn)
        self.worker.moveToThread(self.thread)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._update_progress)
        self.worker.finished.connect(self._on_worker_finished)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()
        self.stop_button.setEnabled(True)

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def _update_progress(self, current: int, total: int) -> None:
        if total <= 0:
            return
        value = int(current / total * 100)
        self.progress_bar.setValue(min(value, 100))

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

    def _parse_range(self, value: str) -> tuple[int, int]:
        if "-" not in value:
            raise ValueError("章节范围必须是 start-end 格式")
        start_str, end_str = value.split("-", 1)
        start = int(start_str)
        end = int(end_str)
        if start <= 0 or end <= 0 or end < start:
            raise ValueError("章节范围必须为正数且 end >= start")
        return start, end

    def _run_generate(self) -> None:
        outline_path = self.outline_path.text().strip()
        output_dir = self.output_dir.text().strip()
        memory_dir = self.memory_dir.text().strip()
        novel_id = self.novel_id.text().strip()
        chapter_num = self.chapter_num.text().strip()
        chapter_range = self.chapter_range.text().strip()
        base_leadin = self.base_leadin.text().strip() or DEFAULT_LEADIN
        try:
            prev_window = int(self.prev_window.text().strip() or "3")
            next_window = int(self.next_window.text().strip() or "3")
        except ValueError:
            QMessageBox.warning(self, "提示", "窗口大小必须是整数。")
            return

        if not outline_path:
            QMessageBox.warning(self, "提示", "请选择 outline.json。")
            return
        if not output_dir:
            QMessageBox.warning(self, "提示", "请选择输出目录。")
            return

        def task(log, progress):
            self._apply_settings()
            if self._is_cancelled():
                raise CancelledError("已取消")

            settings = get_settings()
            client = get_client(settings)
            outline = load_outline(outline_path)
            if self.novel_id_auto.isChecked():
                auto_id = (outline.title or "").strip()
                if auto_id:
                    novel_id_local = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in auto_id).strip() or "default"
                else:
                    novel_id_local = "default"
            else:
                novel_id_local = novel_id or "default"
            chapter_map = {c.chapter_num: c for v in outline.volumes for c in v.chapters}
            if chapter_range:
                start, end = self._parse_range(chapter_range)
                chapter_numbers = list(range(start, end + 1))
            else:
                if not chapter_num:
                    raise ValueError("请提供单章或范围。")
                chapter_numbers = [int(chapter_num)]

            memory_store = MemoryStore(Path(memory_dir))
            novel_title = outline.title or "Untitled"
            safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in novel_title).strip()
            if not safe_title:
                safe_title = "Untitled"
            base_dir = Path(output_dir) / safe_title
            base_dir.mkdir(parents=True, exist_ok=True)

            total = len(chapter_numbers)
            for idx, ch_num in enumerate(chapter_numbers, start=1):
                if self._is_cancelled():
                    raise CancelledError("已取消")
                chapter_node = chapter_map.get(ch_num)
                if not chapter_node:
                    log(f"未找到第 {ch_num} 章，跳过")
                    progress(idx, total)
                    continue

                def stream_handler(_text: str) -> None:
                    return

                result = generate_chapter(
                    client,
                    settings.model_ghostwriter,
                    chapter_node,
                    memory_store,
                    novel_id_local,
                    stream_handler,
                    str(base_dir.parent),
                    novel_title,
                    base_leadin=base_leadin,
                    prev_window=prev_window,
                    next_window=next_window,
                )

                filename = f"chapter-{chapter_node.chapter_num:03d}.md"
                path = base_dir / filename
                content = f"# {chapter_node.title}\n\n{result['chapter_text']}\n"
                path.write_text(content, encoding="utf-8")
                log(f"已生成：{path}")
                progress(idx, total)

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
    window = NovelWriterApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
