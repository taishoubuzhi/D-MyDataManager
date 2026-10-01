# 单元测试范式

本目录只保留两类东西：**共享的隔离基类**和**按主题新增的用例模块**。
不维护「一次跑完整套」的用例集合：改动哪块代码，就只跑哪块的用例；整体回归交给 `scripts/dev_check_*.py`。

## 目录

| 路径 | 说明 |
| --- | --- |
| `harness.py` | `IsolatedCase` / `TempDir`：把数据库、库文件夹、内容仓库、封面与导出目录重定向到 `tests/_tmp/<类名小写>/`，不读写真实的 `.resources/`、`config/`、`logs/` |
| `dataset.py` | 多样化语料生成（`Corpus` / `build_corpus`），导入类用例与 `scripts/seed_demo.py` 共用 |
| `test_<主题>.py` | 按主题新增的用例模块（一个主题一个模块，例如隐藏数据 `test_hidden.py`、隐私保护 `test_privacy.py`） |
| `_tmp/`、`_scratch/` | 运行时临时目录，已在 `.gitignore` 中 |

## 新增用例的四条约定

1. **一个主题一个模块**：文件名 `test_<主题>.py`，主题对齐被测模块，例如 `src/app/services/library_service.py` → `tests/test_library_service.py`。
   不要把不相关的东西塞进同一个文件，也不要按「杂项」「其他」建模块。
2. **类名 `XxxCase`，按需选基类**：需要数据库 / 库目录 / 配置的用例继承 `tests.harness.IsolatedCase`；
   纯函数（解析、格式化、筛选、分页、文案）直接用 `unittest.TestCase`，不要为了统一而引入 IO。
3. **模块首行一句中文 docstring** 说明覆盖范围；用例名写成 `test_<行为>_<条件>`，例如 `test_delete_user_merges_data_into_default`。
4. **用例之间互不依赖**：不依赖执行顺序、不用跨用例的类变量；临时文件一律落在 `tests/_tmp/` 下。
   界面用例销毁控件统一用 `self.drop_widget(widget)`（立即 `sip.delete`，不要 `deleteLater()`）。

## 模板

```python
"""<主题>：<覆盖范围一句话>。"""

from tests.harness import IsolatedCase


class LibraryServiceCase(IsolatedCase):
    def test_scan_registers_new_files(self):
        service = ...
        self.assertEqual(service.scan(), 1)
```

纯逻辑模块改成 `import unittest` + `class XxxCase(unittest.TestCase):` 即可。

`IsolatedCase` 现成可用的东西：`self.session`（每个用例一份干净会话）、`self.corpus`（语料）、
`self.current_user()`、`self.default_library()`、`self.importer(**kwargs)`、`self.drop_widget(widget)`。

## 运行方式

```powershell
.venv\Scripts\python.exe -m unittest tests.test_library_service -v            # 只跑相关主题
.venv\Scripts\python.exe -m unittest tests.test_manage tests.test_libraries -v  # 多个相关主题
$env:DM_KEEP_TMP=1                                                            # 保留 tests/_tmp/ 便于排查
```

- 改完哪块代码，只跑对应主题的模块；**不再全量 `unittest discover`**。
- 需要整体回归时跑自检脚本：`scripts/dev_check.py`、`dev_check_services.py`、`dev_check_flow.py`、`dev_check_ui.py`
  （覆盖数据层、服务层、真实交互流程与 31 项界面检查，比单元测试更贴近真实装配）。
