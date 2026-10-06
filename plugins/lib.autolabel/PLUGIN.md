# 自动标注共享库（`lib.autolabel`）

这是**内置共享库插件**（`builtin: true`），本身不注册页面、不写数据库，只把「自动打标签 /
打关键词」需要的纯逻辑与出厂默认交给上层插件：

| 上层插件 | 用途 | 用到的东西 |
| --- | --- | --- |
| `auto_tag.rule` | 只按规则轻量挂标签 | `rules`、`pipeline` |
| `auto_tag` | 规则 + 模型两套方案 | `rules`、`align`、`pipeline`、`controls` |
| `auto_keyword` | 同上的关键词版本 | 同上（`purpose="keyword"`） |

库不主动升级/卸载，三个上层插件是外部插件（`builtin: false`），可以各自独立升级、禁用；
它们通过 `ctx.require("autolabel.open")` 拿到同一个 `AutoLabelApi` 实例。

## 文件

| 文件 | 内容 |
| --- | --- |
| `rules.py` | 规则模型（`Rule` / `RuleSet`）、匹配引擎、出厂 + 用户规则合并 |
| `align.py` | 数据类型 ↔ 模型对齐表（`AlignRow` / `AlignTable` / `AlignBook`） |
| `pipeline.py` | 条目 → 批量请求 → 结果 → 名字列表（不含写库/界面） |
| `ui/controls.py` | 共用控件：规则/对齐行编辑对话框、进度面板、表格填充 |
| `.data/rules.json` | 出厂规则（25 条，覆盖常见后缀与类型），**统一清单格式**（`id: lib.autolabel.rules`，规则在 `items` 里） |
| `.data/align.json` | 出厂对齐表：`label` / `keyword` 两张，各 10 个数据类型，**统一清单格式**（`id: lib.autolabel.align`，每项 `key = <purpose>/<数据类型>`） |

用户改动只写两份文件，**只在和出厂不同时才写**（出厂规则更新后仍然生效）：

* 规则：`.configs/autolabel.rules.json`，载荷 `{"version": 1, "rules": [...], "hidden": [...]}`
  —— `rules` 只含用户改过/新增的规则，`hidden` 是用户删掉的出厂规则 key；
* 对齐表：`.configs/autolabel.align.json`，载荷 `{"version": 1, "label": [...], "keyword": [...]}`
  —— 每个用途只含与出厂不同的行。

## 规则（`rules.py`）

```python
rule = Rule(key="suffix.pdf", name="PDF 文档", field=FIELD_SUFFIX, op=OP_IN,
            pattern="pdf", tags=("文档", "PDF"))
rule.match(item)          # item 是 app.sdk.items.ItemRef
rule.problems             # ("正则表达式有问题：...",) —— 只提示，不抛异常
```

* `kind`：`match`（字段匹配直接挂标签）/ `prompt`（交给模型判断，见 `model_ready`）。
* `field`：`suffix` / `name` / `path` / `text` / `type`；`text` 用 `match(item, text=...)` 传入的正文。
* `op`：`is` / `in` / `contains` / `startswith` / `endswith` / `regex` / `glob`；
  `in` 的 `pattern` 用逗号、顿号、分号或空白分隔；`regex` 用 `re.search(..., re.IGNORECASE)`。
* `enabled=False` 或 `problems` 非空的规则不参与匹配（`usable` 为 False）。

`RuleSet` 是「出厂 − hidden + 用户覆盖」的合并视图：同名 key 用户覆盖出厂并**保留出厂位置**，
用户新增排在最后；`match_tags(item)` 返回命中的标签，`prompt_rules(item)` 返回要问模型的规则。

## 对齐表（`align.py`）

```python
row = AlignRow(datatype="IMAGE", template="qwen2.5-1.5b-instruct-gguf",
               align_template="clip-vit-base-patch32")
table = AlignTable(purpose=PURPOSE_LABEL, rows=(row,))
table.resolved("IMAGE", registered=registered)          # → 真正要调用的 model_id
table.alignment("IMAGE", registered=registered)         # → 对齐模型 model_id
table.missing(registered=registered)                    # → 还没着落的行（界面提示用）
```

`registered` 是 `{预定义方案 key: 已登记 model_id}`，来自
`registered_models()`（内部走 `dm_plugin.lib.model.api.templates()`，每行取 `id` / `registered_id`）；`AlignRow.model_id` 优先于
`template`。界面上的「一键补全」= `dm_plugin.lib.model.api.create_from_template(key)` 登记草稿并写回对齐表，
缺权重时弹一个确认框、确认后 `dm_plugin.lib.model.api.download_model(model_id)` 排队下载；缺什么由
`dm_plugin.lib.model.api.requirements(model_id)` 提示（运行环境仍去模型页装）。
`ui/controls.py` 的对齐表工具：`preset_align_text(row)`（出厂方案的中文文案，供提示行用）、
`align_rows(rows, *, registered=None, factory=None, names=None)` / `fill_align_table(...)`（5 列：
数据类型 / 方案 / 主模型 / 对齐模型 / 状态；「方案」列三态——`启用`（这一行跟随系统）、`自定义`（自己挑过）、`已停用`（`enabled=False` 时优先显示），
状态列由 `AlignRow.status_text()` 给——按这一行自己的主模型与对齐模型有没有登记写成
`对齐模型未登记：<方案 key>；主模型未登记：<方案 key>`，都登记了才是「就绪」，
判据就是这一行自己的设置 `follows = bool(row.use_preset)`——“内容与出厂相同”不能当跟随（用户取消勾选后
选的往往就是系统方案里的同一个模型）；`names` 是 `{model_id: 登记时起的名字}`，主模型 / 对齐模型两列优先显示这个名字
（与编辑框里看到的完全一致）；`factory` 是 `{数据类型: 出厂 AlignRow}`，只作参考）。「重置」用
`AlignBook.reset_row(purpose, datatype)`（还原一行）与 `AlignBook.clear_user()`（整表还原成出厂）。

