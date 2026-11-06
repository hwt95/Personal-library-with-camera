# Changelog · 更新日志

## 1.2 — 2026-11-07

### English

- Choose CLC, DDC or LCC for your books. Automatic mode starts with CLC in Simplified Chinese and DDC in the other interface languages; each library remembers its own rule.
- Keep each book's classification system and numbers. Language changes leave existing records and unfinished entries alone; search, details, classification dictionaries and CSV exports follow the selected system.
- Apply a new rule to future entries or reorganise existing books after viewing the affected copies and proposed call numbers. A full backup comes first, and numbering changes can be exported. Magazine numbers and copy suffixes stay in place.
- Read explicit DDC and LCC numbers from Open Library. Missing numbers remain pending; the application does not infer cross-system mappings or bundle complete DDC/LCC schedules.
- Upgrade to database format 3 with a backup. Existing 1.1 call numbers are preserved. Upgraded libraries require 1.2; use the pre-upgrade backup to return to an older application.

### 简体中文

- 新增 CLC、DDC、LCC 图书分类。自动模式下简体中文默认 CLC，其余语言默认 DDC，每个书库保存自己的规则。
- 每本书保留自身分类体系及各体系分类号。切换语言不改写已有书目或当前草稿，检索、详情、分类字典和 CSV 导出适配多体系。
- 新规则可以仅用于新录入，也可以在查看涉及册数和新旧编号后同步已有书目。同步前完整备份，编号变更记录可导出；杂志编号与副本后缀保留。
- Open Library 查询读取明确提供的 DDC、LCC 分类号；缺少时保留为待分类，不猜测跨体系映射，不附带完整 DDC、LCC 类目表。
- 数据库升级至结构版本 3，升级前自动备份，保留 1.1 馆藏编号。升级后的书库须使用 1.2，回退旧版时请使用升级前的备份。

## v1.1 — 2026-10-30

### English

- Books and magazines now have separate entry layouts. Record magazine years and issues, and track unread, in-progress and finished items.
- A barcode scan flows straight into cover photography. Local DocAligner models help find the edges; the enlarged corner editor adds a magnifier and keyboard adjustments.
- Call numbers now count within each classification, such as `TP303-001`. Extra copies keep their suffixes. Older databases are backed up before migration, with old and new codes preserved.
- Six interface languages, a matching title bar and regular-weight Chinese, Latin and Arabic fonts make the application more comfortable to use.
- A local licence reader brings together project terms, third-party notices and corresponding source downloads. The portable package omits unused components and third-party source archives.
- The repository now has an English/Chinese overview and empty entry-screen images for both languages. Text files consistently use LF line endings.

### 简体中文

- 图书、杂志分别录入，支持杂志年度与期数，以及未阅读、阅读中、已阅读三种状态。
- 扫码后直接衔接封面拍摄；本地 DocAligner 模型辅助找边，放大四角编辑器加入顶点放大镜和键盘微调。
- 馆藏编号改为分类内递增，例如 `TP303-001`；同书副本继续使用后缀。旧库升级前自动备份，并保留旧新编号对照。
- 提供六种界面语言、统一主题标题栏及常规字重的中文、拉丁和阿拉伯语字体。
- 本地许可阅读器集中展示项目条款、第三方声明及对应源码下载入口；便携包移除未使用的组件和第三方源码归档。
- 仓库首页改为中英双语概览，并配有两种语言的空白首屏截图；文本文件统一使用 LF 换行。

## v1.0 — 2026-10-25

初始发布
