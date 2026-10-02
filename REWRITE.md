# 项目重写方案（TODO.md 任务 1）

> 状态：Phase 0–6 全部完成。`src/app/ui/{framework,components,pages}` 就位，旧 `common.py`、`widgets/` 与旧门禁脚本 `scripts/dev_check*.py` 已删除；`scripts/selfcheck.py` 四层（`data` 4 / `services` 18 / `pages` 22 / `flows` 3）共 47 项检查全绿（末行 `RESULT failures=0`），`src\main.py --self-check` 在源码仓库里等价于全量自检，`compileall`（本机 3.14 与打包目标 3.13 都通过）、`unittest` 50 项、旧套件在删除前也保持可用。移植期与收尾期修掉四个产品缺陷并各配回归检查：①`CategoryRepository` 同级重名在归属不同时误判「无重名」，触发 `UNIQUE constraint failed: categories.parent_id, categories.name` 使整档还原崩溃（`category_shared_conflicts`）；②导入页切换目标用户后分类下拉回落到第一个根分类而不是「未分类」（`import_page_scope` 新增断言）；③插件列表为空时非管理员仍能点到「启用 / 停用插件」「插件更多选项」「删除插件」（`superuser_permissions` 新增断言）；④滚动区只清视口时自身仍按调色板实绘底色，浅色主题下三个滚动页与用户页卡片列表会露出上一个主题的深色底，改按 qfluentwidgets `ScrollArea.enableTransparentBackground()`的写法把滚动区自身与内层容器设为透明（`scroll_backgrounds`）；另按目录树导入时跳过库元数据目录 `.datamanager`（`import_tree_skips_meta`）。旧 34 项门禁的逐项去向见第 7.1 节，打包（PyAppify）验证结论见第 7.2 节，旧实现只读快照见第 9 节。第 4、6 节细节见 `logs/_rewrite/spec/{A_core_db_repo,B_services,C_ui_plugins_checks}.md`（只读参考）。
> 参考仓库：`PyQt-Fluent-Widgets/`（库源码 1.11.3，与 `requirements.txt` 里 `PyQt6-Fluent-Widgets==1.11.3` 同版本），参考其 `docs/source/*` 与 `examples/gallery` 的工程组织。

## 1. 目标与范围

- **目标**：从零重写 D-MyDataManager，以 PyQt-Fluent-Widgets 的 GUI 架构为基准，消除现存页面之间"风格 / 结构 / 交互不一致"的问题；功能与真实数据保持兼容。
- **范围内**：`src/` 全部（core / data / repositories / services / ui / 插件宿主 / 入口）、`scripts/`（自检与开发脚本）、`tests/`（单测套件）、随代码的文档（`README.md` / `HELP.md` / `PLUGIN.md`）。
- **范围外（用户明确要求先不做）**：TODO 任务 2（插件系统重构）、任务 3（存档增量 / 压缩）、任务 4（远程交互）。
  - 因此**插件协议与现有插件冻结**：`plugins/` 下 11 个内置插件必须在新架构下继续可用，协议字段、`plugin.py` 约定、插件使用的 `app.*` 导入路径都要保持（或提供兼容层）。
- **验收**：另写一套新的自检套件（见第 7 节）；旧套件（`scripts/dev_check*.py`）已在新套件功能对等后删除（只读快照见第 9 节）。
- **红线（不可破坏）**：
  1. 真实数据 `.resources/data.db`（含正文 FTS）与 `.resources/library/**` 的文件布局；
  2. `config/config.json`、`config/open_with.json`、`config/plugins.json` 的键与语义；
  3. `plugins/*/plugin.json` + `plugin.py` 的协议；
  4. Windows 下的沙箱 / ACL 行为（资源保护、隐藏文件开关）；
  5. `src/main.py --self-check` 这类既有入口语义要保留（可重构实现）。

## 2. 架构基准（来自 examples/gallery）

