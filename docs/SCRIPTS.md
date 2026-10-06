# 脚本协议（scripts/）

> `scripts/` 放**程序运行与运维需要的脚本**：维护者能在仓库里直接跑，也随发布载荷分发（自检、插件桩、
> 示例数据、性能基准）。开发期单元测试在 `tests/`，规范见 [`TESTS.md`](TESTS.md)。
> 本文件声明每个脚本的作用、临时目录约定，以及新增脚本必须遵守的规范。

## 1. 文件清单

| 文件 | 类型 | 作用 |
| --- | --- | --- |
| `selfcheck.py` | 入口 | 自检套件入口：四层共 149 项检查，末行输出 `RESULT failures=N`；`src/main.py --self-check` 也加载它 |
| `selfcheck/` | 包 | 自检套件实现：`harness.py`（注册表 / 隔离环境 / 结果收集）、`cli.py`（命令行）、`fixtures.py`（代表数据）+ 15 个 `checks_*.py` |
| `tmpenv.py` | 共用工具（非入口） | `tests_tmp()` / `scripts_tmp()` / `TempDir` / `redirect_paths()` / `reset_config()` / `reset_runtime_dirs()`：统一临时目录（`scripts/.tmp/`、`tests/.tmp/`）与隔离运行环境，`tests/` 也复用它 |
| `plugin_stubs.py` | 入口 | 按插件清单生成 `stubs/dm_plugin/**.pyi`，供 IDE 解析运行期合成包 `dm_plugin.<id>`；`--check` 只校验一致性 |
| `seed_demo.py` | 入口 | 清空运行期数据后注入一份多样化示例数据（**会操作真实数据**） |
| `dev_reset.py` | 入口 | 把数据库与运行期目录恢复到首次运行的干净状态（**会操作真实数据**） |
| `benchmark.py` | 入口 | 内容仓库 / 数据库热点的性能基准，打一张 markdown 表给 `docs/HELP.md` |

## 2. 自检套件（`selfcheck.py` + `selfcheck/`）

分四层，一项检查一个隔离环境（临时库 + 临时配置 + 已播种默认数据）：

| 分层 | 项数 | 覆盖 | 检查模块 |
| --- | --- | --- | --- |
| `data` | 6 | 库结构与内容仓库（业务表、FTS5、触发器、blob 布局、配置落盘） | `checks_data.py`、`checks_model.py`、`checks_autolabel.py` |
| `services` | 68 | 服务层行为（导入 / 导出 / 存档 / 用户 / 插件载入 / 统计） | `checks_services.py`、`checks_plugins.py`、`checks_plugin_layout.py`、`checks_editor_ui.py`、`checks_model.py`、`checks_logging.py` |
| `pages` | 71 | 页面结构（offscreen 建页面，断言结构、权限装配与界面回归） | `checks_pages.py`、`checks_manage_ui.py`、`checks_tags_ui.py`、`checks_archive_ui.py`、`checks_contributions.py`、`checks_navigation.py`、`checks_model.py`、`checks_autolabel.py` |
| `flows` | 4 | 端到端流程（导入 → 筛选 → 移动 → 存档 → 还原） | `checks_flows.py`、`checks_tags_ui.py` |

```powershell
.venv\Scripts\python.exe scripts\selfcheck.py                    # 全量：四层一次跑完，末行 RESULT failures=0
.venv\Scripts\python.exe scripts\selfcheck.py --list             # 列出全部检查（分层 / 名称 / 一句话说明）
.venv\Scripts\python.exe scripts\selfcheck.py --layer services   # 只跑某一层
.venv\Scripts\python.exe scripts\selfcheck.py --only style_uniformity --verbose
.venv\Scripts\python.exe scripts\selfcheck.py --json             # 每项一条 JSON，便于脚本抓取
.venv\Scripts\python.exe scripts\selfcheck.py --keep-db          # 保留临时库目录并打印路径
```

- **新增检查**：在对应层的 `checks_*.py` 里写一个函数并加 `@check("名字", "分层")` 装饰器，函数体只调用公开契约；
  名字必须唯一，检查之间互不影响（每项自带隔离环境，由 `Case` 提供）。
- 模块与分层在 `selfcheck/harness.py` 的 `MODULES` 里登记；同名的 `checks_model.py` / `checks_autolabel.py` /
  `checks_tags_ui.py` 被多层复用，只是注册的检查分层不同。
