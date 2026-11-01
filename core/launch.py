from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .auth import Account
from .java import choose_java
from .util import LauncherError, ensure_dir
from .version import Repo, resolve_libraries

DEFAULT_JVM = [
    "-Djava.library.path=${natives_directory}",
    "-Dminecraft.launcher.brand=${launcher_name}",
    "-Dminecraft.launcher.version=${launcher_version}",
    "-cp",
    "${classpath}",
]

_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


@dataclass
class LaunchOptions:
    memory_mb: int = 4096
    jvm_args: str = ""
    java_path: str = ""
    client_id: str = ""
    allow_newer_java: bool = True
    width: int = 0
    height: int = 0


def required_java(version: dict) -> int:
    java_version = version.get("javaVersion") or {}
    try:
        major = int(java_version.get("majorVersion") or 0)
    except (TypeError, ValueError):
        major = 0
    if major:
        return major
    release = str(version.get("releaseTime") or "")
    year = 0
    m = re.match(r"(\d{4})", release)
    if m:
        year = int(m.group(1))
    if year >= 2025:
        return 21
    if year >= 2021:
        return 17
    return 8


def _expand(entries, features: dict, substitute) -> list[str]:
    from .util import rules_allow

    result: list[str] = []
    for entry in entries or []:
        if isinstance(entry, str):
            result.append(substitute(entry))
        elif isinstance(entry, dict):
            if rules_allow(entry.get("rules"), features):
                value = entry.get("value")
                if isinstance(value, str):
                    result.append(substitute(value))
                elif isinstance(value, list):
                    result.extend(substitute(str(v)) for v in value)
    return result


