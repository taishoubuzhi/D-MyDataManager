# 开发与维护说明

运行、依赖与自检脚本见 `README.md`。这里只记录与资源、文案相关的操作。

> 找某个功能的具体实现时，**先看功能索引 [`index/INDEX.md`](index/INDEX.md)**（两级结构
> `index/<功能>/<子功能>.md`，具体到文件、函数与行号）；本文档很长，按需查对应小节即可，不要通读。

## 资源目录

`src/app/resource/` 下的图片由代码通过文件系统路径读取（见 `src/app/core/runtime/paths.py` 的 `RESOURCE_DIR` / `IMAGE_DIR`）：

- `images/logo.png`：窗口与打包用图标（`src/app/ui/main_window.py`）
- `images/header1.png`：首页横幅
- `icons/`：10 个 SVG 图标（`src/app/ui/framework/tokens.py` 的类型图标来源）+ 打包用 `icon.ico` / `icon.png`
- `i18n/`：4 个翻译文件

界面样式全部使用控件自带样式或内联 QSS，**不再使用 `.qrc` / `resource.py` 编译产物**，因此无需执行 `pyside6-rcc`；
重构时清理掉的 `qss/`、`images/controls/`、`resource.qrc`、`resource.py` 已不存在。

## 资源文件夹与库布局

资源文件夹**全局唯一**（配置项 `Storage/Resource-Path`，默认项目根的 `.resources`，旧 `resources/` 启动时自动改名；可在「设置 → 资源文件夹」中更改，
选的是**容器目录**、资源文件夹是它下面的 `.resources`；目标位置里已经有一个 `.resources`（多半是上次没搬完留下的）时程序会问你要不要删掉它再重搬，
`ResourceRootExists` 就是这一问；搬不动（复制失败 / 配置写不回去）会把原文件夹与配置都退回去，绝不留下半份，搬迁成功后写回库路径并自动重启），
数据库（`data.db`）与唯一的库文件夹 `library/` 都在它下面。
库内部结构由 `src/app/core/runtime/paths.py` 与 `src/app/services/library_service.py` 约定：

```
<库>/
  全局/                     全局资源
    store/                  内容寻址仓库（ab/cd/<sha256>）
    covers/                 封面缓存
    backups/                数据库备份
    .datamanager/           库元数据（layout-2.json 为布局标记）
  <用户名>/                 每个用户一个文件夹（名取自用户名，非法字符替换为 _）
    <分类目录>/<原文件名>    该用户的数据文件（各用户分类互不影响；重名自动加序号）
    <原文件名>                「未分类」的数据：没有分类目录，直接放在用户名文件夹下
    <分类目录>/.hiddens/      该分类下被隐藏的数据文件（物理隔离，可单独 ACL 锁定）
    .hiddens/                「未分类」里被隐藏的数据文件
```

- **分类就是目录**：数据管理页左侧的分类树是这些目录的**可视化**，分类的增 / 改 / 移 / 删都要落到目录上，
  磁盘上的目录改了也要回到分类（实现：`LibraryService.category_chain()` / `move_category_dir()` 与
  `src/app/services/taxonomy_service.py`、`category_sync.py`；界面有「按目录同步」按钮）。
  只有「未分类」是例外：它就是用户名文件夹本身，没有自己的目录。
- 数据项记录的是**库内相对路径**（`<用户名>/<分类目录>/<文件名>`），因此用户改名时要调用
  `LibraryService.rename_user_dir()` 同步重命名文件夹并改写记录（「用户」页改名已接线）。
- 扫描只处理用户名文件夹下的文件，跳过 `全局/` 与点目录（分类目录下的 `.hiddens/` 例外：里面的文件登记为隐藏项）；识别不出所属用户的文件会被跳过并打日志
  （见 `LibraryService.scan()`）。
- **布局迁移**：启动时 `src/main.py` 在 `seed()` 之后调用 `migrate_layout()`（`src/app/services/layout_migration.py`）。
  若 `全局/.datamanager/layout-2.json` 不存在，就先备份数据库到 `全局/backups/data-before-layout-v2-<时间戳>.db.bak`，
  再把旧布局（资源文件夹下的 `store/`、`covers/` 与顶层分类目录下的文件）搬进各用户名文件夹并改写记录，
  最后写入标记文件；迁移是幂等的，重复启动不会重复搬动。
- **未分类归置**：紧随其后调用 `migrate_uncategorized()` 给每个用户补齐固定的「未分类」根分类（`UNCATEGORIZED_NAME`，新建用户也会自动获得），
  并把 `category_id` 为空的历史数据项写回该分类。**文件不搬动**：库里记的 `file_path` 与盘上位置不一致时（早期版本在文件没真的搬动时也写了新路径，
  例如 `logo_6.png` 盘上在 `<用户>/` 根、数据库却记 `<用户>/未分类/logo_6.png`），按 `<用户>/<文件名>`、`<分类>/.hiddens/<文件名>`、`<分类>/<文件名>` 找回真实文件并把 `file_path` 修正成它的位置；
  实在找不到的条目只改分类、保留原路径并记 warning。无归属（`user_id` 为空）的数据保持原样，记入统计的 `unassigned`；该步骤同样幂等。
- **分类目录同步**：再调用 `reconcile_categories()`（`src/app/services/category_sync.py`）把两边对齐：
  ①把旧布局遗留的 `<用户>/未分类/` 目录并回用户名文件夹（「未分类」不再有自己的目录）；
  ②磁盘上的每层目录都建出同名分类；
  ③每条数据项按真实文件位置重新定位并按所在目录反推分类（路径对得上就只花一次 `stat`）；
  ④磁盘上没有目录、也没有数据与子分类的「空壳分类」收掉（**首次运行**只补目录、不清理，并写下
  `全局/.datamanager/categories-1.json` 标记，避免把升级前「还没有目录概念」的分类误删）；
  ⑤其余分类缺目录的补上（新播种的默认分类因此也会得到同名目录）。新建用户（`UserService.create()`）与新建分类
  （`TaxonomyService.create_category()`）会立即建目录，所以之后每次启动的清理都是安全的。
  用户名文件夹本身缺失时不清理该用户的分类（换过库位置 / 盘没挂上）。

## 隐藏数据与隐私保护

- 隐藏是**物理隔离**：`LibraryService.set_item_hidden()` 把文件在「分类目录」与「分类目录/.hiddens/」之间搬动（重名自动加序号），
  库内相对路径仍是 `<用户名>/<分类目录>/.hiddens/<文件名>`，数据项的 `is_hidden` 与库内位置始终一致；
  导入时 `is_hidden=True` 直接落到 `.hiddens/`（`ImportService.sanitize_subdir()`）；扫描时 `.hiddens/` 之后不再参与分类匹配，里面的文件登记为隐藏项。
  存档记录 `ArchiveEntry.is_hidden`（schema 5 → 6），还原时按存档把隐藏状态对齐回来（内容相同但隐藏状态不同也算 `changed`）。
- 隐私保护（`src/app/core/runtime/acl.py` + `src/app/services/privacy_service.py`）：配置项 `Storage/Resource-Protected`（锁资源文件夹）与
  `Storage/Hidden-Protected`（只锁各 `.hiddens/`），在「设置 → 隐私保护」用两个开关切换。
  `acl.lock()` 用 `icacls <路径> /deny *S-1-1-0:(OI)(CI)(RX)` 拒绝 Everyone 读取，`acl.lock(path, deep=False)` 只挂 `(RX)` 挡住对象本身；
  `acl.unlock()` 用 `icacls <路径> /remove:d *S-1-1-0` 摘掉拒绝项，并在摘掉后查一次 `icacls <路径>`，**已经在继承时跳过 `/inheritance:e`**——
  那是空操作，却会让 Windows 把 ACE 重新传播到整棵子树（`.resources` 下的运行环境 venv 有十几万个文件，一次传播能卡住启动好几分钟）。
  **浅层锁**：拒绝项一律不带 `/inheritance:r`（继承链不动），`acl.lock_tree()` 先给资源根下的每个直接子项（`.resources/models/` 除外，见 `UNPROTECTED_DIRS`）挂一条深拒绝，
  最后才给资源根挂一条浅拒绝（`deep=False`）——顺序不能反，拒绝项连「遍历」权限一起挡，先锁根就列不出子项，子目录会全漏在保护之外；
  `acl.unlock_tree()` 反向逐项 `/remove:d`（先放行根才枚举得到子项，子项绝不 `/inheritance:e`，否则又会在那十几万个文件上传播一遍）；
  它用的是与 `lock_tree()` 同一份跳过名单——对从没被锁过的 `.resources/models/` 多跑一次 icacls 会重写它的 DACL，Windows 顺势把可继承的 ACE
  传播进整棵子树，启动就卡在那里几分钟（`_icacls()` 另有 60 秒超时兜底，宁可放弃这次操作也不无声挂住启动路径）。
  一次锁 / 放行的 ACL 传播范围因此从十几万个对象降到几百个，开启「保护资源文件夹」后退出与启动都是秒级。
  运行环境与模型权重是程序自己装 / 下载的，不算用户资料却占了十几万个文件，**整棵子树从头到尾一个 ACE 都不碰**，
  程序没在跑的时候（比如 llama-server、ollama 想读权重）它们也照样可用。新版本的模型放在程序目录下的 `.models`
  （不在资源文件夹里，压根不经过 ACL），`UNPROTECTED_DIRS` 里那条 `models` 是留给升级前旧位置 `.resources/models/` 的
  —— 那个旧位置第一次用到模型目录时会被整体搬到 `.models`（见下面「模型机制」），搬不动（有程序占着）才会留着它。
  资源文件夹受保护时隐藏项开关置灰并自动收起（`targets()` 用 `elif`：资源根已锁就不再单独列 `.hiddens`），
  设置页 `_normalize_privacy()` 负责把同时为真的两个开关收敛掉。
- **静态保护模型**：ACL 无法区分同一用户下的不同进程，程序自己也会被拒绝，因此不再「平时锁着、读写时瞬时放行」，而是
  **运行期整场放行、退出时才锁定**。开关的语义随之变为：打开只是记下设置（下次启动整场放行、退出后锁定），关闭立刻放行
  （`_on_protection_changed()` → `privacy.unlock()`）。`privacy.guard()` / `@guarded` 现在是纯语义标记（直接 `yield`），不再翻转 ACL；
  数据库连接、备份、删除 `-wal`/`-shm` 都不再需要放行窗口（`database._privacy_guard()` / `_guarded_sqlite_connection()` 已删除）。
- **异常退出自愈**：启动时 `privacy.begin_session()` 在保护开启时强制放行资源根与各 `.hiddens`（`force_unlock()` 无视设置）；
  两个开关都是关的时候它**一次 icacls 都不跑**（没有锁要解，白跑一次就是上面那几分钟的传播），
  万一真留着锁，`_bootstrap_data()` 会失败、`_degrade_protection()` 的强制放行仍会解开它。
  `paths.make_dir()` 建目录失败只告警不致命，`paths.release_locked_root()` 每个进程只放行一次（`_released` 标志）。
  `_bootstrap_data()`（建目录 + 开库 + 补数据）失败时 `_degrade_protection()` 会关掉两个开关并强制放行再重试一次；
  仍然失败则打印 `python src/main.py --unlock` 的应急提示并返回码 2。`--unlock` 只强制放行、不改设置。
  会话标记 `.configs/session.json`（`paths.SESSION_FILE`）在启动时写入、退出时删除，残留即说明上次异常退出并记入日志。
  退出路径是 `aboutToQuit` + `atexit` 双保险：`_lock_on_exit()` 先 `dispose_engine()`（让 WAL 收尾写回 `data.db`）再 `privacy.end_session()`，
  幂等（`main._locked`）。非 Windows 平台 `is_supported()` 为假，一律跳过并只打日志。
- `tests/services/test_hidden.py` / `tests/services/test_privacy.py` 覆盖隐藏流转、ACL 命令构造、会话标记与启动自愈；自检套件的 `settings_privacy_group`（沿用旧界面门禁 `privacy_group` 的判据）检查设置页分组与「打开不立刻锁定、关闭立刻放行」。

## 翻译文件

界面文案直接写简体中文，`.qm` 文件只用于 FluentWidgets 控件的内建英文文本。修改 `.ts` 后重新编译：

```cmd
pyside6-lrelease src\app\resource\i18n\app.zh_CN.ts -qm src\app\resource\i18n\app.zh_CN.qm
pyside6-lrelease src\app\resource\i18n\app.en.ts -qm src\app\resource\i18n\app.en.qm
```

## 开发者自检

