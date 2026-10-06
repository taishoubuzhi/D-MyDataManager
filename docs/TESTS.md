# 测试协议（tests/）

> `tests/` 只放**开发期**测试与门禁入口：程序运行不依赖这里（`src/` 与 `plugins/` 不 import 任何 `tests/` 模块）。
> 端到端自检套件不在这里——`src/main.py --self-check` 加载的是 `scripts/selfcheck.py`，脚本侧的规范见
> [`SCRIPTS.md`](SCRIPTS.md)。本文件声明 `tests/` 里每个文件的作用、运行方式与新增测试必须遵守的约束。

## 1. 目录结构

| 路径 | 作用 |
| --- | --- |
| `__init__.py` | 测试包入口：把 `ROOT`、`ROOT/src`、`ROOT/scripts` 加进 `sys.path`，并创建**进程级** `QApplication` 一直持有（否则某个用例回收 app 时 Qt 会连带销毁先于它创建的模块级 `QObject`，后续用例报 `wrapped C/C++ object of type SignalBus has been deleted`） |
| `harness.py` | `IsolatedCase` / `TempDir`：每个用例一份临时数据库、库文件夹、内容仓库、封面与导出目录（`tests/.tmp/<类名小写>/`），不碰真实 `.resources/` / `.configs/` / `.logs/`；隔离环境的实现在 `scripts/tmpenv.py` |
| `dataset.py` | 多样化语料生成（`Corpus` / `build_corpus`），导入类用例与 `scripts/seed_demo.py` 共用 |
| `core/` `services/` `sdk/` `plugins/` | 按**被测对象**分目录的单元测试包（一个主题一个模块） |
| `verify.py` | 门禁聚合入口：编译 → 插件桩 `--check` → `unittest discover` → 自检四层 → `pytest` → 可选依赖验收 → 打包冒烟 |
| `smoke_checkout.py` | 打包冒烟：用 git 载荷造干净检出，在里面编译、走真实启动路径、跑门禁子集 |
| `verify_optional_absence.py` | 把可降级依赖全藏起来后，程序仍能导入 / 入库 / 读回 / 回退监听 |
| `.tmp/` | `tests/` 下所有测试共用的临时目录根（`tests/.tmp/`），运行结束自动清除（见第 5 节），已在 `.gitignore` 中 |

## 2. 单元测试清单（每个文件的作用）

> 新增用例前先在这里查：**已有模块覆盖同一被测对象时，追加到那个模块**，不要新建重复模块（规范第 3 条）。

### core/ —— 对应 `src/app/core` 与 `src/app/db`：配置、模块数据、清单机制、数据库、JSON / 编码 / MIME、口令散列、能力探测

| 文件 | 覆盖范围 |
| --- | --- |
| `core/test_capabilities.py` | 可选能力探测（批 K）：能查、缺了有中文提示、装/没装都不影响启动。 |
| `core/test_config_migration.py` | 配置迁移（批 C）：旧版「简化显示」布尔值在启动时一次性换算成三挡位。 |
| `core/test_core_module_data.py` | core 模块数据：`app.core.runtime.module_data` 的读取与校验，以及三个实际数据文件与代码常量的一致性。 |
| `core/test_database.py` | 数据库层：连接 PRAGMA（批 J2）与引擎整理。 |
| `core/test_jsonio.py` | JSON 读写（批 J6）：优先 orjson、缺依赖退回标准库，两者语义一致。 |
| `core/test_manifest.py` | 清单机制：读取 / 查询 / 对照 / 变更 / 重置 / 备份，以及 SDK 面。 |
| `core/test_mime.py` | MIME 与编码探测（批 J4）：内容嗅探优先于扩展名，但同类时扩展名更具体。 |
| `core/test_security.py` | 口令散列：argon2id 首选、PBKDF2 兜底、老散列惰性升级（批 J3）。 |

### services/ —— 对应 `src/app/services`：条目与批量改名、标签、存档、隐私、用户与权限、布局迁移

