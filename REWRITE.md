# 项目重写方案（TODO.md 任务 1）

> 状态：Phase 0–6 全部完成。`src/app/ui/{framework,components,pages}` 就位，旧 `common.py`、`widgets/` 与旧门禁脚本 `scripts/dev_check*.py` 已删除；`scripts/selfcheck.py` 四层（`data` 4 / `services` 29 / `pages` 39 / `flows` 4）共 76 项检查全绿（末行 `RESULT failures=0`；18 / 22 → 26 / 26 与新增的插件检查来自后续的任务 2，见第 10 节；v5 又加 `plugin_stubs_current`、`provides_note_recorded`、`workbench_lists_builtin_pages`、`plugin_action_buttons_visible`，见第 11 节；分页可读性 / 布局偏好 /「简化显示」再加 `icon_text_buttons`、`layout_preferences`，见第 12 节；悬停提示与「说明不常显」再加 `hover_hints`、`prose_moved_to_hints`，见第 13 节；问号标识 / 数字配置输入框 / 方形图标按钮补内容区断言与插件页展示再加 `number_setting_cards`、`plugin_display`（并扩充 `icon_text_buttons`、`hover_hints`），见第 14 节；问号收敛到页面标题、标签图标化与配置切换提示再加 `hint_badges_on_titles`、`icon_text_labels`、`setting_change_toasts`，见第 15 节；按钮尺寸还原 / 标签内部对齐 / 插件行两行绘制 / 用户卡信息行扩充了 `icon_text_buttons`、`icon_text_labels`、`plugin_display` 并新增 `user_card_info`，见第 16 节；「简化显示」三挡化与用户卡流式操作区再加 `simple_modes`，见第 18 节），`src\main.py --self-check` 在源码仓库里等价于全量自检，`compileall`（本机 3.14 与打包目标 3.13 都通过）、`unittest` 50 项、旧套件在删除前也保持可用。移植期与收尾期修掉四个产品缺陷并各配回归检查：①`CategoryRepository` 同级重名在归属不同时误判「无重名」，触发 `UNIQUE constraint failed: categories.parent_id, categories.name` 使整档还原崩溃（`category_shared_conflicts`）；②导入页切换目标用户后分类下拉回落到第一个根分类而不是「未分类」（`import_page_scope` 新增断言）；③插件列表为空时非管理员仍能点到「启用 / 停用插件」「插件更多选项」「删除插件」（`superuser_permissions` 新增断言）；④滚动区只清视口时自身仍按调色板实绘底色，浅色主题下三个滚动页与用户页卡片列表会露出上一个主题的深色底，改按 qfluentwidgets `ScrollArea.enableTransparentBackground()`的写法把滚动区自身与内层容器设为透明（`scroll_backgrounds`）；另按目录树导入时跳过库元数据目录 `.datamanager`（`import_tree_skips_meta`）。旧 34 项门禁的逐项去向见第 7.1 节，打包（PyAppify）验证结论见第 7.2 节，旧实现只读快照见第 9 节。第 4、6 节细节见 `logs/_rewrite/spec/{A_core_db_repo,B_services,C_ui_plugins_checks}.md`（只读参考）。
> 参考仓库：`PyQt-Fluent-Widgets/`（库源码 1.11.3，与 `requirements.txt` 里 `PyQt6-Fluent-Widgets==1.11.3` 同版本），参考其 `docs/source/*` 与 `examples/gallery` 的工程组织。

## 1. 目标与范围

- **目标**：从零重写 D-MyDataManager，以 PyQt-Fluent-Widgets 的 GUI 架构为基准，消除现存页面之间"风格 / 结构 / 交互不一致"的问题；功能与真实数据保持兼容。
- **范围内**：`src/` 全部（core / data / repositories / services / ui / 插件宿主 / 入口）、`scripts/`（自检与开发脚本）、`tests/`（单测套件）、随代码的文档（`README.md` / `HELP.md` / `PLUGIN.md`）。
- **范围外（用户明确要求先不做）**：TODO 任务 2（插件系统重构）、任务 3（存档增量 / 压缩）、任务 4（远程交互）。
  - 因此**插件协议与现有插件冻结**：`plugins/` 下 11 个内置插件必须在新架构下继续可用，协议字段、`plugin.py` 约定、插件使用的 `app.*` 导入路径都要保持（或提供兼容层）。（**后续变化**：用户随后要求实施 TODO 任务 2，插件协议改为 v4 并重建内置插件，见第 10 节；第 5 节里标注「冻结」的插件协议面已随任务 2 调整。）
