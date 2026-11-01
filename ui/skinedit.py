from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import urlsplit

from core import auth, skins
from core.config import DEFAULT_CLIENT_ID
from core.util import ensure_dir
from ui.colorpicker import ColorPickerDialog
from ui.dialogs import BaseDialog
from ui.skin3d import PaintHit, SkinPaintView
from ui.theme import (
    ACCENT,
    BG,
    BORDER,
    DARK_FG,
    FG,
    FONT_SMALL,
    MUTED,
    PANEL,
    PANEL2,
    PANEL3,
)
from ui.widgets import flat_button

CELL = 8
SIZE = 64
CANVAS_SIZE = CELL * SIZE
PALETTE = (
    "#f5d0a9", "#d9a066", "#8d5524", "#3b2416",
    "#ffffff", "#111111", "#e53935", "#fb8c00",
    "#fdd835", "#43a047", "#1e88e5", "#8e24aa",
)
LEGACY_COPY = (
    ((4, 20, 4, 12), (20, 52)),
    ((12, 20, 4, 12), (28, 52)),
    ((0, 20, 4, 12), (16, 52)),
    ((8, 20, 4, 12), (24, 52)),
    ((4, 16, 4, 4), (20, 48)),
    ((8, 16, 4, 4), (24, 48)),
    ((44, 20, 4, 12), (36, 52)),
    ((52, 20, 4, 12), (44, 52)),
    ((40, 20, 4, 12), (32, 52)),
    ((48, 20, 4, 12), (40, 52)),
    ((44, 16, 4, 4), (36, 48)),
    ((48, 16, 4, 4), (40, 48)),
)


def _cells_line(x0, y0, x1, y1):
    """Bresenham 直线上的像素坐标。"""
    cells = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        cells.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return cells