| 文件 | 覆盖范围 |
| --- | --- |
| `services/test_batch_rename.py` | 批量改名规则与关键词批量增减的用例：四种改名方式、重名序号、扩展名保护。 |
| `services/test_hidden.py` | 隐藏数据（`.hiddens/`）的用例：搬动、回搬、清理、扫描与过滤。 |
| `services/test_item_api.py` | 数据接口（`app.sdk.items` + `app.services.item_api`）的用例。 |
| `services/test_item_rename.py` | 数据改名用例：改显示名时库内文件一起改名，重名加序号，文件不在库里也不报错。 |
| `services/test_layout_migration.py` | 库目录结构迁移：找回文件的三种真实布局（用户 m42407）。 |
| `services/test_privacy.py` | 资源文件夹保护（静态保护模型）与启动自愈的用例。 |
| `services/test_tag_permissions.py` | 标签权限：默认用户（管理员）可管理任意标签，其他用户只能管理自己创建的标签；仍被他人数据使用的标签不能转为个人。 |
| `services/test_tag_shadows.py` | 标签重名：个人标签不得与全局标签同名，重名时并入全局标签（含启动修复）。 |
| `services/test_user_delete.py` | 用户删除：不能删除当前用户；被删用户的数据、标签、存档条目与创建者一并转移。 |

### sdk/ —— 对应 `src/app/sdk`：插件 SDK 门面

| 文件 | 覆盖范围 |
| --- | --- |
| `sdk/test_console_sdk.py` | SDK 控制台接口：默认落 loguru，程序提供 console.output 时转发，插件侧用 ctx.console 播报。 |

### plugins/ —— 对应 `plugins/`：自动标签 / 自动关键词 / 模型插件 / 插件载入

| 文件 | 覆盖范围 |
| --- | --- |
| `plugins/test_auto_keyword.py` | 任务 4（`auto_keyword`）：按数据类型生成关键词、写库与插件接线。 |
| `plugins/test_auto_tag.py` | 任务 3（`auto_tag`）：规则 + 模型两套方案、合并写库与插件接线。 |
| `plugins/test_auto_tag_rule.py` | 任务 2（`auto_tag.rule`）：规则挂标签的执行逻辑、格式默认标签与插件接线。 |
| `plugins/test_autolabel_align.py` | 自动标注共享库：数据类型 ↔ 模型对齐表（离线用例，不联网）。 |
| `plugins/test_autolabel_pipeline.py` | 自动标注共享库：批处理管线（离线用例，`run_batch` 用假的替换）。 |
| `plugins/test_autolabel_rules.py` | 自动标注共享库：规则模型、匹配引擎与用户规则文件（离线用例，不联网）。 |
| `plugins/test_model_batch.py` | 模型批量调度与预定义方案：离线用例，不联网、不装包。 |
| `plugins/test_model_download.py` | 模型下载器的用例：本地 http.server 验续传、sha256、镜像回退、暂停与磁盘预检。 |
| `plugins/test_model_registry.py` | 模型登记表、记录、调度器与设置：离线用例，不联网、不装包。 |
| `plugins/test_model_runtime.py` | 模型插件的运行环境与 worker 子进程后端测试。 |
| `plugins/test_plugin_loading.py` | 插件载入：冲突只影响启用、不影响载入，且启用/禁用变更与不可用原因都要进控制台。 |

## 3. 运行方式（只跑本次改动相关的单元）

```powershell
# 改哪块就只跑哪块（推荐日常）
.venv\Scripts\python.exe -m unittest tests.core.test_manifest -v
.venv\Scripts\python.exe -m unittest tests.services.test_tag_permissions tests.services.test_tag_shadows -v

# 只跑某个单元包
.venv\Scripts\python.exe -m unittest discover -s tests/core -t . -v

# 需要整体回归时（编译 / 插件桩 / 单测 / 自检四层 / pytest / 可选依赖 / 打包冒烟）
.venv\Scripts\python.exe tests\verify.py                 # --quick 只跑快的四步，--list 列步骤
$env:DM_KEEP_TMP=1                                       # 保留 .tmp/ 便于排查
```

