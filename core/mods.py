from __future__ import annotations

import html as _html
import json
import re
import shutil
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from .util import (
    UA,
    DownloadItem,
    LauncherError,
    Progress,
    download_item,
    ensure_dir,
    http_json,
    http_request,
    human_size,
)

DEFAULT_MODRINTH = "https://api.modrinth.com/v2"
MODRINTH = DEFAULT_MODRINTH
MODRINTH_MIRROR = "https://mod.mcimirror.top/modrinth/v2"
CURSEFORGE = "https://mod.mcimirror.top/curseforge/v1"
CF_GAME_ID = 432
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LOADERS = ("fabric", "forge", "neoforge", "quilt")
_CF_CACHE: dict[tuple, str] = {}


def mods_dir(game_dir) -> Path:
    return Path(game_dir) / "mods"


_MOD_META_CACHE: dict[tuple, dict | None] = {}


def _jar_text(zf: zipfile.ZipFile, names: tuple[str, ...]) -> str:
    for name in names:
        try:
            info = zf.getinfo(name)
        except KeyError:
            continue
        if info.file_size > 2 << 20:
            continue
        try:
            return zf.read(info).decode("utf-8", "replace")
        except Exception:
            continue
    return ""


def _version_tuple(value: str):
    parts = re.findall(r"\d+", str(value))
    if not parts:
        return None
    return tuple(int(x) for x in parts[:4])


def _vcompare(a, b) -> int:
    size = max(len(a), len(b))
    a = a + (0,) * (size - len(a))
    b = b + (0,) * (size - len(b))
    return (a > b) - (a < b)


def _range_ok(mc_version: str, spec) -> bool | None:
    if spec is None:
        return True
    if isinstance(spec, (list, tuple)):
        spec = " ".join(str(x) for x in spec)
    spec = str(spec).strip()
    if not spec or spec == "*":
        return True
    mc = _version_tuple(mc_version)
    if not mc:
        return None
    if "||" in spec:
        results = [_range_ok(mc_version, part.strip()) for part in spec.split("||")]
        if any(r is True for r in results):
            return True
        return False if all(r is False for r in results) else None
    maven = re.match(r"^\s*([\[\(])\s*([^,]*)\s*,?\s*([^\]\)]*)\s*([\]\)])\s*$", spec)
    if maven:
        lower, upper = maven.group(2).strip(), maven.group(3).strip()
        if lower:
            lv = _version_tuple(lower)
            if lv:
                cmp = _vcompare(mc, lv)
                if cmp < 0 or (cmp == 0 and maven.group(1) == "("):
                    return False
        if upper:
            uv = _version_tuple(upper)
            if uv:
                cmp = _vcompare(mc, uv)
                if cmp > 0 or (cmp == 0 and maven.group(4) == ")"):
                    return False
        return True
    tokens = re.findall(r"(>=|<=|>|<|=|~|\^)?\s*([0-9][0-9A-Za-z._-]*)", spec)
    if not tokens:
        return None
    known = False
    for op, version in tokens:
        vt = _version_tuple(version)
        if not vt:
            continue
        known = True
        cmp = _vcompare(mc, vt)
        if op == ">=" and cmp < 0:
            return False
        if op == "<=" and cmp > 0:
            return False
        if op == ">" and cmp <= 0:
            return False
        if op == "<" and cmp >= 0:
            return False
        if op in ("=", "") and len(tokens) == 1:
            prefix = tuple(mc[:len(vt)])
            if prefix != vt:
                return False
        if op in ("~", "^"):
            prefix = tuple(mc[:len(vt)])
            if prefix != vt:
                return False
    return True if known else None