- **验收**：另写一套新的自检套件（见第 7 节）；旧套件（`scripts/dev_check*.py`）已在新套件功能对等后删除（只读快照见第 9 节）。
- **红线（不可破坏）**：
  1. 真实数据 `.resources/data.db`（含正文 FTS）与 `.resources/library/**` 的文件布局；
  2. `config/config.json`、`config/open_with.json`、`config/plugins.json` 的键与语义；
  3. `plugins/*/plugin.json` + `plugin.py` 的协议（重写期冻结；任务 2 起由 v4 协议取代，见第 10 节）；
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

顶层包名保持不变（重写期 `app.core.viewer_data`、`app.core.plugin_kinds`、`app.ui.viewers.*` 被当作冻结的插件导入面，改路径等于改插件协议），重写的是包内部的代码组织。（任务 2 取消了类型概念，`app.core.plugin_kinds` 已删除，扩展名表也从 `app.core.viewer_data` 下沉到插件 `data/viewer.json`；v5 进一步取消了「插件按 `app.ui.viewers.<x>` 导入」这条冻结面 —— 视图代码已搬进各插件，程序侧不再被插件依赖，见第 11 节。）

```
src/
  main.py                    # 唯一入口：解析参数、bootstrap、--self-check
  app/
    bootstrap.py             # 启动装配：日志 → 配置 → 数据库 → 插件 → 信号 → UI
    core/                    # 基础设施（不含业务）：paths / config / logging_setup / signals / security / acl / shell / version
                             #   插件协议面：plugin_core / plugin_options / viewers / app_ui / extensions
    db/                      # SQLAlchemy：database(engine/session/迁移/一次性修复) / models / seed
    repositories/            # 纯数据访问：items / categories / tags / users / libraries / archives / base
    services/                # 业务：library / taxonomy / item / import / archive / blob / export / stats / user / privacy / open_with / plugin / feature / maintenance / layout_migration
    ui/
      framework/             # 页面基座：Page 基类、page_header、section_card、间距常量、EmptyState、StyleSheet、toast/confirm 包装
      components/            # 可复用控件：DataTable / Pager / FilterPanel / FlowArea / CategoryTree / ItemCard / KeywordInput / TagPicker…
      pages/                 # 10 个页面（任务 2 新增「页面管理」）
      dialogs/               # 对话框
      viewers/               # 查看器调度：open_flow.py + window.py（只有调度，视图代码在各自插件目录里）
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
    checks_plugins.py     # 插件协议与服务（清单白名单、data 引用、导入边界、类契约、多库、载入报告、查看器扩展名、事件广播）
    checks_pages.py       # 页面骨架与统一样式（page_shells / page_navigation / pages_style_guard /
                          #   style_uniformity / theme_background / home_kpis）
    checks_manage_ui.py   # 数据管理 · 导入页行为（分类过滤、筛选面板、批量选择、导入范围、标签选择器）
    checks_tags_ui.py     # 标签 / 用户 / 设置页行为（三态、全局标签、权限态、隐私分组、最近访问）
    checks_archive_ui.py  # 存档 / 打开方式 / 插件页与查看器（分栏、表格、详情、页面注入、图片查看器）
    checks_contributions.py # 界面扩展点贡献与贡献生命周期（任务 2 新增）
    checks_navigation.py  # 导航工作台：固定 / 排序 / 恢复默认 / 插件页随启停出现消失（任务 2 新增）
    checks_flows.py       # 端到端流程（导入 → 过滤 → 批量移动 → 存档 → 恢复）
tests/                    # 单元测试：按主题拆 test_<主题>.py + IsolatedCase
```

分层由 `harness.py` 的 `MODULES` 决定：`data → checks_data`、`services → checks_services + checks_plugins`、
`pages → checks_pages + checks_manage_ui + checks_tags_ui + checks_archive_ui + checks_contributions + checks_navigation`、`flows → checks_flows`；
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
任务 2 之后又多了整组插件检查（`checks_plugins.py` 8 项、`checks_contributions.py` 2 项、`checks_navigation.py` 1 项），
其中 `plugin_page_detail` / `plugin_injected_pages` 也按 v4 协议调整过。

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
| 5 | 插件宿主移植（协议冻结） | 11 个内置插件可载入、启用/禁用/页面注入正常（任务 2 重建为 9 个内置插件 + 1 个示例插件） |
| 6 | 新自检套件收口 + 单测 + 打包（pyappify）+ 文档 + 删旧代码 | 新套件全绿、`--self-check` 通过、`HELP.md`/`README.md`/`PLUGIN.md` 与实现一致 |

## 9. 迁移与回滚

- 旧代码快照（只读参考，`.gitignore` 已忽略 `logs/`）：`logs/_rewrite/legacy_snapshot/{src,scripts,tests,plugins}`。
- 仓库自身 git 历史即回滚点；重写期间保持"每个 Phase 结束时 `compileall` + 该层自检通过"。
- 真实数据只在必要时（Phase 1 之后）用**只读**方式验证；任何写操作都在临时副本上进行。

