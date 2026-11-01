"""联机页：通过 Terracotta（陶瓦联机）的房间码联机。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from core import terracotta
from core.util import Cancelled
from ui.theme import (
    ACCENT,
    ACCENT_DARK,
    BG,
    BORDER,
    ERR,
    FG,
    FONT,
    FONT_BOLD,
    FONT_SMALL,
    MUTED,
    PANEL,
    PANEL2,
    SELECT_FG,
    WARN,
)
from ui.widgets import flat_button

EXCEPTIONS = {
    0: "无法连接房主，房间可能已关闭或网络不稳定。",
    1: "与房主的连接中断。",
    2: "加入方网络组件崩溃。",
    3: "房主网络组件崩溃。",
    4: "房主的 Minecraft 服务器已失联（可能退出了存档）。",
    5: "联机协议错误，请重试。",
}
DIFFICULTY = {
    "EASIEST": "很简单",
    "SIMPLE": "简单",
    "MEDIUM": "中等",
    "TOUGH": "较难",
    "UNKNOWN": "未知",
}
ROLE = {"HOST": "房主", "GUEST": "加入者", "LOCAL": "本机"}


class MultiplayerTab(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.port = 0
        self.owned = False
        self.busy = False
        self.gen = 0
        self.last_index = -1
        self.messages: queue.Queue = queue.Queue()
        self.poll_stop: threading.Event | None = None
        self.cancel_event = threading.Event()
        self.name_var = tk.StringVar()
        self.room_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.state_var = tk.StringVar(value="未启动")
        self.detail_var = tk.StringVar(
            value="创建或加入房间时会自动启动陶瓦联机。")
        self.engine_var = tk.StringVar()
        self.progress_var = tk.StringVar()
        self.join_var = tk.StringVar()
        self._build()
        self._load_name()
        self._update_engine()
        self.after(150, self._drain)

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=16, pady=14)

        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text="联机", bg=BG, fg=FG,
                 font=("Courier New", 18, "bold")).pack(side="left")
        tk.Label(head, text="陶瓦联机 · 房间码联机，无需公网 IP",
                 bg=BG, fg=MUTED, font=FONT_SMALL).pack(
            side="left", padx=(12, 0))
        credit = tk.Label(
            head, text="由 Terracotta 提供 · AGPL-3.0",
            bg=BG, fg=ACCENT, font=FONT_SMALL, cursor="hand2")
        credit.pack(side="right")
        credit.bind("<Button-1>",
                    lambda e: webbrowser.open(terracotta.PROJECT_URL))

        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=380, weight=0)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = tk.Frame(body, bg=PANEL, highlightthickness=1,
                         highlightbackground=BORDER)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_status(left)
        self._build_actions(right)

    def _build_status(self, parent):
        box = tk.Frame(parent, bg=PANEL)
        box.pack(fill="both", expand=True, padx=18, pady=16)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(6, weight=1)

        tk.Label(box, text="当前状态", bg=PANEL, fg=MUTED,
                 font=FONT_SMALL).grid(row=0, column=0, sticky="w")
        self.state_label = tk.Label(
            box, textvariable=self.state_var, bg=PANEL, fg=ACCENT,
            font=("Courier New", 18, "bold"), anchor="w")
        self.state_label.grid(row=1, column=0, sticky="ew", pady=(2, 3))
        tk.Label(box, textvariable=self.detail_var, bg=PANEL, fg=MUTED,
                 font=FONT_SMALL, justify="left", anchor="w",
                 wraplength=620).grid(row=2, column=0, sticky="ew")

        code_row = tk.Frame(box, bg=PANEL)
        code_row.grid(row=3, column=0, sticky="ew", pady=(14, 3))
        tk.Label(code_row, text="房间码", bg=PANEL, fg=MUTED,
                 font=FONT_SMALL, width=7, anchor="w").pack(side="left")
        self.code_label = tk.Label(
            code_row, textvariable=self.room_var, bg=PANEL, fg=FG,
            font=("Courier New", 15, "bold"), anchor="w")
        self.code_label.pack(side="left", padx=(4, 10))
        self.copy_code_btn = flat_button(
            code_row, "复制", lambda: self._copy(self.room_var.get(), "房间码"),
            padx=8, pady=2)
        self.copy_code_btn.pack(side="right")

        url_row = tk.Frame(box, bg=PANEL)
        url_row.grid(row=4, column=0, sticky="ew", pady=(2, 3))
        tk.Label(url_row, text="直连地址", bg=PANEL, fg=MUTED,
                 font=FONT_SMALL, width=7, anchor="w").pack(side="left")
        tk.Label(url_row, textvariable=self.url_var, bg=PANEL, fg=FG,
                 font=FONT_SMALL, anchor="w").pack(
            side="left", padx=(4, 10))
        flat_button(
            url_row, "复制", lambda: self._copy(self.url_var.get(), "直连地址"),
            padx=8, pady=2).pack(side="right")

        tk.Label(box, text="房间成员", bg=PANEL, fg=MUTED,
                 font=FONT_SMALL).grid(row=5, column=0, sticky="w",
                                       pady=(12, 2))
        self.members = tk.Listbox(
            box, height=5, bg=PANEL2, fg=FG, bd=0, highlightthickness=0,
            font=FONT_SMALL, selectbackground=ACCENT_DARK,
            selectforeground=SELECT_FG, activestyle="none")
        self.members.grid(row=6, column=0, sticky="nsew")

        actions = tk.Frame(box, bg=PANEL)
        actions.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        self.reset_btn = flat_button(
            actions, "返回待机", self._reset, padx=10, pady=4)
        self.reset_btn.pack(side="left")
        self.stop_btn = flat_button(
            actions, "停止联机", self._stop, danger=True, padx=10, pady=4)
        self.stop_btn.pack(side="left", padx=8)

    def _build_actions(self, parent):
        box = tk.Frame(parent, bg=PANEL)
        box.pack(fill="both", expand=True, padx=18, pady=16)

        tk.Label(box, text="操作", bg=PANEL, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w")
        tk.Label(box, text="玩家名称", bg=PANEL, fg=FG,
                 font=FONT_SMALL).pack(anchor="w", pady=(12, 2))
        tk.Entry(box, textvariable=self.name_var, bg=PANEL2, fg=FG, bd=0,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT, insertbackground=FG,
                 font=FONT).pack(fill="x", ipady=5)

        self.create_btn = flat_button(
            box, "创建房间", self._create, accent=True, pady=8)
        self.create_btn.pack(fill="x", pady=(10, 4))
        tk.Label(
            box,
            text="先在游戏里进入世界，按 Esc → 对局域网开放，再点创建。",
            bg=PANEL, fg=MUTED, font=FONT_SMALL, justify="left",
            wraplength=330).pack(anchor="w")

        tk.Label(box, text="加入房间", bg=PANEL, fg=FG,
                 font=FONT_SMALL).pack(anchor="w", pady=(16, 2))
        join_row = tk.Frame(box, bg=PANEL)
        join_row.pack(fill="x")
        tk.Entry(join_row, textvariable=self.join_var, bg=PANEL2, fg=FG,
                 bd=0, highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT, insertbackground=FG,
                 font=FONT).pack(side="left", fill="x", expand=True, ipady=5)
        self.join_btn = flat_button(
            join_row, "加入", self._join, padx=14, pady=4)
        self.join_btn.pack(side="left", padx=(6, 0))
        tk.Label(box, text="房间码示例：U/XXXX-XXXX-XXXX-XXXX",
                 bg=PANEL, fg=MUTED, font=FONT_SMALL).pack(
            anchor="w", pady=(4, 0))

        self.progress_frame = tk.Frame(box, bg=PANEL)
        self.progress_bar = ttk.Progressbar(
            self.progress_frame, mode="determinate", maximum=100)
        self.progress_bar.pack(fill="x")
        self.progress_label = tk.Label(
            self.progress_frame, textvariable=self.progress_var, bg=PANEL,
            fg=MUTED, font=FONT_SMALL, anchor="w")
        self.progress_label.pack(fill="x", pady=(3, 0))

        self.engine_label = tk.Label(
            box, textvariable=self.engine_var, bg=PANEL, fg=MUTED,
            font=FONT_SMALL, justify="left", wraplength=330)
        self.engine_label.pack(anchor="w", pady=(14, 0))

        tk.Label(
            box,
            text="加入成功后，在游戏多人列表双击「陶瓦联机大厅」进入；"
                 "也可以使用直连地址。",
            bg=PANEL, fg=MUTED, font=FONT_SMALL, justify="left",
            wraplength=330).pack(anchor="w", pady=(12, 0))

    # ------------------------------------------------------------------ 名称
    def _load_name(self):
        try:
            name = str(self.app.cfg.get("terracotta_name") or "")
        except Exception:
            name = ""
        if not name:
            account = getattr(self.app, "account", None)
            name = getattr(account, "name", "") or ""
        self.name_var.set(name)

    def refresh_name(self):
        if not self.name_var.get().strip():
            self._load_name()

    def _player_name(self) -> str:
        return self.name_var.get().strip()[:32]

    def _save_name(self, name: str):
        try:
            self.app.cfg.set("terracotta_name", name)
        except Exception:
            pass

    # ------------------------------------------------------------------ 状态
    def _update_engine(self):
        if self.port:
            text = f"陶瓦联机已连接 · 本地端口 {self.port}"
        elif terracotta.is_installed():
            text = f"陶瓦联机已就绪（v{terracotta.VERSION}）"
        else:
            text = "首次联机将自动下载陶瓦联机（约 8 MB）"
        self.engine_var.set(text)

    def _set_state(self, title: str, detail: str, color: str = ACCENT):
        self.state_var.set(title)
        self.detail_var.set(detail)
        self.state_label.configure(fg=color)

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.create_btn, self.join_btn):
            try:
                button.configure(state=state)
            except Exception:
                pass

    def _render_members(self, profiles):
        self.members.delete(0, "end")
        for profile in profiles or []:
            name = str(profile.get("name") or "匿名玩家")
            kind = ROLE.get(str(profile.get("kind") or ""), "")
            self.members.insert("end", f"{name}  [{kind}]" if kind else name)

    def _render_state(self, st: dict):
        index = int(st.get("index") or 0)
        if index and index <= self.last_index:
            return
        if index:
            self.last_index = index
        state = str(st.get("state") or "waiting")
        room = str(st.get("room") or "")
        url = str(st.get("url") or "")
        if room:
            self.room_var.set(room)
        if url:
            self.url_var.set(url)
        profiles = st.get("profiles") or []
        if profiles:
            self._render_members(profiles)

        if state == "waiting":
            self._set_state("待机", "创建房间，或输入房间码加入。", MUTED)
            self.room_var.set("")
            self.url_var.set("")
            self._render_members([])
            self._set_busy(False)
        elif state == "host-scanning":
            self._set_state(
                "正在扫描本机 Minecraft 端口",
                "请先进入世界并按 Esc → 对局域网开放；扫描到端口后会自动创建房间。")
            self._set_busy(True)
        elif state == "host-starting":
            self._set_state("正在创建房间…", "正在连接公共节点，稍等片刻。")
            self._set_busy(True)
        elif state == "host-ok":
            self._set_state(
                "房间已创建",
                "把房间码发给朋友。他们加入后会在多人游戏列表看到「陶瓦联机大厅」。")
            self._set_busy(False)
        elif state == "guest-connecting":
            self._set_state("正在连接房主…", "正在寻找房主，请稍等。")
            self._set_busy(True)
        elif state == "guest-starting":
            difficulty = DIFFICULTY.get(
                str(st.get("difficulty") or "UNKNOWN"), "未知")
            self._set_state(
                "正在建立虚拟局域网…",
                f"NAT 连接难度：{difficulty}。")
            self._set_busy(True)
        elif state == "guest-ok":
            self._set_state(
                "已加入房间",
                "在游戏多人列表双击「陶瓦联机大厅」进入；"
                "也可以使用上方直连地址。")
            self._set_busy(False)
        elif state == "exception":
            kind = int(st.get("type") or 0)
            self._set_state(
                "联机异常",
                EXCEPTIONS.get(kind, "未知异常，请返回待机后重试。"),
                ERR)
            self._set_busy(False)
        else:
            self._set_state(state, "状态更新中…", WARN)
            self._set_busy(False)

    # ------------------------------------------------------------------ 操作
    def _create(self):
        if self.busy:
            return
        name = self._player_name()
        if not name:
            messagebox.showinfo("需要名称", "请先填写玩家名称。", parent=self.app)
            return
        self._save_name(name)
        self._ensure_and_run(("host", name))

    def _join(self):
        if self.busy:
            return
        room = self.join_var.get().strip()
        if not room:
            messagebox.showinfo("需要房间码", "请输入房主发来的房间码。",
                                parent=self.app)
            return
        name = self._player_name()
        if not name:
            messagebox.showinfo("需要名称", "请先填写玩家名称。", parent=self.app)
            return
        self._save_name(name)
        self.room_var.set(room)
        self._ensure_and_run(("guest", room, name))

    def _ensure_and_run(self, action):
        if self.port:
            self._do_action(action)
            return
        if not terracotta.is_installed():
            if not messagebox.askyesno(
                    "下载陶瓦联机",
                    "联机功能使用 Terracotta（陶瓦联机）。\n"
                    "首次使用需要下载约 8 MB 的官方程序，"
                    "从 Gitee / GitHub 获取，不修改二进制。\n\n"
                    "是否继续？",
                    parent=self.app):
                return
        self._start_engine(action)

    def _start_engine(self, action):
        self._set_busy(True)
        self._show_progress(True)
        self.progress_var.set("正在准备陶瓦联机…")
        self.cancel_event.clear()
        self.gen += 1
        gen = self.gen

        def work():
            try:
                terracotta.ensure_installed(self._on_progress,
                                            self.cancel_event)
                if gen != self.gen:
                    return
                port, owned = terracotta.start()
                if gen != self.gen:
                    terracotta.stop(port)
                    return
            except Cancelled:
                if gen == self.gen:
                    self.messages.put(("cancelled", gen))
            except Exception as e:
                if gen == self.gen:
                    self.messages.put(("error", str(e), gen))
            else:
                self.messages.put(("ready", port, owned, action, gen))

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done, total, bdone, btotal, label, elapsed):
        self.messages.put(("progress", bdone, btotal))

    def _do_action(self, action):
        self._set_busy(True)
        port = self.port
        gen = self.gen

        def work():
            try:
                if action[0] == "host":
                    terracotta.create_room(port, action[1])
                else:
                    terracotta.join_room(port, action[1], action[2])
            except Exception as e:
                if gen == self.gen:
                    self.messages.put(("error", str(e), gen))

        threading.Thread(target=work, daemon=True).start()

    def _reset(self):
        if not self.port:
            self._render_state({"state": "waiting", "index": 0})
            return
        self._set_busy(True)
        port = self.port
        gen = self.gen

        def work():
            try:
                terracotta.reset(port)
            except Exception as e:
                if gen == self.gen:
                    self.messages.put(("error", str(e), gen))

        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        self.cancel_event.set()
        self.gen += 1
        self._stop_polling()
        port = self.port
        self.port = 0
        self.owned = False
        self.last_index = -1
        self._show_progress(False)
        self._set_busy(False)
        self._render_state({"state": "waiting", "index": 0})
        self._set_state("已停止联机", "创建或加入房间时会重新启动。", MUTED)
        self._update_engine()
        if port:
            # 同步停止，避免用户点完停止立刻退出时遗留进程
            terracotta.stop(port)

    # ------------------------------------------------------------------ 轮询
    def _start_polling(self):
        self._stop_polling()
        stop = threading.Event()
        self.poll_stop = stop
        port = self.port
        gen = self.gen

        def loop():
            failures = 0
            while not stop.is_set():
                try:
                    data = terracotta.state(port)
                except Exception as e:
                    failures += 1
                    if failures >= 5:
                        self.messages.put(("poll_error", str(e), gen))
                        break
                else:
                    failures = 0
                    self.messages.put(("state", data, gen))
                stop.wait(0.6)

        threading.Thread(target=loop, daemon=True).start()

    def _stop_polling(self):
        if self.poll_stop is not None:
            self.poll_stop.set()
            self.poll_stop = None

    def _drain(self):
        try:
            while True:
                message = self.messages.get_nowait()
                self._handle(message)
        except queue.Empty:
            pass
        try:
            if self.winfo_exists():
                self.after(150, self._drain)
        except Exception:
            pass

    def _handle(self, message):
        kind = message[0]
        if kind == "progress":
            _, done, total = message
            if total:
                value = min(100.0, done * 100.0 / total)
                self.progress_bar.configure(value=value)
                self.progress_var.set(
                    f"正在下载陶瓦联机 {done / 1048576:.1f} / "
                    f"{total / 1048576:.1f} MB")
            else:
                self.progress_var.set(
                    f"正在下载陶瓦联机 {done / 1048576:.1f} MB")
        elif kind == "ready":
            _, port, owned, action, gen = message
            if gen != self.gen:
                terracotta.stop(port)
                return
            self.port = port
            self.owned = owned
            self.last_index = -1
            self.room_var.set("")
            self.url_var.set("")
            self._render_members([])
            self._show_progress(False)
            self._update_engine()
            self._start_polling()
            if action:
                self._do_action(action)
            else:
                self._set_busy(False)
        elif kind == "state":
            if len(message) > 2 and message[2] != self.gen:
                return
            self._render_state(message[1])
        elif kind == "error":
            if len(message) > 2 and message[2] != self.gen:
                return
            self._set_busy(False)
            self._show_progress(False)
            self._set_state("操作失败", str(message[1]), ERR)
            messagebox.showerror("联机失败", str(message[1]), parent=self.app)
        elif kind == "cancelled":
            if len(message) > 1 and message[1] != self.gen:
                return
            self._set_busy(False)
            self._show_progress(False)
            self._set_state("已取消", "下载已取消。", MUTED)
        elif kind == "poll_error":
            if len(message) > 2 and message[2] != self.gen:
                return
            self.port = 0
            self.owned = False
            self.last_index = -1
            self._set_busy(False)
            self._set_state(
                "连接已断开",
                "陶瓦联机进程可能已退出，请重新创建或加入房间。", ERR)
            self._update_engine()

    def _show_progress(self, visible: bool):
        if visible:
            if not self.progress_frame.winfo_manager():
                self.progress_frame.pack(fill="x", pady=(12, 0),
                                         before=self.engine_label)
            self.progress_bar.configure(value=0)
        else:
            self.progress_frame.pack_forget()

    def _copy(self, text: str, label: str):
        text = (text or "").strip()
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.app._set_status(f"{label}已复制")
        except Exception:
            pass

    # ------------------------------------------------------------------ 关闭
    def shutdown(self):
        self.cancel_event.set()
        self.gen += 1
        self._stop_polling()
        if self.owned and self.port:
            try:
                terracotta.stop(self.port)
            except Exception:
                pass
        self.port = 0
        self.owned = False

