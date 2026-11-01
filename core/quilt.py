from __future__ import annotations

import re
from urllib.parse import quote

from .util import LauncherError, http_json

META = "https://meta.quiltmc.org/v3"


def _version_key(version: str):
    numbers = tuple(int(x) for x in re.findall(r"\d+", version)[:5])
    stable = 1 if not re.search(r"beta|alpha|rc|pre", version, re.I) else 0
    return (stable, numbers)


def list_loaders(mc_version: str) -> list[str]:
    data = http_json(
        f"{META}/versions/loader/{quote(mc_version, safe='')}",
        timeout=40)
    if not isinstance(data, list):
        raise LauncherError("Quilt 版本列表格式不正确")
    versions = []
    for item in data:
        loader = (item or {}).get("loader") or {}
        version = loader.get("version")
        if version and version not in versions:
            versions.append(str(version))
    versions.sort(key=_version_key, reverse=True)
    return versions


def install(repo, mc_version: str, loader_version: str | None = None,
            with_assets: bool = True, log=None) -> str:
    def emit(message):
        if log:
            try:
                log(message)
            except Exception:
                pass

    emit(f"准备安装 Quilt（Minecraft {mc_version}）…")
    repo.install(mc_version, with_assets=False)
    loaders = list_loaders(mc_version)
    if not loaders:
        raise LauncherError(f"没有找到适用于 {mc_version} 的 Quilt Loader")
    lv = loader_version or loaders[0]
    emit(f"下载 Quilt Loader {lv} 配置…")
    profile = http_json(
        f"{META}/versions/loader/{quote(mc_version, safe='')}/"
        f"{quote(lv, safe='')}/profile/json",
        timeout=40,
    )
    if not isinstance(profile, dict) or not profile.get("libraries"):
        raise LauncherError("Quilt 配置下载失败")
    version_id = profile.get("id") or f"quilt-loader-{lv}-{mc_version}"
    profile["id"] = version_id
    profile.setdefault("inheritsFrom", mc_version)
    repo.write_version_json(version_id, profile)
    emit("下载 Quilt 依赖库…")
    repo.install(version_id, with_assets=with_assets)
    return version_id