官方 gallery 示例的工程组织即"GUI 架构"的样板，新项目照此分层：

```
app/
  common/       # 与界面无关或全局共享的基础设施：config、paths、signal_bus、icons、resource、translator、style_sheet
  components/   # 可复用 UI 组件（卡片、区块、工具条、表格、分页、筛选、对话框…）
  view/         # 页面（interface）+ 主窗口
  resource/     # qss / i18n / images
```

关键惯例（必须遵守，否则页面必然再次走样）：

- 主窗口继承 `FluentWindow`；每个页面是一个 `QWidget` 子类，`self.setObjectName("xxxPage")`，用 `addSubInterface(page, icon, text, position)` 注册，导航路由 key = `objectName`。
- 页面统一用 `ScrollArea` + `setWidgetResizable(True)` + `setViewportMargins`/`enableTransparentBackground` 的官方写法，不要各自 `setStyleSheet` 手绘背景。
- 页面内的分组用 `SettingCardGroup` / `SettingCard`（设置类）或项目的 `section_card()`（内容类），间距与边距只允许来自 `common` 里的常量。
- 组件样式走 `StyleSheetBase` + `resource/qss/{light,dark}/<name>.qss`，按 `objectName` 选择；页面代码里不出现写死的颜色值。
- 主题 / 语言 / 字体交给 `qconfig`（库的全局配置）与 `SystemThemeListener`；项目自己的配置只存业务项，并在启动时把主题同步给 `qconfig`。
- 启动流程：`initWindow()`（splash screen、居中、`show()` + `processEvents()`）→ 建页面 → `connectSignalToSlot()` → `initNavigation()` → `splashScreen.finish()`。

## 3. 新目录布局

顶层包名保持不变（`app.core.viewer_data`、`app.core.plugin_kinds`、`app.ui.viewers.*` 是冻结的插件导入面，改路径等于改插件协议），重写的是包内部的代码组织。

```
src/
  main.py                    # 唯一入口：解析参数、bootstrap、--self-check
  app/
    bootstrap.py             # 启动装配：日志 → 配置 → 数据库 → 插件 → 信号 → UI
    core/                    # 基础设施（不含业务）：paths / config / logging_setup / signals / security / acl / shell / version
                             #   插件协议面（冻结）：plugins(host) / plugin_kinds / plugin_options / viewers / viewer_data / app_ui / extensions
    db/                      # SQLAlchemy：database(engine/session/迁移/一次性修复) / models / seed
    repositories/            # 纯数据访问：items / categories / tags / users / libraries / archives / base
    services/                # 业务：library / taxonomy / item / import / archive / blob / export / stats / user / privacy / open_with / plugin / feature / maintenance / layout_migration
    ui/
      framework/             # 页面基座：Page 基类、page_header、section_card、间距常量、EmptyState、StyleSheet、toast/confirm 包装
      components/            # 可复用控件：DataTable / Pager / FilterPanel / FlowArea / CategoryTree / ItemCard / KeywordInput / TagPicker…
      pages/                 # 9 个页面
      dialogs/               # 对话框
      viewers/               # 查看器（路径冻结：插件按 app.ui.viewers.<x> 导入）
      models/                # Qt item models
      main_window.py         # FluentWindow 主窗口 + 导航装配
    resource/                # qss / i18n / images
```

约定：`framework/` 不 import 任何具体页面；`pages/` 之间不互相 import；`components/` 不 import `pages/`、也不直接持有 service（只读查询例外，须注释说明）。

## 4. 分层与依赖方向

- 依赖方向单向：`view` → `services` → `repositories` → `data`；`common` 与 `core` 不依赖上层，`core.plugins`/`core.viewers` 只依赖 `common` + `services` 的抽象接口。
- 页面**不允许**直接持有 `Session`（除少数需要跨事务读取的只读查询，须有注释说明）；写操作一律经 service，`commit` 责任在 service 层。
- 跨层通信只用 `common/signals.py` 的 `signalBus`；service、repository、data 层不 import 任何 Qt UI 符号。
- 物理分层用目录 + `__init__.py` 导出面控制；禁止 `view` 内 `import` 其它页面的内部符号。

