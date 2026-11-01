from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

from .util import (
    ARCH,
    IS_64,
    OS_NAME,
    DownloadItem,
    LauncherError,
    Progress,
    download_many,
    ensure_dir,
    file_ok,
    http_json,
    http_request,
    maven_path,
    read_json,
    rules_allow,
    write_json,
)

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MANIFEST_MIRROR = "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"
MIRROR = "https://bmclapi2.bangbang93.com"
LIBRARIES_BASE = "https://libraries.minecraft.net/"
ASSETS_BASE = "https://resources.download.minecraft.net/"


def _looks_native(value: str) -> bool:
    value = value or ""
    base = value.replace("\\", "/").rsplit("/", 1)[-1]
    if "-natives-" in base:
        return True
    parts = value.split(":")
    return len(parts) >= 4 and parts[3].startswith("natives")


def _native_arch_ok(value: str) -> bool:
    base = (value or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base.endswith(".jar"):
        base = base[:-4]
    tags = base.split("-")
    if "arm64" in tags or "aarch64" in tags:
        return ARCH == "arm64"
    if "x86" in tags:
        return ARCH == "x86"
    if "64" in tags:
        return ARCH == "x86_64"
    if "32" in tags:
        return ARCH == "x86"
    return True


def _with_classifier(name: str, classifier: str) -> str:
    ext = ""
    base = name
    if "@" in base:
        base, ext = base.split("@", 1)
    parts = base.split(":")
    if len(parts) >= 4:
        parts[3] = classifier
    else:
        parts.append(classifier)
    result = ":".join(parts)
    return result + ("@" + ext if ext else "")


def resolve_libraries(version: dict) -> list[dict]:
    """Turn a (merged) version json into a flat list of artifact entries."""
    result: list[dict] = []
    for lib in version.get("libraries") or []:
        if not isinstance(lib, dict):
            continue
        if not rules_allow(lib.get("rules")):
            continue
        name = lib.get("name") or ""
        downloads = lib.get("downloads") or {}
        natives_map = lib.get("natives")
        base = lib.get("url") or LIBRARIES_BASE
        if not base.endswith("/"):
            base += "/"

        if natives_map:
            classifier = (natives_map or {}).get(OS_NAME)
            if not classifier:
                continue
            classifier = classifier.replace("${arch}", "64" if IS_64 else "32")
            dl = (downloads.get("classifiers") or {}).get(classifier)
            if dl is None:
                path = maven_path(_with_classifier(name, classifier))
                dl = {"path": path, "url": base + path, "sha1": "", "size": 0}
            path = dl.get("path") or maven_path(_with_classifier(name, classifier))
            if not _native_arch_ok(path):
                continue
            result.append({
                "name": name,
                "path": path,
                "url": dl.get("url") or base + path,
                "sha1": dl.get("sha1") or "",
                "size": int(dl.get("size") or 0),
                "native": True,
                "excludes": (lib.get("extract") or {}).get("exclude") or ["META-INF/"],
            })
            continue

        dl = downloads.get("artifact")
        if dl is None:
            path = maven_path(name)
            native = _looks_native(name) or _looks_native(path)
            if native and not _native_arch_ok(path):
                continue
            result.append({
                "name": name,
                "path": path,
                "url": base + path,
                "sha1": "",
                "size": 0,
                "native": native,
                "excludes": (lib.get("extract") or {}).get("exclude") or ["META-INF/"],
            })
            continue

        path = dl.get("path") or maven_path(name)
        native = _looks_native(name) or _looks_native(path)
        if native and not _native_arch_ok(path):
            continue
        result.append({
            "name": name,
            "path": path,
            "url": dl.get("url") or base + path,
            "sha1": dl.get("sha1") or "",
            "size": int(dl.get("size") or 0),
            "native": native,
            "excludes": (lib.get("extract") or {}).get("exclude") or ["META-INF/"],
        })
    return result


def _merge_two(parent: dict, child: dict) -> dict:
    out = dict(parent)
    for key, value in child.items():
        if key in ("libraries", "arguments", "minecraftArguments", "downloads",
                   "inheritsFrom", "id", "jar"):
            continue
        out[key] = value
    out["id"] = child.get("id") or parent.get("id")
    if child.get("jar"):
        out["jar"] = child["jar"]
    downloads = dict(parent.get("downloads") or {})
    downloads.update(child.get("downloads") or {})
    if downloads:
        out["downloads"] = downloads

    seen = set()
    libraries = []
    for lib in list(child.get("libraries") or []) + list(parent.get("libraries") or []):
        if not isinstance(lib, dict):
            continue
        name = lib.get("name") or ""
        if name and name in seen:
            continue
        if name:
            seen.add(name)
        libraries.append(lib)
    out["libraries"] = libraries

    pargs = parent.get("arguments") or {}
    cargs = child.get("arguments") or {}
    if pargs or cargs:
        out["arguments"] = {
            "game": list(pargs.get("game") or []) + list(cargs.get("game") or []),
            "jvm": list(pargs.get("jvm") or []) + list(cargs.get("jvm") or []),
        }
    if "minecraftArguments" not in child and parent.get("minecraftArguments"):
        out["minecraftArguments"] = parent["minecraftArguments"]
    return out


class Repo:
    """One Minecraft game directory (versions / libraries / assets)."""

    def __init__(self, root, source: str = "official", workers: int = 16,
                 progress=None, log=None, cancel=None, cache_dir=None):
        self.root = Path(root)
        self.source = source if source in ("official", "bmcl") else "official"
        self.workers = max(1, int(workers or 16))
        self.progress = progress
        self.log_fn = log
        self.cancel = cancel
        self.cache_dir = ensure_dir(cache_dir or (Path(__file__).resolve().parent.parent / "cache"))
        self._manifest = None

    # ------------------------------------------------------------------ paths
    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def libraries_dir(self) -> Path:
        return self.root / "libraries"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def indexes_dir(self) -> Path:
        return self.assets_dir / "indexes"

    def version_dir(self, version_id: str) -> Path:
        return self.versions_dir / version_id

    def version_json_path(self, version_id: str) -> Path:
        return self.versions_dir / version_id / f"{version_id}.json"

    def log_config_path(self, version: dict) -> Path | None:
        client = (version.get("logging") or {}).get("client")
        if not client:
            return None
        file = client.get("file") or {}
        name = Path(str(file.get("id") or "log4j.xml")).name
        path = self.assets_dir / "log_configs" / name
        return path if path.is_file() else None

    def client_jar_path(self, version: dict) -> Path:
        jar_id = version.get("__jar") or version.get("id")
        return self.versions_dir / jar_id / f"{jar_id}.jar"

    def log(self, message: str):
        if self.log_fn:
            try:
                self.log_fn(message)
            except Exception:
                pass

    def _report(self, items: list[DownloadItem], label: str):
        if not items:
            return
        progress = Progress(self.progress, label)
        download_many(items, self.workers, progress, self.cancel)

    # --------------------------------------------------------------- manifest
    def manifest(self, force: bool = False) -> dict:
        if self._manifest is not None and not force:
            return self._manifest
        cache_file = self.cache_dir / "version_manifest_v2.json"
        if not force and cache_file.is_file():
            age = time.time() - cache_file.stat().st_mtime
            if age < 6 * 3600:
                data = read_json(cache_file)
                if isinstance(data, dict) and data.get("versions"):
                    self._manifest = data
                    return data
        urls = [MANIFEST_MIRROR, MANIFEST_URL] if self.source == "bmcl" else [MANIFEST_URL, MANIFEST_MIRROR]
        last_error = None
        for url in urls:
            try:
                data = http_json(url, timeout=40)
                if isinstance(data, dict) and data.get("versions"):
                    write_json(cache_file, data)
                    self._manifest = data
                    return data
            except Exception as e:
                last_error = e
        cached = read_json(cache_file)
        if isinstance(cached, dict) and cached.get("versions"):
            self._manifest = cached
            self.log("使用缓存的版本列表（网络不可用）")
            return cached
        raise LauncherError(f"获取版本列表失败：{last_error}")

    def manifest_entry(self, version_id: str) -> dict | None:
        try:
            for entry in self.manifest().get("versions") or []:
                if entry.get("id") == version_id:
                    return entry
        except Exception:
            return None
        return None

    def installed(self) -> list[dict]:
        items = []
        if not self.versions_dir.is_dir():
            return items
        for d in self.versions_dir.iterdir():
            if not d.is_dir():
                continue
            data = read_json(d / f"{d.name}.json")
            if not isinstance(data, dict):
                continue
            items.append({
                "id": d.name,
                "type": data.get("type") or "",
                "releaseTime": data.get("releaseTime") or data.get("time") or "",
                "clientVersion": data.get("clientVersion") or "",
            })
        items.sort(key=lambda x: str(x.get("releaseTime")), reverse=True)
        return items

    def installed_ids(self) -> list[str]:
        return [v["id"] for v in self.installed()]

    # ------------------------------------------------------------------ json
    def ensure_version_json(self, version_id: str, refresh: bool = False) -> dict:
        path = self.version_json_path(version_id)
        local = read_json(path)
        if local is not None and not refresh:
            return local
        entry = self.manifest_entry(version_id)
        urls: list[str] = []
        if entry:
            if self.source == "bmcl":
                urls.append(f"{MIRROR}/version/{version_id}/json")
            if entry.get("url"):
                urls.append(entry["url"])
        if local is None and not entry:
            raise LauncherError(f"版本不存在：{version_id}（也不在本地）")
        last_error = None
        for url in urls:
            try:
                data = http_json(url, timeout=40)
                if isinstance(data, dict) and (data.get("id") or data.get("mainClass")):
                    self._save_version_json(version_id, data)
                    return data
            except Exception as e:
                last_error = e
        if local is not None:
            if urls:
                self.log(f"无法更新 {version_id} 的版本信息，使用本地文件")
            return local
        raise LauncherError(f"下载版本信息失败：{version_id}\n{last_error}")

    def _save_version_json(self, version_id: str, data: dict):
        path = self.version_json_path(version_id)
        ensure_dir(path.parent)
        write_json(path, data)

    def write_version_json(self, version_id: str, data: dict):
        self._save_version_json(version_id, data)

    def chain(self, version_id: str) -> list[dict]:
        chain = []
        seen = set()
        current = version_id
        while current:
            if current in seen:
                raise LauncherError(f"版本继承关系存在循环：{current}")
            seen.add(current)
            data = read_json(self.version_json_path(current))
            if not isinstance(data, dict):
                raise LauncherError(f"缺少版本文件：{current}，请先下载该版本")
            chain.append(data)
            current = data.get("inheritsFrom")
        return chain

    def merged(self, version_id: str) -> dict:
        chain = self.chain(version_id)
        version = dict(chain[-1])
        for child in reversed(chain[:-1]):
            version = _merge_two(version, child)
        if not version.get("id"):
            version["id"] = version_id
        version["__jar"] = version.get("jar") or chain[-1].get("id") or version_id
        version["__chain"] = [v.get("id") for v in chain]
        version["__root"] = chain[-1].get("id") or version_id
        return version

    # --------------------------------------------------------------- install
    def install(self, version_id: str, with_assets: bool = True) -> str:
        data = self.ensure_version_json(version_id, refresh=True)
        parent = data.get("inheritsFrom")
        if parent and not self.version_json_path(parent).exists():
            self.log(f"先安装父版本 {parent} …")
            self.install(parent, with_assets=False)
        merged = self.merged(version_id)
        self._download_client(merged)
        self._download_logging(merged)
        self._download_libraries(merged)
        if with_assets:
            self._download_assets(merged)
        self.log(f"版本 {version_id} 安装完成")
        return version_id

    def _download_client(self, version: dict):
        client = (version.get("downloads") or {}).get("client")
        if not client:
            raise LauncherError("版本信息中缺少客户端下载地址")
        jar_id = version.get("__jar") or version.get("id")
        dest = self.versions_dir / jar_id / f"{jar_id}.jar"
        url = client.get("url") or ""
        if self.source == "bmcl":
            url = f"{MIRROR}/version/{jar_id}/client"
        item = DownloadItem(url, dest, client.get("sha1") or "",
                            int(client.get("size") or 0), f"客户端 {jar_id}", "client")
        self.log(f"下载客户端 {jar_id} …")
        self._report([item], "客户端")

    def _download_logging(self, version: dict):
        client = (version.get("logging") or {}).get("client")
        if not client:
            return
        file = client.get("file") or {}
        if not file.get("url"):
            return
        name = Path(str(file.get("id") or "log4j.xml")).name
        dest = self.assets_dir / "log_configs" / name
        item = DownloadItem(file["url"], dest, file.get("sha1") or "",
                            int(file.get("size") or 0), name, "logging")
        self._report([item], "日志配置")

    def _lib_url(self, url: str, path: str) -> str:
        if self.source == "bmcl" and "libraries.minecraft.net/" in url:
            return f"{MIRROR}/maven/{path}"
        return url

    def _download_libraries(self, version: dict):
        entries = resolve_libraries(version)
        items = []
        for entry in entries:
            dest = self.libraries_dir / entry["path"]
            items.append(DownloadItem(
                self._lib_url(entry["url"], entry["path"]), dest,
                entry["sha1"], entry["size"], entry["name"], "library",
            ))
        self.log(f"检查依赖库（{len(items)} 个文件）…")
        self._report(items, "依赖库")

    def _ensure_index(self, asset_index: dict) -> Path:
        index_id = str(asset_index.get("id") or "")
        path = self.indexes_dir / f"{index_id}.json"
        probe = DownloadItem(asset_index.get("url") or "", path,
                             asset_index.get("sha1") or "",
                             int(asset_index.get("size") or 0))
        if file_ok(probe) or (path.is_file() and path.stat().st_size > 0):
            return path
        urls = [asset_index.get("url") or ""]
        if self.source == "bmcl":
            urls.insert(0, f"{MIRROR}/assets/indexes/{index_id}.json")
        last_error = None
        for url in urls:
            if not url:
                continue
            try:
                raw = http_request(url, timeout=60)
                data = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(data.get("objects"), dict):
                    raise LauncherError("资源索引格式不正确")
                ensure_dir(path.parent)
                tmp = path.with_name(path.name + ".tmp")
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, path)
                return path
            except Exception as e:
                last_error = e
        raise LauncherError(f"下载资源索引失败：{index_id}\n{last_error}")

    def read_asset_index(self, version: dict) -> dict:
        asset_index = version.get("assetIndex") or {}
        index_id = str(asset_index.get("id") or "legacy")
        path = self.indexes_dir / f"{index_id}.json"
        data = read_json(path)
        if not isinstance(data, dict):
            raise LauncherError(f"资源索引缺失：{index_id}，请重新下载该版本")
        return data

    def _download_assets(self, version: dict):
        asset_index = version.get("assetIndex")
        if not asset_index:
            return
        path = self._ensure_index(asset_index)
        data = read_json(path) or {}
        objects = data.get("objects") or {}
        items = []
        for name, obj in objects.items():
            h = (obj or {}).get("hash")
            if not h:
                continue
            sub = h[:2]
            dest = self.assets_dir / "objects" / sub / h
            url = f"{ASSETS_BASE}{sub}/{h}"
            if self.source == "bmcl":
                url = f"{MIRROR}/assets/{sub}/{h}"
            items.append(DownloadItem(url, dest, h, int(obj.get("size") or 0), name, "asset"))
        self.log(f"检查资源文件（索引 {asset_index.get('id')}，{len(items)} 个文件）…")
        self._report(items, "资源文件")
        if data.get("virtual"):
            self.materialize_assets(data, self.assets_dir / "virtual" / str(asset_index.get("id")),
                                    stamp=asset_index.get("sha1") or "")

    def materialize_assets(self, index: dict, dest: Path, stamp: str = ""):
        dest = Path(dest)
        stamp_file = dest / ".pymc-index"
        if stamp and stamp_file.is_file():
            try:
                if stamp_file.read_text(encoding="utf-8").strip() == stamp:
                    return
            except OSError:
                pass
        objects = index.get("objects") or {}
        copied = 0
        for name, obj in objects.items():
            h = (obj or {}).get("hash")
            if not h:
                continue
            src = self.assets_dir / "objects" / h[:2] / h
            if not src.is_file():
                continue
            pure = PurePosixPath(str(name).replace("\\", "/"))
            if ".." in pure.parts:
                continue
            target = dest.joinpath(*[p for p in pure.parts if p not in ("", ".")])
            ensure_dir(target.parent)
            try:
                if target.is_file() and target.stat().st_size == src.stat().st_size:
                    continue
                shutil.copyfile(src, target)
                copied += 1
            except OSError:
                continue
        ensure_dir(dest)
        if stamp:
            try:
                stamp_file.write_text(stamp, encoding="utf-8")
            except OSError:
                pass
        if copied:
            self.log(f"已准备旧版资源文件（{copied} 个）")

    def asset_root(self, version: dict, game_dir) -> tuple[Path, str]:
        asset_index = version.get("assetIndex") or {}
        index_id = str(asset_index.get("id") or "legacy")
        if not asset_index:
            return self.assets_dir, index_id
        data = self.read_asset_index(version)
        if data.get("virtual"):
            dest = self.assets_dir / "virtual" / index_id
            self.materialize_assets(data, dest, stamp=asset_index.get("sha1") or "")
            return dest, index_id
        if data.get("map_to_resources"):
            dest = Path(game_dir) / "resources"
            self.materialize_assets(data, dest, stamp=asset_index.get("sha1") or "")
            return dest, index_id
        return self.assets_dir, index_id

    def extract_natives(self, version: dict, version_id: str) -> Path:
        libs = [l for l in resolve_libraries(version) if l["native"]]
        dest = self.versions_dir / version_id / f"{version_id}-natives-pymc"
        signature = hashlib.sha1(
            ("v2|" + "|".join(sorted(f"{l['path']}:{l['sha1']}" for l in libs))).encode()
        ).hexdigest()
        stamp_file = dest / ".pymc-natives"
        if dest.is_dir() and stamp_file.is_file():
            try:
                has_files = any(p.is_file() and p.name != ".pymc-natives" for p in dest.iterdir())
                if has_files and stamp_file.read_text(encoding="utf-8").strip() == signature:
                    return dest
            except OSError:
                pass
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        ensure_dir(dest)
        extracted = 0
        for lib in libs:
            src = self.libraries_dir / lib["path"]
            if not src.is_file():
                self.log(f"缺少本地库文件：{lib['path']}")
                continue
            excludes = tuple(lib.get("excludes") or ["META-INF/"])
            try:
                with zipfile.ZipFile(src) as zf:
                    for info in zf.infolist():
                        name = info.filename.replace("\\", "/")
                        if info.is_dir() or not name:
                            continue
                        if any(name.startswith(x) for x in excludes):
                            continue
                        filename = name.rsplit("/", 1)[-1]
                        if not filename or filename in (".", ".."):
                            continue
                        target = dest / filename
                        if target.exists():
                            continue
                        with zf.open(info) as rf, open(target, "wb") as wf:
                            shutil.copyfileobj(rf, wf)
                        extracted += 1
            except (zipfile.BadZipFile, OSError) as e:
                self.log(f"解压本地库失败：{lib['name']}（{e}）")
        try:
            stamp_file.write_text(signature, encoding="utf-8")
        except OSError:
            pass
        if extracted:
            self.log(f"已解压 {extracted} 个本地库文件")
        return dest

    def missing_libraries(self, version: dict) -> list[str]:
        missing = []
        for lib in resolve_libraries(version):
            if lib["native"]:
                continue
            if not (self.libraries_dir / lib["path"]).is_file():
                missing.append(lib["path"])
        return missing

    def version_of_client(self, version: dict) -> str:
        return version.get("clientVersion") or version.get("__root") or version.get("id") or ""
