"""Gemini TTS 工具集 Tkinter 图形界面。"""
from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, List, Optional, Tuple

from igtts import (
    __version__,
    MODEL_KEY,
    DEFAULT_CONFIG,
    DEFAULT_VOICES,
    configure_logging,
    fetch_available_voices,
    gemini_tts,
    load_config,
    save_config,
)

SINGLE_TEXT_HEIGHT = 8
BATCH_ROW_TEXT_HEIGHT = 4
LOG_TEXT_HEIGHT = 6
MAX_LOG_LINES = 500
VOICE_LABEL = "音色 / ID"
STATUS_READY = "已准备完毕。"
BATCH_FILENAME_TEMPLATE = "{stem}_{index:03d}{suffix}"
GUI_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DEFAULT_BATCH_ROWS = 3
BATCH_HINT = (
    "每个条目可独立填写文本、音色 ID 与输出路径；未填写输出时将按照默认输出自动追加序号。"
)


def _escape_field(value: str) -> str:
    """将字段内容编码为批量文件格式。"""
    buffer: List[str] = []
    for ch in value:
        if ch == "\n":
            buffer.append("\\n")
        elif ch == "\\":
            buffer.append("\\\\")
        elif ch == "|":
            buffer.append("\\|")
        else:
            buffer.append(ch)
    return "".join(buffer)


def _unescape_field(value: str) -> str:
    """还原批量文件中的转义字段。"""
    buffer: List[str] = []
    i = 0
    length = len(value)
    while i < length:
        ch = value[i]
        if ch == "\\" and i + 1 < length:
            nxt = value[i + 1]
            if nxt == "n":
                buffer.append("\n")
            elif nxt in {"|", "\\"}:
                buffer.append(nxt)
            else:
                buffer.append(nxt)
            i += 2
            continue
        buffer.append(ch)
        i += 1
    return "".join(buffer)


def _split_escaped_line(line: str) -> List[str]:
    """在保留转义的前提下按竖线拆分。"""
    fields: List[str] = []
    current: List[str] = []
    escape = False
    for ch in line:
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == "\\":
            current.append(ch)
            escape = True
            continue
        if ch == "|":
            fields.append("".join(current))
            current = []
            continue
        current.append(ch)
    fields.append("".join(current))
    return fields

@dataclass
class BatchRow:
    """批量模式中单个任务的控件集合。"""

    frame: ttk.Frame
    index_label: ttk.Label
    text_widget: tk.Text
    voice_var: tk.StringVar
    voice_entry: ttk.Entry
    output_var: tk.StringVar
    output_entry: ttk.Entry
    browse_button: ttk.Button
    remove_button: ttk.Button


class GuiLogHandler(logging.Handler):
    """将模块日志投递到界面日志面板。"""

    def __init__(self, app: "GeminiTTSApp") -> None:
        super().__init__()
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        """处理单条日志并推送到 UI 队列。"""
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.app.queue_log_message(message)