## 10. 后续变化：TODO 任务 2（插件系统重构）

Phase 0–6 收尾之后，用户要求实施 TODO.md 任务 2，插件协议改成 v4，本方案里标注「冻结」的插件
部分随之调整（实施记录见 `logs/_rewrite/plugin_refactor_plan.md`）：

- **协议**：取消 `kind` / `kinds` 等类型字段（`src/app/core/plugin_kinds.py` 已删除），工具库插件就是库；
  插件用清单的 `libraries` 声明对外提供的库模块、用 `data` 声明数据文件，复用别的插件走
  `depends` + `from dm_plugin.<id>.<模块> import ...` 静态导入（`library()` 兜底）；字段与规则见
  `plugins/PLUGIN_PROTOCOL.md`，扩展点与事件见 `plugins/EXTENSION_POINTS.md`。
- **内置插件**：11 个 → 9 个（`builtin.lib.viewer` 查看器基类库 + `builtin.lib.dialog` 弹窗工具库 + 7 个查看器），
  扩展名表从 `src/app/core/viewer_data.py` 下沉到各插件的 `data/viewer.json`；另有示例插件
  `plugins/example.ui_extension`；每个插件目录都有自己的 `PLUGIN.md`。
- **界面**：8 个界面扩展点（概览卡片 / 设置卡片 / 工具栏按钮 / 条目菜单 / 详情行 / 导入筛选 / 打开方式 / 页面）
  与 7 个事件都有了真实消费方；新增内置页「页面管理」（只读列出全部页面，侧栏顺序固定：
  内置页按内置顺序、设置恒在最下面，插件页按载入顺序追加，追加不下的只在页面管理里打开）。
- **自检**：新增 `checks_plugins.py`（8 项）、`checks_contributions.py`（2 项）、`checks_navigation.py`（1 项），
  `plugin_page_detail` / `plugin_injected_pages` 也按 v4 调整；检查总数 47 → 59（v5 再加 `plugin_stubs_current`，见第 11 节）。

## 11. 后续变化：插件系统 v5（插件与程序彻底解耦）

任务 2 验收之后，用户又提了一组修复要求，逐条落在 v5 里（协议细节见 `plugins/PLUGIN_PROTOCOL.md`）：

- **协议补充**：清单新增 `enabled`（默认启用状态；内置插件可用它默认禁用自己，用户改动以 `config/plugins.json` 为准），
  示例插件 `example.ui_extension` 因此默认禁用；`item.imported` 收窄为「文件 / 目录 / 粘贴文本导入」，
  库扫描登记不再广播。
- **公开面收窄**：一个插件对其他插件的公开面只有入口 `plugin.py`，`libraries` 的 `module` 一律写 `plugin.py`
  （`builtin.lib.viewer`、`builtin.lib.dialog` 都已改），跨插件只允许 `from dm_plugin.<id>.plugin import ...`；
  自检 `plugin_imports` 收紧为「`app` 下只放行 `app.sdk`」，插件不再 import 程序 UI。
- **SDK 扩容**：新增 `app.sdk.data`（文本解码 / xlsx / csv / 压缩包成员与读取 / 图片信息，原 `app.core.viewer_data`
  整体搬入并删除该模块）与 `app.sdk.ui`（间距常量、`clear_scroll_background`、`open_default` / `reveal`）；
  程序侧改为从 SDK 取同一份实现（`ui/framework/tokens.py`、`ui/framework/theme.py`、`core/viewers.py`）。
- **查看器搬进插件**：`builtin.lib.viewer` 的库模块 `plugin.py` 提供 `ViewerPlugin` 基类、`ViewerWindow` 窗口外壳，
  另有 `viewer_window.py` / `media_panel.py` 实现文件；`builtin.lib.dialog` 继续提供弹窗外壳与 `dialog` 接口。
  7 个查看器的视图代码（约 800 行）从 `src/app/ui/viewers/` 搬进各自插件目录（`archive_view.py`、`image_view.py`、
  `markdown_view.py`、`sheet_view.py`、`text_view.py`，以及拆出来的 `audio_view.py` / `video_view.py`），
  `src/app/ui/viewers/` 只剩 `open_flow.py` 与瘦身后的 `window.py`（只做调度）。新登记的 `Viewer.opener` +
  `ctx.add_viewer(opener=...)` 让插件自己建窗口、自己经 `dialog` 弹出，程序侧不参与界面。
