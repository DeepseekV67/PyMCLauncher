"""可在 3D 模型上直接绘制的皮肤画布。

渲染方式与查看器一致：逐 texel 多边形贴图。
区别是这里会记录每个贴图像素对应的多边形，从而把鼠标位置反查成
皮肤贴图中的 (u, v)，实现在立体模型上落笔。
"""
from __future__ import annotations

import math
import tkinter as tk
from typing import Callable, NamedTuple

from ui.skinmodel import (
    FACES,
    FACE_SHADE,
    Projector,
    build_parts,
    face_axes,
)
from ui.theme import PANEL

STAGE_BG = "#f3f6fb"


class PaintHit(NamedTuple):
    u: int
    v: int
    part: str
    rect: tuple  # (u0, v0, tw, th)
    overlay: bool


def shade_color(color: str, factor: float) -> str:
    if not color.startswith("#") or len(color) != 7:
        return color
    try:
        r = min(255, max(0, int(int(color[1:3], 16) * factor)))
        g = min(255, max(0, int(int(color[3:5], 16) * factor)))
        b = min(255, max(0, int(int(color[5:7], 16) * factor)))
    except ValueError:
        return color
    return f"#{r:02x}{g:02x}{b:02x}"


def point_in_poly(x: float, y: float, pts: list[float]) -> bool:
    inside = False
    count = len(pts) // 2
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = pts[2 * i], pts[2 * i + 1]
        xj, yj = pts[2 * j], pts[2 * j + 1]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _bbox(pts: list[float]):
    xs = pts[0::2]
    ys = pts[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


class SkinPaintView(tk.Frame):
    """带旋转、缩放和命中检测的 3D 皮肤画布。"""

    def __init__(self, parent, pixels, on_paint=None, on_end=None,
                 on_hover=None):
        super().__init__(parent, bg=PANEL)
        self.canvas = tk.Canvas(self, bg=STAGE_BG, highlightthickness=0,
                                width=460, height=470, takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self.pixels = pixels
        self.on_paint: Callable | None = on_paint
        self.on_end: Callable | None = on_end
        self.on_hover: Callable | None = on_hover
        self.slim = False
        self.yaw = 0.0
        self.pitch = math.radians(10)
        self.zoom = 1.0
        self.rotate_mode = False
        self.layer_mode = "auto"
        self.items_by_uv: dict[tuple[int, int], list[tuple[int, float]]] = {}
        self.texel_geom: dict[tuple[int, int], list[tuple]] = {}
        self.quads: list[tuple] = []
        self.quad_index: dict[tuple[int, int], int] = {}
        self._shade_cache: dict[tuple[str, float], str] = {}
        self._last_quad_points = None
        self._proj = None
        self._rotate_anchor = None
        self._rotate_button = None
        self._painting = False
        self._prev_hit = None
        self._redraw_pending = False
        self._hover_key = None
        self._bind_events()

    # ------------------------------------------------------------------ 绑定
    def _bind_events(self):
        c = self.canvas
        c.bind("<Configure>", lambda e: self.schedule_draw())
        c.bind("<Button-1>", self._left_down)
        c.bind("<B1-Motion>", self._left_move)
        c.bind("<ButtonRelease-1>", self._left_up)
        c.bind("<Button-3>", self._right_down)
        c.bind("<B3-Motion>", self._right_move)
        c.bind("<ButtonRelease-3>", self._right_up)
        c.bind("<Button-2>", self._middle_down)
        c.bind("<B2-Motion>", self._middle_move)
        c.bind("<ButtonRelease-2>", self._middle_up)
        c.bind("<Motion>", self._hover)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Button-4>", lambda e: self._zoom_by(1.1))
        c.bind("<Button-5>", lambda e: self._zoom_by(1 / 1.1))
        c.bind("<Left>", lambda e: self.rotate_by(0.12))
        c.bind("<Right>", lambda e: self.rotate_by(-0.12))
        c.bind("<Up>", lambda e: self.rotate_by(0, -0.08))
        c.bind("<Down>", lambda e: self.rotate_by(0, 0.08))
        c.bind("<Leave>", self._leave)

    # ------------------------------------------------------------------ 配置
    def set_pixels(self, pixels):
        self.pixels = pixels
        self.schedule_draw()

    def set_slim(self, slim: bool):
        if self.slim != bool(slim):
            self.slim = bool(slim)
            self.schedule_draw()

    def set_prefer_overlay(self, prefer: bool):
        self.set_layer_mode("outer" if prefer else "inner")

    def set_layer_mode(self, mode: str):
        if mode in ("auto", "outer", "inner"):
            self.layer_mode = mode

    def set_rotate_mode(self, rotate: bool):
        self.rotate_mode = bool(rotate)
        try:
            self.canvas.configure(
                cursor="fleur" if self.rotate_mode else "")
        except Exception:
            pass
        if self.rotate_mode:
            self.canvas.delete("hover")

    def reset_view(self):
        self.yaw = 0.0
        self.pitch = math.radians(10)
        self.zoom = 1.0
        self.schedule_draw()

    def _pixel(self, u: int, v: int):
        try:
            return self.pixels[v][u]
        except (IndexError, TypeError):
            return None

    # ------------------------------------------------------------------ 绘制
    def schedule_draw(self):
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

    def _quad(self, geom, proj):
        origin, du, dv, tw, th, tx, ty = geom[:7]
        points = []
        for ox, oy in ((0, 0), (1, 0), (1, 1), (0, 1)):
            uf = (tx + ox) / max(1, tw)
            vf = (ty + oy) / max(1, th)
            px = origin[0] + du[0] * uf + dv[0] * vf
            py = origin[1] + du[1] * uf + dv[1] * vf
            pz = origin[2] + du[2] * uf + dv[2] * vf
            sx, sy, _sz = proj.point(px, py, pz)
            points.extend((sx, sy))
        return points

    def _add_item(self, u, v, geom):
        proj = self._proj
        if proj is None:
            return
        color = self._pixel(u, v)
        if not color:
            return
        (_origin, _du, _dv, _tw, _th, _tx, _ty, shade,
         _part, _rect, _overlay) = geom
        points = self._quad(geom, proj)
        fill = self._cached_shade(color, shade)
        item = self.canvas.create_polygon(
            points, fill=fill, outline=fill, width=1)
        self.items_by_uv.setdefault((u, v), []).append((item, shade))
        self._restack(item, u, v)

    def _cached_shade(self, color: str, shade: float) -> str:
        key = (color, shade)
        cached = self._shade_cache.get(key)
        if cached is None:
            cached = shade_color(color, shade)
            self._shade_cache[key] = cached
        return cached

    def _restack(self, item, u, v):
        """把后建的多边形放回正确图层顺序，避免盖住外层。"""
        index = self.quad_index.get((u, v))
        if index is None:
            return
        below = None
        for j in range(index + 1, len(self.quads)):
            other = self.quads[j][2]
            if other.u == u and other.v == v:
                continue
            entries = self.items_by_uv.get((other.u, other.v))
            if entries:
                below = entries[0][0]
                break
        if below is not None:
            try:
                self.canvas.tag_lower(item, below)
            except Exception:
                pass

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        self.items_by_uv.clear()
        self.texel_geom.clear()
        self.quads.clear()
        self.quad_index.clear()
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 40 or height < 40:
            width, height = 460, 470
        scale = min(width / 30.0, height / 44.0) * self.zoom
        cx = width / 2.0
        cy = height / 2.0 + 4
        proj = Projector(self.yaw, self.pitch, cx, cy, scale)
        self._proj = proj
        faces = []
        for part in build_parts(self.slim, legacy=False):
            for name, (normal, order) in FACES.items():
                if not proj.visible(normal):
                    continue
                origin, du, dv = face_axes(part["bounds"], name)
                p = proj.point(*origin)
                q = proj.point(origin[0] + du[0] + dv[0],
                               origin[1] + du[1] + dv[1],
                               origin[2] + du[2] + dv[2])
                faces.append(((p[2] + q[2]) / 2.0, order, origin, du, dv,
                              part, name))
        faces.sort(key=lambda item: (item[0], item[1]))
        for _depth, _order, origin, du, dv, part, name in faces:
            u0, v0, tw, th = part["tex"][name]
            shade = FACE_SHADE.get(name, 1.0)
            s00 = proj.point(*origin)
            s10 = proj.point(origin[0] + du[0], origin[1] + du[1],
                             origin[2] + du[2])
            s01 = proj.point(origin[0] + dv[0], origin[1] + dv[1],
                             origin[2] + dv[2])
            ax = s10[0] - s00[0]
            ay = s10[1] - s00[1]
            bx = s01[0] - s00[0]
            by = s01[1] - s00[1]
            for ty in range(th):
                vf0 = ty / th
                vf1 = (ty + 1) / th
                for tx in range(tw):
                    u, v = u0 + tx, v0 + ty
                    if not (0 <= u < 64 and 0 <= v < 64):
                        continue
                    geom = (origin, du, dv, tw, th, tx, ty, shade,
                            part["name"], (u0, v0, tw, th),
                            part["overlay"])
                    self.texel_geom.setdefault((u, v), []).append(geom)
                    uf0 = tx / tw
                    uf1 = (tx + 1) / tw
                    x00 = s00[0] + ax * uf0 + bx * vf0
                    y00 = s00[1] + ay * uf0 + by * vf0
                    x10 = s00[0] + ax * uf1 + bx * vf0
                    y10 = s00[1] + ay * uf1 + by * vf0
                    x11 = s00[0] + ax * uf1 + bx * vf1
                    y11 = s00[1] + ay * uf1 + by * vf1
                    x01 = s00[0] + ax * uf0 + bx * vf1
                    y01 = s00[1] + ay * uf0 + by * vf1
                    points = [x00, y00, x10, y10, x11, y11, x01, y01]
                    hit = PaintHit(u, v, part["name"], (u0, v0, tw, th),
                                   part["overlay"])
                    self.quads.append((points, _bbox(points), hit))
                    self.quad_index.setdefault((u, v), len(self.quads) - 1)
                    color = self._pixel(u, v)
                    if color:
                        fill = self._cached_shade(color, shade)
                        item = canvas.create_polygon(
                            points, fill=fill, outline=fill, width=1)
                        self.items_by_uv.setdefault(
                            (u, v), []).append((item, shade))
        self._hover_key = None

    def refresh_texel(self, u: int, v: int):
        color = self._pixel(u, v)
        entries = self.items_by_uv.get((u, v))
        if entries:
            for item, shade in entries:
                if color:
                    fill = self._cached_shade(color, shade)
                    self.canvas.itemconfigure(item, fill=fill, outline=fill)
                else:
                    self.canvas.itemconfigure(item, fill="", outline="")
            if not color:
                self.items_by_uv.pop((u, v), None)
            return
        if color:
            for geom in self.texel_geom.get((u, v), []):
                self._add_item(u, v, geom)

    # ------------------------------------------------------------------ 命中
    def hit_test(self, x: int, y: int) -> PaintHit | None:
        self._last_quad_points = None
        mode = self.layer_mode
        if mode == "auto":
            fallback = None
            for points, bbox, hit in reversed(self.quads):
                if (x < bbox[0] or x > bbox[2]
                        or y < bbox[1] or y > bbox[3]):
                    continue
                if not point_in_poly(x, y, points):
                    continue
                if self._pixel(hit.u, hit.v):
                    self._last_quad_points = points
                    return hit
                if fallback is None or (fallback.overlay and not hit.overlay):
                    fallback = hit
                    self._last_quad_points = points
            return fallback
        want_overlay = mode == "outer"
        for points, bbox, hit in reversed(self.quads):
            if hit.overlay != want_overlay:
                continue
            if (x < bbox[0] or x > bbox[2]
                    or y < bbox[1] or y > bbox[3]):
                continue
            if point_in_poly(x, y, points):
                self._last_quad_points = points
                return hit
        return None

    # ------------------------------------------------------------------ 交互
    def _left_down(self, event):
        self.canvas.focus_set()
        self.canvas.delete("hover")
        if self.rotate_mode:
            self._begin_rotate(event, "left")
            return
        if self._rotate_button in ("right", "middle"):
            return
        hit = self.hit_test(event.x, event.y)
        if hit is None:
            return
        self._painting = True
        self._prev_hit = hit
        if self.on_paint:
            self.on_paint(hit, True, None)

    def _left_move(self, event):
        if self._rotate_button == "left":
            self._rotate(event)
            return
        if self._rotate_button in ("right", "middle"):
            return
        if not self._painting:
            return
        hit = self.hit_test(event.x, event.y)
        if hit is None:
            return
        previous = self._prev_hit
        self._prev_hit = hit
        if self.on_paint:
            self.on_paint(hit, False, previous)

    def _left_up(self, _event):
        if self._rotate_button == "left":
            self._end_rotate()
        if self._painting:
            self._painting = False
            self._prev_hit = None
            if self.on_end:
                self.on_end()

    def _right_down(self, event):
        self._begin_rotate(event, "right")

    def _right_move(self, event):
        if self._rotate_button == "right":
            self._rotate(event)

    def _right_up(self, _event):
        if self._rotate_button == "right":
            self._end_rotate()

    def _middle_down(self, event):
        self._begin_rotate(event, "middle")

    def _middle_move(self, event):
        if self._rotate_button == "middle":
            self._rotate(event)

    def _middle_up(self, _event):
        if self._rotate_button == "middle":
            self._end_rotate()

    def _begin_rotate(self, event, button: str):
        if self._rotate_anchor is None:
            self._rotate_anchor = (event.x, event.y)
            self._rotate_button = button
        self.canvas.delete("hover")

    def _end_rotate(self):
        self._rotate_anchor = None
        self._rotate_button = None
        self.canvas.delete("hover")
        # 视角变化后不要从上一次落笔位置连线
        self._prev_hit = None

    def _rotate(self, event):
        if self._rotate_anchor is None:
            return
        dx = event.x - self._rotate_anchor[0]
        dy = event.y - self._rotate_anchor[1]
        self._rotate_anchor = (event.x, event.y)
        self.yaw -= dx * 0.012
        self.pitch = max(math.radians(-58),
                         min(math.radians(58),
                             self.pitch + dy * 0.01))
        self._prev_hit = None
        self.schedule_draw()

    def rotate_by(self, dyaw: float, dpitch: float = 0.0):
        self.yaw += dyaw
        self.pitch = max(math.radians(-58),
                         min(math.radians(58), self.pitch + dpitch))
        self._prev_hit = None
        self.schedule_draw()

    def _wheel(self, event):
        delta = event.delta or 0
        if delta == 0:
            return
        self._zoom_by(1.1 ** (delta / 120.0))

    def _zoom_by(self, factor: float):
        self.zoom = max(0.55, min(2.2, self.zoom * factor))
        self.schedule_draw()

    def _hover(self, event):
        if self.rotate_mode:
            if self._hover_key is not None:
                self._hover_key = None
                self.canvas.delete("hover")
            return
        if self._painting or self._rotate_anchor is not None:
            return
        hit = self.hit_test(event.x, event.y)
        key = (hit.u, hit.v, hit.part) if hit else None
        if key == self._hover_key:
            return
        self._hover_key = key
        self._draw_hover()
        if self.on_hover:
            self.on_hover(hit)

    def _draw_hover(self):
        self.canvas.delete("hover")
        if self._last_quad_points:
            self.canvas.create_polygon(
                self._last_quad_points, fill="", outline="#0f766e",
                width=2, dash=(4, 2), tags="hover")

    def _leave(self, _event):
        self._hover_key = None
        self._last_quad_points = None
        self.canvas.delete("hover")
        if self.on_hover:
            self.on_hover(None)