def _parse_mod_meta(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _MOD_META_CACHE:
        return _MOD_META_CACHE[key]
    meta = None
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            raw = _jar_text(zf, ("fabric.mod.json",))
            if raw:
                data = json.loads(raw)
                depends = data.get("depends") or {}
                spec = depends.get("minecraft") if isinstance(depends, dict) else None
                meta = {"loader": "fabric", "mc": spec,
                        "name": data.get("name") or data.get("id") or path.stem}
            else:
                raw = _jar_text(zf, ("quilt.mod.json",))
                if raw:
                    data = json.loads(raw)
                    ql = data.get("quilt_loader") or {}
                    spec = None
                    for dep in ql.get("depends") or []:
                        if isinstance(dep, dict) and dep.get("id") == "minecraft":
                            spec = dep.get("versions") or dep.get("version")
                            break
                    meta = {"loader": "quilt", "mc": spec,
                            "name": ql.get("id") or path.stem}
            if meta is None:
                raw = _jar_text(zf, ("META-INF/neoforge.mods.toml",
                                     "META-INF/mods.toml"))
                if raw:
                    data = tomllib.loads(raw)
                    spec = None
                    dependencies = data.get("dependencies") or {}
                    if isinstance(dependencies, dict):
                        for entries in dependencies.values():
                            for dep in entries or []:
                                if str(dep.get("modId") or "").lower() == "minecraft":
                                    spec = dep.get("versionRange")
                                    break
                    loader = "forge"
                    if ("META-INF/neoforge.mods.toml" in names
                            or "neoforge" in raw.lower()):
                        loader = "neoforge"
                    meta = {"loader": loader, "mc": spec,
                            "name": data.get("mods", [{}])[0].get("displayName")
                            if isinstance(data.get("mods"), list) and data.get("mods")
                            else path.stem}
            if meta is None:
                raw = _jar_text(zf, ("mcmod.info",))
                if raw:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        data = data.get("modList") or data.get("modlist") or []
                    first = data[0] if isinstance(data, list) and data else {}
                    if isinstance(first, dict):
                        meta = {
                            "loader": "forge",
                            "mc": first.get("mcversion") or first.get("acceptedMinecraftVersions"),
                            "name": first.get("name") or first.get("modid") or path.stem,
                        }
    except Exception:
        meta = None
    _MOD_META_CACHE[key] = meta
    return meta


def _mod_compat(meta: dict | None, mc_version: str, loader: str):
    if not loader:
        return None, "未选择加载器"
    if loader == "vanilla":
        return False, "原版不支持模组"
    if not meta:
        return None, "未识别"
    mod_loader = meta.get("loader") or ""
    if mod_loader == "fabric":
        if loader not in ("fabric", "quilt"):
            return False, "需要 Fabric/Quilt"
    elif mod_loader == "quilt":
        if loader != "quilt":
            return False, "需要 Quilt"
    elif mod_loader in ("forge", "neoforge"):
        if loader != mod_loader:
            return False, f"需要 {mod_loader.title()}"
    else:
        return None, "未识别加载器"
    if mc_version:
        ok = _range_ok(mc_version, meta.get("mc"))
        if ok is False:
            return False, f"不支持 MC {mc_version}"
        if ok is None:
            return None, "版本范围未知"
    return True, ""


def list_mods(game_dir, mc_version: str = "", loader: str = "") -> list[dict]:
    folder = mods_dir(game_dir)
    result = []
    if not folder.is_dir():
        return result
    for path in folder.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name.lower().endswith(".jar.disabled"):
            display = name[:-len(".disabled")]
            enabled = False
        elif name.lower().endswith(".jar"):
            display = name
            enabled = True
        else:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        meta = _parse_mod_meta(path)
        compat, reason = _mod_compat(meta, mc_version, loader)
        compat_text = ("兼容" if compat is True
                       else "不兼容" if compat is False else "未知")
        result.append({
            "path": path,
            "name": display,
            "enabled": enabled,
            "size": size,
            "size_text": human_size(size),
            "compat": compat,
            "compat_text": compat_text,
            "compat_reason": reason,
        })
    result.sort(key=lambda m: (not m["enabled"], m["name"].lower()))
    return result


def toggle_mod(path) -> Path:
    path = Path(path)
    if path.name.lower().endswith(".jar.disabled"):
        target = path.with_name(path.name[:-len(".disabled")])
    elif path.name.lower().endswith(".jar"):
        target = path.with_name(path.name + ".disabled")
    else:
        raise LauncherError("只能操作 .jar 模组文件")
    if target.exists():
        raise LauncherError(f"目标文件已存在：{target.name}")
    path.rename(target)
    return target


def delete_mod(path):
    Path(path).unlink()


def add_mod_file(src, game_dir, overwrite: bool = False) -> Path:
    src = Path(src)
    if not src.is_file():
        raise LauncherError(f"文件不存在：{src}")
    folder = ensure_dir(mods_dir(game_dir))
    target = folder / src.name
    if target.exists() and not overwrite:
        raise LauncherError(f"模组目录里已有同名文件：{src.name}")
    shutil.copy2(src, target)
    return target


def detect_loader(repo, version_id: str) -> str:
    try:
        version = repo.merged(version_id)
    except Exception:
        version = {}
    main = str(version.get("mainClass") or "").lower()
    id_text = version_id.lower()
    libs = " ".join(str((l or {}).get("name") or "") for l in version.get("libraries") or []).lower()
    if "quilt" in main or "quilt" in libs or "quilt" in id_text:
        return "quilt"
    if "fabric" in main or "fabric" in libs or "fabric" in id_text:
        return "fabric"
    if "neoforge" in libs or "neoforge" in main or "neoforge" in id_text:
        return "neoforge"
    if "forge" in main or "minecraftforge" in libs or "forge" in id_text:
        return "forge"
    if "optifine" in main or "optifine" in libs or "optifine" in id_text:
        return "optifine"
    return "vanilla"


def mc_version_of(repo, version_id: str) -> str:
    try:
        version = repo.merged(version_id)
    except Exception:
        version = {}
    client_version = version.get("clientVersion")
    if client_version:
        return str(client_version)
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", version_id)
    if m:
        return m.group(1)
    return version_id


def _facets(mc_version: str, loader: str, project_type: str = "mod") -> list[list[str]]:
    facets = [[f"project_type:{project_type}"]]
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    if project_type == "mod" and loader in LOADERS:
        facets.append([f"categories:{loader}"])
    return facets


def configure_api(url: str = ""):
    global MODRINTH
    MODRINTH = (url or "").strip().rstrip("/") or DEFAULT_MODRINTH


def _filter_hits(hits: list, mc_version: str, loader: str,
                 project_type: str = "mod") -> list[dict]:
    result = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        versions = hit.get("versions") or []
        categories = hit.get("categories") or []
        if mc_version and versions and mc_version not in versions:
            continue
        if project_type == "mod" and loader and categories and loader not in categories:
            continue
        result.append(hit)
    return result


def _filter_versions(versions: list, mc_version: str, loader: str,
                     project_type: str = "mod") -> list[dict]:
    result = []
    for version in versions:
        if not isinstance(version, dict):
            continue
        game_versions = version.get("game_versions") or []
        loaders = version.get("loaders") or []
        if mc_version and game_versions and mc_version not in game_versions:
            continue
        if project_type == "mod" and loader and loaders and loader not in loaders:
            continue
        result.append(version)
    return result


def _modrinth_json(path: str, params: dict | None = None,
                   list_filter=None, hit_filter=None):
    query = "?" + urlencode(params) if params else ""
    last_err: Exception | None = None
    bases = []
    for base in (MODRINTH, MODRINTH_MIRROR, DEFAULT_MODRINTH):
        if base and base not in bases:
            bases.append(base)
    for base in bases:
        try:
            data = http_json(base + path + query,
                             headers={"User-Agent": UA, "Accept": "application/json"},
                             timeout=40)
        except Exception as e:
            last_err = e
            continue
        if isinstance(data, list) and list_filter:
            data = list_filter(data)
        if isinstance(data, dict) and hit_filter and isinstance(data.get("hits"), list):
            data["hits"] = hit_filter(data["hits"])
        return data
    raise LauncherError(f"Modrinth 请求失败：{last_err}") from None


def _tag_text(match) -> str:
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _scrape_search(query: str, mc_version: str, loader: str, limit: int,
                   project_type: str = "mod") -> list[dict]:
    params = {"q": query or ""}
    if mc_version:
        params["g"] = mc_version
    if project_type == "mod" and loader:
        params["l"] = loader
    route = {"mod": "mods", "resourcepack": "resourcepacks",
             "shader": "shaders", "datapack": "datapacks",
             "modpack": "modpacks"}.get(project_type, "mods")
    url = f"https://modrinth.com/{route}?" + urlencode(params)
    raw = http_request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
                       timeout=40)
    text = raw.decode("utf-8", "replace")
    chunks = re.split(
        r'<div class="smart-clickable smart-clickable--has-clickable"', text)[1:]
    hits = []
    seen = set()
    for chunk in chunks:
        slug_match = re.search(
            r'href="/(?:mod|resourcepack|shader|datapack|modpack)/([^"?#]+)"',
            chunk)
        pid_match = re.search(r'cdn\.modrinth\.com/data/([A-Za-z0-9]+)/', chunk)
        if not slug_match or not pid_match:
            continue
        project_id = pid_match.group(1)
        if project_id in seen:
            continue
        seen.add(project_id)
        title = _tag_text(re.search(r'project-card-title[^>]*>(.*?)</span>', chunk, re.S))
        summary = _tag_text(
            re.search(r'project-card-summary[^>]*>(.*?)</(?:p|div|span)>', chunk, re.S))
        author = _tag_text(re.search(r'\bby\s*<a[^>]*>(.*?)</a>', chunk, re.S))
        hits.append({
            "project_id": project_id,
            "slug": slug_match.group(1),
            "title": title or slug_match.group(1),
            "description": summary,
            "author": author,
            "downloads": 0,
        })
        if len(hits) >= limit:
            break
    if not hits:
        raise LauncherError("Modrinth 网页抓取失败，请检查网络或稍后重试。")
    return hits