def build_command(repo: Repo, version_id: str, game_dir, account: Account,
                  options: LaunchOptions | None = None) -> dict:
    options = options or LaunchOptions()
    game_dir = ensure_dir(game_dir)
    version = repo.merged(version_id)
    client_jar = repo.client_jar_path(version)
    if not client_jar.is_file():
        raise LauncherError(f"客户端文件缺失：{client_jar.name}\n请到下载页重新安装该版本。")

    classpath: list[str] = []
    seen = set()
    missing = []
    for lib in resolve_libraries(version):
        if lib["native"]:
            continue
        path = repo.libraries_dir / lib["path"]
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        classpath.append(str(path))
        if not path.is_file():
            missing.append(lib["path"])
    if missing:
        preview = "\n".join("  " + m for m in missing[:6])
        more = f"\n  …共 {len(missing)} 个" if len(missing) > 6 else ""
        raise LauncherError(f"缺少 {len(missing)} 个依赖库文件：\n{preview}{more}\n请到下载页重新安装该版本。")
    classpath.append(str(client_jar))

    natives_dir = repo.extract_natives(version, version_id)
    assets_root, asset_index = repo.asset_root(version, game_dir)
    log_config = repo.log_config_path(version)
    java_info = choose_java(required_java(version), options.java_path, root=repo.root,
                            allow_higher=options.allow_newer_java)

    features = {
        "is_demo_user": False,
        "has_custom_resolution": bool(options.width and options.height),
        "has_quick_plays_support": False,
        "is_quick_play_singleplayer": False,
        "is_quick_play_multiplayer": False,
        "is_quick_play_realms": False,
    }
    access_token = account.access_token or "0"
    uuid_str = account.uuid or ""
    replacements = {
        "auth_player_name": account.name,
        "auth_uuid": uuid_str,
        "auth_access_token": access_token,
        "auth_session": f"token:{access_token}:{uuid_str}",
        "auth_xuid": account.xuid or "",
        "clientid": options.client_id or "",
        "user_type": ("msa" if account.is_microsoft
                      else "mojang" if account.is_external else "legacy"),
        "version_name": version_id,
        "version_type": version.get("type") or "release",
        "game_directory": str(game_dir),
        "assets_root": str(assets_root),
        "assets_index_name": asset_index,
        "natives_directory": str(natives_dir),
        "classpath": os.pathsep.join(classpath),
        "classpath_separator": os.pathsep,
        "library_directory": str(repo.libraries_dir),
        "launcher_name": "PyMCLauncher",
        "launcher_version": "1.0",
        "resolution_width": str(options.width or 0),
        "resolution_height": str(options.height or 0),
        "path": str(log_config or ""),
    }

    def substitute(text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: str(replacements.get(m.group(1), "")), text)

    arguments = version.get("arguments") or {}
    jvm_entries = arguments.get("jvm") or []
    if jvm_entries:
        jvm_args = _expand(jvm_entries, features, substitute)
    else:
        jvm_args = [substitute(x) for x in DEFAULT_JVM]

    logging_arg = ((version.get("logging") or {}).get("client") or {}).get("argument")
    if logging_arg and log_config:
        jvm_args.append(substitute(logging_arg))

    memory = int(options.memory_mb or 4096)
    memory = max(512, min(memory, 65536))
    jvm_args = ["-Dfile.encoding=UTF-8", "-Dlog4j2.formatMsgNoLookups=true"] + jvm_args
    native_path = f"-Djava.library.path={natives_dir}"
    jvm_args = [native_path if arg.startswith("-Djava.library.path=") else arg
                for arg in jvm_args]
    if not any(arg.startswith("-Djava.library.path=") for arg in jvm_args):
        jvm_args.insert(0, native_path)
    if account.is_external:
        if not account.server:
            raise LauncherError("外置账户缺少服务器地址，请重新登录。")
        from .authlib import ensure_authlib_injector
        jar = ensure_authlib_injector()
        jvm_args.insert(0, f"-javaagent:{jar}={account.server}")
    jvm_args += [f"-Xms{min(512, memory)}m", f"-Xmx{memory}m"]
    if options.jvm_args.strip():
        try:
            jvm_args += shlex.split(options.jvm_args)
        except ValueError as e:
            raise LauncherError(f"JVM 参数格式不正确：{e}") from None

    if arguments.get("game"):
        game_args = _expand(arguments.get("game"), features, substitute)
    elif version.get("minecraftArguments"):
        try:
            legacy = shlex.split(version["minecraftArguments"])
        except ValueError:
            legacy = version["minecraftArguments"].split()
        game_args = [substitute(x) for x in legacy]
    else:
        game_args = [
            "--username", account.name,
            "--version", version_id,
            "--gameDir", str(game_dir),
            "--assetsDir", str(assets_root),
            "--assetIndex", asset_index,
            "--uuid", uuid_str,
            "--accessToken", access_token,
            "--userType", replacements["user_type"],
            "--versionType", replacements["version_type"],
        ]

    main_class = version.get("mainClass")
    if not main_class:
        raise LauncherError("版本信息缺少主类（mainClass）")

    cmd = [str(java_info.path)] + jvm_args + [main_class] + game_args
    env = os.environ.copy()
    env.setdefault("LIBGL_ALWAYS_SOFTWARE", "0")
    return {
        "cmd": cmd,
        "cwd": str(game_dir),
        "env": env,
        "java": str(java_info.path),
        "java_info": java_info,
        "version": version_id,
        "natives": str(natives_dir),
        "assets": str(assets_root),
        "required_java": required_java(version),
    }


def command_line(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(shlex.quote(c) for c in cmd)


def redact_command(cmd: list[str]) -> str:
    result: list[str] = []
    hide_next = False
    for arg in cmd:
        if hide_next:
            result.append("<hidden>")
            hide_next = False
            continue
        if arg.startswith("--accessToken="):
            result.append("--accessToken=<hidden>")
            continue
        result.append(arg)
        if arg == "--accessToken":
            hide_next = True
    return command_line(result)


def spawn(cmd: list[str], cwd, env: dict, log_path, on_line=None):
    log_path = Path(log_path)
    ensure_dir(log_path.parent)
    log_file = open(log_path, "a", encoding="utf-8", errors="replace")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        bufsize=1,
    )

    def reader():
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    log_file.write(line)
                    log_file.flush()
                    if on_line:
                        try:
                            on_line(line.rstrip("\r\n"))
                        except Exception:
                            pass
        finally:
            try:
                log_file.close()
            except OSError:
                pass

    threading.Thread(target=reader, daemon=True).start()
    return proc
