from __future__ import annotations

import hashlib
import os
import threading
import zipfile
from pathlib import Path

from .config import RUNTIME_DIR
from .util import (
    UA,
    DownloadItem,
    LauncherError,
    Progress,
    download_item,
    ensure_dir,
    http_json,
)

METADATA_URL = "https://authlib-injector.yushi.moe/artifact/latest.json"
JAR_NAME = "authlib-injector.jar"
_LOCK = threading.Lock()


def injector_path() -> Path:
    return RUNTIME_DIR / JAR_NAME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def ensure_authlib_injector(progress_cb=None, cancel=None) -> Path:
    """Download authlib-injector if it is not present in runtime/."""
    path = injector_path()
    with _LOCK:
        if (path.is_file() and path.stat().st_size > 0
                and zipfile.is_zipfile(path)):
            return path
        ensure_dir(path.parent)
        try:
            meta = http_json(
                METADATA_URL,
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=40,
            )
        except Exception as e:
            raise LauncherError(
                "无法获取 authlib-injector，请检查网络。\n"
                f"也可以手动下载后放到：{path}"
            ) from e
        url = str(meta.get("download_url") or "")
        sha256 = str((meta.get("checksums") or {}).get("sha256") or "").lower()
        if not url:
            raise LauncherError("authlib-injector 下载地址无效，请稍后重试。")
        tmp = path.with_name(path.name + ".part")
        try:
            item = DownloadItem(url=url, dest=tmp, label=JAR_NAME, kind="authlib")
            download_item(item, Progress(progress_cb, JAR_NAME), cancel, timeout=120)
            if sha256 and _sha256(tmp).lower() != sha256:
                raise LauncherError("authlib-injector 校验失败，请重试。")
            os.replace(tmp, path)
        except Exception as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            if isinstance(e, LauncherError):
                raise
            raise LauncherError(f"下载 authlib-injector 失败：{e}") from e
        return path
