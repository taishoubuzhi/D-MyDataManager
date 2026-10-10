# 清单机制协议（MANIFEST PROTOCOL）

> 实现位置：`src/app/core/manifest/`（`registry.py` / `kit.py` / `errors.py` / `schema/`）与插件侧门面 `src/app/sdk/manifest.py`。
> 面向「程序本体与插件都想读、想查、想改、改错了还能退回去」的那类数据。

## 1. 边界：什么进清单，什么不进

清单机制只管**固定、但需要被外部替换或扩展**的注册表 / 列表 / 映射（用户决定 D1/D4）。判定规则：

| 数据形态 | 归属 | 例子 |
| --- | --- | --- |
| 固定、且需要被外部替换 / 扩展的注册表、列表、映射 | **清单**（受本文约束） | `core.runtime`、`core.plugins`、模型清单、运行时档位、查看器 / 编辑器类型表、能力与依赖表 |
| 用户在界面里改的配置项 | **`.configs/config.json`（QConfig），不进清单** | 主题、语言、分页大小、日志级别、导入策略、归档保留份数 |
| 只在模块内部使用、无外部替换需求的常量与逻辑 | **留 Python** | 状态标识 `STATE_READY`、压缩级别、正则、协议字段名 |

一句话：**配置项归 QConfig，可替换数据归清单，内部常量留代码。**

## 2. 统一格式（`kind` 受管清单）

```json
{
  "manifest": "1",
  "id": "core.runtime",
  "version": "1",
  "kind": "module-data",
  "description": "core 运行期常量",
  "items": [
    {"key": "app_name", "value": "D-MyDataManager"},
    {"key": "log_glob", "value": "*.log"}
  ]
}
```

* 顶层必须项：`manifest`（协议版本，当前 `"1"`）、`id`、`version`、`kind`、`items`。
* `items` 必须是数组，每一项必须有非空字符串 `key`，同一份清单里 `key` 不得重复。
* 每项除 `key` 之外的字段随 `kind` 自定义；**要按 key 取值的那一项必须带 `value` 字段**（`ManifestData.value()` 只认 `value`）。
* `id` 规则与插件 id 相同：`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`。
* `kind` 取值：`module-data`（模块常量）、`registry`（注册表）、`profiles`（档位表）、`catalog`（清单目录）、`journal`（临时清单，见 §2.1）。

### 2.1 临时清单（`kind = "journal"`）

`journal` 是唯一一种**运行期产生、用完即删**的清单类型，承载「长任务的可恢复进度」：把待处理项先落盘，中途允许暂停 / 继续 / 取消，进程异常退出后下次启动还能接着做。它与其余四种类型的区别不在字段形状，而在生命周期：

| | 普通受管清单 | 临时清单 |
| --- | --- | --- |
| 存在时长 | 长期，是程序数据的一部分 | 一个任务的生命周期 |
| 谁创建 | 随代码 / 插件声明，固定路径 | 运行期动态 `register` + `write` |
| 备份 | 每次变更前备份，留 10 份 | **不备份**（`write(..., backup=False)`，易失状态备份没有意义） |
| 写入节奏 | 变更即写 | 节流合批（见下） |
| 结束 | 一直存在 | 待处理项全部结清时删文件 + 注销登记 |

顶层字段在统一格式之外附带：`journal_kind`（哪类任务，也是目录名）、`batch`（批次标识）、`state`（`running` / `paused` / `finished` / `abandoned`）、`created_at` / `updated_at`（ISO 字符串）、`options`（恢复时重现同一套语义的参数，例如导入的目标用户 / 分类 / 重名策略）。`title` / `description` 沿用统一格式的那两个可选字段。

`items` 每一项在 `key` 之外至少带 `status`：`pending`（待处理）→ `active`（进行中）→ `done` / `skipped` / `failed` / `cancelled`（终态）。`pending` 与 `active` 是「还没结清」，只要还有一项没结清，这份清单就不许删。

