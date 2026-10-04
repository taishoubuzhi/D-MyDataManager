# 模型工具库（builtin.lib.model）

模型插件的公共工具箱：模型登记表、下载器、运行环境（独立 venv）、推理适配器与 worker 子进程协议，外加「模型」管理页。
程序本体只留一个扩展接口 `model.open` 与调度门面（`app.sdk.models`），**不实现任何模型调用、也不保存模型数据**：
没启用本插件时 `list_models()` 返回空、`acquire()` / `invoke()` 直接抛 `SdkError`。

权重与运行环境全部落在**资源文件夹**（`.resources/models/`），插件自己的 `data/` 只放只读模板 —— 插件覆盖安装时插件目录会被整个删掉重建。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：id、名称、`class = ModelLibraryPlugin`，depends `builtin.lib.ui`，provides `model.open`，data 三项模板 |
| plugin.py | 库模块（也是入口文件）：ModelLibraryPlugin（注册 `model.open` 接口与「模型」页）、模块级 `model_manager`、`data_templates(ctx, key)` |
| constants.py | 常量：页面键 / 标题 / 图标 / 排序、模型类型、状态与标签、任务与能力、能力→后端映射、适配器名、默认值 |
| record.py | `ModelRecord`（记录）与 `slugify()` / `make_id()`：id 一旦创建不可改，改名只改 `name` |
| registry.py | `ModelRegistry`：读写 `.resources/models/registry.json`（`load()` / `save()` / `all()` / `ids()` / `get()` / `find()` / `add()` / `update()` / `remove()` / `clear()`） |
| settings.py | `ModelSettings`（下载 / 运行 / 密钥三段）与 `load_settings()` / `mask_secret()`；设置文件是 `.configs/models.json` |
| paths.py | 路径：registry.json、`local/<模型id>/`、`download/<模型id>/`、`logs/`、`runtime/<profile>/venv`、`.configs/models.json` |
| manager.py | `ModelManager` 与 `Lease`：加载 / 卸载 / 引用计数 / 常驻上限 LRU / 空闲卸载 / 单飞排队 |
| provider.py | 扩展接口实现 `ModelOpenApi`：对外成员都是薄转发，转发给 `manager` |
| adapters/ | 适配器：`base.py`（`Adapter` 抽象与 `AdapterError`）、`http.py`（OpenAI 兼容 / Ollama / llama-server）、`worker.py`（独立进程）、`inprocess.py`（默认拒绝） |
| download/ | 下载器：`downloader.py`（任务与队列、续传、镜像回退、sha256、原子落盘）、`hub.py`（HF 仓库探测与地址展开） |
| runtime/ | 运行环境：`profiles/profile_of/venv_dir/python_path/installed/marker_path/requirements_path/ensure/ensure_system/discard/uninstall/uninstall_system/package_versions/log_file/has_dir/clear_logs/Control/check_wheels/install_wheels`；`installed()` 要求 `runtime/<id>/installed.json`（装成功才写），暂停 / 中断留下的半成品不会被当成已安装；`Control` 是页面持有的停止开关（`cancel()` / `pause()` 直接收掉绑定的 pip 子进程，`ensure*` 收 `control=`）；`install_wheels()` 用本地 `.whl` 离线装（`check_wheels()` 先校验文件名里的 python / 平台） |
| worker/ | worker 子进程：`worker_main.py`（JSON-Lines over stdio 的服务端）与后端实现 |
| ui/ | 界面：`cards.py`（`ModelCard`）、`dialogs.py`（模板选择 / 新建本地 / 新建外部）、`page.py`（`ModelPage`） |

## 暴露的库

库模块固定写成入口 `plugin.py`（协议规定：一个插件对其他插件的公开面只有 plugin.py）。两种取法：

    from dm_plugin.builtin.lib.model.plugin import ModelRecord, ModelManager   # 推荐：静态导入（要先 depends）
    library("builtin.lib.model", "model")                                      # 兜底：运行时取

