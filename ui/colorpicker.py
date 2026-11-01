"""自由调色对话框：RGB 滑块 + 十六进制输入 + 实时预览。"""
from __future__ import annotations

import tkinter as tk

from ui.dialogs import BaseDialog
from ui.theme import (
    ACCENT,
    BG,
    BORDER,
    FG,
    FONT,
    FONT_SMALL,
    MUTED,
    PANEL,
    PANEL2,
)
from ui.widgets import flat_button


def parse_hex(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = "#" + text
    if len(text) != 7:
        return None
    try:
        int(text[1:], 16)
    except ValueError:
        return None
    return text.lower()


class ColorPickerDialog(BaseDialog):
    def __init__(self, parent, color: str = "#d9a066", on_change=None,
                 on_done=None):
        super().__init__(parent, "自由调色", 430, 400)
        self.on_change = on_change
        self.on_done = on_done
        self._done = False
        self._updating = False
        self._color = parse_hex(color) or "#d9a066"

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=20, pady=16)
        tk.Label(pad, text="自由调色", bg=BG, fg=FG,
                 font=("Courier New", 15, "bold")).pack(anchor="w")
        tk.Label(pad, text="拖动红/绿/蓝滑块，或直接输入十六进制颜色。",
                 bg=BG, fg=MUTED, font=FONT_SMALL).pack(anchor="w",
                                                        pady=(4, 10))

        top = tk.Frame(pad, bg=BG)
        top.pack(fill="x", pady=(0, 10))
        self.preview = tk.Label(top, text="", width=7, height=3,
                                bg=self._color, relief="flat", bd=0)
        self.preview.pack(side="left")
        right = tk.Frame(top, bg=BG)
        right.pack(side="left", fill="x", expand=True, padx=(12, 0))
        tk.Label(right, text="十六进制", bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w")
        self.hex_var = tk.StringVar(value=self._color)
        entry = tk.Entry(right, textvariable=self.hex_var, width=12, bd=0,
                         bg=PANEL, fg=FG, insertbackground=ACCENT,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, font=FONT)
        entry.pack(anchor="w", pady=(2, 0), ipady=4)
        entry.bind("<Return>", lambda e: self._apply_hex())
        entry.bind("<FocusOut>", lambda e: self._apply_hex())

        self.scales: dict[str, tk.Scale] = {}
        self.values: dict[str, tk.StringVar] = {}
        rgb = self._to_rgb(self._color)
        for key, name, value in (("r", "红", rgb[0]), ("g", "绿", rgb[1]),
                                 ("b", "蓝", rgb[2])):
            row = tk.Frame(pad, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=name, bg=BG, fg=FG, width=3,
                     font=FONT).pack(side="left")
            var = tk.StringVar(value=str(value))
            self.values[key] = var
            tk.Label(row, textvariable=var, bg=BG, fg=MUTED, width=4,
                     font=FONT_SMALL).pack(side="right")
            scale = tk.Scale(
                row, from_=0, to=255, orient="horizontal", showvalue=False,
                command=lambda _v, k=key: self._on_scale(k),
                bg=BG, fg=FG, troughcolor=PANEL2, activebackground=ACCENT,
                highlightthickness=0, bd=0, sliderrelief="flat",
                length=250)
            scale.set(value)
            scale.pack(side="left", fill="x", expand=True, padx=(6, 6))
            self.scales[key] = scale

        self.swatches = tk.Frame(pad, bg=BG)
        self.swatches.pack(fill="x", pady=(10, 0))
        for color_value in ("#f5d0a9", "#d9a066", "#8d5524", "#ffffff",
                            "#111111", "#e53935", "#fdd835", "#43a047",
                            "#1e88e5", "#8e24aa"):
            button = tk.Button(
                self.swatches, bg=color_value, activebackground=color_value,
                width=3, height=1, bd=1, relief="flat", cursor="hand2",
                command=lambda c=color_value: self._set_color(c))
            button.pack(side="left", padx=2)

        footer = tk.Frame(pad, bg=BG)
        footer.pack(fill="x", side="bottom", pady=(14, 0))
        flat_button(footer, "应用", self._ok, accent=True,
                    padx=18, pady=6).pack(side="right")
        flat_button(footer, "取消", self._cancel, padx=14,
                    pady=6).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._refresh(self._color, notify=False)

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _to_rgb(color: str):
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    @staticmethod
    def _to_hex(r: int, g: int, b: int) -> str:
        return f"#{r:02x}{g:02x}{b:02x}"

    def _on_scale(self, _key):
        if self._updating:
            return
        rgb = tuple(int(self.scales[k].get()) for k in ("r", "g", "b"))
        self._refresh(self._to_hex(*rgb), notify=True, set_scales=False)

    def _apply_hex(self):
        color = parse_hex(self.hex_var.get())
        if color:
            self._refresh(color, notify=True)
        else:
            self.hex_var.set(self._color)

    def _set_color(self, color: str):
        self._refresh(color, notify=True)

    def _refresh(self, color: str, notify: bool = True,
                 set_scales: bool = True):
        color = parse_hex(color) or self._color
        self._color = color
        self._updating = True
        try:
            self.preview.configure(bg=color)
            self.hex_var.set(color)
            rgb = self._to_rgb(color)
            for key, value in zip(("r", "g", "b"), rgb):
                if set_scales:
                    self.scales[key].set(value)
                self.values[key].set(str(value))
        finally:
            self._updating = False
        if notify and self.on_change:
            self.on_change(color)

    def _ok(self):
        self._finish(self._color)

    def _cancel(self):
        self._finish(None)

    def _finish(self, color: str | None):
        if self._done:
            return
        self._done = True
        if self.on_done:
            self.on_done(color)
        self.destroy()