**进程重启后必须重新登记。** `ManifestRegistry` 只是内存字典，进程一退登记就没了；盘上残留的 JSON 不重新 `register`，`manifest_kit` 会直接报「没有登记名为 … 的清单」。所以临时清单的宿主必须在启动时扫描自己的目录、按文件里的 `id` 重新登记（`app.core.journals.JournalStore.scan()` 做的就是这件事），再把清单呈现给用户决定「继续 / 放弃」。

**写入节流。** 逐项修改都整份重写是 O(n²)。宿主应合批：默认每 1 秒或每 20 条改动落盘一次（`FLUSH_INTERVAL` / `FLUSH_EVERY`），并在状态跃迁（开始 / 暂停 / 结束）、退出时强制落盘。崩溃最多丢掉最后一个合批窗口内的进度，而恢复本来就要重新核对，所以这个取舍是安全的。

**恢复语义。** `active` 的项在异常退出时结果未知，恢复时退回 `pending`，由宿主按自己的语义核对（导入侧就是查内容校验和、收养库里已落盘但没登记的文件），而不是猜成功或猜失败。

**实现位置**：`src/app/core/journals.py`（`Journal` / `JournalStore`），底层仍走 `manifest_kit`。普通清单的宿主不需要关心这一层。

## 3. 历史格式（`legacy`）

只剩 `.configs/plugins.json`（登记为 `core.plugin_state`）：它是用户勾选的插件开关状态，格式由插件服务自己定，登记只为备份 / 重置，没有迁成统一格式的必要。

插件 `.data/` 下的数据文件已全部迁成统一格式：

* 批 H5：`lib.autolabel.rules`、`lib.autolabel.align`；
* 批 I：`lib.model.{model_list,api_templates,runtime_profiles}`、7 份 `<builtin.viewer.*>.viewer`、2 份 `<builtin.editor.*>.editor`。
* 从历史格式统一到现在的形状是一次性迁移（搬运脚本已随清理删除）；`legacy` 只留给不改格式的 `core.plugin_state`。

`legacy` 条目可 `raw()` 整份读、可整份 `diff`；`query()` / `update()` / `write()` 明确报错。

## 4. 登记表（`ManifestRegistry`）

* `ManifestEntry(id, path, kind, owner, format, schema, description)`，`format ∈ {"manifest", "legacy"}`，`managed` 即 `format == "manifest"`。
* `ManifestRegistry.register(entry, source="")`：重复 id 抛 `ValueError`；`entry(id)` 未登记抛 `ManifestNotFoundError`。
* `builtin_entries()` 当前 17 条：

| id | 路径 | 归属 | 格式 |
| --- | --- | --- | --- |
| `core.runtime` | `src/app/core/runtime/runtime.json` | core | manifest |
| `core.plugins` | `src/app/core/plugins/plugins.json` | core | manifest |
| `lib.autolabel.rules` / `.align` | `plugins/lib.autolabel/.data/*.json` | lib.autolabel | manifest |
| `core.plugin_state` | `.configs/plugins.json` | core | legacy |
| `lib.model.model_list` / `.runtime_profiles` / `.api_templates` | `plugins/lib.model/.data/*.json` | lib.model | manifest |
| `builtin.viewer.{archive,audio,image,markdown,spreadsheet,text,video}.viewer` | `plugins/builtin.*/.data/viewer.json` | 对应插件 | manifest |
| `builtin.editor.{text,office}.editor` | `plugins/builtin.*/.data/editor.json` | 对应插件 | manifest |

共 17 条，其中 **16 条受管（`format == "manifest"`）+ 1 条历史格式**。

插件数据文件的形状（批 I 定案）：