- `ModelRecord` / `slugify` / `make_id` / `KIND_LOCAL` / `KIND_EXTERNAL` / `STATE_READY` / …
- `ModelRegistry` / `ModelManager` / `Lease` / `model_manager`（库自己的管理器单例）
- `ModelOpenApi` / `MODEL_EXTENSION` / `data_templates(ctx, key)`
- `ModelCard` / `ModelPage`（界面，宿主没有 `app.ui` 时不要用）

各具体后端仍在 `adapters/` / `worker/` 里，**不**进 `__all__`：外部插件只用 `model.open` 接口，不直接碰适配器。

## 扩展接口 model.open

程序侧 `app.sdk.models` 的 `list_models()` / `model_by_id()` / `acquire()` / `invoke()` 都先取
`extension_registry.provider("model.open")`（`MODEL_EXTENSION = "model.open"` 定义在 `app.sdk.models`）。
`ModelOpenApi` 的成员：

| 分组 | 成员 |
| --- | --- |
| 查询 | `list_models(kind="", capability="")`、`model_by_id(model_id)`、`capabilities()`、`loaded()`、`running_info()` |
| 取用 | `acquire(model_id="", capability="", task="", timeout=None)`、`invoke(model_id="", capability="", task="", payload=None, *, stream=False, timeout=None)`、`unload(model_id)`、`unload_all()`、`sweep_idle()` |
| 登记 | `add_local(name, *, capabilities, description, files, source, runtime)`、`add_external(name, *, base_url, model, capabilities, description, adapter, api_key_ref, headers, params)`、`update(...)`、`remove(model_id, *, delete_files=False)`、`scan_directory(path, *, capabilities=(), recursive=True)` |
| 持久化 | `save()`、`reload()` |

调用方拿到的是一份**租约**（`Lease`，也支持 `with`）：

```python
from app.sdk import models

lease = models.acquire(capability="embedding", task="embedding", timeout=30)
try:
    vectors = lease.invoke("embedding", {"input": ["你好"]})
finally:
    lease.close()          # 只还引用计数；卸载由常驻上限 / 空闲策略决定
```

- `acquire()` 同时接受 `model_id` 与 `capability`，至少给一个；同一模型加载中会**排队**，超时抛 `ModelBusyError`。
- 记录的 `id` 里已经写明归属（`local/<名字>` / `api/<名字>`），其他插件对接时按 id 或 capability 取用。
- 状态：`未填充`（草稿）/ `下载中` / `已就绪` / `加载中` / `错误`；本地模型没就绪时 `acquire()` 抛 `ModelError`。

## 权限与安全

- 插件被禁用 / 卸载时 `teardown()` 调 `model_manager.shutdown()`（停掉所有 worker 子进程）并清空登记表。
- API Key 存在 `.configs/models.json` 的 `secrets` 段里，登记表只保存**引用名**，日志与界面一律打码（`mask_secret()`）。
- 加载模型只在**用户点过「加载」**、或其它插件通过 `acquire()` 请求时才发生，默认不常驻；常驻上限默认 1，超出按最久没用到的顺序卸载 —— 卸载等于杀进程，显存会真的还回去。

## 数据布局

| 位置 | 内容 |
| --- | --- |
| `.resources/models/registry.json` | 模型登记表（本地 + 外部） |
| `.resources/models/local/<模型id>/` | 本地权重（扫描登记的文件可以留在用户自己的目录，记录里用 `source.path` 指向它） |
| `.resources/models/download/<模型id>/` | 下载中的 `.part` 与断点信息 |
| `.resources/models/logs/` | worker 与运行环境安装日志；模型日志一个模型一份（`<slug>.log`，每次运行重写、删模型连日志删） |
| `.resources/models/runtime/<profile>/venv` | 每个运行环境一套独立虚拟环境 |
| `.configs/models.json` | 插件设置：下载源（模型）（`download_source`：HuggingFace官方 / HF-Mirror镜像 / 自定义）、自定义下载列表（`custom_urls`）、代理、并发、常驻上限、空闲卸载秒数、设备、密钥、安装源（`pip_mirror` / `pip_mirror_custom`）、下载源（GitHub）（`github_source`：Github官方 / ghproxy镜像 / gh-proxy镜像 / ghfast镜像 / 自定义网址，`github_custom` 存自定义前缀） |

## 运行时体系