## 5. UI 一致性规约

每个页面必须满足（写成自检项，见第 7 节）：

1. **骨架**：`ScrollArea` 页面正文 → `page_header(page, title, subtitle)` → 若干个 `section_card(title, description)` 分组；不允许"裸"控件直接铺在页面上。
2. **间距**：只用 `common` 的 `PAGE_MARGINS` / `PAGE_SPACING` / `PANEL_MARGINS` / `DETAIL_MARGINS` / `CARD_SPACING`。
3. **组件**：按钮只用 qfluentwidgets 的 `PushButton`/`PrimaryPushButton`/`TransparentToolButton`；表格用统一的 `DataTable` 组件；分页用统一 `Pager`；空态用统一 `EmptyState`；提示统一 `toast_success/warning/error`；确认统一 `confirm()`；输入统一 `LineEdit`/`ComboBox`/`SearchLineEdit`。
4. **交互**：三态全选框、批量操作条、右键菜单、快捷键（F5 刷新、Delete 删除、Ctrl+A 全选…）在同类页面上语义一致；批量按钮的启用条件与提示文案遵循同一套规则（含"已全选顶层则禁用"这类既有语义）。
5. **权限**：默认用户 / 普通用户的界面权限统一由一处判定（`services.user.is_admin()` + 页面 `_apply_permissions()`），受限控件统一"禁用 + hint 卡片 + toast"三件套。
6. **异步**：耗时任务（导入、扫描、缩略图、封面加载）统一走 `components` 里的 worker 封装，禁止在页面里散写 `QThread`。

## 6. 兼容性清单（重写时逐条对齐）

*(Phase 0 規格清点完成后回填：数据表与列、config 键、路径常量、插件协议字段、必须保持的 app.* 导入符号。)*

## 7. 新自检套件

旧套件单文件 11 万字节、34 项检查，且直接读页面私有属性 —— 新套件改成**分层 + 面向公开契约**：

```
scripts/
  selfcheck.py            # 入口：--list / --layer <层> / --only <name> / --json / --keep-db / --verbose
  selfcheck/
    __init__.py
    cli.py                # 参数解析与退出码（未知检查名 → 参数错误 exit 2）
    harness.py            # QApplication 生命周期、offscreen、临时库与配置重定向、断言收集、分层模块表
    fixtures.py           # 种子数据（用户 / 分类树 / 标签 / 数据项 / 存档 / 插件）
    checks_data.py        # 数据层与迁移（schema、FTS、seed、一次性修复）
    checks_services.py    # 服务层业务规则（权限、迁移、标签合并、存档、导入导出、隐私）
    checks_pages.py       # 页面骨架与统一样式（page_shells / page_navigation / pages_style_guard /
                          #   style_uniformity / theme_background / home_kpis）
    checks_manage_ui.py   # 数据管理 · 导入页行为（分类过滤、筛选面板、批量选择、导入范围、标签选择器）
    checks_tags_ui.py     # 标签 / 用户 / 设置页行为（三态、全局标签、权限态、隐私分组、最近访问）
    checks_archive_ui.py  # 存档 / 打开方式 / 插件页与查看器（分栏、表格、详情、页面注入、图片查看器）
    checks_flows.py       # 端到端流程（导入 → 过滤 → 批量移动 → 存档 → 恢复）
tests/                    # 单元测试：按主题拆 test_<主题>.py + IsolatedCase
```

分层由 `harness.py` 的 `MODULES` 决定：`data → checks_data`、`services → checks_services`、
`pages → checks_pages + checks_manage_ui + checks_tags_ui + checks_archive_ui`、`flows → checks_flows`；
模块文件不存在时跳过，便于分人并行移植。

