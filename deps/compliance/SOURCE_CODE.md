# 第三方源码下载、通知与替换说明

本应用动态使用 Qt / PySide6 / Shiboken 6.12.0，按适用的 LGPLv3 条款交付。完整 LGPLv3 和 GPLv3 文本在 `licenses/Qt/`。本项目的 PolyForm 限制和商业授权不缩减 LGPL 等第三方许可证授予的权利，包括修改、替换、重新链接，以及为调试库的修改而进行逆向工程。

## 在线取得对应版本源码

为减小安装空间，本便携包不携带第三方源码压缩包。设置 → 软件许可与第三方协议 → 第三方源码下载，提供下列组件的精确版本下载地址和 SHA-256。也可使用本文件中的链接；无需注册或申请，日常运行不需要下载源码。`SOURCE_DOWNLOADS.json` 保存对应版本、原始 URL、大小和校验值；本项目没有修改这些库。

<!-- SOURCE_DOWNLOAD_TABLE -->

使用 HTTPS 网络下载方式再分发时，应在程序下载链接旁同时提供这些对应源码的明确下载指引，并持续确保源码可获取。链接可指向提供等效下载方式的第三方服务器，但分发者仍承担保证可获取的责任；若原链接失效，应提供经校验的源码镜像或改为随包提供。维护者的源码归档保留在构建缓存 `deps/compliance-cache/`，不占用最终用户程序目录。相关依据：[GPLv3 第 6(d) 条](https://www.gnu.org/licenses/gpl.en.html#section6)、[GNU 关于源码与程序使用不同服务器的说明](https://www.gnu.org/licenses/gpl-faq.en.html#SourceAndBinaryOnDifferentSites)、[MPL 2.0 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)。采用其他交付方式时须选择适用的源码提供方式，不能仅靠一个链接取代全部义务。

| 文件前缀 | 对应组件 |
| --- | --- |
| `qtbase-6.12.0` | QtCore、QtGui、QtWidgets 及基础插件；包含构建脚本、头文件和内嵌第三方源文件 |
| `qtsvg-6.12.0` | QtSvg、SVG 图片/图标插件 |
| `qtimageformats-6.12.0` | 扩展图片格式插件及内嵌库 |
| `pyside-setup-6.12.0` | PySide6、Shiboken 的源码、绑定生成器输入与构建脚本 |
| `eigen-1d8b82b0740839c0de7f1242a3585e3390ff5f33` | ONNX Runtime 1.31.0 的 `cmake/deps.txt` 固定的 Eigen 版本，MPL-2.0 等逐文件许可 |
| `certifi-2026.10.23` | 同版本 certifi/Mozilla CA 数据与源码，MPL-2.0 |

`licenses/native/INDEX.json` 记录其他底层依赖通知的出处与校验值；`licenses/native/` 保留上游许可证、版权和专利通知。Qt 的 `ATTRIBUTIONS.json` 还保留内嵌组件的名称、版本、Copyright、LicenseFile 和用途。通知收集范围包括上游可选组件、测试和构建工具，不表示它们全部进入了本应用，也不把可选 GPL 文件的许可套用到整个应用。实际二进制范围以包内文件为准。

## Windows x64 动态库替换

1. 先关闭应用，并备份整个程序目录及个人数据库。程序库位于 `_internal/`，不是封入 exe 的静态库。
2. Qt DLL 和插件位于 `_internal/PySide6/`，如 `Qt6Core.dll`、`Qt6Gui.dll`、`Qt6Widgets.dll`、`Qt6Svg.dll` 和 `plugins/`。PySide6 的 `.pyd`/辅助 DLL 位于同目录；Shiboken 位于 `_internal/shiboken6/`。依打包布局也可能有辅助 DLL 位于 `_internal/` 根目录。
3. 解压对应源码，按其 README 和构建脚本使用 Windows x64 / MSVC 2022 兼容工具链、共享库 Release 配置构建。Qt 的入口是 `configure.bat` / CMake；例如在独立构建目录运行 `qtbase源码路径\configure.bat -prefix 安装路径 -opensource -confirm-license -release -shared -nomake tests -nomake examples`，然后 `cmake --build . --parallel` 和 `cmake --install .`。qtsvg、qtimageformats 使用该安装的 `qt-configure-module.bat` 分别构建。不同机器的工具链和 SDK 路径需自行指定。
4. PySide6/Shiboken 用下载的对应版本 `pyside-setup` 的 `setup.py` 和官方构建文档，指向上述 Qt 的 `qtpaths.exe`；典型入口为 `python setup.py bdist_wheel --qtpaths=安装路径\bin\qtpaths.exe --build-type=all`。需要 CMake、Ninja、适配的 LLVM/libclang 和 Python 开发文件，详见其源码说明。这些是构建工具，不是新增的应用运行依赖。
5. 替换为接口和 ABI 兼容、架构相同的 DLL、绑定和插件。当前包使用 CPython 3.13.12 x64、PySide6/Shiboken 6.12.0、Qt 6.12.0，共享 Release / MSVC 2022 构建；Qt 插件应与其主库匹配。跨版本或编译选项不兼容时应同时重建相关绑定/应用。应用不校验这些库的签名、哈希或特定供应商身份，也无激活密钥或设备锁限制替换。
6. 从程序目录启动 `PersonalLibrary.exe`，或运行 `PersonalLibrary.exe --self-test 自检报告.json --self-test-platform offscreen`。自检会在报告所在目录使用临时合成数据库；请指定可写的独立测试目录。替换后自行检查界面、字体、图像和本地模型功能。

本文件提供动态库替换途径和构建入口。第三方库自己的许可证不因本说明改变。官方完整构建说明：[Qt for Windows](https://doc.qt.io/qt-6/windows-building.html)、[Qt for Python 构建](https://doc.qt.io/qtforpython-6/building_from_source/index.html)。

## 精简包移除的组件

摄像头使用 Windows DirectShow，应用不提供视频文件播放，因此未分发 OpenCV FFmpeg 插件。QtNetwork、Qt TLS 插件和它携带的 OpenSSL 副本也已移除；HTTP 请求仍使用 Python/httpx，Python 的 OpenSSL 与其许可证保留。界面使用 Qt Widgets 的常规绘制，未分发 Mesa/LLVM 的 `opengl32sw.dll` 回退库。

Python 模型推理使用 `onnxruntime_pybind11_state.pyd`，移除了没有被程序调用的独立 C API `onnxruntime.dll`。保留 Windows 原生平台插件及离屏自检插件，移除未使用的 Direct2D/minimal 平台插件。所有运行模型、字体和许可证原文保留。

本文件不授予 Microsoft 运行库再分发权，不授予国图、Open Library 或 Google Books 数据/服务使用权，也不代替本项目的书面商业授权。
