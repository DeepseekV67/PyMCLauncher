from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from .config import CACHE_DIR, safe_folder_name, unique_dir
from .util import (
    UA,
    DownloadItem,
    LauncherError,
    Progress,
    download_item,
    ensure_dir,
    http_json,
)

CURSEFORGE = "https://mod.mcimirror.top/curseforge/v1"
GAME_ID = 432
WORLD_CLASS_ID = 17


def _cf_json(path: str, params: dict | None = None):
    url = CURSEFORGE + path
    if params:
        url += "?" + urlencode(params)
    return http_json(url, headers={"User-Agent": UA, "Accept": "application/json"},
                     timeout=40)


def search_worlds(query: str, mc_version: str = "", limit: int = 20,
                  offset: int = 0) -> list[dict]:
    params = {
        "gameId": str(GAME_ID),
        "classId": str(WORLD_CLASS_ID),
        "pageSize": str(max(1, min(limit, 50))),
        "index": str(max(0, offset)),
    }
    if query:
        params["searchFilter"] = query
    if mc_version:
        params["gameVersion"] = mc_version
    try:
        data = _cf_json("/mods/search", params)
    except Exception:
        if not mc_version:
            raise
        params.pop("gameVersion", None)
        data = _cf_json("/mods/search", params)
    hits = []
    for mod in (data.get("data") if isinstance(data, dict) else None) or []:
        if not isinstance(mod, dict):
            continue
        authors = mod.get("authors") or []
        author = ""
        if authors and isinstance(authors[0], dict):
            author = str(authors[0].get("name") or "")
        hits.append({
            "project_id": str(mod.get("id") or ""),
            "slug": mod.get("slug") or "",
            "title": mod.get("name") or mod.get("slug") or "?",
            "description": mod.get("summary") or "",
            "author": author,
            "downloads": int(mod.get("downloadCount") or 0),
            "source": "curseforge",
        })
    return hits


def _world_files(project_id: str, mc_version: str = "") -> list[dict]:
    params = {"pageSize": "50"}
    if mc_version:
        params["gameVersion"] = mc_version
    data = _cf_json(f"/mods/{project_id}/files", params)
    files = (data.get("data") if isinstance(data, dict) else None) or []
    if not files and mc_version:
        data = _cf_json(f"/mods/{project_id}/files", {"pageSize": "50"})
        files = (data.get("data") if isinstance(data, dict) else None) or []
    return files


def _safe_extract(zf: zipfile.ZipFile, target: Path):
    names = [n.replace("\\", "/") for n in zf.namelist()]
    files = [n for n in names if n and not n.endswith("/")]
    prefix = ""
    level_paths = [n for n in files
                   if n == "level.dat" or n.endswith("/level.dat")]
    if "level.dat" not in level_paths and level_paths:
        level_path = min(level_paths, key=lambda n: len(n.split("/")))
        prefix = level_path.rsplit("/", 1)[0] + "/"
    elif not level_paths:
        tops = {n.split("/", 1)[0] for n in files if "/" in n}
        if len(tops) == 1:
            prefix = next(iter(tops)) + "/"
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if prefix:
            if not name.startswith(prefix):
                continue
            name = name[len(prefix):]
        if not name or name.endswith("/"):
            continue
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts:
            continue
        dest = target.joinpath(*parts)
        ensure_dir(dest.parent)
        with zf.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def install_world(project_id: str, title: str, game_dir, mc_version: str = "",
                  progress_cb=None, cancel=None) -> dict:
    files = _world_files(project_id, mc_version)
    chosen = None
    for file in files:
        if file.get("downloadUrl"):
            chosen = file
            if file.get("releaseType") == 1:
                break
    if not chosen:
        raise LauncherError("该地图没有可下载文件。")
    filename = Path(str(chosen.get("fileName") or "world.zip")).name
    url = str(chosen.get("downloadUrl") or "")
    hashes = chosen.get("hashes") or []
    sha1 = next((h.get("value") for h in hashes
                 if str(h.get("algo")) == "1"), "") or ""
    temp = ensure_dir(CACHE_DIR / "content") / f"world-{project_id}-{filename}"
    item = DownloadItem(url=url, dest=temp, sha1=sha1,
                        size=int(chosen.get("fileLength") or 0),
                        label=filename, kind="world")
    progress = Progress(progress_cb, f"地图 {filename}")
    try:
        download_item(item, progress, cancel, timeout=180)
        name = safe_folder_name(title or Path(filename).stem, "world")
        target = unique_dir(ensure_dir(Path(game_dir) / "saves"), name)
        ensure_dir(target)
        try:
            with zipfile.ZipFile(temp) as zf:
                _safe_extract(zf, target)
            if not (target / "level.dat").is_file():
                raise LauncherError("地图压缩包中未找到 level.dat。")
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    progress.emit(True)
    return {
        "path": target,
        "filename": target.name,
        "version": "",
        "name": title or target.name,
        "dependencies": [],
        "kind": "world",
    }
