# 开发与维护说明

运行、依赖与自检脚本见 `README.md`。这里只记录与资源、文案相关的操作。

## 资源目录

`src/app/resource/` 下的图片由代码通过文件系统路径读取（见 `src/app/core/paths.py` 的 `RESOURCE_DIR` / `IMAGE_DIR`）：

- `images/logo.png`：窗口与打包用图标（`src/app/ui/main_window.py`）
- `images/header1.png`：首页横幅
- `icons/`：10 个 SVG 图标（`src/app/ui/framework/tokens.py` 的类型图标来源）+ 打包用 `icon.ico` / `icon.png`
- `i18n/`：4 个翻译文件

界面样式全部使用控件自带样式或内联 QSS，**不再使用 `.qrc` / `resource.py` 编译产物**，因此无需执行 `pyside6-rcc`；
重构时清理掉的 `qss/`、`images/controls/`、`resource.qrc`、`resource.py` 已不存在。

## 资源文件夹与库布局

资源文件夹**全局唯一**（配置项 `Storage/Resource-Path`，默认项目根的 `.resources`，旧 `resources/` 启动时自动改名；可在「设置 → 资源文件夹」中更改，
目标文件夹必须为空，搬迁后写回库路径并自动重启），数据库（`data.db`）与唯一的库文件夹 `library/` 都在它下面。
库内部结构由 `src/app/core/paths.py` 与 `src/app/services/library_service.py` 约定：

```
<库>/
  全局/                     全局资源
    store/                  内容寻址仓库（ab/cd/<sha256>）
    covers/                 封面缓存
    backups/                数据库备份
    .datamanager/           库元数据（layout-2.json 为布局标记）
  <用户名>/                 每个用户一个文件夹（名取自用户名，非法字符替换为 _）
    <分类目录>/<原文件名>    该用户的数据文件（各用户分类互不影响；重名自动加序号；未选分类的数据落在「未分类」目录）
    <分类目录>/.hiddens/      该分类下被隐藏的数据文件（物理隔离，可单独 ACL 锁定）
```

- 数据项记录的是**库内相对路径**（`<用户名>/<分类目录>/<文件名>`），因此用户改名时要调用
  `LibraryService.rename_user_dir()` 同步重命名文件夹并改写记录（「用户」页改名已接线）。
- 扫描只处理用户名文件夹下的文件，跳过 `全局/` 与点目录（分类目录下的 `.hiddens/` 例外：里面的文件登记为隐藏项）；识别不出所属用户的文件会被跳过并打日志
  （见 `LibraryService.scan()`）。
- **布局迁移**：启动时 `src/main.py` 在 `seed()` 之后调用 `migrate_layout()`（`src/app/services/layout_migration.py`）。
  若 `全局/.datamanager/layout-2.json` 不存在，就先备份数据库到 `全局/backups/data-before-layout-v2-<时间戳>.db.bak`，
  再把旧布局（资源文件夹下的 `store/`、`covers/` 与顶层分类目录下的文件）搬进各用户名文件夹并改写记录，
  最后写入标记文件；迁移是幂等的，重复启动不会重复搬动。
- **未分类归置**：紧随其后调用 `migrate_uncategorized()` 给每个用户补齐固定的「未分类」根分类（`UNCATEGORIZED_NAME`，新建用户也会自动获得），
  并把 `category_id` 为空的历史数据项写回该分类、文件搬进 `<库>/<用户名>/未分类/`；无归属（`user_id` 为空）的数据保持原样，
  记入统计的 `unassigned`。该步骤同样幂等。

## 隐藏数据与隐私保护

- 隐藏是**物理隔离**：`LibraryService.set_item_hidden()` 把文件在「分类目录」与「分类目录/.hiddens/」之间搬动（重名自动加序号），
  库内相对路径仍是 `<用户名>/<分类目录>/.hiddens/<文件名>`，数据项的 `is_hidden` 与库内位置始终一致；
  导入时 `is_hidden=True` 直接落到 `.hiddens/`（`ImportService.sanitize_subdir()`）；扫描时 `.hiddens/` 之后不再参与分类匹配，里面的文件登记为隐藏项。
  存档记录 `ArchiveEntry.is_hidden`（schema 5 → 6），还原时按存档把隐藏状态对齐回来（内容相同但隐藏状态不同也算 `changed`）。
- 隐私保护（`src/app/core/acl.py` + `src/app/services/privacy_service.py`）：配置项 `Storage/Resource-Protected`（锁资源文件夹）与
  `Storage/Hidden-Protected`（只锁各 `.hiddens/`），在「设置 → 隐私保护」用两个开关切换。
  `acl.lock()` 用 `icacls <路径> /inheritance:r /deny *S-1-1-0:(OI)(CI)(RX)` 拒绝 Everyone 读取并去掉继承，`acl.unlock()` 反向恢复。
  资源文件夹受保护时隐藏项开关置灰并自动收起（`targets()` 用 `elif`：资源根已锁就不再单独列 `.hiddens`），
  设置页 `_normalize_privacy()` 负责把同时为真的两个开关收敛掉。
- **静态保护模型**：ACL 无法区分同一用户下的不同进程，程序自己也会被拒绝，因此不再「平时锁着、读写时瞬时放行」，而是
  **运行期整场放行、退出时才锁定**。开关的语义随之变为：打开只是记下设置（下次启动整场放行、退出后锁定），关闭立刻放行
  （`_on_protection_changed()` → `privacy.unlock()`）。`privacy.guard()` / `@guarded` 现在是纯语义标记（直接 `yield`），不再翻转 ACL；
  数据库连接、备份、删除 `-wal`/`-shm` 都不再需要放行窗口（`database._privacy_guard()` / `_guarded_sqlite_connection()` 已删除）。
- **异常退出自愈**：启动时 `privacy.begin_session()` 先强制放行资源根与各 `.hiddens`（`force_unlock()` 无视设置），
  `paths.make_dir()` 建目录失败只告警不致命，`paths.release_locked_root()` 每个进程只放行一次（`_released` 标志）。
  `_bootstrap_data()`（建目录 + 开库 + 补数据）失败时 `_degrade_protection()` 会关掉两个开关并强制放行再重试一次；
  仍然失败则打印 `python src/main.py --unlock` 的应急提示并返回码 2。`--unlock` 只强制放行、不改设置。
  会话标记 `config/session.json`（`paths.SESSION_FILE`）在启动时写入、退出时删除，残留即说明上次异常退出并记入日志。
  退出路径是 `aboutToQuit` + `atexit` 双保险：`_lock_on_exit()` 先 `dispose_engine()`（让 WAL 收尾写回 `data.db`）再 `privacy.end_session()`，
  幂等（`main._locked`）。非 Windows 平台 `is_supported()` 为假，一律跳过并只打日志。
- `tests/test_hidden.py` / `tests/test_privacy.py` 覆盖隐藏流转、ACL 命令构造、会话标记与启动自愈；自检套件的 `settings_privacy_group`（沿用旧界面门禁 `privacy_group` 的判据）检查设置页分组与「打开不立刻锁定、关闭立刻放行」。

## 翻译文件

界面文案直接写简体中文，`.qm` 文件只用于 FluentWidgets 控件的内建英文文本。修改 `.ts` 后重新编译：

```cmd
pyside6-lrelease src\app\resource\i18n\app.zh_CN.ts -qm src\app\resource\i18n\app.zh_CN.qm
pyside6-lrelease src\app\resource\i18n\app.en.ts -qm src\app\resource\i18n\app.en.qm
```

## 开发者自检

