"""Minecraft 玩家模型几何与皮肤贴图布局（查看器 / 编辑器共用）。"""
from __future__ import annotations

import math

# 每张面的贴图区域：(u, v, 宽, 高)，单位为 64x64 皮肤中的像素
HEAD_TEX = {
    "front": (8, 8, 8, 8), "back": (24, 8, 8, 8),
    "right": (0, 8, 8, 8), "left": (16, 8, 8, 8),
    "top": (8, 0, 8, 8), "bottom": (16, 0, 8, 8),
}
BODY_TEX = {
    "front": (20, 20, 8, 12), "back": (32, 20, 8, 12),
    "right": (16, 20, 4, 12), "left": (28, 20, 4, 12),
    "top": (20, 16, 8, 4), "bottom": (28, 16, 8, 4),
}
RIGHT_LEG_TEX = {
    "front": (4, 20, 4, 12), "back": (12, 20, 4, 12),
    "right": (0, 20, 4, 12), "left": (8, 20, 4, 12),
    "top": (4, 16, 4, 4), "bottom": (8, 16, 4, 4),
}
LEFT_LEG_TEX = {
    "front": (20, 52, 4, 12), "back": (28, 52, 4, 12),
    "right": (16, 52, 4, 12), "left": (24, 52, 4, 12),
    "top": (20, 48, 4, 4), "bottom": (24, 48, 4, 4),
}
RIGHT_ARM_TEX = {
    "front": (44, 20, 4, 12), "back": (52, 20, 4, 12),
    "right": (40, 20, 4, 12), "left": (48, 20, 4, 12),
    "top": (44, 16, 4, 4), "bottom": (48, 16, 4, 4),
}
LEFT_ARM_TEX = {
    "front": (36, 52, 4, 12), "back": (44, 52, 4, 12),
    "right": (32, 52, 4, 12), "left": (40, 52, 4, 12),
    "top": (36, 48, 4, 4), "bottom": (40, 48, 4, 4),
}
SLIM_RIGHT_ARM_TEX = {
    "front": (44, 20, 3, 12), "back": (51, 20, 3, 12),
    "right": (43, 20, 4, 12), "left": (47, 20, 4, 12),
    "top": (44, 16, 3, 4), "bottom": (47, 16, 3, 4),
}
SLIM_LEFT_ARM_TEX = {
    "front": (36, 52, 3, 12), "back": (43, 52, 3, 12),
    "right": (35, 52, 4, 12), "left": (39, 52, 4, 12),
    "top": (36, 48, 3, 4), "bottom": (39, 48, 3, 4),
}
HAT_TEX = {
    "front": (40, 8, 8, 8), "back": (56, 8, 8, 8),
    "right": (32, 8, 8, 8), "left": (48, 8, 8, 8),
    "top": (40, 0, 8, 8), "bottom": (48, 0, 8, 8),
}
JACKET_TEX = {
    "front": (20, 36, 8, 12), "back": (32, 36, 8, 12),
    "right": (16, 36, 4, 12), "left": (28, 36, 4, 12),
    "top": (20, 32, 8, 4), "bottom": (28, 32, 8, 4),
}
RIGHT_SLEEVE_TEX = {
    "front": (44, 36, 4, 12), "back": (52, 36, 4, 12),
    "right": (40, 36, 4, 12), "left": (48, 36, 4, 12),
    "top": (44, 32, 4, 4), "bottom": (48, 32, 4, 4),
}
LEFT_SLEEVE_TEX = {
    "front": (36, 68, 4, 12), "back": (44, 68, 4, 12),
    "right": (32, 68, 4, 12), "left": (40, 68, 4, 12),
    "top": (36, 64, 4, 4), "bottom": (40, 64, 4, 4),
}
RIGHT_PANT_TEX = {
    "front": (4, 36, 4, 12), "back": (12, 36, 4, 12),
    "right": (0, 36, 4, 12), "left": (8, 36, 4, 12),
    "top": (4, 32, 4, 4), "bottom": (8, 32, 4, 4),
}
LEFT_PANT_TEX = {
    "front": (20, 68, 4, 12), "back": (28, 68, 4, 12),
    "right": (16, 68, 4, 12), "left": (24, 68, 4, 12),
    "top": (20, 64, 4, 4), "bottom": (24, 64, 4, 4),
}
SLIM_RIGHT_SLEEVE_TEX = {
    "front": (44, 36, 3, 12), "back": (51, 36, 3, 12),
    "right": (43, 36, 4, 12), "left": (47, 36, 4, 12),
    "top": (44, 32, 3, 4), "bottom": (47, 32, 3, 4),
}
SLIM_LEFT_SLEEVE_TEX = {
    "front": (36, 68, 3, 12), "back": (43, 68, 3, 12),
    "right": (35, 68, 4, 12), "left": (39, 68, 4, 12),
    "top": (36, 64, 3, 4), "bottom": (39, 64, 3, 4),
}

FACES = {
    "front": ((0, 0, 1), 0),
    "back": ((0, 0, -1), 1),
    "right": ((1, 0, 0), 2),
    "left": ((-1, 0, 0), 3),
    "top": ((0, 1, 0), 4),
    "bottom": ((0, -1, 0), 5),
}
FACE_SHADE = {
    "front": 1.0, "back": 0.86, "right": 0.80,
    "left": 0.68, "top": 1.08, "bottom": 0.52,
}


