# 待办与进度

## 已完成（本轮重构）

- [x] 工程分层：`core` / `db` / `repositories` / `services` / `ui`，UI 不再直接查库
- [x] 数据导入：文本录入、文件与文件夹（递归）批量导入、重复策略、按时间命名、自动封面
- [x] 数据管理：列表 / 卡片视图、搜索、类型 / 标签 / 分类筛选、排序、多选、右键菜单、
      打开文件、在文件夹中显示、复制路径、详情查看、批量编辑 / 加标签 / 隐藏 / 删除 / 导出
- [x] 数据分类：无限层级分类树，新建 / 重命名 / 移动 / 删除（子分类上移或整体删除）
- [x] 数据标签：标签库（重命名 / 合并 / 清理未使用）+ 自由关键词
- [x] 回收站：软删除、还原、彻底删除（同时释放无引用的仓库文件）
- [x] 数据存档：**内容寻址（SHA-256）替代 git**，对图片 / 视频等任意二进制同样有效；
      历史对比、按条目还原、孤儿清理、按数量裁剪
- [x] 数据特征：校验和、MIME、图片尺寸、感知哈希（dHash）、文本统计
- [x] 数据导出：导出文件并生成 CSV / JSON 清单
- [x] 用户与口令：默认用户（管理员）、每用户口令 + PBKDF2-HMAC-SHA256（20 万次迭代），口令用于切换用户与解锁隐藏数据
- [x] 设置页：主题、云母特效、导入策略、库文件夹、日志（文件模式与保留策略）、日志级别、恢复初始化（重置后自动重启）
- [x] 库文件夹：单一库文件夹（取消多库），内有「全局」文件夹（内容仓库 / 封面 / 备份 / 元数据）与每用户一个用户名文件夹；文件按「用户名 / 分类目录」真实存放，手动"扫描并登记"，可在设置页更改位置 / 重建结构 / 打开
- [x] 重复内容检测与清理：按校验和分组，每组默认保留最新、可批量清理
- [x] 多用户与数据隔离：每个用户独立的数据项与分类，可设口令、切换用户；第一个用户为默认用户（管理员），只有它能管理其他用户且不可删除；删除用户时其分类以「用户名」为一级分类镜像到默认用户下，文件整目录搬进 `<库>/<默认用户>/<用户名>/`，同名标签合并，存档条目一并转移
- [x] 标签按用户隔离：新建用户自动获得默认分类与标签，删除用户时标签移交并合并同名标签
- [x] 全文检索：SQLite FTS5（trigram 分词，支持中文子串与多词 AND），触发器实时同步，可重建索引
- [x] 数据库结构版本检查：结构变化时先把旧库备份为 `resources/data.db.bak-<时间戳>` 再重建空库
- [x] 存档自动清理策略配置化：可选按数量 / 容量 / 时间 / 关闭（设置 → 存储），创建存档时自动执行；超限时从最早的快照开始删除并始终保留最新一份，同时释放不再被引用的内容；存档页提供「按策略清理」按钮
- [x] 库文件夹目录监听：`QFileSystemWatcher` 递归监听唯一库目录（含各用户名文件夹，去抖 1.5 秒），外部新增 / 删除时提示到「设置 → 库文件夹」执行「扫描并登记」；内部写入会被自动忽略
- [x] 大数量分页与缩略图异步加载：分页控件（每页 50 / 100 / 200 / 500 条，首页 / 末页 / 页码跳转，跨页多选保持），封面缩略图在 `QThreadPool` 工作线程解码并做 LRU 缓存（上限 400）
- [x] 框架版本核实：本地源码与 `.venv` 内的 PyQt6-Fluent-Widgets 1.11.3 完全一致、官方 latest 亦为 1.11.3，无升级项
- [x] 按最新框架示例对齐组件与写法：首页统计卡与数据管理卡片视图改用 `AdaptiveFlowLayout`（按宽度自适应分列）、长耗时操作用 `StateToolTip` 显示进行中状态、「重复项」按钮用工具提示给出重复组数量（导航蓝色 `InfoBadge` 角标后来按用户反馈移除）、图标按钮加 `ToolTipFilter`
- [x] 打包改用 PyAppify（`../pyappify.yml` + `icons/` + `.github/workflows/build.yml`）：推送 `v*` 标签自动构建在线安装包与整包，用户端启动器按标签增量更新；运行期数据（`config/`、`resources/`、`logs/`）全部移出代码目录，避免拉取代码时冲突
- [x] 清理打包尝试残留（`build/`、`dist/`、`_tools/`、`_wheels/`、`_tmp/`、`scripts/build_exe.py`），并在 `../README.md` / `HELP.md` 记录 PyAppify 流程
- [x] `../requirements.txt`、`README.md`、开发自检脚本（`scripts/dev_check_*.py`、`scripts/dev_reset.py`）
- [x] 示例数据注入脚本 `../scripts/seed_demo.py`：先清空运行期数据，再向真实环境注入 100 余条多类型示例数据（图片 / 视频 / 音频 / 文档 / 表格 / 演示 / 压缩包 / 代码 / 各类文本，含嵌套目录、隐藏项、回收站项、重复内容与一个存档快照），并按类型归入分类树与标签；额外建 3 个测试用户，各自带分类、标签与数据。导入后 **不自动删除**，便于直接上手体验；原始文件留在 `resources/demo/`
- [x] 设置页「维护 → 恢复初始化」：一键清空全部数据并把所有设置重置为默认值；实现集中在 `../src/app/services/maintenance.py`，设置页与 `scripts/dev_reset.py` 共用
- [x] 修复用户反馈的两个界面接线问题：数据管理页右侧筛选栏的分类勾选此前被忽略（`manage_page._load_items()` 只看左侧分类树），首页统计卡首屏不刷新（`home_page` 构造时未调用 `refresh()`，四张卡恒为 0）；`../scripts/dev_check_ui.py` 增加 `home_stats` / `category_filter` 两个回归检查
- [x] 修复用户反馈的三个导入页问题：①分类下拉重复（`import_page._reload_categories()` 调 `TaxonomyService.tree()` 时未传 `user_id`，取到了全部用户的同名分类；数据管理页编辑对话框的分类下拉同样漏传，一并修正）；②标签改为可从已有标签中多选（新增 `../src/app/ui/widgets/tag_picker.py` 的 `TagPicker`，导入页与编辑对话框共用，标签列表按当前用户加载并在标签 / 用户变化时刷新）；③关键词回车后「没反应」（标签块其实已加入，但 `FlowLayout` 所在容器高度为 0 被压扁；`keyword_input.py` 重写为 `ChipArea` 按宽度锁高 + `KeywordChip`，并在布局请求 / 尺寸变化 / 显示时重算）；`scripts/dev_check_ui.py` 增加 `import_categories` / `tag_picker` 两个回归检查
- [x] 数据管理页关键词：筛选面板新增「关键词」多选分区（可与其他条件叠加，语义为全部命中），列表行与卡片显示数据项的关键词（`#标签` 与关键词两组 chip）；`../scripts/dev_check_ui.py` 增加 `keyword_filter` / `keyword_display` 回归检查
- [x] 修复导入页标签 / 关键词胶囊重叠：`qfluentwidgets` 的 `FlowLayout` 只在容器几何变化时重排，新增 chip 不改变容器尺寸导致新 chip 停在默认几何压住第一个 chip；`../src/app/ui/widgets/keyword_input.py` 的 `ChipArea.sync_height()` 末尾补 `setGeometry()` 强制重排
- [x] 标签体系重构：标签分全局 / 个人（`Tag.is_global` + `Tag.created_by`），全局标签所有用户可用，个人标签仅创建者可见且不得与全局标签重名，只有创建者能切换归属；新增「标签」页（`../src/app/ui/pages/tag_page.py`）管理新建、改归属、重命名、删除与清理未使用；`TagRepository._scope()` / `by_name()` / `set_global()` 与 `TaxonomyService.set_tag_global()` / `cleanup_unused()` 承载规则；数据库 `SCHEMA_VERSION` 升到 3 并改为原地补列升级（历史 `user_id IS NULL` 视为全局，重名全局标签去重后建唯一索引），只有降级运行才备份重建；`scripts/seed_demo.py` 的示例数据补上每条 2~3 个标签 / 关键词与「年度归档」「公共素材」两个全局标签
- [x] 库目录重构（按用户反馈）： **取消多库设计**，只保留唯一的库文件夹；库内固定为「全局」（含 `store/` 内容仓库、`covers/` 封面、`backups/` 数据库备份、`.datamanager/` 元数据）与「<用户名>」（该用户的数据与分类目录）；文件路径整体从「分类目录/文件」变为「用户名/分类目录/文件」，因此各用户的分类可以不同；导入页与编辑对话框去掉「所在库」选择，设置页改为唯一「库文件夹」卡片（更改位置 / 扫描并登记 / 重建目录结构 / 打开文件夹）；用户改名同步重命名其文件夹（`LibraryService.rename_user_dir()`）。旧布局启动时自动迁移一次（`../src/app/services/layout_migration.py`）：先备份数据库到 `<库>/全局/backups/data-before-layout-v2-<时间戳>.db.bak`，再搬文件并改写记录，最后写布局标记 `<库>/全局/.datamanager/layout-2.json`（幂等）；真实库已验证 106 项全部迁入用户名文件夹、无残留
- [x] 全局标签在界面上的标识：筛选面板与标签选择器对全局标签显示「（全局）」后缀，取值仍是纯标签名（`filter_panel.set_options(global_tags=…)` / `TagPicker(global_tags=…)`）；`../scripts/dev_check_ui.py` 增加 `single_library` / `no_library_picker` / `global_tag_marks` 三个回归检查（当时共 11 项）
- [x] 存档归属用户与权限（按用户反馈）：存档条目新增 `user_id` / `user_name` 列记录所属用户，还原时按「传入用户 → 条目所属用户 → 当前用户」决定归属，并按条目记录的「分类 / 路径」在目标用户下逐级重建分类；普通用户只能看到 / 还原自己的条目，默认用户（管理员）可看全部并使用「还原整个存档」；存档页表格新增「所属用户」列；`SCHEMA_VERSION` 升到 4，旧库原地补列并从 `items` 回填归属
- [x] 用户管理独立成「用户」页（`../src/app/ui/pages/user_page.py`）：从设置页迁出全部用户卡片；默认用户可新建 / 重命名 / 删除其他用户并改其口令，普通用户只能改自己（重命名、改口令），默认用户不可删除；改自己的口令需先验证当前口令
- [x] 启动窗口居中：`MainWindow.showEvent()` 首次显示时把窗口移到当前屏幕中央（`_centered` 只执行一次）
- [x] 删除无用的「数据仓库」配置：配置项 `storePath` 由 `libraryPath` 取代，内容仓库固定为 `<库>/全局/store`（`config.store_dir()`）；设置页移除该卡片，存储组只保留库文件夹与存档自动清理
- [x] 日志输出模式与保留策略配置化（`../src/app/core/logging_setup.py` 重写）：可选单文件追加 / 每次启动一个文件 / 每天一个文件 / 按大小切分，按「最多保留文件数 / 单个文件大小上限 / 保留天数 / 总量上限」清理（最新文件永不删除，daily 模式按天数，其余按数量与总量）；设置页新增模式下拉与四张数值卡，按模式自动启停不适用的项
- [x] 恢复初始化后自动重启：`../src/app/ui/common.py` 新增 `restart_application()`（`QProcess.startDetached(sys.executable, sys.argv)` 后退出当前进程），设置页重置完成后调用
- [x] `../scripts/dev_check_ui.py` 增加 `user_page` / `archive_owner` / `settings_extras` 三项回归检查（当时共 14 项、页面 7 个，现为 16 项）；新增 `tests/test_user_admin.py`（默认用户标记、默认用户不可删除、删除成员用户的分类 / 文件 / 存档并入、改名搬目录）与 `tests/test_archive_owner.py`（条目归属、可见范围、按条目还原回所属目录、整档还原计数、按用户对比），当时用例总数 141
- [x] 自检脚本与重构后的架构对齐：`../scripts/dev_check_flow.py` 三处过时检查改为 `window.user_page.refresh()`（并补默认用户不可删除 / `is_admin` 断言）、`handler = lambda: seen.append(1)`（`LibraryWatcher.changed` 现为无参信号）、关键词 chip 改用 `KeywordChip` + `chips_height()`（原先取 `field._flow`，该属性随 `ChipArea` 重构消失）；顺带清掉 8 处失效 import（`items.py` 的 `cast`/`String`、`layout_migration.py` 的 `backup_dir`、`user_service.py` 的 `Path`、`home_page.py` 的 `DATA_TYPE_NAMES`/`DataType`、`import_page.py` 的 `SubtitleLabel`、`manage_page.py` 的 `FlowLayout`、`drop_area.py` 的 `QHBoxLayout`、`item_card.py` 的 `FluentIcon`），`src` 内未使用 import 归零；四个自检脚本（`dev_check.py` / `dev_check_services.py` / `dev_check_flow.py` / `dev_check_ui.py`）全部 EXIT=0 且 `RESULT failures=0`，单元测试 141 项 OK，`compileall -q src` 通过
- [x] 单元测试（``，147 个用例）：`tests/dataset.py` 生成多样化语料（真实图片 / 伪造魔数的视频音频 / 压缩包 / 办公文档 / 各类文本 / 嵌套与隐藏文件），
  `tests/harness.py` 把数据库与运行期目录隔离到 `tests/_tmp/`；覆盖导入、检索筛选、统计、编辑与标签、删除与回收站、分类树、标签库、存档、
  库文件夹与扫描、用户与隐私、导出与特征。顺带修出 8 个真问题（文本导入不读正文、存档与回收站口径不一致、彻底删除误删存档引用的内容、
  导出取源文件路径错误、中文关键词筛选与检索失效、删除分类会连子分类及其数据项一起删、新建子分类在同一会话内对分类树不可见、
  标签合并后会话内仍显示源标签）；另有 `tests/test_maintenance.py` 覆盖「恢复初始化」的设置重置与数据清空，
   `tests/test_layout_migration.py` 覆盖旧布局 → 单库 + 用户名文件夹的自动迁移、幂等、迁移前备份（含 WAL 内未落盘的提交）与「已是新布局但缺标记时不得重复套用户名目录」，
   `tests/test_tag_scope.py` 覆盖全局 / 个人标签的可见性、创建者权限、重名与清理规则，
   `tests/test_schema_upgrade.py` 覆盖旧版库原地升级、重名全局标签去重与升级幂等
