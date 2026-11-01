from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
import traceback
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from core import (auth, diagnose, fabric, forge, java, launch, neoforge,
                  optifine, quilt, skins, worlds)
from core import mods as modlib
from core.auth import Account
from core.config import CACHE_DIR, DEFAULT_CLIENT_ID, LOG_DIR, Config, safe_folder_name, unique_dir
from core.util import Cancelled, LauncherError, ensure_dir, human_size
from core.version import Repo
from ui.dialogs import (AccountDialog, CrashDialog, DeviceCodeDialog,
                        InstanceDialog, LogWindow)
from ui.multiplayer import MultiplayerTab
from ui.theme import (
    ACCENT,
    ACCENT_DARK,
    ACCENT_DISABLED_BG,
    ACCENT_DISABLED_FG,
    ACCENT_HOVER,
    BG,
    BLUE,
    BORDER,
    DARK_FG,
    ERR,
    FG,
    FONT,
    FONT_BIG,
    FONT_BOLD,
    FONT_SMALL,
    FONT_TITLE,
    INPUT,
    MUTED,
    PANEL,
    PANEL2,
    PANEL3,
    SELECT_FG,
    WARN,
    apply_theme,
)
from ui.widgets import (
    append_text,
    busy_text,
    center_window,
    clear_text,
    flat_button,
    idle_text,
    link_button,
    text_area,
)

LOADERS = {"原版": "vanilla", "Fabric": "fabric", "Forge": "forge",
           "NeoForge": "neoforge", "Quilt": "quilt", "OptiFine": "optifine"}
LOADER_NAMES = {"vanilla": "原版", "fabric": "Fabric", "forge": "Forge",
                "neoforge": "NeoForge", "quilt": "Quilt", "optifine": "OptiFine"}
FTYPES = {"正式版": "release", "快照": "snapshot",
          "旧版本": ("old_beta", "old_alpha"), "全部": None}
TYPE_NAMES = {"release": "正式版", "snapshot": "快照",
              "old_beta": "远古 Beta", "old_alpha": "远古 Alpha"}
CONTENT_TYPES = {"资源包/材质包": "resourcepack", "光影": "shader",
                 "地图": "world", "皮肤": "skin"}
SOURCES = {"官方源": "official", "BMCLAPI 镜像": "bmcl"}