**「跟随系统方案」这个选项**：`AlignRow.use_preset: bool` 表示这一行是否跟随出厂方案——为真时 `AlignBook.table()`
直接把出厂行的 `template` / `align_template` 换上，并清掉用户自己填的 `model_id` / `align_model`（出厂以后换模型，这些行跟着换），
`payload` 写的是**整份清单**：每个用途下把所有数据类型的当前设置都写进去（见下），刷新时直接铺到表里。界面上不再有单独的「使用系统方案」按钮：点「设置对齐」打开的编辑框里第一个就是
「启用系统方案（强制使用系统规定的模型与对齐模型）」复选框——勾上时模型与对齐模型两组只读、显示系统值，取消勾选才出现两个可选下拉
（`已登记` 组 = 本地下过的模型、按登记时的名字显示；只有**还没登记**的出厂方案才作为 `系统方案` 组出现——
已经在 `registered`（`{方案 key: 已登记 model_id}`）里的模板 key 不再重复。勾选时那两行只读值也显示**登记名**
（如 `系统方案：BLIP 图像描述（base）`），没登记才写 `<方案 key>（未登记）`，这样「跟随系统」「自己挑」「表格」
三处说的是同一个东西。
`use_preset` 缺省为假，老的用户文件读进来不受影响。

**清单（`AlignBook.payload` / `from_payload`）**：用户文件 `.configs/autolabel.align.json` 存的是
**每个用途下全部数据类型的当前设置**（`{"version": 1, "label": [...], "keyword": [...]}`，`payload` 直接取
`self.table(purpose).rows`，所以「表格看到的就是清单里的」）。`from_payload` 不再按「与出厂不同」过滤——
取消勾选「启用系统方案」但内容恰好与出厂相同的行同样留在清单里。清单里**没写到**的数据类型 = 用户没单独设过，
`table()` 按「跟随系统方案」（`use_preset=True`）补齐，方案列因此显示「启用」；`table()` 对 `use_preset=True` 的行
仍取出厂 `template` / `align_template`（出厂以后换模型这些行跟着换）。

## 批处理管线（`pipeline.py`）

```python
plan_obj = plan(items, purpose=PURPOSE_LABEL, table=book.table(PURPOSE_LABEL),
                registered=registered_models())
results = run(plan_obj, on_progress=lambda done, total: ..., cancel=lambda: stop)
names = collect(plan_obj, results, minimum=1, maximum=max_tags, known=known_tags)
pending_keys(plan_obj, results)     # 取消后还没出结果的条目
```

* `plan()` 把没有 id、没有可用模型的条目放进 `skipped`（附可显示的中文原因）；
* `run()` 只是转交 `dm_plugin.lib.model.api.run_batch()`（软依赖：模型工具库没启用时会在那里报错）；
* `parse_names()` 解析模型输出（JSON 数组、项目符号、编号、逗号/顿号/换行、``` 围栏）；
* `clamp()` 负责数量上下限与「只留库里已有的名字」，`merge_names()` 合并规则与模型结果。
* `align_texts(items, *, table=None, registered=None, purpose=PURPOSE_LABEL, on_problem=None)`：只有
  `ALIGN_TASKS = {"IMAGE": "caption", "AUDIO": "asr"}` 这两种类型会先让对齐模型读一遍（视频不在里面：抽帧要额外解码器，而系统方案给视频配的 CLIP 是向量模型、出不了句子，硬发 `caption` 只会失败）
  （组 `BatchRequest(task=..., payload={"input": 文件路径}, key=条目 id, model_id=对齐模型)`；文件路径取条目快照的 `abs_path`，没有才退回 `file_path`，并且先用可注入的 `is_file()` 核对文件在盘上——不在就报「文件不在盘上」并跳过，不把相对路径丢给 worker），结果按条目 id
  返回 `{id: 文本}`；`default_prompt(..., align_text=...)` 把它作为「对齐信息」段落接进提示词（截断 1200 字）。
  纯文本类型（系统方案配的是向量模型，如 `all-minilm-l6-v2`）不参与提示词，这时每种类型只通过 `on_problem` 说明一次
  「只出向量、不参与提示词，本批直接用主模型」；对齐模型没登记 / 条目没有路径 / `run_batch` 抛错 / 单条失败也都会经
  `on_problem` 报出来，失败一律不阻断主流程（返回 `{}`）。上层插件把 `on_problem` 接到日志
  （`self._ctx.log.info("对齐模型：{}", message)`），用户就能在日志里看到对齐模型为什么没参与。

## 扩展接口

`setup()` 里注册 `autolabel.open`，上层插件：

```python
api = ctx.require("autolabel.open")
rule_set = api.rules()          # RuleSet；reload=True 强制重读文件
api.save_rules(rule_set)        # 只写与出厂不同的部分
book = api.align()
api.save_align(book)
api.registered()                # {方案 key: 已登记 model_id}
api.path_text                   # 界面上显示两个用户文件的名字
```

## 界面约定

`ui/controls.py` 只用 `dm_plugin.builtin.lib.ui.plugin` 的控件工厂（外加 PyQt6 与
`FluentIcon`），不直接使用 `qfluentwidgets` 的其它控件——与模型工具库同一条红线，
由自检 `autolabel_ui_via_tool_library` 守着。
