from __future__ import annotations

import base64
import json
import uuid as uuidlib
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .config import safe_folder_name
from .util import (
    UA,
    DownloadItem,
    HttpError,
    LauncherError,
    Progress,
    download_item,
    ensure_dir,
    http_json,
    http_request,
)

SKINLIB_LIST = "https://littleskin.cn/skinlib/list"
SKINLIB_RAW = "https://littleskin.cn/raw/{tid}"
SKINLIB_PREVIEW = "https://littleskin.cn/preview/{tid}?height=360&png"
MICROSOFT_SKIN_URL = "https://api.minecraftservices.com/minecraft/profile/skins"


class SkinAuthError(LauncherError):
    """上传时令牌失效；刷新令牌后可以重试。"""


def search_skins(keyword: str = "", sort: str = "likes", page: int = 1) -> list[dict]:
    params = {"filter": "skin", "sort": sort, "page": str(max(1, page))}
    if keyword:
        params["keyword"] = keyword
    url = SKINLIB_LIST + "?" + urlencode(params)
    data = http_json(url, headers={"User-Agent": UA, "Accept": "application/json"},
                     timeout=40)
    skins = data.get("data") if isinstance(data, dict) else data
    return skins if isinstance(skins, list) else []


def preview_url(tid) -> str:
    return SKINLIB_PREVIEW.format(tid=tid)


def _local_latest(game_dir) -> Path | None:
    folder = Path(game_dir) / "skins"
    if not folder.is_dir():
        return None
    files = []
    for path in folder.glob("*.png"):
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            pass
    return max(files)[1] if files else None


def _skin_from_profile(profile: dict) -> tuple[str, str]:
    for prop in profile.get("properties") or []:
        if not isinstance(prop, dict) or prop.get("name") != "textures":
            continue
        value = str(prop.get("value") or "")
        try:
            raw = base64.b64decode(value + "=" * (-len(value) % 4))
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        skin = ((data.get("textures") or {}).get("SKIN") or {})
        url = skin.get("url") or ""
        model = (skin.get("metadata") or {}).get("model") or ""
        if url:
            return str(url), str(model)
    return "", ""


def skin_model(path) -> str:
    sidecar = Path(str(path) + ".json")
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return str(data.get("model") or "")
    except Exception:
        return ""


def cached_skin_path(account, cache_dir) -> Path | None:
    if account is None or account.type == "offline" or not account.uuid:
        return None
    folder = ensure_dir(Path(cache_dir) / "skins")
    return folder / f"{safe_folder_name(account.name or account.uuid, 'skin')}-{account.uuid}.png"


CSL_TEXTURE_DIRS = {"skin": "skins", "cape": "capes", "elytra": "elytras"}


def csl_texture_path(game_dir, name: str, kind: str = "skin") -> Path:
    """CustomSkinLoader LocalSkin 的本地材质路径。"""
    folder = CSL_TEXTURE_DIRS.get(kind)
    if folder is None:
        raise ValueError(f"unknown CSL texture kind: {kind}")
    return (Path(game_dir) / "CustomSkinLoader" / "LocalSkin"
            / folder / f"{name}.png")


def csl_available(game_dir) -> bool:
    """检测实例是否安装了 CustomSkinLoader。"""
    base = Path(game_dir)
    folder = base / "CustomSkinLoader"
    try:
        if folder.is_dir() and any(folder.glob("*.json")):
            return True
    except OSError:
        pass
    try:
        return any("customskinloader" in p.name.lower()
                   for p in (base / "mods").glob("*.jar"))
    except OSError:
        return False


def apply_csl_texture(game_dir, name: str, kind: str, src) -> Path:
    data = Path(src).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise LauncherError("材质文件不是有效的 PNG。")
    dest = csl_texture_path(game_dir, name, kind)
    ensure_dir(dest.parent)
    dest.write_bytes(data)
    return dest


def remove_csl_texture(game_dir, name: str, kind: str) -> bool:
    dest = csl_texture_path(game_dir, name, kind)
    try:
        dest.unlink()
        return True
    except FileNotFoundError:
        return False