- **IDE 支持**：`scripts/plugin_stubs.py` 按插件清单生成 `stubs/dm_plugin/**` 的 `.pyi` 桩（`.idea/D-MyDataManager.iml`
  把 `stubs` 标成源码根），解决「`from dm_plugin` 在 PyCharm 里始终报错」。
- **界面**：撤销「侧栏可改」——删掉 `config/navigation.json`、`src/app/core/navigation.py`、`navigationChanged` 与
  页面的固定 / 排序接口；侧栏顺序固定（内置页按内置顺序、设置恒在最下面），插件页面按载入顺序追加，
  追加不下的只出现在只读的「页面管理」页里（原来每次应用后设置图标消失的问题随之一并消失）。
- **协议细化（任务 2 收尾之后）**：`api_version` 从「可选、默认 `>=1.0`」变成**必填**（缺字段、范围非法、当前 SDK 不满足
  都拒绝载入，`src/app/core/plugin_core.py:_check_api_version()`），10 个内置 / 示例插件清单统一写 `">=1.0 <2.0"`；
  插件页明细新增「适配 SDK」一行；`plugins/PLUGIN_PROTOCOL.md` 新增 2.7 节写清 `provides`（运行期对象实例）与
  `libraries`（可 import 的代码模块）的分工，自检 `plugin_manifest_whitelist` 增加「缺 `api_version` / 超出 SDK 范围」两条断言。
- **弹窗插件改名**：`plugins/builtin.dialog` → `plugins/builtin.lib.dialog`（定位为工具库，与 `builtin.lib.viewer` 对齐），
  仍以 `provides: dialog` + `ctx.require("dialog")` 供查看器使用。
- **自检**：新增 `plugin_stubs_current`（桩与清单一致），`plugin_imports` / `plugin_viewer_extensions` 按 v5 调整；
  总数 59 → 60。
- **控制台输出（任务 2 收尾之后）**：`src/main.py` 不再自己 `logger.info(loaded_summary(viewers))`；`app.sdk` 首次被导入时播报
  「SDK 已载入：版本 1.0」（`src/app/sdk/__init__.py` 的 `sdk_banner()`），`PluginService._load_one()` 每载入一个插件来一行
  「插件 `<id>` 已载入」，`load()` 末尾用重写后的 `loaded_summary()` 汇总成
  「插件载入：库插件 N 个（已启用 a、未启用 b）、功能插件 M 个（已启用 c、未启用 d）」——不再按扩展点统计打开方式 / 查看器数量。
- **`provides` 一致性提醒**：清单声明了 `provides` 但 `setup()` 没 `ctx.provide()` 时，`PluginService._note_unregistered_provides()`
  只往插件备注写 `清单声明的扩展接口没有注册：<名字>`（前缀常量 `PROVIDES_NOTE_PREFIX`），不算载入失败，补上注册后自动清掉；
  自检新增 `provides_note_recorded`（含「修好后清掉」分支）。
- **插件页按钮栏**：详情区下方的功能按钮栏去掉 `FlowArea(adaptive=True, minimum_width=96)`，改回按各按钮自己的
  `sizeHint()` 换行（`AdaptiveFlowLayout` 会把一行均分，窄窗口下「打开插件目录」等按钮被压到装不下文字）；
  自检新增 `plugin_action_buttons_visible`（量真实几何，旧写法下必失败）。
- **页面管理**：`MainWindow._init_navigation()` 末尾补一次 `self.workbench_page.refresh()`——`PageBase.auto_refresh()` 只连信号、
  不会立刻刷新，而插件在主窗口之前载入，导致没有插件页面时「页面管理」永远是空页；内置页面本来就都列在里面，
  自检新增 `workbench_lists_builtin_pages`。
- **自检总数**：63 → 65（`data` 4 / `services` 28 / `pages` 28 → 30 / `flows` 3），新增的两项见第 12 节。

## 12. 后续变化：分页可读性、布局偏好与「简化显示」

- **分页条能看出第几页**：`src/app/ui/components/pager.py` 的 `page_box` 原来被 `setFixedWidth(90)` 压到装不下页码，改成
  `setAlignment(AlignCenter)` + `setMinimumWidth(sizeHint().width())`；文案统一成 `page_prefix`「第」+ `/ 共 M 页`（`set_state()` 里更新），
  四个翻页按钮按 `max(sizeHint().width())` 等宽；`DEFAULT_PAGE_SIZE` 与新增的 `normalize_page_size()` 把每页条数收敛到 50 / 100 / 200 / 500。