```cmd
.venv\Scripts\python.exe tests\verify.py          :: 一条命令跑完验收门禁（--quick 只跑快的四步，--list 列步骤）
.venv\Scripts\python.exe -m compileall -q src plugins scripts tests
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
只调用公开契约，**不碰真实的 `.resources/` 与 `.configs/`**，因此不需要备份还原，随时可重跑；末行固定输出
`RESULT failures=N`，N>0 时退出码为 1。无图形界面的环境先设 `$env:QT_QPA_PLATFORM='offscreen'`。
`src\main.py --self-check` 在源码仓库里等价于全量自检；没有 `scripts/selfcheck.py` 时（例如只拷了程序本体）
会退化为「能建好界面就退出」的冒烟测试。
`tests\verify.py` 把整套门禁收成一个入口：编译 → 插件桩 `--check` → `unittest discover` → 自检四层 →
`pytest` → 可选依赖验收（`verify_optional_absence.py`）→ 打包冒烟（`smoke_checkout.py`），逐步打印
`ok` / `FAIL`、末行 `RESULT failures=N`；它自动带上 `PYTHONIOENCODING=utf-8` 与 `QT_QPA_PLATFORM=offscreen`，
并把 `pages` 层按 12 项一组拆进独立进程（该层 72 项 Qt 检查在同一长驻进程里偶发原生崩溃）；selfcheck 各步遇到
「原生崩溃且没有任何 FAIL 行」会自动重跑一次（本机 Qt offscreen 的已知环境问题，不是检查失败）。
脚本与自检套件的完整清单、分层与扩展方式见 [`SCRIPTS.md`](SCRIPTS.md)，单元测试的范式见 [`TESTS.md`](TESTS.md)。

**性能基准**：`scripts/benchmark.py`（独立进程、临时目录 `scripts/.tmp/`、跑完自动清理；`--profile` 顺带 pyinstrument 采样，`--keep` 保留目录）。
本机实测（3.8 MB 文本 / 4.0 MB 随机字节 / 4.0 MB PNG 头）：`sha256_of` 1159 MB/s、`put_file` 冷写 955–1053 MB/s、
重复入库 966 MB/s、`read_content` 629–638 MB/s、`iter_content` 747 MB/s、`verify(quick)` 5170 MB/s、`verify(deep)` 558 MB/s。
不把 `pytest-benchmark` 放进常跑门禁：它拖慢 `pytest -q`，而基准的用途是同一台机器上的前后对照。

**基线（改动后不得劣化）**：`unittest discover` 与 `pytest -q` 全绿（当前 unittest 635 项、pytest 637 项）、`selfcheck` 四层 151 项全绿、
`plugin_stubs.py --check` 一致、`compileall` rc 0、`verify_optional_absence.py` = `PROBLEMS: 0`、`smoke_checkout.py` = `PACKAGE-SMOKE OK`。

旧套件（`scripts/dev_check.py`、`dev_check_services.py`、`dev_check_flow.py`、`dev_check_ui.py`）在功能对等后已删除：
它内部 `init_db(force=True)` 重建数据库、运行前备份真实库、结束后自动还原；需要对照旧实现时看 git 历史。

单元测试按主题拆分、按被测对象放进 `tests/{core,services,sdk,plugins}/`：改哪块代码就只跑对应的模块（`.venv\Scripts\python.exe -m unittest tests.core.test_manifest -v`），
不再全量 `unittest discover`；文件名、基类与模板等范式见 [`TESTS.md`](TESTS.md)，隔离目录与语料由 `tests/harness.py`
的 `IsolatedCase`（`tests/.tmp/<用例类名>/`）和 `tests/dataset.py` 提供。临时物统一落在各自目录的 `.tmp/` 下，
进程退出自动清除（`$env:DM_KEEP_TMP=1` 可保留排查），实现在 `scripts/tmpenv.py`。

`scripts/seed_demo.py` 依赖 `tests/dataset.py` 生成示例文件，向真实环境注入约 100 条数据（含隐藏项、回收站项、
第二个用户与一个存档快照）且不会自动删除；清空用应用内「设置 → 维护 → 恢复初始化」或 `scripts/dev_reset.py`。
两者的公共实现是 `src/app/services/maintenance.py`（`reset_config()` 重置全部设置、`reset_runtime_data()` 清数据并重建空库）。

在无图形界面的环境里，可先设置 `QT_QPA_PLATFORM=offscreen`（PowerShell：`$env:QT_QPA_PLATFORM='offscreen'`）。

## 标签体系

标签分两类（`src/app/db/models.py` 的 `Tag`）：`is_global` 为真时对所有用户可见，`user_id` 记录归属用户，
`created_by` 记录创建者。可见性由 `src/app/repositories/tags.py` 的 `_scope()` 决定：全局标签 + 自己创建或归属自己的个人标签（默认用户传 None 表示不限制，因此能看到全部标签）；
`by_name()` 优先返回全局标签，因此新建个人标签不会与已有全局标签重名，转全局时若已有同名全局标签会被拒绝。反过来，新建全局标签（或把个人标签转为全局）时若库里已有同名个人标签，会把这些副本并入全局标签（`TagRepository.merge_shadow_copies()`：引用先转到全局标签再删除副本行），所以不会长期留下「个人标签与全局标签同名」的重复项；历史库里已有的这类副本由启动时的 `database._merge_shadow_tags()` 修复。
全局标签一旦被**别的用户**挂到数据项上，创建者（管理员也一样）就**不能再把它改回个人**：`TagRepository.set_global()` 会用 `others_use(tag)` 统计「非创建者的数据项引用数」，大于 0 直接拒绝（`TaxonomyService.set_tag_global()` 记日志、标签页弹「仍被其他用户的数据项使用」），否则那些条目会带着一个自己看不见、也摘不掉的标签。
默认用户（管理员）可以管理任意标签，其他用户只能管理自己创建的标签：重命名、切换全局 / 个人归属、删除都由
`TagRepository.can_manage(tag, user_id, is_admin)` 判定（管理员或创建者），越权时在状态栏给出提示；
批量操作只处理有权限的标签，其余计入「跳过」（`TaxonomyService.set_tag_global()` / `rename_tag()` / `delete_tag()` / `cleanup_unused()`）。

「标签」页（`src/app/ui/pages/tag_page.py`）用表格展示：`prepare_table(self.table, movable=True)` 让表头可拖动，
`fit_columns(min_width=64, max_width=220)` 按内容自适应列宽；表头的 `TableFilterBar` 提供名称 / 归属 / 创建者 / 数据项数四列筛选
（「数据项数」是数值列：可选「等于 / 大于 / 小于 / 区间」，区间为严格两侧，行内数值由 `_row_numbers` 给出），
`_apply_filters()` 用 `match_filters()` + `setRowHidden()` 处理，结果由「显示 X / Y 个标签」标注，被隐藏行的选中会被忽略。
表格首列是勾选框（`TAG_CHECK_COLUMN = 0`，名称 / 归属等列整体右移一列），选择条上的三态框与「全选 / 全不选 / 反选」按钮同步勾选状态
（全选只作用于当前显示的行）；勾选后可以「批量转为全局 / 批量转为个人 / 批量删除」——只有当前用户创建的标签会被处理，
其余会在提示里说明跳过数量，`TaxonomyService` 的权限校验（`TagRepository.can_manage()`：默认用户或创建者）仍然是最终依据；「批量转为个人」里仍被别人使用的标签会单独计入「不能转为个人」的跳过数。

数据库结构版本在 `src/app/db/database.py` 的 `SCHEMA_VERSION`：低版本库启动时按 `_upgrade_schema()` 原地补列并回填
（历史 `user_id IS NULL` 的标签视为全局），重名的历史全局标签会被重命名为「名称（N）」后再建唯一索引；
只有库版本高于程序版本（降级运行）时才会备份并重建。

程序版本号只有 `src/app/core/runtime/runtime.json` 一处：改 `app_version` 就会改「设置 → 关于」处显示的版本
（`SettingCard` 里写成 `v{APP_VERSION} · 数据目录 …`）；同一份数据里的 `manager_version` 只服务程序自身的协议版本，插件清单已不再有该字段。
`src/app/core/runtime/version.py` 只负责把这两个值读出来（`APP_VERSION` / `MANAGER_VERSION`），不再自己写死。
包级 `src/app/__init__.py` 不再放版本，在那里设置不会有任何效果。

## 用户与权限

`users` 表的 `is_default` 标记默认用户（管理员）：结构升级到 `SCHEMA_VERSION` 4 时把 `MIN(id)` 置位，种子数据建的第一个用户即管理员
（`src/app/db/seed.py` 的 `User(name=DEFAULT_USER, is_default=True)`，`UserRepository.default()` / `ensure_default()` 优先取它）。
`UserService.is_admin()` 决定界面权限：默认用户能在「用户」页新建 / 重命名 / 删除任意用户并设置或清除其口令，
也才能执行设置页与插件页的系统级操作（见下）；存档页的「还原整个存档」对所有用户可见，但普通用户的范围收敛为
本人的条目（`ArchivePage._restore_user_id()` + `visible_entries()`），默认用户才能整份回档所有用户的数据；
普通用户可以改自己的用户名与口令、清除自己的口令，但**不能删除当前用户**：`UserService.delete()` 的 `allow_current` 默认为 False，删自己会把当前用户静默切到默认用户（等于提权），所以服务层直接拒绝，界面也把当前用户卡片上的删除按钮置灰。口令按用户独立保存（`src/app/core/runtime/security.py`），切换用户与解锁隐藏数据都用当前用户口令。
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
`refresh()` 重建 `UserCard` 并高亮当前用户（`set_highlighted()` / `current_card()`），同时用 `_schedule_card_fit()` → `_equalize_card_heights()` 统一各卡片高度（操作区是 `FlowArea`，高度取决于自身宽度，所以延后一轮、按同步后的宽度再量一次）；
`_layout_cards()` 把每行的行伸缩设为 0、只让末行下方的占位行伸缩（`setRowStretch(rows, 1)`），多余高度全部落在网格底部，卡片始终从左上角开始逐个排列。
每张卡片都是自洽的对象单元（`UserPage._user_card()`）：三段式布局 = ①首字头像（`_avatar()`，当前用户蓝底 / 其他灰底）+ 用户名 + 徽标（`card_badges()`：当前用户 / 默认用户 / 已设口令）；
②三行信息（`card_info_lines()`：数据项数 / 分类数 / 创建时间，各带一个图标；完整摘要另挂 `card_summary()` 的悬停提示，不再拿一行超长文本硬裁）；③流式操作区（`FlowArea`，按钮按自身宽度排列、窄卡片自动换行；按 `card_permissions()` 显隐）——
切换为当前用户（当前用户卡片上是禁用态的「当前用户」）、重命名、口令、清除口令、删除，因此所有针对该用户的操作都在卡片内完成，按钮不会被裁出卡片。
`UserService.delete()` 保留默认用户（`if user.is_default: return False`）并拒绝删除当前用户（`allow_current` 默认 False）；其余用户删除时先看有没有数据（是否有未软删除的数据项）：
**有数据**时先算要保留的分类：`_retained_categories()` 只收「有数据项的分类 + 它们的全部祖先分类」，`_mirror_categories(user, target, retained)` 再以「用户名」为一级分类把它们镜像到默认用户下（返回旧→新 id 映射与镜像根；保留集合为空时不建一级分类，`DataItem.category_id` 回落到目标用户的「未分类」，
镜像时用 `CategoryRepository.unique_sibling_name()` 避开同级重名），`<库>/<用户名>` 整目录搬到 `<库>/<目标用户>/<用户名>`
（`LibraryService.relocate_user_dir()`）并给 `DataItem.file_path` 加默认用户名前缀，随后 `_prune_empty_dirs()` 自底向上删掉镜像目录里空掉的目录（子树里没有数据文件的分类连磁盘目录一起消失）；
标签归属与 `created_by` 一并改为默认用户（同名标签走 `TagRepository.merge()`，**没有任何数据项引用的标签直接删除**，不迁移）；
存档条目 `user_id` / `user_name` 改为目标用户且 `category` 前缀上「用户名 / 」；**数据为空**时不建任何镜像分类，直接
`LibraryService.remove_user_dir()` 删掉其用户文件夹（目录里还有库不认识的残留文件时保留目录并打日志），再删除其分类行与用户行。

## 数据概览页

首页（`src/app/ui/pages/home_page.py`）是仪表盘：8 张 KPI 卡（数据总量 / 占用空间 / 今日导入 / 用户数 / 分类 / 标签数 / 存档数 / 存档占用）由模块级
`format_summary(overview(), users=, archives=, archive_bytes=, archive_logical=)` 生成。「存档占用」是全部存档内容在内容仓库里
真实落盘的字节数（跨存档按内容去重），副标题给出存档份数与逻辑大小。整页由四张分区卡片组织（`framework` 的 `section_card()`：概览 / 快捷操作 / 最近导入 / 类型分布），
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
双击左键才打开（`opened` → `ManagePage._on_open()`；默认交给查看器，可在「设置 → 外观 → 左键双击」里改成打开编辑器）；复选框用于多选，Ctrl + 左键逐个切换、Shift + 左键从锚点选到点击项（Windows 规则，
区间由纯函数 `ManagePage.range_ids(order, anchor, target)` 计算，`_anchor` 记录最近一次点击项）。`ItemCard` 是 qfluentwidgets 的
`CardWidget`，它的 `mouseReleaseEvent` 无条件发出 `clicked`，所以页面用 `_press_button` 只认左键，右键不会破坏多选。
工具栏下方的选择条（`ManagePage._build_selection_bar()`）有三态全选框「全选本页」（`tri_state(checked, total)`：空 = 全不选、横 = 部分选中、
勾 = 全选，`_syncing` 守卫防回环，与每行的复选框双向同步）、已选数量、「移动到分类…」、「标签管理」、「关键词管理」、
「批量重命名…」与「清空选择」，没有选中项时这些批量按钮（`_batch_buttons`）一并禁用。

左栏分类树（`src/app/ui/components/category_tree.py`）的每个分类节点都带复选框，**「全部数据」根节点也是三态复选框**（勾上即全选整棵树）：勾选集合由 `checked_categories()` 读出（只收真正勾选的分类，根节点不计入）、`set_nodes(..., checked=...)` 写回，`itemChanged` → `checkedChanged` → `ManagePage._on_category_checked()` 后回到第 1 页重新查数据。`ManagePage._load_items()` 以勾选集合为准（勾选集合非空时忽略单选），并把它连同**所有子孙分类**一起交给 `ItemFilter.category_ids`、把勾选的文件行交给 `ItemFilter.item_ids`（两者取并集），全部取消勾选时回落到最后点过的分类（`_category_id`）或「全部数据」；勾选后中间列表会把这些数据一并**选中**（`_load_items(select_checked=True)` 把过滤结果整批写进 `_selected`，原本没显示出来的数据也因此显示出来）；单击分类行仍是单选并清空勾选集合（例外见下一段的 Ctrl / Shift 多选），`refresh()` 重建树期间由 `_syncing_tree` 守卫，不会误清勾选。
三态级联与汇总：勾选一个分类会把它下面的所有子分类一起勾上（`_apply_state()` 递归向下），子分类的状态再向上汇总（`_aggregate_state()`：子分类全勾 = 勾、全不勾 = 空、否则半选；`_aggregate_all()` 自底向上逐层汇总），所以「全部数据」根节点天然反映整棵树的状态；`_on_item_changed()` 把半选按勾选处理，`_updating` 守卫防止级联过程里信号回环，`set_checked_categories()` 期间不触发 `checkedChanged`。
分类栏底部的「仅显示分类」复选框（`ManagePage.only_categories_box`，默认勾选）决定分类栏要不要列文件：勾选时树里只有分类节点；取消勾选后 `set_nodes(..., files=..., only_categories=False)` 按 `category_id` 把数据项挂到各自分类下面（目录在前、文件在后，组内按名称排序），**「未分类」不再单列节点**——它的文件本来就在用户名文件夹下，因此直接挂在「全部数据」下面。文件行只带**两态**复选框（本身没有子节点），与它同级的子分类一起参与父节点的三态汇总（`_aggregate_state()` 只看 `childCount()`，级联与汇总不必区分行类型）：勾选一个文件就是把它加进 `ItemFilter.item_ids`，勾选父分类则整棵子树（含文件行）一起勾上，只勾中一部分时父节点是半选。文件行右键没有菜单，单击它等于「跳到该数据项」（`fileSelected` → `ManagePage.focus_item()`）；文件行的可见性与中间列表一致——只有打开「显示隐藏项」或「回收站」时，隐藏 / 已删除的数据才会出现在分类栏里。这个开关记在配置项 `Layout/Only-Show-Categories`（`.configs/config.json` 的 `Layout` 组），下次打开按上次的样子恢复。
勾选后可点左栏的「批量移动」/「批量删除」：两个按钮只在勾选了**非根分类**（`_eligible_category_ids()` 排除根分类与固定的「未分类」）时启用；批量移动的目标是树里当前选中的分类（选中「全部数据」= 移到顶层），目标是待移动分类自身或其子孙时拒绝，逐个走 `TaxonomyService.move_category()`；批量删除先确认，子分类上移会与同级分类重名的（`promotion_conflicts()` 非空）跳过并在提示里说明数量，其余走 `delete_category()`（其中的数据变成未分类）；勾上「全部数据」时整棵树都处于勾选状态（此时按钮一并禁用：顶层没有可移动的去处、顶层分类也不能整体删除，提示会改成「已全选「全部数据」…」，处理器同样会拒绝这次操作）；批量移动时若所选分类本来就都在目标分类下，会提示「无需移动」而不再走一次无意义的提交。
分类栏还支持**快捷多选**与**排序**：单选之外，`Ctrl + 左键`逐个加选、`Shift + 左键`选中区间（`CategoryTree` 用 `ExtendedSelection`，`selected_categories()` 把文件行折算成它所属分类并按出现顺序去重、根节点不参与），选中的分类会同步勾选，`ManagePage._on_category_selected()` 在选中项多于一个时**不退回单选过滤**、直接交给勾选集合走批量操作；分类栏上方是排序控件——下拉框 `category_sort_box`（`SORT_MODES`：默认顺序 / 按名称 / 按数据量 / 按最新导入，缺省「默认顺序」即分类自己的 `sort_order`，写 `Layout/Category-Sort-Mode`）与「正序 / 逆序」切换按钮（写 `Layout/Category-Sort-Reverse`；按钮可勾选，图标随状态在 `FluentIcon.UP`（正序）与 `FluentIcon.DOWN`（逆序）之间切换，由 `ManagePage._update_sort_button()` 统一设置，用户 m01544 第 1 条要求两种顺序一眼可辨），排序键由 `CategoryTree._sort_key()` 计算（名称不区分大小写、数据量用分类的 `total_count`、最新导入用 `CategoryNode.latest_at`＝该分类下最近一条数据的导入时间，由 `CategoryRepository.latest_imports()` 一次查询算出），「未分类」始终排在最后；分类行插入时先记下元数据（`_remember_sort_meta()`，键是分类 id——`QTreeWidgetItem` 在 PyQt6 里不可哈希），重建后由零间隔 `QTimer` 推迟到事件循环空闲时统一重排（`_apply_sort_now()`：把分类行整体摘下再按序插回，文件行因此仍留在分类行之后）；分类名过长时分类栏自动换行显示、不再被截断：`CategoryTree` 同时开了 `setWordWrap(True)` + `ElideNone` + `setUniformRowHeights(False)`，但有些 Qt 平台 / 样式下 `QTreeView` 不理会 `setWordWrap`，所以文本本身由纯函数 `_wrap_label()`（按显示宽度折行，全角算 2、半角算 1，阈值 `LABEL_WRAP_UNITS = 22`）插好换行符；`category_label()` 的折行只影响显示，`file_label()` 返回的仍是未折行的原名，分类栏里文件的排序因此不受影响。勾选后除「批量移动」/「批量删除」外还有「批量重命名」（`_renameable_category_ids()` 只排除固定的「未分类」，根分类可以改名）：与文件批量重命名同一套 `BatchRenameDialog` 流程（替换 / 覆盖 / 插入 / 删除），同级的其它分类名作为 `reserved` 预留给重名检查，改名失败（同级重名）的逐个跳过并在提示里说明数量。
中间标题行右侧的「分类栏」/「筛选栏」两个可切换按钮（`tree_toggle_button` / `filter_toggle_button`）分别显示 / 隐藏左右两栏，
显隐状态分别记进 `Layout/Show-Category-Panel` / `Layout/Show-Filter-Panel`，重启后按上次的样子复原。
左侧分类栏默认**全部收起**（配置项 `Layout/Expand-Categories` 打开后启动即展开），用户手动展开 / 收起某个分类后刷新列表仍按用户的
状态显示；右侧筛选区的类型 / 标签 / 关键词三个分组默认**全部折叠**，展开哪几个就记进 `Layout/Expanded-Filters`，下次启动照着恢复。
每页条数、两栏显隐、分类栏与筛选栏的折叠状态都存在 `.configs/config.json` 的 `Layout` 组里，也可以在「设置 → 外观」里改
（「每页条数」下拉、「分类栏默认展开」开关）；分类栏是否列出文件记在 `Layout/Only-Show-Categories`，开关就在分类栏下面。
右键菜单由纯函数 `menu_items(count)` / `open_with_items(suffix)` / `editor_menu_items(suffix)` 生成、`ManagePage._build_menu()` 渲染：**直接打开**、
**查看器**（系统默认程序 / 点名某个内置查看器 / 交给系统选择…）、**编辑器**（系统默认程序 / 点名某个内置编辑器 / 交给系统选择…）、
插件贡献项（一律排在「查看器」「编辑器」两个子菜单之后，样例见下文「编辑器机制」；「自动标签」「自动关键词」四个插件还有工具栏按钮与条目菜单项，
每次运行都尊重当前选中范围，用法见下文「自动标签与自动关键词」）、在文件夹中显示、复制路径、移动到分类…、编辑信息、标签管理、关键词管理、批量重命名…、隐藏 / 取消隐藏、导出选中项、导出为压缩包、移入回收站、
从回收站还原、彻底删除、详情；多选时「编辑信息」「详情」禁用（`MENU_SINGLE_ONLY`），其余批量操作作用于全部选中项（都走 `_require_selection()`）。
「移入回收站」只在选中项里确有未删除项时可用（`ItemService.delete()` 也会先滤掉已在回收站里的项并返回真实条数），
全部已删除时按钮禁用、即便误触发也只会提示「选中的数据都已在回收站里」。
`ManagePage.move_selected(category_id)` 把选中项批量移到目标分类（`None` 表示「未分类」），走 `ItemService.set_category()`，文件跟随到
`<库>/<用户名>/<分类链>/`；工具栏的「移动到分类」按钮与右键菜单共用它。
「编辑信息…」弹出的 `ItemEditDialog` 分两块：上方只读的「数据信息」（类型 / 大小 / 归属用户 / 所在库 / 库内路径 / 磁盘位置 / 创建时间 / 内容指纹，由 `ManagePage._item_info()` 拼装），
下方「可修改的信息」才是名称 / 分类 / 标签 / 关键词 / 隐藏；改名会顺带把库内那份文件一起重命名（`ItemService.update()` → `LibraryService.rename_item_file()`：
沿用原后缀、同名冲突时加 `_N`、文件不在库里时只改显示名并记一条警告），分类变化仍由 `move_item()` 搬目录。
「标签管理」/「关键词管理」（`ManagePage._on_manage_tags()` / `_on_manage_keywords()`）打开 `TagManagerDialog` / `KeywordManagerDialog`
（`src/app/ui/dialogs.py` 的 `TriStateManagerDialog` 子类）：上半区是输入框 + 「添加」——输入一个**新的**标签 / 关键词就对所选数据全部加上，
输入一个**已存在**的、且下面那一行不是全选状态时也按「全选」处理（等于给所选数据全加上）；下面用 `TriStateList`
（`src/app/ui/components/tri_state_list.py`）列出「所选数据现有标签 / 关键词」的合集，每项一个三态复选框：勾 = 所选数据全都有、
横杠 = 只有一部分有、空 = 全都没有；点勾即批量添加、点掉即批量移除（用户点出来的横杠按「勾」处理，避免一次点击变成什么也不做），
关闭后 `ItemService.add_tags()` / `remove_tags()` / `add_keywords()` / `remove_keywords()` 一次提交，没有任何改动则提示「没有改动」；
「标签管理」里全局标签（`TagRepository.global_names()`）在名字后标「（全局）」，被全局标签遮蔽的同名用户标签不重复列出。
「批量重命名…」打开 `BatchRenameDialog`（`src/app/core/runtime/naming.py` 是纯规则）：先选方式——**替换**（子串替换，例：`abc.md` / `bdh.md` / `kjc.md`
把 `b` 换成 `j` 得 `ajc.md` / `jdh.md` / `kjc.md`）、**覆盖**（整体改成一个主干名，可带编号 / 多级字母）、**插入**（在名称的某个位置统一插入文本）、
**删除**（从某个位置起删掉固定长度，没有则跳过，例：`abc.md` 去掉第 2 个字符得 `ac.md`）；编号样式可选数字（可设起始与位数，如 `001`）或字母（`a`…`z`、`aa`…，
大小写可切换），插入 / 删除的位置按字符计。改完**先弹变更清单**（`条目：原名称 → 新名称`，每行可取消勾选），确认后逐个走
`ItemService.update(name=...)`（文件同 `ItemEditDialog` 一起重命名）；重名自动在主干后加 `-1`、`-2`（本页未选中项的原名也算占用），
取消则一项都不改。

「导出选中项」与「导出为压缩包」（工具栏的「导出」/「导出为 ZIP」两个按钮，右键菜单同名两项）都作用于当前选中范围：
**导出选中项**（`ManagePage._on_export()`）先弹「选择导出目录」，`ExportService.export_items()` 把每个条目复制成普通文件到该目录
（文本项直接写为 `.txt`，其它从内容仓库取原始字节），同名文件自动加 `_1` / `_2`，最后生成一份清单
（CSV 或 JSON，列见 `MANIFEST_FIELDS`：名称 / 类型 / 分类 / 标签 / 关键词 / 大小 / 校验和 / 导入时间等）；
**导出为压缩包**（工具栏按钮「导出为 ZIP」，右键菜单项「导出为压缩包…」，都接到 `ManagePage._on_export_zip()`）先弹**导出对话框**
（`app/ui/components/export_dialog.py` 的 `ExportDialog`，默认目录是设置里的「导出目录」，配置项 `Export-Path`，见 [CONFIG.md](CONFIG.md)），
在框里选**分包方式**（`single` 打成一个包 / `top` 按最顶层分类分别打包，见 `PLAN_MODES`）、改**命名模板**
（模板下拉插入变量、改完实时预览包名；认不出的变量原样保留并在框里提示）、调**编号起点与间隔**
（模板里只要有 `number` / `alpha` / `roman` 这类编号变量就能用，改数字会把模板原文重写成 `{变量,起点,间隔}`），
确认后由 `ExportService.export_packages()` 落包：每个包仍按原名存放、清单也一并打进去，写包时先在旁边写
`<包名>.zip.part`、成功后才改名落位，中断不会留下半份包。两者都没有可导出的文件时只提示、不建空文件。

程序本体与插件发起的导出都走同一套引擎（`app.core.export`：分包、命名模板、写包），插件侧门面是
`app.sdk.export`（红线同上：默认先弹确认框，用户关掉就一个字节都不写）。命名模板支持 `{number}` /
`{alpha}` / `{roman}`（可写起点与间隔，如 `{number,3,2}`，等价写法 `{number}` = `{number,1,1}`）与
`{date}` / `{time}` / `{category}` / `{user}` 这些变量；对话框里的起点与间隔对**所有**编号式变量生效
（`app.core.export.apply_numbering()`，例如 `{alpha}` 会写成 `{alpha,2,3}`），详见 [SDK.md](SDK.md) 第 13 节。

`PageBase.auto_refresh()` 只在页面可见（或整个窗口不可见）时刷新，否则记一个待刷新标记、`showEvent` 补刷；
数据管理页的卡片 / 列表行改为复用控件池（`ItemCard.set_item()` / `ItemListRow.set_item()`），删除与回档后的整页重建从约 260ms 降到 30ms 量级。

标题区右上角还有一个「打开导出文件夹」（`ManagePage.export_dir_button` → `_on_open_export_dir()`）：按设置里的「导出目录」
（配置项 `Export-Path`）在系统文件管理器里打开它——导出的压缩包、存档包、整库备份都默认落在这里；目录还不存在会先建出来，
打不开只弹一句提示。下载管理页标题区同样有一个「打开下载目录」（`config.downloadPath` / `download_dir()`，下载设置里可改），
两个按钮都走 `app.ui.framework.open_path()`（最终交给 `app.core.runtime.shell.open_default()`，失败返回 False）。

## 数据存档

存档是「快照 + 引用」：`Archive` 记录快照本身，`ArchiveEntry` 记录每个数据项当时的校验和、分类与路径，
`Blob` 按 SHA-256 内容寻址存放，相同内容只存一份（`src/app/services/archive_service.py`）。
`ArchiveService.entry_state()` 把条目与当前数据对比为 same / changed / removed / missing，还原只处理与当前不一致的条目。
**新建存档只收录未删除的数据项**（`create()` 走 `_all_items(include_deleted=False)`）：回收站里的项不会进新快照，
所以刚建完的存档预览恒为「零变更」；反过来，快照建好之后才被删掉的项仍在存档里，还原时按「撤销删除」恢复。

回档分两种方式：**恢复式（`restore`，默认）**保留当前数据、把存档里的内容补回或替换；
**覆盖式（`mirror`）**以存档为镜像、把存档里没有的现有项收进回收站，只能对整个存档执行、且只有默认用户可用。
「还原整个存档」会打开「回档变更」对话框（`src/app/ui/components/archive_restore_dialog.py`）：默认用户下头部有
两个单选可在两种方式间切换，切换时立刻用 `preview_restore(scope, 新模式)` 重新预演并刷新清单，清单为空则禁用
「确认回档」；普通用户与条目回档不显示切换控件，服务层 `_plan()` 也会对带 `user_id` 的 `mirror` 抛
`PermissionError("覆盖式回档仅管理员可用")`。设置里 **关闭 `Archive/Restore-Preview` 就只走恢复式**（没有弹窗即没有切换入口）。

自动清理可按数量 / 容量 / 时间 / 关闭（设置 → 存储 → 存档自动清理，对应 `prune()` / `prune_by_size()` / `prune_by_age()` /
`auto_prune()`），从最早的快照开始删并始终保留最新一份；**已标记的存档（`Archive.pinned`）会被全部清理策略跳过**，
只有先取消标记、或在存档页手动删除才会消失。`ArchiveService.set_pinned()` 切换标记，`SCHEMA_VERSION` 升到 5 时给旧库原地补上该列。
存档页的「标记存档 / 取消标记」按钮跟随选中存档（未选中时禁用），列表项与详情都会标出【已标记】。
存档页（`src/app/ui/pages/archive_page.py`）用 `SegmentedWidget` + `QStackedWidget` 分成两个页签（`TAB_ARCHIVES` = `archives` /
`TAB_ENTRIES` = `entries`，`TAB_INDEX` / `tab_index(route_key)` 做键位映射）：**存档列表**页签是上方那排存档表格
（逐列筛选 + `Pager` 分页，每页条数读配置项 `Layout/Page-Size`，与数据管理页共用；筛选栏除名称 / 备注 / 标记外，
创建时间按天给「在该日 / 之后 / 之前 / 区间」，条目数 / 逻辑大小 / 实际占用 / 去重率给「等于 / 大于 / 小于 / 区间」，
数值与时间列排在下半行），**存档内条目**页签是明细表，
列定义在 `ENTRY_COLUMNS` / 表头 `ENTRY_HEADERS`（**首列是勾选框「选择」**，其后是名称 / 类型 / 大小 / 分类 / 标签 / 所属用户 / 状态；因此各列序号比 `ENTRY_COLUMNS` 右移一位，代码里用 `ENTRY_HEADERS.index(...)` 取值），上方自带第二条
`TableFilterBar`（`entry_filter_bar`）：文本列模糊匹配，类型 / 所属用户 / 状态是选项列，大小是「等于 / 大于 / 小于 / 区间」
数值列，候选值由 `_refresh_entry_options()` 按当前存档实际出现的取值实时给出，重启筛选不丢用户已选的值；
「分类」与「标签」是筛选栏下方两个并排的可折叠分组（`FilterSection`，与数据管理页同款：搜索框 + 三态全选框 + 复选）——
分类按「用户名 / 分类」分组展开（只看得到自己的分组），标签列出当前存档里出现过的标签，两边都是勾选即筛选。
条目状态走
`ArchiveService.states_for()` 批量计算（含「库内文件已丢失」：档案还在仓库里、但库内那份文件被直接在磁盘上删掉了，
这时回档会「补回文件」而不是误报一致）；「还原整个存档」按钮在有筛选时变成「还原筛选结果（N 项）」，
只回档筛出来的条目（筛不出条目只提示、不发起回档），没有筛选才是整档范围 —— 普通用户下这个范围由
`_restore_user_id()` 收敛为本人，所以普通用户也能安全地按「所属用户」筛选后整份回退自己的部分。
存档页覆写了 `refresh()`（`auto_refresh(signalBus.itemsChanged, signalBus.archivesChanged)` 接的就是它），
所以别处删改数据或新建存档后，存档列表与**条目状态会立刻重取**：在数据管理里删掉一项、再进「存档内条目」，
那一行的状态已经是「已删除」，不会再停在「一致（无需还原）」；
用户点选一条存档会自动切到「存档内条目」页签，也可以随时手动切回去；程序化刷新（恢复选中行、重载列表）时用 `blockSignals`
屏蔽信号，不会强行抢走页签。`tab_keys()` / `current_tab()` / `switch_tab(route_key)` 是给外部（如跳转逻辑）用的接口。

存档列表首列是勾选框，选择条上是一个三态全选框（空 = 全不选、横杠 = 部分选中、勾 = 全选本页，点一下在全选 / 全不选之间切换；只作用于当前页），
可以「批量标记 / 批量取消标记 / 批量删除」，以及**「导出所选存档」**（把勾选的存档连内容打成一个存档包，
见下文「存档包」）。**已标记的存档不能直接删除**：单条删除会提示先取消标记，
批量删除会跳过它们并在确认框与结果提示里说明跳过数量；服务层 `ArchiveService.delete()` 对已标记的存档返回 `False`
（自动清理策略同样跳过已标记的存档）。

「存档内条目」页签用的是同一套勾选（首列勾选框 + 三态「全选本页」+ 已选计数），选择条上另有
**「只选不一致」「清空选择」「下一个不一致」**：前者一次勾上所有与当前数据不一致的条目，后者逐条滚动并选中
（到底回到第一条，方便一条条核对）。打开一个新存档时会**自动勾选不一致的条目**；不一致的行**整行加粗**、
状态列用主题强调色，单元格的悬停提示写明具体状态（内容已变化 / 已删除 / 存档文件缺失 / 库内文件已丢失）。
双击不一致的行会切到数据管理页并定位该数据项（`signalBus.focusItem`）；点「还原选中条目」时优先用勾选的条目，
一条都没勾才退回表格里选中的行。判据是 `ENTRY_INCONSISTENT_STATES`（`same` 之外的状态都算不一致）。

存档页的操作行在「清理无用文件」旁边还有两个入口：**校验存档**（`verify("quick")`，整库比对索引与文件系统，
通过就提示「校验通过」，报出问题时先弹确认框、再让用户决定跑一次深度校验 `verify("deep")` —— 深度校验会解压
每份内容重算 SHA-256）与**优化空间**（有内容还在用旧的压缩编码时出现，点击先回收没有索引引用的内容文件、
再按当前方案把旧编码内容重压一遍，提示几份内容换了编码、回收了多少字节；清理同时收掉空掉的哈希目录，
删内容之后不会在内容仓库里留下空文件夹）。`verify()` 自己会写日志——通过时一行
`完整性校验通过（quick|deep）：N 份内容 / M 个文件`，有问题时一行 `完整性校验发现问题：…` 加逐条
`校验发现：…`，所以控制台 / 日志文件里能看到校验结果，不必只靠界面提示。仓库详情在存档表格上方一行的统计提示里
（`_storage_hint()`：内容份数、实际占用与压缩后大小），同一行还报「共 N 个存档，筛选后 M 个 · 总占用 X」——总占用是全部存档内容
在仓库里真实落盘的大小（跨存档按内容去重），`ArchiveService.archive_usage()` 提供。表格另有「逻辑大小 / 实际占用 / 去重率」三列 ——
逻辑大小是条目大小之和，实际占用是**这份存档**的内容去重后真正落盘的量（`footprints()`，不再是新建存档时的增量占用），
去重率 = `1 - 实际 / 逻辑`（逻辑 ≤ 0 或实际 ≥ 逻辑时显示「—」）。点选存档后的详情里，对比标签说的是**回档将做什么**
（「回档将：新增 N，撤销删除 N…」），若当前另有存档里没有的数据项，会另注「当前另有 N 项不在存档中（回档会保留）」——
它和点「还原整个存档」时的提示同源，因此不会出现「标签说有新增、提示却说无需回档」的矛盾。
「回档变更」对话框把每条变更按种类分组列出（文件 / 分类 / 归属三列，清单高度按条目数在 220-420 之间伸缩，
单元格挂完整内容的悬停提示）：**「内容缺失」的条目也在清单里**（`CHANGE_MISSING`，存档里没有内容、回档补不回来），
`RestoreReport.actionable` 才是「有没有真能执行的变更」的判据——它为假时确认与「先存档再回档」两个按钮禁用，
头部摘要会写明「其中没有可回档的变更」。

所有内容都**整份压缩**后按内容寻址存一个文件（一份内容一个文件、一条内容记录，同一内容不重复占空间）。
压缩优先用 **zstd**（3.14+ 是标准库 `compression.zstd`，3.13 及更早是随依赖装的 `backports.zstd`，两者帧格式互通），两处都没有时自动退回 deflate（zlib）；
文本类用更高级别，已压缩格式（视频 / 音频 / 图片 / 压缩包等）与压不动的内容直接原样保存 —— 方案由
`content_store.policy_for()` 按文件名与 MIME 挑选，两种编码的旧内容都能正常读取。旧编码的内容会在
**自动清理**与「优化空间」时重新压缩（`needs_recode()` 数出待升级内容，`recompress()` 重压），无需重建存档。

从分块版本升级上来时，`SCHEMA_VERSION` 自动从 7 升到 8：旧的块 / pack / 清单索引表会被删除，
旧的 `store/packs/` 数据文件随后由自动清理当作没有索引引用的文件回收（`library/**` 真实文件不受影响）。
升级后旧存档的内容文件需要按新机制重建一次 —— 用下面的「重新加载存档文件」即可。

**重新加载存档文件**（仅默认用户可见，`_load_identity()` 里按 `user.is_default` 显隐）用于索引与真实文件
对不上时的修复：点击先跑 `plan_rebuild()` 预检（直接使用现有文件 / 从存档还原文件 / 与存档不一致，
以存档为准各有多少），确认后交给 `ArchiveRebuildWorker` 后台线程按 `restore`（还原文件）→ `rebuild`
（重建索引）两阶段推进，进度写进 `busy`；重建期间所有写存档入口被禁用（服务层另有一层互斥），
非默认用户或重建进行中调用会被服务层以 `PermissionError` / `RebuildBlocked` 挡住。

**存档包**用于在机器之间搬存档：选择条上的「导出所选存档」把勾选的存档打成
`export_dir()/存档包-<时间戳>.zip`（也可以另存到别处），包内是 `存档包.json` 清单加 `内容/<SHA-256>` 若干份
（跨存档按内容去重，同一份内容只装一次）；头部还有**导入存档包**（可选多个文件）从别的机器导出的包里重建存档记录。
导入**只重建存档记录**（外加把内容写回内容仓库，这样之后真能回档），不碰现有数据项、分类、标签与用户；
同名用户存在时条目会认回那位用户，重名存档自动加「（导入）」「（导入 2）」后缀，**绝不覆盖**现有存档。
服务层在 `src/app/services/archive_bundle.py`（`ArchiveBundleService.export/inspect/import_bundle`），
写包复用导出引擎的 `ZipPack`（先 `.part` 再原子改名）。它和上面的「重新加载存档文件」分工不同：
后者修的是本机索引与文件对不上，存档包则是把存档搬到另一台机器。

**整库包**比存档包大一号，用于整台机器搬家：设置 → 备份与迁移里的「导出整库包」把数据库与库文件夹
一起打成 `数据库包.json` 清单 + `data/data.db`（SQLite 在线备份导出的快照，不带 `-wal` 边车）+ `library/**`
（数据文件、内容仓库、封面、备份、元数据，`__pycache__` / `.pyc` / `.part` 这类缓存跳过），
默认落在「默认导出目录」下，保存框里也能另存到别处。导入有两种：

* **导入整库（新增式）**按名字并用户 / 分类 / 标签，数据项与存档各建新记录，库文件按需落盘
  （同名文件加 `_1` 后缀并改指向），**不删任何现有数据**，包里的内容缺失只记警告不影响其余部分；
* **导入整库（覆盖式）**先把现有数据库与库文件夹改名备份成 `data.db.bak-<时间戳>` 与
  `library.bak-<时间戳>`（挪不动就逐个文件清掉），再**原地清空整库**（删表重建 + 播种默认数据），
  然后用新增式把包里的内容整份搬回来。清空是原地做的，别的页面开着的会话不会挡住它；
  这一步不可撤销，界面上要连点两次确认，完成后程序会重启。

两种方式都先读清单：包不是 `.zip`、没有 `数据库包.json`、格式标识不对、包里的库结构版本比本程序新
（`database.SCHEMA_VERSION`）都会直接拒绝，一个字节都不动；包里一个用户都没有也不认。
「导入整库」属于系统级操作，只对默认用户开放。服务层在 `src/app/services/database_bundle.py`
（`DatabaseBundleService.export/inspect/import_merge` 与模块级 `import_replace`），写包同样走 `ZipPack`。

设置 → 存储里与存档相关的卡片只剩一张：**自动清理无用内容**（`Archive/Auto-Cleanup`，默认开启）——
启动后、创建存档、删除 / 批量删除、`prune*` 之后回收没有索引引用的内容文件，并按当前方案重写旧编码内容
（分块时期的「碎片整理阈值」「整份压缩上限」「整份压缩收益」三张卡已随分块方案一并删除）。
自动清理的执行时机是：启动后延迟 1.5 秒一次（`main_window.showEvent` 里 `QTimer.singleShot(1500, ...)`）、
创建存档后、删除 / 批量删除后、`prune*` 之后各一次。存档页「按策略清理」按钮的提示框会写明当前清理策略
（`policy_summary()` + 「创建存档时自动执行」），标题下不再单独占一行。

## 导入页与批量导入

导入页（`src/app/ui/pages/import_page.py`）有三部分：数据来源（文本 / 批量导入）、导入目标与数据信息、待导入文件信息与导入进度。
「导入用户」下拉用 `UserService.list_users()` 列出全部用户；只有默认用户（`UserService.is_admin()`）能替其他用户导入，其他用户的下拉被禁用且只列出自己，
文案带「（当前用户）」，提示语为「只有默认用户可以替其他用户导入数据，其他用户只能导入到自己的文件夹」；`target_user_id()` 决定分类 / 标签候选与最终归属
（非管理员直接返回 `UserService.current_id()`）；
切换用户会重建分类与标签候选（`_reload_categories()` / `_reload_tags()`）。
「未分类」是每个用户的固定根分类（新建用户自动获得）：分类下拉直接列出 `TaxonomyService.tree(user_id=...)` 的真实分类（没有占位项），
默认选中「未分类」，所以没单独选分类的文本 / 文件都会落在 `<用户名>/` 根目录（「未分类」没有自己的目录；服务层 `ImportService._category_for()` 兜底）。
模式卡片上会实时显示当前选中的文件 / 文件夹摘要（文本模式隐藏该行，文件夹只显示省略后的路径），
导入完成后在结果摘要里追加本次耗时；来源按钮同样是流式布局，窄窗口自动换行。
「开始导入」右侧还可以出现插件按钮（扩展点 `app.ui.import.action`，`import_page._sync_plugin_actions()` 在插件启用 / 禁用后自动增删，
禁用时不占位）：`auto_tag.rule` 与 `auto_tag` 各贡献一个「按规则预填标签」——按当前待导入文件的路径匹配规则，
把建议并进导入页的标签框（`context.apply_tags()`），**不写库**（文件这时还没入库、没有条目 id）；`auto_keyword` 没有导入页入口。
分类的改名与删除规则：`TaxonomyService.rename_category()` 拒绝同级重名（界面提示「无法重命名」）；`delete_category()` 删除分类时把子分类上移到
被删分类的父级，若与同级已有分类重名，界面先弹 `CategoryConflictDialog` 让用户选「自动编号」（`unique_sibling_name()` 依次尝试 `名字-1`、`名字-2`）
或逐个填写新名字，保证同一级下不会出现两个同名分类。

「未分类」是固定的系统分类（`TaxonomyService.is_uncategorized()`：名称为 `UNCATEGORIZED_NAME` 且为根分类）：分类树里它固定排在所有根分类之后、
标签追加「（固定）」并带说明提示，右键菜单为空（`src/app/ui/components/category_tree.py` 的 `menu_entries(fixed=True)` 不返回任何操作）。
服务层同样兜底：`rename_category()` / `delete_category()` 对它直接返回 `False` / `0`，`create_category()`（父级为它时）与 `move_category()` 也会拒绝并打日志，
所以它既不会被改名、删除，也不会长出子分类。把文件夹导入到它下面时，`ImportService.ensure_category()` 会把新分类改为建在根级，不违反这条规则。
数据管理页的「编辑数据项」对话框同样列出当前用户的真实分类树（没有额外占位项）：没有分类的数据默认选中「未分类」，保存后
`ItemService.move_item()` 会把文件移回 `<用户名>/` 根目录（「未分类」= 用户名文件夹本身）。

分类与目录的双向同步：分类的改名 / 移动会同步重命名或搬动磁盘上的对应目录，删除分类会把目录里的数据搬到目标分类目录
（默认「未分类」= 用户名文件夹根目录）再收掉空目录；反过来，界面左栏「按目录同步」（`reconcile_categories()`）会按磁盘上的
真实目录结构刷新分类树——在资源管理器里新建目录就多出分类、删掉目录就少一个分类（空壳分类才会被收掉，有数据或子分类的会保留并补回目录），
启动时也会自动做一次同样的对齐。
批量导入可选多个文件或一个文件夹：文件夹会被展开为「相对子目录 + 文件」清单（跳过 `.DS_Store`、`__pycache__`、`node_modules` 等），
待导入表格逐行显示文件名 / 类型 / 大小 / 修改时间 / 子目录 / 状态（文件数 ≤ 200 时用 `ItemRepository.by_checksum(sha256_of(path))` 标注「库内已有同类内容」）。
这份预览就是本次导入的**快照**：点「开始导入」时按它生成临时清单（`src/app/services/import_job.py`），
之后一律以清单为准、不再重扫目录，所以「预览里看到的」就是「真正会导入的」。

**临时清单（`kind = "journal"`，见 `docs/MANIFEST_PROTOCOL.md` §2.1）** 是批量导入的容错底座：
点「开始导入」先落盘一份待导入清单（`.configs/journals/import/<批次>.json`），**每个文件处理完立刻提交一次**，
所以已完成的项马上能在管理页看到，不必等整批结束。

- **暂停 / 继续**：进度卡片上的「暂停导入」让后台线程在**当前文件处理完之后**停下（`import_file()` 内部的复制不可中断，
  强行中断只会多留一个孤儿文件），清单与进度都留着；「继续导入」读回同一份清单接着做。
- **取消整批**：剩余还没开始的项全部标成「已取消」，已经导入的内容不受影响；「取消选中项」只取消「待导入文件信息」里选中的那一行
  （暂停中取消同样有效，状态直接写在清单里）。
- **崩溃恢复**：清单要等到**待导入项全部到终态（导入完或已取消）才删除**。程序异常退出后重新打开导入页，
  页面会在启动时扫描残留清单，显示「上次未完成的导入」卡片，给出「继续导入」与「放弃这次导入」两个选择；
  恢复时对每个未结清的项按三步核对——① 内容 SHA-256 已登记、且来源就是同一个文件 → 直接认回来；
  ② 在它本该落到的目录里找到「大小相同、SHA-256 相同、还没登记」的文件（崩溃瞬间复制进库却没提交的孤儿）→
  用 `ImportService.register_file()` 原地收养，不再复制一份；③ 都没有 → 交给 `import_file()` 重做（顺带按当前重复策略重新判定）。
- **写入节流**：清单按「每 200 条 / 每 2 秒」合批落盘（`IMPORT_FLUSH_EVERY` / `IMPORT_FLUSH_INTERVAL`），
  状态跃迁、暂停、取消时强制写一次；崩溃最多丢掉最后一个窗口内的进度，而恢复本来就要逐项重新核对。

服务层（`src/app/services/import_service.py`）：

- `import_files(sources, *, on_event=None, **options)`：多文件导入所选分类；
- `import_folder(directory, *, name="", user_id=None, parent_category_id=None, on_event=None, **options)`：把整个文件夹当作一个新分类（`ensure_category(name or 目录名)`）整体导入，`ImportResult.category_name` 回填分类名；
- `import_tree()` 保留文件夹内的相对子目录（写进条目 `subdir`，再由 `LibraryService.unique_rel_path(..., subdir=...)` 决定落盘子目录）；
- `on_event` 回调收到 `ImportEvent(index, total, source, status, detail)`（status 为 added / skipped / failed），界面据此实时刷新进度与结果表；
- 进度下方的结果表（3 列：文件 / 状态 / 说明）用 `wrap_table()` 自动换行、不省略（长文件名与库内路径整段可见，完整文本仍挂 tooltip），
  行高与表格高度靠 `fit_table_height()` 跟着内容长（上限 460 px，超过就用表格自己的滚动条），导入过程中每 20 行重算一次、结束后再算一次。

进度与结果由 `ImportWorker(QThread)`（`src/app/ui/components/import_worker.py`）在后台执行：工作线程里 `database.new_session()`，
驱动 `ImportJob.run()`，把清单事件转成 Qt 信号（`progressed` / `evented` / `jobReady` / `stateChanged` / `finished_job`），
主线程只做界面更新；暂停 / 取消通过线程安全的 `ImportControl` 传进去。结束后 emit `itemsChanged` / `librariesChanged` / `categoriesChanged`；
`ImportPage.refresh()` 与其它页面一致，用来在进入页面时重建用户 / 分类 / 标签候选项，并再扫一次残留清单。

## 封面

封面规则集中在一个服务里：`src/app/services/cover_service.py`，导入、扫描登记（`LibraryService` 的「扫描并登记」）与设置页「重置封面」三处共用，界面侧只认 `DataItem.cover_path` 与 `item_card.cover_source(item)`：

- **图片**：不再生成缩略图副本，`cover_path` 留空，卡片直接拿**库内那份原文件**当封面——`cover_source()` 在 `cover_path` 为空且类型是图片时调 `_image_cover_path()`，**先把 `file_path`（库内相对路径）拼到 `library_root()` 上**，库内文件不在了才退回导入来源 `source_path`，两处都没有才返回空串（显示类型图标）。**不能**改用 `item_api._absolute_path()`：那个函数优先返回导入时的外部 `source_path`，原文件被移走 / 删除后封面就取不到了（用户 m01544：重置封面后部分图片退回默认图标）。视频抽帧用的 `cover_service._source_of()` 同理，也是库内优先。这样封面目录里不会为一堆图片各存一份重复内容。
- **视频**：导入时用 ffmpeg 抽**第一帧**，`ffmpeg -y -loglevel error -i <源文件> -frames:v 1 -vf scale=<Cover-Size>:<Cover-Size>:force_original_aspect_ratio=decrease <全局>/covers/<checksum>.png`，成功才写进 `cover_path`。
- **其它类型**：没有封面概念，`cover_path` 一律清空，界面显示类型图标。

ffmpeg 的定位顺序（`ffmpeg_executable()`）：PATH 上的 `ffmpeg` → 当前解释器装的 `imageio-ffmpeg` 自带的 ffmpeg →「模型」页装的运行环境（`<模型根>/runtime/<profile>/venv`，模型根默认是程序目录下的 `.models`，路径层由 `plugins/lib.model/.plugin/paths.py` 决定）。**取不到 ffmpeg 或抽帧失败都不算错误**：`grab_video_frame()` 返回空串、日志记一条 debug，卡片退回类型图标，导入流程照常完成。

设置页「维护 → 重置封面」（`SettingsPage._reset_covers()`）走 `cover_service.reset_covers(session)`：先删掉 `全局/covers/` 下现有的封面文件、再把所有非删除项的旧 `cover_path` 清空，然后按上面的规范重新生成一遍（图片留空、视频重新抽帧、其它类型清空），返回 `{"items", "covers", "cleared", "failed"}` 并在提示里报出生成、清理与失败数量。数据文件本身不受影响。

## 表格与列表通用件

`src/app/ui/components/data_table.py` 提供列表页共用的小工具（标签页 / 存档页 / 导入页都在用）：

- `TableFilterBar`（`configure([(键, 显示名, "text"|"choice"|"number"|"date"[, 选项]), ...])` + `set_options()` / `set_filter()` / `filters()` / `has_filters()` / `reset()`，`changed` 信号）：贴在表格上方的 Excel 式逐列筛选栏，文本列子串匹配、选项列精确匹配；数值列给「不限 / 等于 / 大于 / 小于 / 区间」模式（区间才显示第二个输入框，`解析数字` 时 `unit="size"` 接受 `512KB` / `1.5MB`），日期列给「不限 / 在该日 / 在该日之后 / 在该日之前 / 区间」模式（用 qfluentwidgets `DatePicker`，按天）；选项里 `row: 1` 可把该列排到筛选栏第二行；`reset()` 只在确有变化时发信号；
- `prepare_table(table, *, movable=True)`：隐藏行号、整行多选、只读、表头可拖动（`setSectionsMovable`）、列宽 Interactive；
- `fit_columns(table, *, min_width=72, max_width=260, weights=None)`：先按内容量宽再夹紧，权重列吃剩余宽度；
- `wrap_table(table, *, min_width=88, max_width=320, fit_rows=True)`：与 `prepare_table()` 相反的一套——关掉省略号（`ElideNone`）、打开自动换行、列宽按内容夹在 `min_width` ～ `max_width` 之间且末列吃满剩余宽度，长文件名 / 长路径整段可见（导入页的结果表用它；成百上千行的数据网格仍然用单行省略的那套）；
- `fit_table_height(table, *, max_height=420, min_height=0, extra=8)`：按当前内容重算最小高度（换行表格内容一变就得重算，否则多出来的行被裁在可视区外），夹在 `min_height` ～ `max_height` 之间，超上限交给表格自己的滚动条；
- `match_filters(values, filters, numbers=None, sets=None)`：判断一行是否命中全部筛选条件——文本列忽略大小写的子串匹配、选项列精确匹配、数值 / 日期列命中 4 元区间 `(下限, 含下限, 上限, 含上限)`、集合条件（分类 / 标签）要求被勾选的值全在行内集合里；空条件一律跳过。

动态重建列表时，摘掉旧控件必须走 `src/app/ui/framework/feedback.py` 的 `release_widget(widget)`（先 `hide()` 再 `setParent(None)` + `deleteLater()`）：
PyQt6 + Windows 下只调 `setParent(None)` 并不会隐藏控件，每个被摘掉的条目都会变成一闪而过的小顶层窗口（`ManagePage._clear_layout()`、
`HomePage._clear()`（`FlowArea.take_widgets()`）、`FilterSection.set_items()`、`UserPage.refresh()` 的旧卡片、`SettingsPage._refresh_libraries()` 的旧行、
`TableFilterBar.configure()` 的旧筛选控件、`ItemCard.set_tags()` 的旧标签块均已改用）。只 `deleteLater()` 的旧控件会作为子控件继续留在界面上
（且 Python 侧的类身份会丢失），所以「先隐藏、再断父级」这一步不能省。

## 界面样式统一

页面骨架尺寸统一取自「插件管理 / 查看器管理 / 存档管理 / 标签管理」这一套风格，常量与构件都在 `src/app/ui/framework/`（间距与构件）与 `src/app/ui/components/`：

- `PAGE_MARGINS = (24, 20, 24, 20)`、`PAGE_SPACING = 12`：所有页面的外层边距与间距（`ScrollPage` / `Page` 基类建好正文布局，内容用 `add_header()` / `add_section()` / `add_widget()` / `add_row()` 加进去）；
- `PANEL_MARGINS = (12, 12, 12, 12)`：列表面板卡片；`DETAIL_MARGINS = (16, 14, 16, 14)`：详情 / 表单卡片；`COMPACT_MARGINS = (10, 8, 10, 8)`：紧凑正文（查看器正文、筛选面板内层）；`KPI_MARGINS = (14, 8, 14, 8)`：KPI / 统计卡片内边距；`SCROLL_GUTTER = 6`：滚动区右侧留白（避免内容贴住滚动条）；
  `panel_card(parent, margins=..., spacing=...)` 返回 `(CardWidget, 卡内竖直布局)`；
  `section_card(parent, 标题, 说明, ...)` 在它上面再叠一层标题（`StrongBodyLabel`）与说明（`CaptionLabel`），概览 / 导入 / 用户 / 设置四页的面板都改用它；
- `PageBase.add_header(标题, 说明)`（工厂函数 `page_header(parent, 标题, 说明)`）：统一的标题行（`TitleLabel` + 弹簧）与下方说明文字，主操作按钮用 `header.add_action(控件)` 挂到右侧；
- `accent_color()` / `accent_name()`：主题强调色（包 `qfluentwidgets.themeColor()`，默认 `#009faa`），
  禁止在 QSS 里写死强调色——自定义控件（用户卡片头像与徽标、选中指示条、拖放框、关键词块）都改成按主题取色；
  用户卡片的头像 `avatar_style(accent)`、徽标 `badge_style(accent)`、卡片高亮 `highlight_fill()` / `highlight_hover()` 也已下沉到 `src/app/ui/framework/theme.py`，页面里不再有样式字符串；
- 空态统一用 `empty_state(parent, 文案, icon=...)`（图标 + 居中说明），不要再用裸 `CaptionLabel` 当占位。

按钮一律用 qfluentwidgets 的 `PrimaryPushButton`（主操作）与 `PushButton`（次操作），不要用原生 `QPushButton`；
**同时带图标和文字**的按钮用 `src/app/ui/framework/buttons.py` 的 `IconTextButton` / `IconTextPrimaryButton`（插件里写 `ui.IconTextButton`）：
配置项 `Layout/Simple-Display`（「设置 → 外观 → 简化显示」）是三挡下拉：`none` 不简化、`default` 默认、`full` 完全简化（旧配置里的布尔值自动换算：`true` → `full`、`false` → `none`）；`full` 下它们只显示图标、变成居中的方形按钮（方形模式把 QSS 内边距清零，图标不会被左右裁掉），完整文字挪进提示条，所有页面与弹窗一起生效；`default` 只收「同一个容器里图标不重复」的按钮 —— 插件页的「重命名 / 编辑说明 / 编辑备注」都是 `FluentIcon.EDIT`，只留图标就分不清，这类共用图标的保持「图标 + 文字」；
自检 `icon_text_buttons` 会静态断言 `src` 与 `plugins` 里不再有「图标 + 文本」却用裸 `PushButton` 的地方，并在运行时把简化显示验一遍（还按 `SE_PushButtonContents` 量按钮内容区，方形按钮必须装得下图标，退出简化显示后按钮必须按 `sizeHint` 恢复文字尺寸——包括没显示过的页面，不会留下 32×32 的方按钮把图标与文字挤在一起）；主色按钮的强调色底与反转图标色来自 qfluentwidgets 的 `PrimaryPushButton` QSS 规则，所以 `IconTextPrimaryButton` 必须把 `PrimaryPushButton` 放在第一个基类（PyQt 的多继承只保留第一个基类的 QMetaObject 链，QSS 类型选择器按链匹配）；切换简化显示时用 `setCustomStyleSheet(widget, qss, qss)` 重装样式，**不要写空字符串**——那会把 `PushButton` 自带的整份按钮 QSS 冲掉（底色、给图标让位的内边距、禁用态一起丢）；
没有图标的按钮（如「重置筛选」）不受影响，仍然显示文字。自检 `simple_modes` 专门盯这件事：不简化时按钮都有文字、完全简化时都没有文字、默认挡位下共用 `FluentIcon.EDIT` 的三个插件按钮保留文字而图标唯一的按钮收成方形。
设置页的 `PushSettingCard` 自带原生按钮，已用 `SettingsPage` 里的 `ActionCard`（继承它并换成 `PushButton`）替换。
纯文本标签也能图标化：`framework/labels.py` 的 `IconTextLabel`（工厂 `icon_text_label(图标, 文字, parent)`，只画图标的用 `icon_label(...)`）
平时画「图标 + 文字」，`Layout/Simple-Display` 不是 `none` 时只留图标、文字挪进悬停提示（`default` 挡位下同一个容器里共用同一枚图标的标签仍保留文字）；筛选栏的分组标题与「范围与排序」、导入页的字段标签、
查看器页的字段名、插件页的两栏标题、数据管理页的「用户」与概览页的「当前用户」都改用它，纯文本按钮（自动编号、每组只保留最新、
全部取消勾选、清空选择、重置筛选）也都补上了图标。标签内部图标固定 18 px 且贴左、文字紧随其后（`addWidget(..., AlignVCenter)` + `addStretch(1)`），放在很宽的容器里也不会被推到中间；信息行可以传 `keep_text=True`：即使打开简化显示也保留文字（用户卡片的三行信息就是这么写的）；
自检 `icon_text_labels` 逐项验「平时有文字、简化显示只留图标且有同一段提示」与「图标 18 px、贴左、文字紧跟图标」。
「设置 → 外观」另有「每页条数」（`Layout/Page-Size`，50 / 100 / 200 / 500）、「分类栏默认展开」（`Layout/Expand-Categories`）与「悬停提示延迟（毫秒）」（`Layout/Tooltip-Delay`，默认 2000，0 表示立刻弹出）与「左键双击」（`Layout/Double-Click-Action`，默认「打开查看器」，可改成「打开编辑器」——只影响左键双击，右键菜单里两个入口都还在）四张卡。
所有数字配置卡都换成 `framework/settings_cards.py` 的 `NumberSettingCard`：滑块左边多一个输入框（宽 `SPIN_WIDTH` = 96 px），
滑块与输入框双向同步、取值范围直接取配置项的 `range`，要精确填一个数时不必拖滑块；自检 `number_setting_cards` 逐张验范围与双向同步。
设置页的每张配置卡（开关 / 下拉 / 数字）改动后都会在右上角弹一条提示：`SettingsPage._connect_setting_toasts()` 汇总三类信号，
`TOAST_DELAY_MS` = 400 毫秒内的多次改动合并成一条（拖滑块不会刷屏），两个隐私保护开关保留自己的详细提示、不再重复；
自检 `setting_change_toasts` 逐张切换开关与下拉框，并检查连拖滑块 6 次只弹一条。
页面 / 分区的说明文字不再常显：`PageHeader` 的副标题默认隐藏、整段挂到标题的悬停提示上，`section_card()` 的说明也挂到分区标题
（分区没有标题时挂到卡片本身）上；要补充说明用 `header.add_hint()`，随状态变化的文案用 `header.set_hint()`，别再拿
`CaptionLabel` 把说明铺在页面上。`framework/tooltips.py` 的 `install_tooltips()`（`src/main.py` 与自检基座都会调用）
把 `QProxyStyle` 的 `SH_ToolTip_WakeUpDelay` 接到配置项 `Layout/Tooltip-Delay`、`SH_ToolTip_FallAsleepDelay` 归零，
`describe()` 还会往上借最近的父级提示（最多 `MAX_PARENT_HOPS` = 4 层），所以写在卡片 / 列表行上的说明会被里面的标题与数值继承。
自检 `hover_hints`（延迟可配 + 提示继承 + 简化显示下的方形按钮）与 `prose_moved_to_hints`（说明确实搬进了提示）逐项校验。
问号标识（`hint_badge()` / `HintBadge`，14 px）只在**页面标题旁边**保留一个（`PageHeader`）：卡片、表单行与列表里的说明改成纯悬停提示，
不再到处挂问号，鼠标停在问号或标题文字上弹出的是同一段提示。提示文本按 `HINT_COLUMNS` = 44 个半角宽自动折行（`wrap_hint()`，中文按两个宽度算），
长说明不会拉成一条横穿屏幕的长条：`TooltipFilter` 拦截 `ToolTip` 事件，用 `QToolTip.showText()` 画折行后的文本；
自检 `hint_badges_on_titles` 断言整窗问号数量与页面标题数一致、且没有挂在卡片上。
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
  `setStyleSheet(widget, CustomStyleSheet(widget).setCustomStyleSheet(light, dark))`（`clear_background` 内部就是这么写的）。数据管理页筛选栏最下面两个复选框用 `align_check_box()`（`theme.py` 的 `CHECK_BOX_QSS`：`margin-left: 0`、`spacing: 4px`、指示器 18 px），让指示器与文字的左缘跟同卡片里 `IconTextLabel` 的图标 / 文字对齐。
  页面底色不走 QSS：由 `install_app_theme()` 装的调色板（`theme_palette()`，浅色 `window` = `#f0f4f9`、深色 = `#202020`）提供，页面与滚动视口保持透明。
`scripts/selfcheck.py` 的 `theme_background` 会切到浅色逐页断言「没有任何可见控件仍按旧调色板实绘深色」，`settings_privacy_group` 会断言设置页的
「资源文件夹 / 隐藏文件」两个开关、资源加密时隐藏开关置灰并自动收起，以及分组里不再出现多余的「立即锁定 / 立即放行」按钮。

## 默认标签

`src/app/db/seed.py` 的 `DEFAULT_TAGS`（重要 / 待整理 / 收藏）在 `seed_user_defaults()` 里只写入一次，且写成**全局标签**
（`user_id=None`、`is_global=True`、`created_by` 记第一个用户）：新用户开箱即用，也不会每个用户各存一份。写之前先看有没有同名全局标签；
没有同名全局标签、但库里已有同名**个人**标签时，会先建好全局标签再把这些个人标签并入它（`TagRepository.merge_shadow_copies()`），
避免凭空多出一个同名全局副本；历史库里已经存在的这种副本由启动时的 `database._merge_shadow_tags()` 修复（`item_tags` 的引用改到全局标签后删除个人标签行）。

## 查看器机制与查看器插件

打开行为全部由插件 `builtin.lib.viewer` 通过 `viewer.open` 扩展接口提供：程序侧 `app.services.viewer_service` 只留类型别名（`ViewerInfo` / `ViewerFactory` /
`ViewerOpener`）与调度门面（注册表搬到了 `plugins/builtin.lib.viewer/.plugin/registry.py`）
（`open_path()` / `open_viewer_with()` / `open_system()`，拿不到接口时退回系统默认）。插件里的 `ViewerRules.resolve(path)` 按顺序决定用哪种方式 ——
自定义规则且填了程序 → `custom`；自定义规则没填程序 → `ask`（交给系统选择）；规则为内置且指定了 `viewer_id` 且该查看器还在 → 用指定查看器
（已不存在时回退到该扩展名的默认查看器）；规则为内置但没指定 → 该扩展名的默认查看器；没有可用查看器 → 回退 `inherit`；
没有规则时，有内置查看器就用内置，否则用系统默认。规则按扩展名（不含点）存在 `.configs/viewers.json`（旧版 `.configs/open_with.json`
首次读取时自动迁移），形如 `{mode, program, args, viewer_id}`，`mode` 取 `builtin` / `inherit` / `custom`，
参数里的 `{path}` 会替换成实际路径（没有占位符时自动补在末尾，见 `src/app/core/runtime/shell.py` 的 `build_command()`）。

「查看器」页同样由该插件以**插件页面**形式提供（`plugins/builtin.lib.viewer/.plugin/config_page.py`，页面模板来自 UI 工具库 `builtin.lib.ui`）：
以「库里出现过的所有文件格式」为列表（`ctx.host.file_formats()` ∪ 已配置规则，可按扩展名 / 插件名搜索），选中后在右侧配置：打开方式下拉
（「使用查看器」/「继承系统默认」/「自定义程序」，可用项由 `ViewerRules.available_modes()` 决定）、具体查看器下拉（仅「使用查看器」且该格式有多个查看器时可选，
「自动」表示按注册顺序）、自定义程序与参数，另有「保存」「恢复默认」「测试打开」。

插件可以用 `ctx.require("viewer.open")` 拿到 `ViewerOpenApi` 查询 / 修改查看器（`set_viewer` / `use_viewer_for_all` / `reset_viewer` 等）；
执行外部程序、交给系统选择、在资源管理器中定位分别是 `app.sdk.ui.open_with_program()` / `ask_open_with()` / `reveal()`，失败只记日志、不抛异常。

### 插件协议（v2）

插件都放在 `plugins/<id>/` 下，内置插件与外部插件走同一条载入路径。每个插件根目录只有 `plugin.json`（必需）、`plugin.py`（入口）、`PLUGIN.md`、`.plugin/`（实现模块）与 `.data/`（数据）；
插件类继承 `src/app/sdk/` 的 `Plugin`，在 `setup(ctx)` 里用 `PluginContext` 注册东西、在 `teardown()` 里收尾。清单示例：

```json
{
  "id": "sample.viewer",
  "name": "示例查看器",
  "version": "1.0.0",
  "description": "说明文字",
  "author": "作者",
  "api_version": ">=1.0 <2.0",
  "entry": "plugin.py",
  "class": "SampleViewerPlugin",
  "depends": [{"id": "builtin.lib.viewer"}, {"id": "builtin.lib.ui"}],
  "builtin": false
}
```

- 必需字段是 `id`（匹配 `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`）、`name` 与 `api_version`（适配的 SDK 版本范围，例如 `>=1.0 <2.0`，缺了会被拒绝载入）；`entry` 对**外部插件**必填且文件必须存在，
  内置插件可以省略（因此纯库插件、纯数据插件不必写入口脚本）；`class` 用来点名入口脚本里的插件类。
- **协议不再区分插件类型**：清单里没有 `kind` / `kinds`（写了会报「插件清单已取消类型字段」）；`extensions` / `capabilities`
  这类数据字段也不再放清单（写了会提示放进 `.data/`），扩展名、显示名、能力说明这些数据由提供相应能力的库插件读取。
- `libraries` 声明插件对外提供的库模块，例如 `builtin.lib.viewer` 提供 `viewer` → `plugin.py`；别的插件 `depends` 它之后
  可以用 `from dm_plugin.<插件 id>.plugin import ...` 静态导入（`app.sdk.library("<id>", "plugin")` 是兜底写法），
  `libraries` 里声明的模块必须存在且非空；它的导入名由 `module` 路径决定——`plugin.py` → `dm_plugin.<id>.plugin`、`.plugin/api.py` → `dm_plugin.<id>.api`、`.plugin/ui/controls.py` → `dm_plugin.<id>.ui.controls`（`/` 换成点、不带 `.plugin`）；没在 `libraries` 里声明的模块属于实现细节。
- `provides` 与 `libraries` 的分工：`provides` 声明的扩展接口是**运行期对象**，别人用 `ctx.require("名字")` 取，插件禁用时随之消失、
  消费方要自己兜底；`libraries` 声明的库模块是**可 import 的代码**，别人用 `from dm_plugin.<id>.plugin import ...` 拿到类 / 函数
  （可继承、可实例化、有 IDE 补全）。清单里的 `provides` 只用于声明与展示，不参与校验。
- `depends` 支持简写（`"builtin.lib.ui"`）与对象（`{"id": ..., "version": ">=1.0", "optional": true}`）两种写法；
  `optional` 的依赖缺失只跳过、不算失败；`conflicts` 声明冲突插件；**载入顺序不用单独声明**（旧的 `load_after` / `incompatible`
  已取消，写了会在 `manifest` 阶段报 `插件清单已取消字段 …：请改用 …`：顺序由 `depends` 决定，冲突改用 `conflicts`）。
  缺依赖、版本不满足、重复声明、循环依赖都会让插件载入失败（个别失败不影响其它插件与程序启动）；**冲突不是载入失败**：冲突插件照常载入，
  只是在对方启用时不能启用——见下面的「插件冲突」。
- 数据文件放在插件根目录的 `.data/`（清单里已没有 `data` 字段），插件用 `ctx.data("viewer")` 读 `.data/viewer.json`、`ctx.data_path("viewer")` 取它的路径：清单里只放参数，不放数据。
- `api_version` 是版本范围（`1.2`、`>=1.0 <2`、`1.2.*`、`^1.2`、`~1.2.3`、`||` 或、空格 / 逗号表示「且」，
  见 [插件协议](PLUGIN_PROTOCOL.md)），要求高于当前 SDK 版本会让插件载入失败（`manager_version` 已从协议删除）；`version` 是插件自己的版本。
- `options` 声明用户可配置项（`bool` / `int` / `text` / `choice`，`choice` 必须有 `choices` 且默认值在其中，`int` 可用
  `minimum` / `maximum` / `step` 限定范围），插件用 `ctx.option("键")`
  读回，详见下面的「插件选项与插件页」。

载入时程序按 `(内置优先, id 字典序)` 排序，依次走五个阶段：`manifest`（读清单）→ `dependency`（依赖与版本）→
`import`（建 `dm_plugin.<id>` 命名空间并导入入口脚本）→ `construct`（实例化插件类并 `attach()`）→ `setup`（调用 `setup(ctx)`）。
任何阶段失败都只把错误记在那个插件上（「插件」页标「异常」并强制禁用），不影响其它插件与程序启动。
插件脚本**只能** import 标准库、Qt、`app.sdk`，以及自己 `depends` 过的插件（而且只能取对方 `libraries` 里声明过的模块：
`from dm_plugin.<id>.plugin import ...`、`from dm_plugin.<id>.api import ...`）；`app` 下除 `app.sdk` 之外的**任何**模块（`app.core` / `app.services` / `app.ui.*` …）
都会被自检 `plugin_imports` 直接拦下 —— 需要什么能力，通过 SDK、扩展点、事件或插件库来拿。

### 插件冲突

两个插件可以互相「不合」：在清单里用 `conflicts` 声明对方的 id（旧写法 `incompatible` 已取消，写了会报「已取消字段」）。

- 冲突**只在两边都启用时**挡住启用，不挡载入：两个插件都能正常载入、正常提供各自的能力，只是不能同时启用。
- 两个都启用时按载入顺序（`(内置优先, id 字典序)`，被依赖的更前）**靠前者胜出**，另一个保持禁用，「插件」页状态显示
  「与插件冲突」，详情里写明与谁冲突；把胜出的那个禁用后，另一个立刻可以启用。
- 在「插件」页点「启用」被冲突挡住时不会误报成功：程序在控制台打印
  `插件启用失败：<id>（与插件冲突：<对方>）；先禁用插件 <对方> 再启用本插件`，界面同时弹红色提示，插件保持禁用。
- 每次启用 / 禁用（包括批量）都会在控制台留一行 `插件已启用：<id>（<名称>）` / `插件已禁用：<id>（<名称>）`；
  状态本来就一样时打印 `插件保持启用/禁用：…`，不会假装发生了变更。
- 声明是对称的：任意一方写了就算双方冲突，不需要两边都写（写了也无害）。

插件**载入失败**只来自这些原因：清单字段 / 版本范围不合适（`manifest`）、缺依赖或依赖未启用（`dependency`）、
入口导入失败（`import`）、构造或初始化抛异常（`construct` / `setup`）——具体原因逐条打在控制台（`插件不可用：<id>（[阶段] 原因）`），
汇总句尾也会列出。

`dm_plugin.<id>` 是载入期在内存里合成的包，磁盘上不存在，编辑器会把 `from dm_plugin...` 标成找不到模块：
仓库里由 `scripts/plugin_stubs.py` 按清单生成 `stubs/dm_plugin/**` 的 `.pyi` 桩，把 `stubs` 标成源码根即可
（仓库自带 `.idea/D-MyDataManager.iml` 已配好），改了清单或库的 `__all__` 后重跑脚本，自检 `plugin_stubs_current` 保证同步。

### 查看器

内置的查看器是一套「工具库 + 七个具体插件」：`builtin.lib.viewer`（`plugin.py` 提供 `ViewerPlugin` 基类与 `ViewerWindow`
窗口外壳，`registry.py` 提供注册表 `ViewerRegistry`）、`builtin.lib.ui`（`provides: dialog` / `ui`，提供弹窗外壳、页面模板与控件工厂），以及
`builtin.viewer.image` / `builtin.viewer.text` / `builtin.viewer.markdown` / `builtin.viewer.spreadsheet` / `builtin.viewer.archive` / `builtin.viewer.audio` / `builtin.viewer.video`
（图片 / 文本 / Markdown / 表格 / 压缩包 / 音频 / 视频）—— 每个查看器的视图代码就放在**自己的插件目录**里
（例如 `plugins/builtin.viewer.image/.plugin/image_view.py`），程序里没有任何查看器界面代码；音频与视频的播放控件由界面工具库提供
（`builtin.lib.ui` 的 `PlayerPanel` 与 `format_time()`，`QtMultimedia` 到真正播放时才导入），
两个插件都直接继承它（`AudioViewer` / `VideoViewer`），只差一个 `shows_video` 开关。
每个查看器把显示名、`kind`、宿主、扩展名、能力写在自己的 `.data/viewer.json` 里；基类的 `setup()` 读它、`ctx.require("dialog")`
之后调 `ctx.add_viewer(...)` 登记，子类只实现 `create_view(path, parent=None)` 返回视图控件。查看器控件是普通 `QWidget`，
构造签名 `(path, parent=None)`，可提供 `caption` 属性作为补充说明。

弹窗由插件自己完成：基类在登记查看器时同时登记 `opener`（`ViewerPlugin.open_view()`），它用自己目录里的视图配上
`ViewerWindow` 外壳，再向 `dialog` 扩展接口（`builtin.lib.ui` 用 `ctx.provide("dialog", DialogApi())` 登记）要一个独立顶层
窗口（Esc 或标题栏关闭按钮退出）；缺少该插件时提示「缺少弹窗工具库（dialog），请到「插件」页启用后重试」。
程序侧只剩调度：`app.services.viewer_service` 的 `open_path()` / `open_viewer_with()` / `open_system()` 通过 `viewer.open` 扩展接口把活儿交给插件
（`plugins/builtin.lib.viewer/.plugin/window.py` 的 `open_viewer()` 先调插件给的 `opener`，没有 opener 时才用宿主把 `factory` 控件包一层兜底）。
数据管理页的条目在双击或右键「打开」时（`ManagePage._on_open()`）取出 `ItemService.file_path_of()` 的路径，交给 `open_path()` 打开（`Layout/Double-Click-Action` 选成「打开编辑器」时改走 `app.services.editor_service` 的 `edit_path()`，`ManagePage._open_in_editor()`；没装编辑器插件时编辑器门面自己退回系统默认程序）；
右键「查看器」里的点名查看器与「系统默认程序 / 交给系统选择…」分别走 `open_viewer_with()` 与 `open_system()`。
「系统默认程序」最终落到 `app.core.runtime.shell.open_default()`：本进程的 `os.startfile()` 在本机对**任何**路径都返回
`[WinError 5] 拒绝访问`（Store 版记事本这类 AppX 关联无法从本进程激活），被拒后自动回退 `explorer.exe <路径>`，并按它的退出码确认到底有没有接手
（交出去后退 1 才算成功；起不来死在 `0xC0000142`、或一直不退都算没接手）。没接手就返回 False，不再假装成功：
`app.services.viewer_service.open_system()` 这时改用**能打开这个格式的内置查看器**打开，并说明原因——「设置 → 资源文件夹 / 日志 / 备份」这类
打开文件夹的按钮拿到的也是 False，会如实提示。

查看器由 `plugins/builtin.lib.viewer/.plugin/registry.py` 的 `ViewerRegistry` 按扩展名索引（同一扩展名取最后注册者），模块级实例 `viewer_registry`
随 `viewer.open` 扩展接口一起由插件持有。`PluginService.load()`（`src/main.py` 启动时调用；历史命名 `load_viewers()`）先取旧接口调
`api.clear()` 清空注册表、再清空 `ExtensionRegistry`，然后按上面的顺序载入所有已启用插件，单个插件出错只把错误记在该插件上；
插件卸载时 `_drop_plugin_state()` 调 `api.unregister_plugin(plugin_id)`。禁用「查看器」插件后，对应格式会退回系统默认程序。

### 编辑器机制与编辑器插件

数据编辑走的是与查看器**平行**的一套机制：程序侧 `app.services.editor_service` 只留类型别名（`EditorInfo` / `EditorFactory` / `EditorOpener`）与调度门面
（`edit_path()` / `edit_with()` / `open_system()`，拿不到 `editor.open` 接口时退回系统默认编辑器）；
注册表、规则（`.configs/editors.json`）与窗口外壳都在工具库插件 `builtin.lib.editor`（`plugin.py` 提供 `EditorPlugin` 基类与 `EditorWindow`
窗口外壳，`registry.py` 提供 `EditorRegistry`）里。数据管理页右键的「编辑器」子菜单由程序本体搭出来
（`ManagePage.editor_menu_items()` / `_build_editor_menu()` 读 `app.services.editor_service.editors_for()`），所以禁用 `builtin.lib.editor` 后菜单仍然在，
只是只剩「系统默认程序 / 交给系统选择…」。
编辑器分两种 `kind`：`internal` 在程序内用可编辑控件改内容（控件若提供 `save()` / `is_dirty()`，窗口会启用「保存」按钮、显示「已修改」并在关闭前
询问未保存改动，保存后通过公开信号刷新条目的 checksum / size / 内容）；`external` 不提供程序内控件，直接调用系统默认程序编辑，符合「本身进行
数据编辑则打开电脑系统默认的编辑器」。
内置编辑器插件：`builtin.editor.text`（文本 / 代码 / Markdown / CSV / JSON 等文本后缀的内部编辑器，复用 `app.sdk.data` 的编码探测）与
`builtin.editor.office`（doc/docx/xls/xlsx/ppt/pptx/pdf 等文档表格走系统默认程序）。没有可用编辑器插件时，右键「编辑器 → 系统默认程序」走
`app.services.editor_service.open_system()` 退回系统默认程序，子菜单不会因为缺少插件而消失。
「编辑器」配置页与「查看器」页同构：左列格式清单每行标注当前打开方式（`内置编辑器（…）` / `自定义程序（…）` / `继承系统默认`）与「库中 N 项」，
搜索框按「格式 + 编辑器名」过滤，见 `EditorConfigPage._fill_list()` / `_state_text()`。

### 模型机制与模型插件

模型能力由内置库插件 `lib.model` 通过扩展接口 `model.open` 提供（调度门面 `dm_plugin.lib.model.api` 属于插件自己，
程序侧没有模型门面）。没有启用模型插件时列表返回空、`acquire()` / `invoke()` 直接抛 `ModelError`。模型分两类：**本地模型**（权重放模型目录的 `local/` 下，
也可以由「扫描目录」或拖拽直接登记用户自己目录里的权重，由 worker 子进程 / llama-server / ollama 跑）与**外部模型**
（填接口地址、模型名与密钥，走 OpenAI 兼容接口或 Ollama）。登记表在 `<模型目录>/registry.json`，设置与密钥在
`.configs/models.json`（密钥只存本地，日志与界面一律打码）；模型目录默认 `<程序目录>/.models`，可以在「模型」页设置里改到别处
（换位置时**选的目录只当容器，模型目录是它下面的 `.models`**，与资源文件夹一个规矩；目标建不出来 / 写不进去会明说是哪个位置，不会装作成功），
插件目录被覆盖安装删掉也不会丢。

其它插件对接模型统一走 `ctx.require("model.open")`，或依赖 `lib.model` 后 `from dm_plugin.lib.model.api import acquire`：按 `model_id` 或 `capability`
（`chat` / `completion` / `embedding` / `rerank` / `vision` / `asr` / `tts` / `image` / `ocr` / `classify`）取一份**租约**，
`lease.invoke(task, payload)` 调用、`lease.close()` 归还。模型不会一开始就常驻：`acquire()` 时才加载（同一模型加载中会排队，
超时抛 `ModelBusyError`），常驻上限默认 1、超出按最久没用到卸载（卸载等于杀进程，显存会真的还回去），空闲默认 600 秒自动卸载。

「模型」页（`plugin.model_manager`）负责新建本地 / 外部模型、一键下载（断点续传、sha256 校验；地址先按本页「下载源（模型）」算，
再交**程序本体的下载队列**，镜像回退走「下载管理 → 下载设置」里的全局下载规则）、
扫描或拖入权重目录登记、加载 / 卸载 / 测试、查看下载队列，以及**运行环境**（每个 profile 一套独立 venv，装在
模型目录下的 `runtime/<profile>/venv`，装依赖前弹确认框、**永不静默安装**，失败可重建或整目录卸载）。
模型目录深到让路径顶破 Windows 单条 260 字符上限时，会提示你**在设置里把「模型目录」换到更浅的位置**；
你不改就取消这次安装——绝不不问就往别处（比如系统盘）写。模型目录不在资源文件夹下面，所以改「资源文件夹」
不会再搬走几十 GB 的权重，资源文件夹上锁也不会牵连运行环境里十几万个文件；升级前留在 `.resources/models/` 里的
模型会在第一次用到模型目录时**自动整体搬到** `<程序目录>/.models/`（同盘只是改名、瞬间完成；跨盘先整份复制到临时目录、
复制完整了才落位，落位成功才删旧目录；登记表与权重不用重新登记，运行环境也跟着目录走、一般不用重建），搬不动（有程序占着、复制失败）
才继续用旧位置，并在页面上说明原因；想自己挑位置就用设置里的「更改位置…」。
带 `-gpu` 的运行环境会一并装上 CUDA 12 运行库（cudart / cuBLAS / cuDNN，走 `nvidia-*-cu12` 这几个包），
否则 GPU 版 llama.cpp 加载时会报缺 DLL；模型登记的是 CPU 版 profile（如 `llama-cpp`）而只装了 `-gpu` 版也能跑；设备选到 CUDA 时 llama.cpp 默认把所有层放上显卡（参数里写了 `n_gpu_layers` 就按写的来），CPU 设备则强制全走 CPU。
下载队列**就是程序本体的那一个**（「下载管理」页看到的是同一批任务，在哪一边暂停都算数）：每个任务有「暂停 / 继续 / 取消 / 移除记录」，队列右上角还有**一键继续 / 一键暂停 / 一键取消**：
按当前任务状态决定能不能点（有暂停的任务才能「一键继续」、有正在下载或排队的任务才能「一键暂停」、两者有其一就能「一键取消」），
一键取消会先确认再连同半成品分片一起清掉（已完成的权重文件不动）。卡片上的「打开权重目录」只在**这条模型已经有权重**时可点，
还没下载时会禁用并提示先点「下载」；点「下载」会**立即把文件排进下载队列**（只用本地信息算地址，不等联网探测），队列里马上看到任务，提示「已加入下载队列：N 个文件；连不上会自动重试」；
一个都没排上（例如仓库信息缺文件清单）会弹红字说明原因，不会毫无反应。任务下完后**自动认盘上的权重**：模型卡片自己刷新、状态从「未填充」变成就绪，不用再手动「更换权重」。
同页设置里，最上面是「模型目录」（只读显示当前生效位置，右边「更改位置…」换到别处、「恢复默认」回到程序目录下的 `.models`；
选位置时挑的是**容器目录**，模型目录会落在它下面的 `.models`；换位置时可以选「把现有模型文件一起搬过去」，同盘瞬间完成、
跨盘先整份复制到临时目录再落位，落位成功才删旧目录，旧目录删不掉会在下次进页面时再清一次；**搬不动就留在原地、绝不把设置切到搬不过去的目录**，
失败原因会同时进页面提示与 `.logs/app-*.log`），
接着是「代理」（填本地代理地址，留空就是直连），然后是「下载源（模型）」下拉决定去哪个地址
（「HuggingFace官方」/「HF-Mirror镜像」/「自定义」，自定义时可以一行行加自己的下载路径，和「安装源（pip）」一样：下拉
左边、自定义输入框和按钮右边对齐；地址清单固定高度，不会在这一项下面留一大片空白）；它下面那行**提示**跟着选项变——选官方写官方地址、选镜像写镜像地址、选自定义写最先用的那条路径（都是提示，界面上不给改）。
「下载源（GitHub）」下拉只管**装运行环境**时要下的 GitHub Releases 轮子（比如 llama.cpp 的 CUDA 轮子）：选「Github官方」就直连，三个镜像各是一个独立选项
（「ghproxy镜像」「gh-proxy镜像」「ghfast镜像」，选谁就只拼谁的前缀），选「自定义网址」填自己的前缀，
除官方档外最后都还会退回官方直连。**模型下载不再认这一项**：模型下载的镜像与回退全在「下载管理 → 下载设置」的下载规则里按域名匹配。
这一页**没有「保存设置」按钮**——代理、下载并发、同时常驻模型数、空闲多久卸载、
推理设备、安装源、下载源（GitHub）、下载源（模型）、程序环境开关都是改一下就立刻存盘。运行环境每行除「安装 / 卸载 / 日志」外还有「本地 whl…」，
可以挑手上已有的 `.whl` 离线安装（先校验文件名里的 Python 与平台对不对得上；装好后这个按钮变灰，要换包先「卸载」，
离线装会把清单里还差的包一起补上（不要求 whl 自带全部依赖），例如 `-gpu` 环境缺的 CUDA 运行库会顺手从镜像拉；
要是普通安装卡在 `github.com` 连不上（llama.cpp 的 CUDA 轮子挂在 GitHub Releases 上），失败提示会直接告诉你改用「本地 whl…」，
或者把「下载源（GitHub）」换成镜像后重试（pip 失败时程序会自己从日志里认出那条 GitHub 直链、换成镜像地址再跑一次）；
控制台与行内悬停提示会写明「来源：本地文件：…」）；**不同运行环境可以同时装**（互不等待，同一个环境不会开两条），
一键补全点下去会先让你选「并发安装」（几个环境同时装，默认）还是「挨个安装」（装完一个再装下一个）；
安装中点「暂停 / 取消」会立刻掐掉 pip 进程（行内写「正在暂停…」/「正在取消…」），
卸载成功（含装进程序环境的模式）连日志一起清掉；模型卡片上有「日志」按钮（跑过一次才会出现），一个模型只留最新一份日志，
删掉模型会连它的日志一起删；本地模型加载失败后卡片按钮不会消失，改好设置可以直接重试，
不必先点「刷新」。
细节与接口成员见 [`../plugins/lib.model/PLUGIN.md`](../plugins/lib.model/PLUGIN.md)。
启动时的控制台输出分三层，程序侧不需要再打印任何东西：`app.sdk` 第一次被导入时播报一次
「SDK 已载入：版本 1.0」（`app.sdk.sdk_banner()`），随后每载入成功一个插件各来一行「插件 `<id>` 已载入」，
最后由 `PluginService.loaded_summary()` 给一句汇总，形如
「插件载入：库插件 2 个（已启用 2、未启用 0）、功能插件 8 个（已启用 7、未启用 1）」——
不再按扩展点统计「几个查看器」，只看库插件 / 功能插件的数量与启停情况；
扫描行还会报「不可用」的数量，并且每个不可用插件（清单有误 / 缺依赖 / 依赖未启用 / 载入失败）都单独打一条 `warning`「插件不可用：<id>（[阶段] 原因）」，汇总句尾再补「N 个插件不可用：<id>（原因）；…」——控制台能直接看到是谁、为什么；冲突插件不算不可用（照常载入，只是被挡住启用，见「插件冲突」）。查看器共用的纯函数在 SDK 里（`app.sdk.data`，插件可直接 import）：
文本解码与截断、xlsx / csv 解析（xlsx 用 `zipfile` + `ElementTree` 自解析，不依赖 openpyxl）、压缩包成员列表与读取、图片信息（Pillow）。

### 自动标签与自动关键词（四个外部插件）

`auto_tag.rule`（规则自动标签）、`auto_keyword.rule`（规则自动关键词）、`auto_tag`（模型自动标签）与 `auto_keyword`（自动关键词）是
**外部功能插件**：清单里 `builtin: false` **且** `enabled: false`，默认不启用，未启用时程序本体一个控件都不多（页面、工具栏、
导入页按钮全都不出现，`.configs/plugins.json` 里也不会多出条目）。它们共用共享库插件 `lib.autolabel`（规则模型 + 匹配引擎、
数据类型→模型对齐表、批处理管线、共用控件），方案、逐条需求映射与全部偏差记录见 [`../plugins/lib.autolabel/PLUGIN.md`](../plugins/lib.autolabel/PLUGIN.md)。

- 数据文件：用户规则写 `.configs/autolabel.rules.json`（出厂默认 `plugins/lib.autolabel/.data/rules.json`，同名键用户覆盖，
  坏正则只标红不炸）；**关键词规则单独一份** `.configs/autolabel.keyword-rules.json`（`auto_keyword.rule` 出厂自带的 9 条规则
  写在插件代码里 `plugins/auto_keyword.rule/.plugin/store.py` 的 `FACTORY_RULES`，不占 `.data/`，用户只存自己那份）；
  **关键词库**写 `.configs/autolabel.keywords.json`（`{"version": 1, "keywords": [...]}`，页面上可手输、可从现有条目扫描收进来）；
  各数据类型的模型对齐写 `.configs/autolabel.align.json` 的 `label`（标签）与 `keyword`（关键词）两段，各存各的。
  `auto_tag.rule` 与 `auto_tag` 在清单里互指 `conflicts`：两者都能载入，但**不能同时启用**（用户 m42668）；`auto_tag` 自己也能按规则挂标签——页面上的「规则」卡共用同一份规则文件，`merge_rule_tags`（默认开）打开时规则标签与模型标签合并去重后一起挂，两边的新标签都会自动进标签库（用户 m42753）。
  关键词那一对同理：`auto_keyword.rule` 与 `auto_keyword` 互指 `conflicts`（用户 m43110），都能载入、不能同时启用；两组方案之间（关键词 ↔ 标签）互不冲突，可以同时启用。
- 页面：四个插件各有一个页面（`auto_tag_rule` / `auto_keyword_rule` / `auto_tag` / `auto_keyword`），一律用界面工具库 `builtin.lib.ui` 的构件
  （自检 `autolabel_ui_via_tool_library` 拦 `qfluentwidgets` / `PyQt6` 直连，只放行 `FluentIcon`）。`auto_tag` 页面只有模型方案（规则方案在 `auto_tag.rule` 页）；
   它与 `auto_keyword` 页都有一张「数据类型对齐」表（5 列：数据类型 / 方案 / 主模型 / 对齐模型 / 状态）+
  「方案」列三态 = `启用`（跟随系统）/ `自定义`（自己挑过）/ `已停用`（这一行被关掉）；「主模型 / 对齐模型」两列显示登记时起的名字
  （没登记才写「未登记：<方案 key>」）；保存后表格会自动选中刚改的那一行，该行的备注显示在表格下面的提示里。
  设置存在用户清单 `.configs/autolabel.align.json`（每个用途下全部数据类型一行，写全量），表格刷新时直接读清单，显示与设置不会脱节。
  运行区（进度面板可取消、预览框），运行在后台线程、用 `QTimer(150ms)` 回主线程画界面。
- **按行「跟随系统」或「自定义」**：对齐表的「方案」列只写这两种——`启用` 表示这一行跟随系统，`自定义` 表示自己挑过
  （判据就是这一行自己勾没勾「启用系统方案」；没在清单里出现的数据类型按跟随系统算）。系统方案是 `lib.autolabel/.data/align.json` 给每种数据类型配好的
  主模型 + 对齐模型。选中一个数据类型，点「设置对齐」打开编辑框：勾上「启用系统方案」这一行就**跟随系统**（模型与对齐模型都用系统配好的，
  出厂以后换模型它跟着换，只读那一行显示的是**登记模型的名字**）、取消勾选就能自己挑（主模型与对齐模型各一个下拉：
  `已登记` 组是本地下过的模型、按登记时的名字显示，`系统方案` 组只列还没登记的出厂方案——已经登记过的方案不会再重复出现）；
  点「重置」把这一行还原成系统方案、点「全部重置」还原整张表。「一键补全」把这一行还缺的模型逐条
  `models.create_from_template(key)` 登记成草稿并写回对齐表；「检查缺失」看已登记模型的权重 / 运行环境
  （`models.requirements(model_id)`），逐条报还缺什么，并可直接跳到「模型」页。对齐表里的长内容（模型名、状态等）
  默认**自动换行、完整显示**，行高与表格高度都随内容长，不用再切换显示方式；这一行是跟随系统方案还是自己设的，由「方案」列与下面那行提示标识。
- **缺文件只问一次**：「一键补全」发现缺**模型文件**时弹**一个**确认框，里面按「权重 / 配置 / 分词器」分组列出缺哪些
  （例如 CLIP 这种 transformers 模型要在同一个目录里放 `pytorch_model.bin`（权重）+ `config.json`、`preprocessor_config.json`（配置）
  + `tokenizer.json`、`vocab.json`、`merges.txt` 等（分词器）——`from_pretrained(本地目录)` 按约定读它们，缺一个就跑不起来，所以都得下）。
  点确认就把主模型与对齐模型的这些文件交给模型工具库的下载队列（与「模型」页同一套：镜像回退、断点续传、可暂停 / 取消，进度看「模型」页）；
  不点确认不会下载。运行环境（pip 包）仍然只在「模型」页装，插件不静默安装。
- **数量当场改**：模型标签页与关键词页（`auto_tag` / `auto_keyword`）的运行区都有「设置数量」按钮，直接改 `min_tags` / `max_tags`（标签页还含「规则标签与模型标签合并」）
  或 `min_keywords` / `max_keywords`，写进 `.configs/plugins.json` 的插件设置，等价于在「插件」页改。
- **挂完标签界面立刻刷新**：整批挂标签结束后广播一次 `itemsChanged` + `notify_tags_changed()`（`app.sdk.items`），
  数据管理页的列表与标签页的标签清单当场就能看到新标签（含刚建出来的）；批处理**中途**的每一次写标签都不广播，
  免得标签页跟着重建很多遍——`ensure_tags()` / `tag_items()` 自己不发标签变更信号，由调用方在整批写完时调一次（用户 m07851）。
- 批量统一走模型工具库：所有请求交 `models.run_batch()`（按模型分组、同组只加载一次模型、组间并行、单条失败隔离、带进度与取消）。
- 入口与收敛（两处登记过的偏差）：`auto_tag.rule` 与 `auto_keyword.rule` 都有导入页「按规则预填标签 / 关键词」+ 管理页工具栏 + 条目菜单三个入口
  （导入页那一个就地按文件后缀预填，不写库）；
  `auto_tag` 没有导入页入口，工具栏与条目菜单**直接起后台任务挂标签**（完成提示并刷新列表；页面里还有带进度与取消的整批运行）；
  `auto_keyword` 没有导入页入口，另外两个入口只跳转到关键词页。原因都是：导入时文件还没入库、没有条目 id，构造不出
  `BatchRequest`；页面外的入口又没有地方放进度与取消。
- `auto_tag.rule` 页面只有「规则」与「运行」两区：它不调用模型，所以规则编辑弹窗里没有「交模型判断」类型，
  也没有提示词输入框；按后缀配默认标签就是建一条 `field=suffix` / `op=is` 的普通规则（不再另设一张「数据格式」表）。
  编辑到出厂自带、类型为「交模型判断」的规则时，页面会说明本页不跑模型并禁用保存。
- `auto_keyword.rule` 页面是「规则 / 关键词库 / 运行」三区，与标签规则页同构：规则的「匹配」列里挑的是**要挂的关键词**
  （弹窗里的目标字段标题与「关键词库」下拉都由共享控件参数化，标签页行为不变）；关键词库区能手输加词 / 删词、
  「扫描现有条目」把库里已经出现过的关键词（含出现次数）收进库里、「保存关键词库」才落盘；运行区可「预览（不写库）」
  或直接「开始挂关键词」，挂完广播一次 `itemsChanged`（**不发**标签变更信号——关键词不是标签）。
  它与 `auto_keyword` 在清单里互指 `conflicts`（用户 m43110）：都能载入，但**不能同时启用**——要先用规则挂一批、
  再禁用本插件换成 `auto_keyword` 补其余条目。
- 数量选项用清单的 `kind: "int"`（`min_tags` / `max_tags`、`min_keywords` / `max_keywords`），标签上限还不超过库里标签总数，
  关键词只设上限（`0` = 不限）。两对同名方案各自**互斥**：`auto_tag.rule` 与 `auto_tag`、`auto_keyword.rule` 与 `auto_keyword` 都互指 `conflicts`，不能同时启用；
  关键词插件与标签插件之间**互不冲突**（产物不同，宿主写库时会去重）。

### 插件选项与「插件」页

插件用清单里的 `options` 声明用户可配置项（`src/app/core/plugins/plugin_options.py` 的 `parse_options()`：`bool` / `int` / `text` / `choice` 四种
（`int` 用数字微调框展示，`minimum` / `maximum` / `step` 都可省），
`key` 匹配 `^[a-z][a-z0-9_.\-]{0,63}$`，`choice` 必须有 `choices` 且默认值必须在其中）。「插件」页的「插件选项」按钮弹出
`src/app/ui/plugin_options_dialog.py` 的 `PluginOptionsDialog`，按声明生成控件并把改动写进 `.configs/plugins.json` 里该插件的 `settings`
（`PluginService.set_option()` / `reset_options()` / `options_of()`）；插件在 `setup(ctx)` 里用 `ctx.option("键")` 读回（取值经
`coerce_option()` 规范化，无法识别时回退默认值），选项改动后插件会整体重新载入，因此读到的值立即生效。若插件注册了查看器，
该对话框还会列出其扩展名的勾选框，勾选即调用 `viewer.open` 扩展接口的 `set_viewer()`，用户不必去「查看器」页逐个设置。
「插件」页按贡献（按扩展点分组）/ 来源（内置 / 外部）/ 创建者 / 状态与关键词筛选，支持按默认顺序 / 名称 / 来源 / 创建者 / 状态 / 版本 / 贡献
排序并切换正序、逆序（`PluginService.all(query, state, source, author, contribution, order, reverse)` 与 `PLUGIN_ORDERS`）。计数文案直接说明状态：没有筛选时是「共 N 个插件」，被筛过时是「已筛选：X / 共 N 个插件」并在旁边出现「清除筛选」按钮（从「查看器」页跳过来会按该扩展点自动筛选，列表因此只列出一部分插件）。
列表项右侧由 `components/plugin_delegate.py` 的 `PluginItemDelegate` 自绘一枚状态徽章（已启用 / 已禁用 / 载入失败，色调取自
`PluginInfo.state_tone`），列表行按两行画：第一行插件名（`TITLE_ROLE`），第二行「类型 · 来源 · 贡献」（`SUBTITLE_ROLE`，小一号、淡一些），
两行都按可用宽度省略、并裁剪在右侧徽章左边（`PluginItemDelegate` 覆写 `initStyleOption()` 清掉基类要画的文本，`paint()` 里自己按 `SE_ItemViewItemText` 画两行）；
徽章数据放在自定义的 `BADGES_ROLE` 角色里，列表项宽度跟视口、关掉横向滚动条，徽章不会被挤出可视区；「全部贡献」下**所有插件都会出现**（包括纯库插件）。
详情区标题下并排三枚徽章（类型 / 来源 / 状态），随后是版本 / 创建者 / 状态 / 贡献（按扩展点分组）与清单数据摘要，
再分两栏列出「协议与接口」（贡献 / 依赖插件 / 扩展接口 / 提供库 / 适用管理器版本 / 入口文件）与「清单与选项」（清单数据 / 插件选项摘要 / 清单路径），
不再是一条把八件事用 `·` 串起来的长句；自检 `plugin_display` 校验代理类型、每行的徽章数据与详情徽章的内容，并断言每行有标题 / 副标题、行高够两行、文字不会压到右侧徽章上。
操作包括导入插件目录或 `.zip` 包（两种都可以一次选多个，批量导入；选目录时按住 Ctrl / Shift 多选，装完给一份「导入成功 / 没导入」的汇总，已经装过的插件最后一次性问要不要覆盖；解压时拒绝 `..` 与绝对路径）、导出插件（把勾选的插件打包成 `.zip`，详见本节末尾）、启用 / 禁用、改显示名 / 说明 / 备注、打开插件目录（内置插件同样可以打开）、
「插件选项」配置、删除外部插件；启用状态 / 备注 / 插件选项都存 `.configs/plugins.json`（`{"version": 1, "plugins": {...}}`）。内置插件不能删除；
每次启用 / 禁用（含批量）都会在控制台留一行 `插件已启用：<id>（<名称>）` / `插件已禁用：<id>（<名称>）`，被拒绝时也留一行原因，
界面同时给出红色提示，不会误报成功（见「插件冲突」）；
清单里声明了 `provides` 却没有实际注册（或反过来）时，程序只在该插件的备注里写一条「清单声明的扩展接口没有注册：`<名字>`」提示，
不算载入失败，补上注册重新载入后会自动清掉；
载入失败的插件在列表里标为「异常」并强制禁用，不影响程序启动。启动时控制台按「启动 1/8 … 8/8」报告阶段（日志系统 / 缩放 / 会话 / 隐私 / 数据库 / 界面框架 / 插件系统 / 主窗口），插件部分先打印「插件扫描完成：发现 N 个（启用 X、未启用 Y、不可用 Z）」，紧跟着每个不可用插件一条 `warning`「插件不可用：<id>（[阶段] 原因）」，载入完成后打印汇总「插件载入：共 N 个（已启用 X、未启用 Y）；库插件 …、功能插件 …」并在句尾列出不可用插件的 id 与原因，每个载入成功的插件各一条「已载入」、失败的逐条 `warning`；自检 `plugin_load_summary` 与 `startup_stage_logs` 会校验这套输出。
列表项可以勾选，选择条上是一个三态全选框（空 = 全不选、横杠 = 部分选中、勾 = 全选当前列出的插件，点一下在全选 / 全不选之间切换），
勾选后可以「批量启用 / 批量禁用 / 批量删除」
（已处于目标状态的插件会被跳过，内置插件不可删除，删除前有确认框），也可以「导出插件」：
勾选几个就导出几个（一个没勾就用当前选中的那一行），单个插件打成一个 `<id>.zip`，
多个插件各自打成一个 `<id>.zip` 再整体套一层总包（总包名形如 `插件导出-20261007-205508.zip`），
默认导出到设置里的「默认导出目录」，保存框里也能临时改到别处；单插件的包可以直接用「导入插件包」装回来
（多个插件的总包里，每个 `<id>.zip` 也各是一个能直接导入的包），
`__pycache__`、`.pyc` 这类缓存不会被打进包里，导出不改变任何插件状态（任何用户都能导出）；右侧的功能按钮改用
`AdaptiveFlowLayout`（`needAni=False`、`isTight=True`，按钮最小宽 96 px），窄窗口下自动换行，不再被裁掉。

### 扩展点、事件与插件页面

插件除了提供查看器与扩展接口，还能往界面上加东西。程序定义了 11 个扩展点（`src/app/sdk/points.py` 的 `ExtensionPoint`）：
概览卡片（`app.ui.home.kpi`）、设置卡片（`app.ui.settings.card`）、数据管理工具栏（`app.ui.manage.toolbar`）、条目右键菜单
（`app.ui.manage.item_menu`）、详情面板行（`app.ui.detail.panel`）、导入筛选器（`app.ui.import.filter`）、查看器（`app.viewer`）、
编辑器（`app.editor`）、页面（`app.ui.page`）；另有 `app.data.import.hook` 与 `app.item.open.resolver` 是**协议预留**、程序侧尚未接线。
界面上的扩展点由 `src/app/ui/framework/contributions.py` 统一读取，插件用 `ctx.contribute(扩展点, {...})` 贡献，
插件被禁用时贡献随之撤销；某个回调抛异常只记日志，不会影响页面。

程序另外广播 7 个事件（`src/app/sdk/points.py` 的 `Events`）：`item.imported`（导入或扫描登记成功）、`item.deleted`、
`user.changed`、`library.changed`（库文件夹迁移）、`theme.changed`、`plugin.enabled` / `plugin.disabled`。
插件用 `ctx.on(事件, 处理函数)` 订阅，处理函数必须写成 `def _on_x(self, **payload)`（载荷见
[扩展点与事件](SDK.md)）。

插件加页面用 `ctx.add_page(key, title, factory, icon=..., bottom=...)`：程序本体在 `src/main.py` 里用
`plugin_service.bootstrap("app.ui", AppUiApi())`（`src/app/core/plugins/app_ui.py`）把 `app.ui` 接口登记进 `extension_registry`，
插件登记后主窗口（`src/app/ui/main_window.py` 的 `_sync_plugin_pages()`）会按 `PageSpec.route`（`plugin.<key>`）装配 / 移除导航项与堆叠页，
页面工厂抛异常时退化成一条提示页而不影响其它页面；`load()` 每次重载都会重新提供 `app.ui` 并调用它的 `sync_plugins(已载入插件 id)`，
因此**插件的页面会随启用 / 禁用自动出现与消失**。左侧导航的顺序是固定的：内置页面按内置顺序（设置恒在最下面），
插件页面按载入顺序追加，最多显示 7 个（`src/app/ui/main_window.py` 的 `PLUGIN_SIDEBAR_LIMIT`），追加不下的只出现在内置的
**「页面管理」页**（`src/app/ui/pages/workbench_page.py`）里 —— 那一页每行都**点整行（或行尾的「打开」）直接进入**。`app.ui` 之外，插件还可以
`ctx.provide(name, provider)` 暴露自己的扩展接口供其它插件 `ctx.require(name)` 使用。

**写插件的完整说明（最小插件、SDK 全部接口、库插件、目录规范、排错）见仓库根目录的 [PLUGIN.md](PLUGIN.md)；
清单字段与错误码见 [PLUGIN_PROTOCOL.md](PLUGIN_PROTOCOL.md)；扩展点与事件见
[SDK.md](SDK.md)。**

## 日志与保留策略

`src/app/core/runtime/logging_setup.py` 按 `Log/Mode` 决定文件名与切分方式：`single`（恒为 `app.log`）、`session`（每次启动 `app-<时间戳>.log`）、
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