def build_parts(slim: bool = False, legacy: bool = False) -> list[dict]:
    """返回模型各部件：bounds、六个面的贴图区域、是否为第二层。"""
    arm = 3 if (slim and not legacy) else 4
    right_arm_tex = (SLIM_RIGHT_ARM_TEX if slim and not legacy
                     else RIGHT_ARM_TEX)
    left_arm_tex = (SLIM_LEFT_ARM_TEX if slim and not legacy
                    else (LEFT_ARM_TEX if not legacy else RIGHT_ARM_TEX))
    right_sleeve_tex = (SLIM_RIGHT_SLEEVE_TEX if slim and not legacy
                        else RIGHT_SLEEVE_TEX)
    left_sleeve_tex = (SLIM_LEFT_SLEEVE_TEX if slim and not legacy
                       else LEFT_SLEEVE_TEX)
    head_b = (-4, 24, -4, 4, 32, 4)
    body_b = (-4, 12, -2, 4, 24, 2)
    right_leg_b = (-4, 0, -2, 0, 12, 2)
    left_leg_b = (0, 0, -2, 4, 12, 2)
    right_arm_b = (-4 - arm, 12, -2, -4, 24, 2)
    left_arm_b = (4, 12, -2, 4 + arm, 24, 2)
    parts = [
        {"name": "head", "bounds": head_b, "tex": HEAD_TEX, "overlay": False},
        {"name": "body", "bounds": body_b, "tex": BODY_TEX, "overlay": False},
        {"name": "right_leg", "bounds": right_leg_b, "tex": RIGHT_LEG_TEX,
         "overlay": False},
        {"name": "left_leg", "bounds": left_leg_b,
         "tex": LEFT_LEG_TEX if not legacy else RIGHT_LEG_TEX,
         "overlay": False},
        {"name": "right_arm", "bounds": right_arm_b, "tex": right_arm_tex,
         "overlay": False},
        {"name": "left_arm", "bounds": left_arm_b, "tex": left_arm_tex,
         "overlay": False},
    ]
    if not legacy:
        parts += [
            {"name": "hat", "bounds": grow(head_b), "tex": HAT_TEX,
             "overlay": True},
            {"name": "jacket", "bounds": grow(body_b), "tex": JACKET_TEX,
             "overlay": True},
            {"name": "right_pant", "bounds": grow(right_leg_b),
             "tex": RIGHT_PANT_TEX, "overlay": True},
            {"name": "left_pant", "bounds": grow(left_leg_b),
             "tex": LEFT_PANT_TEX, "overlay": True},
            {"name": "right_sleeve", "bounds": grow(right_arm_b),
             "tex": right_sleeve_tex, "overlay": True},
            {"name": "left_sleeve", "bounds": grow(left_arm_b),
             "tex": left_sleeve_tex, "overlay": True},
        ]
    return parts


def grow(bounds, amount: float = 0.3):
    x0, y0, z0, x1, y1, z1 = bounds
    return (x0 - amount, y0 - amount, z0 - amount,
            x1 + amount, y1 + amount, z1 + amount)


def face_axes(bounds, name):
    """返回面原点与两条边向量：origin + du*u + dv*v，u/v 为 0..1。"""
    x0, y0, z0, x1, y1, z1 = bounds
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    if name == "front":
        return (x0, y1, z1), (dx, 0, 0), (0, -dy, 0)
    if name == "back":
        return (x1, y1, z0), (-dx, 0, 0), (0, -dy, 0)
    if name == "right":
        return (x1, y1, z1), (0, 0, -dz), (0, -dy, 0)
    if name == "left":
        return (x0, y1, z0), (0, 0, dz), (0, -dy, 0)
    if name == "top":
        return (x0, y1, z0), (dx, 0, 0), (0, 0, dz)
    return (x0, y0, z1), (dx, 0, 0), (0, 0, -dz)


class Projector:
    """把模型坐标投影到画布坐标，并做背面剔除。"""

    def __init__(self, yaw: float, pitch: float, cx: float, cy: float,
                 scale: float):
        self.cy = math.cos(yaw)
        self.sy = math.sin(yaw)
        self.cp = math.cos(pitch)
        self.sp = math.sin(pitch)
        self.cx = cx
        self.cy0 = cy
        self.scale = scale

    def point(self, x: float, y: float, z: float):
        x1 = x * self.cy - z * self.sy
        z1 = x * self.sy + z * self.cy
        y0 = y - 16.0
        y1 = y0 * self.cp - z1 * self.sp
        z2 = y0 * self.sp + z1 * self.cp
        return (self.cx + x1 * self.scale,
                self.cy0 - y1 * self.scale, z2)

    def visible(self, normal) -> bool:
        nx, ny, nz = normal
        nx1 = nx * self.cy - nz * self.sy
        nz1 = nx * self.sy + nz * self.cy
        ny1 = ny * self.cp - nz1 * self.sp
        nz2 = ny * self.sp + nz1 * self.cp
        return nz2 > 0.001
