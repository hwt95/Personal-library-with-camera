# Data sources · 数据来源

## English

The application licence and the rights to data are separate. Catalogue adapters are application code; records, descriptions, cover images, classification data and access to external services retain their providers' terms and any existing rights. The release contains no personal catalogue or downloaded catalogue pages.

The Simplified Chinese interface offers local records, the National Library of China, Open Library and optional Google Books. Other interface languages skip the National Library source. Google Books requires a key supplied by the user. Manual entry and local cover capture remain available without a network lookup.

| Source | How it is used | Terms and attribution |
| --- | --- | --- |
| National Library of China | ISBN catalogue lookup in the Simplified Chinese interface | The application labels this source for testing only, not commercial use. Preserve the full source name and relevant attribution; consult the [provider's copyright notice](https://www.nlc.cn/web/dsb_footer/bqsm/index.shtml) and obtain any required permission. The label itself is not a grant of permission. |
| Open Library | Book details, contributors, descriptions and covers | Follow the [licensing guidance](https://openlibrary.org/developers/licensing) and any existing rights in individual content. |
| Google Books | Optional lookup with a user-supplied API key | Follow the [Books API terms](https://developers.google.com/books/terms) and applicable API terms. Supplying a key does not replace them. |
| Classification dictionaries | 48,104 CLC main entries, eight auxiliary tables and user-imported terms | The original user-provided dictionary is limited to noncommercial use according to its supplier. Wikipedia contributors retain attribution and the archived CC BY-SA 3.0 terms. Fifth-edition classifications retain the original rights. See the source records below; the software licence does not relicense these data. |
| Photos and manual entries | Local cover processing and catalogue storage | Users remain responsible for their rights to imported or photographed material. |

Attribution does not replace permission where permission is required. Copying a database, exporting a list or redistributing content does not remove these rights. Written commercial permission from the application author covers only the author's own material, not third-party data or services.

## 简体中文

软件许可和数据权利分别处理。查询适配器属于应用代码；查询得到的书目、简介、封面、分类数据和服务访问权，仍适用提供方条款及已有权利。发行包不包含个人馆藏或抓取的书目网页。

简体中文界面提供本地资料、国家图书馆、Open Library 和可选 Google Books；其他界面语言跳过国图。Google Books 使用用户自行配置的 API Key。不联网查询时，仍可手工录入并在本机拍摄、处理封面。

| 来源 | 使用方式 | 条款与署名 |
| --- | --- | --- |
| 中国国家图书馆·中国国家数字图书馆网站 | 简体中文界面的 ISBN 书目查询 | 程序标注仅供测试、不能用于商业用途。保留完整出处及相关署名，遵守[版权声明](https://www.nlc.cn/web/dsb_footer/bqsm/index.shtml)并取得所需许可；测试标注本身不构成授权。 |
| Open Library | 书目、作者、简介和封面 | 遵守[许可说明](https://openlibrary.org/developers/licensing)及具体内容的已有权利。 |
| Google Books | 配置 Key 后的可选查询 | 遵守 [Books API 条款](https://developers.google.com/books/terms)和适用的通用 API 条款，自行提供 Key 不代替这些义务。 |
| 分类字典 | 48,104 项中图法主表、八张附表及用户导入词典 | 原字典按提供者说明限非商业使用；维基百科及第 5 版资料分别保留来源条款和原有权利，详见下方记录；不改用软件许可。 |
| 拍摄、导入和手填内容 | 本机封面处理和书目保存 | 使用者须拥有相应材料所需的权利。 |

需要许可的用途不能仅靠署名代替许可。复制数据库、导出列表或再分发内容，不会消除第三方权利。应用作者的商业授权只覆盖其自身有权许可的材料，不包含第三方数据或服务授权。

## Bundled CLC sources · 内置中图法来源

Wikipedia-derived additions are attributed to Chinese Wikipedia contributors and retain the archived [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) terms, including applicable share-alike obligations. The application licence does not replace the data-source terms. Snapshot URLs, revisions, changes and checksums are provided in `SUPPLEMENT_SOURCE.json`; reference PDFs and raw downloaded pages are not distributed.

### 本地中图法字典（2026-11-07）

原始文件为用户提供的 `clc.json`，SHA-256 为 `ee2b336ff0e1b3f1eb254083f6d67e97bcdceabb72634104a3485cf44e945feb`。按用户说明仅用于非商业用途；具体原始许可和作者来源待补充，不将本数据改称 CC、GPL 或 PolyForm 数据。

程序保留分类树及原有括号标记，将数据转换为压缩索引，现有数据库中的自定义词典优先。使用另行提供的《中国图书馆分类法（第5版）》PDF 随机抽查 5 项：4 项一致，发现一项标点顺序问题；同页相邻 4 项存在相同问题，依该页修正共 5 个编号 `U653.923.+1…5` → `U653.923+.1…5`（PDF 第 966 页，书内第 946 页），原编号在校正记录中保留。该抽查不代表全量校对或完整性保证。PDF 仅用于本地参考，不随程序打包。

数据来源、原文件校验值和逐条校正记录随包保存在 `_internal/resources/classification/SOURCE.json`。软件商业授权不包含本字典的商业使用权。

### 细分类与复分表补丁（2026-11-11）

原压缩字典保持不变，补充数据独立保存在 `code/resources/classification/supplement.json.gz`，来源及逐页快照地址、修订号、校验值和覆盖范围记在同目录 `SUPPLEMENT_SOURCE.json`。新增条目携带 `source_id`，用户导入的词典仍优先。原有 45,785 项加上 2,319 项补充，主表合计 48,104 项；附表另计 1,211 项。

| 资料 | 采用方式与出处 |
| --- | --- |
| 中文维基百科贡献者的中图法分表 | 从用户给出的[目录快照](https://web.archive.org/web/20220829031250/https://zh.wikipedia.org/wiki/中国图书馆分类法)与 [I 类快照](https://web.archive.org/web/20220730151209/https://zh.wikipedia.org/wiki/中国图书馆分类法_(I))追溯 22 个大类和 O、T、TQ 分页；具体日期及原文校验值逐页记录。保留快照页脚 CC BY-SA 3.0 及附加条款说明。只增补缺失且无停用标记、无歧义的有效编号，原有名称和父子关系优先。 |
| 较早的维基百科存档 | TQ1、TQ2、TQ6、TQ9 使用 2017 年存档，I 类额外比对 [2019 年快照](https://web.archive.org/web/20190702112918/https://zh.wikipedia.org/wiki/中国图书馆分类法_(I))，补回 `I516.4`。网页删除与分类号正式停用分别处理；例如 `I234.7` 在来源中注明第四版删除，保留停用释义而不恢复为有效号。 |
| 《中国图书馆分类法》第 5 版，国家图书馆《中国图书馆分类法》编辑委员会，国家图书馆出版社，2010 | 武汉职业技术学院图书馆提供的[原书地址](https://tsg.wtc.edu.cn/_upload/article/files/fd/74/76b7c82e4eb7a591a1112f168df1/9a512fd6-9c4c-42dc-b227-2d31a2ab49b7.pdf)，本次使用其 [2025-06-21 存档](https://web.archive.org/web/20250621112256/https://tsg.wtc.edu.cn/_upload/article/files/fd/74/76b7c82e4eb7a591a1112f168df1/9a512fd6-9c4c-42dc-b227-2d31a2ab49b7.pdf)。八表位于书内第 991–1014 页（PDF 第 1012–1035 页），文学专类复分表位于书内第 174–175 页。附表逐项保留页码、父级和标记状态；原书只缓存于本地核对目录。 |
| 深圳大学图书馆 NKOS 研究室 CLSS | [通用复分表浏览入口](https://www.lib.szu.edu.cn/nkosapp/ProjectForCLC/clcmain.jsp?lang=chi&tag=1)，同路径 `tag=2…8` 为其他附表。该站明确采用第 4 版，用于独立交叉核对；与第 5 版有差异时以第 5 版原书为准，不混合版本。 |
| 西安交通大学图书馆 雒虹，2011 年 5 月 | [《中图法》标记符号系统和复分表的修订和使用](https://lib.xdxd.cn/uploads/ftp1213/zhongtu5.9.pdf)，用于核对连接符、组合顺序、专类优先及复分适用例外。原资料不随程序分发。 |
| 用户指定的通用复分表网页 | [2019-06-29 快照](https://web.archive.org/web/20190629202539/https://zh.wikipedia.org/wiki/中国图书馆分类法_(通用复分表))保留为交叉核对来源，运行时以核对后的第 5 版八表为准。 |

新增数据只提取编号、类名、必要标记及来源，不复制原书全文。附表保留原版历史名称和范围，不代表当代行政区划或族群学标准。原书和分类数据权利仍归原权利人，不能把网络可访问或已署名解释为取得再分发或商业许可。

对编号、OCR 及版本差异逐项核对的记录保存在 `deps/classification/auxiliary-v5.json`。快照中的 `I313.74`“语言”据文学复分表改为“寓言”；厄立特里亚由错误的 `I421.99` 改为 `I421.9`，均保留原文和核对页码。同码多名、范围号及停用分支不作猜测合并。O4、TF、TN、TS 在尝试多个日期和地址后仍未取得可读存档，保留本地原有分支，不声明这四页已补齐，也不声明原有主表已全量校订。

复分自动解释优先采用主表精确类目；只对完整、已知的附表编号和有明确依据的连接规则生成含义，并标注“复分组合”。它不是涵盖所有专类复分表的自动编目器。错误、未知或未实现的组合仍返回明确标记的上级参考，既有分类号不改写。分类数据和规则的适用范围不因本次发布而扩大。


维基百科衍生补充条目署名为中文维基百科贡献者，沿用快照中的 [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) 及适用的相同方式共享要求；软件许可不替代数据许可。