规则：
- 检查只使用**公开契约**（页面暴露的属性、service API、信号），需要窥探内部时改用 `objectName` / 组件查找；
- 每个检查自己建临时库（`IsolatedCase` 思路），结束时还原现场；不依赖真实 `.resources/data.db`；
- 输出格式：`<name>: ok|FAIL <reason>`，结尾 `RESULT failures=N`，退出码 0/1；
- 迁移期按下表逐项登记「新检查名 ← 旧检查名」，全部迁移完成后再删旧套件。

### 7.1 旧门禁 → 新检查映射

| 旧检查（`scripts/dev_check_ui.py` 34 项，已删除） | 新检查 | 模块 |
| --- | --- | --- |
| `home_stats` L139 | `home_kpis` | `checks_pages.py` |
| `style_uniformity` L53 | `style_uniformity` | `checks_pages.py` |
| `theme_background` L101 | `theme_background` | `checks_pages.py` |
| `category_filter` L158 / `uncategorized_fixed` L403 / `filter_sections` L2282 | `manage_category_filter` | `checks_manage_ui.py` |
| `manage_selection` L459 | `manage_selection` | `checks_manage_ui.py` |
| `import_categories` L557 / `import_user_scope` L590 / `import_batch` L1049 / `single_library` L1236 / `no_library_picker` L1275 | `import_page_scope` | `checks_manage_ui.py` |
| `tag_picker` L646 / `keyword_filter` L679 / `keyword_display` L708 | `tag_picker_keywords` | `checks_manage_ui.py` |
| `tag_page` L725 / `tag_filters` L1126 | `tag_page_rows` | `checks_tags_ui.py` |
| `global_tag_marks` L1310 | `global_tag_marks` | `checks_tags_ui.py` |
| `user_page` L1347 / `user_password_clear` L2229 | `user_page_rows` | `checks_tags_ui.py` |
| `system_permissions` L875 | `superuser_permissions` | `checks_tags_ui.py` |
| `settings_extras` L2128 / `privacy_group` L2166 | `settings_privacy_group` | `checks_tags_ui.py` |
| `recent_focus` L1182 | `recent_focus` | `checks_tags_ui.py`（层 `flows`） |
| `archive_tabs` L1698 / `archive_owner` L1677 / `archive_pin` L1723 | `archive_tabs_owner_pin` | `checks_archive_ui.py` |
| `archive_table` L1782 | `archive_table` | `checks_archive_ui.py` |
| `open_with` L1840 | `open_with_page` | `checks_archive_ui.py` |
| `plugins` L1911 | `plugin_page_detail` | `checks_archive_ui.py` |
| `plugin_pages` L2030 | `plugin_injected_pages` | `checks_archive_ui.py` |
| `image_viewer` L2067 | `image_viewer` | `checks_archive_ui.py` |
| `tag_permissions` L791 | `tag_permissions` | `checks_services.py` |
| `user_delete_transfer` L1503 | `user_delete_transfer` | `checks_services.py` |

新套件另有旧套件没有的结构性检查：`page_shells`、`page_navigation`、`pages_style_guard`（页面骨架、
导航注册、源码里禁写死边距/样式），以及 `checks_flows.py` 的 `user_journey`、`pages_on_real_data`。

### 7.2 打包验证（PyAppify）

- 配置：`pyappify.yml`（应用名 / 图标 / 站点 + `profiles.release`：`git_url`、`main_script: "src/main.py"`、
  `requires_python: "3.13"`、`requirements: "requirements.txt"`）；`.github/workflows/build.yml` 在 `v*` 标签上由
  `ok-oldking/pyappify-action@master`（`version: v1.2.3`）构建，`softprops/action-gh-release@v2` 发布 `pyappify_dist/*`
  （标签含 `-` 视为 prerelease）。