```cmd
.venv\Scripts\python.exe -m compileall -q src scripts
.venv\Scripts\python.exe scripts\selfcheck.py
.venv\Scripts\python.exe scripts\selfcheck.py --list
.venv\Scripts\python.exe scripts\selfcheck.py --layer services
.venv\Scripts\python.exe scripts\selfcheck.py --only manage_selection,user_journey --verbose
.venv\Scripts\python.exe scripts\selfcheck.py --json
.venv\Scripts\python.exe scripts\selfcheck.py --keep-db
.venv\Scripts\python.exe src\main.py --self-check
.venv\Scripts\python.exe scripts\seed_demo.py
```

`scripts/selfcheck.py` 是自检套件：按 `data`（纯函数与库结构）、`services`（服务层）、`pages`
（页面结构与交互）、`flows`（端到端流程）四层组织，每项检查在自己的临时目录与全新数据库上运行，
只调用公开契约，**不碰真实的 `.resources/` 与 `config/`**，因此不需要备份还原，随时可重跑；末行固定输出
`RESULT failures=N`，N>0 时退出码为 1。无图形界面的环境先设 `$env:QT_QPA_PLATFORM='offscreen'`。
`src\main.py --self-check` 在源码仓库里等价于全量自检；打包后没有 `scripts/` 目录，会退化为
「能建好界面就退出」的冒烟测试。

旧套件（`scripts/dev_check.py`、`dev_check_services.py`、`dev_check_flow.py`、`dev_check_ui.py`）在功能对等后已删除：
它内部 `init_db(force=True)` 重建数据库、运行前备份真实库、结束后自动还原；需要对照旧实现时看只读快照
`logs/_rewrite/legacy_snapshot/scripts/` 或 git 历史，旧 34 项检查到新检查名的逐项对应见 `REWRITE.md` §7.1。

单元测试按主题拆分：改哪块代码就只跑对应的模块（`.venv\Scripts\python.exe -m unittest tests.test_manage -v`），
不再全量 `unittest discover`；文件名、基类与模板等范式见 `tests/README.md`，隔离目录与语料由 `tests/harness.py`
的 `IsolatedCase`（`tests/_tmp/<用例类名>/`）和 `tests/dataset.py` 提供。

`scripts/seed_demo.py` 依赖 `tests/dataset.py` 生成示例文件，向真实环境注入约 100 条数据（含隐藏项、回收站项、
第二个用户与一个存档快照）且不会自动删除；清空用应用内「设置 → 维护 → 恢复初始化」或 `scripts/dev_reset.py`。
两者的公共实现是 `src/app/services/maintenance.py`（`reset_config()` 重置全部设置、`reset_runtime_data()` 清数据并重建空库）。

在无图形界面的环境里，可先设置 `QT_QPA_PLATFORM=offscreen`（PowerShell：`$env:QT_QPA_PLATFORM='offscreen'`）。

## 标签体系

标签分两类（`src/app/db/models.py` 的 `Tag`）：`is_global` 为真时对所有用户可见，`user_id` 记录归属用户，
`created_by` 记录创建者。可见性由 `src/app/repositories/tags.py` 的 `_scope()` 决定：全局标签 + 自己创建或归属自己的个人标签（默认用户传 None 表示不限制，因此能看到全部标签）；
`by_name()` 优先返回全局标签，因此新建个人标签不会与已有全局标签重名，转全局时若已有同名全局标签会被拒绝。反过来，新建全局标签（或把个人标签转为全局）时若库里已有同名个人标签，会把这些副本并入全局标签（`TagRepository.merge_shadow_copies()`：引用先转到全局标签再删除副本行），所以不会长期留下「个人标签与全局标签同名」的重复项；历史库里已有的这类副本由启动时的 `database._merge_shadow_tags()` 修复。
默认用户（管理员）可以管理任意标签，其他用户只能管理自己创建的标签：重命名、切换全局 / 个人归属、删除都由
`TagRepository.can_manage(tag, user_id, is_admin)` 判定（管理员或创建者），越权时在状态栏给出提示；
批量操作只处理有权限的标签，其余计入「跳过」（`TaxonomyService.set_tag_global()` / `rename_tag()` / `delete_tag()` / `cleanup_unused()`）。

「标签」页（`src/app/ui/pages/tag_page.py`）用表格展示：`prepare_table(self.table, movable=True)` 让表头可拖动，
`fit_columns(min_width=64, max_width=220)` 按内容自适应列宽；表头的 `TableFilterBar` 提供名称 / 归属 / 创建者 / 数据项数四列筛选，
`_apply_filters()` 用 `match_filters()` + `setRowHidden()` 处理，结果由「显示 X / Y 个标签」标注，被隐藏行的选中会被忽略。
表格首列是勾选框（`TAG_CHECK_COLUMN = 0`，名称 / 归属等列整体右移一列），选择条上的三态框与「全选 / 全不选 / 反选」按钮同步勾选状态
（全选只作用于当前显示的行）；勾选后可以「批量转为全局 / 批量转为个人 / 批量删除」——只有当前用户创建的标签会被处理，
其余会在提示里说明跳过数量，`TaxonomyService` 的权限校验（`TagRepository.can_manage()`：默认用户或创建者）仍然是最终依据。

数据库结构版本在 `src/app/db/database.py` 的 `SCHEMA_VERSION`：低版本库启动时按 `_upgrade_schema()` 原地补列并回填
（历史 `user_id IS NULL` 的标签视为全局），重名的历史全局标签会被重命名为「名称（N）」后再建唯一索引；
只有库版本高于程序版本（降级运行）时才会备份并重建。

## 用户与权限

`users` 表的 `is_default` 标记默认用户（管理员）：结构升级到 `SCHEMA_VERSION` 4 时把 `MIN(id)` 置位，种子数据建的第一个用户即管理员
（`src/app/db/seed.py` 的 `User(name=DEFAULT_USER, is_default=True)`，`UserRepository.default()` / `ensure_default()` 优先取它）。
`UserService.is_admin()` 决定界面权限：默认用户能在「用户」页新建 / 重命名 / 删除任意用户并设置或清除其口令，也才能用存档页的「还原整个存档」，也才能执行设置页与插件页的系统级操作（见下）；
普通用户可以改自己的用户名与口令、清除自己的口令，但**不能删除当前用户**：`UserService.delete()` 的 `allow_current` 默认为 False，删自己会把当前用户静默切到默认用户（等于提权），所以服务层直接拒绝，界面也把当前用户卡片上的删除按钮置灰。口令按用户独立保存（`src/app/core/security.py`），切换用户与解锁隐藏数据都用当前用户口令。
「清除口令」按钮只对已设口令的用户显示（`UserPage._clear_password()`）：默认用户可以清除任何用户的口令，其他用户只能清除自己的，越权时给出提示。

系统级操作只对默认用户开放（`src/app/ui/pages/settings_page.py` / `plugin_page.py`）：设置页的「恢复初始化」与资源文件夹的
「更改位置 / 扫描并登记 / 重建目录结构」、插件页的「导入插件包 / 导入插件目录 / 启用 / 插件选项（启用时） / 重命名 /
编辑说明 / 编辑备注 / 删除 / 批量启用 / 批量禁用 / 批量删除」在普通用户下被禁用，并由页面上的「仅默认用户可用」
SettingCard（设置页）或说明文字（插件页）标注原因；处理器入口还有 `_require_admin()` 兜底，误调时提示「无权操作」。
切换用户时 `signalBus.userChanged` 触发 `_sync_admin()` → `_apply_permissions()`，立即刷新按钮可用状态与提示显隐。
「打开文件夹」（资源文件夹）与隐私保护分组（资源文件夹 / 隐藏文件两个开关）对所有用户可用，不受此限制。
删除用户（`UserService.delete()`）时：默认用户与当前用户不可删除（`allow_current=True` 只供内部与自检使用）；其余用户的数据项、分类、标签与存档条目一并转到默认用户——分类以「用户名」为一级分类镜像到默认用户下（**只镜像真正有数据项的分类及其祖先分类**，子树里没有数据文件的空分类不保留、镜像后对应的空目录也会删掉）、文件整目录搬进 `<库>/<默认用户>/<用户名>/`，个人标签改归属并在同名时合并、**没有任何数据项引用的标签直接删除而不迁移**，该用户创建的**全局标签**也改写创建者（`created_by`，否则外键的 `ON DELETE SET NULL` 会抹掉它），存档条目保留原来的 `user_id` / `user_name` / `category` 文本；数据为空的用户直接清理其分类与用户文件夹。没有引入「已删除用户」影子账号。