- **布局偏好落盘**：新增配置组 `Layout`（`src/app/core/config.py`）——`Page-Size`、`Show-Category-Panel`、`Show-Filter-Panel`、
  `Expand-Categories`、`Expanded-Filters`、`Simple-Display`；数据管理页与存档页的每页条数都读 `Layout/Page-Size`（删掉写死的
  `ARCHIVE_PAGE_SIZE`），两栏显隐（`_save_panel_visibility()`）与筛选分组折叠（`_on_filter_collapsed()`）实时写回，
  「设置 → 外观」新增「每页条数」「分类栏默认展开」两张卡，`tests/harness.py reset_config()` 一并重置这些项。
- **分类栏与筛选栏默认收起**：`components/category_tree.py` 的 `set_nodes()` 不再无条件 `setExpanded(True)`，改由 `_wants_expanded()` 决定
  （`Layout/Expand-Categories` 默认关；用户手动展开后 `_user_expanded` 优先，刷新列表不回弹）；`components/filter_panel.py` 的
  `FilterSection` 支持初始 `collapsed` 与 `set_collapsed()`（信号 `collapsedChanged`），三个分组默认全部折叠，
  `expanded_keys()` 与 `Layout/Expanded-Filters` 对齐。
- **「简化显示」统管所有按钮**：新增 `src/app/ui/framework/buttons.py` 的 `IconTextButton` / `IconTextPrimaryButton`
  （不重写 `__init__`——qfluentwidgets 的 `PushButton.__init__` 是 `singledispatchmethod` 且内部递归转调；简化显示下只留图标、
  完整文字进提示条，没有图标的按钮不受影响），SDK 经 `src/app/sdk/ui.py` 的 `__getattr__` 惰性导出给插件（导入 SDK 仍不拉起 Qt）；
  `src` 与 `plugins` 共 19 个文件 79 处「图标 + 文本」按钮由一次性 AST 脚本改名，页面与弹窗一并覆盖。
- **自检**：新增 `icon_text_buttons`（静态扫 `src` + `plugins` 不许再有裸 `PushButton` 承载图标，运行时验简化显示开关与还原）
  与 `layout_preferences`（每页条数 / 页码可见 / 两栏显隐 / 分组折叠 / 分类树默认收起都写进配置）；总数 63 → 65。

## 13. 后续变化：悬停提示与「说明不常显」

- **提示延迟可配**：新增 `src/app/ui/framework/tooltips.py` —— `HoverStyle(QProxyStyle)` 把 `SH_ToolTip_WakeUpDelay` 接到配置项
  `Layout/Tooltip-Delay`（默认 2000 毫秒）、`SH_ToolTip_FallAsleepDelay` 归零，`TooltipFilter` 在鼠标进入时给没有提示的控件补上
  `own_hint()`（显式提示 > `hoverHint` 属性 > 按钮文字），`describe()` 还会往上借最近 4 层父控件的提示；
  `install_tooltips()` 在 `src/main.py` 与 `scripts/selfcheck/harness.py ensure_app()` 里各装一次（幂等）。
- **说明不常显**：`framework/sections.py` 的 `PageHeader` 副标题默认隐藏、整段挂到标题提示上，新增 `add_hint()` / `set_hint()`
  （后者用于随状态变化的文案）；`section_card()` 的说明挂到分区标题（无标题时挂卡片）；`home_page.py` 的 `StatCard` 用新增的
  `KPI_HINTS` 与插件贡献的 `hint` 字段（已在 `plugins/EXTENSION_POINTS.md` 记录）；导入 / 管理 / 打开方式 / 插件页把
  `CaptionLabel` 说明改成标题提示或控件提示（`import_page` 与 `manage_page` 的文案随选择变化，各自的更新点同步刷新提示），
  存档页与用户页的身份说明改用 `header.set_hint()`。
- **按钮随简化显示变形**：`framework/buttons.py` 的 `IconTextButton` 在简化显示下 `setFixedSize(side, side)`（图标居中），
  `setMinimumWidth/Height/Size` 被覆写以便关掉简化显示后按原值还原；常量 `SQUARE_PADDING = 8`。
- **流式容器不再压窄按钮**：`home_page.py` 的「快捷操作」与 `import_page.py` 的文件按钮区从 `adaptive=True` 改回普通 `FlowArea`
  （自适应布局按 `minimum_width` 等分一行，会把按钮压到文字显示不全）。
- **自检**：新增 `hover_hints`（延迟可配、提示继承、方形按钮与还原）与 `prose_moved_to_hints`（说明确实进了提示）；总数 65 → 67。
## 14. 后续变化：问号标识、数字配置输入框与插件页展示