三种适配器（记录里用 `api.adapter` / `runtime.adapter` 选择）：

1. **http 族**（外部模型）：`openai_compat`（`/v1/chat/completions`、`/v1/embeddings`，探活 `/v1/models`）、`ollama`（`/api/chat`、`/api/embeddings`，探活 `/api/tags`，默认 `http://127.0.0.1:11434`）、`llama_server`（探活 `/health`，默认 `http://127.0.0.1:8080`）。
2. **worker 族**（本地模型，推荐）：独立 Python 进程，JSON-Lines over stdio；一行一个请求 `{"id":1,"op":"load","payload":{...}}`，一行一个响应 `{"id":1,"ok":true,...}`，流式数据是 `{"id":1,"stream":"delta","text":"…"}`；op 取 `ping` / `info` / `load` / `invoke` / `cancel` / `unload`。worker 按 backend 分派（`llama_cpp` / `transformers` / `sentence_transformers` / `faster_whisper` / `piper` / `diffusers` / `rapidocr` / `custom`），5 秒没响应判僵死、杀进程并置 `错误`；Windows 上子进程用 `CREATE_NO_WINDOW`；worker 的 stdout / stderr 一律强制 UTF-8，中文日志不会因为目标环境是 GBK 控制台而变成乱码。
3. **inprocess 族**：默认**拒绝**（会把模型挂进程序进程，难以释放），只留一个明确的报错。

能力 → 后端 → 运行环境 profile 的对应写在 `constants.py` 的 `CAPABILITY_BACKENDS` 与 `data/runtime_profiles.json`：
`chat`/`completion` → `llama_cpp`（GGUF）；`embedding`/`rerank` → `sentence_transformers`；`vision`/`classify` → `transformers`；
`asr` → `faster_whisper`；`tts` → `piper`；`image` → `diffusers`；`ocr` → `rapidocr`。
清单里一共 13 个 profile：每个 CPU 版都有一个 `-gpu` 变体（`llama-cpp-gpu` / `transformers-gpu` /
`sentence-transformers-gpu` / `whisper-gpu` / `diffusers-gpu` / `rapidocr-gpu`），外加 `piper`。
带 `-gpu` 的除了把 `extra_index` 指向 CUDA 轮子索引（`https://download.pytorch.org/whl/cu126` 等），
还要把 **CUDA 12 运行库**装进同一套 venv：`nvidia-cuda-runtime-cu12`（`cudart64_12.dll`）、
`nvidia-cublas-cu12`、`nvidia-cudnn-cu12`（PyPI 上的 `nvidia-*` 轮子）。`llama-cpp-gpu` /
`whisper-gpu` / `rapidocr-gpu` 的清单里都写着这几个包 —— CUDA 版 llama.cpp 的 `llama.dll` 链的是
`cudart64_12.dll`，缺了它 `import llama_cpp` 会直接报「Could not find module…」；worker 的
`_prepare_dll_dirs()` 会把 venv 里 `site-packages/nvidia/*/bin` 加进 DLL 搜索路径，
`_require_llama_cpp()` 再把错误补成「要 CUDA 12 的运行库…或者改用 CPU 版运行环境 `llama-cpp`」。
**torch 的 CUDA 版自带 CPU 内核，不需要装两份**；CUDA 轮的版本号带本地标记（`+cu126`），
按 PEP 440 排在 PyPI 同版本 CPU 轮之前，pip 会自动选中。`rapidocr-gpu` 用 `onnxruntime-gpu` 取代
`onnxruntime`（CPU / GPU 只有一个 onnxruntime 发行包，别同时装）；CUDA 运行库也只装进自己那套
venv，CPU profile 不会跟着变重。**写 CPU 版 profile 的模型，在只装了 `-gpu` 版的机器上也能跑**：
`runtime.resolve_id()` 找不到精确的那套时会看 `twin_ids()`（`llama-cpp` ↔ `llama-cpp-gpu`），
worker 适配器用解析后的解释器；两边都没装时才报「运行环境 X 尚未安装（已经装的：…）」并把
手头装了哪些环境列出来。

