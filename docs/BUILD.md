# Build from source · 从源码构建

## English

Use Windows x64 and Python 3.13. The portable build uses the versions pinned in `deps/requirements.lock.txt`; the matching third-party source records are in `deps/compliance/SOURCES.json`.

Run these commands from the repository root:

```powershell
python -m venv deps/.venv
& deps/.venv/Scripts/python.exe -m pip install -r deps/requirements.lock.txt
& deps/.venv/Scripts/python.exe code/main.py
```

To build, download the corresponding library sources once, then run the packaging script:

```powershell
& deps/.venv/Scripts/python.exe code/scripts/fetch_compliance_sources.py
& deps/.venv/Scripts/python.exe code/scripts/build.py
```

The package is written to `build/dist`. Source archives stay in `deps/compliance-cache`; `PERSONAL_LIBRARY_COMPLIANCE_CACHE` can point to an existing cache. Publish the generated `SOURCE_DOWNLOADS.md` alongside the executable ZIP. Runtime data, caches and reports are excluded from Git.

The repository includes synthetic tests. Private camera samples are not distributed, so their optional test is skipped when those samples are absent.

```powershell
& deps/.venv/Scripts/python.exe -m pytest test -q --basetemp .pytest-tmp
& deps/.venv/Scripts/python.exe code/scripts/verify_portable.py
```

Text uses LF on every platform, controlled by `.gitattributes`; fonts, models and images are binary. New public files must be added explicitly to the `.gitignore` allowlist before they can be committed.

## 简体中文

构建环境为 Windows x64 和 Python 3.13。依赖固定在 `deps/requirements.lock.txt`，对应第三方源码记录位于 `deps/compliance/SOURCES.json`。在仓库根目录依次执行上面的环境创建、依赖安装和启动命令。

首次打包前运行 `fetch_compliance_sources.py` 取得对应源码，再执行 `build.py`。便携包输出到 `build/dist`；源码归档保留在 `deps/compliance-cache`，也可用 `PERSONAL_LIBRARY_COMPLIANCE_CACHE` 指定已有缓存。对外分发时，请把生成的 `SOURCE_DOWNLOADS.md` 与程序 ZIP 一同提供。

测试使用合成数据；私有摄像头样本不随源码分发，缺少样本时相应可选测试会跳过。运行数据、缓存和报告不进入 Git。

`.gitattributes` 将文本统一为 LF，字体、模型和图片按二进制处理。新增公开文件须先加入 `.gitignore` 的明确文件清单。
