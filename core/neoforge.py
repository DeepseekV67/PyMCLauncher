from __future__ import annotations

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import java
from .forge import _ensure_launcher_profiles
from .util import (
    DownloadItem,
    LauncherError,
    download_item,
    ensure_dir,
    http_request,
)

MIRROR = "https://neoforged.forgecdn.net"
OFFICIAL = "https://maven.neoforged.net"
METADATA_PATH = "/releases/net/neoforged/neoforge/maven-metadata.xml"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "installers"


def _prefixes(mc_version: str) -> list[str]:
    if mc_version.startswith("1."):
        parts = mc_version[2:].split(".")
        if len(parts) >= 2:
            return [".".join(parts[:2]) + "."]
        return [parts[0] + ".0.", parts[0] + "."]
    return [mc_version + "."]


def _version_key(version: str):
    return tuple(int(x) for x in re.findall(r"\d+", version)[:6])


def list_versions(mc_version: str, limit: int = 60) -> list[str]:
    last_error = None
    raw = None
    for base in (MIRROR, OFFICIAL):
        try:
            raw = http_request(base + METADATA_PATH, timeout=45)
            break
        except Exception as e:
            last_error = e
    if raw is None:
        raise LauncherError(f"获取 NeoForge 版本列表失败：{last_error}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise LauncherError("NeoForge 版本列表解析失败") from None
    versions = [(node.text or "").strip() for node in root.iter("version")]
    prefixes = _prefixes(mc_version)
    result = [v for v in versions if any(v.startswith(p) for p in prefixes)]
    result.sort(key=_version_key, reverse=True)
    return result[:limit]


def install(repo, mc_version: str, loader_version: str | None = None,
            with_assets: bool = True, java_path: str = "", log=None) -> str:
    def emit(message):
        if log:
            try:
                log(message)
            except Exception:
                pass

    emit(f"准备安装 NeoForge（Minecraft {mc_version}）…")
    repo.install(mc_version, with_assets=False)
    versions = list_versions(mc_version)
    if not versions:
        raise LauncherError(f"没有找到适用于 {mc_version} 的 NeoForge")
    lv = loader_version or versions[0]
    ensure_dir(repo.versions_dir)
    _ensure_launcher_profiles(repo.root)
    installer_name = f"neoforge-{lv}-installer.jar"
    installer = ensure_dir(CACHE_DIR) / installer_name
    path = f"/releases/net/neoforged/neoforge/{lv}/{installer_name}"
    emit("下载 NeoForge 安装器…")
    download_item(DownloadItem(
        MIRROR + path, installer, label=installer_name,
        alternates=(OFFICIAL + path,)), timeout=180)
    java_exe = java_path or str(
        java.choose_java(17, root=repo.root, allow_higher=True).path)
    before = {p.name for p in repo.versions_dir.iterdir() if p.is_dir()}
    started = time.time() - 5
    emit("运行 NeoForge 安装器（可能需要几分钟）…")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [java_exe, "-jar", str(installer), "--installClient", str(repo.root)],
            cwd=str(repo.root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1200,
            creationflags=flags)
    except subprocess.TimeoutExpired:
        raise LauncherError("NeoForge 安装器超时。") from None
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-12:])
        raise LauncherError(f"NeoForge 安装器返回错误（{proc.returncode}）：\n{tail}")
    candidates = [p for p in repo.versions_dir.iterdir()
                  if p.is_dir() and (p / f"{p.name}.json").is_file()]
    new = [p for p in candidates if p.name not in before]
    if not new:
        new = [p for p in candidates
               if "neoforge" in p.name.lower() and p.stat().st_mtime >= started]
    if not new:
        tail = "\n".join(output.splitlines()[-12:])
        raise LauncherError("NeoForge 安装器没有生成新的版本。\n" + tail)
    version_dir = max(new, key=lambda p: p.stat().st_mtime)
    version_id = version_dir.name
    emit(f"NeoForge 版本已生成：{version_id}，检查依赖库…")
    repo.install(version_id, with_assets=with_assets)
    return version_id
