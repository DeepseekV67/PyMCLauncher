"""把运行时目录打包成 C# 引导程序使用的 payload.zip。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {"config.json", "launcher.log"}


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: pack_payload.py <runtime-dir> <payload.zip>")
        return 2
    root = Path(sys.argv[1])
    out = Path(sys.argv[2])
    if not root.is_dir():
        print(f"runtime dir not found: {root}")
        return 1
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts[:-1]):
                continue
            if path.name in SKIP_FILES or path.suffix in (".pyc", ".pyo"):
                continue
            z.write(path, rel.as_posix())
            count += 1
    print(f"payload: {count} files, {out.stat().st_size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
