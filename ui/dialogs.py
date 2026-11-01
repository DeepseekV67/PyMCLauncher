from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import auth, diagnose, java
from core.config import DEFAULT_CLIENT_ID, safe_folder_name, unique_dir
from core.util import Cancelled, LauncherError, ensure_dir
from ui.theme import (
    ACCENT,
    ACCENT_DARK,
    BG,
    BORDER,
    ERR,
    FG,
    FONT,
    FONT_BOLD,
    FONT_HUGE,
    FONT_SMALL,
    INPUT,
    MUTED,
    PANEL,
    PANEL2,
    PANEL3,
    SELECT_FG,
    WARN,
)
from ui.widgets import append_text, center_window, flat_button, text_area


class BaseDialog(tk.Toplevel):
    def __init__(self, parent, title: str, width: int, height: int):
        super().__init__(parent)
        self.app = parent
        self.configure(bg=BG)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        center_window(self, parent, width, height)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(30, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass


def _title(parent, text: str, subtitle: str = ""):
    tk.Label(parent, text=text, bg=BG, fg=FG, font=("Courier New", 15, "bold")).pack(anchor="w")
    if subtitle:
        tk.Label(parent, text=subtitle, bg=BG, fg=MUTED, font=FONT_SMALL,
                 justify="left", wraplength=480).pack(anchor="w", pady=(5, 0))


class AccountDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "账户", 580, 540)
        self.app = app
        self.account_keys: list[str] = []
        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=22, pady=18)
        pad.columnconfigure(0, weight=1)
        pad.rowconfigure(1, weight=1)
        head = tk.Frame(pad, bg=BG)
        head.grid(row=0, column=0, sticky="ew")
        _title(head, "账户",
               "支持保存多个账户。微软正版、LittleSkin 等外置登录和离线账户"
               "都可以加入列表并随时切换。")

        tree_frame = tk.Frame(pad, bg=PANEL)
        tree_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 6))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.account_tree = ttk.Treeview(
            tree_frame, columns=("name", "type", "active"),
            show="headings", selectmode="browse", height=4)
        self.account_tree.heading("name", text="账户", anchor="w")
        self.account_tree.column("name", width=260, anchor="w", stretch=True)
        self.account_tree.heading("type", text="类型", anchor="w")
        self.account_tree.column("type", width=100, anchor="w", stretch=False)
        self.account_tree.heading("active", text="状态", anchor="w")
        self.account_tree.column("active", width=70, anchor="w", stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical",
                               command=self.account_tree.yview)
        self.account_tree.configure(yscrollcommand=scroll.set)
        self.account_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.account_tree.bind("<Double-1>", lambda e: self._switch())
        self.account_tree.bind("<<TreeviewSelect>>", self._on_account_select)

        account_actions = tk.Frame(pad, bg=BG)
        account_actions.grid(row=2, column=0, sticky="ew")
        flat_button(account_actions, "切换账户", self._switch, padx=10,
                    pady=4).pack(side="left")
        flat_button(account_actions, "删除选中", self._delete,
                    danger=True, padx=10, pady=4).pack(side="left", padx=6)

        self.info_var = tk.StringVar()
        info = tk.Label(pad, textvariable=self.info_var, bg=PANEL, fg=FG,
                        font=FONT_SMALL, justify="left", anchor="nw",
                        padx=14, pady=10, wraplength=480, height=5)
        info.grid(row=3, column=0, sticky="ew", pady=(10, 10))

        row = tk.Frame(pad, bg=BG)
        row.grid(row=4, column=0, sticky="ew")
        flat_button(row, "微软正版登录", self._ms_login, accent=True).pack(side="left")
        flat_button(row, "外置登录", self._external).pack(side="left", padx=6)
        flat_button(row, "离线登录", self._offline).pack(side="left")
        flat_button(row, "皮肤", self._skin, padx=8).pack(side="left", padx=(6, 0))

        footer = tk.Frame(pad, bg=BG)
        footer.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.logout_btn = flat_button(footer, "退出当前账户", self._logout,
                                      danger=True)
        self.logout_btn.pack(side="left")
        flat_button(footer, "关闭", self.destroy).pack(side="right")
        self._refresh()

    def _type_text(self, account) -> str:
        if account.is_microsoft:
            return "微软正版"
        if account.is_external:
            return "外置登录"
        return "离线"

    def _refresh_accounts(self):
        self.account_tree.delete(*self.account_tree.get_children())
        self.account_keys = []
        active_key = self.app.cfg.active_account_key()
        selected_iid = None
        for index, account in enumerate(self.app.accounts):
            data = account.to_dict()
            key = self.app.cfg.account_key(data)
            self.account_keys.append(key)
            iid = str(index)
            self.account_tree.insert("", "end", iid=iid, values=(
                account.name, self._type_text(account),
                "当前" if key == active_key else ""))
            if key == active_key:
                selected_iid = iid
        if selected_iid:
            self.account_tree.selection_set(selected_iid)
            self.account_tree.see(selected_iid)
        self.logout_btn.configure(
            state="normal" if self.app.accounts else "disabled")
        self._refresh_info()

    def _refresh_info(self):
        account = self.app.account
        if not account:
            self.info_var.set(
                "当前未登录。可以在下方添加微软正版、外置或离线账户；"
                "已保存的账户会显示在上面的列表中。")
            return
        if account.is_microsoft:
            self.info_var.set(
                f"用户名 {account.name} · 微软正版 · UUID {account.uuid}\n"
                f"XUID {account.xuid or '-'} · "
                f"令牌 {'有效' if account.access_token else '无'}（过期自动刷新）")
        elif account.is_external:
            self.info_var.set(
                f"用户名 {account.name} · 外置登录 · UUID {account.uuid}\n"
                f"服务器 {account.server}\n"
                f"令牌 {'有效' if account.access_token else '无'}（启动时自动验证）")
        else:
            self.info_var.set(
                f"用户名 {account.name} · 离线 · UUID {account.uuid}\n"
                "离线账户只能进入离线服务器，无法加入正版验证服务器。")

    def _refresh(self):
        self._refresh_accounts()

    def _on_account_select(self, _event=None):
        return

    def _selected_account_key(self) -> str:
        selection = self.account_tree.selection()
        if not selection:
            return ""
        try:
            return self.account_keys[int(selection[0])]
        except (ValueError, IndexError):
            return ""

    def _switch(self):
        key = self._selected_account_key()
        if not key:
            messagebox.showinfo("提示", "请先选择一个账户。", parent=self)
            return
        if self.app.switch_account(key):
            self._refresh()
        else:
            messagebox.showerror("切换失败", "账户信息已失效，请重新登录。",
                                 parent=self)

    def _delete(self):
        key = self._selected_account_key()
        if not key:
            messagebox.showinfo("提示", "请先选择一个账户。", parent=self)
            return
        if not messagebox.askyesno("删除账户",
                                   "确定从启动器删除这个账户吗？\n"
                                   "不会影响微软/皮肤站账号本身。",
                                   parent=self):
            return
        self.app.remove_account(key)
        self._refresh()

    def _ms_login(self):
        self.destroy()
        DeviceCodeDialog(self.app)

    def _external(self):
        self.destroy()
        ExternalLoginDialog(self.app)

    def _skin(self):
        self.destroy()
        self.app.open_skin_viewer()

    def _offline(self):
        self.destroy()
        OfflineDialog(self.app)

    def _logout(self):
        key = self.app.cfg.active_account_key()
        if not key:
            return
        if not messagebox.askyesno("退出登录",
                                   "确定退出并删除当前账户吗？",
                                   parent=self):
            return
        self.app.remove_account(key)
        self._refresh()


class OfflineDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "离线登录", 430, 240)
        self.app = app
        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=22, pady=18)
        _title(pad, "离线登录", "用户名会作为游戏内 ID（3-16 位字母、数字、下划线）。")
        default = ""
        if app.account and app.account.type == "offline":
            default = app.account.name
        self.name_var = tk.StringVar(value=default)
        entry = tk.Entry(pad, textvariable=self.name_var, bg=PANEL2, fg=FG, insertbackground=ACCENT,
                         relief="flat", font=FONT, bd=0, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT)
        entry.pack(fill="x", pady=(16, 6), ipady=8)
        entry.focus_set()
        self.error_var = tk.StringVar()
        tk.Label(pad, textvariable=self.error_var, bg=BG, fg=ERR, font=FONT_SMALL).pack(anchor="w")
        row = tk.Frame(pad, bg=BG)
        row.pack(side="bottom", fill="x")
        flat_button(row, "进入游戏", self._submit, accent=True).pack(side="left")
        flat_button(row, "取消", self.destroy).pack(side="right")
        entry.bind("<Return>", lambda e: self._submit())

    def _submit(self):
        try:
            account = auth.offline_account(self.name_var.get())
        except LauncherError as e:
            self.error_var.set(str(e))
            return
        self.app.set_account(account)
        self.destroy()


class ExternalLoginDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "外置登录", 540, 440)
        self.app = app
        self.msg_queue: queue.Queue = queue.Queue()
        self.busy = False
        self.finished = False

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=22, pady=18)
        _title(pad, "外置登录",
               "使用 LittleSkin 或其他兼容 Yggdrasil 的皮肤站账号登录。\n"
               "启动时会通过 authlib-injector 注入登录服务器。")

        existing = app.account if app.account and app.account.is_external else None
        self.preset_var = tk.StringVar(value="自定义" if existing else "LittleSkin")
        self.server_var = tk.StringVar(
            value=existing.server if existing else "https://littleskin.cn/api/yggdrasil")
        self.user_var = tk.StringVar(value=existing.name if existing else "")
        self.pass_var = tk.StringVar()

        preset_row = tk.Frame(pad, bg=BG)
        preset_row.pack(fill="x", pady=(14, 4))
        tk.Label(preset_row, text="预设", bg=BG, fg=MUTED, font=FONT_SMALL,
                 width=10, anchor="w").pack(side="left")
        self.preset = ttk.Combobox(preset_row, textvariable=self.preset_var,
                                   values=("LittleSkin", "自定义"),
                                   state="readonly", width=18, font=FONT)
        self.preset.pack(side="left")
        self.preset.bind("<<ComboboxSelected>>", self._preset_changed)

        self.server_entry = self._field(pad, "服务器", self.server_var)
        self.user_entry = self._field(pad, "账号", self.user_var)
        self.pass_entry = self._field(pad, "密码", self.pass_var, show="*")

        self.status_var = tk.StringVar(value="")
        self.status = tk.Label(pad, textvariable=self.status_var, bg=BG, fg=WARN,
                               font=FONT_SMALL, wraplength=490, justify="left")
        self.status.pack(anchor="w", pady=(8, 4))

        row = tk.Frame(pad, bg=BG)
        row.pack(side="bottom", fill="x")
        self.login_btn = flat_button(row, "登录", self._submit, accent=True)
        self.login_btn.pack(side="left")
        flat_button(row, "打开官网", self._open_site).pack(side="left", padx=8)
        flat_button(row, "取消", self._close).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Return>", lambda e: self._submit())
        self.after(100, self._poll)
        if existing:
            self.pass_entry.focus_set()
        else:
            self.user_entry.focus_set()

    def _field(self, parent, label, variable, show=None):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=5)
        tk.Label(row, text=label, bg=BG, fg=MUTED, font=FONT_SMALL,
                 width=10, anchor="w").pack(side="left")
        entry = tk.Entry(row, textvariable=variable, bg=PANEL2, fg=FG,
                         insertbackground=ACCENT, relief="flat", bd=0,
                         font=FONT, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT,
                         show=show)
        entry.pack(side="left", fill="x", expand=True, ipady=6)
        return entry

    def _preset_changed(self, _event=None):
        if self.preset_var.get() == "LittleSkin":
            self.server_var.set("https://littleskin.cn/api/yggdrasil")
        else:
            self.server_entry.focus_set()

    def _open_site(self):
        if self.preset_var.get() == "LittleSkin":
            webbrowser.open("https://littleskin.cn/auth/register")
        else:
            server = self.server_var.get().strip()
            if server:
                webbrowser.open(server)

    def _submit(self):
        if self.busy:
            return
        server = self.server_var.get().strip()
        username = self.user_var.get().strip()
        password = self.pass_var.get()
        if not server or not username or not password:
            self.status.configure(fg=ERR)
            self.status_var.set("请填写服务器、账号和密码。")
            return
        self.busy = True
        self.status.configure(fg=WARN)
        self.status_var.set("正在连接外置登录服务器…")
        self.login_btn.configure(state="disabled")
        self.preset.configure(state="disabled")
        threading.Thread(target=self._worker,
                         args=(server, username, password), daemon=True).start()

    def _worker(self, server, username, password):
        try:
            account = auth.external_account(server, username, password)
        except Exception as e:
            logging.exception("external login failed")
            self.msg_queue.put(("error", e))
        else:
            self.msg_queue.put(("ok", account))

    def _close(self):
        self.finished = True
        self.destroy()

    def _poll(self):
        if self.finished:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                if msg[0] == "ok":
                    account = msg[1]
                    self.finished = True
                    self.app.set_account(account)
                    self.app._set_status(f"登录成功：{account.name}")
                    self.destroy()
                    return
                self.busy = False
                self.status.configure(fg=ERR)
                self.status_var.set(str(msg[1]))
                self.login_btn.configure(state="normal")
                self.preset.configure(state="readonly")
        except queue.Empty:
            pass
        self.after(120, self._poll)


