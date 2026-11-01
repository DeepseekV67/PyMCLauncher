from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from core import skins
from core.util import ensure_dir
from ui.dialogs import BaseDialog
from ui.skinmodel import (
    FACES,
    FACE_SHADE,
    RIGHT_LEG_TEX,
    Projector,
    build_parts,
    face_axes,
)
from ui.theme import (
    ACCENT,
    BG,
    BORDER,
    FG,
    FONT,
    FONT_BOLD,
    FONT_SMALL,
    MUTED,
    PANEL,
    PANEL2,
    WARN,
)
from ui.widgets import flat_button


class Skin3DView(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=PANEL)
        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0,
                                width=420, height=520)
        self.canvas.pack(fill="both", expand=True)
        self.image = None
        self.path = None
        self.slim = False
        self.yaw = 0.0
        self.pitch = math.radians(12)
        self._drag = None
        self._redraw_pending = False
        self.canvas.bind("<Configure>", lambda e: self._schedule_draw())
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    def load_skin(self, path, slim: bool = False) -> bool:
        try:
            image = tk.PhotoImage(file=str(path), master=self.canvas)
        except Exception:
            return False
        self.image = image
        self.path = Path(path)
        self.slim = bool(slim)
        self.draw()
        return True

    def clear(self):
        self.image = None
        self.path = None
        self.slim = False
        self.draw()

    def _drag_start(self, event):
        self._drag = (event.x, event.y)

    def _drag_move(self, event):
        if not self._drag:
            return
        dx = event.x - self._drag[0]
        dy = event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.yaw -= dx * 0.012
        self.pitch = max(math.radians(-55),
                         min(math.radians(55), self.pitch + dy * 0.01))
        self._schedule_draw()

    def _schedule_draw(self):
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.after_idle(self._draw_now)

    def _draw_now(self):
        self._redraw_pending = False
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.draw()

    def _pixel(self, u, v):
        if self.image is None:
            return None
        try:
            if self.image.transparency_get(u, v):
                return None
            value = self.image.get(u, v)
        except Exception:
            return None
        if isinstance(value, str):
            return value
        try:
            r, g, b = value[:3]
            return f"#{int(r):02x}{int(g):02x}{int(b):02x}"
        except Exception:
            return None

    @staticmethod
    def _shade(color: str, factor: float) -> str:
        if not color.startswith("#") or len(color) != 7:
            return color
        try:
            r = min(255, max(0, int(int(color[1:3], 16) * factor)))
            g = min(255, max(0, int(int(color[3:5], 16) * factor)))
            b = min(255, max(0, int(int(color[5:7], 16) * factor)))
        except ValueError:
            return color
        return f"#{r:02x}{g:02x}{b:02x}"

    def draw(self):
        self.canvas.delete("all")
        if self.image is None:
            self.canvas.create_text(
                12, 12, anchor="nw", fill=MUTED, font=FONT,
                text="未找到可显示的皮肤。\n可先登录正版/外置账户，\n或在 skins 文件夹放置皮肤 PNG。")
            return
        width = max(200, self.canvas.winfo_width())
        height = max(240, self.canvas.winfo_height())
        scale = min(width / 26.0, height / 44.0)
        cx = width / 2.0
        cy = height / 2.0
        tex_scale = max(1, self.image.width() // 64)
        tex_h_units = max(1, self.image.height() // tex_scale)
        legacy = tex_h_units < 64
        proj = Projector(self.yaw, self.pitch, cx, cy, scale)
        draw_faces = []
        for part in build_parts(self.slim, legacy):
            for name, (normal, order) in FACES.items():
                if not proj.visible(normal):
                    continue
                rect = part["tex"][name]
                if legacy and rect[1] + rect[3] > tex_h_units:
                    rect = RIGHT_LEG_TEX[name]
                origin, du, dv = face_axes(part["bounds"], name)
                p = proj.point(*origin)
                q = proj.point(origin[0] + du[0] + dv[0],
                               origin[1] + du[1] + dv[1],
                               origin[2] + du[2] + dv[2])
                draw_faces.append(((p[2] + q[2]) / 2.0, p, q,
                                   origin, du, dv, rect, order, name,
                                   part["overlay"]))
        draw_faces.sort(key=lambda item: (item[0], item[7]))
        tex_w, tex_h = self.image.width(), self.image.height()
        for (_depth, _p, _q, origin, du, dv, rect, _order,
             name, overlay) in draw_faces:
            u0, v0, tw, th = rect
            shade = FACE_SHADE.get(name, 1.0)
            for ty in range(th):
                for tx in range(tw):
                    u = (u0 + tx) * tex_scale
                    v = (v0 + ty) * tex_scale
                    if u < 0 or v < 0 or u >= tex_w or v >= tex_h:
                        color = None
                    else:
                        color = self._pixel(u, v)
                    if not color:
                        continue
                    color = self._shade(color, shade)
                    points = []
                    for ox, oy in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        uf = (tx + ox) / max(1, tw)
                        vf = (ty + oy) / max(1, th)
                        px = origin[0] + du[0] * uf + dv[0] * vf
                        py = origin[1] + du[1] * uf + dv[1] * vf
                        pz = origin[2] + du[2] * uf + dv[2] * vf
                        sx, sy, _sz = proj.point(px, py, pz)
                        points.extend((sx, sy))
                    self.canvas.create_polygon(
                        points, fill=color, outline=color, width=1)
            if not overlay:
                outline = []
                for ox, oy in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    px = origin[0] + du[0] * ox + dv[0] * oy
                    py = origin[1] + du[1] * ox + dv[1] * oy
                    pz = origin[2] + du[2] * ox + dv[2] * oy
                    sx, sy, _sz = proj.point(px, py, pz)
                    outline.extend((sx, sy))
                self.canvas.create_polygon(
                    outline, fill="", outline="#5b6b7f", width=1)


class SkinViewerDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "玩家皮肤", 540, 540)
        self.resizable(True, True)
        self.msg_queue: queue.Queue = queue.Queue()
        self.finished = False
        self.loading = False
        instance = app._selected_instance()
        self.game_dir = Path((instance or {}).get("game_dir") or app.repo.root)

        pad = tk.Frame(self, bg=BG)
        pad.pack(fill="both", expand=True, padx=16, pady=14)
        top = tk.Frame(pad, bg=BG)
        top.pack(fill="x", pady=(0, 6))
        tk.Label(top, text="当前玩家皮肤", bg=BG, fg=FG,
                 font=("Courier New", 15, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="正在获取皮肤…")
        tk.Label(top, textvariable=self.status_var, bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(side="right")

        hint_row = tk.Frame(pad, bg=BG)
        hint_row.pack(fill="x", pady=(0, 4))
        tk.Label(hint_row, text="按住鼠标拖动可旋转查看", bg=BG, fg=MUTED,
                 font=FONT_SMALL).pack(side="left")
        flat_button(hint_row, "本地披风/鞘翅", self._local_textures,
                    padx=8, pady=3).pack(side="right")

        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x", side="bottom")
        flat_button(row, "刷新皮肤", self._refresh, accent=True).pack(side="left")
        flat_button(row, "选择本地 PNG", self._choose_file).pack(side="left", padx=8)
        flat_button(row, "打开皮肤文件夹", self._open_folder).pack(side="left")
        flat_button(row, "编辑皮肤", self._edit, padx=8).pack(
            side="left", padx=(8, 0))
        flat_button(row, "关闭", self._close).pack(side="right")

        self.view = Skin3DView(pad)
        self.view.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self._refresh()
        self.after(100, self._poll)

    def _refresh(self):
        if self.loading:
            return
        self.loading = True
        self.status_var.set("正在获取当前皮肤…")
        account = self.app.account
        cache_dir = self.app.cfg.cache_dir()
        game_dir = self.game_dir

        def work():
            return skins.current_skin(account, game_dir, cache_dir, refresh=True)

        def done(path):
            self.loading = False
            if self.finished:
                return
            if path and self.view.load_skin(
                    path, slim=skins.skin_model(path) == "slim"):
                self.status_var.set(f"已加载：{Path(path).name}")
            else:
                self.view.clear()
                self.status_var.set("未找到皮肤，可下载皮肤或放入 skins 文件夹")

        def worker():
            try:
                path = work()
            except Exception:
                path = None
            self.msg_queue.put((done, path))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        if self.finished:
            return
        try:
            while True:
                done, path = self.msg_queue.get_nowait()
                done(path)
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(100, self._poll)

    def _choose_file(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择皮肤 PNG",
            filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")])
        if not path:
            return
        if self.view.load_skin(
                path, slim=skins.skin_model(path) == "slim"):
            self.status_var.set(f"本地预览：{Path(path).name}")
        else:
            self.status_var.set("无法读取该 PNG 文件")

    def _edit(self):
        path = self.view.path
        self._close()
        from ui.skinedit import SkinEditorDialog
        SkinEditorDialog(self.app, path)

    def _local_textures(self):
        from ui.localskin import LocalTextureDialog
        LocalTextureDialog(self.app)

    def _open_folder(self):
        self.app._open_path(ensure_dir(self.game_dir / "skins"))

    def _close(self):
        self.finished = True
        self.destroy()