- [x] 按用户反馈修复八处问题：①标签管理页对默认用户显示全部标签，其他用户只显示全局标签与自己创建的标签（`TagRepository._scope()` 去掉 `Tag.user_id IS NULL` 这条会把历史标签泄漏给所有人的分支，并补上 `created_by` 判定）；②删除用户时数据与标签一律并入默认用户，标签的 `created_by` 一并转移（`UserService.delete()` 不再默认挑"第一个其他用户"）；③存档只还原与当前数据不一致的条目：新增 `ArchiveService.entry_state()`（same / changed / removed / missing），一致条目直接跳过，存档页新增「状态」列并给出提示；④数据管理页「还原」按钮只在选中项含回收站中的数据时可用（`_update_count_label()` 按选中项启用，`_on_restore()` 过滤 `is_deleted`）；⑤重复内容检测按用户区分（`ItemService.duplicate_map(user_id)`，默认用户查全部，其他用户只查自己）；⑥删除设置页的隐私口令分组，口令改为每用户口令并入「用户」页：非默认用户可以改自己的口令并删除自己，默认用户可以管理所有用户，隐藏数据解锁改用当前用户口令，散列工具由 `services/privacy_service.py` 移到 `core/security.py`（`PrivacyService` 已删除）；⑦概览页「最近导入」条目可点击，经 `signalBus.focusItem` 跳转到数据管理页并选中该项、自动展开其分类；⑧移除「数据管理」导航项右上角会遮住图标的蓝色 `InfoBadge` 角标，重复组数量改在「重复项」按钮的工具提示中显示。`../scripts/dev_check_ui.py` 重写 `tag_page` 权限断言并新增 `recent_focus` 检查（当时共 14 项），`dev_check_services.py` / `dev_check_flow.py` 的隐私检查改用 `UserService` 口令，单元测试增至 147 项
- [x] 按用户反馈优化两处交互：①「用户」页为已设口令的用户新增「清除口令」按钮（`UserPage._clear_password()`）——默认用户可以清除任意用户的口令，其他用户只能清除自己的，越权或该用户没有口令时给出提示；②数据管理页：上方工具栏改用 `FlowLayout` 流式排布并放进 `_ToolbarView`（`QScrollArea`）容器，宽度不足自动换行、最多 `TOOLBAR_MAX_ROWS`（2）行（`_fit_toolbar()` 按 `flow.heightForWidth(viewport 宽度)` 计算高度，`resizeEvent` 中重算），再多则纵向滚动；右侧筛选面板每个分组改为 `FilterSection`：箭头折叠 + 选项区固定高 `SECTION_BODY_HEIGHT`（116 px）内部滚动、标题右侧搜索框按显示名过滤（无匹配时显示「没有匹配的选项」）、三态全选框（空 / 横杠 / 勾）经 `_sync_all()` / `_on_all_state()` 与分组内勾选双向同步（`_syncing` 守卫避免信号回环），顺带修掉旧实现里标签项重建会丢失勾选状态的问题；`../scripts/dev_check_ui.py` 新增 `user_password_clear` / `filter_sections` 检查（现共 16 项）
- [x] 数据安全与迁移健壮性：①`../src/app/db/database.py` 新增 `backup_database_file()`，改用 SQLite 在线备份接口复制数据库（库以 WAL 模式运行，原先直接 `shutil.copy2` 会漏掉尚未落盘的提交），降级重建与迁移前备份都改用它；②`src/app/services/layout_migration.py` 的「已迁移」判断修正为按首段比较（`legacy_rel.parts[:1] == (owner,)`）——原先的 `source == root / owner / legacy_rel` 对已是新布局的数据项永不成立，只要布局标记缺失（例如重新注入数据后）就会把每条路径再套一层用户名目录，迁移前先 `session.commit()` 让备份包含启动时 seed 的数据；③新增 `scripts/dev_check_guard.py`：`dev_check.py` / `dev_check_services.py` / `dev_check_flow.py` 运行前把真实数据库备份到 `logs/_selfcheck-data.db.bak`，跑完用 SQLite 在线备份写回并 `wal_checkpoint(TRUNCATE)`，自检不再清空用户数据；真实环境（106 项 / 4 用户 / 1 存档）已验证重跑三个自检脚本后数据完好

## 后续可做

- [ ] 发布第一个版本：提交当前重构改动 → 打 `v1.0.0` 标签 → 推送，让 CI 产出安装包（用户已决定**暂不发布**；前提是 `taishoubuzhi/D-MyDataManager` 对未登录用户可见——目前匿名访问返回 404）
- [ ] 在 PyCharm 中实测 GUI 并收集问题反馈
