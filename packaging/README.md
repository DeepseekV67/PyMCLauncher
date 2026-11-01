# PyMC Launcher 单文件绿色版

把整个 Python + Tk 启动器嵌入一个 C# 引导 exe，运行时不需要安装 Python。

## 构建要求

- Windows 10 / 11 x64
- Python 3.11.x，且已安装 `httpx[http2]`
- .NET SDK 8 或更高版本（用于编译 `net48`，本机需要 .NET Framework 4.8 参考程序集）

## 构建

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

或者双击 `packaging\build.bat`。

输出：

```
dist\PyMCLauncher.exe
```

可选参数：

```powershell
# 跳过构建后的自检
... -NoSelfTest

# 保留 packaging\build（运行时目录、payload.zip），方便下次增量构建
... -KeepBuild

# 指定 Python 安装目录
... -PythonRoot "C:\Python311"
```

## 运行方式

1. 把 `dist\PyMCLauncher.exe` 放到任意可写目录。
2. 双击运行。

首次运行会：

- 把内置运行时解压到 `%LOCALAPPDATA%\PyMCLauncher\runtime-<hash>`；
- 在 exe 同目录创建 `PyMCLauncher-data`，保存 `config.json`、`cache`、`logs` 和 `runtime/authlib-injector.jar`；
- 如果 exe 所在目录不可写，数据目录回退到 `%LOCALAPPDATA%\PyMCLauncher\data`。

删除 `PyMCLauncher-data` 和 `%LOCALAPPDATA%\PyMCLauncher` 即可完全重置。

## 说明

- exe 内置 Python 3.11、Tcl/Tk、httpx，不依赖本机 Python。
- 引导程序使用 .NET Framework 4.8（Windows 10/11 通常自带），不是 .NET 8 自包含发布。
- 不会打包开发目录里的 `config.json`，因此账户、实例配置不会带进发布文件。
- 联机页使用 Terracotta（陶瓦联机），首次使用会从 Gitee/GitHub 下载约 8 MB 的官方未修改二进制，保存到数据目录的 `terracotta/` 下；启动器通过其本地 HTTP API 工作。详见 `THIRD_PARTY_NOTICES.md`。
- `--selftest` 参数会解压运行时并检查 Tk/httpx/启动器模块，然后退出，适合发布前验证：

```powershell
dist\PyMCLauncher.exe --selftest
```

引导日志：`%LOCALAPPDATA%\PyMCLauncher\boot.log`。