- **不建议每次全量跑**：改一处就跑对应模块；全量 `tests/verify.py` 留给提交前或整批改完时。
- 界面用例需要 `QT_QPA_PLATFORM=offscreen`（`tests/verify.py` 会自动带上）。

## 4. 单元测试规范（新增测试必须遵守）

1. **一个主题一个模块**：文件名 `test_<主题>.py`，主题对齐被测模块（`src/app/services/library_service.py` → `tests/services/test_library_service.py`）。不要按「杂项 / 其他」建模块。
2. **先归目录**：按被测对象放进 `tests/{core,services,sdk,plugins}/`；跨领域的新单元先新建目录并在第 1 节登记。
3. **不与已有测试重叠**：动手前查第 2 节清单；被测对象相同 → 在同一模块里加用例（必要时按类拆分），不要新开文件重复覆盖。
4. **类名 `XxxCase`，按需选基类**：需要数据库 / 库目录 / 配置的继承 `tests.harness.IsolatedCase`；纯函数（解析、格式化、筛选、分页、文案）直接用 `unittest.TestCase`，不要为了统一而引入 IO。
5. **模块首行一句中文 docstring** 说明覆盖范围（第 2 节的表格就是从这里生成的）。
6. **用例名写成 `test_<行为>_<条件>`**，例如 `test_global_tag_used_by_others_cannot_become_personal`；断言的失败信息写清期望与实际的差别。
7. **用例之间互不依赖**：不依赖执行顺序、不用跨用例的类变量；临时文件一律走第 5 节的 `.tmp/`；界面用例销毁控件用 `self.drop_widget(widget)`（立即 `sip.delete`，不要 `deleteLater()`）。
8. **断言用 `self.assertXxx`**（不用裸 `assert`，除非是测试夹具的 setup 前置条件），一个用例只验证一个行为。
9. **新增 / 删除用例模块后同步第 2 节清单**，保证协议与代码一致。

## 5. 临时目录约定（脚本与测试统一）

**唯一规则：`tests/` 下的临时物一律落在 `tests/.tmp/`，`scripts/` 下的一律落在 `scripts/.tmp/`。**

- 实现只有一处：`scripts/tmpenv.py`。测试侧用 `tests_tmp(*parts)`，脚本侧用 `scripts_tmp(*parts)`
  （两者都是 `temp_root(<顶层目录>, *parts)` 的便捷包装）；**不使用系统临时目录**，也不在仓库根或
  被测目录里散落临时物。
- `.tmp/` 在运行期间创建，进程退出时由 `atexit` 整体删除；设置 `DM_KEEP_TMP=1` 可保留以便排查失败
  （`scripts/selfcheck.py --keep-db` 只保留自检自己的检查目录）。
- 用例自己的隔离目录请用 `self.root`（即 `tests/.tmp/<类名小写>/`，`tearDownClass` 会删），
  不要往 `tests/` 根写文件；需要额外目录就用 `tests_tmp("<名字>")`。
- `.tmp/` 已在 `.gitignore` 中，任何时候都不应进入提交与打包载荷（`tests/smoke_checkout.py` 会检查
  载荷里没有任何名为 `.tmp` 的路径段）。

## 6. 与自检套件（`scripts/selfcheck/`）的关系

- 单元测试验**函数与服务的契约**；自检套件验**页面装配与端到端流程**（建真实窗口 + 真实数据库，但都在隔离临时目录里）。
- 两者互补：单元测试快、定位准；自检覆盖 GUI 与跨层流程。改动数据层时先跑单元测试，改动界面 / 插件装配时跑
  `scripts/selfcheck.py --layer pages`。
- 自检套件的分层、检查清单与扩展方式见 [`SCRIPTS.md`](SCRIPTS.md)。