class CrashDialog(BaseDialog):
    def __init__(self, app, instance: dict, exit_code, crash_path,
                 log_path, findings: list[dict], report: str):
        super().__init__(app, "游戏崩溃", 780, 620)
        self.resizable(True, True)
        self.instance = instance or {}
        self.exit_code = exit_code
        self.crash_path = str(crash_path or "")
        self.log_path = str(log_path or "")
        self.findings = findings or []
        self.report = report or ""

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=18, pady=14)
        _title(pad, "游戏崩溃",
               f"实例 {self.instance.get('name') or self.instance.get('version') or '-'}"
               f" · 退出代码 {exit_code}")

        nb = ttk.Notebook(pad)
        nb.pack(fill="both", expand=True, pady=(12, 10))
        diag_tab = tk.Frame(nb, bg=BG)
        nb.add(diag_tab, text="  自动诊断  ")
        diag_box, self.diag_text = text_area(diag_tab, height=14, font=FONT_SMALL)
        diag_box.pack(fill="both", expand=True)
        append_text(self.diag_text, diagnose.findings_text(self.findings))

        log_tab = tk.Frame(nb, bg=BG)
        nb.add(log_tab, text="  错误日志  ")
        log_box, self.log_text = text_area(log_tab, height=18, font=FONT_SMALL)
        log_box.pack(fill="both", expand=True)
        append_text(self.log_text, self.report)

        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x", side="bottom")
        self.export_btn = flat_button(row, "导出错误日志", self._export)
        self.export_btn.pack(side="left")
        self.repair_btn = flat_button(row, "补全游戏文件", self._repair, accent=True)
        self.repair_btn.pack(side="left", padx=8)
        flat_button(row, "打开日志文件夹", self._open_folder).pack(side="left")
        flat_button(row, "关闭", self._close).pack(side="right")
        flat_button(row, "重新启动", self._restart).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())

    def _append(self, text: str):
        append_text(self.diag_text, text)
        self.report = self.report.rstrip() + "\n\n" + text.strip() + "\n"
        append_text(self.log_text, "\n" + text.strip())

    def _export(self):
        name = f"crash-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(
            parent=self, title="导出错误日志", defaultextension=".txt",
            initialfile=name,
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        report = self.report
        if self.log_path:
            try:
                full_log = Path(self.log_path).read_text(
                    encoding="utf-8", errors="replace")
                if full_log.strip():
                    report += "\n\n===== 完整日志文件 =====\n" + full_log
            except OSError:
                pass
        try:
            Path(path).write_text(report, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("导出失败", str(e), parent=self)
            return
        messagebox.showinfo("导出完成", path, parent=self)

    def _open_folder(self):
        target = Path(self.log_path).parent if self.log_path else self.app.cfg.log_dir()
        self.app._open_path(target)

    def _repair(self):
        self.repair_btn.configure(state="disabled", text="正在补全…")
        self._append("开始校验并补全客户端、依赖库和资源文件…")
        if not self.app.repair_instance(self.instance, self._on_repaired,
                                        self._on_repair_failed):
            self.repair_btn.configure(state="normal", text="补全游戏文件")
            self._append("当前有任务正在进行，请稍后再试。")

    def _on_repair_failed(self, _error):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.repair_btn.configure(state="normal", text="补全游戏文件")
        self._append("补全失败，请查看错误信息和网络状态后重试。")

    def _on_repaired(self, result):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.repair_btn.configure(state="normal", text="补全游戏文件")
        text = f"补全完成：{result.get('version') or self.instance.get('version')}"
        self._append(text)
        messagebox.showinfo(
            "补全完成",
            f"{text}\n现在可以点击“重新启动”再次尝试。",
            parent=self)

    def _restart(self):
        instance_id = self.instance.get("id")
        self.destroy()
        self.app.crash_dialog = None
        if instance_id:
            self.app.selected_instance_id = instance_id
            self.app._refresh_instances(select_id=instance_id)
        self.app.after(150, self.app._launch)

    def _close(self):
        self.app.crash_dialog = None
        self.destroy()


class DeviceCodeDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "微软正版登录", 570, 420)
        self.app = app
        self.cancel_event = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self.uri = ""
        self.finished = False

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=22, pady=18)
        _title(pad, "微软正版登录",
               "1. 点击「打开浏览器」并登录微软账号。\n"
               "2. 在页面中输入下方代码并批准。\n"
               "3. 登录成功前请保持此窗口打开。")

        self.code_var = tk.StringVar(value="— — — —")
        code = tk.Label(pad, textvariable=self.code_var, bg=PANEL2, fg=ACCENT,
                        font=FONT_HUGE, padx=18, pady=12)
        code.pack(fill="x", pady=(16, 10))

        self.uri_var = tk.StringVar(value="正在获取验证地址…")
        tk.Label(pad, textvariable=self.uri_var, bg=BG, fg=MUTED, font=FONT_SMALL,
                 wraplength=510, justify="left").pack(anchor="w")

        self.status_var = tk.StringVar(value="正在向微软请求登录代码…")
        self.status = tk.Label(pad, textvariable=self.status_var, bg=BG, fg=WARN,
                               font=FONT_SMALL, wraplength=510, justify="left")
        self.status.pack(anchor="w", pady=(10, 8))

        row = tk.Frame(pad, bg=BG)
        row.pack(side="bottom", fill="x")
        self.open_btn = flat_button(row, "打开浏览器", self._open_browser, accent=True)
        self.open_btn.pack(side="left")
        self.open_btn.configure(state="disabled")
        self.copy_btn = flat_button(row, "复制代码", self._copy_code)
        self.copy_btn.pack(side="left", padx=8)
        self.copy_btn.configure(state="disabled")
        self.cancel_btn = flat_button(row, "取消", self._cancel, danger=True)
        self.cancel_btn.pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(150, self._poll)

    def _worker(self):
        client_id = (self.app.cfg.get("ms_client_id") or DEFAULT_CLIENT_ID).strip()
        try:
            account = auth.device_code_login(client_id, on_code=self._on_code,
                                             cancel=self.cancel_event)
        except Cancelled:
            self.msg_queue.put(("cancelled",))
        except Exception as e:
            logging.exception("microsoft login failed")
            self.msg_queue.put(("error", e))
        else:
            self.msg_queue.put(("ok", account))

    def _on_code(self, code, uri, expires_in, message):
        self.msg_queue.put(("code", code, uri, int(expires_in), message))

    def _copy_code(self):
        code = self.code_var.get().replace(" ", "")
        if not code or code.startswith("—"):
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        self.status_var.set("代码已复制到剪贴板。请在浏览器中输入该代码。")

    def _open_browser(self):
        if self.uri:
            webbrowser.open(self.uri)

    def _cancel(self):
        self.cancel_event.set()
        self.finished = True
        self.destroy()

    def _poll(self):
        if self.finished:
            return
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "code":
                    _, code, uri, expires_in, message = msg
                    self.uri = uri or ""
                    self.code_var.set(" ".join(code))
                    self.uri_var.set(f"验证地址：{uri}")
                    self.open_btn.configure(state="normal")
                    self.copy_btn.configure(state="normal")
                    self.status_var.set("等待浏览器中完成登录…")
                    self._copy_code()
                elif kind == "ok":
                    account = msg[1]
                    self.finished = True
                    self.app.set_account(account)
                    self.app._set_status(f"登录成功：{account.name}")
                    self.destroy()
                    return
                elif kind == "error":
                    err = msg[1]
                    self.status.configure(fg=ERR)
                    self.status_var.set(str(err))
                    self.cancel_btn.configure(text="关闭")
                    self.open_btn.configure(state="disabled")
                    self.copy_btn.configure(state="disabled")
                elif kind == "cancelled":
                    self.finished = True
                    self.destroy()
                    return
        except queue.Empty:
            pass
        self.after(150, self._poll)


