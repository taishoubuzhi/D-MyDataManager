# 个人数据管理器（D-MyDataManager）

[![license](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
![python](https://img.shields.io/badge/python-3.13%2B-blue)
![platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![PyQt6](https://img.shields.io/badge/PyQt6-6.11.0-41cd52)
![tests](https://img.shields.io/badge/tests-147%20passed-brightgreen)

一个**纯本地**的个人数据管理器，基于 **PyQt6 + PyQt6-Fluent-Widgets** 构建。它把散落在电脑里的资料
（文档、图片、视频、音频、代码、压缩包……）集中到一个「库文件夹」中统一管理，提供导入、分类树、
全局/个人标签、SQLite FTS5 全文检索、回收站、内容寻址存档、导出与多用户口令保护等能力，
界面为 Windows 11 Fluent 风格。

- **完全离线**：数据只存在本机，不联网、不上传、无账号体系。
- **多用户**：每个用户拥有独立的数据、分类与标签，可设口令、随时切换；默认用户（管理员）可管理其他用户。
- **内容寻址**：文件按 SHA-256 存储，同一份内容重复导入不重复占空间；存档是"快照 + 引用"，
  对图片、视频等任意类型都有效。
- **库文件夹即目录**：数据按「用户名 / 分类目录 / 原文件名」真实存放，库目录可直接用资源管理器查看、备份。

## 目录

- [功能一览](#功能一览)
- [界面预览](#界面预览)
- [快速开始](#快速开始)
- [库文件夹结构](#库文件夹结构)
- [项目结构](#项目结构)
- [数据安全](#数据安全)
- [开发与自检](#开发与自检)
- [打包与发布](#打包与发布)
- [贡献](#贡献)
- [许可证](#许可证)
- [致谢](#致谢)

## 功能一览

| 模块 | 说明 |
| --- | --- |
| 数据导入 | 文本直接录入；文件 / 整个文件夹批量导入（含递归），支持按时间命名、重复策略、自动生成封面与特征；标签既可直接输入，也可从当前用户的已有标签中多选（全局标签标注「（全局）」） |
| 库文件夹 | **全局唯一**的库文件夹：内部一个「全局」文件夹（内容寻址备份、封面缓存、数据库备份与元数据）与每个用户一个用户名文件夹；数据文件按「用户名 / 分类目录 / 原文件名」真实存放，可执行「扫描并登记」把已有文件纳入管理 |
| 数据管理 | 列表 / 卡片两种视图，全文检索（FTS5，支持多词 AND 与中文子串），按类型 / 标签 / 关键词 / 分类筛选与排序（关键词为多选、需全部命中）；右侧筛选栏每个分组都可折叠、选项区内部可滚动，标题右侧带搜索框与三态全选框（空 = 全不选、横杠 = 部分选中、勾 = 全选，与分组内勾选双向同步）；上方工具栏为流式布局，宽度不足自动换行、超过两行可纵向滚动；分页浏览（每页 50 / 100 / 200 / 500 条，跨页多选保持），多选批量操作；缩略图在工作线程异步加载并做 LRU 缓存；卡片视图与首页统计卡按窗口宽度自适应分列；重复内容分组清理 |
| 数据分类 | 无限层级分类树，支持新建、重命名、移动、删除（子分类上移或整体删除） |
| 数据标签 | 标签库 + 关键词双轨：标签是受管理的实体（可重命名、合并、清理未使用），分为**全局标签**（所有用户可见共用）与**个人标签**（仅创建者可见，且不得与已有全局标签重名），记录创建者；独立的「标签」页可新建、改归属、重命名、删除、清理未使用；关键词是数据项上的自由词，可在导入时填写并在列表 / 卡片中显示 |
| 回收站 | 软删除，可还原；彻底删除时才释放底层文件 |
| 数据存档 | 内容寻址（SHA-256）快照：相同内容只存一份，支持历史对比、按条目还原（只还原与当前数据不一致的条目）、孤儿文件清理；自动清理策略可选按数量 / 容量 / 时间 / 关闭，超限时从最早的快照开始删除并始终保留最新一份。每条条目记录所属用户，还原时回到该用户的分类目录；**默认用户（管理员）**可查看 / 还原全部条目并「还原整个存档」，其他用户只看得到、也只还原得了自己的条目 |
| 数据特征 | 校验和、MIME、图片尺寸、感知哈希（dHash）、文本统计等，随导入自动生成 |
| 数据导出 | 导出选中项（文本写为 .txt，其它从仓库复制）并生成 CSV / JSON 清单 |
| 用户 | 多用户配置档（独立的「用户」页）：每个用户拥有独立的数据项、分类与标签，可设口令、随时切换；新建用户自动获得默认分类与标签。第一个用户是**默认用户（管理员）**，只有它能新建 / 重命名 / 删除其他用户并设置或清除其口令，自身不可删除；普通用户可以修改自己的用户名与口令（口令用于切换与解锁隐藏数据）、清除自己的口令，也可以删除自己（数据与标签一并并入默认用户）。删除用户时其数据并入默认用户：分类以「用户名」为一级分类镜像过去，文件整目录搬进 `<库>/<默认用户>/<用户名>/`，标签移交并合并同名标签（`created_by` 一并改写），存档条目一并转移归属 |
| 用户与口令 | 口令按用户独立保存（PBKDF2-HMAC-SHA256，20 万次迭代，散列工具见 `src/app/core/security.py`）：切换用户、勾选「显示隐藏项」时校验当前用户口令 |
| 设置 | 主题（浅色 / 深色 / 跟随系统）、云母特效、导入策略、库文件夹、存档自动清理、日志文件模式（单文件追加 / 每次启动一个文件 / 每天一个文件 / 按大小切分）与保留策略（文件数 / 单文件大小 / 保留天数 / 总量上限）、日志级别、恢复初始化（清空数据并重置全部设置，完成后自动重启） |
| 交互反馈 | 长耗时操作（导入 / 扫描库文件夹 / 创建存档 / 重建索引）显示进行中提示并自动淡出；「重复项」按钮的工具提示显示当前范围的重复组数量；概览页「最近导入」条目可点击，跳转到数据管理页并选中该项、展开其分类；图标按钮带工具提示；窗口首次显示时自动居中 |

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
- **Python 3.13 或更高**（开发与单测在 3.14.5 上验证通过；PyAppify 打包使用 3.13）
- 依赖见 [`requirements.txt`](requirements.txt)：PyQt6 6.11.0、PyQt6-Fluent-Widgets 1.11.3、
  SQLAlchemy 2.0.52、loguru 0.7.3、Pillow 12.3.0

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

首次启动会自动创建数据库、默认分类（学习资料 / 工作文档 / 图片素材 / 影音资料）、默认标签
（重要 / 待整理 / 收藏）与唯一的库文件夹（默认 `resources/library`），并写入 `config/config.json`。
运行期数据（`config/`、`resources/`、`logs/`）都在项目根目录，且不纳入版本控制。
之后新建的用户同样会获得这套默认分类与标签，并在库文件夹下获得自己的用户名文件夹。

从旧版多库布局升级时，启动会自动迁移一次：先把数据库备份到
`<库>/全局/backups/data-before-layout-v2-<时间戳>.db.bak`，再把文件搬进各用户名文件夹并改写记录，
最后写入布局标记 `<库>/全局/.datamanager/layout-2.json`（已迁移过的库不会重复处理）。

### 示例数据（快速上手）

```powershell
.venv\Scripts\python.exe scripts\seed_demo.py
```

脚本先清空运行期数据，再向真实环境注入 100 余条示例数据：图片 / 视频 / 音频 / 文档 / 表格 / 演示 / 压缩包 /
代码 / 各类文本（含嵌套目录、隐藏项、回收站项、重复内容与一个存档快照），每条数据带 2~3 个标签与 2~3 个关键词，
并按类型归入分类树与标签；其中「年度归档」「公共素材」是全局标签（所有用户共用），其余为各用户的个人标签。
另外还建 3 个测试用户（演示用户 / 家人共享 / 归档账号），每个用户各有一套分类、标签与数据。
打开「标签」页可以看到全局 / 个人两类标签的归属与创建者。
导入后**不会自动删除**，可直接启动应用体验；原始文件保留在 `resources/demo/`，便于与库内条目对照。

清空这些数据：应用内「设置 → 维护 → 恢复初始化」（同时把所有设置重置为默认值），
或命令行 `.venv\Scripts\python.exe scripts\dev_reset.py`（只清数据、不动设置）。

## 库文件夹结构

库文件夹**全局唯一**，可在「设置 → 库文件夹」中更改位置（目标文件夹必须为空）、扫描登记、重建目录结构或打开。
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
  演示用户/
```

- **全局**：存全局资源 —— `store/`（内容寻址备份仓库）、`covers/`（封面缓存）、`backups/`（数据库备份）、
  `.datamanager/`（库元数据与布局标记）。
- **<用户名>**：该用户的数据与分类目录，数据文件按「分类目录 / 原文件名」真实存放，因此各用户的分类互不影响；
  用户改名时其文件夹会同步改名。
- 导入时的目标固定为唯一的库文件夹（无需也无法选择库），数据项直接落在当前用户的用户名文件夹下。
- 外部往库内放了文件后，可在「设置 → 库文件夹」执行「扫描并登记」，把库内文件（按同名分类目录归类）登记为数据项；
  库目录有外部改动时界面会提示。

## 项目结构

```text
src/
  main.py                     程序入口
  app/
    core/                     路径、配置、日志、信号总线、口令散列
    db/                       SQLAlchemy 模型、引擎、初始数据
    repositories/             数据访问层（查询与持久化）
    services/                 业务层（导入、条目、分类标签、导出、存档、用户与口令、统计）
    ui/                       界面层
      common.py               通用类型/格式化/提示
      dialogs.py              文本输入与条目编辑对话框
      main_window.py          主窗口与导航
      pages/                  首页、导入、数据管理、标签、用户、存档、设置
      widgets/                关键词输入与标签选择、拖放区、条目卡片、筛选面板、分类树、分页控件、封面加载器、库目录监听
    resource/                 图标与翻译资源
config/                       用户配置（首次运行自动生成，不随代码分发）
resources/
  data.db                     SQLite 数据库
  library/                    唯一的库文件夹（可在「设置 → 库文件夹」中改位置）
  demo/                       示例数据脚本生成的原始文件
logs/                         运行日志
scripts/                      开发自检脚本（含示例数据注入 seed_demo.py）
tests/                        单元测试（语料生成 + 各功能用例，数据隔离在 tests/_tmp/）
pyappify.yml                  打包配置（PyAppify）
icons/                        打包用图标（icon.ico / icon.png）
.github/workflows/build.yml   推送 v* 标签时自动打包并发布
```

## 数据安全

- 文件按 SHA-256 内容寻址存储，同一份内容重复导入不会重复占用磁盘。
- 删除默认为软删除；彻底删除会同时释放仓库中不再被引用的文件。
- 存档是"快照 + 引用"，不做版本控制的二进制 diff，因此对图片、视频等任意类型都有效。
- 存档自动清理在「设置 → 存储 → 存档自动清理」配置（按数量 / 容量 / 时间 / 关闭），创建存档时自动执行；
  存档页的「按策略清理」可立即执行一次，清理会同时释放不再被任何快照或数据项引用的内容。
- 全文检索使用 SQLite FTS5 虚拟表 `items_fts`（trigram 分词），由触发器随数据增删改实时同步；
  检索异常时可在「设置 → 存储 → 重建索引」中重建。短于 3 个字符的词自动退回 `LIKE` 匹配。
- 口令按用户保存，只存 PBKDF2 派生的哈希，无法从配置中还原出原文。
- 数据库带结构版本号（`app_meta.schema_version`）：结构升级时先把旧库另存为
  `resources/data.db.bak-<时间戳>`，再重建空库，避免旧结构静默不兼容。
- 库目录结构带布局版本号（`<库>/全局/.datamanager/layout-2.json`）：首次运行会先备份数据库到
  `<库>/全局/backups/data-before-layout-v2-<时间戳>.db.bak`，再按用户名文件夹重排文件与记录；迁移可重复执行。

更多细节（标签体系、用户与权限、数据管理页交互、日志保留策略）见 [`HELP.md`](HELP.md)。

## 开发与自检

界面与数据层的约定、开发过程中的设计记录见 [`tests/TODO.md`](tests/TODO.md)。

### 自检脚本

```powershell
.venv\Scripts\python.exe scripts\dev_check_services.py   # 数据层：导入/编辑/导出/存档/口令/统计
.venv\Scripts\python.exe scripts\dev_check_ui.py         # 界面构建：离屏创建主窗口并刷新各页
.venv\Scripts\python.exe scripts\dev_check_flow.py       # 交互流程：导入→编辑→标签→删除→存档→解锁
.venv\Scripts\python.exe scripts\dev_reset.py            # 清空数据库与运行期目录，恢复到首次运行状态
.venv\Scripts\python.exe scripts\seed_demo.py            # 注入约 100 条示例数据，方便直接体验各功能
```

`dev_check.py` / `dev_check_services.py` / `dev_check_flow.py` 内部会 `init_db(force=True)` 重建数据库，
运行前由 `scripts/dev_check_guard.py` 把真实数据库备份到 `logs/_selfcheck-data.db.bak`，跑完（含异常退出）
自动还原，所以随时可以重跑而不会弄丢已导入的数据。无图形界面的环境可先设
`$env:QT_QPA_PLATFORM='offscreen'`。

`dev_check_ui.py` 当前包含 16 项界面回归检查（首页统计、筛选栏、导入页分类、标签多选、关键词筛选与展示、
标签页权限、单库布局、全局标签标识、最近导入跳转、清除口令权限、筛选分组折叠与三态全选、工具栏流式布局等）。

### 单元测试

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.venv\Scripts\python.exe -m unittest tests.test_manage -v      # 单个模块
$env:DM_KEEP_TMP=1                                              # 保留 tests/_tmp/ 便于排查
```

- 共 **147 个用例**，每个用例都会在 `tests/_tmp/<用例类名>/` 下重建数据库与库文件夹（库、仓库、封面、导出），
  互不影响，也不会碰真实的 `resources/`、`config/`、`logs/`。
- `tests/dataset.py` 生成一份多样化语料并写入临时目录：真实编码的 PNG / JPEG / WEBP / TIFF / 动画 GIF / ICO、
  伪造魔数的视频与音频、真实 ZIP / TAR / WAV / SQLite / PDF、docx / xlsx / pptx / odt / epub，
  以及各类文本（空文件、超过 512 KiB 的大文本、无扩展名、大小写、空格与特殊字符、隐藏文件），
  另有嵌套子目录、空目录和用于查重的同内容副本。
- 用例覆盖导入（按类型 / 命名库 / 分类 / 递归目录 / 去重策略 / 扫描登记）、检索与筛选、分页排序、统计、
  编辑与标签关键词、删除与回收站、分类树、标签库、存档与孤儿清理、库文件夹与扫描、用户与口令、
  导出与特征提取；另有 `tests/test_layout_migration.py`（旧布局 → 单库 + 用户名文件夹的自动迁移、幂等与备份）、
  `tests/test_tag_scope.py`（全局 / 个人标签的可见性与创建者权限、重名与清理规则）
  与 `tests/test_schema_upgrade.py`（旧版库原地补列升级、重名全局标签去重、升级幂等）。

## 打包与发布

使用 [PyAppify](https://github.com/ok-oldking/pyappify) 打包：启动器本体约 3 MB，首次运行时按标签克隆本仓库、
创建独立 venv 并安装 `requirements.txt`；之后的更新是增量拉取，通常一两秒完成。因此**发布新版本就是打标签**。

- 配置：[`pyappify.yml`](pyappify.yml)（应用名、图标，以及 `profiles`：仓库地址、入口 `src/main.py`、
  `requires_python: 3.13`、依赖文件）。
- 图标：`icons/icon.ico`、`icons/icon.png`（由 `src/app/resource/images/logo.png` 生成）。
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
  提 Issue 时请附上系统版本、Python 版本与 `logs/` 中对应的日志片段。
- 提交代码前请确保：`.venv\Scripts\python.exe -m compileall -q src` 无输出、
  147 个单元测试全部通过、四个自检脚本 `RESULT failures=0`。
- 代码风格：界面文案与注释使用中文；分层保持 `ui → services → repositories → db` 单向依赖。

## 许可证

本项目以 [**GNU General Public License v3.0**](https://www.gnu.org/licenses/gpl-3.0.html)（GPL-3.0）发布，
完整许可文本见仓库根目录的 [`LICENSE`](LICENSE)。

这是由依赖决定的：核心依赖 **PyQt6** 与 **PyQt6-Fluent-Widgets** 均以 GPL-3.0 授权
（GPL 具有传染性），因此任何链接它们的发行版也必须以 GPL-3.0 发布并提供完整源码。
若需闭源分发，需向 Riverbank Computing 与 qfluentwidgets 作者购买商业授权。

| 依赖 | 版本 | 许可证 |
| --- | --- | --- |
| [PyQt6](https://pypi.org/project/PyQt6/) | 6.11.0 | GPL-3.0 / 商业授权 |
| [PyQt6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) | 1.11.3 | GPL-3.0 / 商业授权 |
| [SQLAlchemy](https://www.sqlalchemy.org/) | 2.0.52 | MIT |
| [loguru](https://github.com/Delgan/loguru) | 0.7.3 | MIT |
| [Pillow](https://python-pillow.org/) | 12.3.0 | MIT-CMU |

## 致谢

- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)：Fluent Design 组件库，界面风格来源。
- [ok-oldking/pyappify](https://github.com/ok-oldking/pyappify) 与 `pyappify-action`：打包与自动发布。
- [Qt](https://www.qt.io/) / [SQLAlchemy](https://www.sqlalchemy.org/) / [loguru](https://github.com/Delgan/loguru) /
  [Pillow](https://python-pillow.org/) 及其社区。