class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PyMC Launcher")
        self.geometry("1160x740")
        self.minsize(1000, 660)
        apply_theme(self)
        try:
            from ui.icon import ICON_PNG
            self._icon_image = tk.PhotoImage(data=ICON_PNG, format="png")
            self.iconphoto(True, self._icon_image)
        except Exception:
            self._icon_image = None

        self.cfg = Config.load()
        modlib.configure_api(self.cfg.get("mod_api"))
        self.cancel_event = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self.busy = False
        self.game_proc = None
        self.game_log: deque[str] = deque(maxlen=5000)
        self.log_window = None
        self.crash_dialog = None
        self.last_log_path: Path | None = None
        self.last_launch_instance: dict | None = None
        self.last_launch_started = 0.0
        self.last_launch_java = ""
        self.last_command = ""
        self.accounts = [
            account for account in (
                Account.from_dict(data) for data in self.cfg.accounts_data())
            if account is not None]
        self.account = Account.from_dict(self.cfg.account_data())
        self.selected_instance_id: str | None = None
        self.manifest_versions: list[dict] = []
        self.manifest_loading = False
        self.manifest_refreshing = False
        self.dl_selected: str | None = None
        self.loader_cache: dict[str, dict[str, list[str]]] = {
            name: {} for name in ("fabric", "forge", "neoforge",
                                  "quilt", "optifine")}
        self.modrinth_hits: list[dict] = []
        self.recommend_loading = False
        self.recommend_seq = 0
        self.mods_instance_id: str | None = None
        self.mod_inst_ids: list[str] = []
        self.content_hits: list[dict] = []
        self.content_kind = "resourcepack"
        self.content_inst_ids: list[str] = []
        self.content_seq = 0
        self.content_recommend_loading = False
        self.java_infos = []
        self.repo = self._make_repo()

        self._build_ui()
        self._load_settings_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll)
        self.after(300, self._ensure_manifest)

        self._refresh_account_label()
        self._refresh_instances()
        self.after(200, self._auto_import_local)
        self.after(900, self._scan_java)
        self.after(30 * 60 * 1000, self._periodic_manifest)

    # ------------------------------------------------------------------ setup
    def _make_repo(self) -> Repo:
        return Repo(
            self.cfg.game_root(),
            source=self.cfg.get("source", "official"),
            workers=int(self.cfg.get("workers", 16)),
            progress=self._emit_progress,
            log=self._emit_log,
            cancel=self.cancel_event,
            cache_dir=CACHE_DIR,
        )

    def _build_ui(self):
        header = tk.Frame(self, bg=PANEL, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="PyMC", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 22, "bold")).pack(side="left", padx=(22, 4), pady=10)
        tk.Label(header, text="LAUNCHER", bg=PANEL, fg=MUTED,
                 font=("Courier New", 10, "bold")).pack(side="left", pady=(26, 12))

        self.account_btn = tk.Button(
            header, text="未登录 · 点击登录", command=self.open_account_dialog,
            bd=0, relief="flat", highlightthickness=0, bg=PANEL2, fg=MUTED,
            activebackground=PANEL3, activeforeground=FG, font=FONT_BOLD,
            padx=16, pady=8, cursor="hand2")
        self.account_btn.pack(side="right", padx=22)

        status = tk.Frame(self, bg=PANEL2, height=34)
        status.pack(side="bottom", fill="x")
        status.pack_propagate(False)
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(status, textvariable=self.status_var, bg=PANEL2, fg=MUTED,
                 font=FONT_SMALL, anchor="w").pack(side="left", padx=14)
        self.progress = ttk.Progressbar(status, length=240, maximum=1.0,
                                        mode="determinate", style="TProgressbar")
        self.progress.pack(side="right", padx=(6, 14), pady=10)
        self.cancel_btn = flat_button(status, "取消任务", self._cancel_task,
                                      padx=10, pady=3, bg=PANEL3, fg=MUTED)
        self.cancel_btn.pack(side="right", padx=6)
        self.cancel_btn.configure(state="disabled")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self._build_start_tab()
        self._build_download_tab()
        self._build_mods_tab()
        self._build_content_tab()
        self._build_settings_tab()
        self.multiplayer_tab = MultiplayerTab(self.nb, self)
        self.nb.add(self.multiplayer_tab, text="  联机  ")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ------------------------------------------------------------- start tab
    def _build_start_tab(self):
        tab = ttk.Frame(self.nb, padding=0)
        self.nb.add(tab, text="  启动  ")
        tab.columnconfigure(0, minsize=390, weight=0)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        left = ttk.Frame(tab, style="Card.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=14)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        head = tk.Frame(left, bg=PANEL)
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="游戏实例", bg=PANEL, fg=FG, font=FONT_BOLD).pack(side="left")
        self.inst_count = tk.Label(head, text="", bg=PANEL, fg=MUTED, font=FONT_SMALL)
        self.inst_count.pack(side="right")

        tree_frame = tk.Frame(left, bg=PANEL)
        tree_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 10))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.inst_tree = ttk.Treeview(tree_frame, columns=("name", "version", "loader"),
                                      show="headings", selectmode="browse", height=14)
        for key, text, width, anchor in (
                ("name", "实例", 190, "w"),
                ("version", "版本", 130, "w"),
                ("loader", "加载器", 80, "w")):
            self.inst_tree.heading(key, text=text, anchor=anchor)
            self.inst_tree.column(key, width=width, anchor=anchor, stretch=True)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.inst_tree.yview)
        self.inst_tree.configure(yscrollcommand=scroll.set)
        self.inst_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.inst_tree.bind("<<TreeviewSelect>>", self._on_instance_select)
        self.inst_tree.bind("<Double-1>", lambda e: self._launch())

        buttons = tk.Frame(left, bg=PANEL)
        buttons.grid(row=2, column=0, sticky="ew")
        flat_button(buttons, "新建实例", self._new_instance, accent=True,
                    padx=12, pady=5).pack(side="left")
        flat_button(buttons, "编辑", self._edit_instance, padx=12, pady=5).pack(side="left", padx=6)
        flat_button(buttons, "删除", self._delete_instance, danger=True,
                    padx=12, pady=5).pack(side="left")
        flat_button(buttons, "刷新", lambda: self._refresh_instances(), padx=8, pady=5).pack(side="right")
        flat_button(buttons, "导入本地", self._import_local_versions, padx=8,
                    pady=5).pack(side="right", padx=(0, 6))

        right = ttk.Frame(tab, style="Card.TFrame", padding=20)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=14)
        right.columnconfigure(1, weight=1)

        tk.Label(right, text="启动游戏", bg=PANEL, fg=MUTED, font=FONT_SMALL).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.detail_title = tk.Label(right, text="未选择实例", bg=PANEL, fg=FG,
                                     font=("Courier New", 20, "bold"), anchor="w")
        self.detail_title.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 16))

        self.detail_vars = {}
        for row, (key, name) in enumerate((
                ("version", "游戏版本"), ("loader", "加载器"), ("dir", "游戏目录"),
                ("memory", "内存"), ("java", "Java"), ("account", "账户"), ("state", "状态")), start=2):
            tk.Label(right, text=name, bg=PANEL, fg=MUTED, font=FONT_SMALL,
                     width=10, anchor="w").grid(row=row, column=0, sticky="nw", pady=4)
            var = tk.StringVar(value="—")
            tk.Label(right, textvariable=var, bg=PANEL, fg=FG, font=FONT,
                     anchor="w", justify="left", wraplength=560).grid(
                row=row, column=1, sticky="w", pady=4)
            self.detail_vars[key] = var

        self.launch_btn = flat_button(right, "启动游戏", self._launch, accent=True, pady=12)
        self.launch_btn.configure(font=("Courier New", 14, "bold"))
        self.launch_btn.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(22, 8))

        actions = tk.Frame(right, bg=PANEL)
        actions.grid(row=11, column=0, columnspan=2, sticky="w")
        flat_button(actions, "打开游戏目录", self._open_game_dir, padx=12, pady=5).pack(side="left")
        flat_button(actions, "模组目录", self._open_mods_folder, padx=12, pady=5).pack(side="left", padx=6)
        flat_button(actions, "打开日志", self._open_log_window, padx=12, pady=5).pack(side="left")
        flat_button(actions, "补全文件", self._repair_selected_instance,
                    padx=10, pady=5).pack(side="left", padx=6)

        hint = tk.Label(right, bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left",
                        wraplength=560,
                        text="提示：首次使用请先在「下载」页安装版本；缺少 Java 可到「下载 → Java 运行时」一键安装。")
        hint.grid(row=12, column=0, columnspan=2, sticky="w", pady=(22, 0))

    # ---------------------------------------------------------- download tab
    def _build_download_tab(self):
        tab = ttk.Frame(self.nb, padding=16)
        self.nb.add(tab, text="  下载  ")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, minsize=360, weight=0)
        tab.rowconfigure(2, weight=1)

        toolbar = tk.Frame(tab, bg=BG)
        toolbar.grid(row=0, column=0, sticky="ew")
        self.version_filter = ttk.Combobox(toolbar, values=list(FTYPES.keys()),
                                           state="readonly", width=10, font=FONT)
        self.version_filter.set("正式版")
        self.version_filter.pack(side="left")
        self.version_filter.bind("<<ComboboxSelected>>", lambda e: self._refresh_version_tree())
        self.version_search = tk.StringVar()
        search = tk.Entry(toolbar, textvariable=self.version_search, bg=PANEL2, fg=FG,
                          insertbackground=FG, relief="flat", bd=0, font=FONT,
                          highlightthickness=1, highlightbackground=BORDER,
                          highlightcolor=ACCENT, width=28)
        search.pack(side="left", padx=8, ipady=5)
        self.version_search.trace_add("write", lambda *a: self._refresh_version_tree())
        flat_button(toolbar, "刷新列表", self._reload_manifest, padx=10, pady=4).pack(side="left")
        self.dl_count = tk.Label(toolbar, text="", bg=BG, fg=MUTED, font=FONT_SMALL)
        self.dl_count.pack(side="left", padx=10)

        tree_frame = tk.Frame(tab, bg=BG)
        tree_frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0), padx=(0, 12))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.version_tree = ttk.Treeview(tree_frame, columns=("version", "type", "date"),
                                         show="headings", selectmode="browse")
        for key, text, width, anchor in (("version", "版本", 240, "w"),
                                         ("type", "类型", 100, "w"),
                                         ("date", "发布日期", 120, "w")):
            self.version_tree.heading(key, text=text, anchor=anchor)
            self.version_tree.column(key, width=width, anchor=anchor, stretch=True)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.version_tree.yview)
        self.version_tree.configure(yscrollcommand=scroll.set)
        self.version_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.version_tree.bind("<<TreeviewSelect>>", self._on_version_select)

        side = tk.Frame(tab, bg=BG)
        side.grid(row=0, column=1, rowspan=3, sticky="nsew")
        side.columnconfigure(0, weight=1)

        install_card = ttk.Frame(side, style="Card.TFrame", padding=14)
        install_card.grid(row=0, column=0, sticky="ew")
        install_card.columnconfigure(1, weight=1)
        tk.Label(install_card, text="安装版本", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.dl_selected_var = tk.StringVar(value="在左侧选择一个版本")
        tk.Label(install_card, textvariable=self.dl_selected_var, bg=PANEL, fg=ACCENT,
                 font=FONT, anchor="w", justify="left", wraplength=310).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(4, 10))

        tk.Label(install_card, text="加载器", bg=PANEL, fg=MUTED, font=FONT_SMALL,
                 width=8, anchor="w").grid(row=2, column=0, sticky="w", pady=3)
        self.dl_loader = ttk.Combobox(install_card, values=list(LOADERS.keys()),
                                      state="readonly", font=FONT, width=20)
        self.dl_loader.set("原版")
        self.dl_loader.grid(row=2, column=1, sticky="ew", pady=3)
        self.dl_loader.bind("<<ComboboxSelected>>", self._on_loader_change)

        tk.Label(install_card, text="版本", bg=PANEL, fg=MUTED, font=FONT_SMALL,
                 width=8, anchor="w").grid(row=3, column=0, sticky="w", pady=3)
        self.dl_loader_ver = ttk.Combobox(install_card, values=[], state="disabled", font=FONT)
        self.dl_loader_ver.grid(row=3, column=1, sticky="ew", pady=3)

        self.assets_var = tk.BooleanVar(value=bool(self.cfg.get("download_assets", True)))
        ttk.Checkbutton(install_card, text="下载资源文件（首次约 200-500MB）",
                        variable=self.assets_var, style="Card.TCheckbutton").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.auto_instance_var = tk.BooleanVar(value=bool(self.cfg.get("auto_instance", True)))
        ttk.Checkbutton(install_card, text="安装完成后自动创建实例",
                        variable=self.auto_instance_var, style="Card.TCheckbutton").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.install_btn = flat_button(install_card, "开始下载", self._install_selected,
                                       accent=True, pady=7)
        self.install_btn.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(2, 10))

        self.dl_log_frame, self.dl_log_text = text_area(install_card, height=9)
        self.dl_log_frame.grid(row=7, column=0, columnspan=2, sticky="nsew")
        self._dl_log("等待开始。下载来源可在「设置」中切换。")

        java_card = ttk.Frame(side, style="Card.TFrame", padding=14)
        java_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        java_card.columnconfigure(0, weight=1)
        tk.Label(java_card, text="Java 运行时", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, sticky="w")
        self.java_combo = ttk.Combobox(java_card, state="readonly", font=FONT)
        self.java_combo.configure(values=[text for _c, text in java.COMPONENTS])
        self.java_combo.current(4)
        self.java_combo.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        flat_button(java_card, "下载并安装 Java", self._download_java,
                    pady=6).grid(row=2, column=0, sticky="ew")
        self.java_hint = tk.Label(java_card, text="", bg=PANEL, fg=MUTED,
                                  font=FONT_SMALL, anchor="w", justify="left", wraplength=310)
        self.java_hint.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._update_java_hint()

    # -------------------------------------------------------------- mods tab
    def _build_mods_tab(self):
        tab = ttk.Frame(self.nb, padding=16)
        self.nb.add(tab, text="  模组  ")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, minsize=400, weight=0)
        tab.rowconfigure(1, weight=1)

        top = tk.Frame(tab, bg=BG)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        tk.Label(top, text="实例", bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left")
        self.mod_inst_combo = ttk.Combobox(top, state="disabled", font=FONT, width=42)
        self.mod_inst_combo.pack(side="left", padx=8)
        self.mod_inst_combo.bind("<<ComboboxSelected>>", lambda e: self._on_mod_instance())
        flat_button(top, "刷新", self._refresh_mods_tab, padx=10, pady=4).pack(side="left")
        self.mod_count = tk.Label(top, text="", bg=BG, fg=MUTED, font=FONT_SMALL)
        self.mod_count.pack(side="left", padx=10)

        left = ttk.Frame(tab, style="Card.TFrame", padding=12)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        tk.Label(left, text="已安装模组", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, sticky="w")
        tree_frame = tk.Frame(left, bg=PANEL)
        tree_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.mod_tree = ttk.Treeview(
            tree_frame, columns=("name", "state", "compat", "size"),
            show="headings", selectmode="browse")
        for key, text, width, anchor, stretch in (
                ("name", "文件", 340, "w", True),
                ("state", "状态", 70, "w", False),
                ("compat", "兼容性", 80, "w", False),
                ("size", "大小", 80, "e", False)):
            self.mod_tree.heading(key, text=text, anchor=anchor)
            self.mod_tree.column(key, width=width, anchor=anchor, stretch=stretch)
        self.mod_tree.tag_configure("incompatible", foreground=ERR)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.mod_tree.yview)
        self.mod_tree.configure(yscrollcommand=scroll.set)
        self.mod_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.mod_tree.bind("<Double-1>", lambda e: self._toggle_mod())
        self.mod_tree.bind("<<TreeviewSelect>>", self._on_mod_select)
        self.mod_hint = tk.Label(left, text="", bg=PANEL, fg=MUTED,
                                 font=FONT_SMALL, anchor="w", justify="left",
                                 wraplength=360)
        self.mod_hint.grid(row=3, column=0, sticky="ew", pady=(6, 0))

        row = tk.Frame(left, bg=PANEL)
        row.grid(row=2, column=0, sticky="ew")
        flat_button(row, "启用 / 禁用", self._toggle_mod, padx=10, pady=5).pack(side="left")
        flat_button(row, "添加本地模组", self._add_mods, padx=10, pady=5).pack(side="left", padx=6)
        flat_button(row, "删除", self._delete_mod, danger=True, padx=10, pady=5).pack(side="left")
        flat_button(row, "打开文件夹", self._open_mods_folder, padx=10, pady=5).pack(side="right")

        right = ttk.Frame(tab, style="Card.TFrame", padding=12)
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)
        tk.Label(right, text="Modrinth 搜索", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, sticky="w")
        search_row = tk.Frame(right, bg=PANEL)
        search_row.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        search_row.columnconfigure(0, weight=1)
        self.mr_query = tk.StringVar()
        entry = tk.Entry(search_row, textvariable=self.mr_query, bg=PANEL2, fg=FG,
                         insertbackground=FG, relief="flat", bd=0, font=FONT,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.grid(row=0, column=0, sticky="ew", ipady=6)
        entry.bind("<Return>", lambda e: self._search_modrinth())
        flat_button(search_row, "搜索", self._search_modrinth, accent=True,
                    padx=12, pady=4).grid(row=0, column=1, padx=(6, 0))
        flat_button(search_row, "热门", self._recommend_mods,
                    padx=10, pady=4).grid(row=0, column=2, padx=(6, 0))

        result_frame = tk.Frame(right, bg=PANEL)
        result_frame.grid(row=2, column=0, sticky="nsew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        self.mr_tree = ttk.Treeview(result_frame, columns=("title", "downloads"),
                                    show="headings", selectmode="browse", height=9)
        self.mr_tree.heading("title", text="名称", anchor="w")
        self.mr_tree.column("title", width=260, anchor="w", stretch=True)
        self.mr_tree.heading("downloads", text="下载量", anchor="e")
        self.mr_tree.column("downloads", width=80, anchor="e", stretch=False)
        scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.mr_tree.yview)
        self.mr_tree.configure(yscrollcommand=scroll.set)
        self.mr_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.mr_tree.bind("<<TreeviewSelect>>", self._on_mr_select)
        self.mr_tree.bind("<Double-1>", lambda e: self._install_modrinth())

        self.mr_desc = tk.Label(right, text="输入关键词搜索模组；会自动匹配当前实例的游戏版本与加载器。",
                                bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w",
                                justify="left", wraplength=360, height=3)
        self.mr_desc.grid(row=3, column=0, sticky="ew", pady=(8, 6))
        flat_button(right, "安装选中模组", self._install_modrinth, accent=True,
                    pady=6).grid(row=4, column=0, sticky="ew")
        link_button(right, "从链接安装模组…", self._install_mod_link,
                    bg=PANEL).grid(row=5, column=0, sticky="w", pady=(6, 0))

    # ----------------------------------------------------------- content tab
    def _build_content_tab(self):
        tab = ttk.Frame(self.nb, padding=16)
        self.nb.add(tab, text="  资源  ")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, minsize=380, weight=0)
        tab.rowconfigure(1, weight=1)

        top = tk.Frame(tab, bg=BG)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        tk.Label(top, text="实例", bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left")
        self.content_inst_combo = ttk.Combobox(top, state="disabled", font=FONT, width=26)
        self.content_inst_combo.pack(side="left", padx=6)
        self.content_inst_combo.bind("<<ComboboxSelected>>",
                                     lambda e: self._on_content_instance())
        tk.Label(top, text="类型", bg=BG, fg=MUTED, font=FONT_SMALL).pack(
            side="left", padx=(12, 0))
        self.content_type = ttk.Combobox(top, values=tuple(CONTENT_TYPES),
                                         state="readonly", font=FONT, width=13)
        self.content_type.current(0)
        self.content_type.pack(side="left", padx=6)
        self.content_type.bind("<<ComboboxSelected>>",
                               lambda e: self._on_content_type())
        self.content_query = tk.StringVar()
        entry = tk.Entry(top, textvariable=self.content_query, bg=PANEL2, fg=FG,
                         insertbackground=FG, relief="flat", bd=0, font=FONT,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.pack(side="left", fill="x", expand=True, padx=(12, 6), ipady=5)
        entry.bind("<Return>", lambda e: self._search_content())
        flat_button(top, "搜索", self._search_content, accent=True,
                    padx=10, pady=4).pack(side="left")

        left = ttk.Frame(tab, style="Card.TFrame", padding=12)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        head = tk.Frame(left, bg=PANEL)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(head, text="资源列表", bg=PANEL, fg=FG, font=FONT_BOLD).pack(side="left")
        self.content_count = tk.Label(head, text="", bg=PANEL, fg=MUTED, font=FONT_SMALL)
        self.content_count.pack(side="right")
        tree_frame = tk.Frame(left, bg=PANEL)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.content_tree = ttk.Treeview(tree_frame, columns=("title", "info"),
                                         show="headings", selectmode="browse", height=16)
        self.content_tree.heading("title", text="名称", anchor="w")
        self.content_tree.column("title", width=300, anchor="w", stretch=True)
        self.content_tree.heading("info", text="信息", anchor="e")
        self.content_tree.column("info", width=160, anchor="e", stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.content_tree.yview)
        self.content_tree.configure(yscrollcommand=scroll.set)
        self.content_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.content_tree.bind("<<TreeviewSelect>>", self._on_content_select)
        self.content_tree.bind("<Double-1>", lambda e: self._install_content())

        right = ttk.Frame(tab, style="Card.TFrame", padding=14)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        self.content_title = tk.Label(right, text="选择资源", bg=PANEL, fg=FG,
                                      font=FONT_BIG, anchor="w", justify="left",
                                      wraplength=340)
        self.content_title.grid(row=0, column=0, sticky="ew")
        self.content_meta = tk.Label(right, text="", bg=PANEL, fg=MUTED,
                                     font=FONT_SMALL, anchor="w", justify="left",
                                     wraplength=340)
        self.content_meta.grid(row=1, column=0, sticky="ew", pady=(6, 8))
        self.content_desc = tk.Label(right, text="搜索资源包、光影、地图或皮肤。",
                                     bg=PANEL, fg=FG, font=FONT_SMALL, anchor="nw",
                                     justify="left", wraplength=340)
        self.content_desc.grid(row=2, column=0, sticky="new")
        self.content_btn = flat_button(right, "安装资源包", self._install_content,
                                       accent=True, pady=8)
        self.content_btn.grid(row=4, column=0, sticky="ew", pady=(12, 6))
        flat_button(right, "打开保存目录", self._open_content_folder,
                    padx=10, pady=4).grid(row=5, column=0, sticky="ew")

    def _refresh_content_tab(self):
        old_index = self.content_inst_combo.current()
        old_id = (self.content_inst_ids[old_index]
                  if 0 <= old_index < len(self.content_inst_ids) else None)
        instances = self._visible_instances()
        self.content_inst_ids = [str(i["id"]) for i in instances]
        values = [f"{i.get('name')}   [{i.get('version')}]" for i in instances]
        if values:
            self.content_inst_combo.configure(values=values, state="readonly")
            index = self.content_inst_ids.index(old_id) if old_id in self.content_inst_ids else 0
            self.content_inst_combo.current(index)
        else:
            self.content_inst_combo.configure(values=[], state="disabled")
            self.content_inst_combo.set("")
        self._auto_recommend_content()

    def _content_instance(self) -> dict | None:
        index = self.content_inst_combo.current()
        if index < 0 or index >= len(self.content_inst_ids):
            return None
        return self.cfg.instance(self.content_inst_ids[index])

    def _on_content_instance(self):
        self._on_content_type()

    def _on_content_type(self, recommend: bool = True):
        self.content_seq += 1
        self.content_recommend_loading = False
        self.content_hits = []
        self.content_tree.delete(*self.content_tree.get_children())
        self.content_count.configure(text="")
        self.content_title.configure(text="选择资源")
        self.content_meta.configure(text="")
        self.content_desc.configure(text="搜索资源包、光影、地图或皮肤。")
        kind = CONTENT_TYPES.get(self.content_type.get(), "resourcepack")
        self.content_kind = kind
        text = {"resourcepack": "安装资源包", "shader": "安装光影",
                "world": "安装地图", "skin": "下载皮肤"}.get(kind, "下载")
        self.content_btn.configure(text=text)
        if recommend:
            self._auto_recommend_content()

    def _auto_recommend_content(self):
        if (self.content_hits or self.content_query.get().strip()
                or self.content_recommend_loading):
            return
        if not self._content_instance():
            return
        self.content_recommend_loading = True
        self._search_content(recommend=True)

    def _search_content(self, recommend: bool = False):
        inst = self._content_instance()
        if not inst:
            if not recommend:
                messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        if not recommend:
            self.content_recommend_loading = False
        kind = CONTENT_TYPES.get(self.content_type.get(), "resourcepack")
        self.content_kind = kind
        query = self.content_query.get().strip().lower()
        mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
        loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
        if kind == "world":
            work = lambda: worlds.search_worlds(query, mc, limit=30)
        elif kind == "skin":
            work = lambda: skins.search_skins(query, sort="likes", page=1)
        else:
            work = lambda: modlib.search_modrinth(
                query, mc, "", limit=30,
                index="downloads" if not query else "relevance",
                project_type=kind)
        self.content_seq += 1
        seq = self.content_seq
        self._set_status("加载推荐资源…" if recommend else "搜索资源…")

        def run():
            try:
                return work()
            except Exception:
                if recommend:
                    return []
                raise

        def done(hits):
            if seq != self.content_seq:
                return
            self.content_recommend_loading = False
            self._on_content_search_done(hits, kind)

        self._async(run, done, "搜索资源失败")

    def _on_content_search_done(self, hits, kind):
        self.content_hits = hits or []
        self.content_kind = kind
        self.content_tree.delete(*self.content_tree.get_children())
        for index, hit in enumerate(self.content_hits):
            if kind == "skin":
                info = (f"❤ {int(hit.get('likes') or 0)} · "
                        f"{hit.get('type') or '?'} · {hit.get('nickname') or ''}")
                title = hit.get("name") or "?"
            else:
                info = f"下载 {int(hit.get('downloads') or 0):,}"
                title = hit.get("title") or hit.get("slug") or "?"
            self.content_tree.insert("", "end", iid=str(index), values=(title, info))
        self.content_count.configure(text=f"{len(self.content_hits)} 个")
        rows = self.content_tree.get_children()
        if rows:
            self._set_status(f"找到 {len(self.content_hits)} 个资源")
            self.content_tree.selection_set(rows[0])
            self._on_content_select()
        else:
            query = self.content_query.get().strip()
            self._on_content_type(recommend=False)
            self._set_status("没有找到资源，请换个关键词" if query else "暂无推荐内容")

    def _on_content_select(self, _event=None):
        selection = self.content_tree.selection()
        if not selection:
            return
        try:
            hit = self.content_hits[int(selection[0])]
        except (ValueError, IndexError):
            return
        kind = self.content_kind
        if kind == "skin":
            title = hit.get("name") or "?"
            meta = (f"类型 {hit.get('type') or '?'} · "
                    f"❤ {int(hit.get('likes') or 0)} · "
                    f"作者 {hit.get('nickname') or '?'}")
            desc = ("下载为 PNG 皮肤文件，保存到实例的 skins 目录。"
                    "离线/外置账户需要 CustomSkinLoader 或手动上传才能显示。")
            action = "下载皮肤"
        elif kind == "world":
            title = hit.get("title") or "?"
            meta = (f"CurseForge · 下载 {int(hit.get('downloads') or 0):,} · "
                    f"作者 {hit.get('author') or '?'}")
            desc = (hit.get("description") or "") + "\n\n下载后会解压到 saves 目录。"
            action = "安装地图"
        else:
            title = hit.get("title") or "?"
            meta = (f"下载 {int(hit.get('downloads') or 0):,} · "
                    f"作者 {hit.get('author') or '?'}")
            desc = (hit.get("description") or "") + "\n\n会自动匹配当前实例版本。"
            action = "安装资源包" if kind == "resourcepack" else "安装光影"
        self.content_title.configure(text=title)
        self.content_meta.configure(text=meta)
        self.content_desc.configure(text=desc.strip())
        self.content_btn.configure(text=action)

    def _install_content(self):
        inst = self._content_instance()
        if not inst:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        selection = self.content_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个资源。", parent=self)
            return
        try:
            hit = self.content_hits[int(selection[0])]
        except (ValueError, IndexError):
            return
        kind = self.content_kind
        game_dir = inst.get("game_dir", self.repo.root)
        mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
        loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
        if kind == "skin":
            work = lambda: skins.download_skin(
                hit.get("tid"), hit.get("name") or "skin", game_dir,
                progress_cb=self._emit_progress, cancel=self.cancel_event,
                model="slim" if hit.get("type") == "alex" else "")
        elif kind == "world":
            work = lambda: worlds.install_world(
                str(hit.get("project_id") or ""), hit.get("title") or "world",
                game_dir, mc, progress_cb=self._emit_progress,
                cancel=self.cancel_event)
        else:
            work = lambda: modlib.install_content(
                str(hit.get("project_id") or ""), kind, mc, loader, game_dir,
                progress_cb=self._emit_progress, cancel=self.cancel_event)
        self._async(work, self._on_content_installed, "下载资源失败", locked=True)

    def _on_content_installed(self, result):
        self._set_status(f"已完成：{result.get('filename')}")
        messagebox.showinfo("下载完成",
                            f"{result.get('filename')}\n{result.get('path')}",
                            parent=self)

    def _open_content_folder(self):
        inst = self._content_instance()
        if not inst:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        names = {"resourcepack": "resourcepacks", "shader": "shaderpacks",
                 "world": "saves", "skin": "skins"}
        name = names.get(self.content_kind, "resourcepacks")
        self._open_path(Path(inst.get("game_dir", self.repo.root)) / name)

    # ---------------------------------------------------------- settings tab
    def _build_settings_tab(self):
        tab = ttk.Frame(self.nb, padding=16)
        self.nb.add(tab, text="  设置  ")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        def entry(parent, var):
            return tk.Entry(parent, textvariable=var, bg=PANEL2, fg=FG, insertbackground=FG,
                            relief="flat", bd=0, font=FONT, highlightthickness=1,
                            highlightbackground=BORDER, highlightcolor=ACCENT)

        def label(parent, text):
            tk.Label(parent, text=text, bg=PANEL, fg=MUTED, font=FONT_SMALL,
                     anchor="w").grid(sticky="w", padx=(0, 10), pady=4)

        # paths
        path_card = ttk.Frame(tab, style="Card.TFrame", padding=14)
        path_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        path_card.columnconfigure(1, weight=1)
        tk.Label(path_card, text="路径", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.s_game_root = tk.StringVar()
        label(path_card, "游戏目录")
        entry(path_card, self.s_game_root).grid(row=1, column=1, sticky="ew", pady=4, ipady=5)
        flat_button(path_card, "浏览", self._browse_game_root, padx=10, pady=3).grid(
            row=1, column=2, padx=(6, 0))
        self.s_java = tk.StringVar()
        label(path_card, "Java 路径")
        entry(path_card, self.s_java).grid(row=2, column=1, sticky="ew", pady=4, ipady=5)
        row = tk.Frame(path_card, bg=PANEL)
        row.grid(row=2, column=2, sticky="e", padx=(6, 0))
        flat_button(row, "浏览", self._browse_java, padx=10, pady=3).pack(side="left")
        flat_button(row, "扫描", self._scan_java, padx=10, pady=3).pack(side="left", padx=(4, 0))
        tk.Label(path_card, text="留空表示自动选择（优先匹配版本需要的 Java 版本）",
                 bg=PANEL, fg=MUTED, font=FONT_SMALL).grid(
            row=3, column=1, columnspan=2, sticky="w")
        tk.Label(path_card, text="检测到的 Java（双击使用）", bg=PANEL, fg=MUTED,
                 font=FONT_SMALL).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 4))
        self.java_list = tk.Listbox(path_card, height=4, bg=INPUT, fg=FG, font=FONT_SMALL,
                                    relief="flat", bd=0, highlightthickness=1,
                                    highlightbackground=BORDER, selectbackground=ACCENT_DARK,
                                    selectforeground=SELECT_FG, activestyle="none")
        self.java_list.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.java_list.bind("<Double-1>", self._pick_java)

        # launch
        launch_card = ttk.Frame(tab, style="Card.TFrame", padding=14)
        launch_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        launch_card.columnconfigure(1, weight=1)
        tk.Label(launch_card, text="启动与下载", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.s_memory = tk.StringVar()
        label(launch_card, "默认内存 (MB)")
        tk.Spinbox(launch_card, from_=512, to=32768, increment=512,
                   textvariable=self.s_memory, bg=PANEL2, fg=FG, buttonbackground=PANEL3,
                   relief="flat", bd=0, font=FONT, width=10, insertbackground=FG,
                   highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=ACCENT).grid(row=1, column=1, sticky="w", pady=4)
        self.s_workers = tk.StringVar()
        label(launch_card, "下载线程")
        tk.Spinbox(launch_card, from_=1, to=64, increment=1, textvariable=self.s_workers,
                   bg=PANEL2, fg=FG, buttonbackground=PANEL3, relief="flat", bd=0,
                   font=FONT, width=10, insertbackground=FG, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACCENT).grid(
            row=2, column=1, sticky="w", pady=4)
        self.s_source = tk.StringVar()
        label(launch_card, "下载来源")
        self.source_combo = ttk.Combobox(launch_card, textvariable=self.s_source,
                                        values=list(SOURCES.keys()), state="readonly", font=FONT)
        self.source_combo.grid(row=3, column=1, sticky="ew", pady=4)
        self.s_client = tk.StringVar()
        label(launch_card, "微软 Client ID")
        entry(launch_card, self.s_client).grid(row=4, column=1, sticky="ew", pady=4, ipady=5)
        tk.Label(launch_card, text="默认使用公开启动器 Client ID，通常无需修改。",
                 bg=PANEL, fg=MUTED, font=FONT_SMALL).grid(
            row=5, column=1, sticky="w")
        self.s_mod_api = tk.StringVar()
        label(launch_card, "模组 API")
        entry(launch_card, self.s_mod_api).grid(row=6, column=1, sticky="ew",
                                                pady=4, ipady=5)
        tk.Label(launch_card,
                 text="默认 https://api.modrinth.com/v2，可改为镜像或自建 API。",
                 bg=PANEL, fg=MUTED, font=FONT_SMALL).grid(
            row=7, column=1, sticky="w")
        self.s_open_log = tk.BooleanVar()
        ttk.Checkbutton(launch_card, text="启动游戏后打开日志窗口",
                        variable=self.s_open_log, style="Card.TCheckbutton").grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(10, 2))
        self.s_newer_java = tk.BooleanVar()
        ttk.Checkbutton(launch_card, text="允许使用更高版本的 Java",
                        variable=self.s_newer_java, style="Card.TCheckbutton").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=2)

        # account
        account_card = ttk.Frame(tab, style="Card.TFrame", padding=14)
        account_card.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(12, 0))
        account_card.columnconfigure(0, weight=1)
        tk.Label(account_card, text="账户", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, sticky="w")
        self.s_account = tk.StringVar(value="未登录")
        tk.Label(account_card, textvariable=self.s_account, bg=PANEL, fg=MUTED,
                 font=FONT_SMALL, anchor="w", justify="left").grid(
            row=1, column=0, sticky="w", pady=(6, 8))
        account_actions = tk.Frame(account_card, bg=PANEL)
        account_actions.grid(row=2, column=0, sticky="w")
        flat_button(account_actions, "管理账户", self.open_account_dialog,
                    padx=12, pady=4).pack(side="left")
        flat_button(account_actions, "皮肤", self.open_skin_viewer,
                    padx=10, pady=4).pack(side="left", padx=6)

        # about
        about_card = ttk.Frame(tab, style="Card.TFrame", padding=14)
        about_card.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(12, 0))
        tk.Label(about_card, text="关于", bg=PANEL, fg=FG, font=FONT_BOLD).grid(
            row=0, column=0, sticky="w")
        tk.Label(about_card, justify="left", anchor="w", wraplength=440,
                 bg=PANEL, fg=MUTED, font=FONT_SMALL,
                 text="PyMC Launcher 1.0\n"
                      "纯 Python 标准库 + tkinter，无第三方依赖。\n"
                      "支持原版 / Fabric / Forge 下载、模组管理、"
                      "Modrinth 搜索与正版 / 离线启动。\n"
                      "字体：Courier New。").grid(row=1, column=0, sticky="w", pady=(6, 0))

        bottom = tk.Frame(tab, bg=BG)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        flat_button(bottom, "保存设置", self._save_settings, accent=True,
                    padx=20, pady=7).pack(side="left")
        self.s_saved = tk.Label(bottom, text="", bg=BG, fg=ACCENT, font=FONT_SMALL)
        self.s_saved.pack(side="left", padx=12)

    # --------------------------------------------------------------- events
    def _poll(self):
        try:
            while True:
                event = self.msg_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def _handle_event(self, event):
        kind = event[0]
        if kind == "progress":
            self._apply_progress(event[1])
        elif kind == "log":
            self._dl_log(event[1])
        elif kind == "status":
            self._set_status(event[1])
        elif kind == "task_done":
            _, result, callback, locked = event
            if locked:
                self._finish_locked()
            if callback:
                try:
                    callback(result)
                except Exception as e:
                    logging.exception("callback failed")
                    messagebox.showerror("操作失败", str(e), parent=self)
        elif kind == "task_error":
            _, error, title, locked = event[:4]
            error_done = event[4] if len(event) > 4 else None
            if locked:
                self._finish_locked()
                self._set_launch_state(bool(self.game_proc and self.game_proc.poll() is None))
            logging.error("task error: %s", error)
            self._set_status(f"{title}：{error}")
            self._dl_log(f"[错误] {error}")
            messagebox.showerror(title, str(error), parent=self)
            if error_done:
                try:
                    error_done(error)
                except Exception:
                    logging.exception("error callback failed")
        elif kind == "task_cancel":
            if event[1]:
                self._finish_locked()
                self._set_launch_state(bool(self.game_proc and self.game_proc.poll() is None))
            self._set_status("已取消")
            self._dl_log("[取消] 任务已取消")
        elif kind == "game_log":
            self._append_game_log(event[1])
        elif kind == "game_exit":
            self._on_game_exit(event[1], event[2])

    # ------------------------------------------------------------- utilities
    def _emit_progress(self, done, total, bdone, btotal, label, elapsed):
        self.msg_queue.put(("progress", (done, total, bdone, btotal, label, elapsed)))

    def _emit_log(self, message: str):
        self.msg_queue.put(("log", message))

    def _set_status(self, text: str):
        self.status_var.set(text)

    def _apply_progress(self, data):
        done, total, bdone, btotal, label, elapsed = data
        if btotal:
            frac = min(1.0, bdone / btotal) if btotal else 0.0
            speed = bdone / elapsed if elapsed > 0.6 else 0
            text = f"{label}   {frac * 100:5.1f}%   {human_size(bdone)} / {human_size(btotal)}"
            if speed:
                text += f"   {human_size(speed)}/s"
        elif total:
            frac = min(1.0, done / total)
            text = f"{label}   {done}/{total}   {frac * 100:.0f}%"
        else:
            frac = 0.0
            text = label or "处理中…"
        self.progress.configure(value=frac)
        self._set_status(text)

    def _finish_locked(self):
        self.busy = False
        self.progress.configure(value=0)
        self.cancel_btn.configure(state="disabled")

    def _async(self, fn, done=None, error_title="操作失败", locked=False,
               error_done=None):
        if locked and self.busy:
            messagebox.showinfo("请稍候", "当前有任务正在进行。", parent=self)
            return False
        if locked:
            self.busy = True
            self.cancel_event.clear()
            self.cancel_btn.configure(state="normal")
            self.progress.configure(value=0)
            self._set_status("处理中…")
        else:
            self.cancel_event.clear()

        def worker():
            try:
                result = fn()
            except Cancelled:
                self.msg_queue.put(("task_cancel", locked))
            except Exception as e:
                logging.exception("background task failed")
                self.msg_queue.put(("task_error", e, error_title, locked, error_done))
            else:
                self.msg_queue.put(("task_done", result, done, locked))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _cancel_task(self):
        self.cancel_event.set()
        self._set_status("正在取消…")

    def _dl_log(self, message: str):
        try:
            append_text(self.dl_log_text, message)
        except Exception:
            pass

    def _on_tab_changed(self, _event=None):
        try:
            index = self.nb.index(self.nb.select())
        except Exception:
            return
        if index == 0:
            self._refresh_instances(select_id=self.selected_instance_id)
        elif index == 1:
            self._ensure_manifest()
        elif index == 2:
            self._refresh_mods_tab()
        elif index == 3:
            self._refresh_content_tab()
        elif index == 5:
            self.multiplayer_tab.refresh_name()

    # --------------------------------------------------------------- account
    def _refresh_account_label(self):
        account = self.account
        if account:
            if account.is_microsoft:
                short, full = "正版", "微软正版"
            elif account.is_external:
                short, full = "外置", "外置登录"
            else:
                short, full = "离线", "离线"
            text = f"{account.name} · {short}"
            self.account_btn.configure(
                text=text, fg=ACCENT if account.type != "offline" else FG)
            self.s_account.set(f"{account.name}（{full}）")
        else:
            self.account_btn.configure(text="未登录 · 点击登录", fg=MUTED)
            self.s_account.set("未登录")
        self._update_instance_details()

    def open_account_dialog(self):
        AccountDialog(self)

    def open_skin_viewer(self):
        from ui.skinview import SkinViewerDialog
        SkinViewerDialog(self)

    def start_ms_login(self):
        DeviceCodeDialog(self)

    def _reload_accounts(self):
        self.accounts = [
            account for account in (
                Account.from_dict(data) for data in self.cfg.accounts_data())
            if account is not None]
        self.account = Account.from_dict(self.cfg.account_data())
        self._refresh_account_label()

    def set_account(self, account: Account | None):
        if account is None:
            self.cfg.set_accounts_data([])
            self._reload_accounts()
            return
        key = self.cfg.add_or_update_account(account.to_dict())
        self.accounts = [
            item for item in (
                Account.from_dict(data) for data in self.cfg.accounts_data())
            if item is not None]
        self.account = account
        self._refresh_account_label()
        kind = ("正版" if account.is_microsoft
                else "外置" if account.is_external else "离线")
        self._set_status(f"已登录：{account.name}")
        self._dl_log(f"[账户] {account.name}（{kind}）")

    def switch_account(self, key: str) -> bool:
        if not self.cfg.set_active_account(key):
            return False
        self._reload_accounts()
        if self.account:
            self._set_status(f"已切换到：{self.account.name}")
        return True

    def remove_account(self, key: str):
        self.cfg.remove_account(key)
        self._reload_accounts()

    # ------------------------------------------------------------- instances
    def _instance_in_root(self, inst: dict) -> bool:
        raw = inst.get("game_dir") or self.repo.root
        try:
            root = os.path.normcase(os.path.abspath(str(self.repo.root)))
            path = os.path.normcase(os.path.abspath(str(raw)))
            return os.path.commonpath([root, path]) == root
        except (OSError, ValueError):
            return False

    def _visible_instances(self) -> list[dict]:
        return [inst for inst in self.cfg.instances if self._instance_in_root(inst)]

    def _refresh_instances(self, select_id=None):
        target = select_id or self.selected_instance_id
        self.inst_tree.delete(*self.inst_tree.get_children())
        instances = self._visible_instances()
        for inst in instances:
            loader = inst.get("loader") or "vanilla"
            self.inst_tree.insert("", "end", iid=inst["id"], values=(
                inst.get("name") or inst.get("version"),
                inst.get("mc_version") or inst.get("version"),
                LOADER_NAMES.get(loader, loader),
            ))
        count = len(instances)
        self.inst_count.configure(text=f"{count} 个")
        rows = self.inst_tree.get_children()
        if rows:
            target = target if target in rows else rows[0]
            self.inst_tree.selection_set(target)
            self.inst_tree.focus(target)
            self.inst_tree.see(target)
            self.selected_instance_id = target
        else:
            self.selected_instance_id = None
        self._update_instance_details()

    def _auto_import_local(self):
        if self._visible_instances() or self.cfg.get("local_imported"):
            return
        try:
            self._import_local_versions(silent=True)
        except Exception:
            pass

    def _import_local_versions(self, silent=False):
        try:
            local = self.repo.installed()
        except Exception as e:
            if not silent:
                messagebox.showerror("导入失败", str(e), parent=self)
            return
        existing = {str(i.get("version") or "") for i in self._visible_instances()}
        added = 0
        for item in local:
            vid = str(item.get("id") or "")
            if not vid or vid in existing:
                continue
            try:
                loader = modlib.detect_loader(self.repo, vid)
                mc = (modlib.mc_version_of(self.repo, vid)
                      or str(item.get("clientVersion") or "") or vid)
            except Exception:
                loader = "vanilla"
                mc = str(item.get("clientVersion") or "") or vid
            try:
                self.cfg.add_instance(vid, vid, self._instance_game_dir(vid),
                                      loader=loader, mc_version=mc, save=False)
            except Exception:
                continue
            existing.add(vid)
            added += 1
        self.cfg.set("local_imported", True)
        self._refresh_instances()
        try:
            self._refresh_mods_tab()
        except Exception:
            pass
        if silent:
            if added:
                self._set_status(f"已自动导入 {added} 个本地版本")
            return
        if added:
            messagebox.showinfo("导入完成", f"已导入 {added} 个本地版本。", parent=self)
        else:
            messagebox.showinfo("导入完成", "没有发现新的本地版本。", parent=self)

    def _instance_game_dir(self, version_id: str) -> Path:
        version_dir = self.repo.version_dir(version_id)
        for marker in ("mods", "config", "options.txt", "saves", "resourcepacks"):
            if (version_dir / marker).exists():
                return version_dir
        return self.repo.root

    def _selected_instance(self) -> dict | None:
        if not self.selected_instance_id:
            return None
        return self.cfg.instance(self.selected_instance_id)

    def _on_instance_select(self, _event=None):
        selection = self.inst_tree.selection()
        if selection:
            self.selected_instance_id = selection[0]
        self._update_instance_details()

    def _update_instance_details(self):
        inst = self._selected_instance()
        if not inst:
            self.detail_title.configure(text="未选择实例")
            for key, var in self.detail_vars.items():
                var.set("—")
            self.detail_vars["account"].set(self.account.display() if self.account else "未登录")
            self._set_launch_state(False)
            return
        self.detail_title.configure(text=inst.get("name") or inst.get("version"))
        loader = inst.get("loader") or "vanilla"
        memory = inst.get("memory") or self.cfg.get("memory_mb", 4096)
        version_file = self.repo.version_json_path(inst.get("version", ""))
        state = "游戏运行中" if (self.game_proc and self.game_proc.poll() is None) else (
            "就绪" if version_file.is_file() else "版本文件缺失")
        self.detail_vars["version"].set(inst.get("version", "—"))
        self.detail_vars["loader"].set(LOADER_NAMES.get(loader, loader))
        self.detail_vars["dir"].set(inst.get("game_dir", "—"))
        self.detail_vars["memory"].set(f"{memory} MB")
        java_path = inst.get("java") or self.cfg.get("java_path") or "自动检测"
        self.detail_vars["java"].set(java_path)
        self.detail_vars["account"].set(self.account.display() if self.account else "未登录")
        self.detail_vars["state"].set(state)
        running = bool(self.game_proc and self.game_proc.poll() is None)
        self._set_launch_state(running)

    def _set_launch_state(self, running: bool):
        if running:
            self.launch_btn.configure(text="游戏运行中…", state="disabled",
                                      bg=ACCENT_DISABLED_BG, fg=ACCENT_DISABLED_FG)
        else:
            self.launch_btn.configure(text="启动游戏", state="normal",
                                      bg=ACCENT, fg=DARK_FG)

    def detect_loader(self, version_id: str) -> str:
        try:
            return modlib.detect_loader(self.repo, version_id)
        except Exception:
            return "vanilla"

    def mc_version_of(self, version_id: str) -> str:
        try:
            return modlib.mc_version_of(self.repo, version_id)
        except Exception:
            return version_id

    def create_instance(self, name, version_id, game_dir, memory, jvm_args):
        game_dir = Path(game_dir)
        ensure_dir(game_dir)
        ensure_dir(modlib.mods_dir(game_dir))
        loader = self.detect_loader(version_id)
        mc_version = self.mc_version_of(version_id)
        inst = self.cfg.add_instance(name, version_id, game_dir, memory=memory,
                                     jvm_args=jvm_args, loader=loader,
                                     mc_version=mc_version)
        self.selected_instance_id = inst["id"]
        self._refresh_instances(select_id=inst["id"])
        self._refresh_mods_tab()
        self._set_status(f"已创建实例：{inst['name']}")
        return inst

    def update_instance_fields(self, inst_id, **fields):
        self.cfg.update_instance(inst_id, **fields)
        self._refresh_instances(select_id=inst_id)
        self._set_status("实例已更新")

    def _new_instance(self):
        InstanceDialog(self)

    def _edit_instance(self):
        inst = self._selected_instance()
        if not inst:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        InstanceDialog(self, inst)

    def _delete_instance(self):
        inst = self._selected_instance()
        if not inst:
            return
        if not messagebox.askyesno("删除实例",
                                   f"确定删除实例「{inst.get('name')}」吗？\n"
                                   "只会移除实例记录，不会删除游戏文件。", parent=self):
            return
        self.cfg.remove_instance(inst["id"])
        self.selected_instance_id = None
        self._refresh_instances()
        self._refresh_mods_tab()
        self._set_status("实例已删除")

    def _open_path(self, path):
        path = Path(path)
        try:
            ensure_dir(path)
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("打开失败", str(e), parent=self)

    def _open_game_dir(self):
        inst = self._selected_instance()
        if inst:
            self._open_path(inst.get("game_dir", self.repo.root))

    def _open_mods_folder(self):
        inst = self._selected_instance()
        if not inst:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        self._open_path(modlib.mods_dir(inst.get("game_dir", self.repo.root)))

    # ---------------------------------------------------------------- launch
    def _launch(self):
        inst = self._selected_instance()
        if not inst:
            messagebox.showinfo("提示", "请先创建或选择一个实例。", parent=self)
            return
        if self.busy:
            messagebox.showinfo("请稍候", "当前有任务正在进行。", parent=self)
            return
        if self.game_proc and self.game_proc.poll() is None:
            messagebox.showinfo("提示", "游戏正在运行。", parent=self)
            return
        if not self.account:
            self.open_account_dialog()
            return
        if not self.repo.version_json_path(inst.get("version", "")).is_file():
            messagebox.showerror("无法启动", "版本文件缺失，请在下载页重新安装该版本。", parent=self)
            return

        memory = int(inst.get("memory") or self.cfg.get("memory_mb", 4096) or 4096)
        options = launch.LaunchOptions(
            memory_mb=memory,
            jvm_args=inst.get("jvm_args", ""),
            java_path=inst.get("java") or self.cfg.get("java_path", ""),
            client_id=self.cfg.get("ms_client_id") or DEFAULT_CLIENT_ID,
            allow_newer_java=bool(self.cfg.get("allow_newer_java", True)),
        )
        game_dir = Path(inst.get("game_dir") or self.repo.root)
        ensure_dir(game_dir)
        ensure_dir(modlib.mods_dir(game_dir))
        repo = self.repo
        cfg_client = self.cfg.get("ms_client_id") or DEFAULT_CLIENT_ID
        log_path = LOG_DIR / f"game-{time.strftime('%Y%m%d-%H%M%S')}.log"
        account = self.account

        def work():
            acc = auth.ensure_account(account, cfg_client)
            if acc.is_external:
                from core.authlib import ensure_authlib_injector
                ensure_authlib_injector(self._emit_progress, self.cancel_event)
            plan = launch.build_command(repo, inst["version"], game_dir, acc, options)
            proc = launch.spawn(plan["cmd"], plan["cwd"], plan["env"], log_path,
                                on_line=self._game_line)
            return acc, plan, proc, log_path, dict(inst)

        self._async(work, self._on_launched, "启动失败", locked=True)
        self._set_status("启动中…")
        self.launch_btn.configure(text="启动中…", state="disabled")

    def _game_line(self, line: str):
        self.msg_queue.put(("game_log", line))

    def _on_launched(self, result):
        account, plan, proc, log_path, instance = result
        self.last_log_path = log_path
        self.game_proc = proc
        self.last_launch_instance = dict(instance) if instance else None
        self.last_launch_started = time.time()
        self.last_launch_java = str(plan["java_info"].major)
        self.last_command = launch.redact_command(plan["cmd"])
        self.game_log.clear()
        self.game_log.append(f"$ {self.last_command}")
        self.game_log.append(f"# Java: {plan['java']}")
        self.game_log.append(f"# 日志文件: {log_path}")
        self.game_log.append("")
        if account is not self.account:
            self.set_account(account)
        self._update_instance_details()
        self._set_status("游戏已启动")
        self._dl_log(f"[启动] {plan['version']}（Java {plan['java_info'].major}）")
        if self.log_window and self.log_window.winfo_exists():
            for line in self.game_log:
                self.log_window.append(line, scroll=False)
        elif bool(self.cfg.get("open_log", True)):
            self._open_log_window()
        threading.Thread(target=self._watch_game, args=(proc,), daemon=True).start()

    def _watch_game(self, proc):
        code = proc.wait()
        self.msg_queue.put(("game_exit", code, proc))

    def _on_game_exit(self, code, proc):
        if proc is not self.game_proc:
            return
        self.game_proc = None
        self._set_launch_state(False)
        self._update_instance_details()
        message = "游戏已退出" if code == 0 else f"游戏已退出（代码 {code}）"
        self._set_status(message)
        self._append_game_log(f"[退出] {message}")
        self._dl_log(f"[退出] {message}")
        if code != 0 or self._latest_crash_report(self.last_launch_instance):
            self.after(300, lambda: self._show_crash_dialog(code))

    def _read_text_file(self, path) -> str:
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _latest_crash_report(self, instance: dict | None):
        since = self.last_launch_started or (time.time() - 300)
        dirs = []
        if instance:
            dirs.append(Path(instance.get("game_dir") or self.repo.root) / "crash-reports")
        dirs.append(self.repo.root / "crash-reports")
        seen = set()
        latest = None
        for folder in dirs:
            try:
                key = str(folder.resolve())
            except Exception:
                key = str(folder)
            if key in seen or not folder.is_dir():
                continue
            seen.add(key)
            for path in folder.glob("crash-*.txt"):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime < since - 10:
                    continue
                if latest is None or mtime > latest[0]:
                    latest = (mtime, path)
        return latest[1] if latest else None

    def _show_crash_dialog(self, code: int):
        instance = self.last_launch_instance or self._selected_instance()
        crash_path = self._latest_crash_report(instance)
        crash_text = self._read_text_file(crash_path)
        log_text = self._read_text_file(self.last_log_path) or "\n".join(self.game_log)
        context = {
            "version": (instance or {}).get("version") or "",
            "java": self.last_launch_java,
            "memory": int((instance or {}).get("memory")
                          or self.cfg.get("memory_mb", 4096) or 4096),
        }
        findings = diagnose.diagnose(log_text, crash_text, context)
        report = diagnose.build_report(
            instance, code, findings, crash_text, log_text,
            self.last_command, str(crash_path or ""),
            str(self.last_log_path or ""))
        try:
            exists = bool(self.crash_dialog and self.crash_dialog.winfo_exists())
        except Exception:
            exists = False
        if exists:
            self.crash_dialog.destroy()
        self.crash_dialog = CrashDialog(
            self, instance or {}, code, crash_path, self.last_log_path,
            findings, report)

    def _repair_selected_instance(self):
        instance = self._selected_instance()
        if not instance:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        if not messagebox.askyesno(
                "补全游戏文件",
                "将校验客户端、依赖库、资源文件和 natives。\n"
                "缺失或损坏的文件会重新下载，可能需要一些时间。\n是否继续？",
                parent=self):
            return
        self.repair_instance(instance, self._on_manual_repaired)

    def _on_manual_repaired(self, result):
        messagebox.showinfo(
            "补全完成",
            f"版本 {result.get('version')} 的游戏文件校验完成。",
            parent=self)

    def repair_instance(self, instance: dict, done=None, error_done=None):
        if self.game_proc and self.game_proc.poll() is None:
            messagebox.showinfo("提示", "游戏正在运行，请先退出游戏。", parent=self)
            return False
        version_id = str(instance.get("version") or "")
        with_assets = True

        def work():
            if not version_id:
                raise LauncherError("实例缺少版本 ID，无法补全。")
            native_dir = (self.repo.versions_dir / version_id
                          / f"{version_id}-natives-pymc")
            shutil.rmtree(native_dir, ignore_errors=True)
            self.repo.install(version_id, with_assets=with_assets)
            self.repo.extract_natives(self.repo.merged(version_id), version_id)
            return {"version": version_id}

        def finish(result):
            self._refresh_instances(select_id=instance.get("id"))
            self._set_status(f"游戏文件补全完成：{version_id}")
            if done:
                try:
                    done(result)
                except Exception:
                    logging.exception("repair callback failed")

        return self._async(work, finish, "补全游戏文件失败", locked=True,
                           error_done=error_done)

    def _append_game_log(self, line: str):
        self.game_log.append(line)
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.append(line)

    def _open_log_window(self):
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        self.log_window = LogWindow(self, list(self.game_log))

    def open_latest_log(self):
        candidates = []
        if self.last_log_path and self.last_log_path.is_file():
            candidates.append(self.last_log_path)
        try:
            candidates.extend(sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True))
        except OSError:
            pass
        if not candidates:
            messagebox.showinfo("提示", "还没有游戏日志。", parent=self)
            return
        try:
            os.startfile(str(candidates[0]))
        except Exception as e:
            messagebox.showerror("打开失败", str(e), parent=self)

    # -------------------------------------------------------------- downloads
    def _ensure_manifest(self):
        if self.manifest_versions or self.manifest_loading:
            return
        self.manifest_loading = True
        self._set_status("加载版本列表…")
        repo = self.repo
        self._async(lambda: repo.manifest().get("versions") or [],
                    self._on_initial_manifest, "加载版本列表失败")

    def _on_initial_manifest(self, versions):
        self._on_manifest(versions)
        self.after(1500, self._refresh_manifest_bg)

    def _refresh_manifest_bg(self):
        if self.manifest_loading or self.manifest_refreshing:
            return
        self.manifest_refreshing = True
        repo = self.repo

        def work():
            try:
                return repo.manifest(force=True).get("versions") or []
            except Exception:
                return None

        def done(versions):
            self.manifest_refreshing = False
            if not versions:
                self._set_status("检查新版本失败，继续使用当前列表")
                return
            old = {str(v.get("id") or "") for v in self.manifest_versions}
            new = [v for v in versions if str(v.get("id") or "") not in old]
            self.manifest_versions = versions
            self._refresh_version_tree()
            if new:
                names = "、".join(str(v.get("id") or "") for v in new[:3])
                more = f" 等 {len(new)} 个" if len(new) > 3 else ""
                self._set_status(f"发现新版本：{names}{more}")

        self._async(work, done, "刷新版本列表失败")

    def _periodic_manifest(self):
        try:
            if not self.busy:
                self._refresh_manifest_bg()
        finally:
            self.after(30 * 60 * 1000, self._periodic_manifest)

    def _reload_manifest(self):
        if self.manifest_loading or self.manifest_refreshing:
            return
        self.manifest_loading = True
        self.manifest_versions = []
        repo = self.repo
        self._set_status("刷新版本列表…")
        self._async(lambda: repo.manifest(force=True).get("versions") or [],
                    self._on_manifest, "刷新版本列表失败")

    def _on_manifest(self, versions):
        self.manifest_loading = False
        self.manifest_versions = versions
        self._refresh_version_tree()
        self._set_status(f"已加载 {len(versions)} 个游戏版本")

    def _refresh_version_tree(self):
        if not hasattr(self, "version_tree"):
            return
        selected = self.dl_selected
        self.version_tree.delete(*self.version_tree.get_children())
        ftype = FTYPES.get(self.version_filter.get(), None)
        query = self.version_search.get().strip().lower()
        shown = 0
        for entry in self.manifest_versions:
            vid = entry.get("id") or ""
            vtype = entry.get("type") or ""
            if ftype:
                if isinstance(ftype, tuple):
                    if vtype not in ftype:
                        continue
                elif vtype != ftype:
                    continue
            if query and query not in vid.lower():
                continue
            self.version_tree.insert("", "end", iid=vid, values=(
                vid, TYPE_NAMES.get(vtype, vtype or "?"),
                (entry.get("releaseTime") or "")[:10],
            ))
            shown += 1
        self.dl_count.configure(text=f"{shown} 个版本")
        if selected in self.version_tree.get_children():
            self.version_tree.selection_set(selected)
            self.version_tree.see(selected)

    def _on_version_select(self, _event=None):
        selection = self.version_tree.selection()
        if not selection:
            return
        vid = selection[0]
        self.dl_selected = vid
        entry = next((v for v in self.manifest_versions if v.get("id") == vid), {})
        date = (entry.get("releaseTime") or "")[:10]
        self.dl_selected_var.set(f"{vid}\n{TYPE_NAMES.get(entry.get('type'), '')}  {date}")
        self.dl_loader_ver.configure(values=[], state="disabled")
        self._on_loader_change()
        self._update_java_hint()

    def _loader(self) -> str:
        return LOADERS.get(self.dl_loader.get(), "vanilla")

    def _on_loader_change(self):
        vid = self.dl_selected
        loader = self._loader()
        if not vid or loader == "vanilla":
            self.dl_loader_ver.configure(values=[], state="disabled")
            return
        cache = self.loader_cache.setdefault(loader, {})
        if vid in cache:
            values = ["自动（最新）"] + cache[vid]
            self.dl_loader_ver.configure(values=values, state="readonly")
            self.dl_loader_ver.current(0)
            return
        self.dl_loader_ver.configure(values=["加载中…"], state="disabled")
        if loader == "fabric":
            work = lambda: fabric.list_loaders(vid)
        elif loader == "forge":
            work = lambda: forge.list_versions(vid, limit=40)
        elif loader == "neoforge":
            work = lambda: neoforge.list_versions(vid, limit=40)
        elif loader == "quilt":
            work = lambda: quilt.list_loaders(vid)
        else:
            work = lambda: optifine.list_versions(vid, limit=40)
        self._async(work,
                    lambda res: self._apply_loader_versions(vid, loader, res),
                    f"获取 {LOADER_NAMES.get(loader, loader)} 版本失败")

    def _apply_loader_versions(self, vid, loader, versions):
        if vid != self.dl_selected or self._loader() != loader:
            return
        cache = self.loader_cache.setdefault(loader, {})
        cache[vid] = list(versions or [])
        values = ["自动（最新）"] + cache[vid]
        self.dl_loader_ver.configure(values=values, state="readonly")
        self.dl_loader_ver.current(0)
        self._set_status(
            f"{LOADER_NAMES.get(loader, loader)} 共 {len(cache[vid])} 个可用版本")

    def _install_selected(self):
        vid = self.dl_selected
        if not vid:
            messagebox.showinfo("提示", "请先在左侧选择一个版本。", parent=self)
            return
        loader = self._loader()
        loader_version = self.dl_loader_ver.get()
        if loader_version in ("", "自动（最新）", "加载中…"):
            loader_version = None
        if loader == "forge" and not loader_version:
            messagebox.showinfo("提示", "请先选择 Forge 版本。", parent=self)
            return
        with_assets = bool(self.assets_var.get())
        self.cfg.set("download_assets", with_assets, save=False)
        self.cfg.set("auto_instance", bool(self.auto_instance_var.get()), save=False)
        self.cfg.save()
        repo = self.repo

        def work():
            self._emit_log(f"=== 开始安装 {vid}（{LOADER_NAMES.get(loader, loader)}）===")
            if loader == "vanilla":
                return repo.install(vid, with_assets=with_assets)
            if loader == "fabric":
                return fabric.install(repo, vid, loader_version,
                                      with_assets=with_assets, log=self._emit_log)
            if loader == "forge":
                return forge.install(
                    repo, vid, loader_version, with_assets=with_assets,
                    java_path=self.cfg.get("java_path", ""), log=self._emit_log)
            if loader == "neoforge":
                return neoforge.install(
                    repo, vid, loader_version, with_assets=with_assets,
                    java_path=self.cfg.get("java_path", ""), log=self._emit_log)
            if loader == "quilt":
                return quilt.install(repo, vid, loader_version,
                                     with_assets=with_assets, log=self._emit_log)
            return optifine.install(
                repo, vid, loader_version, with_assets=with_assets,
                java_path=self.cfg.get("java_path", ""), log=self._emit_log)

        self._async(work, self._on_install_done, "安装失败", locked=True)

    def _on_install_done(self, version_id):
        self._finish_locked()
        self._set_status(f"{version_id} 安装完成")
        self._dl_log(f"[完成] {version_id}")
        if self.auto_instance_var.get():
            name = version_id
            folder = unique_dir(self.repo.root / "instances", name)
            self.create_instance(name, version_id, folder, 0, "")
            self.nb.select(0)
        messagebox.showinfo("安装完成", f"版本 {version_id} 已就绪。", parent=self)

    def _download_java(self):
        index = self.java_combo.current()
        if index < 0:
            return
        component = java.COMPONENTS[index][0]

        def work():
            return java.install_runtime(component, progress_cb=self._emit_progress,
                                        log=self._emit_log, cancel=self.cancel_event)

        self._async(work, self._on_java_done, "Java 下载失败", locked=True)
        self._set_status(f"准备下载 {component}…")

    def _on_java_done(self, path):
        java.scan_java(root=self.repo.root, force=True)
        self._scan_java()
        self._set_status(f"Java 已安装：{path}")
        messagebox.showinfo("Java 已安装", f"已下载到：\n{path}\n\n启动时会自动使用。", parent=self)

    def _update_java_hint(self):
        installed = java.installed_components()
        if installed:
            text = "已安装运行时：\n" + "\n".join(str(p.parent.parent.name) for p in installed)
        else:
            text = "未在本启动器内安装 Java。会自动使用系统已有的 Java。"
        self.java_hint.configure(text=text)

    # ------------------------------------------------------------------ mods
    def _refresh_mods_tab(self):
        instances = self._visible_instances()
        self.mod_inst_ids = [i["id"] for i in instances]
        modded_ids = [
            i["id"] for i in instances
            if (i.get("loader") or "vanilla") in modlib.LOADERS]
        values = [f"{i.get('name')}   [{i.get('version')}]" for i in instances]
        if values:
            self.mod_inst_combo.configure(values=values, state="readonly")
            index = 0
            if self.mods_instance_id in self.mod_inst_ids:
                index = self.mod_inst_ids.index(self.mods_instance_id)
            if modded_ids and self.mods_instance_id not in modded_ids:
                index = self.mod_inst_ids.index(modded_ids[0])
            self.mod_inst_combo.current(index)
        else:
            self.mod_inst_combo.configure(values=[], state="disabled")
            self.mod_inst_combo.set("")
        self._on_mod_instance()
        self._auto_recommend_mods()

    def _mods_instance(self) -> dict | None:
        if not self.mods_instance_id:
            return None
        return self.cfg.instance(self.mods_instance_id)

    def _on_mod_instance(self):
        index = self.mod_inst_combo.current()
        new_id = (self.mod_inst_ids[index]
                  if 0 <= index < len(self.mod_inst_ids) else None)
        changed = new_id != self.mods_instance_id
        self.mods_instance_id = new_id
        if changed:
            self.recommend_seq += 1
            self.modrinth_hits = []
            self.mr_tree.delete(*self.mr_tree.get_children())
        inst = self._mods_instance()
        if inst:
            loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
            mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
            if loader not in modlib.LOADERS:
                self.mr_desc.configure(
                    text="当前实例是原版，无法安装或推荐模组。\n"
                         "请选择 Fabric / Forge / NeoForge / Quilt 实例。")
            else:
                self.mr_desc.configure(
                    text=f"搜索范围：MC {mc} · {LOADER_NAMES.get(loader, loader)}\n"
                         "输入关键词搜索模组；会自动匹配当前实例的游戏版本与加载器。")
        else:
            self.mr_desc.configure(text="还没有实例。请先在「启动」页新建实例。")
        self._refresh_mods()

    def _refresh_mods(self):
        self.mod_tree.delete(*self.mod_tree.get_children())
        self.mods_by_path = {}
        self.mod_hint.configure(text="", fg=MUTED)
        inst = self._mods_instance()
        if not inst:
            self.mod_count.configure(text="0 个")
            return
        mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
        loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
        mods = modlib.list_mods(inst.get("game_dir", self.repo.root), mc, loader)
        self.mods_by_path = {str(m["path"]): m for m in mods}
        for mod in mods:
            tags = ("incompatible",) if mod.get("compat") is False else ()
            self.mod_tree.insert("", "end", iid=str(mod["path"]), values=(
                mod["name"],
                "启用" if mod["enabled"] else "禁用",
                mod.get("compat_text") or "未知",
                mod["size_text"],
            ), tags=tags)
        enabled = sum(1 for m in mods if m["enabled"])
        bad = sum(1 for m in mods if m.get("compat") is False)
        extra = f" · 不兼容 {bad}" if bad else ""
        self.mod_count.configure(text=f"{len(mods)} 个（启用 {enabled}）{extra}")
        self.mod_hint.configure(text="", fg=MUTED)

    def _on_mod_select(self, _event=None):
        selection = self.mod_tree.selection()
        if not selection:
            self.mod_hint.configure(text="", fg=MUTED)
            return
        mod = getattr(self, "mods_by_path", {}).get(selection[0])
        if not mod:
            return
        compat = mod.get("compat")
        reason = mod.get("compat_reason") or ""
        if compat is True:
            self.mod_hint.configure(text="兼容当前实例", fg=ACCENT)
        elif compat is False:
            self.mod_hint.configure(
                text="不兼容" + (f"：{reason}" if reason else ""), fg=ERR)
        else:
            self.mod_hint.configure(
                text="兼容性未知" + (f"：{reason}" if reason else ""), fg=WARN)

    def _selected_mod(self) -> Path | None:
        selection = self.mod_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个模组。", parent=self)
            return None
        return Path(selection[0])

    def _toggle_mod(self):
        path = self._selected_mod()
        if not path:
            return
        try:
            modlib.toggle_mod(path)
        except LauncherError as e:
            messagebox.showerror("操作失败", str(e), parent=self)
            return
        self._refresh_mods()
        self._set_status("模组状态已切换")

    def _delete_mod(self):
        path = self._selected_mod()
        if not path:
            return
        if not messagebox.askyesno("删除模组", f"确定删除 {path.name} 吗？", parent=self):
            return
        try:
            modlib.delete_mod(path)
        except OSError as e:
            messagebox.showerror("删除失败", str(e), parent=self)
            return
        self._refresh_mods()
        self._set_status("模组已删除")

    def _add_mods(self):
        inst = self._mods_instance()
        if not inst:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        files = filedialog.askopenfilenames(
            parent=self, title="选择模组 jar",
            filetypes=[("Minecraft 模组", "*.jar"), ("所有文件", "*.*")])
        added = 0
        for src in files:
            try:
                modlib.add_mod_file(src, inst.get("game_dir", self.repo.root))
                added += 1
            except LauncherError as e:
                if "同名" in str(e) and messagebox.askyesno(
                        "文件已存在", f"{Path(src).name} 已存在，是否覆盖？", parent=self):
                    try:
                        modlib.add_mod_file(src, inst.get("game_dir", self.repo.root), overwrite=True)
                        added += 1
                    except Exception as err:
                        messagebox.showerror("添加失败", str(err), parent=self)
        self._refresh_mods()
        self._set_status(f"已添加 {added} 个模组")

    def _recommend_mods(self):
        self.mr_query.set("")
        self.modrinth_hits = []
        self.recommend_loading = False
        self._auto_recommend_mods()

    def _auto_recommend_mods(self):
        if self.modrinth_hits or self.recommend_loading:
            return
        inst = self._mods_instance()
        if not inst:
            return
        loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
        if loader not in modlib.LOADERS:
            return
        mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
        self.recommend_loading = True
        self.recommend_seq += 1
        seq = self.recommend_seq
        self._set_status("加载热门推荐模组…")

        def work():
            try:
                return modlib.search_modrinth("", mc, loader, limit=25,
                                               index="downloads")
            except Exception:
                return []

        def done(hits):
            if seq != self.recommend_seq:
                return
            self.recommend_loading = False
            if self.mr_query.get().strip():
                return
            self._on_search_done(hits)
            if hits:
                self._set_status("热门推荐模组（按下载量）")
            else:
                self._set_status("没有加载到推荐模组，请检查网络或实例版本")

        self._async(work, done, "加载推荐模组失败")

    def _search_modrinth(self):
        self.recommend_seq += 1
        self.recommend_loading = False
        inst = self._mods_instance()
        if not inst:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
        if loader not in modlib.LOADERS:
            messagebox.showinfo("提示", "原版实例无法安装模组。请安装 Fabric/Forge 后创建实例。",
                                parent=self)
            return
        mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
        query = self.mr_query.get().strip().lower()
        seq = self.recommend_seq
        self._set_status("搜索 Modrinth…")

        def work():
            return modlib.search_modrinth(query, mc, loader, limit=25)

        def done(hits):
            if seq == self.recommend_seq:
                self._on_search_done(hits)

        self._async(work, done, "搜索失败")

    def _on_search_done(self, hits):
        self.modrinth_hits = hits or []
        self.mr_tree.delete(*self.mr_tree.get_children())
        for index, hit in enumerate(self.modrinth_hits):
            downloads = int(hit.get("downloads") or 0)
            count_text = f"{downloads:,}" if downloads > 0 else "—"
            self.mr_tree.insert("", "end", iid=str(index), values=(
                hit.get("title") or hit.get("slug") or "?", count_text))
        self._set_status(f"Modrinth 找到 {len(self.modrinth_hits)} 个结果")
        if self.modrinth_hits:
            first = self.mr_tree.get_children()
            if first:
                self.mr_tree.selection_set(first[0])
                self._on_mr_select()

    def _on_mr_select(self, _event=None):
        selection = self.mr_tree.selection()
        if not selection:
            return
        try:
            hit = self.modrinth_hits[int(selection[0])]
        except (ValueError, IndexError):
            return
        desc = (hit.get("description") or "").strip()
        author = hit.get("author") or ""
        downloads = int(hit.get("downloads") or 0)
        count_text = f"{downloads:,}" if downloads > 0 else "—"
        self.mr_desc.configure(
            text=f"{hit.get('title', '')}\n{desc}\n作者 {author} · 下载 {count_text}")

    def _install_modrinth(self):
        inst = self._mods_instance()
        if not inst:
            return
        selection = self.mr_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个搜索结果。", parent=self)
            return
        try:
            hit = self.modrinth_hits[int(selection[0])]
        except (ValueError, IndexError):
            return
        project_id = hit.get("project_id")
        if not project_id:
            return
        loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
        mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
        game_dir = inst.get("game_dir", self.repo.root)

        def work():
            return modlib.install_project(project_id, mc, loader, game_dir,
                                          progress_cb=self._emit_progress,
                                          cancel=self.cancel_event)

        self._async(work, self._on_mod_installed, "安装模组失败", locked=True)

    def _install_mod_link(self):
        inst = self._mods_instance()
        if not inst:
            messagebox.showinfo("提示", "请先选择一个实例。", parent=self)
            return
        loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
        if loader not in modlib.LOADERS:
            messagebox.showinfo("提示", "原版实例无法安装模组。", parent=self)
            return
        mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
        game_dir = inst.get("game_dir", self.repo.root)
        url = simpledialog.askstring(
            "从链接安装模组",
            "粘贴 Modrinth 项目/版本链接，或任意 .jar 直链：",
            parent=self)
        if not url:
            return
        self._set_status("正在解析模组链接…")

        def work():
            return modlib.install_from_url(url, mc, loader, game_dir,
                                           progress_cb=self._emit_progress,
                                           cancel=self.cancel_event)

        self._async(work, self._on_mod_installed, "安装模组失败", locked=True)

    def _on_mod_installed(self, result):
        dependencies = result.get("dependencies") or []
        inst = self._mods_instance()
        if dependencies and inst:
            mc = inst.get("mc_version") or self.mc_version_of(inst.get("version", ""))
            loader = inst.get("loader") or self.detect_loader(inst.get("version", ""))
            game_dir = inst.get("game_dir", self.repo.root)
            self._set_status("正在检查前置模组…")

            def work():
                return modlib.describe_dependencies(dependencies, game_dir, mc, loader)

            def done(missing):
                self._on_dependencies_checked(result, inst, missing, mc, loader, game_dir)

            self._async(work, done, "检查前置模组失败")
            return
        self._show_mod_installed(result)

    def _on_dependencies_checked(self, result, inst, missing, mc, loader, game_dir):
        if not missing:
            self._show_mod_installed(result)
            return
        names = "\n".join(f"· {item.get('name') or item.get('project_id')}"
                          for item in missing)
        if not messagebox.askyesno(
                "需要前置模组",
                f"已安装 {result.get('filename')}。\n\n"
                f"该模组还需要以下前置：\n{names}\n\n是否自动安装？",
                parent=self):
            self._show_mod_installed(
                result, f"\n\n仍缺少前置：\n{names}")
            return

        def work():
            return modlib.install_dependencies(
                missing, game_dir, mc, loader,
                progress_cb=self._emit_progress, cancel=self.cancel_event)

        def done(installed):
            self._refresh_mods()
            count = installed.get("count", 0)
            self._set_status(f"已安装 {count} 个前置模组")
            messagebox.showinfo(
                "前置安装完成",
                f"已安装 {count} 个前置模组。\n"
                "如仍有嵌套前置，启动器会继续提示。",
                parent=self)

        self._async(work, done, "安装前置模组失败", locked=True)

    def _show_mod_installed(self, result, extra: str = ""):
        self._refresh_mods()
        self._set_status(f"已安装模组：{result.get('filename')}")
        version = result.get("version")
        detail = (f"{result.get('filename')}\n版本 {version}"
                  if version else result.get("filename"))
        messagebox.showinfo("模组已安装", detail + (extra or ""), parent=self)

    # -------------------------------------------------------------- settings
    def _load_settings_ui(self):
        self.s_game_root.set(str(self.cfg.game_root()))
        self.s_java.set(self.cfg.get("java_path", ""))
        self.s_memory.set(str(self.cfg.get("memory_mb", 4096)))
        self.s_workers.set(str(self.cfg.get("workers", 16)))
        source_label = next((k for k, v in SOURCES.items() if v == self.cfg.get("source")), "官方源")
        self.s_source.set(source_label)
        self.s_client.set(self.cfg.get("ms_client_id", DEFAULT_CLIENT_ID))
        self.s_mod_api.set(self.cfg.get("mod_api")
                           or "https://api.modrinth.com/v2")
        self.s_open_log.set(bool(self.cfg.get("open_log", True)))
        self.s_newer_java.set(bool(self.cfg.get("allow_newer_java", True)))
        visible = self._visible_instances()
        if self.mods_instance_id not in [i["id"] for i in visible]:
            self.mods_instance_id = visible[0]["id"] if visible else None
        self._refresh_account_label()

    def _save_settings(self):
        root_text = self.s_game_root.get().strip()
        if not root_text:
            messagebox.showerror("设置错误", "游戏目录不能为空。", parent=self)
            return
        game_root = Path(root_text)
        try:
            ensure_dir(game_root)
        except OSError as e:
            messagebox.showerror("设置错误", f"无法使用该目录：\n{e}", parent=self)
            return
        try:
            memory = int(self.s_memory.get())
            workers = int(self.s_workers.get())
        except ValueError:
            messagebox.showerror("设置错误", "内存和线程数需要是整数。", parent=self)
            return
        if not (512 <= memory <= 65536):
            messagebox.showerror("设置错误", "内存需要在 512 - 65536 MB 之间。", parent=self)
            return
        if not (1 <= workers <= 64):
            messagebox.showerror("设置错误", "下载线程需要在 1 - 64 之间。", parent=self)
            return
        self.cfg.data.update({
            "game_root": str(game_root),
            "java_path": self.s_java.get().strip(),
            "memory_mb": memory,
            "workers": workers,
            "source": SOURCES.get(self.s_source.get(), "official"),
            "ms_client_id": self.s_client.get().strip() or DEFAULT_CLIENT_ID,
            "mod_api": (self.s_mod_api.get().strip()
                        or "https://api.modrinth.com/v2"),
            "open_log": bool(self.s_open_log.get()),
            "allow_newer_java": bool(self.s_newer_java.get()),
        })
        self.cfg.save()
        modlib.configure_api(self.cfg.get("mod_api"))
        self.modrinth_hits = []
        self.recommend_loading = False
        self.repo = self._make_repo()
        self.manifest_versions = []
        self.manifest_loading = False
        self._refresh_version_tree()
        self._refresh_instances(select_id=self.selected_instance_id)
        self._import_local_versions(silent=True)
        self.s_saved.configure(text="已保存 · " + time.strftime("%H:%M:%S"))
        self._set_status("设置已保存")
        self._ensure_manifest()

    def _browse_game_root(self):
        path = filedialog.askdirectory(parent=self, initialdir=self.s_game_root.get() or str(self.repo.root))
        if path:
            self.s_game_root.set(path)

    def _browse_java(self):
        path = filedialog.askopenfilename(parent=self, title="选择 java.exe",
                                          filetypes=[("java.exe", "java.exe"), ("所有文件", "*.*")])
        if path:
            self.s_java.set(path)

    def _scan_java(self):
        def work():
            return java.scan_java(root=self.repo.root, force=True)

        self._async(work, self._on_java_scan, "扫描 Java 失败")

    def _on_java_scan(self, infos):
        self.java_infos = infos or []
        self.java_list.delete(0, "end")
        for info in self.java_infos:
            self.java_list.insert("end", info.label())
        if not self.java_infos:
            self.java_list.insert("end", "没有找到 Java，可到下载页安装运行时")
        elif not self.busy:
            self._set_status(f"找到 {len(self.java_infos)} 个 Java")

    def _pick_java(self, _event=None):
        selection = self.java_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index < len(self.java_infos):
            self.s_java.set(str(self.java_infos[index].path))
            self.s_saved.configure(text="已选择，点击保存设置生效")

    # ----------------------------------------------------------------- close
    def destroy(self):
        try:
            if hasattr(self, "multiplayer_tab"):
                self.multiplayer_tab.shutdown()
        except Exception:
            pass
        super().destroy()

    def _on_close(self):
        if self.busy:
            if not messagebox.askyesno("退出", "还有任务正在进行，确定退出吗？", parent=self):
                return
            self.cancel_event.set()
        if self.game_proc and self.game_proc.poll() is None:
            if not messagebox.askyesno("退出", "游戏仍在运行。关闭启动器不会关闭游戏。\n确定退出吗？",
                                       parent=self):
                return
        self.destroy()