class InstanceDialog(BaseDialog):
    def __init__(self, app, instance=None):
        self.app = app
        self.instance = instance
        self.is_edit = instance is not None
        super().__init__(app, "编辑实例" if self.is_edit else "新建实例", 620, 560 if not self.is_edit else 520)
        self._last_auto = ""
        self.loader_var = tk.StringVar(value="原版")
        self.preview_var = tk.StringVar()
        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=22, pady=18)
        pad.columnconfigure(1, weight=1)
        header = tk.Frame(pad, bg=BG)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        _title(header, "编辑实例" if self.is_edit else "新建实例",
               "实例拥有独立的游戏目录、存档与模组。" if not self.is_edit else instance.get("version", ""))

        row = 1
        self.name_var = tk.StringVar(value=(instance or {}).get("name", ""))
        self.version_var = tk.StringVar()
        self.memory_var = tk.StringVar(value=str((instance or {}).get("memory") or app.cfg.get("memory_mb", 4096)))
        self.jvm_var = tk.StringVar(value=(instance or {}).get("jvm_args", ""))
        self.java_var = tk.StringVar(value=(instance or {}).get("java", ""))
        self.dir_mode = tk.StringVar(value="own")
        self.game_dir_var = tk.StringVar(value=(instance or {}).get("game_dir", ""))

        if self.is_edit:
            self._label(pad, row, "实例名称")
            tk.Entry(pad, textvariable=self.name_var, **self._entry_opts()).grid(
                row=row, column=1, sticky="ew", pady=6)
            row += 1
            self._label(pad, row, "游戏版本")
            tk.Label(pad, text=instance.get("version", ""), bg=BG, fg=ACCENT,
                     font=FONT_BOLD).grid(row=row, column=1, sticky="w", pady=6)
            row += 1
            self._label(pad, row, "游戏目录")
            frame = tk.Frame(pad, bg=BG)
            frame.grid(row=row, column=1, sticky="ew", pady=6)
            frame.columnconfigure(0, weight=1)
            tk.Entry(frame, textvariable=self.game_dir_var, **self._entry_opts()).grid(
                row=0, column=0, sticky="ew")
            flat_button(frame, "浏览", self._browse_game_dir, padx=10, pady=4).grid(
                row=0, column=1, padx=(6, 0))
            row += 1
            self._label(pad, row, "Java 路径")
            frame = tk.Frame(pad, bg=BG)
            frame.grid(row=row, column=1, sticky="ew", pady=6)
            frame.columnconfigure(0, weight=1)
            tk.Entry(frame, textvariable=self.java_var, **self._entry_opts()).grid(
                row=0, column=0, sticky="ew")
            flat_button(frame, "浏览", self._browse_java, padx=10, pady=4).grid(
                row=0, column=1, padx=(6, 0))
            row += 1
        else:
            installed = app.repo.installed()
            if not installed:
                messagebox.showinfo("没有可用版本", "请先到「下载」页面安装一个游戏版本。", parent=self)
                self.after(10, self.destroy)
                return
            versions = [item["id"] for item in installed]
            self.version_var.set(versions[0])
            self._last_auto = versions[0]
            self.name_var.set(versions[0])
            self._label(pad, row, "实例名称")
            tk.Entry(pad, textvariable=self.name_var, **self._entry_opts()).grid(
                row=row, column=1, sticky="ew", pady=6)
            row += 1
            self._label(pad, row, "游戏版本")
            combo = ttk.Combobox(pad, textvariable=self.version_var, values=versions,
                                    state="readonly", font=FONT)
            combo.grid(row=row, column=1, sticky="ew", pady=6, ipady=3)
            combo.bind("<<ComboboxSelected>>", lambda e: self._on_version_change())
            row += 1
            self._label(pad, row, "加载器")
            tk.Label(pad, textvariable=self.loader_var, bg=BG, fg=ACCENT,
                     font=FONT_BOLD).grid(row=row, column=1, sticky="w", pady=6)
            row += 1
            self._label(pad, row, "游戏目录")
            modes = tk.Frame(pad, bg=BG)
            modes.grid(row=row, column=1, sticky="w", pady=4)
            tk.Radiobutton(modes, text="独立目录（推荐）", variable=self.dir_mode, value="own",
                           bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                           activeforeground=FG, font=FONT, bd=0,
                           command=self._update_preview).pack(side="left")
            tk.Radiobutton(modes, text="使用 .minecraft 根目录", variable=self.dir_mode, value="root",
                           bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                           activeforeground=FG, font=FONT, bd=0,
                           command=self._update_preview).pack(side="left", padx=(14, 0))
            row += 1
            tk.Label(pad, textvariable=self.preview_var, bg=BG, fg=MUTED, font=FONT_SMALL,
                     wraplength=420, justify="left").grid(row=row, column=1, sticky="w", pady=(0, 6))
            row += 1
            self.name_var.trace_add("write", lambda *a: self._update_preview())
            self._on_version_change(silent=True)

        self._label(pad, row, "内存 (MB)")
        tk.Spinbox(pad, from_=0, to=32768, increment=512, textvariable=self.memory_var,
                   bg=PANEL2, fg=FG, buttonbackground=PANEL3, insertbackground=FG,
                   relief="flat", bd=0, font=FONT, width=10,
                   highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=ACCENT).grid(row=row, column=1, sticky="w", pady=6)
        row += 1

        self._label(pad, row, "JVM 参数")
        tk.Entry(pad, textvariable=self.jvm_var, **self._entry_opts()).grid(
            row=row, column=1, sticky="ew", pady=6)
        row += 1
        tk.Label(pad, text="可选，例如 -XX:+UseG1GC；留空使用默认值。", bg=BG, fg=MUTED,
                 font=FONT_SMALL).grid(row=row, column=1, sticky="w")
        row += 1

        row_frame = tk.Frame(pad, bg=BG)
        row_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        flat_button(row_frame, "保存" if self.is_edit else "创建", self._submit,
                    accent=True).pack(side="left")
        flat_button(row_frame, "取消", self.destroy).pack(side="left", padx=8)

    def _entry_opts(self):
        return {
            "bg": PANEL2, "fg": FG, "insertbackground": FG, "relief": "flat",
            "bd": 0, "font": FONT, "highlightthickness": 1,
            "highlightbackground": BORDER, "highlightcolor": ACCENT,
        }

    def _label(self, parent, row, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED, font=FONT).grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=6)

    def _on_version_change(self, silent=False):
        version_id = self.version_var.get()
        if not version_id:
            return
        loader = self.app.detect_loader(version_id)
        names = {"vanilla": "原版", "fabric": "Fabric", "forge": "Forge",
                 "neoforge": "NeoForge", "quilt": "Quilt"}
        self.loader_var.set(names.get(loader, loader))
        if not silent and self.name_var.get() in ("", self._last_auto):
            self.name_var.set(version_id)
            self._last_auto = version_id
        self._update_preview()

    def _update_preview(self):
        if self.is_edit:
            return
        if self.dir_mode.get() == "root":
            path = self.app.repo.root
        else:
            name = safe_folder_name(self.name_var.get() or "instance")
            path = self.app.repo.root / "instances" / name
        self.preview_var.set(str(path))

    def _browse_game_dir(self):
        path = filedialog.askdirectory(parent=self, initialdir=self.game_dir_var.get() or str(self.app.repo.root))
        if path:
            self.game_dir_var.set(path)

    def _browse_java(self):
        path = filedialog.askopenfilename(parent=self, title="选择 java.exe",
                                          filetypes=[("java.exe", "java.exe"), ("所有文件", "*.*")])
        if path:
            self.java_var.set(path)

    def _submit(self):
        try:
            memory = int(self.memory_var.get() or 0)
        except ValueError:
            messagebox.showerror("参数错误", "内存需要是整数（MB）。", parent=self)
            return
        if memory and not (512 <= memory <= 65536):
            messagebox.showerror("参数错误", "内存需要在 512 - 65536 MB 之间，或填 0 使用默认。", parent=self)
            return
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("参数错误", "请填写实例名称。", parent=self)
            return
        if self.is_edit:
            game_dir = self.game_dir_var.get().strip() or str(self.app.repo.root)
            self.app.update_instance_fields(
                self.instance["id"], name=name, game_dir=game_dir,
                memory=memory, jvm_args=self.jvm_var.get().strip(),
                java=self.java_var.get().strip(),
            )
        else:
            version_id = self.version_var.get()
            if not version_id:
                messagebox.showerror("参数错误", "请选择游戏版本。", parent=self)
                return
            if self.dir_mode.get() == "root":
                game_dir = self.app.repo.root
            else:
                game_dir = unique_dir(self.app.repo.root / "instances", name)
            self.app.create_instance(name, version_id, game_dir, memory,
                                     self.jvm_var.get().strip())
        self.destroy()