def store_cached_skin(account, path, cache_dir, model: str = "") -> Path | None:
    """把刚上传的皮肤写入本地缓存，查看器可立即预览。"""
    dest = cached_skin_path(account, cache_dir)
    if dest is None:
        return None
    try:
        dest.write_bytes(Path(path).read_bytes())
        sidecar = Path(str(dest) + ".json")
        if model:
            sidecar.write_text(json.dumps({"model": model}), encoding="utf-8")
        else:
            try:
                sidecar.unlink()
            except OSError:
                pass
    except OSError:
        return None
    return dest


def current_skin(account, game_dir, cache_dir, refresh: bool = False) -> Path | None:
    local = _local_latest(game_dir)
    if account is None or account.type == "offline":
        return local
    if not account.uuid:
        return local
    cache = cached_skin_path(account, cache_dir)
    if cache is None:
        return local
    if cache.is_file() and not refresh:
        return cache
    profile_url = ""
    if account.type == "microsoft":
        uuid = account.uuid.replace("-", "")
        profile_url = (f"https://sessionserver.mojang.com/session/minecraft/"
                       f"profile/{uuid}")
    elif account.type == "external" and account.server:
        uuid = account.uuid.replace("-", "")
        profile_url = (f"{account.server.rstrip('/')}/sessionserver/session/"
                       f"minecraft/profile/{uuid}")
    if profile_url:
        try:
            profile = http_json(profile_url, headers={"User-Agent": UA},
                                timeout=30)
            url, model = _skin_from_profile(profile)
            if url:
                raw = http_request(url, headers={"User-Agent": UA}, timeout=40)
                if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                    cache.write_bytes(raw)
                    try:
                        Path(str(cache) + ".json").write_text(
                            json.dumps({"model": model}), encoding="utf-8")
                    except OSError:
                        pass
                    return cache
        except Exception:
            pass
    if cache.is_file():
        return cache
    return local


def _unique_png(folder: Path, name: str) -> Path:
    base = safe_folder_name(name, "skin")
    candidate = folder / f"{base}.png"
    index = 2
    while candidate.exists():
        candidate = folder / f"{base} ({index}).png"
        index += 1
    return candidate


def download_skin(tid, name: str, game_dir, progress_cb=None, cancel=None,
                  model: str = "") -> dict:
    folder = ensure_dir(Path(game_dir) / "skins")
    dest = _unique_png(folder, name or f"skin-{tid}")
    item = DownloadItem(url=SKINLIB_RAW.format(tid=tid), dest=dest,
                        label=dest.name, kind="skin")
    progress = Progress(progress_cb, f"皮肤 {dest.name}")
    download_item(item, progress, cancel, timeout=60)
    try:
        with open(dest, "rb") as f:
            magic = f.read(8)
        if magic != b"\x89PNG\r\n\x1a\n":
            raise LauncherError("下载的皮肤不是有效的 PNG 文件。")
    except Exception:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    if model:
        try:
            Path(str(dest) + ".json").write_text(
                json.dumps({"model": model}), encoding="utf-8")
        except OSError:
            pass
    progress.emit(True)
    return {
        "path": dest,
        "filename": dest.name,
        "version": "",
        "name": name or dest.stem,
        "dependencies": [],
        "kind": "skin",
    }


# ---------------------------------------------------------------- 上传皮肤
def _error_text(error: HttpError) -> str:
    body = error.json()
    for key in ("errorMessage", "message", "error", "cause"):
        value = body.get(key)
        if value:
            return str(value)
    return f"HTTP {error.status}"


def _multipart(fields: dict, data: bytes, filename: str = "skin.png"):
    boundary = "----PyMCLauncher" + uuidlib.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8"))
    chunks.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode("utf-8"))
    chunks.append(data)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _site_root(server: str) -> str:
    site = server.rstrip("/")
    for suffix in ("/api/yggdrasil", "/api/v2", "/api"):
        if site.endswith(suffix):
            return site[:-len(suffix)]
    return site