「用户」页的头部是统一的标题行（`page_header()`，右侧是「新建用户」主按钮），卡片网格整体装在一张分区卡片（`section_card(self, "全部用户", "…", spacing=10)`）里：
「用户」页（`src/app/ui/pages/user_page.py`）把用户排成卡片网格：卡片是**固定宽度** `CARD_WIDTH`（320 px，`setFixedWidth()` +
水平 `QSizePolicy.Fixed`），`grid_columns(available_width, card_width=CARD_WIDTH)` 按窗口宽度决定列数，`_layout_cards()` 在每个
卡片列后追加占位伸缩列（`setColumnStretch(columns, 1)`），所以窗口变宽只增加列数、卡片本身不会被拉宽；
滚动区内部控件跟随视口宽度重排（`setWidgetResizable(True)` + `viewport()` 事件过滤器），窗口变窄时网格仍保证卡片完整可见。
`refresh()` 重建 `UserCard` 并高亮当前用户（`set_highlighted()` / `current_card()`），同时用 `_equalize_card_heights()` 统一各卡片高度；
`_layout_cards()` 把每行的行伸缩设为 0、只让末行下方的占位行伸缩（`setRowStretch(rows, 1)`），多余高度全部落在网格底部，卡片始终从左上角开始逐个排列。
每张卡片都是自洽的对象单元（`UserPage._user_card()`）：三段式布局 = ①首字头像（`_avatar()`，当前用户蓝底 / 其他灰底）+ 用户名 + 徽标（`card_badges()`：当前用户 / 默认用户 / 已设口令）；
②摘要（`card_summary()`：数据项数、分类数、创建时间）；③两列按钮网格（`CARD_BUTTON_COLUMNS = 2`，按 `card_permissions()` 显隐）——
切换为当前用户（当前用户卡片上是禁用态的「当前用户」）、重命名、口令、清除口令、删除，因此所有针对该用户的操作都在卡片内完成，按钮不会被裁出卡片。
`UserService.delete()` 保留默认用户（`if user.is_default: return False`）并拒绝删除当前用户（`allow_current` 默认 False）；其余用户删除时先看有没有数据（是否有未软删除的数据项）：
**有数据**时先算要保留的分类：`_retained_categories()` 只收「有数据项的分类 + 它们的全部祖先分类」，`_mirror_categories(user, target, retained)` 再以「用户名」为一级分类把它们镜像到默认用户下（返回旧→新 id 映射与镜像根；保留集合为空时不建一级分类，`DataItem.category_id` 回落到目标用户的「未分类」，
镜像时用 `CategoryRepository.unique_sibling_name()` 避开同级重名），`<库>/<用户名>` 整目录搬到 `<库>/<目标用户>/<用户名>`
（`LibraryService.relocate_user_dir()`）并给 `DataItem.file_path` 加默认用户名前缀，随后 `_prune_empty_dirs()` 自底向上删掉镜像目录里空掉的目录（子树里没有数据文件的分类连磁盘目录一起消失）；
标签归属与 `created_by` 一并改为默认用户（同名标签走 `TagRepository.merge()`，**没有任何数据项引用的标签直接删除**，不迁移）；
存档条目 `user_id` / `user_name` 改为目标用户且 `category` 前缀上「用户名 / 」；**数据为空**时不建任何镜像分类，直接
`LibraryService.remove_user_dir()` 删掉其用户文件夹（目录里还有库不认识的残留文件时保留目录并打日志），再删除其分类行与用户行。

## 数据概览页

首页（`src/app/ui/pages/home_page.py`）是仪表盘：7 张 KPI 卡（数据总量 / 占用空间 / 今日导入 / 用户数 / 分类 / 标签数 / 存档数）由模块级
`format_summary(overview(), users=, archives=)` 生成。整页由四张分区卡片组织（`framework` 的 `section_card()`：概览 / 快捷操作 / 最近导入 / 类型分布），
每张卡片都带标题与一句话说明：概览卡装 KPI 卡、快捷操作卡装「导入数据 / 数据管理 / 打开资源文件夹 / 新建存档」四个按钮、
最近导入卡装最近条目（点击发 `focusItem`）、类型分布卡装 `type_distribution()` 生成的 `ProgressBar` 条。
标题行右侧是「当前用户」下拉：切换即 `UserService.set_current()` + `signalBus.userChanged`，设了口令的用户会先弹口令框，口令错误则回退到原用户。
KPI 卡与快捷按钮这两块流式区域用 `components/flow_area.py` 的 `FlowArea`（`adaptive=True`，KPI 卡最小宽 180 px、按钮 120 px）：
它按当前宽度自算高度（`heightForWidth`）、增删控件后立刻重排，所以切换用户 / 刷新后新卡片不会再停在默认位置盖住第一张卡；
页面不可见时经历 resize（例如最大化）也不会被压成 0 高，重新显示时会再量一次高度，KPI 卡不会集体消失。
页面订阅 `itemsChanged` / `categoriesChanged` / `tagsChanged` / `userChanged` / `archivesChanged` 自动刷新。

## 数据管理页

上方工具栏用 qfluentwidgets 的 `FlowLayout`（`src/app/ui/pages/manage_page.py` 的 `_build_toolbar()`）：按钮按自身宽度流式排列，
宽度不足时自动换行；容器是 `_ToolbarView`（`QScrollArea`），高度由 `_fit_toolbar()` 按 `flow.heightForWidth(viewport 宽度)` 计算并在
`resizeEvent` 中重算，最多占 `TOOLBAR_MAX_ROWS`（2）行，再多则出现纵向滚动条。

右侧筛选面板（`src/app/ui/components/filter_panel.py`）的每个分组都是 `FilterSection`：标题栏是「箭头 + 加粗标题 + 搜索框 + 三态全选框」，
箭头或标题行控制折叠（`_toggle_body()`），选项区固定 `SECTION_BODY_HEIGHT`（116 px）高度、超出时自己滚动；
搜索框按显示名过滤选项（无匹配时显示「没有匹配的选项」），三态全选框由 `_sync_all()` / `_on_all_state()` 与分组内的勾选状态双向同步
（空 = 全不选、横杠 = 部分选中、勾 = 全选，点击空框即全选、点击勾框即全不选），`_syncing` 守卫避免信号回环。
`FilterPanel` 只有类型 / 标签 / 关键词三个分组——原来的「分类」分组已移除，分类过滤改由左侧分类树的复选框承担；
`_type_boxes` / `_tag_boxes` / `_keyword_boxes` 别名指向各分组的同一份 `boxes` 字典。

