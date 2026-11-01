from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .util import (
    ARCH,
    IS_WINDOWS,
    DownloadItem,
    LauncherError,
    Progress,
    download_many,
    ensure_dir,
    http_json,
    read_json,
    write_json,
)

RUNTIME_ALL_URL = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                   "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
APP_RUNTIME_DIR = Path(__file__).resolve().parent.parent / "runtime"
CACHE_FILE = Path(__file__).resolve().parent.parent / "cache" / "java_runtime_all.json"

COMPONENTS = [
    ("jre-legacy", "Java 8（旧版本）"),
    ("java-runtime-alpha", "Java 16"),
    ("java-runtime-beta", "Java 17（早期）"),
    ("java-runtime-gamma", "Java 17"),
    ("java-runtime-delta", "Java 21"),
    ("java-runtime-epsilon", "Java 25"),
]


@dataclass
class JavaInfo:
    path: Path
    version: str
    major: int
    vendor: str = ""

    def label(self) -> str:
        return f"Java {self.major}  ·  {self.version}  ·  {self.path}"


_CACHE: list[JavaInfo] = []
_CACHE_TIME = 0.0


def _major_of(version: str) -> int:
    m = re.search(r"(\d+)(?:\.(\d+))?", version or "")
    if not m:
        return 0
    major = int(m.group(1))
    if major == 1 and m.group(2):
        major = int(m.group(2))
    return major


def _read_release(java_path: Path) -> tuple[str, str]:
    release = java_path.parent.parent / "release"
    version = vendor = ""
    try:
        for line in release.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("JAVA_VERSION="):
                version = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("IMPLEMENTOR="):
                vendor = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return version, vendor


def inspect_java(java_path) -> JavaInfo | None:
    path = Path(java_path)
    if not path.is_file():
        return None
    version, vendor = _read_release(path)
    if not version:
        flags = 0
        if IS_WINDOWS:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run([str(path), "-version"], capture_output=True, timeout=15,
                                  creationflags=flags)
            text = (proc.stderr or b"").decode("utf-8", "replace")
            m = re.search(r'version "([^"]+)"', text)
            if m:
                version = m.group(1)
            if not vendor:
                first = text.splitlines()[0] if text.splitlines() else ""
                vendor = first.strip()
        except Exception:
            return None
    if not version:
        return None
    return JavaInfo(path=path, version=version, major=_major_of(version), vendor=vendor)


def _candidate_paths(root=None, extra=None) -> list[Path]:
    exe = "java.exe" if IS_WINDOWS else "java"
    candidates: list[Path] = []
    for item in extra or []:
        if item:
            candidates.append(Path(item))
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(Path(java_home) / "bin" / exe)
    which = shutil.which("java")
    if which:
        candidates.append(Path(which))
    if IS_WINDOWS:
        patterns = [
            r"C:\Program Files\Java\*\bin\java.exe",
            r"C:\Program Files\Eclipse Adoptium\*\bin\java.exe",
            r"C:\Program Files\Microsoft\jdk-*\bin\java.exe",
            r"C:\Program Files\Zulu\*\bin\java.exe",
            r"C:\Program Files\BellSoft\*\bin\java.exe",
            r"C:\Program Files\Amazon Corretto\*\bin\java.exe",
            r"C:\Program Files\Semeru\*\bin\java.exe",
            r"C:\Program Files (x86)\Java\*\bin\java.exe",
            r"D:\Program Files\Java\*\bin\java.exe",
            r"D:\Program Files\Eclipse Adoptium\*\bin\java.exe",
            r"D:\Program Files\Microsoft\jdk-*\bin\java.exe",
        ]
        import glob
        for pattern in patterns:
            try:
                candidates.extend(Path(p) for p in glob.glob(pattern))
            except Exception:
                pass
    if root:
        try:
            candidates.extend(Path(root).glob("runtime/*/bin/" + exe))
            candidates.extend(Path(root).glob("runtime/**/bin/" + exe))
        except Exception:
            pass
        try:
            candidates.extend(Path(root).glob("jre/*/bin/" + exe))
        except Exception:
            pass
    try:
        candidates.extend(APP_RUNTIME_DIR.glob("*/bin/" + exe))
    except Exception:
        pass
    seen = set()
    unique = []
    for path in candidates:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def scan_java(root=None, force: bool = False, extra=None) -> list[JavaInfo]:
    global _CACHE, _CACHE_TIME
    if _CACHE and not force and not extra and time.time() - _CACHE_TIME < 120:
        return list(_CACHE)
    found: list[JavaInfo] = []
    for path in _candidate_paths(root=root, extra=extra):
        info = inspect_java(path)
        if info:
            found.append(info)
    found.sort(key=lambda i: (-i.major, i.version), reverse=False)
    if not extra:
        _CACHE = found
        _CACHE_TIME = time.time()
    return found