后端实现状态：worker 认识全部 8 个后端 —— `llama_cpp`（chat / completion）、`transformers`（chat / completion，视觉与分类走它的 `pipeline` 任务）、`sentence_transformers`（embedding / rerank）、`faster_whisper`（asr）、`piper`（tts）、`diffusers`（image）、`rapidocr`（ocr）、`custom`（自定义入口）。其中 `rapidocr` 同时支持 1.x（`rapidocr-onnxruntime`，平铺 kwargs）与 3.x（`rapidocr`，构造参数是 `params` 点号键；没装 `onnxruntime` 时自动退回 `torch` 引擎）。后四个后端用假模块真跑过 worker 帧协议（`tests/test_model_runtime.py` 的 `WorkerBackendCase`），但**真实模型效果还没实测**：需要在「运行环境」区装好 faster-whisper / piper-tts / diffusers / rapidocr 并用真权重跑一遍才能确认。缺依赖时报「缺少依赖 <pip 名>，请在运行环境页安装」。

**请求字段不进 `params`**：`messages` / `prompt` / `input` / `task` / `stream` 由调用处显式传给后端库（`create_chat_completion(messages=…)`），所以适配器 `WorkerAdapter._body()` 把顶层 `messages` 当请求内容放进 `body["input"]`、不再 `setdefault` 进 `params`，worker 侧 `_options(payload)` 再把这些键从 `params` 里 pop 掉。早先两层都漏：界面按 `{"messages": [...]}` 传对话，适配器把整个负载塞进 `params`，worker 又 `create_chat_completion(messages=..., **options)`，结果是模型刚加载完就 `TypeError: got multiple values for keyword argument 'messages'` 然后卸载（回归用例：`tests/test_model_runtime.py` 的 `WorkerRequestShapeCase`）。

推理设备：设置页的下拉打开时会后台探测**当前机器真实可用**的设备（`runtime/probe.py` 起子进程，拿到的设备名带真实型号：CPU 走注册表 `ProcessorNameString`，显卡走 `nvidia-smi` + Windows 显示适配器注册表键 `…\Class\{4d36e968-…}`，再看解释器里的 torch 到底能不能用它），并保留「自动选择（按可用性挑最快的）」；探测起的是程序解释器和**已安装**的运行环境 venv，取第一个能报出设备的。下拉只列当前解释器真能用的设备（`cuda:N` / `mps` / `xpu:N` + CPU），用不了的（CPU 版 torch 下的 NVIDIA 显卡、Intel 核显等）不塞进下拉，而是写在下拉下面的说明文字里，免得选了却悄悄退 CPU；探测失败也至少给一个 CPU。worker 侧把这串设备名翻成后端认识的形式：`llama_cpp` 用 `main_gpu`、`transformers` / `diffusers` 用 `torch.device`、`sentence_transformers` 用 `cuda:N`、`faster_whisper` 用 `device` + `device_index`；llama.cpp 还要层数——设备是 `cpu` 时强制 `n_gpu_layers=0`，设备是 GPU 而模型参数里没写层数时默认 `-1`（全部层上卡，写了就按写的），统一由 `worker/_llama_kwargs()` 出，免得装了 `-gpu` 环境却悄悄全走 CPU；选了本机没有的设备会退回 CPU 而不是报错。下拉只决定候选，真正能不能跑仍以后端自身能力为准。

## 下载与导入

- 权重下载（m29822 之后的形态）：模板 `source["files"]` 给多文件清单（llama.cpp 分片、transformers/tokenizer 成套、whisper、向量化模型），一个文件一个 `DownloadJob`；单文件时总量回落到 `record.size_bytes`。每次入队都往控制台写一行 `[下载权重] <文件名> ← <实际地址>（存到 <目标路径>）`。
- **权重已就绪的本地模型**：卡片按钮是「更换权重」而不是「下载」——`ReplaceWeightsDialog` 可以选磁盘上已有的文件（`_weights_fit` 按 `record.runtime["backend"]` 的后缀集校验，`json/txt/model/vocab/…` 这类 sidecar 一律接受；不匹配时 `_template_for_suffixes` 从 `model_list.json` 找同后缀档的模板问用户是否改记录，找不到就确认后清空），也可以按仓库信息重新下载（`_download(record, replace=True)`，先 `_drop_weights()`）。扫描登记进来的外部目录只登记文件、不删不改。
- **删除模型不再连权重一起删**：`DeleteModelDialog` 只有说明，`api.remove(id, delete_files=False)`；要清垃圾用工具条的「清理未使用的权重」（`_cleanup_candidates()` 列 `local/` 下没登记 + `download/` 下没在跑任务的目录，勾选后删除，不勾不动作）。