def search_modrinth(query: str, mc_version: str = "", loader: str = "",
                    limit: int = 20, offset: int = 0,
                    index: str = "relevance", project_type: str = "mod") -> list[dict]:
    params = {
        "query": query or "",
        "facets": json.dumps(_facets(mc_version, loader, project_type)),
        "limit": str(limit),
        "offset": str(offset),
        "index": index,
    }
    try:
        data = _modrinth_json(
            "/search", params,
            hit_filter=lambda hits: _filter_hits(
                hits, mc_version, loader, project_type))
        hits = data.get("hits") if isinstance(data, dict) else None
        return hits if isinstance(hits, list) else []
    except Exception:
        return _scrape_search(query, mc_version, loader, limit, project_type)


def compatible_versions(project_id: str, mc_version: str, loader: str,
                        project_type: str = "mod") -> list[dict]:
    params = {}
    if mc_version:
        params["game_versions"] = json.dumps([mc_version])
    if project_type == "mod" and loader in LOADERS:
        params["loaders"] = json.dumps([loader])
    data = _modrinth_json(
        f"/project/{project_id}/version", params or None,
        list_filter=lambda versions: _filter_versions(
            versions, mc_version, loader, project_type))
    return data if isinstance(data, list) else []


def _curseforge_file_url(project_slug: str, mc_version: str,
                         loader: str, sha1: str) -> str:
    if not project_slug or not sha1:
        return ""
    key = (project_slug, mc_version, loader, sha1.lower())
    if key in _CF_CACHE:
        return _CF_CACHE[key]
    url = ""
    try:
        params = {"gameId": str(CF_GAME_ID), "slug": project_slug, "pageSize": "5"}
        data = http_json(CURSEFORGE + "/mods/search?" + urlencode(params),
                         headers={"User-Agent": UA, "Accept": "application/json"},
                         timeout=12, retries=0)
        mods = data.get("data") if isinstance(data, dict) else data
        mod = next((m for m in mods or [] if m.get("slug") == project_slug), None)
        if mod:
            params = {"pageSize": "50"}
            if mc_version:
                params["gameVersion"] = mc_version
            data = http_json(
                f"{CURSEFORGE}/mods/{mod.get('id')}/files?" + urlencode(params),
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=12, retries=0)
            files = data.get("data") if isinstance(data, dict) else data
            wanted = sha1.lower()
            for item in files or []:
                for digest in item.get("hashes") or []:
                    if (str(digest.get("algo")) == "1"
                            and str(digest.get("value") or "").lower() == wanted):
                        url = str(item.get("downloadUrl") or "")
                        break
                if url:
                    break
    except Exception:
        url = ""
    if url:
        _CF_CACHE[key] = url
    return url