* 查看器 / 编辑器 `viewer.json` / `editor.json`：只有一条记录，`key` 就是插件 id（`builtin.viewer.image`），元数据字段（`name` / `kind` / `host` / `extensions` / `capabilities` / `description` / `order`）都写在这一项上；基类用 `record_of(ctx.data("viewer"), self.id)` 取。
* `model_list.json` / `api_templates.json` / `runtime_profiles.json`：一条记录一项，`key` 与记录自己的 `id` 相同（`id` 保留，域代码继续按 `id` 取值，`key` 只作清单地址）；原顶层 `note` 搬到清单的 `description`。
* `info.json`：`kind = "module-data"`，`about` / `points` / `events` 各一项，用 `value_of(payload, key, default)` 取。

内置条目的路径由 `builtin_entries()` 在**每次访问时**按当前 `paths` 现算（`ManifestRegistry._refresh_builtin()`），因此运行期重挂资源根 / 隔离测试目录后，`manifest_kit` 操作的始终是当前那份文件，而不是 import 那一刻的快照。

## 5. 接口

### 5.1 程序本体：`app.core.manifest`

```python
from app.core.manifest import manifest_kit
```

| 方法 | 说明 |
| --- | --- |
| `entries()` / `ids()` | 全部登记项 / 全部 id |
| `describe(id=None)` | 元信息：路径、归属、格式、大小、mtime、`version`、`count`、`exists`、`error` |
| `load(id, *, validate=True) -> ManifestData` | 读取 + 校验；`validate=False` 只跳过 JSON Schema 校验，结构校验始终执行 |
| `raw(id)` | 原样返回文件内容（legacy 只有这条路） |
| `query(id, **filters) -> list[dict]` | 按字段筛项，值为元组 / 列表表示「命中其一」 |
| `diff(id, candidate) -> list[str]` | 与候选内容对照，产出 `+ id.key` / `- id.key` / `~ id.key（字段: 旧 → 新）`；`candidate` 可为 dict 或文件路径；legacy 整份比较 |
| `write(id, data, *, backup=True)` | 整份替换：先校验、再备份、最后原子写 |
| `update(id, mutations, *, backup=True)` | 按 key 变更：`{"key": k, ...}` 合并字段，`{"key": k, "remove": True}` 删项 |
| `reset(id)` | 用**最近一次**备份覆盖当前文件（无备份抛 `ManifestBackupError`） |
| `backup(id)` / `backups(id)` | 手工备份 / 列出备份（新的在前） |
| `register(entry, *, source="")` | 运行期追加登记（重复 id 抛 `ValueError`） |
| `drop(id) -> bool` | 注销单份清单的登记（临时清单用完即删；内置清单下次刷新会被加回来）；已注销返回 `False` |

`ManifestData`：`path`、`version`、`kind`、`keys`、`meta`（除 items 外的顶层字段）、`items`（`key → 项`）、`raw`，以及
`get(key, default=None)`、`value(key, default=_MISSING)`、`list()`（保持文件顺序）、`filters(**filters)`。

`value(key)` **不传默认值时是严格的**：缺项或该项没有 `value` 字段都抛 `ManifestFormatError`（模块数据文件在导入期取常量，宁可启动失败也不能悄悄用错值）；传了 `default` 则退化为宽松取值。

### 5.2 插件：`app.sdk.manifest`

插件不直接 import core，统一走 SDK 面（实现由 `src/main.py` 通过 `plugin_service.bootstrap(MANIFEST_EXTENSION, manifest_kit)` 提供）：

```python
from app.sdk import manifest

manifest.ids(); manifest.describe("core.runtime"); manifest.load("core.runtime")
manifest.query("core.runtime"); manifest.diff("core.runtime", {...})
manifest.update("core.runtime", [{"key": "app_name", "value": "X"}])
manifest.backups("core.runtime"); manifest.reset("core.runtime")
manifest.register("my.plugin.data", "plugins/my.plugin/.data/x.json", kind="catalog", owner="my.plugin", source="my.plugin")
```

插件读自己 `.data/` 下的清单时，走 `ctx.data(name)` 读进来（协议 v2 固定 `.data/<name>.json`），再用三个纯函数取值，不必认识 `items` 的排布：