class SkinEditorDialog(BaseDialog):
    def __init__(self, app, skin_path=None):
        super().__init__(app, "皮肤编辑器", 950, 650)
        self.resizable(True, True)
        self.app = app
        self.pixels: list[list[str | None]] = [
            [None] * SIZE for _ in range(SIZE)]
        self.items: dict[tuple[int, int], int] = {}
        self.color = "#d9a066"
        self.tool = "pencil"
        self.brush = 1
        self.mode = "3d"
        self.undo_stack: list[list[tuple[int, int, str | None, str | None]]] = []
        self.redo_stack: list[list[tuple[int, int, str | None, str | None]]] = []
        self.current_stroke: list[tuple[int, int, str | None, str | None]] = []
        self._grid_last = None
        self._grid_active = False
        self._tool_buttons: dict[str, tk.Button] = {}
        self._mode_buttons: dict[str, tk.Button] = {}
        self.upload_queue: queue.Queue = queue.Queue()
        self.uploading = False
        self.last_upload_path: Path | None = None
        self.csl_var = tk.BooleanVar(value=False)
        self.slim_var = tk.BooleanVar(value=False)
        self.layer_var = tk.StringVar(value="auto")

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=16, pady=12)

        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="皮肤编辑器", bg=BG, fg=FG,
                 font=("Courier New", 16, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="左键在模型上绘制")
        tk.Label(head, textvariable=self.status_var, bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(side="right")

        mode_row = tk.Frame(pad, bg=BG)
        mode_row.pack(fill="x", pady=(0, 8))
        self._mode_buttons["3d"] = flat_button(
            mode_row, "立体绘制", lambda: self._set_mode("3d"),
            padx=12, pady=5)
        self._mode_buttons["3d"].pack(side="left")
        self._mode_buttons["2d"] = flat_button(
            mode_row, "展开图", lambda: self._set_mode("2d"),
            padx=12, pady=5)
        self._mode_buttons["2d"].pack(side="left", padx=6)
        tk.Label(mode_row, text="左键落笔 · 右键拖动旋转 · 滚轮缩放",
                 bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left", padx=(12, 0))
        flat_button(mode_row, "本地披风/鞘翅", self._open_local_textures,
                    padx=9, pady=3).pack(side="right")

        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)

        stage_wrap = tk.Frame(body, bg=BG)
        stage_wrap.pack(side="left", fill="both", expand=True)
        self.stage = tk.Frame(stage_wrap, bg=PANEL, highlightthickness=1,
                              highlightbackground=BORDER)
        self.stage.pack(fill="both", expand=True)

        self.view = SkinPaintView(
            self.stage, self.pixels,
            on_paint=self._on_paint, on_end=self._on_end,
            on_hover=self._on_hover)
        self.view.pack(fill="both", expand=True)

        self.grid_frame = tk.Frame(self.stage, bg=PANEL)
        self.grid_canvas = tk.Canvas(
            self.grid_frame, width=CANVAS_SIZE, height=CANVAS_SIZE,
            bg=PANEL, highlightthickness=0)
        self.grid_canvas.pack(expand=True)
        self.grid_canvas.bind("<Button-1>", self._grid_down)
        self.grid_canvas.bind("<B1-Motion>", self._grid_move)
        self.grid_canvas.bind("<ButtonRelease-1>", self._grid_up)

        tools = tk.Frame(body, bg=BG, width=272)
        tools.pack(side="left", fill="y", padx=(14, 0))

        tk.Label(tools, text="颜色", bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w")
        color_row = tk.Frame(tools, bg=BG)
        color_row.pack(fill="x", pady=(4, 8))
        self.swatch = tk.Label(color_row, text="", width=3, bg=self.color,
                               relief="flat", bd=0)
        self.swatch.pack(side="left")
        self.color_var = tk.StringVar(value=self.color)
        tk.Label(color_row, textvariable=self.color_var, bg=BG, fg=MUTED,
                 font=FONT_SMALL, width=8, anchor="w").pack(
            side="left", padx=(5, 5))
        flat_button(color_row, "自由调色", self._pick_color, padx=8,
                    pady=3).pack(side="left")

        palette = tk.Frame(tools, bg=BG)
        palette.pack(fill="x", pady=(0, 6))
        for index, color in enumerate(PALETTE):
            button = tk.Button(
                palette, bg=color, activebackground=color, width=3, height=1,
                bd=1, relief="flat", cursor="hand2",
                command=lambda c=color: self._set_color(c))
            button.grid(row=index // 4, column=index % 4, padx=2, pady=2)

        tk.Label(tools, text="工具", bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w", pady=(6, 2))
        tool_row1 = tk.Frame(tools, bg=BG)
        tool_row1.pack(fill="x")
        tool_row2 = tk.Frame(tools, bg=BG)
        tool_row2.pack(fill="x", pady=(4, 0))
        self._tool_buttons["pencil"] = flat_button(
            tool_row1, "画笔", lambda: self._set_tool("pencil"),
            padx=9, pady=4)
        self._tool_buttons["pencil"].pack(side="left")
        self._tool_buttons["eraser"] = flat_button(
            tool_row1, "橡皮", lambda: self._set_tool("eraser"),
            padx=9, pady=4)
        self._tool_buttons["eraser"].pack(side="left", padx=6)
        self._tool_buttons["picker"] = flat_button(
            tool_row1, "取色笔", lambda: self._set_tool("picker"),
            padx=9, pady=4)
        self._tool_buttons["picker"].pack(side="left")
        self._tool_buttons["bucket"] = flat_button(
            tool_row2, "填充", lambda: self._set_tool("bucket"),
            padx=9, pady=4)
        self._tool_buttons["bucket"].pack(side="left")
        self._tool_buttons["rotate"] = flat_button(
            tool_row2, "旋转", lambda: self._set_tool("rotate"),
            padx=9, pady=4)
        self._tool_buttons["rotate"].pack(side="left", padx=6)
        flat_button(tool_row2, "撤销", self._undo, padx=9, pady=4).pack(
            side="left")
        flat_button(tool_row2, "重做", self._redo, padx=9, pady=4).pack(
            side="left", padx=(6, 0))

        tk.Label(tools, text="笔刷大小", bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w", pady=(8, 2))
        brush_row = tk.Frame(tools, bg=BG)
        brush_row.pack(fill="x")
        self._brush_buttons: dict[int, tk.Button] = {}
        for size in (1, 2, 3):
            button = flat_button(
                brush_row, f"{size} px",
                lambda s=size: self._set_brush(s), padx=9, pady=3)
            button.pack(side="left", padx=(0, 6))
            self._brush_buttons[size] = button

        tk.Label(tools, text="绘制层", bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w", pady=(8, 0))
        layer_row = tk.Frame(tools, bg=BG)
        layer_row.pack(fill="x")
        for value, text in (("auto", "自动"), ("outer", "外层"),
                            ("inner", "内层")):
            tk.Radiobutton(
                layer_row, text=text, value=value, variable=self.layer_var,
                command=self._apply_layer, bg=BG, fg=FG, selectcolor=PANEL2,
                activebackground=BG, activeforeground=FG,
                font=FONT_SMALL, bd=0, highlightthickness=0,
                cursor="hand2").pack(side="left", padx=(0, 9))

        tk.Checkbutton(
            tools, text="Slim 模型（Alex）", variable=self.slim_var,
            command=self._apply_slim, bg=BG, fg=FG, selectcolor=PANEL2,
            activebackground=BG, activeforeground=FG,
            font=FONT_SMALL, bd=0, highlightthickness=0,
            cursor="hand2").pack(anchor="w", pady=(4, 2))

        flat_button(tools, "重置视角", self._reset_view, padx=10,
                    pady=3).pack(anchor="w", pady=(2, 8))

        action_row = tk.Frame(tools, bg=BG)
        action_row.pack(fill="x", side="bottom")
        flat_button(action_row, "清空画布", self._clear, danger=True,
                    padx=10, pady=4).pack(fill="x")
        flat_button(action_row, "导出 PNG", self._export, accent=True,
                    pady=7).pack(fill="x", pady=(6, 4))
        flat_button(action_row, "保存到实例 skins", self._save_to_skins,
                    padx=10, pady=4).pack(fill="x")
        self.csl_check = tk.Checkbutton(
            action_row, text="同时应用为本地皮肤（CSL）",
            variable=self.csl_var, bg=BG, fg=MUTED, selectcolor=PANEL2,
            activebackground=BG, activeforeground=FG, font=FONT_SMALL,
            bd=0, highlightthickness=0, cursor="hand2")
        self.csl_check.pack(anchor="w", pady=(5, 0))
        self.upload_btn = flat_button(
            action_row, self._upload_label(), self._upload, padx=10, pady=4)
        self.upload_btn.pack(fill="x", pady=(6, 0))
        flat_button(action_row, "关闭", self._close, padx=10,
                    pady=4).pack(fill="x", pady=(6, 0))

        if skin_path and Path(skin_path).is_file():
            if self._load(Path(skin_path)):
                self.slim_var.set(skins.skin_model(skin_path) == "slim")
                self.view.set_slim(self.slim_var.get())
                self.status_var.set(f"已载入：{Path(skin_path).name}")
        try:
            if skins.csl_available(self._game_dir()):
                self.csl_var.set(True)
                self.csl_check.configure(fg=FG)
        except Exception:
            pass
        self.bind("<Control-z>", lambda e: self._undo())
        self.bind("<Control-y>", lambda e: self._redo())
        for key, tool in (("b", "pencil"), ("e", "eraser"),
                          ("i", "picker"), ("g", "bucket"),
                          ("r", "rotate")):
            self.bind(f"<KeyPress-{key}>",
                      lambda e, t=tool: self._set_tool(t))
        self._refresh_tool_buttons()
        self._refresh_mode_buttons()
        self._refresh_brush_buttons()

    # ------------------------------------------------------------------ 模式
    def _set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "3d":
            self.grid_frame.pack_forget()
            self.view.pack(fill="both", expand=True)
            self.view.schedule_draw()
        else:
            self.view.pack_forget()
            self.grid_frame.pack(fill="both", expand=True)
            self._draw_all()
            if self.tool == "rotate":
                self._set_tool("pencil")
        self._refresh_mode_buttons()
        self.status_var.set("立体：左键在模型上绘制" if mode == "3d"
                            else "展开图：在 64×64 贴图上绘制")

    def _refresh_mode_buttons(self):
        for key, button in self._mode_buttons.items():
            self._paint_toggle(button, key == self.mode)

    @staticmethod
    def _paint_toggle(button, active: bool):
        button.configure(
            bg=ACCENT if active else PANEL2,
            fg=DARK_FG if active else FG,
            activebackground=ACCENT if active else PANEL3,
            activeforeground=DARK_FG if active else FG)

    # ------------------------------------------------------------------ 工具
    def _refresh_tool_buttons(self):
        for key, button in self._tool_buttons.items():
            self._paint_toggle(button, key == self.tool)

    def _refresh_brush_buttons(self):
        for size, button in self._brush_buttons.items():
            self._paint_toggle(button, size == self.brush)

    def _set_color(self, color: str):
        self.color = color
        self.swatch.configure(bg=color)
        self.color_var.set(color)
        if self.tool in ("eraser", "picker"):
            self.tool = "pencil"
            self._refresh_tool_buttons()

    def _preview_color(self, color: str):
        self.color = color
        self.swatch.configure(bg=color)
        self.color_var.set(color)

    def _pick_color(self):
        original = self.color
        ColorPickerDialog(
            self, self.color,
            on_change=self._preview_color,
            on_done=lambda color: self._set_color(color or original))

    def _set_tool(self, tool: str):
        self.tool = tool
        self.view.set_rotate_mode(tool == "rotate")
        status = {
            "pencil": "画笔：左键在模型上绘制",
            "eraser": "橡皮：左键擦除为透明",
            "picker": "取色笔：点击模型或展开图取色",
            "bucket": "填充：点击同色区域整体换色",
            "rotate": "旋转：左键拖动模型；绘制时也可右键拖动旋转",
        }
        self.status_var.set(status.get(tool, ""))
        self._refresh_tool_buttons()

    def _set_brush(self, size: int):
        self.brush = size
        self._refresh_brush_buttons()

    def _apply_layer(self):
        self.view.set_layer_mode(self.layer_var.get())

    def _apply_slim(self):
        self.view.set_slim(self.slim_var.get())

    def _reset_view(self):
        self.view.reset_view()

    def _open_local_textures(self):
        from ui.localskin import LocalTextureDialog
        LocalTextureDialog(self.app)

    def _on_hover(self, hit):
        if hit is None:
            self.status_var.set("未指向模型 · 可右键拖动旋转")
            return
        layer = "外层" if hit.overlay else "内层"
        self.status_var.set(
            f"贴图 {hit.u},{hit.v} · {hit.part} · {layer}")

    # ------------------------------------------------------------------ 绘制
    def _on_paint(self, hit: PaintHit, first: bool, previous):
        if self.tool == "picker":
            if first:
                color = self.pixels[hit.v][hit.u]
                if color:
                    self._set_color(color)
                    self.status_var.set(f"已取色 {color}")
            return
        if self.tool == "bucket":
            if first:
                self._flood_fill(hit.u, hit.v, hit.rect)
            return
        if first:
            self.current_stroke = []
            self._paint_at(hit.u, hit.v, hit.rect)
            return
        if previous is not None and previous.rect == hit.rect:
            for u, v in _cells_line(previous.u, previous.v, hit.u, hit.v):
                self._paint_at(u, v, hit.rect)
        else:
            self._paint_at(hit.u, hit.v, hit.rect)

    def _on_end(self):
        if self.current_stroke:
            self.undo_stack.append(self.current_stroke)
            if len(self.undo_stack) > 120:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self.current_stroke = []

    def _brush_cells(self, u, v, rect):
        u0, v0, tw, th = rect
        half = (self.brush - 1) // 2
        for du in range(self.brush):
            for dv in range(self.brush):
                cu = u - half + du
                cv = v - half + dv
                if u0 <= cu < u0 + tw and v0 <= cv < v0 + th:
                    yield cu, cv

    def _paint_at(self, u, v, rect):
        color = None if self.tool == "eraser" else self.color
        for cu, cv in self._brush_cells(u, v, rect):
            self._apply_pixel(cu, cv, color)

    def _apply_pixel(self, u, v, color):
        if not (0 <= u < SIZE and 0 <= v < SIZE):
            return
        old = self.pixels[v][u]
        if old == color:
            return
        self.pixels[v][u] = color
        self.current_stroke.append((u, v, old, color))
        self.view.refresh_texel(u, v)
        if self.mode == "2d":
            self._update_cell(u, v)

    def _flood_fill(self, u, v, rect):
        u0, v0, tw, th = rect
        if not (u0 <= u < u0 + tw and v0 <= v < v0 + th):
            return
        start = self.pixels[v][u]
        color = self.color
        if start == color:
            return
        self.current_stroke = []
        stack = [(u, v)]
        seen = set()
        while stack:
            cu, cv = stack.pop()
            if (cu, cv) in seen:
                continue
            if not (u0 <= cu < u0 + tw and v0 <= cv < v0 + th):
                continue
            if self.pixels[cv][cu] != start:
                continue
            seen.add((cu, cv))
            self._apply_pixel(cu, cv, color)
            stack.extend(((cu + 1, cv), (cu - 1, cv),
                          (cu, cv + 1), (cu, cv - 1)))
        self._on_end()

    # ------------------------------------------------------------------ 撤销
    def _undo(self):
        if not self.undo_stack:
            self.status_var.set("没有可撤销的操作")
            return
        stroke = self.undo_stack.pop()
        for u, v, old, _new in reversed(stroke):
            self.pixels[v][u] = old
            self.view.refresh_texel(u, v)
            if self.mode == "2d":
                self._update_cell(u, v)
        self.redo_stack.append(stroke)
        self.status_var.set(f"已撤销 {len(stroke)} 个像素")

    def _redo(self):
        if not self.redo_stack:
            self.status_var.set("没有可重做的操作")
            return
        stroke = self.redo_stack.pop()
        for u, v, _old, new in stroke:
            self.pixels[v][u] = new
            self.view.refresh_texel(u, v)
            if self.mode == "2d":
                self._update_cell(u, v)
        self.undo_stack.append(stroke)
        self.status_var.set(f"已重做 {len(stroke)} 个像素")

    # ------------------------------------------------------------------ 2D
    def _checker(self, x, y) -> str:
        return "#f6f8fb" if (x + y) % 2 == 0 else "#e4eaf2"

    def _cell_color(self, x, y):
        return self.pixels[y][x] or self._checker(x, y)

    def _draw_all(self):
        self.grid_canvas.delete("all")
        self.items.clear()
        for y in range(SIZE):
            for x in range(SIZE):
                item = self.grid_canvas.create_rectangle(
                    x * CELL, y * CELL, (x + 1) * CELL, (y + 1) * CELL,
                    fill=self._cell_color(x, y), outline="", width=0)
                self.items[(x, y)] = item

    def _update_cell(self, x, y):
        item = self.items.get((x, y))
        if item:
            self.grid_canvas.itemconfigure(item, fill=self._cell_color(x, y))

    def _grid_down(self, event):
        x = event.x // CELL
        y = event.y // CELL
        if not (0 <= x < SIZE and 0 <= y < SIZE):
            return
        if self.tool == "rotate":
            self.status_var.set("旋转工具只在立体模式可用")
            return
        self.grid_canvas.focus_set()
        self._grid_last = (x, y)
        self._grid_active = True
        if self.tool == "picker":
            color = self.pixels[y][x]
            if color:
                self._set_color(color)
            return
        self.current_stroke = []
        if self.tool == "bucket":
            self._flood_fill(x, y, (0, 0, SIZE, SIZE))
            return
        self._grid_paint_to(x, y)

    def _grid_move(self, event):
        if not self._grid_active or self.tool in ("picker", "bucket"):
            return
        x = max(0, min(SIZE - 1, event.x // CELL))
        y = max(0, min(SIZE - 1, event.y // CELL))
        if self._grid_last:
            for u, v in _cells_line(self._grid_last[0], self._grid_last[1],
                                    x, y):
                self._grid_paint_to(u, v)
        else:
            self._grid_paint_to(x, y)
        self._grid_last = (x, y)

    def _grid_paint_to(self, x, y):
        color = None if self.tool == "eraser" else self.color
        for u, v in self._brush_cells(x, y, (0, 0, SIZE, SIZE)):
            self._apply_pixel(u, v, color)

    def _grid_up(self, _event):
        if self._grid_active:
            self._grid_active = False
            self._grid_last = None
            self._on_end()

    # ------------------------------------------------------------------ 文件
    def _load(self, path: Path) -> bool:
        try:
            image = tk.PhotoImage(file=str(path), master=self)
        except Exception:
            return False
        scale = max(1, image.width() // SIZE)
        legacy = image.height() // scale < SIZE
        width, height = image.width(), image.height()
        for y in range(SIZE):
            for x in range(SIZE):
                sx, sy = x * scale, y * scale
                try:
                    if (sx >= width or sy >= height
                            or image.transparency_get(sx, sy)):
                        self.pixels[y][x] = None
                    else:
                        value = image.get(sx, sy)
                        if isinstance(value, str):
                            self.pixels[y][x] = value
                        else:
                            r, g, b = value[:3]
                            self.pixels[y][x] = (
                                f"#{int(r):02x}{int(g):02x}{int(b):02x}")
                except Exception:
                    self.pixels[y][x] = None
        if legacy:
            self._expand_legacy()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.current_stroke = []
        self.view.set_pixels(self.pixels)
        if self.mode == "2d":
            self._draw_all()
        return True

    def _expand_legacy(self):
        for (u0, v0, tw, th), (du0, dv0) in LEGACY_COPY:
            for ty in range(th):
                for tx in range(tw):
                    self.pixels[dv0 + ty][du0 + tx] = \
                        self.pixels[v0 + ty][u0 + tx]

    def _clear(self):
        if not messagebox.askyesno("清空画布", "确定清空整个皮肤吗？",
                                   parent=self):
            return
        stroke = [(x, y, self.pixels[y][x], None)
                  for y in range(SIZE) for x in range(SIZE)
                  if self.pixels[y][x]]
        for y in range(SIZE):
            for x in range(SIZE):
                self.pixels[y][x] = None
        if stroke:
            self.undo_stack.append(stroke)
            self.redo_stack.clear()
        self.view.set_pixels(self.pixels)
        self._draw_all()
        self.status_var.set("画布已清空")

    def _write_png(self, path: Path):
        image = tk.PhotoImage(width=SIZE, height=SIZE, master=self)
        for y in range(SIZE):
            for x in range(SIZE):
                color = self.pixels[y][x]
                if color:
                    image.put(color, (x, y))
                else:
                    image.put("#000000", (x, y))
                    image.transparency_set(x, y, True)
        image.write(str(path), format="png")
        sidecar = Path(str(path) + ".json")
        if self.slim_var.get():
            try:
                sidecar.write_text('{"model": "slim"}', encoding="utf-8")
            except OSError:
                pass
        else:
            try:
                sidecar.unlink()
            except OSError:
                pass
        return image

    def _export(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="导出皮肤", defaultextension=".png",
            initialfile="skin-edited.png",
            filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")])
        if not path:
            return
        image = self._write_png(Path(path))
        self._keep = image
        self.status_var.set(f"已导出：{path}")
        messagebox.showinfo("导出完成", path, parent=self)

    def _game_dir(self) -> Path:
        instance = self.app._selected_instance()
        return Path((instance or {}).get("game_dir") or self.app.repo.root)

    @staticmethod
    def _csl_present(game_dir: Path) -> bool:
        return skins.csl_available(game_dir)

    def _apply_local_skin(self, path: Path) -> Path | None:
        account = getattr(self.app, "account", None)
        name = (getattr(account, "name", "") or "").strip()
        if not name:
            return None
        try:
            return skins.apply_csl_texture(
                self._game_dir(), name, "skin", path)
        except Exception:
            return None

    def _save_snapshot(self, prefix: str = "skin-edit") -> Path:
        folder = ensure_dir(self._game_dir() / "skins")
        name = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        path = folder / name
        self._keep = self._write_png(path)
        return path

    def _save_to_skins(self):
        path = self._save_snapshot()
        applied = None
        notes = []
        use_csl = self.csl_var.get()
        csl_ready = self._csl_present(self._game_dir()) if use_csl else False
        if use_csl:
            applied = self._apply_local_skin(path)
            if applied:
                notes.append(f"CSL 本地皮肤：\n{applied}")
                if csl_ready:
                    notes.append("重新启动游戏后生效。")
                else:
                    notes.append(
                        "未检测到 CustomSkinLoader，安装该模组后才会生效。")
            else:
                notes.append("未写入 CSL 本地皮肤：当前没有登录账户。")
        self.status_var.set(f"已保存：{path}")
        message = str(path)
        if notes:
            message += "\n\n" + "\n".join(notes)
        messagebox.showinfo("保存完成", message, parent=self)

    # ------------------------------------------------------------------ 上传
    def _upload_label(self) -> str:
        account = getattr(self.app, "account", None)
        if account is None or account.type == "offline":
            return "上传到账号（需正版/外置）"
        if account.is_microsoft:
            return "上传到微软账号"
        host = urlsplit(account.server or "").hostname or "皮肤站"
        return f"上传到 {host[:18]}"

    def _reset_upload_button(self):
        try:
            self.upload_btn.configure(state="normal",
                                     text=self._upload_label())
        except Exception:
            pass

    def _upload(self):
        if self.uploading:
            return
        account = getattr(self.app, "account", None)
        if account is None or account.type == "offline":
            messagebox.showinfo(
                "无法上传",
                "离线账户没有皮肤服务器，无法上传皮肤。\n\n"
                "可以先点「保存到实例 skins」，或登录微软正版 / "
                "外置账号后再上传。",
                parent=self)
            return
        if not account.access_token:
            messagebox.showwarning(
                "需要重新登录",
                "当前账户没有有效令牌，请到账户窗口重新登录。",
                parent=self)
            return
        if account.is_microsoft:
            where = "微软账号"
        else:
            host = urlsplit(account.server or "").hostname or "皮肤站"
            where = f"{host}（外置登录）"
        if not messagebox.askyesno(
                "上传皮肤",
                f"将当前皮肤上传到 {where} 的 {account.name}？\n\n"
                "上传后进入游戏会使用这张皮肤。",
                parent=self):
            return
        try:
            path = self._save_snapshot("skin-upload")
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self)
            return
        self.last_upload_path = path
        self.uploading = True
        self.upload_btn.configure(state="disabled", text="正在上传…")
        self.status_var.set("正在上传皮肤…")
        client_id = self.app.cfg.get("ms_client_id") or DEFAULT_CLIENT_ID
        model = "slim" if self.slim_var.get() else ""
        cache_dir = self.app.cfg.cache_dir()

        def work():
            try:
                acc = auth.ensure_account(account, client_id)
                try:
                    message = skins.upload_skin(acc, path, model=model)
                except skins.SkinAuthError:
                    if acc.is_microsoft:
                        acc = auth.refresh_account(acc, client_id)
                    else:
                        acc = auth.refresh_external(acc)
                    message = skins.upload_skin(acc, path, model=model)
                skins.store_cached_skin(acc, path, cache_dir, model=model)
            except Exception as e:
                self.upload_queue.put((None, None, str(e)))
            else:
                self.upload_queue.put((acc, message, None))

        threading.Thread(target=work, daemon=True).start()
        self.after(120, self._poll_upload)

    def _poll_upload(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            acc, message, error = self.upload_queue.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_upload)
            return
        self.uploading = False
        self._reset_upload_button()
        if error:
            self.status_var.set("皮肤上传失败")
            account = getattr(self.app, "account", None)
            host = urlsplit(account.server or "").hostname if account else ""
            if host and "littleskin" in host:
                path = getattr(self, "last_upload_path", None)
                copied = False
                if path is not None:
                    try:
                        self.clipboard_clear()
                        self.clipboard_append(str(path))
                        copied = True
                    except Exception:
                        pass
                hint = f"\n\n皮肤已保存在：\n{path}\n" if path else ""
                if copied:
                    hint += "路径已复制到剪贴板。\n"
                if messagebox.askyesno(
                        "上传失败",
                        f"{error}{hint}\n"
                        "LittleSkin 未开放启动器上传接口，"
                        "是否打开 LittleSkin 上传页面？",
                        parent=self):
                    webbrowser.open("https://littleskin.cn/skinlib/upload")
                return
            messagebox.showerror("上传失败", error, parent=self)
            return
        self.status_var.set("皮肤已上传")
        if acc is not None:
            try:
                self.app.set_account(acc)
            except Exception:
                pass
        messagebox.showinfo("上传成功", message or "皮肤已上传。", parent=self)

    def _close(self):
        self.destroy()
