"""CustomSkinLoader 本地披风 / 鞘翅设置。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from core import skins
from core.util import ensure_dir
from ui.dialogs import BaseDialog
from ui.theme import (
    BG,
    BORDER,
    FG,
    FONT_BOLD,
    FONT_SMALL,
    MUTED,
    PANEL,
)
from ui.widgets import flat_button

TEXTURES = (("cape", "披风"), ("elytra", "鞘翅"))


class LocalTextureDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "本地披风 / 鞘翅", 580, 440)
        self.app = app
        instance = app._selected_instance()
        self.game_dir = Path((instance or {}).get("game_dir") or app.repo.root)
        self.account = getattr(app, "account", None)
        self.status_vars: dict[str, tk.StringVar] = {}

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=20, pady=16)
        tk.Label(pad, text="本地披风 / 鞘翅", bg=BG, fg=FG,
                 font=("Courier New", 15, "bold")).pack(anchor="w")
        tk.Label(
            pad, text="通过 CustomSkinLoader 的 LocalSkin 文件夹加载，"
                      "主要对离线账号生效。",
            bg=BG, fg=MUTED, font=FONT_SMALL, justify="left",
            wraplength=520).pack(anchor="w", pady=(5, 10))

        info = tk.Frame(pad, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER)
        info.pack(fill="x", pady=(0, 12))
        self.account_var = tk.StringVar()
        self.csl_var = tk.StringVar()
        self.dir_var = tk.StringVar()
        for label, var in (("账号", self.account_var),
                           ("CustomSkinLoader", self.csl_var),
                           ("材质目录", self.dir_var)):
            row = tk.Frame(info, bg=PANEL)
            row.pack(fill="x", padx=12, pady=4)
            tk.Label(row, text=label, bg=PANEL, fg=MUTED, width=16,
                     anchor="w", font=FONT_SMALL).pack(side="left")
            tk.Label(row, textvariable=var, bg=PANEL, fg=FG, anchor="w",
                     font=FONT_SMALL, justify="left",
                     wraplength=380).pack(side="left", fill="x", expand=True)

        for kind, label in TEXTURES:
            frame = tk.Frame(pad, bg=PANEL, highlightthickness=1,
                             highlightbackground=BORDER)
            frame.pack(fill="x", pady=(0, 10))
            tk.Label(frame, text=label, bg=PANEL, fg=FG, width=6,
                     anchor="w", font=FONT_BOLD).pack(side="left",
                                                       padx=(12, 0),
                                                       pady=10)
            var = tk.StringVar(value="未设置")
            self.status_vars[kind] = var
            tk.Label(frame, textvariable=var, bg=PANEL, fg=MUTED,
                     anchor="w", font=FONT_SMALL).pack(
                side="left", padx=(4, 8))
            flat_button(
                frame, "选择 PNG",
                lambda k=kind, l=label: self._choose(k, l),
                padx=10, pady=4).pack(side="right", padx=(6, 10), pady=8)
            flat_button(
                frame, "移除",
                lambda k=kind, l=label: self._remove(k, l),
                danger=True, padx=8, pady=4).pack(side="right", pady=8)

        tk.Label(
            pad, text="披风建议 64×32（或其整数倍）；鞘翅使用披风贴图。"
                      "\n重新启动游戏后生效。",
            bg=BG, fg=MUTED, font=FONT_SMALL, justify="left").pack(
            anchor="w", pady=(2, 0))

        footer = tk.Frame(pad, bg=BG)
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        flat_button(footer, "打开本地材质文件夹",
                    self._open_folder, padx=10, pady=4).pack(side="left")
        flat_button(footer, "关闭", self.destroy, padx=14,
                    pady=4).pack(side="right")
        self._refresh()

    def _username(self) -> str:
        return (getattr(self.account, "name", "") or "").strip()

    def _refresh(self):
        name = self._username()
        self.account_var.set(name or "未登录")
        self.csl_var.set(
            "已检测到" if skins.csl_available(self.game_dir) else "未检测到")
        self.dir_var.set(str(self.game_dir / "CustomSkinLoader" / "LocalSkin"))
        for kind, var in self.status_vars.items():
            if not name:
                var.set("未登录")
                continue
            path = skins.csl_texture_path(self.game_dir, name, kind)
            var.set(f"已设置：{path.name}" if path.is_file() else "未设置")

    def _require_account(self) -> bool:
        if self._username():
            return True
        messagebox.showinfo("未登录", "请先在账户窗口登录，本地材质需要用户名。",
                            parent=self)
        return False

    def _choose(self, kind: str, label: str):
        if not self._require_account():
            return
        path = filedialog.askopenfilename(
            parent=self, title=f"选择{label} PNG",
            filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            dest = skins.apply_csl_texture(
                self.game_dir, self._username(), kind, path)
        except Exception as e:
            messagebox.showerror(f"{label}应用失败", str(e), parent=self)
            return
        self._refresh()
        message = f"{label}已应用：\n{dest}\n\n重新启动游戏后生效。"
        if not skins.csl_available(self.game_dir):
            message += "\n\n未检测到 CustomSkinLoader，安装该模组后才会生效。"
        messagebox.showinfo("应用完成", message, parent=self)

    def _remove(self, kind: str, label: str):
        if not self._require_account():
            return
        path = skins.csl_texture_path(self.game_dir, self._username(), kind)
        if not path.is_file():
            messagebox.showinfo("提示", f"当前没有设置{label}。", parent=self)
            return
        if not messagebox.askyesno(
                "移除材质", f"确定移除本地{label}吗？\n{path}", parent=self):
            return
        try:
            skins.remove_csl_texture(self.game_dir, self._username(), kind)
        except OSError as e:
            messagebox.showerror("移除失败", str(e), parent=self)
            return
        self._refresh()

    def _open_folder(self):
        folder = ensure_dir(self.game_dir / "CustomSkinLoader" / "LocalSkin")
        try:
            self.app._open_path(folder)
        except Exception:
            messagebox.showinfo("材质目录", str(folder), parent=self)