def choose_java(required: int, override: str = "", root=None,
                allow_higher: bool = True, extra=None) -> JavaInfo:
    if override:
        info = inspect_java(override)
        if not info:
            raise LauncherError(f"指定的 Java 不存在或无法运行：{override}")
        if required and info.major < required:
            raise LauncherError(f"该版本需要 Java {required} 或更高，当前指定的是 Java {info.major}")
        return info
    infos = scan_java(root=root, extra=extra)
    if not infos:
        raise LauncherError("没有找到可用的 Java。请到「下载 → Java 运行时」下载，或在设置中指定 java.exe。")
    exact = [i for i in infos if i.major == required] if required else []
    if exact:
        return exact[0]
    if required:
        higher = sorted([i for i in infos if i.major > required], key=lambda i: i.major)
        if higher and allow_higher:
            return higher[0]
        raise LauncherError(
            f"该版本需要 Java {required}，但只找到：\n"
            + "\n".join(f"  Java {i.major}（{i.path}）" for i in infos)
            + "\n可到「下载 → Java 运行时」自动下载。"
        )
    return infos[0]


def _runtime_manifest(force: bool = False) -> dict:
    cache = Path(CACHE_FILE)
    if not force and cache.is_file() and time.time() - cache.stat().st_mtime < 12 * 3600:
        data = read_json(cache)
        if isinstance(data, dict) and data:
            return data
    data = http_json(RUNTIME_ALL_URL, timeout=60)
    write_json(cache, data)
    return data


def _platform_key() -> str:
    if IS_WINDOWS:
        return {"x86_64": "windows-x64", "arm64": "windows-arm64", "x86": "windows-x86"}.get(ARCH, "windows-x64")
    if sys.platform == "darwin":
        return "mac-os-arm64" if ARCH == "arm64" else "mac-os"
    return "linux"


def available_components() -> list[tuple[str, str]]:
    try:
        data = _runtime_manifest()
        names = set((data.get(_platform_key()) or {}).keys())
    except Exception:
        names = set()
    result = []
    for component, label in COMPONENTS:
        if not names or component in names:
            result.append((component, label))
    return result or list(COMPONENTS)


def component_for_major(major: int) -> str:
    if major <= 8:
        return "jre-legacy"
    if major <= 16:
        return "java-runtime-alpha"
    if major <= 17:
        return "java-runtime-gamma"
    if major <= 21:
        return "java-runtime-delta"
    return "java-runtime-epsilon"


def install_runtime(component: str, progress_cb=None, log=None,
                    cancel=None, workers: int = 8) -> Path:
    def emit(message):
        if log:
            try:
                log(message)
            except Exception:
                pass

    data = _runtime_manifest()
    key = _platform_key()
    bucket = data.get(key) or {}
    if component not in bucket:
        raise LauncherError(f"官方没有提供该平台的运行时：{component}")
    entries = bucket.get(component) or []
    entry = next((e for e in entries if ((e.get("manifest") or {}).get("url"))), None)
    if not entry:
        raise LauncherError(f"运行时 {component} 没有可用的下载清单")
    manifest = http_json(entry["manifest"]["url"], timeout=60)
    files = manifest.get("files") or {}
    dest_root = APP_RUNTIME_DIR / component
    ensure_dir(dest_root)
    items: list[DownloadItem] = []
    for rel, meta in files.items():
        meta = meta or {}
        pure = Path(str(rel).replace("\\", "/"))
        if ".." in pure.parts:
            continue
        target = dest_root.joinpath(*pure.parts)
        kind = meta.get("type")
        if kind == "directory":
            ensure_dir(target)
            continue
        if kind != "file":
            continue
        raw = (meta.get("downloads") or {}).get("raw") or {}
        if not raw.get("url"):
            continue
        items.append(DownloadItem(raw["url"], target, raw.get("sha1") or "",
                                  int(raw.get("size") or 0), str(rel), "runtime"))
    emit(f"下载 Java 运行时 {component}（{len(items)} 个文件）…")
    progress = Progress(progress_cb, f"Java {component}")
    download_many(items, workers, progress, cancel)
    java = dest_root / "bin" / ("java.exe" if IS_WINDOWS else "java")
    if not java.is_file():
        raise LauncherError(f"Java 运行时安装不完整：{java}")
    emit(f"Java 运行时已安装：{java}")
    global _CACHE_TIME
    _CACHE_TIME = 0.0
    return java


def installed_components() -> list[Path]:
    result = []
    if APP_RUNTIME_DIR.is_dir():
        for d in APP_RUNTIME_DIR.iterdir():
            exe = d / "bin" / ("java.exe" if IS_WINDOWS else "java")
            if exe.is_file():
                result.append(exe)
    return result
