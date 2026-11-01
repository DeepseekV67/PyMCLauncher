from __future__ import annotations

import re
from datetime import datetime

FIX_LABELS = {
    "repair": "补全游戏文件",
    "java": "更换匹配的 Java",
    "memory": "提高实例内存",
    "mods": "检查模组与前置依赖",
    "login": "重新登录账户",
    "graphics": "更新显卡驱动或调整图形设置",
    "port": "关闭占用端口的程序",
    "none": "查看完整日志",
}


def _tail(text: str, max_lines: int = 400) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-max_lines:])


def diagnose(log_text: str = "", crash_text: str = "",
             context: dict | None = None) -> list[dict]:
    text = f"{log_text or ''}\n{crash_text or ''}".lower()
    findings: list[dict] = []
    seen = set()

    def add(key, severity, title, detail, fix):
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "severity": severity, "title": title,
            "detail": detail, "fix": fix,
        })

    if any(x in text for x in ("unsupportedclassversionerror",
                               "class file version",
                               "has been compiled by a more recent version")):
        add("java_version", "error", "Java 版本不匹配",
            "游戏需要的 Java 版本高于当前选择的 Java。请在实例设置里选择对应的 Java 21/25，"
            "或先安装该版本 Java。", "java")

    if any(x in text for x in ("outofmemoryerror", "java heap space",
                               "insufficient memory")):
        add("memory", "error", "游戏内存不足",
            "Java 堆内存已耗尽。请在实例设置中提高内存（建议 4096MB 以上），"
            "并关闭其他占用内存的程序。", "memory")

    if any(x in text for x in ("failed to locate library", "unsatisfiedlinkerror",
                               "java.library.path", "no lwjgl in java.library.path",
                               "could not initialize class org.lwjgl")):
        add("native", "error", "原生库缺失或路径错误",
            "LWJGL/原生库没有正确加载，通常是 natives 解压不完整或路径配置异常。"
            "建议补全游戏文件并重新启动。", "repair")

    if any(x in text for x in ("modresolutionexception", "incompatible mod set",
                               "missing or unsupported mandatory dependencies",
                               "duplicate mods", "mod file", "fabric-loader",
                               "modlauncher")):
        add("mods", "error", "模组冲突或缺少前置",
            "崩溃与模组加载有关。请检查是否缺少 Fabric API/Forge 前置，"
            "或暂时移出最近添加的模组，逐个排查。", "mods")

    if any(x in text for x in ("classnotfoundexception", "noclassdeffounderror",
                               "could not find or load main class",
                               "zip file is empty", "invalid distance",
                               "corrupt", "nosuchfileexception")):
        add("library", "error", "游戏依赖文件缺失或损坏",
            "客户端、依赖库或资源文件可能不完整。建议使用“补全游戏文件”"
            "重新校验并下载缺失内容。", "repair")

    if any(x in text for x in ("invalid session", "authentication",
                               "failed to login", "http 401", "401 unauthorized")):
        add("auth", "warning", "登录凭据可能失效",
            "请退出账户后重新登录。外置登录还需要确认皮肤站账号状态。", "login")

    if any(x in text for x in ("glfw error", "opengl", "pixel format",
                               "wgl", "unsupported", "显卡驱动")):
        add("graphics", "warning", "图形驱动或 OpenGL 兼容问题",
            "尝试更新显卡驱动；笔记本可切换到独立显卡运行，"
            "或在启动参数中关闭部分图形增强。", "graphics")

    if "address already in use" in text:
        add("port", "warning", "端口被占用",
            "可能已经有一个游戏实例在运行，请先关闭其他实例。", "port")

    if any(x in text for x in ("failed to download", "http 404",
                               "http 403", "connection timed out",
                               "download failed")):
        add("download", "warning", "资源下载失败",
            "启动时下载依赖失败。请检查网络后使用“补全游戏文件”重试。", "repair")

    description = ""
    match = re.search(r"description:\s*([^\n]+)", crash_text or "", re.I)
    if match:
        description = match.group(1).strip()
    if description:
        add("description", "info", "崩溃报告摘要",
            description, "none")

    if not findings:
        add("unknown", "warning", "未能自动定位明确原因",
            "请导出日志并查看完整堆栈；如果安装了模组，优先尝试移除最近添加的模组。",
            "none")

    if context:
        version = context.get("version") or ""
        java = context.get("java") or ""
        memory = context.get("memory") or ""
        extra = "，".join(x for x in (
            f"版本 {version}" if version else "",
            f"Java {java}" if java else "",
            f"内存 {memory}MB" if memory else "",
        ) if x)
        if extra:
            findings.append({
                "severity": "info", "title": "本次启动环境",
                "detail": extra, "fix": "none",
            })
    return findings


def findings_text(findings: list[dict]) -> str:
    blocks = []
    labels = {"error": "错误", "warning": "警告", "info": "信息"}
    for finding in findings:
        blocks.append(
            f"[{labels.get(finding.get('severity'), '信息')}] "
            f"{finding.get('title')}\n{finding.get('detail')}\n"
            f"建议：{FIX_LABELS.get(finding.get('fix'), '查看日志')}")
    return "\n\n".join(blocks)


def build_report(instance: dict | None, exit_code, findings: list[dict],
                 crash_text: str = "", log_text: str = "",
                 command: str = "", crash_path: str = "",
                 log_path: str = "") -> str:
    instance = instance or {}
    lines = [
        "PyMC Launcher 崩溃报告",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"实例: {instance.get('name') or instance.get('version') or '-'}",
        f"版本: {instance.get('version') or '-'}",
        f"加载器: {instance.get('loader') or 'vanilla'}",
        f"游戏目录: {instance.get('game_dir') or '-'}",
        f"退出代码: {exit_code}",
        f"日志文件: {log_path or '-'}",
        f"崩溃报告: {crash_path or '-'}",
        "",
        "===== 自动诊断 =====",
        findings_text(findings),
        "",
    ]
    if command:
        lines += ["===== 启动命令（已脱敏） =====", command, ""]
    if crash_text:
        lines += ["===== 崩溃报告 =====", crash_text.strip(), ""]
    if log_text:
        lines += ["===== 游戏日志（末尾 400 行） =====", _tail(log_text), ""]
    return "\n".join(lines)
