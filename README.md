# PyMC Launcher

一个轻量的 Minecraft: Java Edition 启动器，使用 Python + Tkinter 编写，提供单文件绿色 exe。

![icon](docs/icon.png)

## 功能

- 多账户：微软正版、LittleSkin 等外置登录、离线账户，列表内切换
- 版本与加载器：原版、Fabric、Forge、NeoForge、Quilt、OptiFine，支持选择加载器版本
- 模组与资源：Modrinth / CurseForge 搜索与下载、资源包 / 地图 / 光影 / 材质包、前置依赖提示
- 皮肤：3D 模型查看、Slim / 外层、本地皮肤库、立体绘制、自由调色、取色、撤销
- 皮肤上传：微软正版 / 标准 Yggdrasil 皮肤站；LittleSkin 未开放接口时引导网页上传
- CustomSkinLoader：一键写入本地皮肤、披风、鞘翅
- 联机：基于 [Terracotta | 陶瓦联机](https://github.com/burningtnt/Terracotta) 的房间码联机，无需公网 IP
- 崩溃诊断：错误日志、自动诊断、补全游戏文件
- 单文件 exe：内置 Python 3.11 + Tk + httpx，运行时不需要安装 Python

## 下载

在 [Releases](https://github.com/DeepseekV67/PyMCLauncher/releases) 下载最新的 `PyMCLauncher.exe`。

首次运行会：

- 把内置运行时解压到 `%LOCALAPPDATA%\PyMCLauncher\runtime-<hash>`
- 在 exe 同目录创建 `PyMCLauncher-data`，保存配置、缓存、日志和联机组件

如果 exe 所在目录不可写，数据目录会回退到 `%LOCALAPPDATA%\PyMCLauncher\data`。

## 从源码运行

要求 Windows 10/11 x64、Python 3.11：

```powershell
pip install -r requirements.txt
python launcher.py
```

也可以双击 `run.bat`。

## 构建单文件 exe

要求：

- Python 3.11（带 tkinter，已安装 `httpx[http2]`）
- .NET SDK 8+（用于编译 `net48` 引导程序）
- .NET Framework 4.8 参考程序集

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

输出：

```
dist\PyMCLauncher.exe
```

可选参数：

```powershell
# 跳过构建后的自检
... -NoSelfTest

# 保留 packaging\build 方便下次构建
... -KeepBuild

# 指定 Python 安装目录
... -PythonRoot "C:\Python311"
```

## 数据目录

源码运行时，数据保存在项目根目录：

```
config.json   # 账户与实例配置（包含加密令牌，不要提交）
cache/        # 下载缓存、皮肤缓存
logs/         # 日志
runtime/      # authlib-injector 等运行时组件
```

打包后的 exe 使用同目录下的 `PyMCLauncher-data`。

`config.json` 里的账户令牌通过 Windows DPAPI 加密，但仍然不应该上传到 GitHub。仓库的 `.gitignore` 已排除它。

## 联机说明

联机页使用 **Terracotta（陶瓦联机）**，它基于 EasyTier，提供房间码联机。

- 首次使用会从 Gitee / GitHub 下载约 8 MB 的官方未修改二进制，并做 SHA-256 校验
- 启动器通过其本地 HTTP API 创建/加入房间
- 联机页已标注 Terracotta 版权信息与 AGPL-3.0 许可
- 房间码是陶瓦联机的房间码，不保证与 PCL 私有联机房间码互通

## 第三方组件

单文件 exe 内置 Python、Tcl/Tk、httpx 等组件；联机功能使用 Terracotta。详细信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可证

本项目自身代码使用 [MIT License](LICENSE)。

Minecraft 是 Mojang / Microsoft 的商标，本项目与 Mojang / Microsoft 无隶属关系。
