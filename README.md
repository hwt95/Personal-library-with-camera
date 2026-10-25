# Personal library with camera

带摄像头封面采集功能的个人藏书管理工具。面向 Windows 10/11 64 位，解压后即可使用，无需安装 Python 或数据库服务器。

书目、实体副本、封面和分类字典保存在本地 SQLite 数据库中。封面裁切、旋转及方向识别均在本机完成，照片不会上传。

## 功能

- **录入与查询**：摄像头识别 ISBN 条码，也可手工录入无 ISBN 的旧书。书目查询优先使用本地资料和国家图书馆，并提供 Open Library；Google Books 需自行填写可用的 API Key。缺失字段可以手工补充，等待查询时可以取消。
- **封面拍摄**：独立窗口实时取景，支持自动对焦、重新对焦和数字缩放。自动识别封面边框、透视裁切和方向，支持拖动四角、手动旋转及导入已有照片。
- **分类与副本**：使用中图分类号管理藏书，内置 22 个基本大类，可导入自己的分类字典。自动分配馆藏编号，同书副本独立记录出版年份、存放位置和备注。
- **存放地点**：预设常用书架或房间，录入时直接选择，也可手工填写位置。
- **列表与检索**：支持书名、ISBN、作者、出版社、年份、分类、位置及简介检索，多条件可按 AND/OR 组合；可筛选待分类、缺封面和有副本的书目，并导出 CSV。
- **本地数据管理**：新建、打开和切换数据库，完整备份、另存副本及重置；重置前自动备份。
- **便携运行**：内置运行库、本地方向模型和中文字体，无需账号或云同步。

## 下载与使用

在本仓库的 **Releases** 页面下载 `PersonalLibrary-v1.0-Windows-x64.zip`，将整个压缩包解压到可写目录，双击 `PersonalLibrary.exe`。请保留 exe 旁的 `_internal` 文件夹。

首次启动时新建或打开自己的藏书数据库。建议将数据库存放在程序目录之外，并定期使用“完整备份”。个人设置保存在程序旁的 `settings.json`，分享程序时请勿连同自己的数据库和设置文件一起发送。

详细操作见 [使用说明](code/USER_GUIDE.md)，版本记录见 [CHANGELOG](CHANGELOG.md)。

## 从源码运行与构建

使用 Windows x64 和 Python 3.13，在仓库根目录执行：

```powershell
python -m venv deps/.venv
& deps/.venv/Scripts/python.exe -m pip install -r deps/requirements.lock.txt
& deps/.venv/Scripts/python.exe code/main.py
```

测试和构建：

```powershell
& deps/.venv/Scripts/python.exe -m pytest test -q --basetemp .pytest-tmp
& deps/.venv/Scripts/python.exe code/scripts/build.py
& deps/.venv/Scripts/python.exe code/scripts/verify_portable.py
```

构建结果位于 `build/dist`。程序通过干净的暂存目录打包，发行包不包含个人数据库或设置；更新本机构建目录中的程序时会保留已有用户数据。依赖版本见 [requirements.lock.txt](deps/requirements.lock.txt)。

摄像头自动对焦取决于设备及驱动支持；网络书目来源的可用性和覆盖范围可能变化。自动裁切与方向识别允许人工修正。本工具用于单机个人藏书管理，暂不包含借还管理或多设备同步。

作者：Gimi.H with Chatgpt model。第三方组件、字体与模型保留各自的版权及许可，发行包附有 `licenses`、`THIRD_PARTY.json` 和 `THIRD_PARTY_NOTICES.txt`。
