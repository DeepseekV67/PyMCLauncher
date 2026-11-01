from __future__ import annotations

import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import java
from .util import (
    DownloadItem,
    LauncherError,
    download_item,
    ensure_dir,
    http_request,
    read_json,
    write_json,
)

FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
METADATA_URL = f"{FORGE_MAVEN}/maven-metadata.xml"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "installers"


def list_versions(mc_version: str, limit: int = 40) -> list[str]:
    raw = http_request(METADATA_URL, timeout=60)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise LauncherError("Forge 版本列表解析失败") from None
    prefix = f"{mc_version}-"
    result = []
    for node in root.iter("version"):
        text = (node.text or "").strip()
        if text.startswith(prefix):
            rest = text[len(prefix):]
            if rest and " " not in rest:
                result.append(rest)
    result.reverse()
    return result[:limit]


def _ensure_launcher_profiles(root: Path):
    path = root / "launcher_profiles.json"
    data = read_json(path)
    if not isinstance(data, dict) or "profiles" not in data:
        if path.exists():
            backup = path.with_name("launcher_profiles.json.pymc-backup")
            try:
                if not backup.exists():
                    backup.write_bytes(path.read_bytes())
            except OSError:
                pass
        write_json(path, {"profiles": {}, "settings": {}, "version": 3})


def install(repo, mc_version: str, forge_version: str,
            with_assets: bool = True, java_path: str = "", log=None) -> str:
    def emit(message):
        if log:
            try:
                log(message)
            except Exception:
                pass

    emit(f"准备安装 Forge（Minecraft {mc_version} / {forge_version}）…")
    repo.install(mc_version, with_assets=False)
    ensure_dir(repo.versions_dir)
    _ensure_launcher_profiles(repo.root)

    installer_name = f"forge-{mc_version}-{forge_version}-installer.jar"
    installer = ensure_dir(CACHE_DIR) / installer_name
    url = f"{FORGE_MAVEN}/{mc_version}-{forge_version}/{installer_name}"
    emit("下载 Forge 安装器…")
    download_item(DownloadItem(url, installer, label=installer_name), timeout=120)

    if java_path:
        java_exe = java_path
    else:
        java_exe = str(java.choose_java(8, root=repo.root, allow_higher=True).path)

    before = {p.name for p in repo.versions_dir.iterdir() if p.is_dir()}
    started = time.time() - 5
    emit("运行 Forge 安装器（可能需要几分钟）…")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [java_exe, "-jar", str(installer), "--installClient", str(repo.root)],
            cwd=str(repo.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1200,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        raise LauncherError("Forge 安装器超时。") from None
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-12:])
        raise LauncherError(f"Forge 安装器返回错误（{proc.returncode}）：\n{tail}")

    candidates = [p for p in repo.versions_dir.iterdir()
                  if p.is_dir() and (p / f"{p.name}.json").is_file()]
    new = [p for p in candidates if p.name not in before]
    if not new:
        new = [p for p in candidates
               if "forge" in p.name.lower() and p.stat().st_mtime >= started]
    if not new:
        tail = "\n".join(output.splitlines()[-12:])
        raise LauncherError("Forge 安装器没有生成新的版本。\n" + tail)
    version_dir = max(new, key=lambda p: p.stat().st_mtime)
    version_id = version_dir.name
    emit(f"Forge 版本已生成：{version_id}，检查依赖库…")
    repo.install(version_id, with_assets=with_assets)
    return version_id