- 依赖：`requirements.txt` = PyQt6 6.11.0 / PyQt6-Fluent-Widgets 1.11.3 / SQLAlchemy 2.0.52 / loguru 0.7.3 /
  Pillow 12.3.0；`pywin32 312` 由 `PyQt6-Frameless-Window`（PyQt6-Fluent-Widgets 的依赖）传递带入，打包环境自带。
- 路径规则：`src/app/core/paths.py:24-27` —— 冻结时 `ROOT = Path(sys.executable).resolve().parent`，源码时
  `_find_project_root()`（沿父目录找同时含 `src/` 与 `CLAUDE.md`/`TODO.md` 的一级，兜底 `parents[3]`）。探针验证冻结下
  `ROOT` 与 `DATA_DIR`/`CONFIG_DIR`/`LOG_DIR`/`PLUGIN_DIR`/`exports` 全落在可执行文件同级目录，`SRC_DIR`/`RESOURCE_DIR`
  指向随包 `src/`。
- 布局冒烟：把 `src/` + `plugins/` 拷到仓库外的临时目录（模拟解包布局：无 `scripts/`、无仓库标记文件），
  `python <dist>/src/main.py --self-check`（`QT_QPA_PLATFORM=offscreen`）→ 退出码 0：自建 `.resources/data.db`、
  `config/config.json`、`logs/`，写入默认数据（用户 + 5 分类 + 3 标签 + 库文件夹），完成库目录结构迁移，载入 11 个内置插件；
  事后真实仓库的 `.resources/data.db` 与 `config/*.json` 哈希不变。
- 版本兼容：打包目标 3.13 与开发 venv 3.14.5 分别对 `src scripts tests plugins` 跑 `compileall`，均 exit 0。
- 残留限制：真机 pyappify 构建（下载 Python 3.13 + 装依赖 + 冻结）只在 GitHub Actions 上执行，离线无法复现；进程内伪造
  `sys.frozen=True` 无法完成全量启动 —— pywin32 冻结模式拒绝从 site-packages 加载 `pywintypes`
  （`ImportError: Module 'pywintypes' isn't in frozen sys.path`），属仿真限制而非产品缺陷，故冻结分支只用路径探针 +
  无标记布局冒烟间接验证。

## 8. 阶段与里程碑

| Phase | 内容 | 完成判据 |
| --- | --- | --- |
| 0 | 规格清点（A/B/C）+ 本方案定稿 + 备份 | 三份 spec 落盘，本文件第 4/6 节回填 |
| 1 | `common` + `core` + `data` + `repositories` | 新代码能打开真实 `.resources/data.db`，读写与旧实现一致；数据层自检通过 |
| 2 | `services` | 服务层自检（权限、迁移、标签、存档、导入导出、隐私）通过 |
| 3 | UI 基座：主窗口 / 导航 / 页面骨架 / 公共组件 | 空壳窗口可跑，`components` 有自检（间距、组件类型、QSS） |
| 4 | 页面重写（home / manage / import / tag / user / archive / open_with / plugin / settings + viewers） | 每页功能对等 + 页面结构自检通过 |
| 5 | 插件宿主移植（协议冻结） | 11 个内置插件可载入、启用/禁用/页面注入正常 |
| 6 | 新自检套件收口 + 单测 + 打包（pyappify）+ 文档 + 删旧代码 | 新套件全绿、`--self-check` 通过、`HELP.md`/`README.md`/`PLUGIN.md` 与实现一致 |

## 9. 迁移与回滚

- 旧代码快照（只读参考，`.gitignore` 已忽略 `logs/`）：`logs/_rewrite/legacy_snapshot/{src,scripts,tests,plugins}`。
- 仓库自身 git 历史即回滚点；重写期间保持"每个 Phase 结束时 `compileall` + 该层自检通过"。
- 真实数据只在必要时（Phase 1 之后）用**只读**方式验证；任何写操作都在临时副本上进行。