- 地址模板 `{repo}` / `{file}` / `{revision}` 支持当前下载源给出的地址，按顺序回退（401 / 403 不回退，直接报错）；`Range` 续传配合 `.part` + `.meta.json`，完成后校验 sha256 并原子改名。主地址由 `ModelSettings.download_base` 决定（HuggingFace官方 / `mirrors[0]` / 自定义列表第一项），回退顺序由 `download_mirrors` 给出。
- `DownloadManager(settings, on_change=None, max_workers=None)`：`enqueue(model_id, urls, target, *, sha256="", total_bytes=0, label="")`、`jobs()`（返回 tuple）/ `find()` / `active()` / `cancel_all()` / `pause_all()`（返回条数）/ `resume_leftovers()`（返回条数）/ `forget(job_id)` / `clear_finished()` / `shutdown(wait=3.0)`；任务支持暂停 / 继续 / 取消，`DownloadJob.phase`（连接中 / 接收数据 / 校验中 / 重试中）与 `DownloadJob.note`（正在暂停… / 正在取消…）给页面当「正在干什么」用，`detail_label` 取 `note or phase`。`forget` / `clear_finished` 只清已结束（done / error / cancelled）的记录，**不删磁盘文件**（成品与 `.part` / `.meta.json` 都留着），清掉后重新入队照样续传。**取消**（含「先暂停再取消」）走 `drop_part(model_id, target)`：删 `.part` + `.meta.json`，再用 `prune_download_dir(model_id)` 收掉空了的 `download/<模型id>/` 与空的 `download/`（只删空目录，别的模型还在就留着）；`done` 之后同样收掉自己那个空目录。**暂停只停不动文件**，`shutdown()`（关程序）也按暂停收尾、不删断点——关掉程序不等于放弃下载。
- 断点信息即「恢复清单」：`remember_job(path, job)` 往 `.meta.json` 里写 `model_id` / `urls` / `sha256` / `label` / `target` / `total_bytes`（`_download()` 开头调用，`_fetch()` 写 etag / last_modified 时合并而不是覆盖）。重进页面时 `ModelPage._recover_downloads()` 调 `resume_leftovers()`：把盘上带来源信息的 `.part` 原样排回队列续传（目标已在队列里就跳过），没有 meta 的老 `.part` 不动。
- 页面侧：新建/编辑弹窗的表单装在 `SingleDirectionScrollArea` 里（`ui/dialogs.py` 的 `_form_area`），窗口矮就滚动而不是把能力复选框压到重叠；选模板后手改任何字段会自动回到「（不使用模板）」。下载行显示任务全名（折行 + 悬停）、`已下载 x / 共 y（nn%）` + 阶段 + 失败原因 + 速度 + 剩余时间，总量未知时进度条走忙等，另有`移除记录`与`清空已结束`。
- 也支持把权重目录直接拖进窗口、或用「扫描目录」/「选择权重文件」（单文件会先复制进插件权重目录）就地把 `*.gguf` / `config.json + safetensors` / `*.onnx` / `*.bin` 登记成模型。

## 依赖