列表 / 卡片项（`src/app/ui/components/item_card.py` 的 `ItemListRow` / `ItemCard`）左侧是复选框：左键单击只选中这一项（不再直接打开），
双击左键才打开（`opened` → `ManagePage._on_open()`）；复选框用于多选，Ctrl + 左键逐个切换、Shift + 左键从锚点选到点击项（Windows 规则，
区间由纯函数 `ManagePage.range_ids(order, anchor, target)` 计算，`_anchor` 记录最近一次点击项）。`ItemCard` 是 qfluentwidgets 的
`CardWidget`，它的 `mouseReleaseEvent` 无条件发出 `clicked`，所以页面用 `_press_button` 只认左键，右键不会破坏多选。
工具栏下方的选择条（`ManagePage._build_selection_bar()`）有三态全选框「全选本页」（`tri_state(checked, total)`：空 = 全不选、横 = 部分选中、
勾 = 全选，`_syncing` 守卫防回环，与每行的复选框双向同步）、已选数量、「移动到分类…」与「清空选择」，没有选中项时批量按钮禁用。

左栏分类树（`src/app/ui/components/category_tree.py`）的每个分类节点都带复选框，**「全部数据」根节点也是三态复选框**（勾上即全选整棵树）：勾选集合由 `checked_categories()` 读出（只收真正勾选的分类，根节点不计入）、`set_nodes(..., checked=...)` 写回，`itemChanged` → `checkedChanged` → `ManagePage._on_category_checked()` 后回到第 1 页重新查数据。`ManagePage._load_items()` 以勾选集合为准（勾选集合非空时忽略单选），全部取消勾选时回落到最后点过的分类（`_category_id`）或「全部数据」；单击分类行仍是单选并清空勾选集合，`refresh()` 重建树期间由 `_syncing_tree` 守卫，不会误清勾选。
三态级联与汇总：勾选一个分类会把它下面的所有子分类一起勾上（`_apply_state()` 递归向下），子分类的状态再向上汇总（`_aggregate_state()`：子分类全勾 = 勾、全不勾 = 空、否则半选；`_aggregate_all()` 自底向上逐层汇总），所以「全部数据」根节点天然反映整棵树的状态；`_on_item_changed()` 把半选按勾选处理，`_updating` 守卫防止级联过程里信号回环，`set_checked_categories()` 期间不触发 `checkedChanged`。
勾选后可点左栏的「批量移动」/「批量删除」：两个按钮只在勾选了**非根分类**（`_eligible_category_ids()` 排除根分类与固定的「未分类」）时启用；批量移动的目标是树里当前选中的分类（选中「全部数据」= 移到顶层），目标是待移动分类自身或其子孙时拒绝，逐个走 `TaxonomyService.move_category()`；批量删除先确认，子分类上移会与同级分类重名的（`promotion_conflicts()` 非空）跳过并在提示里说明数量，其余走 `delete_category()`（其中的数据变成未分类）；勾上「全部数据」时整棵树都处于勾选状态（此时按钮一并禁用：顶层没有可移动的去处、顶层分类也不能整体删除，提示会改成「已全选「全部数据」…」，处理器同样会拒绝这次操作）；批量移动时若所选分类本来就都在目标分类下，会提示「无需移动」而不再走一次无意义的提交。
中间标题行右侧的「分类栏」/「筛选栏」两个可切换按钮（`tree_toggle_button` / `filter_toggle_button`）分别显示 / 隐藏左右两栏。
右键菜单由纯函数 `menu_items(count)` / `open_with_items(suffix)` 生成、`ManagePage._build_menu()` 渲染：**直接打开**、**打开方式**（系统默认程序 /
点名某个内置查看器 / 交给系统选择…）、在文件夹中显示、复制路径、移动到分类…、编辑信息、添加标签、隐藏 / 取消隐藏、导出选中项、移入回收站、
从回收站还原、彻底删除、详情；多选时「编辑信息」「详情」禁用（`MENU_SINGLE_ONLY`），其余批量操作作用于全部选中项（都走 `_require_selection()`）。
`ManagePage.move_selected(category_id)` 把选中项批量移到目标分类（`None` 表示「未分类」），走 `ItemService.set_category()`，文件跟随到
`<库>/<用户名>/<分类链>/`；工具栏的「移动到分类」按钮与右键菜单共用它。

## 数据存档

存档是「快照 + 引用」：`Archive` 记录快照本身，`ArchiveEntry` 记录每个数据项当时的校验和、分类与路径，
`Blob` 按 SHA-256 内容寻址存放，相同内容只存一份（`src/app/services/archive_service.py`）。
`ArchiveService.entry_state()` 把条目与当前数据对比为 same / changed / removed / missing，还原只处理与当前不一致的条目。

自动清理可按数量 / 容量 / 时间 / 关闭（设置 → 存储 → 存档自动清理，对应 `prune()` / `prune_by_size()` / `prune_by_age()` /
`auto_prune()`），从最早的快照开始删并始终保留最新一份；**已标记的存档（`Archive.pinned`）会被全部清理策略跳过**，
只有先取消标记、或在存档页手动删除才会消失。`ArchiveService.set_pinned()` 切换标记，`SCHEMA_VERSION` 升到 5 时给旧库原地补上该列。
存档页的「标记存档 / 取消标记」按钮跟随选中存档（未选中时禁用），列表项与详情都会标出【已标记】。
存档页（`src/app/ui/pages/archive_page.py`）用 `SegmentedWidget` + `QStackedWidget` 分成两个页签（`TAB_ARCHIVES` = `archives` /
`TAB_ENTRIES` = `entries`，`TAB_INDEX` / `tab_index(route_key)` 做键位映射）：**存档列表**页签是上方那排存档表格
（逐列筛选 + `Pager` 分页，`ARCHIVE_PAGE_SIZE` = 50），**存档内条目**页签是明细表，含「所属用户」列，供管理员核对跨用户快照。
用户点选一条存档会自动切到「存档内条目」页签，也可以随时手动切回去；程序化刷新（恢复选中行、重载列表）时用 `blockSignals`
屏蔽信号，不会强行抢走页签。`tab_keys()` / `current_tab()` / `switch_tab(route_key)` 是给外部（如跳转逻辑）用的接口。

存档列表首列是勾选框，选择条上是一个三态全选框（空 = 全不选、横杠 = 部分选中、勾 = 全选本页，点一下在全选 / 全不选之间切换；只作用于当前页），
可以「批量标记 / 批量取消标记 / 批量删除」。**已标记的存档不能直接删除**：单条删除会提示先取消标记，
批量删除会跳过它们并在确认框与结果提示里说明跳过数量；服务层 `ArchiveService.delete()` 对已标记的存档返回 `False`
（自动清理策略同样跳过已标记的存档）。

## 导入页与批量导入

导入页（`src/app/ui/pages/import_page.py`）有三部分：数据来源（文本 / 批量导入）、导入目标与数据信息、待导入文件信息与导入进度。
「导入用户」下拉用 `UserService.list_users()` 列出全部用户；只有默认用户（`UserService.is_admin()`）能替其他用户导入，其他用户的下拉被禁用且只列出自己，
文案带「（当前用户）」，提示语为「只有默认用户可以替其他用户导入数据，其他用户只能导入到自己的文件夹」；`target_user_id()` 决定分类 / 标签候选与最终归属
（非管理员直接返回 `UserService.current_id()`）；
切换用户会重建分类与标签候选（`_reload_categories()` / `_reload_tags()`）。
「未分类」是每个用户的固定根分类（新建用户自动获得）：分类下拉直接列出 `TaxonomyService.tree(user_id=...)` 的真实分类（没有占位项），
默认选中「未分类」，所以没单独选分类的文本 / 文件都会落在 `<用户名>/未分类/` 目录（服务层 `ImportService._category_for()` 兜底）。
模式卡片上会实时显示当前选中的文件 / 文件夹摘要（文本模式隐藏该行，文件夹只显示省略后的路径），
导入完成后在结果摘要里追加本次耗时；来源按钮同样是流式布局，窄窗口自动换行。
分类的改名与删除规则：`TaxonomyService.rename_category()` 拒绝同级重名（界面提示「无法重命名」）；`delete_category()` 删除分类时把子分类上移到
被删分类的父级，若与同级已有分类重名，界面先弹 `CategoryConflictDialog` 让用户选「自动编号」（`unique_sibling_name()` 依次尝试 `名字-1`、`名字-2`）
或逐个填写新名字，保证同一级下不会出现两个同名分类。

