# 个人数据管理器（D-MyDataManager）

[![license](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
![python](https://img.shields.io/badge/python-3.13%2B-blue)
![platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![PyQt6](https://img.shields.io/badge/PyQt6-6.11.0-41cd52)
![tests](https://img.shields.io/badge/tests-per--topic-blue)

一个**纯本地**的个人数据管理器，基于 **PyQt6 + PyQt6-Fluent-Widgets** 构建。它把散落在电脑里的资料
（文档、图片、视频、音频、代码、压缩包……）集中到一个「库文件夹」中统一管理，提供导入、分类树、
全局/个人标签、SQLite FTS5 全文检索、回收站、内容寻址存档、导出与多用户口令保护等能力，
界面为 Windows 11 Fluent 风格。

- **完全离线**：数据只存在本机，不联网、不上传、无账号体系。
- **多用户**：每个用户拥有独立的数据、分类与标签，可设口令、随时切换；默认用户（管理员）可管理其他用户；系统级操作（恢复初始化、资源文件夹维护、插件安装 / 启用 / 编辑 / 删除）也只对默认用户开放。
- **内容寻址**：文件按 SHA-256 存储，同一份内容重复导入不重复占空间；存档是"快照 + 引用"，
  对图片、视频等任意类型都有效。
- **库文件夹即目录**：数据按「用户名 / 分类目录 / 原文件名」真实存放，库目录可直接用资源管理器查看、备份。

## 目录

- [功能一览](#功能一览)
- [界面预览](#界面预览)
- [快速开始](#快速开始)
- [库文件夹结构](#库文件夹结构)
- [项目结构](#项目结构)
- [插件开发](docs/PLUGIN.md)
- [插件协议](docs/PLUGIN_PROTOCOL.md)
- [数据安全](#数据安全)
- [开发与自检](#开发与自检)
- [打包与发布](#打包与发布)
- [贡献](#贡献)
- [许可证](#许可证)
- [致谢](#致谢)

## 功能一览

| 模块 | 说明 |
| --- | --- |
| 概览首页 | 仪表盘：8 张 KPI 卡片（数据总量 / 占用空间 / 今日导入 / 用户数 / 分类 / 标签数 / 存档数 / 存档占用——最后一张是全部存档内容在内容仓库里真实占用的空间，跨存档按内容去重，副标题给出存档份数与逻辑大小）、快捷操作（导入数据 / 数据管理 / 打开资源文件夹 / 新建存档）、最近导入（点击跳转并选中该项）、类型分布条形图；窄窗口自动换行；标题行右侧是**「当前用户」下拉**，可直接切换用户（设了口令需先输入口令） |；整页按「概览 / 快捷操作 / 最近导入 / 类型分布」四张分区卡片组织（`section_card()`，每张都有标题与说明），KPI 卡与快捷按钮装在自适应高度的 `FlowArea` 里——重建卡片或窗口最大化后都不会重叠 / 消失
| 数据导入 | 文本直接录入；文件 / 整个文件夹批量导入（含递归），可选**导入到哪个用户**（只有默认用户能替其他用户导入，其他用户的下拉被禁用且只能选自己），选文件夹时把文件夹整体作为一个**新分类**导入、选多个文件时导入所选分类（未单独选择分类时落在「未分类」分类）；待导入清单先列出完整文件信息（文件名 / 类型 / 大小 / 修改时间 / 子目录 / 是否库内已有同内容），导入过程实时显示进度与逐文件结果；支持按时间命名、重复策略、自动生成封面与特征；标签既可直接输入，也可从当前用户的已有标签中多选（全局标签标注「（全局）」）；模式卡片实时显示当前选中的文件 / 文件夹摘要，导入完成后给出耗时 |；四张卡片（数据来源 / 导入目标与数据信息 / 待导入文件信息 / 导入进度）都改成带标题与说明的分区卡片，文件按钮行改用 `FlowArea` 流式排列
| 资源文件夹 | **全局唯一**（配置项 `Storage/Resource-Path`，默认 `.resources`，旧 `resources/` 自动改名）：数据库 `data.db` + 库文件夹 `library/`。库文件夹内部是一个「全局」文件夹（内容寻址备份、封面缓存、数据库备份与元数据）与每个用户一个用户名文件夹；数据文件按「用户名 / 分类目录 / 原文件名」真实存放，隐藏项物理落在分类目录下的 `.hiddens/`；可整体搬到别处（选的是容器目录、资源文件夹是它下面的 `.resources`；目标里已有 `.resources` 会问要不要删掉重搬，搬不动就把原文件夹与配置都退回去；搬迁后自动重启）、执行「扫描并登记」把已有文件纳入管理 |
| 数据管理 | 列表 / 卡片两种视图，全文检索（FTS5，支持多词 AND 与中文子串），按类型 / 标签 / 关键词筛选与排序（关键词为多选、需全部命中）；右侧筛选栏只有类型 / 标签 / 关键词三个分组（分类分组已移除），每个分组都可折叠、选项区内部可滚动，标题右侧带搜索框与三态全选框（空 = 全不选、横杠 = 部分选中、勾 = 全选，与分组内勾选双向同步）；分类过滤改由左侧分类树承担：树里每个分类都带三态复选框（「全部数据」根节点也有：勾上即全选整棵树、取消即全不选；勾选向下级联到所有子分类、子分类的状态向上汇总成勾 / 空 / 半选，勾选集合本身不含根节点），勾选（可多选）即把中间列表限定为这些分类**及其全部子孙分类**的数据、并把这些数据一并选中，单击分类行仍是单选，勾选后可「批量移动 / 批量删除」分类（**根分类与固定的「未分类」不可移动、不可删除**，删除时其中的数据变成未分类、子分类上移，同级重名的子分类跳过并在提示里说明；移动的目标是树里当前选中的分类，选中「全部数据」即移到顶层；勾选「全部数据」（全选整棵树）时两个按钮禁用——顶层没有可移动的去处，顶层分类也不能整体删除）；分类栏底部还有「仅显示分类」复选框（默认勾选，记在 `Layout/Only-Show-Categories`）：取消勾选后分类栏会在每个分类下列出该分类文件夹里的文件（目录在前、文件在后），**「未分类」不再单列节点**——它的文件就在用户名文件夹下，直接挂在「全部数据」下面；文件行自带两态复选框，与同级分类一起参与父节点的三态汇总，勾选文件即把该数据项显示并选中，单击文件行等于跳到该数据项；标题行右侧的「分类栏 / 筛选栏」两个按钮分别显示 / 隐藏左右两栏；上方工具栏为流式布局，宽度不足自动换行、超过两行可纵向滚动；分页浏览（每页 50 / 100 / 200 / 500 条、默认 50，页码框显示「第 N / 共 M 页」，跨页多选保持，每页条数记进配置项 `Layout/Page-Size`）；左侧分类栏默认全部收起（`Layout/Expand-Categories` 可改成默认展开，用户展开过的分类优先）、右侧类型 / 标签 / 关键词三个分组默认折叠（展开的键记在 `Layout/Expanded-Filters`），两栏显隐记在 `Layout/Show-Category-Panel` / `Layout/Show-Filter-Panel`，重启后复原；列表 / 卡片项左侧是复选框（单击只选中，Ctrl 逐个切换、Shift 连选，选择条上的三态全选框与行勾选双向同步），双击或右键「打开 / 查看器」才打开（内置查看器、点名某个插件、系统默认程序或交给系统选择；双击的动作可在「设置 → 外观 → 左键双击」里改成打开编辑器，对应配置项 `Layout/Double-Click-Action`），选中项可批量「移动到分类…」、批量重命名（替换 / 覆盖 / 插入 / 删除四种方式，先在变更清单里勾选要改的行，重名自动加 `-1`）、标签与关键词管理（三态复选框：勾 = 所选数据都有、横杠 = 部分有、空 = 都没有，点一下即批量加 / 删）及导出 / 隐藏 / 删除等；缩略图在工作线程异步加载并做 LRU 缓存；卡片视图与首页统计卡按窗口宽度自适应分列；重复内容分组清理；右键「编辑信息…」弹窗上方是只读的「数据信息」（类型 / 大小 / 归属用户 / 所在库 / 库内路径 / 磁盘位置 / 创建时间 / 内容指纹），下方才改名称 / 分类 / 标签 / 关键词 / 隐藏，改名会同步重命名库内文件（同名冲突自动加 `_N`）；左（分类）/ 中（列表与卡片）/ 右（筛选）三个面板统一为同款卡片样式（`PANEL_MARGINS` + 透明化滚动区） |
| 数据分类 | 无限层级分类树，同级不允许重名：新建 / 重命名同级重名会被拒绝；删除分类时子分类上移，若与同级重名会弹窗让用户选「自动编号」（`-1`、`-2`）或逐个重命名，也可选择整体删除；每个用户都有一个固定的「未分类」根分类，未指定分类的数据落在其中（导入时与「编辑数据项」对话框里都默认选中它，保存后文件移进 `<用户名>/未分类/`）；它是**固定分类**：排在所有根分类最后、树里带「（固定）」标识，不能重命名、删除，也不能在其下新建子分类（分类树右键无修改入口，服务层同样拒绝） |
| 数据标签 | 标签库 + 关键词双轨：标签是受管理的实体（可重命名、合并、清理未使用），分为**全局标签**（所有用户可见共用）与**个人标签**（仅创建者可见，且不得与已有全局标签重名——新建全局标签时若库里已有同名个人标签会把它并入，启动时也会修复历史库里的同名副本），记录创建者；独立的「标签」页可新建、改归属、重命名、删除、清理未使用（全局标签一旦被别的用户挂到数据项上，就不能再改回个人，避免静默摘掉别人的标签），表格表头可拖动调列、按内容自适应列宽，并支持逐列筛选（名称 / 归属 / 创建者 / 数据项数，Excel 式：文本列模糊匹配，「数据项数」可选等于 / 大于 / 小于 / 区间，可一键重置）；内置的基础标签（重要 / 待整理 / 收藏）默认是**全局标签**，新用户开箱即用；关键词是数据项上的自由词，可在导入时填写并在列表 / 卡片中显示；表格首列是勾选框，上方三态全选框与「全选 / 全不选 / 反选」同步，可**批量转为全局 / 转为个人 / 删除**（默认用户可以处理任意标签，其他用户只处理自己创建的标签，其余在提示里跳过） |
| 回收站 | 软删除，可还原；彻底删除时才释放底层文件；已在回收站里的项不会被重复「移入回收站」（按钮禁用，误触发只提示不重复处理） |
| 数据存档 | 内容寻址（SHA-256）快照：相同内容只存一份，支持历史对比、按条目还原（只还原与当前数据不一致的条目）、孤儿文件清理；自动清理策略可选按数量 / 容量 / 时间 / 关闭，超限时从最早的快照开始删除并始终保留最新一份。每条条目记录所属用户，还原时回到该用户的分类目录；**默认用户（管理员）**可查看 / 还原全部条目并整份「还原整个存档」，其他用户只看得到、也只还原得了自己的条目（「还原整个存档」按钮同样可见，只是范围收敛为本人）；存档内条目支持逐列筛选（含类型 / 所属用户 / 状态选项列与大小的「等于 / 大于 / 小于 / 区间」模式），并可按「用户名 / 分类」分组勾选分类、按当前存档的标签分组多选筛选（都带搜索与三态全选）；条目表首列也是勾选框，选择条上除三态「全选本页」与已选计数外还有「只选不一致 / 清空选择 / 下一个不一致」，打开存档时自动勾选不一致的条目、不一致行整行加粗且状态列标成强调色、双击不一致行直接跳到数据管理页定位该数据项；如果档案在仓库里、但库内那份文件被直接在磁盘上删掉了，条目会显示「库内文件已丢失」并在回档时把文件补回来（不会再误报「与当前数据一致」）；一旦有筛选，「还原整个存档」就变成「还原筛选结果（N 项）」只回档筛出来的条目；数据管理里改动数据后条目状态会自动重取（删除后立刻显示「已删除」，不再停在「与当前数据一致」）；新建快照只收录未删除项（回收站里的项不进存档），回档分恢复式（默认，保留现有数据）与覆盖式（以存档为镜像、多出来的项收进回收站，仅默认用户），整档回档的「回档变更」对话框里可切换并重新预演；「标记存档」可让快照不被自动清理删除，只有取消标记或手动删除才会消失（列表与详情都会标出【已标记】）；存档清单是分页表格，表头可拖动、列宽自适应、支持逐列筛选与一键重置（创建时间按天选「在该日 / 之后 / 之前 / 区间」，条目数 / 逻辑大小 / 实际占用 / 去重率可选「等于 / 大于 / 小于 / 区间」，并可按是否标记筛选）；列表首列是勾选框（与上方三态「全选本页」双向同步，点一下在全选 / 全不选之间切换），可**批量标记 / 批量取消标记 / 批量删除**，**已标记的存档不能直接删除**，需先取消标记；表格另带「逻辑大小 / 实际占用 / 去重率」三列（实际占用 = 这份存档的内容去重后真实落盘的字节数），表格上方统计行报「共 N 个存档，筛选后 M 个 · 总占用 X」，详情里给出包数与碎片率，对比标签按回档计划描述（与「无需回档」提示同源，不会再出现标签说有新增、提示却说一致的矛盾），并可用「校验存档」「优化空间」与（仅默认用户）「重新加载存档文件」做完整性校验 / 碎片整理 / 按存档重建物理层 |
| 数据特征 | 校验和、MIME、图片尺寸、感知哈希（dHash）、文本统计等，随导入自动生成 |
| 数据导出 | 导出选中项（文本写为 .txt，其它从仓库复制）并生成 CSV / JSON 清单 |
| 查看器 | 「查看器」页把**库里出现过的所有文件格式**列成清单（可按扩展名 / 插件名搜索，行内直接显示当前方式与库中条目数），选中后在右侧配置：**使用查看器**（该格式有多个查看器时还能指定具体插件，图片 / 视频 / 音频 / 文本 / Markdown / 压缩包 / 表格七种，可在插件页启停）、**继承系统默认**或**自定义程序**（可带 `{path}` 参数；留空时改为弹出系统的「打开方式」对话框），并可恢复默认与「测试打开」；规则存 `.configs/viewers.json`（含 `viewer_id`），没有规则时默认用内置查看器，指定 / 默认的内置查看器不可用时自动回退到系统默认；内置查看器依赖内置界面工具库（`builtin.lib.ui`）在程序本体之外弹出独立窗口显示，可用 Esc 或标题栏关闭按钮退出。插件可通过 `viewer.open` 扩展接口读写这些规则（含「某插件的所有格式都改用我打开」）。 |
| 编辑器 | 右键条目「编辑器 ▸」（系统默认程序 / 点名某个内置编辑器 / 交给系统选择…，子菜单由程序本体搭建，禁用编辑器工具库后仍在）由编辑器插件按扩展名决定怎么改：内置 `builtin.editor.text` 用程序内可编辑控件改文本 / 代码 / Markdown / CSV / JSON，保存后通过公开信号刷新条目的 checksum / 大小 / 内容；`builtin.editor.office` 对 doc/docx/xls/xlsx/ppt/pptx/pdf 等文档表格调用电脑系统默认程序编辑；没有装编辑器插件时退回系统默认编辑器。按扩展名的编辑器选择 / 继承系统默认 / 自定义程序规则存 `.configs/editors.json`，插件可通过 `editor.open` 扩展接口读写。 |
| 模型 | 内置库插件 `lib.model` 通过扩展接口 `model.open` 提供模型调用能力：**本地模型**（权重放**模型目录**下的 `local/`，模型目录默认是程序目录里的 `.models/`、可在「模型」页设置里「更改位置…」（选的目录只当容器、模型目录是它下面的 `.models`，与资源文件夹一个规矩；搬不动就不切设置、失败原因进页面提示与日志），也可扫描 / 拖拽登记用户自己目录里的权重，交给 worker 子进程 / llama-server / ollama 跑）与**外部模型**（OpenAI 兼容接口或 Ollama，填地址 / 模型名 / 密钥）统一登记在 `<模型目录>/registry.json`，设置与密钥存 `.configs/models.json`（密钥只存本地、日志与界面打码）；模型目录不在资源文件夹下（改「资源文件夹」不会再搬走权重，资源文件夹上锁也不牵连运行环境），升级前的旧位置 `.resources/models/` 会在第一次用到模型目录时**自动整体搬到** `<程序目录>/.models/`（同盘只是改名瞬间完成、跨盘整份复制且复制成功才删旧目录，登记表与权重不用重新登记），搬不动才继续用旧位置并在页面上说明原因；「模型」页可新建模型、一键下载（HuggingFace官方 + HF-Mirror镜像顺序回退、断点续传、sha256 校验）、扫描目录登记、加载 / 卸载 / 测试、查看下载队列与**运行环境**（每个 profile 一套独立 venv，装在模型目录的 `runtime/<profile>/venv` 下，模型目录路径太深会在开装前提示先改「模型目录」、用户不改就取消安装，绝不静默换盘；装依赖前弹确认、永不静默安装，**几个环境可同时装**、同一个环境不会重复开线程，一键补全前先选并发 / 挨个；带 `-gpu` 的环境会一并装上 CUDA 12 运行库，模型写 CPU 版 profile 也能用上已装的 `-gpu` 版、设备选到 CUDA 时默认全部层上卡；另有「本地 whl…」按钮可用手上的 `.whl` 离线安装、装好后按钮变灰，安装中「暂停 / 取消」立刻掐掉 pip 进程，卸载连日志一起清（模型卡片上也有「日志」按钮看最新一份，一个模型只留一份、删模型连日志一起删）；设置项**即改即存**，没有「保存设置」按钮；设置里还有「下载源（模型）」（HuggingFace官方 / HF-Mirror镜像 / 自定义下载路径）与「下载源（GitHub）」（Github官方 / ghproxy镜像 / gh-proxy镜像 / ghfast镜像 / 自定义网址，非官方档都留官方兜底），模型下载与装运行环境时 GitHub 上的资源（含 llama.cpp 的 CUDA 轮子）都按它取地址，pip 失败还会自动换镜像重试一次）；下载队列每个任务可单独暂停 / 继续 / 取消，队列右上角还有一键继续 / 一键暂停 / 一键取消（按当前是否有暂停 / 下载中的任务决定可用）；卡片上的「打开权重目录」要有权重才可点；点「下载」会**立即把文件排进下载队列**（只用本地信息算地址，不等联网探测），一个都没排上会说明原因；下完后卡片自动刷新、状态自动从「未填充」变成就绪（不用再手动换权重）。模型按需懒加载（`dm_plugin.lib.model.api.acquire()` 取租约、`close()` 归还），常驻上限默认 1、空闲默认 600 秒自动卸载；插件可通过 `model.open` 接口或 `dm_plugin.lib.model.api` 按 `model_id` / `capability` 取用（能力含 chat / completion / embedding / rerank / vision / asr / tts / image / ocr / classify）；设置页的「推理设备」下拉打开时后台探测机器**真实型号**（CPU 注册表名 + 显卡名，当前解释器用不了的显卡不列进下拉、改写在说明里），并可勾选「允许把依赖装进程序自己的 Python 环境」（勾选立即存盘生效；运行环境区的「安装」会先弹**两次确认**再往程序解释器里装，装完刷新状态，不勾则照旧在专用 venv 里装）。 |
| 自动标签 / 自动关键词 | 三个**外部功能插件**（默认不启用，启用与否完全不影响程序本体；规则、对齐表与「一键补全」的说明见 `docs/HELP.md` 的「自动标签与自动关键词」）：`auto_tag.rule` 按规则给文件挂标签（不调用模型）、`auto_tag` 模型自动标签（按数据类型对齐模型挂标签；与前者**互斥**：清单里互指 `conflicts`，不能同时启用）、`auto_keyword` 按数据类型生成关键词；三者共用共享库 `lib.autolabel`（规则模型 + 匹配引擎、数据类型→模型对齐表、批处理管线、共用控件）。规则选项写 `.configs/autolabel.rules.json`、各数据类型对齐方案写 `.configs/autolabel.align.json`（`label` 与 `keyword` 两段各存各的）；标签/关键词数量的最小、最大值是插件选项（`kind: "int"`），对齐表有「方案」列（只显示「启用」或「自定义」——跟随系统就是启用），每行可以在「设置对齐」编辑框里勾「启用系统方案」跟随系统（模型与对齐模型都用系统配好的）或取消勾选自己挑，也能「重置 / 全部重置」还原；「一键补全」把缺的模型登记成草稿、缺模型文件时弹**一个**确认框（按「权重 / 配置 / 分词器」分组列出缺哪些），确认后交给「模型」页那套下载队列（镜像回退 / 续传 / 暂停取消），**永不静默下载或安装**；运行环境只在「模型」页装。挂完标签后会广播一次标签变更（`app.sdk.items.notify_tags_changed()`），数据管理页与标签页当场就能看到新标签；批处理只在整批写完之后刷新一次，中途不刷。页面一律基于界面工具库 `builtin.lib.ui`。三个插件都有页面，导入页与数据管理页的入口只在插件启用时出现。 |
| 插件 | 所有插件统一放在 `plugins/<id>/`（`plugin.json` + 入口脚本），内置 7 个查看器分别依赖 `builtin.lib.viewer`（查看器工具库：基类 + 窗口外壳 + 注册表）与 `builtin.lib.ui`（界面工具库：弹窗外壳 + 页面模板 + 控件工厂 + 通用播放控件 `PlayerPanel`）；「插件」页可按贡献（按扩展点分组）/ 来源（内置 / 外部）/ 创建者 / 状态与关键词筛选，筛选后右上角显示「已筛选：X / 共 N 个插件」并给出「清除筛选」按钮，支持按名称 / 来源 / 创建者 / 状态 / 版本 / 贡献排序并切换正序、逆序，列表项右侧画状态徽章（已启用 / 已禁用 / 载入失败），文字是「名称 · 类型 · 贡献 · 来源」并按可用宽度省略（「全部贡献」下所有插件都会出现，含纯库插件），详情区标题下并排类型 / 来源 / 状态三枚徽章，再把「协议与接口」（贡献 / 依赖插件 / 扩展接口 / 提供库 / 入口文件）与「清单与选项」（清单数据 / 插件选项摘要 / 清单路径）分两栏列出；可启用 / 禁用、改显示名与备注、打开插件目录、删除外部插件、导入插件目录或 `.zip` 包（都可多选批量导入：一次选多个包 / 目录，装完给「导入成功 / 没导入」汇总，已装过的最后一次性问要不要覆盖）、把勾选（没勾选就是当前选中行）的插件导出成 `.zip`（一个插件一个包；选中多个则各自成包再套一层总包，默认存到设置里的「默认导出目录」、保存框里也能改地方，单个插件的包可以直接用「导入插件包」装回来（总包里的每个 `<id>.zip` 也是），`__pycache__` 缓存不会被打进去）；只有默认用户可以导入 / 启用 / 编辑 / 删除插件，其他用户可以查看、筛选与打开插件目录（页面会标注原因）；插件可在清单里用 `options` 声明用户可配置项（开关 / 文本 / 单选），「插件选项」对话框按声明生成控件并写回 `.configs/plugins.json`，还能一键把该插件查看器的格式全部改为用它打开；协议**不再区分插件类型**：清单用 `libraries` 声明对外提供的库模块、把数据放在插件自己的 `.data/` 目录（例如查看器的 `.data/viewer.json`），要复用别的插件就写 `depends` 再 `from dm_plugin.<id>.<模块> import ...` 静态导入（`library()` 兜底）；字段与规则见 **[插件协议](docs/PLUGIN_PROTOCOL.md)**，扩展点与事件见 **[扩展点与事件](docs/SDK.md)**；启用状态存 `.configs/plugins.json`，载入失败只会在列表里标为「异常」，不影响程序启动（启动时控制台先报「插件扫描完成：发现 N 个（启用 X、未启用 Y、清单有误 Z）」，载入完成后报汇总「插件载入：共 N 个（已启用 X、未启用 Y）；库插件 …、功能插件 …」）；程序本体通过 `app.ui` 扩展接口把「加导航页」的能力开放给插件（插件的页面随启停自动出现 / 消失），插件页面最多在左侧显示 7 个图标（超出名额的只出现在内置的「页面管理」页里，那一页点整行即进入对应页面），列表项可勾选（选择条上的三态全选框点一下在全选 / 全不选之间切换）并**批量启用 / 禁用 / 删除**（已处于目标状态的跳过、内置插件不可删），右侧功能按钮改为流式布局、窄窗口自动换行；完整协议与可用接口见 **[插件开发指南](docs/PLUGIN.md)** |
| 用户 | 多用户配置档（独立的「用户」页）：每个用户拥有独立的数据项、分类与标签，可设口令、随时切换；新建用户自动获得默认分类与标签。第一个用户是**默认用户（管理员）**，只有它能新建 / 重命名 / 删除其他用户并设置或清除其口令，自身不可删除；系统级操作（恢复初始化、资源文件夹维护、插件安装 / 启用 / 编辑 / 删除）也只有默认用户能做；普通用户可以修改自己的用户名与口令（口令用于切换与解锁隐藏数据）、清除自己的口令，但**不能删除当前用户**（卡片上的删除按钮置灰，服务层同样拒绝）；「用户」页以卡片网格展示每个用户，每张卡片都是自洽的「对象卡」：首字头像 + 用户名 + 徽标（当前用户 / 默认用户 / 已设口令）、数据项数 / 分类数 / 创建时间三行图标信息（完整摘要挂在卡片提示上），以及卡片内两列操作按钮（切换为当前用户 / 重命名 / 口令 / 清除口令 / 删除，按权限显隐，当前用户的「切换」按钮为禁用态）；卡片固定宽 320 px、高度互相对齐，窗口变宽只增加列数、不拉伸卡片，多余高度留在网格底部、卡片从左上角开始逐个排列，窗口变窄时仍完整显示卡片内容，当前用户卡片高亮并带「当前用户」徽标。删除用户时若该用户已有数据则并入默认用户（只镜像真正有数据的分类及其祖先分类，空分类与它空掉的磁盘目录一起剪掉、文件整目录搬进 `<库>/<默认用户>/<用户名>/`、标签移交并合并同名标签而没有任何数据项引用的标签直接删除、它创建的全局标签也改写创建者、存档条目一并转移归属）；若数据为空则直接清理其分类与用户文件夹，不会在默认用户下留下空分类或空目录 |；页面头部是统一的标题行（`page_header()`，右侧是「新建用户」主按钮），卡片网格整体装在一张「全部用户」分区卡片里
| 用户与口令 | 口令按用户独立保存（argon2id 优先、缺依赖时退回 PBKDF2-HMAC-SHA256，散列工具见 `src/app/core/runtime/security.py`）：切换用户、勾选「显示隐藏项」时校验当前用户口令 |
| 设置 | 主题（浅色 / 深色 / 跟随系统）、云母特效、导入策略、资源文件夹（更改位置 / 扫描并登记 / 重建目录结构 / 打开）、隐私保护（资源文件夹与隐藏文件两个开关：打开只是记下设置、程序退出后才锁定，关闭立刻放行；资源文件夹保护开启时隐藏开关置灰并自动收起）、存档自动清理开关与碎片整理阈值、回档预览开关（关闭后覆盖式回档不可用）、整份压缩上限 / 收益、日志文件模式（单文件追加 / 每次启动一个文件 / 每天一个文件 / 按大小切分）与保留策略（文件数 / 单文件大小 / 保留天数 / 总量上限，滑块左边另有输入框可精确填写）、日志级别、控制台按级别着色（INFO 绿 / WARNING 黄 / ERROR 红，写 `stdout` 避免 IDE 把整行 `stderr` 标红）、恢复初始化（清空数据并重置全部设置，完成后自动重启） |；页面头部给出统一的标题与说明，「库内容」分组改用分区卡片
| 交互反馈 | 长耗时操作（导入 / 扫描库文件夹 / 创建存档 / 重建索引）显示进行中提示并自动淡出；「重复项」按钮的工具提示显示当前范围的重复组数量；概览页「最近导入」条目可点击，跳转到数据管理页并选中该项、展开其分类；图标按钮带工具提示；窗口首次显示时自动居中 |
| 界面样式 | 所有页面共用同一套骨架：外边距 24/20、间距 12、面板卡片 12/12/12/12 或 16/14/16/14、统一的标题行与说明文字；按钮只用 qfluentwidgets 的 `PrimaryPushButton` / `PushButton`（图标 + 文本的改用 `framework/buttons.py` 的 `IconTextButton`：「设置 → 外观 → 简化显示」是三挡（**不简化 / 默认 / 完全简化**：默认只收「同一个容器里图标不重复」的按钮与标签，插件页三枚 `FluentIcon.EDIT` 按钮就保留文字；完全简化才全部收成居中的方形图标按钮、完整文字进提示条；旧布尔配置自动换算），所有页面与弹窗一起生效，退出简化显示时用 `setCustomStyleSheet()` 重装 qfluentwidgets 的按钮样式、按 `sizeHint` 恢复文字尺寸，没显示过的页面也照此还原，主色按钮 `IconTextPrimaryButton` 必须把 `PrimaryPushButton` 放在第一个基类，QMetaObject 链上才带得到强调色底与反转图标色）；设置页的 `ActionCard` 原地替换了 `PushSettingCard` 自带的原生按钮）；强调色统一取主题色 `themeColor()`（默认 `#009faa`），不再有写死的蓝/青；页面自己不铺底色：概览 / 导入 / 设置 / 用户四页也已去掉各自写死的浅 / 深 QSS（`common.page_background()` 已随 `common.py` 一起删除），底色统一由 `install_app_theme()` 装的调色板提供、滚动区一律由 `clear_scroll_background()` 透明化，切浅色后不会再露出旧调色板的深色块；常量与构件集中在 `src/app/ui/framework/` 与 `src/app/ui/components/`；页面 / 分区的静态说明不再常显，改挂在标题或控件的悬停提示上（悬停多久出提示由 `Layout/Tooltip-Delay` 决定，默认 2000 毫秒、0 表示立刻，写在卡片 / 列表行上的提示会被里面的子控件继承），问号标识只在页面标题旁保留一个（14 px），提示文本按 44 个半角宽自动折行；图标 + 文字的标签（筛选栏分组、字段名、两栏标题等）改用 `framework/labels.py` 的 `IconTextLabel`，非「不简化」挡位下只留图标（共用同一枚图标的标签保留文字，信息行传 `keep_text=True` 就始终保留文字）；数据管理页筛选栏最下面两个复选框用 `align_check_box()` 对齐指示器与文字；设置页任何配置切换都会在右上角给出提示（400 毫秒内的连续改动合并成一条），`tests/selfcheck.py` 的 `style_uniformity` / `theme_background` 会逐页校验 |；分区卡片统一用 `framework` 的 `section_card()`（`StrongBodyLabel` 标题 + `CaptionLabel` 说明 + 卡内布局），自适应高度的流式容器统一用 `components/flow_area.py` 的 `FlowArea`（增删控件后立即重排，隐藏期间 resize 不会把高度压成 0）

## 界面预览

<!-- 截图占位：把界面截图放进 docs/screenshots/ 后取消下面的注释
<p align="center">
  <img src="docs/screenshots/manage-page.png" width="760" alt="数据管理页">
  <img src="docs/screenshots/home-page.png" width="760" alt="概览页">
</p>
-->

> 首次运行前建议先执行 `scripts/seed_demo.py` 注入示例数据，可以立刻看到上表中的各项能力。

## 快速开始

### 环境要求

- **Windows 10 / 11**（打包脚本与路径处理按 Windows 设计）
- **Python 3.13 / 3.14**（开发与单测用 3.13.5；**打包必须用 3.13**——pyappify 启动器的发行包表
  `KNOWN_PATCHES` 只到 3.13.5，写 3.14 会在 setup 阶段直接失败；3.13 没有标准库 `compression.zstd`，
  由 `requirements.txt` 里的官方回移版 `backports.zstd` 顶上，压缩行为与 3.14 一致）
- 依赖见 [`requirements.txt`](requirements.txt)：界面与基础 5 条（PyQt6 6.11.0、PyQt6-Fluent-Widgets 1.11.3、
  SQLAlchemy 2.0.52、loguru 0.7.3、Pillow 12.3.0）+ 随程序一起装的加速 / 容错 / 功能库（orjson、fastjsonschema、
  argon2-cffi、watchdog、puremagic、charset-normalizer、pymupdf、jieba、usearch、sqlite-vec、py7zr、pyzipper、rarfile、
  以及 3.13 上补 zstd 的 `backports.zstd`）
  —— 装完即可用，不需要再手动补包；只有 `huggingface_hub` / `hf-transfer` 刻意不装（只影响模型下载快不快），
  每项库提供什么能力、没装会有什么结果见 `docs/DEPENDENCIES.md`

### 安装

```powershell
git clone https://github.com/taishoubuzhi/D-MyDataManager.git
cd D-MyDataManager
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 运行

在 PyCharm 中：把 `src` 目录标记为 **Sources Root**（右键 → Mark Directory as → Sources Root），
然后运行 `src/main.py`。

命令行：

```powershell
.venv\Scripts\python.exe src\main.py
```

首次启动会自动创建数据库、默认分类（学习资料 / 工作文档 / 图片素材 / 影音资料 / 未分类）、默认标签
（重要 / 待整理 / 收藏）与资源文件夹（默认 `.resources`，内含唯一的库文件夹 `.resources/library`），并写入 `.configs/config.json`。
运行期数据（`.configs/`、`.resources/`、`.logs/`）都在项目根目录，且不纳入版本控制。
之后新建的用户同样会获得这套默认分类与标签，并在库文件夹下获得自己的用户名文件夹。

从旧版多库布局升级时，启动会自动迁移一次：先把数据库备份到
`<库>/全局/backups/data-before-layout-v2-<时间戳>.db.bak`，再把文件搬进各用户名文件夹并改写记录，
最后写入布局标记 `<库>/全局/.datamanager/layout-2.json`（已迁移过的库不会重复处理）。
布局迁移之后还会执行一次「未分类归置」：给每个用户补齐固定的「未分类」根分类，并把 `category_id` 为空的历史数据项归入其中
（文件搬进 `<库>/<用户名>/未分类/`），这一步同样幂等。

### 示例数据（快速上手）

```powershell
.venv\Scripts\python.exe scripts\seed_demo.py
```

脚本先清空运行期数据，再向真实环境注入 100 余条示例数据：图片 / 视频 / 音频 / 文档 / 表格 / 演示 / 压缩包 /
代码 / 各类文本（含嵌套目录、隐藏项、回收站项、重复内容与一个存档快照），每条数据带 2~3 个标签与 2~3 个关键词，
并按类型归入分类树与标签；其中「年度归档」「公共素材」是全局标签（所有用户共用），其余为各用户的个人标签。
另外还建 3 个测试用户（演示用户 / 家人共享 / 归档账号），每个用户各有一套分类、标签与数据。
打开「标签」页可以看到全局 / 个人两类标签的归属与创建者。
导入后**不会自动删除**，可直接启动应用体验；原始文件保留在 `.resources/demo/`，便于与库内条目对照。

清空这些数据：应用内「设置 → 维护 → 恢复初始化」（同时把所有设置重置为默认值），
或命令行 `.venv\Scripts\python.exe scripts\dev_reset.py`（只清数据、不动设置）。

## 库文件夹结构

资源文件夹**全局唯一**（配置项 `Storage/Resource-Path`，默认项目根的 `.resources`，旧 `resources/` 启动时自动改名），数据库 `data.db` 与库文件夹 `library/` 都在它下面；
可在「设置 → 资源文件夹」中更改位置（选的是容器目录、资源文件夹是它下面的 `.resources`；目标位置里已经有一个 `.resources` 时会问要不要删掉重搬；
搬不动会把原文件夹与配置都退回去、绝不留下半份，搬迁成功后自动写回库路径并重启）、扫描登记、重建目录结构或打开。
它的结构固定为「一个全局文件夹 + 每个用户一个用户名文件夹」：

```text
<库文件夹>/
  全局/                       全局资源
    store/                    内容寻址备份仓库（ab/cd/<sha256>）
    covers/                   图片缩略图缓存
    backups/                  数据库备份（含布局迁移前的 data-before-layout-v2-*.db.bak）
    .datamanager/             库元数据（含布局标记 layout-2.json）
  默认用户/                   每个用户一个用户名文件夹
    工作文档/                 该用户的分类目录
      report.pdf              数据文件按原名存放
      .hiddens/               该分类下隐藏的数据文件（物理隔离存放）
  演示用户/
```

- **全局**：存全局资源 —— `store/`（内容寻址备份仓库）、`covers/`（封面缓存）、`backups/`（数据库备份）、
  `.datamanager/`（库元数据与布局标记）。
- **<用户名>**：该用户的数据与分类目录，数据文件按「分类目录 / 原文件名」真实存放，因此各用户的分类互不影响；
  用户改名时其文件夹会同步改名。
- 导入时的目标固定为唯一的库文件夹（无需也无法选择库），数据项直接落在当前用户的用户名文件夹下。
- 外部往库内放了文件后，可在「设置 → 资源文件夹」执行「扫描并登记」，把库内文件（按同名分类目录归类）登记为数据项；
  库目录有外部改动时界面会提示。

## 项目结构

```text
src/
  main.py                     程序入口
  app/
    core/                     配置（`config.py`）、路径与目录迁移、日志、信号总线、口令散列、ACL 权限封装、系统调用，以及两组子包：`core/runtime/`（版本常量来自 `runtime.json`、模块数据加载器 `module_data.py`）与 `core/plugins/`（插件清单解析与依赖排序 `plugin_core`、插件选项协议 `plugin_options`、扩展接口注册表 `extensions`、主程序界面扩展接口 `app.ui`）；页面清单与侧栏装配在 `app/ui/main_window`
    sdk/                      插件 SDK（插件唯一可见的程序面）：协议与上下文（`Plugin` / `PluginContext` / 扩展点 / 事件 / 版本范围）、数据工具 `app.sdk.data`、界面工具 `app.sdk.ui`、配置读写 `app.sdk.storage`；查看器 / 编辑器 / 模型的专属门面已不在 SDK 里：程序侧是 `app/services/{viewer,editor}_service.py`，模型侧归插件 `plugins/lib.model/`
    db/                       SQLAlchemy 模型、引擎、初始数据
    repositories/             数据访问层（查询与持久化）
    services/                 业务层（导入、条目、分类标签、导出、存档、用户与口令、隐私保护与 ACL 锁定、统计）
    ui/                       界面层
      framework/              页面骨架（Page / ScrollPage、标题行、卡片、toast / confirm）、间距常量（住在 `app.sdk.ui`）与主题色
      dialogs.py              文本输入与条目编辑对话框
      main_window.py          主窗口与导航
      pages/                  首页、导入、数据管理、标签、用户、存档、插件、页面管理、设置（「查看器」页由插件 builtin.lib.viewer 提供）
      components/             关键词输入与标签选择、拖放区、条目卡片、筛选面板、分类树、分页控件、封面加载器、库目录监听、数据表格、流式容器
    resource/                 图标与翻译资源
.configs/                     用户配置（首次运行自动生成，不随代码分发）
.resources/                   资源文件夹（数据库 + 库文件夹；旧 resources/ 启动时自动改名，可整体搬迁）
  data.db                     SQLite 数据库（「设置 → 资源文件夹 → 更改位置」）
  library/                    唯一的库文件夹（分类树就是这里的目录结构：一层目录 = 一级分类，「未分类」= 用户名文件夹本身）
    <用户名>/<分类目录>/      数据文件
    <用户名>/<文件名>         「未分类」的数据文件
    <用户名>/<分类目录>/.hiddens/  隐藏数据文件（可单独 ACL 锁定）
  demo/                       示例数据脚本生成的原始文件
.logs/                       运行日志
docs/                        文档（用户帮助 HELP.md、插件开发指南 PLUGIN.md、插件协议 PLUGIN_PROTOCOL.md、SDK.md、清单协议 MANIFEST_PROTOCOL.md、配置 CONFIG.md、依赖 DEPENDENCIES.md、脚本协议 SCRIPTS.md、测试协议 TESTS.md；随发布包分发）
scripts/                      程序运行 / 运维脚本（自检套件 selfcheck.py + selfcheck/、共用临时环境 tmpenv.py、示例数据 seed_demo.py、恢复初始化 dev_reset.py、插件桩 plugin_stubs.py、性能基准 benchmark.py；协议见 docs/SCRIPTS.md）
plugins/                      插件目录（内置库插件 builtin.lib.viewer / builtin.lib.editor、内置弹窗工具库 builtin.lib.ui、模型工具库 lib.model、自动标签共享库 lib.autolabel、7 个查看器插件、内置编辑器插件 builtin.editor.text / builtin.editor.office、三个默认不启用的功能插件 auto_tag.rule / auto_tag / auto_keyword 与示例插件 example.ui_extension / example.model_usage；每个插件一个子目录，含 plugin.json 与 PLUGIN.md，协议见 docs/PLUGIN_PROTOCOL.md）
stubs/                        插件桩（dm_plugin 的 .pyi，由 scripts/plugin_stubs.py 按插件清单生成，给 IDE 解析 `from dm_plugin...` 用）
tests/                        开发期测试与门禁（按被测对象分 core/ services/ sdk/ plugins/ 四个单元测试包 + 隔离基类 harness.py + 语料 dataset.py；门禁聚合 verify.py、打包冒烟 smoke_checkout.py、可选依赖验收 verify_optional_absence.py；协议与每个文件的作用见 docs/TESTS.md）
pyappify.yml                  打包配置（PyAppify）
icons/                        打包用图标（icon.ico / icon.png）
.github/workflows/build.yml   推送 v* 标签时自动打包并发布
```

## 数据安全

- 文件按 SHA-256 内容寻址存储，同一份内容重复导入不会重复占用磁盘。
- 删除默认为软删除；彻底删除会同时释放仓库中不再被引用的文件。
- 存档是"快照 + 引用"，不做版本控制的二进制 diff，因此对图片、视频等任意类型都有效。
- 存档自动清理在「设置 → 存储」配置（按数量 / 容量 / 时间 / 关闭 + 自动清理开关），启动后、创建存档时、
  删除存档后与「按策略清理」时自动执行；存档页的「按策略清理」可立即执行一次，清理会同时释放不再被任何快照
  或数据项引用的内容文件，并收掉删空后遗留的哈希空目录；有内容还在用旧的压缩编码时会一并按当前方案重压
  （存档页的「优化空间」按钮可手动做同一件事）。
- 所有内容都**整份压缩**后按内容寻址存一个文件（不分块、不打包），压缩方案按数据类型挑选：文本类优先
  **zstd**（3.14+ 是标准库 `compression.zstd`，3.13 及更早是随依赖装的 `backports.zstd`，两者帧格式互通），
  两处都没有时才退回 deflate；已压缩格式与压不动的内容直接原样保存，旧编码内容由
  `needs_recode()` / `recompress()` 升级。
- 存档只收录**未删除**的数据项（回收站项不进新快照），快照建好后被删掉的项在还原时按「撤销删除」恢复。
- 回档有**恢复式**（默认，保留现有数据）与**覆盖式**（以存档为镜像，把多出来的现有项收进回收站，仅默认用户）两种；
  整档回档的「回档变更」对话框里可直接切换，切换会重新预演并刷新清单；关闭 `Archive/Restore-Preview` 后只走恢复式。
- 存档页可「校验存档」（整库比对索引与真实文件，深度校验会重算每份内容的校验和，结果同时写进日志）、
  对表格里的「逻辑大小 / 实际占用 / 去重率」三个列与上方的仓库统计了解空间占用，并可用
  「重新加载存档文件」（仅默认用户）在索引与真实文件对不上时按存档重建物理层（从分块版本升级后也用它重建一次内容文件）。
- 已标记的存档（`Archive.pinned`）不参与自动清理，需要先取消标记，或在存档页手动删除。
- 全文检索使用 SQLite FTS5 虚拟表 `items_fts`（trigram 分词），由触发器随数据增删改实时同步；
  检索异常时可在「设置 → 存储 → 重建索引」中重建。短于 3 个字符的词自动退回 `LIKE` 匹配。
- 口令按用户保存，只存 PBKDF2 派生的哈希，无法从配置中还原出原文。
- 隐藏的数据项是**物理隔离**：文件被搬进所属分类目录下的 `.hiddens/`（库内相对路径仍记在数据项上）；
  可用「设置 → 隐私保护」把资源文件夹与各个 `.hiddens/` 目录用 `icacls` 拒绝 Everyone 读取（并去掉继承）。
  保护是**静态**的：程序启动时整场放行、退出时才锁定（打开开关只是记下设置，关闭开关立刻放行）——
  ACL 无法区分同一用户下的进程，瞬时放行窗口既慢（每次 2 个 `icacls`）又不可靠，因此改为运行期常开。
  异常退出（停电、强杀）留下的解锁状态由启动自愈兜住：先把资源根与各 `.hiddens` 强制放行一次，
  建目录失败只告警，初始化失败还会关掉保护开关重试，并可用 `python src/main.py --unlock` 应急放行；
  `.configs/session.json` 标记上次会话是否正常结束。非 Windows 平台自动跳过。
- 数据库带结构版本号（`app_meta.schema_version`）：结构升级时先把旧库另存为
  `.resources/data.db.bak-<时间戳>`，再重建空库，避免旧结构静默不兼容。
- 库目录结构带布局版本号（`<库>/全局/.datamanager/layout-2.json`）：首次运行会先备份数据库到
  `<库>/全局/backups/data-before-layout-v2-<时间戳>.db.bak`，再按用户名文件夹重排文件与记录；迁移可重复执行。

更多细节（标签体系、用户与权限、数据管理页交互、日志保留策略）见 [`docs/HELP.md`](docs/HELP.md)。

## 开发与自检

界面与数据层的约定、开发过程中的设计记录见 [`docs/HELP.md`](docs/HELP.md)。

### 自检脚本

```powershell
.venv\Scripts\python.exe tests\verify.py                   # 一条命令跑完验收门禁（编译 / 桩 / 单测 / 自检 / pytest / 可选依赖 / 打包冒烟）
.venv\Scripts\python.exe tests\verify.py --quick           # 只跑快的四步（编译 / 桩 / 可选依赖 / 打包冒烟）
.venv\Scripts\python.exe tests\verify.py --list            # 列出步骤名
.venv\Scripts\python.exe scripts\selfcheck.py                 # 全量自检：四层一次跑完，末行 RESULT failures=0
.venv\Scripts\python.exe scripts\selfcheck.py --list          # 列出全部检查（名称 / 分层 / 一句话说明）
.venv\Scripts\python.exe scripts\selfcheck.py --layer services  # 只跑某一层（data / services / pages / flows）
.venv\Scripts\python.exe scripts\selfcheck.py --only manage_selection,user_journey --verbose  # 只跑指定检查，可逗号分隔
.venv\Scripts\python.exe scripts\selfcheck.py --json          # 每项一条 JSON，便于脚本抓取
.venv\Scripts\python.exe scripts\selfcheck.py --keep-db       # 保留临时库目录，便于排查
.venv\Scripts\python.exe src\main.py --self-check             # 等价于全量自检；没有 scripts/selfcheck.py 时退化为「建好界面就退出」的冒烟测试
.venv\Scripts\python.exe scripts\dev_reset.py                 # 清空数据库与运行期目录，恢复到首次运行状态
.venv\Scripts\python.exe scripts\seed_demo.py                 # 注入约 100 条示例数据，方便直接体验各功能
```

`tests/verify.py` 是门禁聚合入口：把上面这些脚本按固定顺序跑一遍，逐步打印 `ok` / `FAIL`，末行 `RESULT failures=N`；
它自动带好 `PYTHONIOENCODING=utf-8` 与 `QT_QPA_PLATFORM=offscreen`，并把 `pages` 层按 12 项一组拆进独立进程
（`pages` 的 70 项 Qt 检查在同一长驻进程里偶发原生崩溃）；selfcheck 各步遇到「原生崩溃且没有任何 `FAIL` 行」时
会自动重跑一次（本机 Qt offscreen 的已知环境问题，不是检查失败）。

新套件自带隔离：在临时目录里新建数据库与配置，**不碰真实的 `.resources/`、`.configs/`**，所以无需备份还原、随时可重跑；
无图形界面的环境可先设 `$env:QT_QPA_PLATFORM='offscreen'`。界面层检查的覆盖面与旧的 34 项界面检查一致。

旧套件（`scripts/dev_check.py` / `dev_check_services.py` / `dev_check_ui.py` / `dev_check_flow.py`，34 项界面检查）
已在新套件达到功能对等后删除；需要对照旧实现时看 git 历史。

### 单元测试

```powershell
.venv\Scripts\python.exe -m unittest tests.services.test_item_api -v            # 只跑相关主题（按被测对象选单元目录）
.venv\Scripts\python.exe -m unittest tests.core.test_manifest tests.services.test_tag_permissions -v   # 多个相关模块
$env:DM_KEEP_TMP=1                                                                # 保留 tests/.tmp/ 便于排查
```

- 用例按主题拆分，**改哪块代码只跑哪块的模块**，不再全量 `unittest discover`；整体回归交给 `scripts/selfcheck.py`
  （`data` / `services` / `pages` / `flows` 四层，末行 `RESULT failures=N`）。
- 新增用例的范式（文件名、基类、用例命名、模板、隔离方式）见 [`docs/TESTS.md`](docs/TESTS.md)。
- `tests/harness.py` 的 `IsolatedCase` 把数据库、库文件夹、内容仓库、封面与导出目录重定向到
  `tests/.tmp/<用例类名>/`，用例之间互不影响，也不会碰真实的 `.resources/`、`.configs/`、`.logs/`。
- `tests/dataset.py` 生成一份多样化语料并写入临时目录：真实编码的 PNG / JPEG / WEBP / TIFF / 动画 GIF / ICO、
  伪造魔数的视频与音频、真实 ZIP / TAR / WAV / SQLite / PDF、docx / xlsx / pptx / odt / epub，
  以及各类文本（空文件、超过 512 KiB 的大文本、无扩展名、大小写、空格与特殊字符、隐藏文件），
  另有嵌套子目录、空目录和用于查重的同内容副本；`scripts/seed_demo.py` 也用它注入示例数据。

## 打包与发布

使用 [PyAppify](https://github.com/ok-oldking/pyappify) 打包：启动器本体约 3 MB，首次运行时按标签克隆本仓库、
创建独立 venv 并安装 `requirements.txt`；之后的更新是增量拉取，通常一两秒完成。因此**发布新版本就是打标签**。

- 配置：[`pyappify.yml`](pyappify.yml)（应用名、图标，以及 `profiles`：仓库地址、入口 `src/main.py`、
  `requires_python: "3.13"`、依赖文件）。启动器 pyappify 的发行包表 `KNOWN_PATCHES` 目前硬编码到
  3.13.5，写 `"3.14"`/`"3.14.5"` 都会在 setup 阶段报 `Unsupported major.minor version for resolving
  latest patch`，所以打包锁 3.13；zstd 在 3.13 由 `backports.zstd` 提供，两条路行为一致。
- 图标：`icons/icon.ico`、`icons/icon.png`（与窗口图标 `src/app/resource/images/logo.png` 同源，均由本地原图 `icons/数据管理器软件图标生成.png`（未纳入版本库）裁圆、去水印、透明背景后生成；ico 含 16/24/32/48/64/128/256 多分辨率）。
- 自动打包：推送 `v*` 标签会触发 [`.github/workflows/build.yml`](.github/workflows/build.yml)，
  由 `ok-oldking/pyappify-action` 构建 `D-MyDataManager-win32-release-setup.exe`（在线安装包）、
  `D-MyDataManager-win32.zip` 等产物，并上传到对应的 Release。

发布流程：

```powershell
git tag v1.0.0
git push origin v1.0.0
```

用户端启动器的「更新」按标签拉取：手动模式下有新版本会提示，自动模式下启动前先更新再运行，
更新说明取自两个标签之间的提交记录。

> 启动器需要能从 GitHub 克隆代码仓库，因此代码仓库要公开可见；若仓库是私有的，使用者需要在本机配置好
> Git 凭据，否则首次安装会失败。

## 贡献

- 发现 Bug 或有功能建议，欢迎开 [Issue](https://github.com/taishoubuzhi/D-MyDataManager/issues)；
  提 Issue 时请附上系统版本、Python 版本与 `.logs/` 中对应的日志片段。
- 提交代码前请确保：`.venv\Scripts\python.exe -m compileall -q src` 无输出、
  相关主题的单元测试通过（只跑改动涉及的模块，范式见 [`docs/TESTS.md`](docs/TESTS.md)）、`scripts/selfcheck.py` 全绿（末行 `RESULT failures=0`）。
- 代码风格：界面文案与注释使用中文；分层保持 `ui → services → repositories → db` 单向依赖。

## 许可证

本项目以 [**GNU General Public License v3.0**](https://www.gnu.org/licenses/gpl-3.0.html)（GPL-3.0）发布，
完整许可文本见仓库根目录的 [`LICENSE`](LICENSE)。

**结论：GPL-3.0 仍然正确。** 决定性原因没变——核心依赖 **PyQt6** 与 **PyQt6-Fluent-Widgets** 都是 GPL-3.0
（GPL 具有传染性），任何链接它们的发行版都必须以 GPL-3.0 发布并提供完整源码。后来加入的这批库经逐项核对
（按已安装的包元数据）全部与 GPL-3.0 兼容，没有冲突：宽松许可（MIT / BSD / ISC / Apache-2.0）、MPL-2.0、
LGPL 三类都不构成障碍（LGPL 在 Python 里以模块动态导入，且 LGPL-2.1-or-later 本身允许升级到 GPL）。

唯一需要额外说明的是 **PyMuPDF（含其内置的 MuPDF）是 AGPL-3.0 或商业授权**：GPLv3 §13 允许把它与 GPLv3 的
作品组合分发，代价是组合体要一并满足 AGPLv3 §13——**如果把本程序作为网络服务提供给他人使用，必须向这些
使用者提供完整源码**（单机桌面运行不触发该条）。需要闭源分发、或想去掉这条约束，就得购买 PyMuPDF 的商业授权，
或移除 PDF 正文抽取 / 页面渲染这两项能力。

| 依赖 | 版本 | 许可证 | 说明 |
| --- | --- | --- | --- |
| [PyQt6](https://pypi.org/project/PyQt6/) | 6.11.0 | GPL-3.0-only / 商业授权 | 决定本项目采用 GPL-3.0 |
| [PyQt6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) | 1.11.3 | GPL-3.0 / 商业授权 | 同上 |
| [PyQt6-Qt6](https://pypi.org/project/PyQt6-Qt6/)（Qt 运行库） | 6.11.2 | LGPL-3.0 | 动态加载，保留用户可替换 Qt 的能力即可 |
| [PyQt6-sip](https://pypi.org/project/PyQt6-sip/) | 13.13.0 | BSD-2-Clause | 宽松 |
| [PyMuPDF](https://pypi.org/project/PyMuPDF/) | 1.28.2 | **AGPL-3.0** / 商业授权 | 见上；只用于 PDF 抽取与渲染 |
| [SQLAlchemy](https://www.sqlalchemy.org/) | 2.0.52 | MIT | 宽松 |
| [loguru](https://github.com/Delgan/loguru) | 0.7.3 | MIT | 宽松 |
| [Pillow](https://python-pillow.org/) | 12.3.0 | MIT-CMU | 宽松 |
| [orjson](https://github.com/ijl/orjson) | 3.12.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | 均与 GPL-3.0 兼容 |
| [fastjsonschema](https://github.com/horejsek/python-fastjsonschema) | 2.22.2 | BSD-3-Clause | 宽松 |
| [argon2-cffi](https://github.com/hynek/argon2-cffi) | 25.1.0 | MIT | 宽松 |
| [watchdog](https://github.com/gorakhargosh/watchdog) | 6.0.0 | Apache-2.0 | 与 GPL-3.0 兼容 |
| [puremagic](https://github.com/cdgriffith/puremagic) | 2.2.0 | MIT | 宽松 |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | 3.5.2 | MIT | 宽松 |
| [jieba](https://github.com/fxsjy/jieba) | 0.42.1 | MIT | 宽松 |
| [usearch](https://github.com/unum-cloud/usearch) | 2.26.4 | Apache-2.0 | 与 GPL-3.0 兼容 |
| [sqlite-vec](https://github.com/asg017/sqlite-vec) | 0.1.9 | MIT / Apache-2.0 | 与 GPL-3.0 兼容 |
| [py7zr](https://github.com/miurahr/py7zr) | 1.1.3 | LGPL-2.1-or-later | 以模块动态导入；LGPL 允许升级到 GPL |
| [pyzipper](https://github.com/danifus/pyzipper) | 0.4.0 | MIT | 宽松 |
| [rarfile](https://github.com/markokr/rarfile) | 4.5 | ISC | 宽松；解包用的 `bsdtar` / `unrar` 由系统提供，不随本包分发 |
| [backports.zstd](https://github.com/rogdham/backports.zstd) | 1.7.0 | PSF-2.0 | 3.13 及更早的 zstd 来源（3.14+ 由标准库提供）；与 CPython 同许可 |

其余随解释器分发的组件（`sqlite3` / `zlib` / `lzma` / `bz2` / `compression.zstd`（3.14 起）等）按 CPython 的 PSF 许可提供。
依赖的用途与「缺了会怎样」见 [`docs/DEPENDENCIES.md`](docs/DEPENDENCIES.md)。

## 致谢

- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)：Fluent Design 组件库，界面风格来源。
- [ok-oldking/pyappify](https://github.com/ok-oldking/pyappify) 与 `pyappify-action`：打包与自动发布。
- [Qt](https://www.qt.io/) / [SQLAlchemy](https://www.sqlalchemy.org/) / [loguru](https://github.com/Delgan/loguru) /
  [Pillow](https://python-pillow.org/) 及其社区。
