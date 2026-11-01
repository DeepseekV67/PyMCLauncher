from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
import time
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

PAGE = "https://optifine.net/downloads"
BASE = "https://optifine.net/"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "installers"
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _rows() -> list[dict]:
    raw = http_request(PAGE, headers={"User-Agent": UA_BROWSER}, timeout=45)
    text = raw.decode("utf-8", "replace")
    rows = re.findall(r"<tr class='downloadLine[^']*'>(.*?)</tr>", text, re.S)
    result = []
    for row in rows:
        label_match = re.search(r"class='colFile'[^>]*>(.*?)</td>", row, re.S)
        link_match = re.search(
            r"class='colMirror'[^>]*>\s*<a[^>]+href=[\"']([^\"']+)", row, re.S)
        if not label_match or not link_match:
            continue
        label = html.unescape(re.sub(r"<[^>]+>", "", label_match.group(1))).strip()
        link = html.unescape(link_match.group(1))
        file_match = re.search(r"[?&]f=([^&\"]+)", link)
        filename = file_match.group(1) if file_match else ""
        mc_match = re.search(r"OptiFine_([^_]+)_", filename)
        mc = mc_match.group(1) if mc_match else ""
        result.append({"label": label, "link": link, "filename": filename,
                       "mc": mc})
    return result


def list_versions(mc_version: str, limit: int = 40) -> list[str]:
    labels = []
    for row in _rows():
        if row["mc"] == mc_version and row["label"] not in labels:
            labels.append(row["label"])
    return labels[:limit]


def _direct_url(link: str) -> str:
    if "downloadx?" in link:
        return link if link.startswith("http") else BASE + link
    page = http_request(link, headers={"User-Agent": UA_BROWSER}, timeout=45)
    text = page.decode("utf-8", "replace")
    match = re.search(r"downloadx\?f=[^\"'<> ]+", text)
    if not match:
        raise LauncherError("OptiFine 下载链接解析失败")
    return BASE + html.unescape(match.group(0))


def _cleanup_appdata(temp):
    if temp is None:
        return
    target = Path(temp) / ".minecraft"
    try:
        os.rmdir(target)
    except OSError:
        pass
    shutil.rmtree(temp, ignore_errors=True)


def _prepare_appdata(repo):
    root = Path(repo.root)
    if root.name.lower() == ".minecraft":
        return str(root.parent), None
    temp = Path(tempfile.mkdtemp(prefix="pymc-optifine-"))
    target = temp / ".minecraft"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(target), str(root)],
        capture_output=True, text=True, errors="replace",
        creationflags=flags)
    if proc.returncode != 0:
        _cleanup_appdata(temp)
        raise LauncherError(
            "无法创建 OptiFine 安装用的目录映射，请把游戏目录设为 .minecraft 后重试。")
    return str(temp), temp


def install(repo, mc_version: str, loader_version: str | None = None,
            with_assets: bool = True, java_path: str = "", log=None) -> str:
    def emit(message):
        if log:
            try:
                log(message)
            except Exception:
                pass

    emit(f"准备安装 OptiFine（Minecraft {mc_version}）…")
    repo.install(mc_version, with_assets=False)
    versions = list_versions(mc_version)
    if not versions:
        raise LauncherError(f"没有找到适用于 {mc_version} 的 OptiFine")
    label = loader_version or versions[0]
    row = next((r for r in _rows() if r["label"] == label and r["mc"] == mc_version), None)
    if not row:
        raise LauncherError("OptiFine 版本已变化，请刷新版本列表。")
    url = _direct_url(row["link"])
    installer_name = row["filename"] or "OptiFine-installer.jar"
    installer = ensure_dir(CACHE_DIR) / installer_name
    emit("下载 OptiFine 安装器…")
    download_item(DownloadItem(url, installer, label=installer_name), timeout=180)
    ensure_dir(repo.versions_dir)
    _ensure_launcher_profiles(repo.root)
    java_exe = java_path or str(
        java.choose_java(8, root=repo.root, allow_higher=True).path)
    appdata, temp = _prepare_appdata(repo)
    before = {p.name for p in repo.versions_dir.iterdir() if p.is_dir()}
    started = time.time() - 5
    env = os.environ.copy()
    env["APPDATA"] = appdata
    emit("运行 OptiFine 安装器（可能需要几分钟）…")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [java_exe, "-jar", str(installer)],
            cwd=str(repo.root), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
            creationflags=flags)
    except subprocess.TimeoutExpired:
        raise LauncherError("OptiFine 安装器超时。") from None
    finally:
        _cleanup_appdata(temp)
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-12:])
        raise LauncherError(f"OptiFine 安装器返回错误（{proc.returncode}）：\n{tail}")
    candidates = [p for p in repo.versions_dir.iterdir()
                  if p.is_dir() and (p / f"{p.name}.json").is_file()]
    new = [p for p in candidates if p.name not in before]
    if not new:
        new = [p for p in candidates
               if "optifine" in p.name.lower() and p.stat().st_mtime >= started]
    if not new:
        tail = "\n".join(output.splitlines()[-12:])
        raise LauncherError("OptiFine 安装器没有生成新的版本。\n" + tail)
    version_dir = max(new, key=lambda p: p.stat().st_mtime)
    version_id = version_dir.name
    emit(f"OptiFine 版本已生成：{version_id}，检查依赖库…")
    repo.install(version_id, with_assets=with_assets)
    return version_id