- builtin.lib.ui（界面工具库：页面模板、控件工厂与弹窗外壳）—— 宿主没有 `app.ui` 时只记一条 warning，「模型」页不注册，接口照常可用。**「模型」页的界面完全从这套工具库搭**（`plugins/builtin.lib.model/ui/{page,dialogs,cards}.py` 只 `from dm_plugin.builtin.lib.ui.plugin import …`），qfluentwidgets 只用 `FluentIcon` 拿图标；页面缺的控件（进度条、数字框、单选、多行文本、弹窗外壳 `FormDialog`、可点卡片 `ClickCard`、`widget_column` / `widget_row` / `check_grid` / `scroll_area` / `field` 等）一律补进工具库，通用卡片这类跨插件控件放在 `app.sdk.ui`（`ClickCard` 走延迟导入，导入 SDK 不拉起 Qt）。`model_ui_via_tool_library` 自检静态守着这条规矩。
- 主程序环境不需要任何模型库；重依赖（torch、llama-cpp-python 等）默认**只装进运行环境自己的 venv**，由用户在「运行环境」区确认后安装（永不静默安装），失败可以重建或整个目录卸载。若用户明确打开高级选项，也可以装进程序自己的环境（`.venv`），见下条。
- 如果依赖是装进程序自己的 Python 环境（`.venv`），要在「模型」页设置里勾选「允许把依赖装进程序自己的 Python 环境（高级选项）」。**不勾**（默认）：每套运行环境在自己的 `runtime/<profile>/venv` 里装依赖，程序环境保持干净，worker 用那个 venv 的解释器跑。**勾上**：worker 直接用程序自己的解释器与已装的包，不再需要 profile venv；运行环境区会改显示成「使用程序环境（已装）」「使用程序环境（缺 torch 等 2 个包）」或「使用程序环境（检测中…）」（行内只放短话，完整依赖清单挂在悬停提示上；后台用 `probe.missing_program_group` 把所有 profile 合并成**一个**子进程一次问完，13 套约 0.15 s，既不卡界面也不会刷一屏日志——整批只在控制台留一条汇总；「装没装」先查 pip 发行版元数据（`importlib.metadata`），查不到再看 import 名，所以 `onnxruntime-gpu`（模块名其实叫 `onnxruntime`）和 `nvidia-*` 这种「有发行版、没有同名模块」的包不会被误报成缺），代价是模型依赖混进程序环境、出问题要自己收拾。勾选/取消**立刻生效并存盘**（不用再点「保存设置」），运行环境区同一瞬间刷新；勾上以后在运行环境区点「安装」会**先弹两次确认**，确认后走 `runtime.ensure_system()` 把清单包装进程序解释器（`sys.executable -m pip install -r runtime/system/requirements-<profile>.txt`，输出在 `logs/runtime-system-<profile>.log`），不勾则照旧装进 profile 自己的 venv；**卸载也按同一个开关走**——程序环境模式用 `runtime.uninstall_system()` 从程序解释器 `pip uninstall -y` 这些包（一次确认），隔离模式才删 profile 目录。行内的「安装 / 卸载 / 日志」按钮跟着状态走：装好了「安装」禁用、「卸载」可用，没装（或目录不在）反过来，「日志」只在有日志文件时可点。**安装中 / 已暂停的行例外**：安装开始时那一行变成「安装中… n s · 最后一行 pip 输出」+ 忙等进度条，按钮是`暂停` / `取消` / `日志`；暂停后显示「已暂停」、给`继续` / `取消`。`ensure` / `ensure_system` / `uninstall_system` 都收 `should_pause=`（与 `should_cancel=` 并列），命中就停掉 pip 进程并抛 `RuntimeStopped`（`paused=True` 是暂停、`False` 是取消）；暂停留着 pip 缓存与半成品 venv 供「继续」续装，取消走 `runtime.discard(id, logs=True)` 清干净回到未安装。安装源是设置里的下拉（官方 PyPI / 清华 / 阿里云 / 中科大 / 自定义地址，存 `pip_mirror` + `pip_mirror_custom`），`ModelSettings.index_url` 拼给 pip 的 `--index-url`；profile 自己带 `index_url` 的（CUDA 轮子索引）仍以 profile 为准。
- **异常退出后接着装**：开工前先写「正在安装」标记（`pending_path(id)` = `runtime/<id>/installing.json`，程序环境模式是 `runtime/system/installing-<id>.json`，内容 `started_at` / `pid` / `packages`），只有装成功或清单为空两个出口才 `_clear_pending()`；`interrupted(id)` = 「标记在、完成标记不在」。页面把这种环境显示成「未完成（上次安装中断）」（判断排在「手工放置」前面，免得半个 venv 被当成装好了），点「安装」重跑 `ensure()` 就从 pip 缓存续装；`discard()` 会连标记一起清，`clear_pending(id, system=True)` 给卸载 / 取消收尾。另外 `has_dir(id)` 看 `runtime/<id>/` 里有没有东西：手工拷进去的环境显示成「已安装（手工放置，没找到完成标记）」并让「卸载」可用，删掉之后「日志」立刻不可点。
- **一键补全与设备提示**：运行环境列表上方说明「GPU 版本自带 CPU 版本，不需要重复下载…」，工具条`一键补全`按设备挑版本（有 CUDA 用 `-gpu`，否则 CPU 版；`-gpu` 条目自身跳过）只装缺的那些（`_complete_plan` / `_pump_complete_queue`），点下去先弹 `CompleteRuntimesDialog` 让用户选「并发安装」（默认）还是「挨个安装」，提示文字写明还缺几个；**并发**——`_pump_complete_queue()` 用 `while` 把队列一次抽干，每个 profile 各开一条 `model-page-task` 线程、互不等待，所以不再「有安装在跑就拒绝补全」；**挨个**——一次只开一个，前一个收尾时 `cleanup()` 再调 `_pump_complete_queue()` 接下一个（这一轮的选择记在 `_complete_sequential`，弹窗取消就什么都不装）；同一个 profile 已经在装时 `_install_profile()` 开头的守卫直接跳过，不会重复开线程。设备探测出「没有 GPU」却要装 `-gpu` 版会先警告「设备没有 GPU，GPU 版用不了」，有 GPU 却选了 CPU 版会说明并问是否改装 `-gpu` 版（选「否」可以继续装 CPU 版）。
- **停止安装不靠轮询**：页面 `_install_control(key)` 返回 `runtime.Control`（`cancel()` / `pause()` 立刻置位并收掉 `_stream` 绑定的 pip 进程），行内文案随之写「正在暂停…」/「正在取消…」并锁住那两个按钮，不会出现「点了取消还显示安装中」；卸载（隔离与程序环境两种模式）都调 `runtime.clear_logs(id, system=True)` 把日志一起删掉，删完「日志」按钮立刻不可点。
- **本地 whl 安装**：运行环境每行有「本地 whl…」按钮（`_install_wheels`）——`QFileDialog` 多选 `.whl` 后先 `runtime.check_wheels()` 校验（文件名里的 python / abi / 平台对不对得上，`abi3`、`any` 都认），确认后 `runtime.install_wheels()` 执行 `pip install <whl…> + 清单里没被这些 whl 覆盖的包`（`_wheel_dist_names()` / `_dist_name()` 按 wheel 文件名里的发行版名比对）——`-gpu` 环境的 `nvidia-*-cu12` 就靠这一步补齐（它们不在 GitHub 上、能从镜像拉），装完写完成标记，离线 / 内网可用；pip 失败时 `_network_hint()` 看一眼日志，命中「github.com + 超时」就补一句「轮子托管在 github.com 上、当前网络连不上，可以用『本地 whl…』装离线轮子」。**来源写清楚**：控制台先留一条「本地 whl 安装 ← 本地文件：a.whl、b.whl…」，`_install_profile` 的 `_console_stage` 与安装行的悬停提示都写「来源：本地文件：…」（不再拿 pip 源冒充）；**装好之后「本地 whl…」按钮变灰**（`_sync_runtime_buttons` 也管 `whl`：只在「未安装且目录不在」时可点），要换 wheel 先「卸载」。
- **下载源（GitHub）**：设置里的「下载源（GitHub）」是五个档——`official`（只用原地址）、三个独立镜像档 `ghproxy` / `gh_proxy` / `ghfast`（`GITHUB_MIRRORS`，下拉里分别显示「ghproxy镜像」「gh-proxy镜像」「ghfast镜像」）、`custom`（「自定义网址」，用户填前缀），除官方档外最后都还挂一条官方直连兜底（`settings.github_prefixes(source, custom)`）；老版本写下的单档 `mirror` 读回来由 `normalize_github_source()` 迁移成 `ghproxy`（`LEGACY_GITHUB_SOURCES`）。`settings.github_urls(url)` 把 GitHub 直链展开成 `<前缀>/<原地址>`（`is_github_url()` 认 `github.com` / `codeload` / `raw.githubusercontent.com` 等，含子域），非 GitHub 地址原样返回。两处都走它：①模型下载队列 `download/downloader.py` 的 `enqueue()` 先把每个地址展开再按序去重；②pip 装运行环境时 `runtime._github_retry()` 从失败日志里认出 GitHub 直链（`github_asset_urls()`，只认 `.whl` / `.zip` / `.tar.gz`），换成镜像地址把同一条命令重跑一次（`ensure()` / `ensure_system()` / `install_wheels()` 都收 `github_prefixes=`，官方档换不出地址就等于不重试）；`_network_hint()` 的失败提示改说「去设置里把「下载源（GitHub）」换成镜像，或用本地 whl」。
- **模型日志一个模型一份**：worker 的 stderr 由 `WorkerAdapter._pump_stderr` 写进 `logs_dir()/<slug>.log`（`paths.model_log_file()`）；`_make_log_path()` 会先 `clear_model_logs()` 清掉老版本按时间戳攒的 `<slug>-20260101-010101.log`，`start()` 再 `_write_log_header()` 用一行表头把文件重写，所以一个模型永远只有最新一份。卡片上有「日志」按钮（`ModelCard.set_record(..., has_log=…)`，有日志才显示），点了走 `page._show_log()` → `ModelLogDialog` 只读看最新那份；删模型（`_delete`）调 `clear_model_logs()`、卸载运行环境调 `runtime.clear_logs()`，日志都跟着一起删。