class GeminiTTSApp:
    """Gemini 文本转语音的图形化主界面。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Gemini TTS 工具集 v{__version__}")
        self.root.geometry("840x680")
        self.root.minsize(820, 640)

        self.config = load_config()
        configure_logging(self.config)

        self.voices: List[Dict[str, str]] = self.config.get("voices", []) or DEFAULT_VOICES
        self.voice_map: Dict[str, str] = {
            (item.get("label") or item.get("id", "")): item.get("id", "")
            for item in self.voices
            if item.get("label") or item.get("id")
        }

        self.status_var = tk.StringVar(value=STATUS_READY)
        self.voice_var = tk.StringVar()
        self.manual_voice_var = tk.StringVar(value=str(self.config.get("default_voice", "")))
        self.output_path_var = tk.StringVar(
            value=str(self.config.get("default_output", DEFAULT_CONFIG["default_output"]))
        )
        self.multi_delay_var = tk.DoubleVar(
            value=float(self.config.get("multi_delay_seconds", DEFAULT_CONFIG["multi_delay_seconds"]))
        )

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self._log_messages: List[str] = []
        self._busy_workers = 0
        self._batch_running = False
        self._cancel_batch = False

        self._single_worker: Optional[threading.Thread] = None
        self._batch_worker: Optional[threading.Thread] = None
        self._refresh_thread: Optional[threading.Thread] = None
        self._log_handler: Optional[GuiLogHandler] = None

        self.batch_rows: List[BatchRow] = []
        self.batch_canvas: Optional[tk.Canvas] = None
        self.batch_items_frame: Optional[ttk.Frame] = None
        self.batch_items_window: Optional[int] = None
        self.batch_scrollbar: Optional[ttk.Scrollbar] = None

        self.add_row_button: Optional[ttk.Button] = None
        self.batch_generate_btn: Optional[ttk.Button] = None
        self.batch_stop_btn: Optional[ttk.Button] = None
        self.delay_spin: Optional[ttk.Spinbox] = None
        self.batch_import_btn: Optional[ttk.Button] = None
        self.batch_export_btn: Optional[ttk.Button] = None
        self.toolbar_settings_btn: Optional[ttk.Button] = None
        self.toolbar_refresh_btn: Optional[ttk.Button] = None

        self._build_ui()
        self._setup_logging()
        self._populate_voice_options()
        self._load_default_text()
        self._ensure_initial_batch_rows()
        self._set_status(self._status_message_from_config())
        self._refresh_voice_list_async()

        self.root.after(200, self._flush_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    def _build_ui(self) -> None:
        """创建主界面布局。"""
        self._build_toolbar()
        self._build_tabs()
        self._build_log_panel()
        self._build_status_bar()
        self._update_controls_state()

    def _build_toolbar(self) -> None:
        """构建顶端工具条，让设置入口更醒目。"""
        toolbar = ttk.Frame(self.root, padding=(12, 8, 12, 0))
        toolbar.pack(fill=tk.X)
        settings_btn = ttk.Button(toolbar, text="打开设置", command=self._open_settings_dialog)
        settings_btn.pack(side=tk.LEFT)
        refresh_btn = ttk.Button(
            toolbar,
            text="刷新音色",
            command=lambda: self._refresh_voice_list_async(force_refresh=True),
        )
        refresh_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.toolbar_settings_btn = settings_btn
        self.toolbar_refresh_btn = refresh_btn

    def _build_tabs(self) -> None:
        """创建单次与批量两个标签页。"""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        self.single_tab = ttk.Frame(self.notebook)
        self.batch_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.single_tab, text="单次模式")
        self.notebook.add(self.batch_tab, text="批量模式")

        self._build_single_tab()
        self._build_batch_tab()

    def _build_single_tab(self) -> None:
        """构造单次合成的输入区域。"""
        frame = ttk.Frame(self.single_tab, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(frame, text="输入文本").grid(row=0, column=0, sticky=tk.W, pady=(0, 4))

        file_controls = ttk.Frame(frame)
        file_controls.grid(row=0, column=1, sticky=tk.E, pady=(0, 4))
        ttk.Button(file_controls, text="保存文本...", command=self._on_save_text).pack(side=tk.RIGHT)
        ttk.Button(file_controls, text="打开文本...", command=self._on_load_text).pack(side=tk.RIGHT, padx=(8, 0))

        self.single_text = ScrolledText(frame, height=SINGLE_TEXT_HEIGHT, wrap=tk.WORD)
        self.single_text.grid(row=1, column=0, columnspan=2, sticky=tk.NSEW)

        ttk.Label(frame, text=VOICE_LABEL).grid(row=2, column=0, sticky=tk.W, pady=(8, 4))

        voice_frame = ttk.Frame(frame)
        voice_frame.grid(row=2, column=1, sticky=tk.EW, pady=(8, 4))
        voice_frame.columnconfigure(0, weight=1)

        self.voice_combo = ttk.Combobox(voice_frame, textvariable=self.voice_var, state="readonly")
        self.voice_combo.grid(row=0, column=0, sticky=tk.EW)
        self.voice_combo.bind("<<ComboboxSelected>>", self._on_voice_selected)

        ttk.Label(voice_frame, text="也可手动输入 ID：").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        self.manual_voice_entry = ttk.Entry(voice_frame, textvariable=self.manual_voice_var)
        self.manual_voice_entry.grid(row=2, column=0, sticky=tk.EW)

        ttk.Label(frame, text="输出 WAV").grid(row=3, column=0, sticky=tk.W, pady=(8, 4))

        output_frame = ttk.Frame(frame)
        output_frame.grid(row=3, column=1, sticky=tk.EW, pady=(8, 4))
        output_frame.columnconfigure(0, weight=1)

        self.output_entry = ttk.Entry(output_frame, textvariable=self.output_path_var)
        self.output_entry.grid(row=0, column=0, sticky=tk.EW)
        ttk.Button(output_frame, text="浏览...", command=self._browse_output_path).grid(row=0, column=1, padx=(8, 0))

        action_row = ttk.Frame(frame)
        action_row.grid(row=4, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        self.single_generate_btn = ttk.Button(action_row, text="生成声音", command=self._on_single_generate)
        self.single_generate_btn.pack(side=tk.RIGHT)

    def _build_batch_tab(self) -> None:
        """构造批量任务设置区域。"""
        frame = ttk.Frame(self.batch_tab, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        ttk.Label(frame, text=BATCH_HINT, wraplength=760, justify=tk.LEFT).grid(row=0, column=0, sticky=tk.W)

        tools_row = ttk.Frame(frame)
        tools_row.grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.batch_import_btn = ttk.Button(tools_row, text="导入条目...", command=self._on_import_batch_tasks)
        self.batch_import_btn.pack(side=tk.LEFT)
        self.batch_export_btn = ttk.Button(tools_row, text="导出条目...", command=self._on_export_batch_tasks)
        self.batch_export_btn.pack(side=tk.LEFT, padx=(6, 0))

        scroll_area = ttk.Frame(frame)
        scroll_area.grid(row=2, column=0, sticky=tk.NSEW, pady=(8, 0))
        scroll_area.columnconfigure(0, weight=1)
        scroll_area.rowconfigure(0, weight=1)

        self.batch_canvas = tk.Canvas(scroll_area, highlightthickness=0)
        self.batch_canvas.grid(row=0, column=0, sticky=tk.NSEW)
        self.batch_scrollbar = ttk.Scrollbar(scroll_area, orient=tk.VERTICAL, command=self.batch_canvas.yview)
        self.batch_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.batch_canvas.configure(yscrollcommand=self.batch_scrollbar.set)

        self.batch_items_frame = ttk.Frame(self.batch_canvas)
        self.batch_items_window = self.batch_canvas.create_window((0, 0), window=self.batch_items_frame, anchor="nw")

        self.batch_items_frame.bind(
            "<Configure>", lambda _: self.batch_canvas.configure(scrollregion=self.batch_canvas.bbox("all"))
        )
        self.batch_canvas.bind("<Configure>", self._on_batch_canvas_configure)

        delay_row = ttk.Frame(frame)
        delay_row.grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Label(delay_row, text="批量延迟 (s)").pack(side=tk.LEFT)
        self.delay_spin = ttk.Spinbox(
            delay_row,
            textvariable=self.multi_delay_var,
            from_=0.0,
            to=60.0,
            increment=0.5,
            width=8,
        )
        self.delay_spin.pack(side=tk.LEFT, padx=(8, 0))

        buttons_row = ttk.Frame(frame)
        buttons_row.grid(row=4, column=0, sticky=tk.E, pady=(12, 0))
        self.add_row_button = ttk.Button(buttons_row, text="新增条目", command=self._on_add_batch_row)
        self.add_row_button.pack(side=tk.RIGHT)
        self.batch_stop_btn = ttk.Button(buttons_row, text="停止", command=self._on_cancel_batch, state=tk.DISABLED)
        self.batch_stop_btn.pack(side=tk.RIGHT, padx=(0, 8))
        self.batch_generate_btn = ttk.Button(buttons_row, text="批量生成", command=self._on_batch_generate)
        self.batch_generate_btn.pack(side=tk.RIGHT, padx=(0, 8))
    def _build_log_panel(self) -> None:
        """创建日志展示区域。"""
        frame = ttk.LabelFrame(self.root, text="日志")
        frame.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0, 12))
        self.log_text = ScrolledText(frame, height=LOG_TEXT_HEIGHT, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_status_bar(self) -> None:
        """放置底部状态栏。"""
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W)
        status.pack(fill=tk.X, padx=12, pady=(0, 12))

    def _setup_logging(self) -> None:
        """附加 GUI 日志处理器。"""
        logger = logging.getLogger("igtts")
        handler = GuiLogHandler(self)
        handler.setFormatter(logging.Formatter(GUI_LOG_FORMAT))
        logger.addHandler(handler)
        self._log_handler = handler

    def queue_log_message(self, message: str) -> None:
        """推送日志消息到界面队列。"""
        self.log_queue.put(message)

    def _flush_log_queue(self) -> None:
        """刷新界面日志内容。"""
        updated = False
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._log_messages.append(message)
            updated = True

        if updated:
            if len(self._log_messages) > MAX_LOG_LINES:
                self._log_messages = self._log_messages[-MAX_LOG_LINES:]
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.delete("1.0", tk.END)
            self.log_text.insert(tk.END, "\n".join(self._log_messages) + "\n")
            self.log_text.configure(state=tk.DISABLED)
            self.log_text.see(tk.END)

        self.root.after(200, self._flush_log_queue)

    def _set_status(self, message: str) -> None:
        """更新状态栏文字。"""
        self.status_var.set(message)

    def _status_message_from_config(self) -> str:
        """根据配置生成状态提示。"""
        default_voice = self.config.get("default_voice", DEFAULT_CONFIG["default_voice"])
        default_output = self.config.get("default_output", DEFAULT_CONFIG["default_output"])
        return f"默认音色: {default_voice} | 默认输出: {default_output}"

    def _schedule(self, callback, *args) -> None:
        """将回调调度回 Tk 事件循环。"""
        self.root.after(0, lambda: callback(*args))

    def _notify_status(self, message: str) -> None:
        """在线程中安全更新状态栏。"""
        self._schedule(self._set_status, message)

    def _populate_voice_options(self) -> None:
        """刷新音色下拉框。"""
        labels = list(self.voice_map.keys())
        labels.sort()
        self.voice_combo["values"] = labels

        target_id = self.manual_voice_var.get() or self.config.get("default_voice", "")
        label = self._label_for_voice_id(target_id)
        if label:
            self.voice_var.set(label)
        elif labels:
            first_label = labels[0]
            self.voice_var.set(first_label)
            self.manual_voice_var.set(self.voice_map[first_label])

    def _label_for_voice_id(self, voice_id: Optional[str]) -> Optional[str]:
        """根据音色 ID 定位下拉显示文本。"""
        if not voice_id:
            return None
        for label, mapped_id in self.voice_map.items():
            if mapped_id == voice_id:
                return label
        return None

    def _load_default_text(self) -> None:
        """尝试加载配置中的默认文本文件。"""
        path = Path(self.config.get("input_text_path", DEFAULT_CONFIG["input_text_path"]))
        if not path.exists():
            return
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.queue_log_message(f"无法读取默认文本: {exc}")
            return
        if content.strip():
            self.single_text.delete("1.0", tk.END)
            self.single_text.insert(tk.END, content)

    def _get_selected_voice_id(self) -> str:
        """优先返回手动输入的音色 ID。"""
        manual = self.manual_voice_var.get().strip()
        if manual:
            return manual
        label = self.voice_var.get()
        if label:
            return self.voice_map.get(label, "")
        return self.config.get("default_voice", DEFAULT_CONFIG["default_voice"])

    def _update_controls_state(self) -> None:
        """根据任务状态统一开关所有按钮。"""
        if self._batch_running:
            self.single_generate_btn.config(state=tk.DISABLED)
            self.batch_generate_btn.config(state=tk.DISABLED)
            self.batch_stop_btn.config(state=tk.NORMAL)
            if self.add_row_button:
                self.add_row_button.config(state=tk.DISABLED)
            if self.batch_import_btn:
                self.batch_import_btn.config(state=tk.DISABLED)
            if self.batch_export_btn:
                self.batch_export_btn.config(state=tk.DISABLED)
            self._update_batch_indices()
            return

        disabled = tk.DISABLED if self._busy_workers else tk.NORMAL
        self.single_generate_btn.config(state=disabled)
        self.batch_generate_btn.config(state=disabled)
        self.batch_stop_btn.config(state=tk.DISABLED)
        if self.add_row_button:
            self.add_row_button.config(state=disabled)
        if self.batch_import_btn:
            self.batch_import_btn.config(state=disabled)
        if self.batch_export_btn:
            export_state = disabled if self.batch_rows else tk.DISABLED
            self.batch_export_btn.config(state=export_state)
        self._update_batch_indices()

    def _enter_busy(self) -> None:
        """标记当前存在后台任务。"""
        self._busy_workers += 1
        self._update_controls_state()

    def _leave_busy(self) -> None:
        """后台任务结束后恢复控件状态。"""
        self._busy_workers = max(0, self._busy_workers - 1)
        self._update_controls_state()

    def _on_voice_selected(self, event: tk.Event) -> None:  # pragma: no cover - UI 回调
        """同步下拉框与手动输入框的音色 ID。"""
        voice_id = self.voice_map.get(self.voice_var.get(), "")
        if voice_id:
            self.manual_voice_var.set(voice_id)

    def _browse_output_path(self) -> None:
        """选择单次合成的输出路径。"""
        current = Path(self.output_path_var.get() or DEFAULT_CONFIG["default_output"])
        try:
            initial_dir = current.resolve().parent
        except Exception:
            initial_dir = Path.cwd()
        selected = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=".wav",
            filetypes=[("WAV", "*.wav"), ("All Files", "*.*")],
            initialdir=str(initial_dir),
            initialfile=current.name,
        )
        if selected:
            self.output_path_var.set(selected)

    def _on_load_text(self) -> None:
        """从磁盘加载文本内容。"""
        initial = Path(self.config.get("input_text_path", DEFAULT_CONFIG["input_text_path"]))
        try:
            initial_dir = initial.resolve().parent
        except Exception:
            initial_dir = Path.cwd()
        filename = filedialog.askopenfilename(
            title="选择文本文件",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            initialdir=str(initial_dir),
        )
        if not filename:
            return
        try:
            content = Path(filename).read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("加载失败", f"无法读取文件: {exc}")
            return
        self.single_text.delete("1.0", tk.END)
        self.single_text.insert(tk.END, content)
        self.config["input_text_path"] = filename
        save_config(self.config)
        self.queue_log_message(f"已加载文本文件: {filename}")

    def _on_save_text(self) -> None:
        """将当前文本编辑内容保存到本地。"""
        current = Path(self.config.get("input_text_path", DEFAULT_CONFIG["input_text_path"]))
        try:
            initial_dir = current.resolve().parent
        except Exception:
            initial_dir = Path.cwd()
        selected = filedialog.asksaveasfilename(
            title="保存文本文件",
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            initialdir=str(initial_dir),
            initialfile=current.name,
        )
        if not selected:
            return
        content = self.single_text.get("1.0", tk.END)
        try:
            Path(selected).write_text(content, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法写入文件: {exc}")
            return
        self.config["input_text_path"] = selected
        save_config(self.config)
        self.queue_log_message(f"已保存文本到 {selected}")
    def _on_single_generate(self) -> None:
        """触发单次语音生成。"""
        if self._single_worker and self._single_worker.is_alive():
            messagebox.showinfo("正在处理", "当前请求仍在执行中。")
            return

        text = self.single_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("缺少内容", "请输入需要转换的文本。")
            return

        voice_id = self._get_selected_voice_id()
        if not voice_id:
            messagebox.showwarning("缺少音色", "请选择或输入音色 ID。")
            return

        output_path = self.output_path_var.get().strip() or self.config.get(
            "default_output", DEFAULT_CONFIG["default_output"]
        )
        if not output_path:
            messagebox.showwarning("缺少输出", "请提供 WAV 保存路径。")
            return

        self.config["default_voice"] = voice_id
        self.config["default_output"] = output_path
        save_config(self.config)

        self._set_status("正在生成声音...")
        self._enter_busy()

        def worker() -> None:
            start = time.time()
            try:
                gemini_tts(text, voice_id, output_path, config=self.config)
            except Exception as exc:
                self.queue_log_message(f"单条任务失败: {exc}")
                self._notify_status("生成失败，请查看日志。")
                self._schedule(lambda err=exc: messagebox.showerror("生成失败", f"单次任务失败：{err}"))
            else:
                duration = time.time() - start
                self.queue_log_message(f"单条任务完成，用时 {duration:.2f}s -> {output_path}")
                self._notify_status("生成完成。")
                self._schedule(lambda: messagebox.showinfo("生成完成", f"音频已保存至 {output_path}"))
            finally:
                self._schedule(self._leave_busy)

        self._single_worker = threading.Thread(target=worker, daemon=True)
        self._single_worker.start()

    def _ensure_initial_batch_rows(self) -> None:
        """确保批量面板至少提供默认条目。"""
        if self.batch_rows:
            return
        for _ in range(DEFAULT_BATCH_ROWS):
            self._add_batch_row()
        self._update_controls_state()

    def _clear_batch_rows(self) -> None:
        """移除所有批量条目控件。"""
        for row in self.batch_rows:
            row.frame.destroy()
        self.batch_rows.clear()

    def _add_batch_row(self, text: str = "", voice: Optional[str] = None, output: str = "") -> None:
        """新增一条批量任务。"""
        if self.batch_items_frame is None:
            return

        row_frame = ttk.Frame(self.batch_items_frame, padding=8, relief=tk.GROOVE)
        row_frame.pack(fill=tk.X, expand=True, pady=(0, 8))
        row_frame.columnconfigure(1, weight=1)

        header = ttk.Frame(row_frame)
        header.grid(row=0, column=0, columnspan=3, sticky=tk.EW)
        header.columnconfigure(0, weight=1)

        index_label = ttk.Label(header, text="")
        index_label.grid(row=0, column=0, sticky=tk.W)

        remove_button = ttk.Button(header, text="删除")
        remove_button.grid(row=0, column=2, sticky=tk.E)

        text_widget = tk.Text(row_frame, height=BATCH_ROW_TEXT_HEIGHT, wrap=tk.WORD)
        text_widget.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW, pady=(6, 6))
        row_frame.rowconfigure(1, weight=1)
        if text:
            text_widget.insert(tk.END, text)

        voice_var = tk.StringVar(value=voice if voice is not None else self.manual_voice_var.get())
        ttk.Label(row_frame, text="音色 ID").grid(row=2, column=0, sticky=tk.W)
        voice_entry = ttk.Entry(row_frame, textvariable=voice_var)
        voice_entry.grid(row=2, column=1, columnspan=2, sticky=tk.EW, padx=(8, 0))

        output_var = tk.StringVar(value=output)
        ttk.Label(row_frame, text="输出路径").grid(row=3, column=0, sticky=tk.W, pady=(6, 0))
        output_entry = ttk.Entry(row_frame, textvariable=output_var)
        output_entry.grid(row=3, column=1, sticky=tk.EW, padx=(8, 0), pady=(6, 0))
        browse_button = ttk.Button(row_frame, text="浏览...")
        browse_button.grid(row=3, column=2, sticky=tk.W, pady=(6, 0))

        row = BatchRow(
            frame=row_frame,
            index_label=index_label,
            text_widget=text_widget,
            voice_var=voice_var,
            voice_entry=voice_entry,
            output_var=output_var,
            output_entry=output_entry,
            browse_button=browse_button,
            remove_button=remove_button,
        )

        browse_button.configure(command=lambda: self._browse_row_output(row))
        remove_button.configure(command=lambda: self._remove_batch_row(row))

        self.batch_rows.append(row)
        self._update_controls_state()

    def _on_add_batch_row(self) -> None:
        """批量面板中点击新增时的处理。"""
        self._add_batch_row()

    def _browse_row_output(self, row: BatchRow) -> None:
        """为批量条目指定输出位置。"""
        current_text = row.output_var.get().strip() or self.output_path_var.get().strip()
        fallback = self.config.get("default_output", DEFAULT_CONFIG["default_output"])
        current = Path(current_text or fallback)
        try:
            initial_dir = current.resolve().parent
        except Exception:
            initial_dir = Path.cwd()
        selected = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=".wav",
            filetypes=[("WAV", "*.wav"), ("All Files", "*.*")],
            initialdir=str(initial_dir),
            initialfile=current.name,
        )
        if selected:
            row.output_var.set(selected)

    def _remove_batch_row(self, target: BatchRow) -> None:
        """删除指定批量条目。"""
        if self._batch_running or self._busy_workers:
            messagebox.showinfo("提示", "任务执行过程中无法删除条目。")
            return
        if len(self.batch_rows) <= 1:
            messagebox.showinfo("提示", "至少需要保留一个批量条目。")
            return
        target.frame.destroy()
        self.batch_rows = [row for row in self.batch_rows if row is not target]
        self._update_controls_state()

    def _update_batch_indices(self) -> None:
        """刷新批量条目的编号与删除按钮状态。"""
        disable_removal = len(self.batch_rows) <= 1 or self._batch_running or self._busy_workers
        for idx, row in enumerate(self.batch_rows, start=1):
            row.index_label.config(text=f"条目 {idx}")
            row.remove_button.config(state=tk.DISABLED if disable_removal else tk.NORMAL)

    def _collect_batch_rows(self, *, include_empty: bool = False) -> List[Tuple[str, str, str]]:
        """收集批量条目的当前值。"""
        entries: List[Tuple[str, str, str]] = []
        for row in self.batch_rows:
            text = row.text_widget.get("1.0", tk.END).strip()
            voice = row.voice_var.get().strip()
            output = row.output_var.get().strip()
            if text or voice or output or include_empty:
                entries.append((text, voice, output))
        return entries

    def _on_import_batch_tasks(self) -> None:
        """从文件导入批量配置。"""
        stored = self.config.get("batch_tasks_path", "")
        if stored:
            try:
                initial_dir = str(Path(stored).resolve().parent)
            except Exception:
                initial_dir = str(Path.cwd())
        else:
            try:
                initial_dir = str(Path(self.config.get("default_output", DEFAULT_CONFIG["default_output"])).resolve().parent)
            except Exception:
                initial_dir = str(Path.cwd())
        filename = filedialog.askopenfilename(
            title="导入批量条目",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            initialdir=initial_dir,
        )
        if not filename:
            return
        try:
            content = Path(filename).read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("导入失败", f"无法读取文件: {exc}")
            return

        tasks: List[Tuple[str, str, str]] = []
        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = [_unescape_field(part).strip() for part in _split_escaped_line(stripped)]
            text = parts[0] if parts else ""
            voice = parts[1] if len(parts) > 1 else ""
            output = parts[2] if len(parts) > 2 else ""
            if not text and not voice and not output:
                continue
            tasks.append((text, voice, output))

        self._clear_batch_rows()
        if tasks:
            for text, voice, output in tasks:
                self._add_batch_row(text=text, voice=voice, output=output)
        self._ensure_initial_batch_rows()
        self.config["batch_tasks_path"] = filename
        save_config(self.config)
        self.queue_log_message(f"已导入批量条目: {filename}")

    def _on_export_batch_tasks(self) -> None:
        """将当前批量配置导出到文本文件。"""
        entries = self._collect_batch_rows()
        if not entries:
            messagebox.showinfo("提示", "当前没有可导出的条目。")
            return

        stored = self.config.get("batch_tasks_path", "")
        if stored:
            try:
                initial_dir = str(Path(stored).resolve().parent)
                initial_file = Path(stored).name
            except Exception:
                initial_dir = str(Path.cwd())
                initial_file = "batch_tasks.txt"
        else:
            try:
                default_output = Path(self.config.get("default_output", DEFAULT_CONFIG["default_output"]))
                initial_dir = str(default_output.resolve().parent)
            except Exception:
                initial_dir = str(Path.cwd())
            initial_file = "batch_tasks.txt"

        selected = filedialog.asksaveasfilename(
            title="导出批量条目",
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            initialdir=initial_dir,
            initialfile=initial_file,
        )
        if not selected:
            return

        lines: List[str] = []
        for text, voice, output in entries:
            parts = [_escape_field(text)]
            if voice:
                parts.append(_escape_field(voice))
            if output:
                parts.append(_escape_field(output))
            lines.append(" | ".join(parts))

        try:
            Path(selected).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("导出失败", f"无法写入文件: {exc}")
            return

        self.config["batch_tasks_path"] = selected
        save_config(self.config)
        self.queue_log_message(f"批量条目已导出至 {selected}")
    def _on_batch_canvas_configure(self, event: tk.Event) -> None:  # pragma: no cover - UI 回调
        """在画布尺寸变化时同步内部容器宽度。"""
        if self.batch_canvas and self.batch_items_window is not None:
            self.batch_canvas.itemconfigure(self.batch_items_window, width=event.width)

    def _parse_batch_entries(self) -> List[Tuple[str, str, str]]:
        """将批量设置转换为可执行的任务列表。"""
        entries: List[Tuple[str, str, str]] = []

        base_path = Path(self.config.get("default_output", DEFAULT_CONFIG["default_output"]))
        stem = base_path.stem or "output"
        suffix = base_path.suffix or ".wav"

        for display_index, row in enumerate(self.batch_rows, start=1):
            text = row.text_widget.get("1.0", tk.END).strip()
            voice_id = row.voice_var.get().strip()
            output_path = row.output_var.get().strip()

            if not text:
                continue

            if not voice_id:
                voice_id = self._get_selected_voice_id()
            if not voice_id:
                raise ValueError(f"条目 {display_index} 缺少音色 ID。")

            entry_index = len(entries) + 1
            if not output_path:
                generated = BATCH_FILENAME_TEMPLATE.format(stem=stem, index=entry_index, suffix=suffix)
                output_path = str(base_path.with_name(generated))

            entries.append((text, voice_id, output_path))

        return entries

    def _on_batch_generate(self) -> None:
        """触发批量任务执行。"""
        if self._batch_worker and self._batch_worker.is_alive():
            messagebox.showinfo("正在处理", "批量任务已处于执行状态。")
            return

        try:
            entries = self._parse_batch_entries()
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc))
            return

        if not entries:
            messagebox.showinfo("提示", "请至少提供一条有效任务。")
            return

        try:
            delay = max(0.0, float(self.multi_delay_var.get()))
        except (TypeError, ValueError):
            messagebox.showerror("配置错误", "延迟值必须为数字。")
            return

        self.multi_delay_var.set(delay)
        self.config["multi_delay_seconds"] = delay
        save_config(self.config)

        self._batch_running = True
        self._cancel_batch = False
        self._enter_busy()
        self._set_status("正在处理批量任务...")
        self._update_controls_state()

        total_count = len(entries)
        outcome = {"errors": 0, "cancelled": False, "total": total_count}

        def worker() -> None:
            for index, (text, voice_id, output_path) in enumerate(entries, start=1):
                if self._cancel_batch:
                    outcome["cancelled"] = True
                    self.queue_log_message("批量任务已被用户取消。")
                    break
                try:
                    gemini_tts(text, voice_id, output_path, config=self.config)
                except Exception as exc:
                    outcome["errors"] += 1
                    self.queue_log_message(f"批量条目 {index} 失败: {exc}")
                else:
                    self.queue_log_message(f"批量条目 {index} -> {output_path}")
                if self._cancel_batch:
                    outcome["cancelled"] = True
                    break
                if delay and index < total_count:
                    time.sleep(delay)
            else:
                self._notify_status("批量任务已完成。")

            if outcome.get("cancelled"):
                self._notify_status("批量任务已取消。")

            self._schedule(lambda: self._finalize_batch(outcome))

        self._batch_worker = threading.Thread(target=worker, daemon=True)
        self._batch_worker.start()

    def _on_cancel_batch(self) -> None:
        """请求终止批量任务。"""
        if not self._batch_running:
            return
        self._cancel_batch = True
        self._set_status("正在请求停止...")

    def _finish_batch(self) -> None:
        """批量任务收尾逻辑。"""
        self._batch_running = False
        self._cancel_batch = False
        self._leave_busy()

    def _finalize_batch(self, outcome: Dict[str, Any]) -> None:
        """批量任务结束后提示结果。"""
        self._finish_batch()
        total = outcome.get("total", 0)
        if outcome.get("cancelled"):
            messagebox.showinfo("批量任务", "批量任务已取消。")
        elif outcome.get("errors"):
            messagebox.showerror("批量任务", "批量任务完成，其中 {errors} / {total} 条失败。".format(errors=outcome["errors"], total=total))
        else:
            messagebox.showinfo("批量任务", "批量任务已完成。")
    def _refresh_voice_list_async(self, *, force_refresh: bool = False) -> None:
        """在后台刷新可用音色列表。"""
        if self._refresh_thread and self._refresh_thread.is_alive():
            self.queue_log_message("音色刷新任务已在进行，稍候即可。")
            return

        def worker() -> None:
            self._notify_status("正在刷新音色...")
            try:
                voices = fetch_available_voices(self.config, force_refresh=force_refresh)
            except Exception as exc:
                self.queue_log_message(f"刷新音色失败: {exc}")
            else:
                self._schedule(self._update_voice_list, voices)
                self.queue_log_message("音色列表已刷新。")
            finally:
                self._notify_status(self._status_message_from_config())

        self._refresh_thread = threading.Thread(target=worker, daemon=True)
        self._refresh_thread.start()

    def _update_voice_list(self, voices: Optional[List[Dict[str, str]]]) -> None:
        """将最新音色写入 UI 和配置缓存。"""
        voices = voices or DEFAULT_VOICES
        voice_map: Dict[str, str] = {}
        for item in voices:
            voice_id = item.get("id", "")
            label = item.get("label") or voice_id
            if label in voice_map:
                label = f"{label} ({voice_id})"
            voice_map[label] = voice_id

        self.voices = voices
        self.voice_map = voice_map
        self._populate_voice_options()

        self.config["voices"] = voices
        save_config(self.config)

    def apply_settings(self, updates: Dict[str, object]) -> None:
        """应用设置对话框返回的变更。"""
        self.config.update(updates)
        self.config["version"] = __version__
        save_config(self.config)
        configure_logging(self.config)

        self.manual_voice_var.set(str(self.config.get("default_voice", "")))
        self.output_path_var.set(str(self.config.get("default_output", DEFAULT_CONFIG["default_output"])))
        self.multi_delay_var.set(float(self.config.get("multi_delay_seconds", DEFAULT_CONFIG["multi_delay_seconds"])))
        self._populate_voice_options()
        self._set_status("设置已保存。")


    def _open_settings_dialog(self) -> None:
        """弹出设置对话框。"""
        SettingsDialog(self.root, self)

    def _on_close(self) -> None:
        """窗口关闭时清理资源。"""
        self._cancel_batch = True
        if self._log_handler:
            logger = logging.getLogger("igtts")
            logger.removeHandler(self._log_handler)
            self._log_handler = None
        self.root.destroy()

class SettingsDialog:
    """用于编辑运行配置的模态对话框。"""

    def __init__(self, master: tk.Tk, app: GeminiTTSApp) -> None:
        self.app = app
        self.top = tk.Toplevel(master)
        self.top.title("设置")
        self.top.transient(master)
        self.top.grab_set()
        self.top.resizable(False, False)
        self.top.geometry("520x340")
        self.top.minsize(500, 320)
        self.top.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.api_key_var = tk.StringVar(value=str(app.config.get("api_key", "")))
        self.base_url_var = tk.StringVar(value=str(app.config.get("base_url", "")))
        self.model_var = tk.StringVar(value=str(app.config.get(MODEL_KEY, DEFAULT_CONFIG[MODEL_KEY])))
        self.output_var = tk.StringVar(value=str(app.config.get("default_output", DEFAULT_CONFIG["default_output"])))
        self.log_file_var = tk.StringVar(value=str(app.config.get("log_file", DEFAULT_CONFIG["log_file"])))
        self.delay_var = tk.DoubleVar(value=float(app.config.get("multi_delay_seconds", DEFAULT_CONFIG["multi_delay_seconds"])))
        self.debug_var = tk.BooleanVar(value=bool(app.config.get("debug_enabled")))

        self._api_key_visible = False
        self.api_key_entry: Optional[ttk.Entry] = None
        self.api_key_toggle: Optional[ttk.Button] = None

        self._build_widgets()
        self._center_on_parent()
        self.top.wait_window()

    def _build_widgets(self) -> None:
        """绘制设置对话框控件。"""
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="API Key").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.api_key_entry = ttk.Entry(frame, textvariable=self.api_key_var, show="*")
        self.api_key_entry.grid(row=0, column=1, sticky=tk.EW, pady=4)
        self.api_key_toggle = ttk.Button(frame, text="显示", width=6, command=self._toggle_api_key_visibility)
        self.api_key_toggle.grid(row=0, column=2, sticky=tk.W, padx=(8, 0), pady=4)

        ttk.Label(frame, text="基础 URL").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.base_url_var).grid(row=1, column=1, columnspan=2, sticky=tk.EW, pady=4)

        ttk.Label(frame, text="模型").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.model_var).grid(row=2, column=1, columnspan=2, sticky=tk.EW, pady=4)

        ttk.Label(frame, text="默认输出").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.output_var).grid(row=3, column=1, sticky=tk.EW, pady=4)
        ttk.Button(frame, text="浏览...", command=self._choose_output).grid(row=3, column=2, sticky=tk.W, padx=(8, 0), pady=4)

        ttk.Label(frame, text="日志文件").grid(row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.log_file_var).grid(row=4, column=1, sticky=tk.EW, pady=4)
        ttk.Button(frame, text="浏览...", command=self._choose_log_file).grid(row=4, column=2, sticky=tk.W, padx=(8, 0), pady=4)

        ttk.Label(frame, text="批量延迟 (s)").grid(row=5, column=0, sticky=tk.W, pady=4)
        ttk.Spinbox(frame, textvariable=self.delay_var, from_=0.0, to=60.0, increment=0.5, width=10).grid(row=5, column=1, sticky=tk.W, pady=4)

        ttk.Checkbutton(frame, text="启用记录", variable=self.debug_var).grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=8)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=7, column=0, columnspan=3, sticky=tk.E, pady=(12, 0))
        ttk.Button(button_frame, text="保存", command=self._on_save).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="取消", command=self._on_cancel).pack(side=tk.RIGHT, padx=(0, 8))

    def _toggle_api_key_visibility(self) -> None:
        """切换 API Key 显示状态。"""
        if not self.api_key_entry or not self.api_key_toggle:
            return
        self._api_key_visible = not self._api_key_visible
        if self._api_key_visible:
            self.api_key_entry.configure(show="")
            self.api_key_toggle.configure(text="隐藏")
        else:
            self.api_key_entry.configure(show="*")
            self.api_key_toggle.configure(text="显示")

    def _choose_output(self) -> None:
        """选择默认输出路径。"""
        current = Path(self.output_var.get() or DEFAULT_CONFIG["default_output"])
        try:
            initial_dir = current.resolve().parent
        except Exception:
            initial_dir = Path.cwd()
        selected = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=".wav",
            filetypes=[("WAV", "*.wav"), ("All Files", "*.*")],
            initialdir=str(initial_dir),
            initialfile=current.name,
        )
        if selected:
            self.output_var.set(selected)

    def _choose_log_file(self) -> None:
        """选择日志输出位置。"""
        current = Path(self.log_file_var.get() or DEFAULT_CONFIG["log_file"])
        try:
            initial_dir = current.resolve().parent
        except Exception:
            initial_dir = Path.cwd()
        selected = filedialog.asksaveasfilename(
            title="选择日志文件",
            defaultextension=".log",
            filetypes=[("Log", "*.log"), ("Text", "*.txt"), ("All Files", "*.*")],
            initialdir=str(initial_dir),
            initialfile=current.name,
        )
        if selected:
            self.log_file_var.set(selected)

    def _on_save(self) -> None:
        """校验输入并保存设置。"""
        try:
            delay = float(self.delay_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("无效值", "延迟值必须为数字。")
            return

        updates = {
            "api_key": self.api_key_var.get().strip(),
            "base_url": self.base_url_var.get().strip(),
            MODEL_KEY: self.model_var.get().strip() or DEFAULT_CONFIG[MODEL_KEY],
            "default_output": self.output_var.get().strip() or DEFAULT_CONFIG["default_output"],
            "log_file": self.log_file_var.get().strip() or DEFAULT_CONFIG["log_file"],
            "multi_delay_seconds": max(0.0, delay),
            "debug_enabled": bool(self.debug_var.get()),
        }
        self.app.apply_settings(updates)
        self.top.destroy()

    def _on_cancel(self) -> None:
        """忽略修改并关闭。"""
        self.top.destroy()

    def _center_on_parent(self) -> None:
        """让对话框居中显示。"""
        self.top.update_idletasks()
        try:
            master = self.top.master
            master.update_idletasks()
            master_x = master.winfo_rootx()
            master_y = master.winfo_rooty()
            master_w = master.winfo_width()
            master_h = master.winfo_height()
        except Exception:
            master_x = self.top.winfo_screenwidth() // 2
            master_y = self.top.winfo_screenheight() // 2
            master_w = 0
            master_h = 0

        width = self.top.winfo_width() or 520
        height = self.top.winfo_height() or 340

        if master_w and master_h:
            x = master_x + (master_w - width) // 2
            y = master_y + (master_h - height) // 2
        else:
            x = master_x - width // 2
            y = master_y - height // 2

        self.top.geometry(f"{width}x{height}+{x}+{y}")


def main() -> None:
    """启动 Gemini TTS 工具集 图形界面。"""
    root = tk.Tk()
    GeminiTTSApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()