class LogWindow(tk.Toplevel):
    def __init__(self, app, lines=None):
        super().__init__(app)
        self.app = app
        self.configure(bg=BG)
        self.title("游戏日志")
        self.geometry("940x560")
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        for line in lines or []:
            self.append(line, scroll=False)
        self.after(80, self._scroll_end)

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(top, text="游戏日志", bg=BG, fg=FG, font=FONT_BOLD).pack(side="left")
        flat_button(top, "关闭", self.close).pack(side="right")
        flat_button(top, "清空", self.clear).pack(side="right", padx=8)
        flat_button(top, "打开日志文件", self.app.open_latest_log).pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.text = tk.Text(body, bg=INPUT, fg=FG, insertbackground=ACCENT,
                            selectbackground=ACCENT_DARK, selectforeground=SELECT_FG,
                            font=FONT_SMALL, relief="flat", bd=0, wrap="none",
                            padx=10, pady=8, state="disabled")
        scroll = tk.Scrollbar(body, command=self.text.yview, bd=0, relief="flat",
                              bg=PANEL2, troughcolor=BG, activebackground=BORDER)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def append(self, line: str, scroll: bool = True):
        try:
            if not self.winfo_exists():
                return
            self.text.configure(state="normal")
            self.text.insert("end", line.rstrip("\n") + "\n")
            # 防止日志无限增长
            if float(self.text.index("end-1c").split(".")[0]) > 20000:
                self.text.delete("1.0", "2000.0")
            self.text.configure(state="disabled")
            if scroll:
                self.text.see("end")
        except tk.TclError:
            pass

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _scroll_end(self):
        try:
            self.text.see("end")
        except tk.TclError:
            pass

    def close(self):
        try:
            self.app.log_window = None
        except Exception:
            pass
        self.destroy()