「未分类」是固定的系统分类（`TaxonomyService.is_uncategorized()`：名称为 `UNCATEGORIZED_NAME` 且为根分类）：分类树里它固定排在所有根分类之后、
标签追加「（固定）」并带说明提示，右键菜单为空（`src/app/ui/components/category_tree.py` 的 `menu_entries(fixed=True)` 不返回任何操作）。
服务层同样兜底：`rename_category()` / `delete_category()` 对它直接返回 `False` / `0`，`create_category()`（父级为它时）与 `move_category()` 也会拒绝并打日志，
所以它既不会被改名、删除，也不会长出子分类。把文件夹导入到它下面时，`ImportService.ensure_category()` 会把新分类改为建在根级，不违反这条规则。
数据管理页的「编辑数据项」对话框同样列出当前用户的真实分类树（没有额外占位项）：没有分类的数据默认选中「未分类」，保存后
`ItemService.move_item()` 会把文件移进 `<用户名>/未分类/`。
批量导入可选多个文件或一个文件夹：文件夹会被展开为「相对子目录 + 文件」清单（跳过 `.DS_Store`、`__pycache__`、`node_modules` 等），
待导入表格逐行显示文件名 / 类型 / 大小 / 修改时间 / 子目录 / 状态（文件数 ≤ 200 时用 `ItemRepository.by_checksum(sha256_of(path))` 标注「库内已有同类内容」）。

服务层（`src/app/services/import_service.py`）：

- `import_files(sources, *, on_event=None, **options)`：多文件导入所选分类；
- `import_folder(directory, *, name="", user_id=None, parent_category_id=None, on_event=None, **options)`：把整个文件夹当作一个新分类（`ensure_category(name or 目录名)`）整体导入，`ImportResult.category_name` 回填分类名；
- `import_tree()` 保留文件夹内的相对子目录（写进条目 `subdir`，再由 `LibraryService.unique_rel_path(..., subdir=...)` 决定落盘子目录）；
- `on_event` 回调收到 `ImportEvent(index, total, source, status, detail)`（status 为 added / skipped / failed），界面据此实时刷新进度与结果表。

进度与结果由 `_ImportWorker(QThread)` 在后台执行（工作线程里 `database.new_session()`，完成后 emit `itemsChanged` / `librariesChanged` / `categoriesChanged`），
主线程只做界面更新；`ImportPage.refresh()` 与其它页面一致，用来在进入页面时重建用户 / 分类 / 标签候选项。

## 表格与列表通用件

`src/app/ui/components/data_table.py` 提供列表页共用的小工具（标签页 / 存档页 / 导入页都在用）：

- `TableFilterBar`（`configure([(键, 显示名, "text"|"choice"), ...])` + `set_options()` / `set_filter()` / `filters()` / `reset()`，`changed` 信号）：贴在表格上方的 Excel 式逐列筛选栏，文本列子串匹配、选项列精确匹配；`reset()` 只在确有变化时发信号；
- `prepare_table(table, *, movable=True)`：隐藏行号、整行多选、只读、表头可拖动（`setSectionsMovable`）、列宽 Interactive；
- `fit_columns(table, *, min_width=72, max_width=260, weights=None)`：先按内容量宽再夹紧，权重列吃剩余宽度；
- `match_filters(values, filters)`：判断一行是否命中全部筛选条件（忽略大小写的子串匹配，空条件跳过）。

动态重建列表时，摘掉旧控件必须走 `src/app/ui/framework/feedback.py` 的 `release_widget(widget)`（先 `hide()` 再 `setParent(None)` + `deleteLater()`）：
PyQt6 + Windows 下只调 `setParent(None)` 并不会隐藏控件，每个被摘掉的条目都会变成一闪而过的小顶层窗口（`ManagePage._clear_layout()`、
`HomePage._clear()`（`FlowArea.take_widgets()`）、`FilterSection.set_items()`、`UserPage.refresh()` 的旧卡片、`SettingsPage._refresh_libraries()` 的旧行、
`TableFilterBar.configure()` 的旧筛选控件、`ItemCard.set_tags()` 的旧标签块均已改用）。只 `deleteLater()` 的旧控件会作为子控件继续留在界面上
（且 Python 侧的类身份会丢失），所以「先隐藏、再断父级」这一步不能省。

## 界面样式统一

页面骨架尺寸统一取自「插件管理 / 打开方式管理 / 存档管理 / 标签管理」这一套风格，常量与构件都在 `src/app/ui/framework/`（间距与构件）与 `src/app/ui/components/`：

- `PAGE_MARGINS = (24, 20, 24, 20)`、`PAGE_SPACING = 12`：所有页面的外层边距与间距（`ScrollPage` / `Page` 基类建好正文布局，内容用 `add_header()` / `add_section()` / `add_widget()` / `add_row()` 加进去）；
- `PANEL_MARGINS = (12, 12, 12, 12)`：列表面板卡片；`DETAIL_MARGINS = (16, 14, 16, 14)`：详情 / 表单卡片；`COMPACT_MARGINS = (10, 8, 10, 8)`：紧凑正文（查看器正文、筛选面板内层）；`KPI_MARGINS = (14, 8, 14, 8)`：KPI / 统计卡片内边距；`SCROLL_GUTTER = 6`：滚动区右侧留白（避免内容贴住滚动条）；
  `panel_card(parent, margins=..., spacing=...)` 返回 `(CardWidget, 卡内竖直布局)`；
  `section_card(parent, 标题, 说明, ...)` 在它上面再叠一层标题（`StrongBodyLabel`）与说明（`CaptionLabel`），概览 / 导入 / 用户 / 设置四页的面板都改用它；
- `PageBase.add_header(标题, 说明)`（工厂函数 `page_header(parent, 标题, 说明)`）：统一的标题行（`TitleLabel` + 弹簧）与下方说明文字，主操作按钮用 `header.add_action(控件)` 挂到右侧；
- `accent_color()` / `accent_name()`：主题强调色（包 `qfluentwidgets.themeColor()`，默认 `#009faa`），
  禁止在 QSS 里写死强调色——自定义控件（用户卡片头像与徽标、选中指示条、拖放框、关键词块）都改成按主题取色；
  用户卡片的头像 `avatar_style(accent)`、徽标 `badge_style(accent)`、卡片高亮 `highlight_fill()` / `highlight_hover()` 也已下沉到 `src/app/ui/framework/theme.py`，页面里不再有样式字符串；
- 空态统一用 `empty_state(parent, 文案, icon=...)`（图标 + 居中说明），不要再用裸 `CaptionLabel` 当占位。

按钮一律用 qfluentwidgets 的 `PrimaryPushButton`（主操作）与 `PushButton`（次操作），不要用原生 `QPushButton`：
设置页的 `PushSettingCard` 自带原生按钮，已用 `SettingsPage` 里的 `ActionCard`（继承它并换成 `PushButton`）替换。
`scripts/selfcheck.py` 的 `style_uniformity` 检查（沿用旧界面门禁同名判据）会逐页断言边距 / 间距、面板卡片边距、没有原生 `QPushButton`、QSS 里没有写死的强调色。
数据管理页的左（分类）/ 中（列表与卡片）/ 右（筛选）三个面板现在都是 `CardWidget` + `PANEL_MARGINS`，标题用 `StrongBodyLabel`，
面板内的滚动区用 `clear_scroll_background()` 透明化。
概览 / 导入 / 用户 / 设置四页也统一成同一套分区卡片（`section_card()`，标题 + 一句话说明），不再用裸 `SubtitleLabel` 或光板 `CardWidget`。
页面底色统一由 `install_app_theme()` 装到 `QApplication` 的调色板提供，**页面自己不再铺底色**（旧 `common.page_background()` 已随 `common.py` 一起删除）：
概览 / 导入 / 设置 / 用户四页此前各自调 `page_background(...)` 写死一对浅 / 深 QSS，又用 `clear_scroll_background(self, inner=False)` 只清了滚动区、没清视口，
所以切到浅色后这些页仍按旧调色板实绘深色块、看起来像混进了原生 Qt 控件；现在它们与其余页面一致：
只留 `setObjectName(...)` 供样式定位、滚动区一律 `clear_scroll_background(self)`。