def _primary_file(version: dict):
    files = version.get("files") or []
    return next((f for f in files if f.get("primary")), None) or (files[0] if files else None)


def _install_file(file: dict, version: dict, game_dir,
                  progress_cb=None, cancel=None, project_slug: str = "",
                  mc_version: str = "", loader: str = "",
                  project_type: str = "mod") -> dict:
    folder_name = {"mod": "mods", "resourcepack": "resourcepacks",
                   "shader": "shaderpacks", "datapack": "datapacks"}.get(
                       project_type, "mods")
    label_name = {"mod": "模组", "resourcepack": "资源包",
                  "shader": "光影", "datapack": "数据包"}.get(
                      project_type, "内容")
    filename = Path(str(file.get("filename") or "content.bin")).name
    dest = ensure_dir(Path(game_dir) / folder_name) / filename
    if dest.exists():
        raise LauncherError(f"{label_name}目录中已存在：{filename}")
    sha1 = (file.get("hashes") or {}).get("sha1") or ""
    official_url = str(file.get("url") or "")
    cf_url = _curseforge_file_url(project_slug, mc_version, loader, sha1)
    item = DownloadItem(
        url=cf_url or official_url,
        dest=dest,
        sha1=sha1,
        size=int(file.get("size") or 0),
        label=filename,
        kind=project_type,
        alternates=(official_url,) if cf_url and official_url else (),
    )
    progress = Progress(progress_cb, f"{label_name} {filename}")
    download_item(item, progress, cancel, timeout=120)
    progress.emit(True)
    return {
        "path": dest,
        "filename": filename,
        "version": version.get("version_number") or "",
        "name": version.get("name") or filename,
        "dependencies": version.get("dependencies") or [],
        "kind": project_type,
    }