| 函数 | 用途 |
| --- | --- |
| `items_of(payload) -> list[dict]` | 取 `items` 里的记录列表（只保留带非空 `key` 的项）；不是清单返回 `[]` |
| `record_of(payload, key="") -> dict` | 取**一条**记录：按 `key` 精确匹配，找不到返回 `{}`；`key` 为空取第一条（查看器 / 编辑器用） |
| `value_of(payload, key, default=None)` | 取某项的 `value`（模块数据用） |

* 常量 `MANIFEST_EXTENSION = "app.manifest"`。
* 没有实现时安全退化：`ids()/entries()` 返回空元组，`describe()` 返回 `[]` / `{}`；`load()` 等动作抛 `SdkError("程序没有提供清单接口 app.manifest")`。

## 6. 写入、备份与恢复

* **原子写**：先写同目录的 `<名字>.<pid>.<序号>.part`，再 `os.replace` 覆盖目标；中途失败不留半截文件，也不留 `.part` 残留（异常时主动删除）。
* **备份**：每次变更前把旧文件按原字节复制到 `.configs/backups/manifest/<id>/<YYYYmmdd-HHMMSS>.json`（同一秒内重名自动加 `-2`、`-3`），只保留最近 `BACKUP_KEEP = 10` 份；删旧备份失败只记日志，不回滚变更。
* **恢复**：`reset(id)` 取最近一份备份覆盖，并重新校验；没有备份则报错，不静默成功。
* 备份与清单文件都是纯文本 JSON（缩进 2 空格，`orjson` 存在时用 `OPT_INDENT_2`，结尾补换行），保证可读、可 diff、可手工修。

## 7. 校验与可选依赖

* JSON 读写优先用 `orjson`，缺失时退回标准库 `json`；两者产出等价的结构化结果。
* 校验优先用 `fastjsonschema` + `src/app/core/manifest/schema/`；`fastjsonschema` 缺失时退化为「必须项 + 最小结构」校验（`_validate_minimal`），功能不中断。
* Schema 只有两份：`schema/manifest.json`（统一格式）与 `schema/legacy.json`（历史格式，只要求对象或数组）。按清单再拆 schema 收益低、维护成本高，字段级约束由各消费方的取值处保证。

## 8. 错误类型（`app.core.manifest.errors`）

| 异常 | 触发场景 |
| --- | --- |
| `ManifestError` | 基类；也用于「读不了文件」这类底层 IO 失败 |
| `ManifestNotFoundError` | 文件不存在、id 未登记 |
| `ManifestFormatError` | 不是合法 JSON、缺必须项、items 不是数组、项缺 `key`、键重复、缺 `value` 字段、对 legacy 调 `query/update/write` |
| `ManifestValidationError` | 结构没问题但不符合 JSON Schema（例如 `kind` 不在枚举里） |
| `ManifestBackupError` | 备份失败（源文件不存在）、没有备份却要 `reset` |

`app.core.runtime.module_data.ModuleDataError` 继承 `ManifestFormatError`，模块 JSON 的读取错误因此能被同一套 `except ManifestError` 捕获。

## 9. 测试与验收

* `tests/core/test_manifest.py`：内置登记表（17 条、16 份受管、登记表形状）、读写 / 查询 / 对照 / 变更 / 重置 / 备份裁剪、坏数据路径、legacy 路径、SDK 面无实现与有实现两种退化。
* `tests/core/test_core_module_data.py`：`core.runtime`、`core.plugins` 两份真实清单与模块导出常量一致，并逐份读取 / 校验 16 份受管清单（结构 + schema）。
* `tests/core/test_journal.py`：临时清单（`kind = "journal"`）的创建、进程重启后的重新登记、写入节流、终态清理、`active` 退回 `pending`、删除与注销登记。
* 批次门禁（批 I 收口时的实测）：`compileall` rc 0、`python -m unittest discover -s tests -t .` = **564 OK**、`pytest -q` = **564 passed**、`python scripts/selfcheck.py --layer data,services,pages,flows` = **149/149**、`python scripts/plugin_stubs.py --check` = 与清单一致。