### 自适应高度的流式容器（FlowArea）

`src/app/ui/components/flow_area.py` 的 `FlowArea(QWidget)` 把「按宽度自算高度」的流式容器抽成一个控件（范式最早来自 `keyword_input.py` 的 `ChipArea`、`pager.py` 与数据管理页的工具栏）：
构造时建 `FlowLayout`（`adaptive=True` 时改用 `AdaptiveFlowLayout` + `setWidgetMinimumWidth(minimum_width)`），`setSizePolicy(Preferred, Fixed)` 并 `setFixedHeight(0)`；
`add_widget()` 后立刻 `sync_height()`，并用 `QTimer.singleShot(0, …)` 再同步一次；`sync_height()` 前有两道守卫——不在显示状态或宽度 ≤ 0 时直接返回，
否则 `height = flow.heightForWidth(width)`（为 0 时回落 `flow.sizeHint().height()`）→ `setFixedHeight()` + `flow.setGeometry(QRect(0, 0, width, max(height, 1)))` + `updateGeometry()`；
`showEvent` / `resizeEvent` / `LayoutRequest` 都会触发同步，`_syncing` 标志防重入。

这两道守卫对应的正是两个真实缺陷：① `FlowLayout.addItem()` 不会 `invalidate()`，父布局尺寸没变也不会重跑，所以**新建的控件会停在默认几何 `0,0:100x96` 盖住第一个**（概览页切换用户后两张卡片重叠）；
② `AdaptiveFlowLayout(isTight=True)` 在 `_doLayout()` 里会跳过不可见控件，容器不可见时 `heightForWidth()` 返回 0（只剩上下边距），外层布局就把卡片区高度压成 0 且之后不再恢复（概览页最大化后 KPI 卡全部不可见）。
`FlowArea` 增删控件后主动重排、并拒绝在不可见 / 零宽时改高度，两个问题都不会再出现。`HomePage` 的 KPI 卡与快捷按钮、`ImportPage` 的文件按钮行都改用它；取下旧控件用 `take_widgets()` 交给 `release_widget()` 释放。

### 主题底色（浅色 / 深色跟随）

qfluentwidgets 的 `setTheme()` 只换 QSS，**不会**调用 `app.setPalette`：凡是按 Qt 调色板实绘底色的控件（`QScrollArea` 视口、`setWidget()` 之后被重新打开
`autoFillBackground` 的宿主、表头等），运行时切到浅色后仍然是深色 —— 表现就是「除少数页面外，其余页面底部背景发黑」。约定：

- `install_app_theme()`：`src/main.py` 在 `_apply_theme()` 之后调用，把 `theme_palette()` 装到 `QApplication` 并挂在 `qconfig.themeChangedFinished` 上，
  切主题时同步换调色板（启动时就装好，之后新建的控件才会拿到正确调色板）；
- `clear_background(widget)`：卡片内部的容器保持透明；`clear_scroll_background(area, inner=True)`：滚动区域连视口一起透明化
  （`qt_scrollarea_viewport` 默认 `autoFillBackground=True`，不清就会漏出一整块深色）；
- `setCustomStyleSheet(widget, light, dark)` 只设属性，没注册过的控件等于没做；必须走
  `setStyleSheet(widget, CustomStyleSheet(widget).setCustomStyleSheet(light, dark))`（`clear_background` 内部就是这么写的）。
  页面底色不走 QSS：由 `install_app_theme()` 装的调色板（`theme_palette()`，浅色 `window` = `#f0f4f9`、深色 = `#202020`）提供，页面与滚动视口保持透明。
`scripts/selfcheck.py` 的 `theme_background` 会切到浅色逐页断言「没有任何可见控件仍按旧调色板实绘深色」，`settings_privacy_group` 会断言设置页的
「资源文件夹 / 隐藏文件」两个开关、资源加密时隐藏开关置灰并自动收起，以及分组里不再出现多余的「立即锁定 / 立即放行」按钮。

## 默认标签

`src/app/db/seed.py` 的 `DEFAULT_TAGS`（重要 / 待整理 / 收藏）在 `seed_user_defaults()` 里只写入一次，且写成**全局标签**
（`user_id=None`、`is_global=True`、`created_by` 记第一个用户）：新用户开箱即用，也不会每个用户各存一份。写之前先看有没有同名全局标签；
没有同名全局标签、但库里已有同名**个人**标签时，会先建好全局标签再把这些个人标签并入它（`TagRepository.merge_shadow_copies()`），
避免凭空多出一个同名全局副本；历史库里已经存在的这种副本由启动时的 `database._merge_shadow_tags()` 修复（`item_tags` 的引用改到全局标签后删除个人标签行）。

## 打开方式与查看器插件

打开文件走 `src/app/services/open_with_service.py`：`OpenWithService.resolve(path)` 按顺序决定用哪种方式 —— 自定义规则且填了程序 →
`custom`；自定义规则没填程序 → `ask`（交给系统选择）；规则为内置且指定了 `viewer_id` 且该查看器还在 → 用指定查看器（查看器已不存在时
回退到该扩展名的默认查看器，reason 记为「指定插件不可用」）；规则为内置但没指定 → 该扩展名的默认查看器；没有可用查看器 → 回退 `inherit`；
没有规则时，有内置查看器就用内置，否则用系统默认。规则按扩展名（不含点）存在 `config/open_with.json`，形如 `{mode, program, args, viewer_id}`，
`mode` 取 `builtin` / `inherit` / `custom`，参数里的 `{path}` 会替换成实际路径（没有占位符时自动补在末尾，见 `src/app/core/shell.py` 的 `build_command()`）。

「打开方式」页（`src/app/ui/pages/open_with_page.py`）以「库里出现过的所有文件格式」为列表（`viewer_registry.extensions()` ∪ 已配置规则 ∪
`ItemService.extensions_in_use()`，可按扩展名 / 插件名搜索），选中后在右侧配置：打开方式下拉（「使用插件打开」/「继承系统默认」/「自定义程序」/
「每次询问」，可用项由 `OpenWithService.available_modes()` 决定）、具体插件下拉（仅「使用插件打开」且该格式有多个查看器时可选，「自动」表示按注册顺序）、
自定义程序与参数，另有「保存」「恢复默认」「测试打开」。当前状态直接写在列表项上（如 `.png — 使用插件（图片查看器）· 库中 12 项`）。

主程序在 `src/main.py` 里用 `plugin_service.bootstrap("app.open_with", open_with_api)` 把 `OpenWithApi` 登记为扩展接口，插件可以据此查询 /
修改打开方式（`set_viewer` / `use_viewer_for_all` / `reset_viewer` 等）；执行外部程序、交给系统选择、在资源管理器中定位分别是
`shell.open_with_program()` / `ask_open_with()` / `reveal()`，失败只记日志、不抛异常。

### 插件协议（v4）

插件都放在 `plugins/<id>/` 下，内置插件与外部插件走同一条载入路径。每个插件目录有 `plugin.json`（必需）、入口脚本与 `data/`；
插件类继承 `src/app/sdk/` 的 `Plugin`，在 `setup(ctx)` 里用 `PluginContext` 注册东西、在 `teardown()` 里收尾。清单示例：