def skin_upload_urls(server: str, uuid_hex: str) -> list[str]:
    """Yggdrasil 皮肤上传接口在 Blessing Skin / LittleSkin 上路径不完全一致。"""
    server = (server or "").rstrip("/")
    site = _site_root(server)
    candidates = [
        f"{server}/api/user/profile/{uuid_hex}/skin",
        f"{server}/api/v2/user/profile/{uuid_hex}/skin",
        f"{site}/api/user/profile/{uuid_hex}/skin",
        f"{site}/api/v2/user/profile/{uuid_hex}/skin",
    ]
    result = []
    for url in candidates:
        if url not in result:
            result.append(url)
    return result


def _upload_response_error(raw: bytes) -> str:
    """个别服务器会用 2xx 返回错误 JSON，这里补一层检查。"""
    if not raw:
        return ""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return ""
    if isinstance(data, dict):
        for key in ("errorMessage", "error"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def _upload_microsoft(account, data: bytes, model: str) -> str:
    variant = "slim" if model == "slim" else "classic"
    body, content_type = _multipart({"variant": variant}, data)
    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Content-Type": content_type,
        "Accept": "application/json",
    }
    try:
        raw = http_request(MICROSOFT_SKIN_URL, method="POST", data=body,
                           headers=headers, timeout=90, retries=0)
    except HttpError as e:
        if e.status == 401:
            raise SkinAuthError(
                "微软登录已失效，请重新登录后再上传。") from None
        if e.status == 429:
            raise LauncherError(
                "微软限制上传频率，请等待几分钟后再试。") from None
        if e.status == 400:
            raise LauncherError(
                "微软拒绝了这张皮肤（尺寸或格式不符合要求）。\n"
                + _error_text(e)) from None
        if e.status == 403:
            raise LauncherError(
                "该微软账号没有上传皮肤的权限，请确认已购买 Minecraft Java 版。\n"
                + _error_text(e)) from None
        raise LauncherError(f"微软皮肤上传失败：{_error_text(e)}") from None
    error = _upload_response_error(raw)
    if error:
        raise LauncherError(f"微软皮肤上传失败：{error}")
    return "皮肤已上传到微软账号，重新进入游戏或刷新皮肤后生效。"


def _upload_external(account, data: bytes, model: str) -> str:
    uuid_hex = account.uuid.replace("-", "")
    fields = {"model": "slim" if model == "slim" else ""}
    body, content_type = _multipart(fields, data)
    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Content-Type": content_type,
        "Accept": "application/json",
    }
    last_error = None
    for url in skin_upload_urls(account.server, uuid_hex):
        for method in ("PUT", "POST"):
            try:
                raw = http_request(url, method=method, data=body,
                                   headers=headers, timeout=90, retries=0)
                error = _upload_response_error(raw)
                if error:
                    raise LauncherError(f"皮肤上传失败：{error}")
                return "皮肤已上传到皮肤站，重新进入游戏后生效。"
            except HttpError as e:
                last_error = e
                if e.status == 405:
                    continue
                if e.status == 404:
                    break
                if e.status == 401:
                    raise SkinAuthError(
                        "外置登录已失效，请在账户里重新登录后再上传。") from None
                if e.status == 403:
                    raise LauncherError(
                        "皮肤站不允许为该角色上传皮肤：\n"
                        + _error_text(e)) from None
                raise LauncherError(
                    f"皮肤上传失败：{_error_text(e)}") from None
    if last_error is not None:
        raise LauncherError(
            "没有找到皮肤站的上传接口，可能该皮肤站不支持启动器上传。\n"
            "请到皮肤站网页手动上传。") from None
    raise LauncherError("皮肤站地址无效，请重新登录外置账户。")


def upload_skin(account, path, model: str = "") -> str:
    """把本地皮肤 PNG 上传到当前账号（微软正版或外置皮肤站）。"""
    data = Path(path).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise LauncherError("皮肤文件不是有效的 PNG。")
    if account is None:
        raise LauncherError("当前没有登录账户。")
    if account.is_microsoft:
        return _upload_microsoft(account, data, model)
    if account.is_external:
        if not account.server:
            raise LauncherError("外置账户缺少服务器地址，请重新登录。")
        return _upload_external(account, data, model)
    raise LauncherError(
        "离线账户没有皮肤服务器，无法上传。\n"
        "可以保存到实例 skins，或登录微软正版 / 外置账号后再上传。")