- **小问号标识与折行提示**：`framework/tooltips.py` 新增 `HintBadge` 与工厂 `hint_badge()`（14 px、「?」、`WhatsThisCursor`，
  样式 `HINT_BADGE_QSS` 放在 `theme.py`），以及 `HINT_COLUMNS` = 44、`display_width()`、`wrap_hint()`；`TooltipFilter.eventFilter`
  在 `ToolTip` 事件上改用 `QToolTip.showText(event.globalPos(), wrap_hint(describe(obj)), obj)` 画折行文本（提示框不再被拉成一条横条）。
  `PageHeader`（标题后）与 `section_card()`（分区标题后）各挂一枚问号，鼠标停在问号或标题上看到的是同一段提示；`home_page.StatCard`、
  导入页与数据管理页的字段说明同样加了问号（文案随状态变化时同步 `set_hint()`）。
- **方形按钮不再裁图标**：`framework/theme.py` 新增 `SQUARE_BUTTON_QSS`（`padding: 0px;`），`IconTextButton._apply_size()` 在简化显示下
  套用它（退出简化显示时清掉）；根因是 qfluentwidgets 的按钮 QSS 左右各 12 px 内边距，比 32 px 的方形按钮还宽，`SE_PushButtonContents`
  实测为负宽，图标被裁掉。
- **数字配置可直接输入**：新增 `framework/settings_cards.py` 的 `NumberSettingCard(RangeSettingCard)`——在滑块左边插入 `SpinBox`
  （宽 `SPIN_WIDTH` = 96 px）、隐藏只读的 `valueLabel`，滑块 / 输入框 / 配置项三者双向同步，范围取配置项的 `range`；设置页 8 张
  数字卡（悬停提示延迟、存档保留数量 / 容量 / 天数、日志保留文件数 / 单文件大小 / 保留天数 / 总量上限）全部换用它。
- **插件页展示**：`core/plugin_core.py` 的 `PluginInfo` 新增 `kind_label`（有 `libraries` 即「库插件」，否则「功能插件」）与
  `state_tone`（`error` / `ok` / `plain`）；新增 `framework/badges.py`（`StatusBadge` / `badge_row` / `paint_badges` / `BADGE_TONES`，
  圆角色调胶囊）与 `components/plugin_delegate.py` 的 `PluginItemDelegate`（继承 qfluentwidgets 的 `ListItemDelegate`，
  文本省略交给基类、右侧按自定义角色 `BADGES_ROLE` 画状态徽章，并把徽章夹在视口右缘内——列表关掉横向滚动条，项宽跟随视口）；
  插件页详情区改成徽章行 + 「协议与接口」/「清单与选项」两栏，不再是一条用 `·` 串起八件事的长句。
- **自检**：新增 `number_setting_cards`（输入框范围 / 双向同步 / 初值）与 `plugin_display`（代理类型、滚动条策略、每行徽章数据、
  详情徽章与清单摘要），`icon_text_buttons` 追加「内容区必须装得下图标」、`hover_hints` 追加「标题问号可见且提示与标题一致」与
  「`wrap_hint()` 每行不超 `HINT_COLUMNS`」；总数 67 → 69（`data` 4 / `services` 28 / `pages` 32 → 34 / `flows` 3）。
- **取证教训**：自检 / 探针的 harness 不装主题，`qfluentwidgets.isDarkTheme()` 会返回 `True` 而调色板仍是浅色，
  于是列表项文字被 `TableItemDelegate.initStyleOption` 画成白字、浅底上看不见——像素取证前必须先 `setTheme(Theme.LIGHT, save=False)` 固定主题。
## 15. 后续变化：问号收敛、标签图标化与配置切换提示

- **问号标识只留在页面标题**：`HintBadge` / `hint_badge()` 仍然只在 `PageHeader` 上用（每个页面标题一枚）；`section_card()` 的标题行、
  概览页 KPI 卡片（`StatCard`）、导入页的用户 / 分类、数据管理页的分类卡片与筛选卡片都不再挂问号，说明仍是纯悬停提示，
  停留时间照旧跟 `Layout/Tooltip-Delay` 走；自检 `hint_badges_on_titles` 断言整窗问号数与 `PageHeader` 数一致、且没有挂在卡片上
  （顺带补上 `framework/__init__.py` 里 `__all__` 声明了却没导入的 `HintBadge`）。
- **文本标签图标化**：新增 `src/app/ui/framework/labels.py` 的 `IconTextLabel`（`ICON_LABEL_SIZE` = 18、可选 `strong` 字重、
  `icon_only` 只画图标）与工厂 `icon_text_label()` / `icon_label()`：平时「图标 + 文字」，`Layout/Simple-Display` 打开后只留图标、
  文字进悬停提示，与 `IconTextButton` 同一套语义。落点：概览页「当前用户」、数据管理页「用户」、导入页七个字段名、
  打开方式页四个字段名、插件页「排序」与「协议与接口 / 清单与选项」、筛选栏三个分组标题与「范围与排序 / 排序」；
  仅剩的纯文本按钮（`dialogs.py` 的自动编号 / 每组只保留最新 / 全部取消勾选、导入页「清空选择」、筛选栏「重置筛选」）换成 `IconTextButton`。