```json
{
  "id": "sample.viewer",
  "name": "示例查看器",
  "version": "1.0.0",
  "description": "说明文字",
  "author": "作者",
  "manager_version": ">=0.1.0",
  "api_version": ">=1.0",
  "entry": "sample_plugin.py",
  "class": "SampleViewerPlugin",
  "depends": [{"id": "builtin.lib.viewer"}, {"id": "builtin.lib.dialog"}],
  "data": {"viewer": "data/viewer.json"},
  "builtin": false
}
```

- 必需字段只有 `id`（匹配 `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`）与 `name`；`entry` 对**外部插件**必填且文件必须存在，
  内置插件可以省略（因此纯库插件、纯数据插件不必写入口脚本）；`class` 用来点名入口脚本里的插件类。
- **协议不再区分插件类型**：清单里没有 `kind` / `kinds`（写了会报「插件清单已取消类型字段」）；`extensions` / `capabilities`
  这类数据字段也不再放清单（写了会提示放进 `data/`），扩展名、显示名、能力说明这些数据由提供相应能力的库插件读取。
- `libraries` 声明插件对外提供的库模块，例如 `builtin.lib.viewer` 提供 `viewer` → `plugin.py`；别的插件 `depends` 它之后
  可以用 `from dm_plugin.<插件 id>.plugin import ...` 静态导入（`app.sdk.library("<id>", "plugin")` 是兜底写法），
  `libraries` 里声明的模块必须存在且非空，并且**约定固定写成入口 `plugin.py`**：一个插件对其他插件的公开面只有它。
- `depends` 支持简写（`"builtin.lib.dialog"`）与对象（`{"id": ..., "version": ">=1.0", "optional": true}`）两种写法；
  `optional` 的依赖缺失只跳过、不算失败；`incompatible` 声明互斥插件，`load_after` 只调整载入顺序、不建立依赖关系。
  缺依赖、版本不满足、互斥、重复声明、循环依赖都会让插件载入失败（个别失败不影响其它插件与程序启动）。
- `data` 把数据文件的**路径**写进清单，插件用 `ctx.data("viewer")` / `ctx.data_path("viewer")` 读回来：清单里只放参数，不放数据。
- `manager_version` / `api_version` 是版本范围（`1.2`、`>=1.0 <2`、`1.2.*`、`^1.2`、`~1.2.3`、`||` 或、空格 / 逗号表示「且」，
  见 [插件协议](plugins/PLUGIN_PROTOCOL.md)），要求高于当前程序 / SDK 版本会让插件载入失败；`version` 是插件自己的版本。
- `options` 声明用户可配置项（`bool` / `text` / `choice`，`choice` 必须有 `choices` 且默认值在其中的），插件用 `ctx.option("键")`
  读回，详见下面的「插件选项与插件页」。

载入时程序按 `(内置优先, id 字典序)` 排序，依次走五个阶段：`manifest`（读清单）→ `dependency`（依赖、互斥、版本）→
`import`（建 `dm_plugin.<id>` 命名空间并导入入口脚本）→ `construct`（实例化插件类并 `attach()`）→ `setup`（调用 `setup(ctx)`）。
任何阶段失败都只把错误记在那个插件上（「插件」页标「异常」并强制禁用），不影响其它插件与程序启动。
插件脚本**只能** import 标准库、Qt、`app.sdk`，以及自己 `depends` 过的插件（而且只能取对方的 `plugin.py`：
`from dm_plugin.<id>.plugin import ...`）；`app` 下除 `app.sdk` 之外的**任何**模块（`app.core` / `app.services` / `app.ui.*` …）
都会被自检 `plugin_imports` 直接拦下 —— 需要什么能力，通过 SDK、扩展点、事件或插件库来拿。

`dm_plugin.<id>` 是载入期在内存里合成的包，磁盘上不存在，编辑器会把 `from dm_plugin...` 标成找不到模块：
仓库里由 `scripts/plugin_stubs.py` 按清单生成 `stubs/dm_plugin/**` 的 `.pyi` 桩，把 `stubs` 标成源码根即可
（仓库自带 `.idea/D-MyDataManager.iml` 已配好），改了清单或库的 `__all__` 后重跑脚本，自检 `plugin_stubs_current` 保证同步。

### 查看器

内置的查看器是一套「工具库 + 七个具体插件」：`builtin.lib.viewer`（`plugin.py` 提供 `ViewerPlugin` 基类、`ViewerWindow`
窗口外壳与 `MediaViewer` 媒体播放页）、`builtin.lib.dialog`（`provides: dialog`，提供弹窗外壳），以及
`builtin.image` / `builtin.text` / `builtin.markdown` / `builtin.spreadsheet` / `builtin.archive` / `builtin.audio` / `builtin.video`
（图片 / 文本 / Markdown / 表格 / 压缩包 / 音频 / 视频）—— 每个查看器的视图代码就放在**自己的插件目录**里
（例如 `plugins/builtin.image/image_view.py`），程序里没有任何查看器界面代码。
每个查看器把显示名、`kind`、宿主、扩展名、能力写在自己的 `data/viewer.json` 里；基类的 `setup()` 读它、`ctx.require("dialog")`
之后调 `ctx.add_viewer(...)` 登记，子类只实现 `create_view(path, parent=None)` 返回视图控件。查看器控件是普通 `QWidget`，
构造签名 `(path, parent=None)`，可提供 `caption` 属性作为补充说明。

弹窗由插件自己完成：基类在登记查看器时同时登记 `opener`（`ViewerPlugin.open_view()`），它用自己目录里的视图配上
`ViewerWindow` 外壳，再向 `dialog` 扩展接口（`builtin.lib.dialog` 用 `ctx.provide("dialog", DialogApi())` 登记）要一个独立顶层
窗口（Esc 或标题栏关闭按钮退出）；缺少该插件时提示「缺少弹窗工具库（dialog），请到「插件」页启用后重试」。
程序侧只剩调度：`src/app/ui/viewers/window.py` 的 `open_viewer()` 先调插件给的 `opener`，没有 opener 时才用宿主把
`factory` 控件包一层兜底。数据管理页的条目在双击或右键「打开」时（`ManagePage._on_open()`）取出
`ItemService.file_path_of()` 的路径，交给 `src/app/ui/viewers/open_flow.py` 的 `open_path()` 打开；右键「打开方式」里的
点名查看器与「系统默认程序 / 交给系统选择…」分别走同模块的 `open_viewer_with()` 与 `open_system()`。

查看器由 `src/app/core/viewers.py` 的 `ViewerRegistry` 按扩展名索引（同一扩展名取最后注册者）。`PluginService.load_viewers()`
（`src/main.py` 启动时调用；历史命名，实际就是 `load()`）先清空 `ViewerRegistry` 与 `ExtensionRegistry`，再按上面的顺序载入所有
已启用插件，单个插件出错只把错误记在该插件上。禁用「打开方式」插件后，对应格式会退回系统默认程序。启动日志由
`PluginService.loaded_summary()` 汇总（`src/main.py` 的 `logger.info(plugin_service.loaded_summary(viewers))`），形如
「共载入 10 个插件（内置 10 个、外部 0 个）：打开方式 7 个、页面 1 个；共注册 7 个查看器；1 个插件载入失败（见插件页）」——
总数、内置 / 外部来源与各扩展点的贡献数量一眼可见。查看器共用的纯函数在 SDK 里（`app.sdk.data`，插件可直接 import）：
文本解码与截断、xlsx / csv 解析（xlsx 用 `zipfile` + `ElementTree` 自解析，不依赖 openpyxl）、压缩包成员列表与读取、图片信息（Pillow）。

