"""Terracotta（陶瓦联机）本地 HTTP API 封装。

联机功能由 Terracotta 提供（AGPL-3.0）：
https://github.com/burningtnt/Terracotta

启动器只下载未经修改的官方二进制，并通过其本地 HTTP API 创建/加入房间。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .util import (
    DownloadItem,
    LauncherError,
    Progress,
    download_item,
    ensure_dir,
)

VERSION = "0.4.2"
ASSET = f"terracotta-{VERSION}-windows-x86_64-pkg.tar.gz"
ASSET_SIZE = 8135914
EXE_SHA256 = "74c10568a7fea9c1d38cf8d2d4ca90baf1517f8e5a26c63d3349db70bc449796"
GITEE_URL = (
    f"https://gitee.com/burningtnt/Terracotta/releases/download/"
    f"v{VERSION}/{ASSET}"
)
GITHUB_URL = (
    f"https://github.com/burningtnt/Terracotta/releases/download/"
    f"v{VERSION}/{ASSET}"
)
PROJECT_URL = "https://github.com/burningtnt/Terracotta"
LICENSE_NAME = "AGPL-3.0"

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def install_root() -> Path:
    return config.DATA_DIR / "terracotta"


def install_dir() -> Path:
    return install_root() / VERSION


def find_exe() -> Path | None:
    folder = install_dir()
    if not folder.is_dir():
        return None
    for path in folder.glob("*.exe"):
        if path.is_file() and path.stat().st_size > 1024 * 1024:
            return path
    return None


def is_installed() -> bool:
    return find_exe() is not None


def _extract(archive: Path, folder: Path) -> None:
    allowed = {"VCRUNTIME140.DLL"}
    try:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = Path(member.name).name
                if not name.lower().endswith(".exe") and name.upper() not in allowed:
                    continue
                source = tf.extractfile(member)
                if source is None:
                    continue
                target = folder / name
                temp = folder / (name + ".part")
                temp.write_bytes(source.read())
                os.replace(temp, target)
    except (OSError, tarfile.TarError) as e:
        raise LauncherError(f"陶瓦联机解压失败：{e}") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_installed(progress_cb=None, cancel=None) -> Path:
    exe = find_exe()
    if exe is not None:
        return exe

    folder = ensure_dir(install_dir())
    archive = folder / ASSET
    try:
        archive.unlink()
    except OSError:
        pass

    item = DownloadItem(
        url=GITEE_URL,
        dest=archive,
        size=ASSET_SIZE,
        label=ASSET,
        kind="runtime",
        alternates=(GITHUB_URL,),
    )
    progress = Progress(progress_cb, "陶瓦联机")
    download_item(item, progress, cancel, timeout=120)
    if not archive.is_file() or archive.stat().st_size < 1024 * 1024:
        raise LauncherError("陶瓦联机下载不完整，请重试。")

    _extract(archive, folder)
    try:
        archive.unlink()
    except OSError:
        pass

    exe = find_exe()
    if exe is None:
        raise LauncherError(
            "陶瓦联机安装失败：压缩包里没有找到可执行文件。\n"
            f"可以手动下载后放到：{folder}")
    if _sha256(exe) != EXE_SHA256:
        try:
            exe.unlink()
        except OSError:
            pass
        raise LauncherError(
            "陶瓦联机文件校验失败，可能下载不完整。\n请重试，或从项目页面手动下载。")
    return exe


def port_file() -> Path:
    return ensure_dir(install_root()) / "port.json"


def lock_file() -> Path:
    return Path(tempfile.gettempdir()) / "terracotta" / "terracotta.lock"


def _read_port_json(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        port = int(data.get("port") or 0)
        return port if 0 < port < 65536 else 0
    except Exception:
        return 0


def _read_lock_port() -> int:
    try:
        raw = lock_file().read_bytes()
    except OSError:
        return 0
    if len(raw) < 2:
        return 0
    port = (raw[0] << 8) | raw[1]
    return port if 0 < port < 65536 else 0


def _request(port: int, path: str, timeout: float = 2.0) -> bytes:
    url = f"http://127.0.0.1:{int(port)}{path}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "PyMCLauncher/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _json(port: int, path: str, timeout: float = 2.0) -> dict:
    raw = _request(port, path, timeout)
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def meta(port: int, timeout: float = 2.0) -> dict:
    return _json(port, "/meta", timeout)


def state(port: int, timeout: float = 2.0) -> dict:
    return _json(port, "/state", timeout)


def _ping(port: int) -> bool:
    try:
        return bool(meta(port, 1.5).get("version"))
    except Exception:
        return False


def discover_port() -> int:
    for port in (_read_port_json(port_file()), _read_lock_port()):
        if port and _ping(port):
            return port
    return 0


def _wait_for_port(path: Path, timeout: float = 12.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = _read_port_json(path)
        if port:
            return port
        time.sleep(0.2)
    return 0


def start() -> tuple[int, bool]:
    """启动（或复用）陶瓦联机，返回 (端口, 是否由本程序启动)。"""
    exe = ensure_installed()
    existing = discover_port()
    if existing:
        return existing, False

    pf = port_file()
    try:
        pf.unlink()
    except OSError:
        pass

    try:
        subprocess.Popen(
            [str(exe), "--hmcl", str(pf)],
            cwd=str(exe.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as e:
        raise LauncherError(f"无法启动陶瓦联机：{e}") from None

    port = _wait_for_port(pf)
    if not port:
        port = _read_lock_port()
    for _ in range(6):
        if port and _ping(port):
            return port, True
        time.sleep(0.5)
    raise LauncherError(
        "陶瓦联机启动失败。\n"
        "请检查杀毒软件是否拦截，或打开联机页面手动下载。\n"
        f"安装目录：{install_dir()}")


def create_room(port: int, player: str = "") -> None:
    params = {"player": player} if player else {}
    path = "/state/scanning"
    if params:
        path += "?" + urllib.parse.urlencode(params)
    _request(port, path, timeout=5.0)


def join_room(port: int, room: str, player: str = "") -> None:
    params = {"room": room}
    if player:
        params["player"] = player
    try:
        _request(port, "/state/guesting?" + urllib.parse.urlencode(params),
                 timeout=5.0)
    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise LauncherError(
                "房间码无效，或当前不在待机状态。\n请确认房间码完整后重试。") from None
        raise LauncherError(f"加入房间失败：HTTP {e.code}") from None


def reset(port: int) -> None:
    try:
        _request(port, "/state/ide", timeout=2.0)
    except Exception as e:
        raise LauncherError(f"无法返回待机：{e}") from None


def stop(port: int) -> None:
    """请求陶瓦联机优雅退出。连接被关闭属于正常现象。"""
    try:
        _request(port, "/panic?peaceful=true", timeout=1.5)
    except Exception:
        pass
