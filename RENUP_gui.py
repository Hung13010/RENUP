import sys
import os

try:
    import customtkinter as ctk
except ImportError:
    print("LOI: Chua cai customtkinter. Chay: pip install customtkinter")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import subprocess
import threading
import json
import urllib.request
import webbrowser
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


GITHUB_REPO = "Hung13010/RENUP"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def get_version():
    version_file = os.path.join(get_app_dir(), 'version.txt')
    try:
        with open(version_file, 'r') as f:
            return f.read().strip()
    except Exception:
        return "1.1.0"


class RenupApp(ctk.CTk):

    VERSION = get_version()

    def __init__(self):
        super().__init__()

        self.title("RENUP")
        self.geometry("1200x750")
        self.minsize(950, 600)

        app_dir = get_app_dir()
        icon_path = os.path.join(app_dir, 'icon.ico')
        if os.path.exists(icon_path):
            self.iconbitmap(icon_path)
        self.bin_dir = os.path.join(app_dir, 'bin')
        self.input_dir = os.path.join(self.bin_dir, 'Input')
        self.output_dir = os.path.join(self.bin_dir, 'output_videos')
        self.ffmpeg_path = os.path.join(self.bin_dir, 'ffmpeg.exe')
        self.ffprobe_path = os.path.join(self.bin_dir, 'ffprobe.exe')
        self.noi_txt_path = os.path.join(self.bin_dir, 'Noi.txt')
        self.codes_dir = os.path.join(self.bin_dir, 'codes')

        self.is_running = False
        self._lock = threading.Lock()

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self._setup_ui()
        self._refresh_video_list()
        self._load_noi_txt()

        # Check for updates in background
        threading.Thread(target=self._check_update, daemon=True).start()

    # ══════════════════════════════════════════════════════════════
    # UI Setup
    # ══════════════════════════════════════════════════════════════

    def _setup_ui(self):
        self._setup_header()
        self._setup_status_bar()
        self._setup_main()

    def _setup_header(self):
        header = ctk.CTkFrame(self, height=56, corner_radius=0, fg_color="#1B2A4A")
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="  R  ",
            font=("Segoe UI", 18, "bold"), text_color="#1B2A4A",
            fg_color="#2E7D6A", corner_radius=8
        ).pack(side="left", padx=(16, 8), pady=10)

        ctk.CTkLabel(
            header, text="RENUP",
            font=("Segoe UI", 18, "bold"), text_color="white"
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            header, text=f"video processing tool · v{self.VERSION}",
            font=("Segoe UI", 11), text_color="#8CA0B3"
        ).pack(side="left")

        status_frame = ctk.CTkFrame(header, fg_color="transparent")
        status_frame.pack(side="right", padx=16)

        self.status_dot = ctk.CTkLabel(
            status_frame, text="●", font=("Segoe UI", 16),
            text_color="#E74C3C"
        )
        self.status_dot.pack(side="left", padx=(0, 6))

        self.header_status = ctk.CTkLabel(
            status_frame, text="stopped",
            font=("Segoe UI", 11), text_color="#8CA0B3"
        )
        self.header_status.pack(side="left")

    def _setup_status_bar(self):
        status_bar = ctk.CTkFrame(self, height=36, corner_radius=0, fg_color="#F5F5F5")
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)

        self.status_var = tk.StringVar(value="San sang.")
        ctk.CTkLabel(
            status_bar, textvariable=self.status_var,
            font=("Segoe UI", 10), text_color="#1B2A4A"
        ).pack(side="left", padx=12)

        self.progress_label = ctk.CTkLabel(
            status_bar, text="",
            font=("Segoe UI", 10), text_color="#1B2A4A"
        )
        self.progress_label.pack(side="right", padx=(0, 12))

        self.progress = ctk.CTkProgressBar(status_bar, width=220, height=14,
                                             progress_color="#2E7D6A")
        self.progress.pack(side="right", padx=(0, 8), pady=10)
        self.progress.set(0)

    def _setup_main(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=12, pady=8)

        left = ctk.CTkFrame(main, width=380, fg_color="transparent")
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)
        self._setup_left_panel(left)

        right = ctk.CTkFrame(main, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)
        self._setup_right_panel(right)

    # ── Left Panel ──

    def _setup_left_panel(self, parent):
        # ── Ghép Video section (hideable) ──
        self.ghep_section = ctk.CTkFrame(parent, fg_color="transparent")
        self.ghep_section.pack(fill="both", expand=True, pady=(0, 6))

        # Editor header
        eh = ctk.CTkFrame(self.ghep_section, fg_color="transparent")
        eh.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(eh, text="≡  NỘI DUNG GHÉP", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.line_count_label = ctk.CTkLabel(eh, text="0 dòng", font=("Segoe UI", 10), text_color="#888")
        self.line_count_label.pack(side="right")

        # Editor
        ef = ctk.CTkFrame(self.ghep_section, corner_radius=8, fg_color="#fafafa",
                           border_width=1, border_color="#e0e0e0")
        ef.pack(fill="both", expand=True, pady=(0, 6))

        self.editor = tk.Text(
            ef, font=("Consolas", 10), undo=True,
            bd=0, highlightthickness=0, bg="#fafafa",
            wrap="none", padx=8, pady=8
        )
        sb = ttk.Scrollbar(ef, orient="vertical", command=self.editor.yview)
        self.editor.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 2), pady=4)
        self.editor.pack(fill="both", expand=True, padx=(4, 0), pady=4)

        self.editor.tag_configure('separator', foreground='#2E7D6A',
                                   font=('Consolas', 10, 'bold'))
        self.editor.bind('<KeyRelease>', self._on_editor_change)

        # Editor buttons
        eb = ctk.CTkFrame(self.ghep_section, fg_color="transparent")
        eb.pack(fill="x")
        ctk.CTkButton(eb, text="Thêm #", width=75, height=30,
                       font=("Segoe UI", 11), fg_color="#2E7D6A",
                       hover_color="#246354", command=self._add_separator
                       ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(eb, text="Lưu Noi.txt", width=100, height=30,
                       font=("Segoe UI", 11), fg_color="#2E7D6A",
                       hover_color="#246354", command=self._save_noi_txt
                       ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(eb, text="Xóa", width=60, height=30,
                       font=("Segoe UI", 11), fg_color="#E74C3C",
                       hover_color="#C0392B", command=self._clear_editor
                       ).pack(side="left")

        # Function selector
        ctk.CTkLabel(parent, text="⚙  CHỌN CHỨC NĂNG",
                      font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 4))

        self._load_codes()
        self.func_var = ctk.StringVar(value=self._code_names[0] if self._code_names else "")
        self.func_menu = ctk.CTkOptionMenu(
            parent, variable=self.func_var,
            values=self._code_names,
            font=("Segoe UI", 11), height=34,
            fg_color="#ffffff", button_color="#2E7D6A",
            button_hover_color="#246354",
            text_color="#333333", dropdown_font=("Segoe UI", 11),
            command=self._on_func_changed
        )
        self.func_menu.pack(fill="x", pady=(0, 10))

        # ── Split options (hideable) ──
        self.split_section = ctk.CTkFrame(parent, fg_color="transparent")
        # hidden by default

        sr = ctk.CTkFrame(self.split_section, fg_color="transparent")
        sr.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(sr, text="✂  THỜI LƯỢNG MỖI PHẦN",
                      font=("Segoe UI", 11, "bold")).pack(side="left")

        time_row = ctk.CTkFrame(self.split_section, fg_color="transparent")
        time_row.pack(fill="x", pady=(0, 10))
        self.split_seconds_var = tk.IntVar(value=300)
        ctk.CTkEntry(time_row, textvariable=self.split_seconds_var,
                      width=80, height=32, font=("Segoe UI", 12),
                      justify="center").pack(side="left", padx=(0, 4))
        ctk.CTkLabel(time_row, text="giây / phần",
                      font=("Segoe UI", 11)).pack(side="left")

        # Folder Input
        ctk.CTkLabel(parent, text="📁  FOLDER INPUT",
                      font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 4))
        ir = ctk.CTkFrame(parent, fg_color="transparent")
        ir.pack(fill="x", pady=(0, 8))
        self.input_var = ctk.StringVar(value="")
        ctk.CTkEntry(ir, textvariable=self.input_var,
                      font=("Consolas", 10), height=32
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(ir, text="📂", width=36, height=32,
                       font=("Segoe UI", 14), fg_color="#2E7D6A",
                       hover_color="#246354", command=self._browse_input
                       ).pack(side="left")

        # Folder Output
        ctk.CTkLabel(parent, text="📁  FOLDER OUTPUT",
                      font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 4))
        orr = ctk.CTkFrame(parent, fg_color="transparent")
        orr.pack(fill="x")
        self.output_var = ctk.StringVar(value="")
        ctk.CTkEntry(orr, textvariable=self.output_var,
                      font=("Consolas", 10), height=32
                      ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(orr, text="📂", width=36, height=32,
                       font=("Segoe UI", 14), fg_color="#2E7D6A",
                       hover_color="#246354", command=self._browse_output
                       ).pack(side="left")

        # Workers + Run (same row)
        wr = ctk.CTkFrame(parent, fg_color="transparent")
        wr.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(wr, text="LUỒNG",
                      font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 8))
        self.workers_var = tk.IntVar(value=2)
        ctk.CTkEntry(wr, textvariable=self.workers_var, width=50, height=36,
                      font=("Segoe UI", 12), justify="center").pack(side="left")
        self.run_btn = ctk.CTkButton(
            wr, text="▶  RUN", height=36, width=140,
            font=("Segoe UI", 13, "bold"),
            fg_color="#FF9800", hover_color="#F57C00",
            command=self._run_selected
        )
        self.run_btn.pack(side="right")

    # ── Right Panel ──

    def _setup_right_panel(self, parent):
        # ── Table section (hideable, only for Ghép Video) ──
        self.table_section = ctk.CTkFrame(parent, fg_color="transparent")
        self.table_section.pack(fill="both", expand=True, pady=(0, 6))

        # Table header
        th = ctk.CTkFrame(self.table_section, fg_color="transparent")
        th.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(th, text="DANH SÁCH VIDEO",
                      font=("Segoe UI", 12, "bold")).pack(side="left")
        self.file_count_label = ctk.CTkLabel(
            th, text="0 file", font=("Segoe UI", 10), text_color="#888")
        self.file_count_label.pack(side="left", padx=8)
        ctk.CTkButton(th, text="Refresh", width=80, height=28,
                       font=("Segoe UI", 10), fg_color="#2E7D6A",
                       hover_color="#246354", command=self._refresh_video_list
                       ).pack(side="right")

        # Treeview style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Renup.Treeview",
                         background="#ffffff", foreground="#333333",
                         fieldbackground="#ffffff", font=("Segoe UI", 10),
                         rowheight=30, borderwidth=0)
        style.configure("Renup.Treeview.Heading",
                         background="#f5f5f5", foreground="#666666",
                         font=("Segoe UI", 10, "bold"), relief="flat",
                         borderwidth=0)
        style.map("Renup.Treeview",
                   background=[("selected", "#E8F5E9")],
                   foreground=[("selected", "#2E7D6A")])

        # Table
        tf = ctk.CTkFrame(self.table_section, corner_radius=8, fg_color="#ffffff",
                           border_width=1, border_color="#e0e0e0")
        tf.pack(fill="both", expand=True, pady=(0, 6))

        cols = ("idx", "name", "size", "ext")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings",
                                  style="Renup.Treeview", selectmode="extended")
        self.tree.heading("idx", text="#", anchor="center")
        self.tree.heading("name", text="TÊN FILE", anchor="w")
        self.tree.heading("size", text="KÍCH THƯỚC", anchor="w")
        self.tree.heading("ext", text="ĐỊNH DẠNG", anchor="center")
        self.tree.column("idx", width=40, minwidth=40, anchor="center")
        self.tree.column("name", width=300, minwidth=120)
        self.tree.column("size", width=100, minwidth=70)
        self.tree.column("ext", width=80, minwidth=60, anchor="center")

        tsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.config(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y", padx=(0, 2), pady=2)
        self.tree.pack(fill="both", expand=True, padx=2, pady=2)

        self.tree.tag_configure('even', background='#ffffff')
        self.tree.tag_configure('odd', background='#f9f9f9')
        self.tree.bind('<Double-Button-1>', self._add_selected_videos)

        # Add button
        ctk.CTkButton(
            self.table_section, text="+ THÊM VÀO DANH SÁCH GHÉP", height=38,
            font=("Segoe UI", 12, "bold"),
            fg_color="#FF9800", hover_color="#F57C00",
            command=self._add_selected_videos
        ).pack(fill="x")

        # Log
        self.log_section = ctk.CTkFrame(parent, fg_color="transparent")
        self.log_section.pack(fill="both", expand=True)

        ctk.CTkLabel(self.log_section, text="LOG",
                      font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 4))

        lf = ctk.CTkFrame(self.log_section, corner_radius=8, fg_color="#1C2333",
                           border_width=1, border_color="#333333")
        lf.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            lf, font=("Consolas", 9), state="disabled",
            bd=0, highlightthickness=0, bg="#1C2333", fg="#d4d4d4",
            wrap="word", padx=8, pady=8
        )
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.config(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y", padx=(0, 2), pady=4)
        self.log_text.pack(fill="both", expand=True, padx=(4, 0), pady=4)

        self.log_text.tag_configure('ok', foreground='#66BB6A')
        self.log_text.tag_configure('err', foreground='#E74C3C')
        self.log_text.tag_configure('info', foreground='#8CA0B3')

    # ══════════════════════════════════════════════════════════════
    # State management
    # ══════════════════════════════════════════════════════════════

    def _load_codes(self):
        """Đọc tất cả file .json trong bin/codes/ và map tên → type."""
        self._code_map = {}
        self._code_names = []
        os.makedirs(self.codes_dir, exist_ok=True)
        for f in sorted(os.listdir(self.codes_dir)):
            if f.lower().endswith('.json'):
                filepath = os.path.join(self.codes_dir, f)
                try:
                    with open(filepath, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                    name = data.get('name', os.path.splitext(f)[0])
                    self._code_map[name] = data
                    self._code_names.append(name)
                except Exception:
                    pass

    def _reload_codes(self):
        """Refresh danh sách codes từ folder."""
        self._load_codes()
        self.func_menu.configure(values=self._code_names)
        if self._code_names:
            self.func_var.set(self._code_names[0])
            self._on_func_changed()
        self._log(f"Da tai lai {len(self._code_names)} code tu bin/codes/", 'info')

    def _open_codes_folder(self):
        os.makedirs(self.codes_dir, exist_ok=True)
        os.startfile(self.codes_dir)

    def _on_func_changed(self, _choice=None):
        code = self._code_map.get(self.func_var.get(), {})
        code_type = code.get('type', '')

        # Hide all optional sections
        self.ghep_section.pack_forget()
        self.table_section.pack_forget()
        self.split_section.pack_forget()
        self.log_section.pack_forget()

        if code_type == 'concat':
            self.ghep_section.pack(fill="both", expand=True, pady=(0, 6))
            self.table_section.pack(fill="both", expand=True, pady=(0, 6))
        elif code_type == 'split_video':
            self.split_section.pack(fill="x", pady=(0, 6))

        self.log_section.pack(fill="both", expand=True)

    def _run_selected(self):
        code = self._code_map.get(self.func_var.get(), {})
        code_type = code.get('type', '')
        if code_type == 'concat':
            self._run_concatenation()
        elif code_type == 'convert_mp3':
            self._run_convert()
        elif code_type == 'split_video':
            self._run_split()
        elif code_type == 'reencode':
            self._run_reencode()
        else:
            self._log(f"Khong ho tro type: {code_type}", 'err')

    def _set_running(self, running):
        self.is_running = running
        if running:
            self.status_dot.configure(text_color="#2E7D6A")
            self.header_status.configure(text="running")
            self.run_btn.configure(state="disabled")
        else:
            self.status_dot.configure(text_color="#E74C3C")
            self.header_status.configure(text="stopped")
            self.run_btn.configure(state="normal")

    # ══════════════════════════════════════════════════════════════
    # Video list
    # ══════════════════════════════════════════════════════════════

    def _refresh_video_list(self):
        self.input_dir = self.input_var.get()
        self.output_dir = self.output_var.get()
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self.input_dir:
            self.file_count_label.configure(text="0 file")
            self.status_var.set("Chua chon folder Input.")
            return
        os.makedirs(self.input_dir, exist_ok=True)
        video_exts = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v')
        videos = sorted(f for f in os.listdir(self.input_dir)
                         if f.lower().endswith(video_exts))
        for i, v in enumerate(videos, 1):
            filepath = os.path.join(self.input_dir, v)
            try:
                size = format_size(os.path.getsize(filepath))
            except OSError:
                size = "—"
            ext = os.path.splitext(v)[1].upper()
            tag = 'even' if i % 2 == 0 else 'odd'
            self.tree.insert('', 'end', values=(i, v, size, ext), tags=(tag,))
        self.file_count_label.configure(text=f"{len(videos)} file")
        self._log(f"Tim thay {len(videos)} video trong Input.", 'info')
        self.status_var.set(f"{len(videos)} video trong Input.")

    def _add_selected_videos(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        for item_id in selected:
            vals = self.tree.item(item_id, 'values')
            name = vals[1]
            self.editor.insert('end', name + '\n')
        self._on_editor_change()

    def _add_separator(self):
        self.editor.insert('end', '#\n')
        self._on_editor_change()

    def _clear_editor(self):
        if messagebox.askyesno("Xac nhan", "Xoa toan bo noi dung editor?"):
            self.editor.delete('1.0', 'end')
            self._on_editor_change()

    def _on_editor_change(self, _event=None):
        self._highlight_separators()
        content = self.editor.get('1.0', 'end-1c')
        lines = [l for l in content.split('\n') if l.strip()]
        self.line_count_label.configure(text=f"{len(lines)} dòng")

    # ══════════════════════════════════════════════════════════════
    # Noi.txt
    # ══════════════════════════════════════════════════════════════

    def _load_noi_txt(self):
        if os.path.exists(self.noi_txt_path):
            with open(self.noi_txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.editor.delete('1.0', 'end')
            self.editor.insert('1.0', content)
            self._on_editor_change()
            self._log("Da tai Noi.txt.", 'info')

    def _save_noi_txt(self):
        content = self.editor.get('1.0', 'end')
        with open(self.noi_txt_path, 'w', encoding='utf-8') as f:
            f.write(content)
        self._log("Da luu Noi.txt.", 'ok')
        self.status_var.set("Da luu Noi.txt.")

    # ══════════════════════════════════════════════════════════════
    # Syntax highlighting
    # ══════════════════════════════════════════════════════════════

    def _highlight_separators(self, _event=None):
        self.editor.tag_remove('separator', '1.0', 'end')
        start = '1.0'
        while True:
            pos = self.editor.search('#', start, stopindex='end')
            if not pos:
                break
            line_end = f"{pos.split('.')[0]}.end"
            self.editor.tag_add('separator', pos, line_end)
            start = line_end

    # ══════════════════════════════════════════════════════════════
    # Folder helpers
    # ══════════════════════════════════════════════════════════════

    def _browse_input(self):
        path = filedialog.askdirectory(initialdir=self.input_dir,
                                        title="Chon thu muc Input")
        if path:
            self.input_dir = path
            self.input_var.set(path)
            self._refresh_video_list()

    def _browse_output(self):
        path = filedialog.askdirectory(initialdir=self.output_dir,
                                        title="Chon thu muc Output")
        if path:
            self.output_dir = path
            self.output_var.set(path)
            self._log(f"Output folder: {path}", 'info')

    def _open_input_folder(self):
        os.makedirs(self.input_dir, exist_ok=True)
        os.startfile(self.input_dir)

    def _open_output_folder(self):
        os.makedirs(self.output_dir, exist_ok=True)
        os.startfile(self.output_dir)

    # ══════════════════════════════════════════════════════════════
    # Concatenation
    # ══════════════════════════════════════════════════════════════

    def _run_concatenation(self):
        if self.is_running:
            return
        self._save_noi_txt()
        self._ui(lambda: self._set_running(True))
        threading.Thread(target=self._concat_worker, daemon=True).start()

    def _concat_worker(self):
        self._ui(lambda: self.status_var.set("Dang xu ly..."))
        self._ui(lambda: self.progress_label.configure(text=""))
        self._ui(lambda: self.progress.set(0))
        self._log("=== Bat dau ghep video ===", 'info')

        try:
            if not os.path.exists(self.ffmpeg_path):
                raise FileNotFoundError(
                    f"Khong tim thay ffmpeg.exe:\n{self.ffmpeg_path}")

            os.makedirs(self.output_dir, exist_ok=True)

            with open(self.noi_txt_path, 'r', encoding='utf-8') as f:
                lines = [l.strip() for l in f.readlines()]

            groups, current = [], []
            for line in lines:
                if line == '#':
                    if current:
                        groups.append(current)
                    current = []
                elif line:
                    current.append(line)
            if current:
                groups.append(current)

            if not groups:
                self._log("Noi.txt rong hoac khong co nhom nao.", 'err')
                return

            max_workers = max(1, self.workers_var.get())
            total = len(groups)
            self._log(f"Tim thay {total} nhom | Chay {max_workers} luong.", 'info')

            ok_count = 0
            done_count = 0

            def update_progress(success):
                nonlocal ok_count, done_count
                with self._lock:
                    if success:
                        ok_count += 1
                    done_count += 1
                    _d = done_count
                self._ui(lambda d=_d: (
                    self.progress.set(d / total),
                    self.progress_label.configure(text=f"{d}/{total}"),
                    self.status_var.set(f"Dang xu ly... {d}/{total} nhom")
                ))

            futures = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for i, group in enumerate(groups, 1):
                    future = executor.submit(self._process_group, i, group, total)
                    futures[future] = i
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        success, label = future.result()
                    except Exception as exc:
                        success = False
                        self._log(f"  [Nhom {idx}] LOI: {exc}", 'err')
                    update_progress(success)

            self._log(f"=== Hoan thanh: {ok_count}/{total} nhom ===", 'ok')
            self._ui(lambda: self.status_var.set(f"Xong! {ok_count}/{total} nhom."))
            self._ui(lambda: messagebox.showinfo(
                "Hoan thanh",
                f"Da ghep xong {ok_count}/{total} nhom video.\n\n"
                f"File luu tai:\n{self.output_dir}"
            ))

        except Exception as exc:
            self._log(f"LOI: {exc}", 'err')
            self._ui(lambda: self.status_var.set("Co loi xay ra."))
        finally:
            self._ui(lambda: self._set_running(False))

    def _get_total_duration(self, group):
        total = 0.0
        for video in group:
            total += self._get_file_duration(
                os.path.join(self.input_dir, video))
        return total

    def _process_group(self, i, group, total):
        names = [os.path.splitext(v)[0] for v in group]
        output_name = ', '.join(names) + '.mp4'
        output_path = os.path.join(self.output_dir, output_name)
        temp_list = os.path.join(self.bin_dir, f'_temp_list_{i}.txt')

        self._log(f"[{i}/{total}] Bat dau: {output_name}", 'info')

        with open(temp_list, 'w', encoding='utf-8') as f:
            for video in group:
                vpath = os.path.join(self.input_dir, video)
                f.write(f"file '{vpath}'\n")

        total_duration = self._get_total_duration(group)

        cmd = [
            self.ffmpeg_path,
            '-f', 'concat', '-safe', '0',
            '-i', temp_list,
            '-c', 'copy',
            '-progress', 'pipe:1',
            '-nostats',
            output_path, '-y'
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )

        stderr_lines = []

        def drain_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        t = threading.Thread(target=drain_stderr, daemon=True)
        t.start()

        last_pct = -1
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('out_time_ms='):
                try:
                    val = int(line.split('=')[1])
                    if val >= 0 and total_duration > 0:
                        pct = min(99, int(val / 1_000_000 / total_duration * 100))
                        if pct >= last_pct + 5:
                            self._log(f"  [{i}/{total}] {pct}%", 'info')
                            last_pct = pct
                except (ValueError, ZeroDivisionError):
                    pass

        proc.wait()
        t.join()

        if os.path.exists(temp_list):
            os.remove(temp_list)

        if proc.returncode == 0:
            self._log(f"  [{i}/{total}] OK: {output_name}", 'ok')
            return True, output_name

        stderr_text = ''.join(stderr_lines).strip()
        last_err = (stderr_text.splitlines()[-1]
                     if stderr_text else 'Unknown error')
        self._log(f"  [{i}/{total}] LOI: {last_err}", 'err')
        return False, output_name

    # ══════════════════════════════════════════════════════════════
    # Convert MP4 -> MP3
    # ══════════════════════════════════════════════════════════════

    def _run_convert(self):
        if self.is_running:
            return
        self._ui(lambda: self._set_running(True))
        threading.Thread(target=self._convert_worker, daemon=True).start()

    def _convert_worker(self):
        self._ui(lambda: self.status_var.set("Dang convert MP3..."))
        self._ui(lambda: self.progress_label.configure(text=""))
        self._ui(lambda: self.progress.set(0))
        self._log("=== Bat dau convert MP4 -> MP3 ===", 'info')

        try:
            if not os.path.exists(self.ffmpeg_path):
                raise FileNotFoundError(
                    f"Khong tim thay ffmpeg.exe:\n{self.ffmpeg_path}")

            os.makedirs(self.output_dir, exist_ok=True)

            mp4_files = sorted(
                f for f in os.listdir(self.input_dir)
                if f.lower().endswith('.mp4')
            )

            if not mp4_files:
                self._log("Khong tim thay file .mp4 nao trong Input.", 'err')
                return

            max_workers = max(1, self.workers_var.get())
            total = len(mp4_files)
            self._log(f"Tim thay {total} file MP4 | Chay {max_workers} luong.", 'info')

            ok_count = 0
            done_count = 0

            def update_progress(success):
                nonlocal ok_count, done_count
                with self._lock:
                    if success:
                        ok_count += 1
                    done_count += 1
                    _d = done_count
                self._ui(lambda d=_d: (
                    self.progress.set(d / total),
                    self.progress_label.configure(text=f"{d}/{total}"),
                    self.status_var.set(f"Dang convert... {d}/{total} file")
                ))

            futures = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for i, mp4_file in enumerate(mp4_files, 1):
                    future = executor.submit(
                        self._convert_file, i, mp4_file, total)
                    futures[future] = i
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        success, label = future.result()
                    except Exception as exc:
                        success = False
                        self._log(f"  [File {idx}] LOI: {exc}", 'err')
                    update_progress(success)

            self._log(
                f"=== Hoan thanh convert: {ok_count}/{total} file ===", 'ok')
            self._ui(lambda: self.status_var.set(
                f"Xong convert! {ok_count}/{total} file."))
            self._ui(lambda: messagebox.showinfo(
                "Hoan thanh",
                f"Da convert xong {ok_count}/{total} file MP4 -> MP3.\n\n"
                f"File luu tai:\n{self.output_dir}"
            ))

        except Exception as exc:
            self._log(f"LOI: {exc}", 'err')
            self._ui(lambda: self.status_var.set("Co loi xay ra."))
        finally:
            self._ui(lambda: self._set_running(False))

    def _convert_file(self, i, mp4_file, total):
        mp3_name = os.path.splitext(mp4_file)[0] + '.mp3'
        input_path = os.path.join(self.input_dir, mp4_file)
        output_path = os.path.join(self.output_dir, mp3_name)

        self._log(f"[{i}/{total}] Convert: {mp4_file} -> {mp3_name}", 'info')

        duration = self._get_file_duration(input_path)

        cmd = [
            self.ffmpeg_path,
            '-i', input_path,
            '-vn',
            '-acodec', 'libmp3lame',
            '-q:a', '2',
            '-progress', 'pipe:1',
            '-nostats',
            output_path, '-y'
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )

        stderr_lines = []

        def drain_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        t = threading.Thread(target=drain_stderr, daemon=True)
        t.start()

        last_pct = -1
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('out_time_ms='):
                try:
                    val = int(line.split('=')[1])
                    if val >= 0 and duration > 0:
                        pct = min(99, int(val / 1_000_000 / duration * 100))
                        if pct >= last_pct + 20:
                            self._log(
                                f"  [{i}/{total}] {mp4_file}: {pct}%", 'info')
                            last_pct = pct
                except (ValueError, ZeroDivisionError):
                    pass

        proc.wait()
        t.join()

        if proc.returncode == 0:
            self._log(f"  [{i}/{total}] OK: {mp3_name}", 'ok')
            return True, mp3_name

        stderr_text = ''.join(stderr_lines).strip()
        last_err = (stderr_text.splitlines()[-1]
                     if stderr_text else 'Unknown error')
        self._log(f"  [{i}/{total}] LOI: {last_err}", 'err')
        return False, mp3_name

    # ══════════════════════════════════════════════════════════════
    # Split Video
    # ══════════════════════════════════════════════════════════════

    def _run_split(self):
        if self.is_running:
            return
        self._ui(lambda: self._set_running(True))
        threading.Thread(target=self._split_worker, daemon=True).start()

    def _split_worker(self):
        self._ui(lambda: self.status_var.set("Dang chia nho video..."))
        self._ui(lambda: self.progress_label.configure(text=""))
        self._ui(lambda: self.progress.set(0))
        self._log("=== Bat dau chia nho video ===", 'info')

        try:
            if not os.path.exists(self.ffmpeg_path):
                raise FileNotFoundError(
                    f"Khong tim thay ffmpeg.exe:\n{self.ffmpeg_path}")

            self.input_dir = self.input_var.get()
            self.output_dir = self.output_var.get()
            if not self.input_dir or not self.output_dir:
                self._log("Chua chon folder Input hoac Output.", 'err')
                return

            os.makedirs(self.output_dir, exist_ok=True)

            video_exts = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v')
            video_files = sorted(
                f for f in os.listdir(self.input_dir)
                if f.lower().endswith(video_exts)
            )

            if not video_files:
                self._log("Khong tim thay video nao trong Input.", 'err')
                return

            segment_sec = max(1, self.split_seconds_var.get())
            max_workers = max(1, self.workers_var.get())
            total = len(video_files)
            self._log(f"Tim thay {total} video | Chia moi {segment_sec} giay | "
                       f"{max_workers} luong.", 'info')

            ok_count = 0
            done_count = 0

            def update_progress(success):
                nonlocal ok_count, done_count
                with self._lock:
                    if success:
                        ok_count += 1
                    done_count += 1
                    _d = done_count
                self._ui(lambda d=_d: (
                    self.progress.set(d / total),
                    self.progress_label.configure(text=f"{d}/{total}"),
                    self.status_var.set(f"Dang chia... {d}/{total} video")
                ))

            futures = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for i, vfile in enumerate(video_files, 1):
                    future = executor.submit(
                        self._split_file, i, vfile, total, segment_sec)
                    futures[future] = i
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        success, label = future.result()
                    except Exception as exc:
                        success = False
                        self._log(f"  [File {idx}] LOI: {exc}", 'err')
                    update_progress(success)

            self._log(
                f"=== Hoan thanh chia nho: {ok_count}/{total} video ===", 'ok')
            self._ui(lambda: self.status_var.set(
                f"Xong! {ok_count}/{total} video."))
            self._ui(lambda: messagebox.showinfo(
                "Hoan thanh",
                f"Da chia xong {ok_count}/{total} video.\n\n"
                f"File luu tai:\n{self.output_dir}"
            ))

        except Exception as exc:
            self._log(f"LOI: {exc}", 'err')
            self._ui(lambda: self.status_var.set("Co loi xay ra."))
        finally:
            self._ui(lambda: self._set_running(False))

    def _split_file(self, i, video_file, total, segment_sec):
        name = os.path.splitext(video_file)[0]
        ext = os.path.splitext(video_file)[1]
        input_path = os.path.join(self.input_dir, video_file)
        output_pattern = os.path.join(
            self.output_dir, f"{name}_phan%03d{ext}")

        self._log(f"[{i}/{total}] Chia: {video_file} "
                   f"(moi {segment_sec} giay)", 'info')

        cmd = [
            self.ffmpeg_path,
            '-i', input_path,
            '-c', 'copy',
            '-f', 'segment',
            '-segment_time', str(segment_sec),
            '-reset_timestamps', '1',
            output_pattern, '-y'
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )

        stderr_lines = []
        for line in proc.stderr:
            stderr_lines.append(line)

        proc.wait()

        if proc.returncode == 0:
            # Count output parts
            parts = [f for f in os.listdir(self.output_dir)
                     if f.startswith(f"{name}_phan")]
            self._log(f"  [{i}/{total}] OK: {video_file} -> "
                       f"{len(parts)} phan", 'ok')
            return True, video_file

        stderr_text = ''.join(stderr_lines).strip()
        last_err = (stderr_text.splitlines()[-1]
                     if stderr_text else 'Unknown error')
        self._log(f"  [{i}/{total}] LOI: {last_err}", 'err')
        return False, video_file

    # ══════════════════════════════════════════════════════════════
    # Re-encode Video
    # ══════════════════════════════════════════════════════════════

    def _run_reencode(self):
        if self.is_running:
            return
        self._ui(lambda: self._set_running(True))
        threading.Thread(target=self._reencode_worker, daemon=True).start()

    def _reencode_worker(self):
        self._ui(lambda: self.status_var.set("Dang nen video..."))
        self._ui(lambda: self.progress_label.configure(text=""))
        self._ui(lambda: self.progress.set(0))

        code = self._code_map.get(self.func_var.get(), {})
        code_name = code.get('name', 'Re-encode')
        self._log(f"=== Bat dau: {code_name} ===", 'info')

        try:
            if not os.path.exists(self.ffmpeg_path):
                raise FileNotFoundError(
                    f"Khong tim thay ffmpeg.exe:\n{self.ffmpeg_path}")

            self.input_dir = self.input_var.get()
            self.output_dir = self.output_var.get()
            if not self.input_dir or not self.output_dir:
                self._log("Chua chon folder Input hoac Output.", 'err')
                return

            os.makedirs(self.output_dir, exist_ok=True)

            video_exts = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v')
            video_files = sorted(
                f for f in os.listdir(self.input_dir)
                if f.lower().endswith(video_exts)
            )

            if not video_files:
                self._log("Khong tim thay video nao trong Input.", 'err')
                return

            max_workers = max(1, self.workers_var.get())
            total = len(video_files)
            self._log(f"Tim thay {total} video | {max_workers} luong.", 'info')

            ok_count = 0
            done_count = 0

            def update_progress(success):
                nonlocal ok_count, done_count
                with self._lock:
                    if success:
                        ok_count += 1
                    done_count += 1
                    _d = done_count
                self._ui(lambda d=_d: (
                    self.progress.set(d / total),
                    self.progress_label.configure(text=f"{d}/{total}"),
                    self.status_var.set(f"Dang nen... {d}/{total} video")
                ))

            cmd_template = code.get('command', '')
            futures = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for i, vfile in enumerate(video_files, 1):
                    future = executor.submit(
                        self._reencode_file, i, vfile, total, cmd_template)
                    futures[future] = i
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        success, label = future.result()
                    except Exception as exc:
                        success = False
                        self._log(f"  [File {idx}] LOI: {exc}", 'err')
                    update_progress(success)

            self._log(
                f"=== Hoan thanh: {ok_count}/{total} video ===", 'ok')
            self._ui(lambda: self.status_var.set(
                f"Xong! {ok_count}/{total} video."))
            self._ui(lambda: messagebox.showinfo(
                "Hoan thanh",
                f"Da xu ly xong {ok_count}/{total} video.\n\n"
                f"File luu tai:\n{self.output_dir}"
            ))

        except Exception as exc:
            self._log(f"LOI: {exc}", 'err')
            self._ui(lambda: self.status_var.set("Co loi xay ra."))
        finally:
            self._ui(lambda: self._set_running(False))

    def _reencode_file(self, i, video_file, total, cmd_template):
        input_path = os.path.join(self.input_dir, video_file)
        output_path = os.path.join(self.output_dir, video_file)

        self._log(f"[{i}/{total}] Nen: {video_file}", 'info')

        duration = self._get_file_duration(input_path)

        cmd = [
            self.ffmpeg_path,
            '-i', input_path,
            '-vf', 'scale=1920:-2',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-b:v', '8000k',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-progress', 'pipe:1',
            '-nostats',
            output_path, '-y'
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )

        stderr_lines = []

        def drain_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        t = threading.Thread(target=drain_stderr, daemon=True)
        t.start()

        last_pct = -1
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('out_time_ms='):
                try:
                    val = int(line.split('=')[1])
                    if val >= 0 and duration > 0:
                        pct = min(99, int(val / 1_000_000 / duration * 100))
                        if pct >= last_pct + 10:
                            self._log(
                                f"  [{i}/{total}] {video_file}: {pct}%", 'info')
                            last_pct = pct
                except (ValueError, ZeroDivisionError):
                    pass

        proc.wait()
        t.join()

        if proc.returncode == 0:
            self._log(f"  [{i}/{total}] OK: {video_file}", 'ok')
            return True, video_file

        stderr_text = ''.join(stderr_lines).strip()
        last_err = (stderr_text.splitlines()[-1]
                     if stderr_text else 'Unknown error')
        self._log(f"  [{i}/{total}] LOI: {last_err}", 'err')
        return False, video_file

    # ══════════════════════════════════════════════════════════════
    # Auto Update
    # ══════════════════════════════════════════════════════════════

    def _check_update(self):
        try:
            req = urllib.request.Request(
                GITHUB_API,
                headers={"User-Agent": "RENUP-Updater"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            latest = data.get("tag_name", "").lstrip("v")
            if not latest:
                return

            if self._version_compare(latest, self.VERSION) > 0:
                self._ui(lambda: self._show_update_dialog(latest, data))

        except Exception:
            pass

    def _version_compare(self, v1, v2):
        """So sanh version. Tra ve >0 neu v1 > v2."""
        def parse(v):
            return [int(x) for x in v.split('.')]
        try:
            p1, p2 = parse(v1), parse(v2)
            for a, b in zip(p1, p2):
                if a != b:
                    return a - b
            return len(p1) - len(p2)
        except Exception:
            return 0

    def _show_update_dialog(self, latest, data):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Cập nhật mới")
        dialog.geometry("450x280")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.attributes("-topmost", True)

        # Center dialog
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 450) // 2
        y = self.winfo_y() + (self.winfo_height() - 280) // 2
        dialog.geometry(f"450x280+{x}+{y}")

        ctk.CTkLabel(
            dialog, text="🔄  Có bản cập nhật mới!",
            font=("Segoe UI", 16, "bold")
        ).pack(pady=(20, 8))

        ctk.CTkLabel(
            dialog, text=f"Phiên bản hiện tại: v{self.VERSION}",
            font=("Segoe UI", 12), text_color="#666666"
        ).pack()

        ctk.CTkLabel(
            dialog, text=f"Phiên bản mới: v{latest}",
            font=("Segoe UI", 14, "bold"), text_color="#2E7D6A"
        ).pack(pady=(4, 8))

        # Changelog
        body = data.get("body", "").strip()
        if body:
            cl_frame = ctk.CTkFrame(dialog, fg_color="#F5F5F5", corner_radius=8)
            cl_frame.pack(fill="x", padx=20, pady=(0, 12))
            ctk.CTkLabel(
                cl_frame, text=body[:200],
                font=("Segoe UI", 10), text_color="#333333",
                wraplength=400, justify="left"
            ).pack(padx=12, pady=8)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 16))

        # Find .exe download URL
        download_url = ""
        for asset in data.get("assets", []):
            if asset["name"].lower().endswith(".exe"):
                download_url = asset["browser_download_url"]
                break

        ctk.CTkButton(
            btn_frame, text="⬇  Cập nhật ngay", width=160, height=38,
            font=("Segoe UI", 12, "bold"),
            fg_color="#2E7D6A", hover_color="#246354",
            command=lambda: self._download_update(download_url, dialog)
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="Bỏ qua", width=100, height=38,
            font=("Segoe UI", 12),
            fg_color="#999999", hover_color="#777777",
            command=dialog.destroy
        ).pack(side="left")

    def _download_update(self, url, dialog):
        if not url:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            dialog.destroy()
            return

        dialog.destroy()
        self._log("=== Dang tai ban cap nhat... ===", 'info')
        self._ui(lambda: self.status_var.set("Dang tai ban cap nhat..."))

        def _do_download():
            try:
                app_dir = get_app_dir()
                exe_name = "RENUP.exe"
                new_exe = os.path.join(app_dir, exe_name + ".new")
                old_exe = os.path.join(app_dir, exe_name + ".old")
                current_exe = os.path.join(app_dir, exe_name)

                req = urllib.request.Request(
                    url, headers={"User-Agent": "RENUP-Updater"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    with open(new_exe, 'wb') as f:
                        shutil.copyfileobj(resp, f)

                self._log(f"Da tai xong. Dang cap nhat...", 'info')

                # Rename: current → .old, new → current
                if os.path.exists(current_exe):
                    if os.path.exists(old_exe):
                        os.remove(old_exe)
                    os.rename(current_exe, old_exe)
                os.rename(new_exe, current_exe)

                self._log("=== Cap nhat thanh cong! Khoi dong lai de su dung ban moi. ===", 'ok')
                self._ui(lambda: self.status_var.set("Cap nhat xong! Hay khoi dong lai."))
                self._ui(lambda: messagebox.showinfo(
                    "Cap nhat thanh cong",
                    f"Da cap nhat RENUP thanh cong!\n\n"
                    f"Hay dong va mo lai RENUP de su dung ban moi."
                ))

            except Exception as exc:
                self._log(f"LOI cap nhat: {exc}", 'err')
                self._ui(lambda: self.status_var.set("Loi cap nhat."))
                # Cleanup
                if os.path.exists(new_exe):
                    os.remove(new_exe)

        threading.Thread(target=_do_download, daemon=True).start()

    # ══════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════

    def _get_file_duration(self, filepath):
        try:
            result = subprocess.run(
                [self.ffprobe_path, '-v', 'quiet',
                 '-print_format', 'json', '-show_format', filepath],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            data = json.loads(result.stdout)
            return float(data['format']['duration'])
        except Exception:
            return 0.0

    def _log(self, msg, tag=''):
        def _write():
            self.log_text.config(state='normal')
            self.log_text.insert('end', msg + '\n', tag)
            self.log_text.see('end')
            self.log_text.config(state='disabled')
        self._ui(_write)

    def _ui(self, fn):
        self.after(0, fn)


# ══════════════════════════════════════════════════════════════════

def main():
    app = RenupApp()
    app.mainloop()


if __name__ == '__main__':
    main()