### 插件选项与「插件」页

插件用清单里的 `options` 声明用户可配置项（`src/app/core/plugin_options.py` 的 `parse_options()`：`bool` / `text` / `choice` 三种，
`key` 匹配 `^[a-z][a-z0-9_.\-]{0,63}$`，`choice` 必须有 `choices` 且默认值必须在其中）。「插件」页的「插件选项」按钮弹出
`src/app/ui/plugin_options_dialog.py` 的 `PluginOptionsDialog`，按声明生成控件并把改动写进 `config/plugins.json` 里该插件的 `settings`
（`PluginService.set_option()` / `reset_options()` / `options_of()`）；插件在 `setup(ctx)` 里用 `ctx.option("键")` 读回（取值经
`coerce_option()` 规范化，无法识别时回退默认值），选项改动后插件会整体重新载入，因此读到的值立即生效。若插件注册了查看器，
该对话框还会列出其扩展名的勾选框，勾选即调用 `app.open_with` 的 `set_viewer()`，用户不必去「打开方式」页逐个设置。
「插件」页按贡献（按扩展点分组）/ 来源（内置 / 外部）/ 创建者 / 状态与关键词筛选，支持按默认顺序 / 名称 / 来源 / 创建者 / 状态 / 版本 / 贡献
排序并切换正序、逆序（`PluginService.all(query, state, source, author, contribution, order, reverse)` 与 `PLUGIN_ORDERS`）。
列表项显示启用标记与「贡献 · 来源」徽标，「全部贡献」下**所有插件都会出现**（包括纯库插件）；详情区显示版本 / 创建者 / 状态、
贡献（按扩展点分组）、清单数据、协议信息（贡献 / 依赖插件 / 扩展接口 / 提供库 / 适用管理器版本 / 入口文件）、插件选项摘要与清单路径。
操作包括导入插件目录或 `.zip` 包（解压时拒绝 `..` 与绝对路径）、启用 / 禁用、改显示名 / 说明 / 备注、打开插件目录（内置插件同样可以打开）、
「插件选项」配置、删除外部插件；启用状态 / 备注 / 插件选项都存 `config/plugins.json`（`{"version": 1, "plugins": {...}}`）。内置插件不能删除；
载入失败的插件在列表里标为「异常」并强制禁用，不影响程序启动。
列表项可以勾选，选择条上是一个三态全选框（空 = 全不选、横杠 = 部分选中、勾 = 全选当前列出的插件，点一下在全选 / 全不选之间切换），
勾选后可以「批量启用 / 批量禁用 / 批量删除」
（已处于目标状态的插件会被跳过，内置插件不可删除，删除前有确认框）；右侧的功能按钮改用
`AdaptiveFlowLayout`（`needAni=False`、`isTight=True`，按钮最小宽 96 px），窄窗口下自动换行，不再被裁掉。

### 扩展点、事件与插件页面

插件除了提供查看器与扩展接口，还能往界面上加东西。程序定义了 10 个扩展点（`src/app/sdk/points.py` 的 `ExtensionPoint`）：
概览卡片（`app.ui.home.kpi`）、设置卡片（`app.ui.settings.card`）、数据管理工具栏（`app.ui.manage.toolbar`）、条目右键菜单
（`app.ui.manage.item_menu`）、详情面板行（`app.ui.detail.panel`）、导入筛选器（`app.ui.import.filter`）、打开方式（`app.viewer`）、
页面（`app.ui.page`）；另有 `app.data.import.hook` 与 `app.item.open.resolver` 是**协议预留**、程序侧尚未接线。
界面上的扩展点由 `src/app/ui/framework/contributions.py` 统一读取，插件用 `ctx.contribute(扩展点, {...})` 贡献，
插件被禁用时贡献随之撤销；某个回调抛异常只记日志，不会影响页面。

程序另外广播 7 个事件（`src/app/sdk/points.py` 的 `Events`）：`item.imported`（导入或扫描登记成功）、`item.deleted`、
`user.changed`、`library.changed`（库文件夹迁移）、`theme.changed`、`plugin.enabled` / `plugin.disabled`。
插件用 `ctx.on(事件, 处理函数)` 订阅，处理函数必须写成 `def _on_x(self, **payload)`（载荷见
[扩展点与事件](plugins/EXTENSION_POINTS.md)）。

插件加页面用 `ctx.add_page(key, title, factory, icon=..., bottom=...)`：程序本体在 `src/main.py` 里用
`plugin_service.bootstrap("app.ui", AppUiApi())`（`src/app/core/app_ui.py`）把 `app.ui` 接口登记进 `extension_registry`，
插件登记后主窗口（`src/app/ui/main_window.py` 的 `_sync_plugin_pages()`）会按 `PageSpec.route`（`plugin.<key>`）装配 / 移除导航项与堆叠页，
页面工厂抛异常时退化成一条提示页而不影响其它页面；`load()` 每次重载都会重新提供 `app.ui` 并调用它的 `sync_plugins(已载入插件 id)`，
因此**插件的页面会随启用 / 禁用自动出现与消失**。左侧导航的顺序固定：内置页面按内置顺序
（设置恒在最下面），插件页面按载入顺序追加；追加不下的插件页面只出现在内置的「页面管理」页里
（在那一页里仍可打开），「页面管理」只读、不改布局。`app.ui` 之外，插件还可以
`ctx.provide(name, provider)` 暴露自己的扩展接口供其它插件 `ctx.require(name)` 使用。

**写插件的完整说明（最小插件、SDK 全部接口、库插件、目录规范、排错）见仓库根目录的 [PLUGIN.md](PLUGIN.md)；
清单字段与错误码见 [plugins/PLUGIN_PROTOCOL.md](plugins/PLUGIN_PROTOCOL.md)；扩展点与事件见
[plugins/EXTENSION_POINTS.md](plugins/EXTENSION_POINTS.md)。**

## 日志与保留策略

`src/app/core/logging_setup.py` 按 `Log/Mode` 决定文件名与切分方式：`single`（恒为 `app.log`）、`session`（每次启动 `app-<时间戳>.log`）、
`daily`（`YYYY-MM-DD.log`，每天 0 点切换）、`size`（同样按启动命名，按 `Log/Max-File-Size-MB` 切分）。
保留策略由 `_select_outdated()` 在启动时与该次切分后执行：最新一个文件永不删除，`daily` 模式先按 `Log/Keep-Days` 删除过期文件，
再按 `Log/Keep-Files` 限制数量，最后按 `Log/Max-Total-Size-MB` 限制总量（从最早的文件删起）。

控制台输出按级别着色：TRACE 青、DEBUG 蓝、INFO 绿、SUCCESS 绿（加粗）、WARNING 黄、ERROR 红、CRITICAL 红（加粗），
时间字段绿、模块与函数名青、消息与等级同色。配色由 `logging_setup.py` 的 `LEVEL_COLORS` 显式写死并覆盖 loguru 默认值
（loguru 默认的 INFO 不带颜色，若终端用 IDE 的配色规则就会看成红色）；控制台 sink 写 `sys.stdout` 而不是 `sys.stderr`，
避免 PyCharm 等 IDE 把整行 `stderr` 标红；写日志文件时 `colorize=False`，不会写入 ANSI 转义序列。

## 打包与发布

使用 PyAppify（配置见 `pyappify.yml`、`icons/`、`.github/workflows/build.yml`），发布新版本只需推送标签：

```cmd
git tag v1.0.0
git push origin v1.0.0
```

推送后 GitHub Actions 会构建在线安装包与整包并挂到对应 Release；用户端启动器按标签增量更新。

本项目**不再使用 PyInstaller**（相关 spec/脚本已删除）。