- **配置切换一定有提示**：`SettingsPage` 新增 `TOAST_DELAY_MS` = 400 与 `_queue_setting_toast()` / `_flush_setting_toast()`，
  `ComboSettingCard` 新增 `changed` 信号（原先只有内部 callback），把开关 / 下拉 / 数字卡的改动统一汇总成右上角一条提示；
  400 毫秒内的连续改动合并成一条（拖滑块只弹一次），两个隐私保护开关自带更详细的提示、从统一接线里排除；
  原先主题与日志文件模式手写的两条提示删除，避免重复。
- **自检**：新增 `hint_badges_on_titles`、`icon_text_labels`、`setting_change_toasts`；总数 69 → 72（`pages` 34 → 37）。
## 16. 后续变化：对齐、按钮尺寸还原与列表 / 卡片可读性

- **退出简化显示要还原按钮尺寸**：`IconTextButton._apply_size()` 的方形分支用 `setFixedSize()`，会把 min/max 一起钉成 32×32；返回文字模式时原来只清 QSS 与最大尺寸，
  **没显示过的页面（布局没激活）不会重排**，于是插件页那种没 `switchTo` 过的页面留下的按钮只有 32×32，图标与文字挤在一起。
  现在非方形分支补 `self.resize(self._plain_size())`（新增 `_plain_size()`：`max(super().sizeHint(), self._plain_minimum)`）；
  `icon_text_buttons` 新增断言「带图标 + 文字的按钮宽度不得小于 `iconSize().width() + 2 * SQUARE_PADDING`」。
- **`IconTextLabel` 内部对齐**：图标 `setFixedSize(ICON_LABEL_SIZE, ICON_LABEL_SIZE)` + 居中，布局改成 `addWidget(icon, 0, AlignVCenter)` + `addWidget(text, 0, AlignVCenter)` + `addStretch(1)`——
  **不传对齐参数时两个子控件按比例拉伸**，宽容器（筛选栏「范围与排序」卡片）里文字会被推到中间，这正是「图标、文字、设置完全没对齐」的原因；`_apply_display()` 末尾补 `updateGeometry()`。
  导入页 `标签` / `关键词` 两个字段名原来用 `AlignTop`、比输入框中心高 11 px，改成 `AlignVCenter`。
- **插件列表两行绘制**：`PluginItemDelegate` 覆写 `initStyleOption()`（`super()` 之后 `option.text = ""`）——基类 `ListItemDelegate.paint()` 会在 `super().paint()` 里重新 `initStyleOption()` 从模型取文本，
  **只在 `paint()` 里改 `opt.text` 拦不住它**（这是「文字没被省略、压到状态胶囊上」的真正原因）；`sizeHint()` 保证宽度跟视口、高度 ≥ `MIN_ROW_HEIGHT` = 46；
  `paint()` 之后按 `SE_ItemViewItemText` 求出文字区、右缘收到徽章左边，标题（`TITLE_ROLE`，DemiBold）与副标题（`SUBTITLE_ROLE`，小 1 磅、alpha 150）各一行并省略，`painter.setClipRect(area)` 兜底；
  `plugin_page._fill_list()` 补 `TITLE_ROLE` / `SUBTITLE_ROLE`，`item.text()` 原样保留（既有断言与检索不受影响）。
- **用户卡片信息行**：新增 `card_info_lines(info)`（`LIBRARY` 项数据 / `TILES` 分类 / `DATE_TIME` 创建时间），`_user_card()` 把原来那行超长 `BodyLabel(card_summary(...))`（固定 `CARD_WIDTH` = 320、被硬裁且没有省略号）
  换成头像行下面的三行 `icon_text_label`，完整摘要挂 `card.setToolTip()`；新增自检 `user_card_info`（三行都能在卡片里找到、不被裁、提示等于 `card_summary()`）。
- **自检**：`icon_text_buttons`、`icon_text_labels`、`plugin_display` 扩充，新增 `user_card_info`；总数 72 → 73（`pages` 37 → 38）。
## 17. 后续变化：主色按钮样式、复选框对齐、启动与载入播报