def install_project(project_id: str, mc_version: str, loader: str, game_dir,
                    progress_cb=None, cancel=None) -> dict:
    if loader not in LOADERS:
        raise LauncherError("原版实例不能安装模组，请先创建 Fabric / Forge 实例。")
    versions = compatible_versions(project_id, mc_version, loader)
    if not versions:
        raise LauncherError(f"没有找到兼容的模组版本（MC {mc_version} / {loader}）")
    slug = project_id
    try:
        project = _modrinth_json(f"/project/{project_id}")
        if isinstance(project, dict):
            slug = project.get("slug") or slug
    except Exception:
        pass
    for version in versions:
        file = _primary_file(version)
        if file:
            return _install_file(file, version, game_dir, progress_cb, cancel,
                                 project_slug=slug, mc_version=mc_version,
                                 loader=loader)
    raise LauncherError("该模组没有可下载的文件")


def install_content(project_id: str, project_type: str, mc_version: str,
                    loader: str, game_dir, progress_cb=None, cancel=None) -> dict:
    if project_type not in ("mod", "resourcepack", "shader"):
        raise LauncherError("不支持的资源类型。")
    if project_type == "mod" and loader not in LOADERS:
        raise LauncherError("原版实例不能安装模组，请先创建 Fabric / Forge 实例。")
    versions = compatible_versions(project_id, mc_version, loader, project_type)
    if not versions:
        raise LauncherError(f"没有找到兼容版本（MC {mc_version}）")
    slug = project_id
    try:
        project = _modrinth_json(f"/project/{project_id}")
        if isinstance(project, dict):
            slug = project.get("slug") or slug
    except Exception:
        pass
    for version in versions:
        file = _primary_file(version)
        if file:
            return _install_file(file, version, game_dir, progress_cb, cancel,
                                 project_slug=slug, mc_version=mc_version,
                                 loader=loader, project_type=project_type)
    raise LauncherError("该项目没有可下载的文件")


def _mod_file_installed(game_dir, filename: str) -> bool:
    if not filename:
        return False
    folder = mods_dir(game_dir)
    return ((folder / filename).is_file()
            or (folder / (filename + ".disabled")).is_file())


def describe_dependencies(dependencies: list, game_dir,
                          mc_version: str = "", loader: str = "") -> list[dict]:
    result = []
    seen = set()
    for dep in dependencies or []:
        if not isinstance(dep, dict):
            continue
        if (dep.get("dependency_type") or "required") != "required":
            continue
        version_id = str(dep.get("version_id") or "")
        project_id = str(dep.get("project_id") or "")
        version = None
        if version_id:
            try:
                version = _modrinth_json(f"/version/{version_id}")
            except Exception:
                version = None
        file = _primary_file(version) if isinstance(version, dict) else None
        if file:
            filename = Path(str(file.get("filename") or "")).name
            if _mod_file_installed(game_dir, filename):
                continue
        pid = str((version or {}).get("project_id") or project_id)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        name = pid
        try:
            project = _modrinth_json(f"/project/{pid}")
            if isinstance(project, dict):
                name = project.get("title") or project.get("slug") or name
        except Exception:
            pass
        result.append({
            "project_id": pid,
            "version_id": version_id,
            "name": name,
            "filename": Path(str((file or {}).get("filename") or "")).name,
        })
    return result


