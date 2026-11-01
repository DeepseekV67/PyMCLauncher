"""把本机 Python 的 Lib 打包成嵌入式运行时使用的 pythonXY.zip。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

EXCLUDE_DIRS = {
    "site-packages", "test", "tests", "idlelib", "ensurepip", "venv",
    "lib2to3", "turtledemo", "pydoc_data", "__pycache__",
}


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: pack_stdlib.py <Lib> <out.zip>")
        return 2
    lib = Path(sys.argv[1])
    out = Path(sys.argv[2])
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in lib.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(lib)
            if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            z.write(path, rel.as_posix())
            count += 1
    print(f"stdlib: {count} files, {out.stat().st_size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