- **主色按钮的 QSS 必须匹配得上**：`IconTextPrimaryButton` 原来写成 `(IconTextButton, PrimaryPushButton)`，PyQt 的多继承**只保留第一个基类的 QMetaObject 链**，
  qfluentwidgets 的 `PrimaryPushButton { background-color: --ThemeColorPrimary; … }` 靠类型选择器匹配 metaObject 链，于是主色按钮一直退回普通按钮底色 ——
  深色主题下 `PrimaryPushButton._drawIcon` 用反转色画深色图标，落在普通按钮的深底上就是「图标没适配深色主题」；给图标让位的内边距也一起丢，
  宽按钮（如「重置筛选」）上文字与图标贴在一起。现在基类顺序改成 `(PrimaryPushButton, IconTextButton)`（`PrimaryPushButton` 没有自定义 `__init__`，构造签名不变），
  自检 `icon_text_buttons` 改成分别查找 `IconTextButton` 与 `IconTextPrimaryButton`。
- **样式表不能被清空**：`_apply_size()` 原来用 `setStyleSheet(self, "")` 退出方形模式，会把 `PushButton.__init__` 装的整份按钮 QSS 冲掉；改用
  `setCustomStyleSheet(self, SQUARE_BUTTON_QSS, SQUARE_BUTTON_QSS)`（方形）与 `setCustomStyleSheet(self, "", "")`（还原）。
- **复选框对齐**：`theme.py` 新增 `CHECK_BOX_QSS`（`margin-left: 0px; spacing: 4px;` + 指示器 18 px）与 `align_check_box(box)`，筛选栏「显示隐藏项 / 只看回收站」用它，
  指示器与文字的左缘跟同卡片里 `IconTextLabel` 的图标 / 文字对齐。
- **信息行不受简化显示影响**：`IconTextLabel` / `icon_text_label()` 新增 `keep_text=True`，用户卡片的三行信息（项数据 / 分类 / 创建时间）始终保留文字。
- **插件载入播报**：`PluginService.load()` 先 `logger.info("插件扫描完成：发现 {} 个（启用 {}、未启用 {}、清单有误 {}）", …)`；`loaded_summary()` 改成
  `插件载入：共 N 个（已启用 X、未启用 Y）；库插件 …、功能插件 …`。
- **启动阶段播报**：`src/main.py` 新增 `_STARTUP_STAGES` = 8 与 `_stage(step, text)`，`main()` 每完成一阶段打印 `启动 n/8：…`，
  退出时补 `事件循环结束，退出码 N` 与 `退出：数据库已收起、会话标记已清理`。
- **自检**：新增 `plugin_load_summary`（services）与 `startup_stage_logs`（flows）；总数 73 → 75（`services` 28 → 29、`flows` 3 → 4）。

## 18. 后续变化：「简化显示」三挡位与用户卡流式操作区

- **挡位化**：`Layout/Simple-Display` 从布尔值改成 `OptionsConfigItem` 三挡（`none` / `default` / `full`），
  `SimpleDisplayValidator.correct()` 把旧配置里的布尔值换算成挡位（`true` → `full`、`false` → `none`、非法值 → `default`），
  设置页那张卡从 `SwitchSettingCard` 换成 `ComboSettingCard`；`simple_display()` 仍然表示「处于某种简化挡位」。
- **逐控件判定**：新增 `src/app/ui/framework/simple_mode.py`（`simple_mode()` / `simple_display()` / `icon_key()` /
  `shares_icon_with_peers()` / `should_simplify()` / `refresh_peers()`）。`default` 挡位只收「同一个容器里没有别的控件用同一枚图标」的按钮与标签 ——
  插件页的「重命名 / 编辑说明 / 编辑备注」都是 `FluentIcon.EDIT`，保留文字才分得清。`IconTextButton` / `IconTextLabel` 记住 `_icon_key`，
  构造完与 `setIcon()` 之后调 `refresh_peers()` 让同容器的兄弟重算。
- **用户卡操作区改流式**：`user_page.py` 的 `_user_card()` 用 `FlowArea`（间距 6）替掉两列 `QGridLayout`（`CARD_BUTTON_COLUMNS` 删除）：
  简化显示下按钮缩成方形后不再各占一格、显得空。`FlowArea` 的高度取决于自身宽度，卡片等高改成 `_schedule_card_fit()` →
  `QTimer.singleShot(0, self._equalize_card_heights)`：先 `grid.activate()` + `area.sync_height()` 再量 `sizeHint().height()`，
  切换挡位（按钮尺寸随之变化）时也重新等高，否则卡片会按「没有操作区」的高度定死、按钮被卡片下边缘裁掉。
- **自检**：新增 `simple_modes`（pages）——三挡位读回值、旧布尔值换算、插件页三个 `EDIT` 按钮在 `default` 下保留文字而图标唯一的按钮收成方形、
  用户页 `keep_text` 信息行在 `full` 下仍显示文字；`checks_tags_ui.py` 的「按钮被裁出卡片」断言改成 `button.mapTo(card, QPoint(0, 0))` 再比
  （按钮的父控件现在是 `FlowArea`）；总数 75 → 76（`pages` 38 → 39）。