- 门禁侧（`tests/verify.py`）不直接跑四层合一：`pages` 的 71 项 Qt offscreen 检查在同一长驻进程里偶发原生崩溃
  （退出码 `3221225477` = `0xC0000005`），所以按 12 项一组拆进独立进程；分块后仍偶发时自动重跑一次
  （崩溃点会漂移、同一块重跑必过，属环境问题而非检查失败）。

## 3. 临时目录与隔离（所有脚本统一）

**唯一规则：`scripts/` 下的临时物一律落在 `scripts/.tmp/`，`tests/` 下的一律落在 `tests/.tmp/`。**

- 实现只有一处：`scripts/tmpenv.py`。脚本侧用 `scripts_tmp(*parts)`，测试侧用 `tests_tmp(*parts)`；
  **不使用系统临时目录**，也不在仓库根或被测目录里散落临时物。
- `.tmp/` 在运行期间创建，进程退出时由 `atexit` 整体删除；`DM_KEEP_TMP=1` 可保留以便排查失败。
- `.tmp/` 已在 `.gitignore` 中，任何时候都不应进入提交与打包载荷（`tests/smoke_checkout.py` 会检查）。
- 会读写运行期数据的脚本，必须先 `redirect_paths(临时根)` + `reset_config(临时根)` 把 `.resources/` 与
  `.configs/` 整体指到临时目录；只有 `seed_demo.py` 与 `dev_reset.py` 例外——它们的用途就是操作真实运行期数据，
  且必须在 docstring 里写明这一点。
- `tmpenv` 在 `redirect_paths()` / `reset_config()` 末尾会核对重定向是否真的生效，不生效直接抛错——因为
  `app.core.config` 首次导入时会执行 `load_config()` 把资源根指回真实目录，这套核对保证脚本不会静默写到真实数据上。

## 4. 新增脚本规范（必须遵守）

1. **入口形状**：模块首行一句中文 docstring + 「用法：」命令行示例；`from __future__ import annotations`；
   `ROOT = Path(__file__).resolve().parents[1]`；把 `ROOT`、`ROOT/src`（需要时 `ROOT/scripts`）加进 `sys.path`；
   `def main(argv: list[str] | None = None) -> int`；结尾 `if __name__ == "__main__": raise SystemExit(main())`。
2. **可直接运行且幂等**：在仓库根用 `.venv\Scripts\python.exe scripts\<名字>.py` 跑得起来，重复执行结果一致，
   失败返回非 0 退出码。
3. **不碰真实数据**：默认走隔离环境（见第 3 节）；确需操作真实数据的脚本必须在 docstring 里显式警告。
4. **不擅自联网、不静默安装或下载**：依赖只取标准库 + `requirements.txt` 里已有的包。
5. **输出可机读**：结果写 stdout；批量检查 / 门禁类脚本末行给结论（统一 `RESULT failures=N`），失败时退出码 1。
6. **共用逻辑放 `tmpenv.py` 或被测模块**，脚本之间不互相 import；唯一例外是 `selfcheck.py`（入口）与
   `selfcheck/`（同包实现）。
7. **不改动仓库状态**：除了 `stubs/`（`plugin_stubs.py` 的产物）与真实运行期数据（`seed_demo.py` /
   `dev_reset.py`），脚本不得在仓库里留下其它文件。
8. **新增 / 删除脚本后同步本文件第 1 节清单**；被门禁调用的脚本还要同步 `tests/verify.py` 的步骤定义。

## 5. 与 `tests/`、程序本体的关系

- `tests/` 可以 import `scripts/tmpenv.py`（隔离环境实现的唯一来源）；`tests/verify.py` 与 `tests/smoke_checkout.py`
  会直接调用 `scripts/` 下的入口。
- `scripts/` 不 import `tests/`，唯一遗留是 `seed_demo.py` 复用 `tests/dataset.py` 的语料生成器——那份语料本来就是
  测试与演示共用，挪动它会同时打断两侧，故保留并在两侧文档中注明。
- `src/main.py --self-check` 加载 `scripts/selfcheck.py`（自带隔离，所以在启动流程之前就返回）；脚本不存在时退回
  「建好界面就退出」的打包冒烟测试。