- **卡片按钮不跟 `ready` 走**：本地模型加载失败会把 `state` 写成 `error`，此前「加载 / 测试」跟着 `ready` 一起消失、只能点「刷新」重读 `registry.json` 才回来；现在 `_apply_actions` 改看「有没有权重」——有权限就能点「加载」、本地模型也常显「测试」，非就绪时提示「上次没跑起来的话，改好设置再点一次」，出错后可直接重试。

## 谁在用

- 程序本体：`app.sdk.models` 门面 + 插件页的「模型」页面（`plugin.model_manager` 路由）。
- 其他插件：`ctx.require("model.open")` 或直接 `import app.sdk.models`，按 `model_id` / `capability` 取租约。

## 验证

- 单测：`tests/test_model_registry.py`（登记表 / 记录 / 调度 / 下载源（GitHub）的设置与取址顺序）、`tests/test_model_download.py`（续传 / 校验 / 镜像回退 / 按「GitHub 下载源」展开地址 / 阶段与动作提示 / `forget` 与 `clear_finished` / 取消清断点与暂停保留）、`tests/test_model_runtime.py`（worker 协议 / 超时 / 卸载 / 设备解析与设备·依赖探测 / 暂停取消与完成标记 / CPU·GPU 双胞胎解析 / llama.cpp 层数默认（`LlamaKwargsCase`）/ 模型日志（`ModelLogCase`）/ GitHub 直链识别与镜像重试（`GitHubSourceCase`）/ 请求字段不进 params（`WorkerRequestShapeCase`））。
- 自检：`scripts/selfcheck/checks_model.py`（清单与模板、`model.open` 接口、常量交叉校验、无插件时的门面行为、设置与密钥、页面装配、页面设置表单（每项即改即存、没有「保存设置」按钮）、下载源（GitHub）（`model_page_github_source`）、程序环境安装、按钮状态、批量依赖探测、安装源与删除弹窗、运行环境暂停取消与完成标记、运行环境补全调度（并发 / 挨个）、双胞胎 profile 解析（`model_runtime_profile_twins`）、模型日志（`model_logs_latest_and_cleanup`）、本地 whl 来源、空目录也能列进清理清单（`local/model` 不再被凭空造出来）、下载目录清理，以及界面回归：`model_ui_via_tool_library`、`model_dialog_form_scroll`、`model_dialog_template_reset`、`model_page_card_cleanup`、`model_page_queue_rows`、`model_page_runtime_installing`）。
- 运行环境那一行：点「安装」后立刻变成「安装中… n s · 最后一行输出」+ 忙等进度条、按钮锁住，装完刷新成真实状态；卸载没删干净（目录里还有文件）会明确报错而不是谎报成功。
