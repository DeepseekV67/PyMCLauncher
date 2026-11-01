# 第三方组件说明

## Terracotta | 陶瓦联机

- 项目主页：https://github.com/burningtnt/Terracotta
- 许可证：GNU Affero General Public License v3.0 or later（AGPL-3.0）
- 使用方式：启动器在用户首次使用「联机」页时，从官方 GitHub Release / Gitee 镜像下载**未经修改**的 Windows x64 二进制，并通过其本地 HTTP API 创建房间、加入房间和读取状态。
- 启动器界面中的联机页已标注 Terracotta 版权信息与项目链接。
- 版权归 Terracotta 原作者及贡献者所有。

## 打包内置运行时

单文件 exe 内嵌以下组件，仅用于运行本启动器：

- Python 3.11（Python Software Foundation License）
- Tcl/Tk（BSD 风格许可证）
- httpx、httpcore、h11、h2、hpack、hyperframe、anyio、sniffio、idna、certifi（各自的开源许可证）

以上组件的版权归各自作者所有。