def install_dependencies(items: list[dict], game_dir, mc_version: str = "",
                         loader: str = "", progress_cb=None, cancel=None) -> dict:
    installed = []
    visited = set()

    def install(version_id: str, project_id: str = ""):
        version_id = str(version_id or "")
        if not version_id or version_id in visited:
            return
        visited.add(version_id)
        try:
            version = _modrinth_json(f"/version/{version_id}")
        except Exception:
            return
        if not isinstance(version, dict):
            return
        file = _primary_file(version)
        if file:
            filename = Path(str(file.get("filename") or "mod.jar")).name
            dest = mods_dir(game_dir) / filename
            if not _mod_file_installed(game_dir, filename):
                pid = str(version.get("project_id") or project_id)
                slug = pid
                try:
                    project = _modrinth_json(f"/project/{pid}")
                    if isinstance(project, dict):
                        slug = project.get("slug") or slug
                except Exception:
                    pass
                _install_file(file, version, game_dir, progress_cb, cancel,
                              project_slug=slug, mc_version=mc_version,
                              loader=loader)
                installed.append(filename)
        for dep in version.get("dependencies") or []:
            if not isinstance(dep, dict):
                continue
            if (dep.get("dependency_type") or "required") != "required":
                continue
            install(dep.get("version_id"), dep.get("project_id"))

    for item in items or []:
        install(item.get("version_id"), item.get("project_id"))
    return {"installed": installed, "count": len(installed)}


def _direct_filename(url: str) -> str:
    return Path(unquote(urlsplit(url).path)).name


def _install_direct(url: str, game_dir, progress_cb=None, cancel=None) -> dict:
    filename = _direct_filename(url) or "mod.jar"
    if not filename.lower().endswith(".jar"):
        filename += ".jar"
    dest = ensure_dir(mods_dir(game_dir)) / filename
    if dest.exists():
        raise LauncherError(f"模组目录中已存在：{filename}")
    item = DownloadItem(url=url, dest=dest, label=filename, kind="mod")
    progress = Progress(progress_cb, f"模组 {filename}")
    download_item(item, progress, cancel, timeout=120)
    progress.emit(True)
    return {
        "path": dest,
        "filename": filename,
        "version": "",
        "name": filename,
        "dependencies": [],
    }


def _modrinth_slug(url: str) -> str:
    match = re.search(r"/(?:mod|project)/([^/?#]+)", urlsplit(url).path)
    return unquote(match.group(1)) if match else ""


def _modrinth_version_id(url: str) -> str:
    match = re.search(r"/version/([A-Za-z0-9]+)", urlsplit(url).path)
    return match.group(1) if match else ""


def install_from_url(url: str, mc_version: str, loader: str, game_dir,
                     progress_cb=None, cancel=None) -> dict:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise LauncherError("请输入以 http:// 或 https:// 开头的链接。")
    if _is_direct_jar(url):
        return _install_direct(url, game_dir, progress_cb, cancel)

    host = (urlsplit(url).hostname or "").lower()
    if host.endswith("modrinth.com"):
        version_id = _modrinth_version_id(url)
        if version_id:
            version = _modrinth_json(f"/version/{version_id}")
            file = _primary_file(version) if isinstance(version, dict) else None
            if not file:
                raise LauncherError("该 Modrinth 版本没有可下载的文件。")
            return _install_file(
                file, version, game_dir, progress_cb, cancel,
                project_slug=_modrinth_slug(url) or str(version.get("project_id") or ""),
                mc_version=mc_version, loader=loader)
        slug = _modrinth_slug(url)
        if not slug:
            raise LauncherError("无法从链接中识别 Modrinth 项目。")
        if loader in LOADERS and mc_version:
            return install_project(slug, mc_version, loader, game_dir, progress_cb, cancel)
        versions = _modrinth_json(f"/project/{slug}/version")
        if isinstance(versions, list):
            for version in versions:
                file = _primary_file(version)
                if file:
                    return _install_file(file, version, game_dir, progress_cb, cancel,
                                         project_slug=slug, mc_version=mc_version,
                                         loader=loader)
        raise LauncherError("该 Modrinth 项目没有可下载的文件。")

    if host.endswith("curseforge.com"):
        raise LauncherError(
            "CurseForge 页面启用了 Cloudflare 防护，无法自动抓取；"
            "请复制文件的 .jar 直链后重试。")
    raise LauncherError("暂不支持该链接，请提供 Modrinth 链接或 .jar 直链。")


def _is_direct_jar(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".jar")
