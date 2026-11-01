from __future__ import annotations

from urllib.parse import quote

from .util import LauncherError, http_json

META = "https://meta.fabricmc.net/v2"


def list_loaders(mc_version: str) -> list[str]:
    data = http_json(f"{META}/versions/loader/{quote(mc_version, safe='')}", timeout=40)
    if not isinstance(data, list):
        raise LauncherError("Fabric 版本列表格式不正确")
    stable = []
    all_versions = []
    for item in data:
        loader = (item or {}).get("loader") or {}
        version = loader.get("version")
        if not version:
            continue
        all_versions.append(version)
        if loader.get("stable"):
            stable.append(version)
    return stable or all_versions


def install(repo, mc_version: str, loader_version: str | None = None,
            with_assets: bool = True, log=None) -> str:
    def emit(message):
        if log:
            try:
                log(message)
            except Exception:
                pass

    emit(f"准备安装 Fabric（Minecraft {mc_version}）…")
    repo.install(mc_version, with_assets=False)
    loaders = list_loaders(mc_version)
    if not loaders:
        raise LauncherError(f"没有找到适用于 {mc_version} 的 Fabric Loader")
    lv = loader_version or loaders[0]
    emit(f"下载 Fabric Loader {lv} 配置…")
    profile = http_json(
        f"{META}/versions/loader/{quote(mc_version, safe='')}/{quote(lv, safe='')}/profile/json",
        timeout=40,
    )
    if not isinstance(profile, dict) or not profile.get("libraries"):
        raise LauncherError("Fabric 配置下载失败")
    version_id = profile.get("id") or f"fabric-loader-{lv}-{mc_version}"
    profile["id"] = version_id
    profile.setdefault("inheritsFrom", mc_version)
    repo.write_version_json(version_id, profile)
    emit(f"下载 Fabric 依赖库…")
    repo.install(version_id, with_assets=with_assets)
    return version_id
