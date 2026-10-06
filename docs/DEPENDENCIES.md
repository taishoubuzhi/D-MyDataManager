# 依赖清单与安装命令（DEPENDENCIES）

> 依赖探测实现在 `src/app/core/capabilities.py`，命令行用法见第 4 节。分四部分：**程序依赖**（`requirements.txt`，装完即用）、
> **默认不装的部分**（两个模型下载才用到的包 + 工具型外部依赖）、**模型运行环境**（模型插件自己的
> venv，按需安装，绝不静默装）、**验收与自查**。用户侧的一键命令默认走**清华镜像**并加 `--no-cache-dir`。

## 1. 程序依赖（`requirements.txt`）

发布产物（PyAppify 启动器）与用户端 venv 都只装这一份，装完即可用，**不需要再手动补包**。

### 1.1 界面与基础

| 包 | 版本 | 为什么必需 |
| --- | --- | --- |
| `PyQt6` | `6.11.0` | 界面框架（`PyQt6.QtWidgets` / `QtCore` / `QtNetwork`） |
| `PyQt6-Fluent-Widgets` | `1.11.3` | 界面组件库（qfluentwidgets），全站控件与主题 |
| `SQLAlchemy` | `2.0.52` | ORM 与数据库层；SQLite 通过标准库 `sqlite3` 驱动 |
| `loguru` | `0.7.3` | 日志（`app.core.runtime.logging_setup`、插件 `console_for`） |
| `Pillow` | `12.3.0` | 图片尺寸、封面生成、感知哈希、图片查看器 |

### 1.2 加速 / 容错 / 功能库（随程序一起装）

**缺任何一个都照常启动**：程序在导入处 `try/except`，功能降级而不是崩掉；
`tests/verify_optional_absence.py` 会把它们全部藏起来跑一遍作为验收。
每项「提供什么能力 / 没装会怎样」也写在下面这张表里（探测实现 `src/app/core/capabilities.py`）。

| 能力（`capabilities` 名） | 包 | 提供的能力 | 没装的结果 |
| --- | --- | --- | --- |
| `orjson` | `orjson` | 清单 / 配置 JSON 读写快 3–10 倍 | 退回标准库 `json`：行为一致，读写明显变慢 |
| `fastjsonschema` | `fastjsonschema` | 清单按 JSON Schema 严格校验 | 只做「必须项 + items 结构」最小校验 |
| `argon2` | `argon2-cffi` | 口令散列用 argon2id | 退回 PBKDF2-HMAC-sha256（老散列仍可校验），抗暴破弱一些 |
| `watchdog` | `watchdog` | 库文件夹递归监听（子目录变动也收得到） | 退回 Qt `QFileSystemWatcher`：只监听已绑定目录、300 目录上限 |
| `puremagic` | `puremagic` | 按文件内容嗅探 MIME | 只按扩展名判断，改过扩展名的文件认不出 |
| `charset_normalizer` | `charset-normalizer` | 文本编码探测更准 | 按固定顺序硬试（gb18030 / big5 仍能试出，冷门编码可能判错） |
| `pymupdf` | `pymupdf` | PDF 正文抽取与页面渲染 | PDF 不能抽正文、不能渲染页面 |
| `jieba` | `jieba` | 中文分词，自动关键词质量更高 | 中文按退化方式切分，自动关键词质量下降 |
| `usearch` | `usearch` | 本地向量索引（相似图片 / 语义检索） | 相似图片 / 语义检索不可用 |
| `sqlite_vec` | `sqlite-vec` | 把向量检索下推到 SQLite | 向量检索只能取回内存里算（更慢、更占内存） |
| `py7zr` | `py7zr` | 7z 压缩包直接列举与解包 | 7z 只能交给系统 bsdtar 兜底 |
| `pyzipper` | `pyzipper` | 加密 zip 的读取 | 加密 zip 读不了（普通 zip 不受影响） |
| `rarfile` | `rarfile` | rar 压缩包列举（配合系统 bsdtar） | rar 列不出内容（bsdtar 也帮不上，rarfile 才是入口） |
| `zstd` | 标准库 `compression.zstd` | 内容仓库默认压缩编码 | 退回 deflate：功能不丢，压缩率差一点（Python 3.14+ 自带，正常不会缺） |

```powershell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir
```

## 2. 默认不装的部分

### 2.1 两个刻意不装的包（只有模型下载用得上）

| 能力（`capabilities` 名） | 包 | 提供的能力 | 没装的结果 |
| --- | --- | --- | --- |
| `huggingface_hub` | `huggingface_hub` | 按仓库列模型文件（分片 / 成套文件识别更准） | 退回公开 HTTP API 列文件：大仓库慢一些、更依赖网络 |
| `hf_transfer` | `hf-transfer` | HuggingFace 大文件多线程提速 | 走普通单连接，大文件慢一些（不影响能不能下） |

```powershell
pip install huggingface_hub hf-transfer -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir
```

也可以直接跑能力探测拿到可复制的 pip 命令（见第 4 节第 3 条）。

### 2.2 工具型外部依赖（不是 pip 包）

| 工具 | 用途 | 获取方式 |
| --- | --- | --- |
| `ffmpeg` | 视频抽帧 / 转码（缩略图、向量化前的解码） | 装 ffmpeg 并加进 `PATH`；或 `pip install imageio-ffmpeg`（程序会优先用 `imageio_ffmpeg.get_ffmpeg_exe()`） |
| `bsdtar` | rar / 7z 等压缩包的外部解包器（libarchive） | Windows 自带 `C:\Windows\System32\tar.exe`；其它系统装 `libarchive`。启动时 `capabilities.configure_tools()` 会把它设成 `rarfile.BSDTAR_TOOL` |

### 2.3 开发 / 门禁 / 基准用

```powershell
pip install pytest pytest-benchmark pyinstrument -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir
```

## 3. 模型运行环境（模型插件自己的 venv）

模型推理不装进程序 venv：每个 profile 一套独立 venv，落在
`.resources/models/runtime/<profile>/venv`，**装之前界面会二次确认，绝不静默安装**。
清单在 `plugins/lib.model/.data/runtime_profiles.json`（统一清单格式，可直接改）。

| profile | 内容 | 体积 |
| --- | --- | --- |
| `llama-cpp` | `llama-cpp-python>=0.3.2` | 约 300 MB |
| `llama-cpp-gpu` | 同上 + `nvidia-cuda-runtime-cu12`、`nvidia-cublas-cu12` | 约 2.3 GB |
| `transformers` | `torch`、`transformers>=4.45`、`accelerate`、`sentencepiece`、`pillow` | 约 2.5 GB |
| `transformers-gpu` | 同上（CUDA 轮子） | 约 3.5 GB |
| `sentence-transformers` | `torch`、`sentence-transformers>=3.0` | 约 2 GB |
| `sentence-transformers-gpu` | 同上（CUDA 轮子） | 约 3 GB |
| `whisper` | `faster-whisper>=1.0` | 约 200 MB |
| `whisper-gpu` | `faster-whisper` + CUDA 运行库 + `nvidia-cudnn-cu12` | 约 1.5 GB |
| `diffusers` | `torch`、`diffusers>=0.31`、`transformers`、`accelerate`、`safetensors` | 约 3 GB |
| `diffusers-gpu` | 同上（CUDA 轮子） | 约 4 GB |
| `rapidocr` | `rapidocr>=3.0`、`onnxruntime` | 约 300 MB |
| `rapidocr-gpu` | `rapidocr` + `onnxruntime-gpu` + CUDA 运行库 | 约 2.4 GB |
| `piper` | `piper-tts>=1.2` | 约 100 MB |

- 带 `-gpu` 的 profile 只是把索引指向 CUDA 轮子（torch 的 CUDA 版自带 CPU 内核，不必装两份）；
  `onnxruntime` 与 `onnxruntime-gpu` 是两个发行包，GPU 版在没有 CUDA 时自动退回 CPU，**只装一个**。
- 想进某个 profile 的 venv 手动折腾：

```powershell
& ".resources\models\runtime\llama-cpp\venv\Scripts\Activate.ps1"
```

## 4. 验收与自查

```powershell
# 1) 把依赖全藏起来时程序仍能启动、入库、读回、回退监听
.venv\Scripts\python.exe tests\verify_optional_absence.py

# 2) 依赖树本身没有冲突（目前只报 5 条与本项目无关的第三方簇：eido / pephubclient / peppy /
#    pipestat 缺 pandas / coloredlogs / jinja2，属既有基线、不算失败）
.venv\Scripts\python.exe -m pip check

# 3) 能力探测（缺哪些、提供什么、怎么装）
.venv\Scripts\python.exe -c "from app.core import capabilities; print(capabilities.summary_text()); print(*capabilities.install_commands(), sep='\n')"

# 4) 打包冒烟：用 git 载荷造一份干净检出，在里面编译 + 走真实启动路径 + 跑门禁子集
.venv\Scripts\python.exe tests\smoke_checkout.py
```

能力探测的输出格式：`report()` 每项给出 `name / available / kind / version / unlocks / fallback / hint`，`summary_text()` 是一行摘要（几项可用、几项缺失）。

## 5. 许可证

上面这些包与项目的 [GPL-3.0](../../LICENSE) 全部兼容，**没有需要替换的依赖**；逐包版本与许可证见
[`README.md` 的「许可证」小节](../README.md#许可证)。两条容易被忽略的点：

- **PyMuPDF 1.28.2 是 AGPL-3.0（或商业授权）**：GPLv3 §13 允许组合分发，但组合体要一并满足 AGPLv3 §13
  ——把程序作为网络服务提供时必须向使用者提供完整源码（单机运行不触发）。要闭源或去掉这条约束，
  需买 PyMuPDF 商业授权或去掉 PDF 正文抽取 / 渲染能力。
- **LGPL 组件（`py7zr` / `inflate64` / `pyppmd` / `multivolumefile` / `PyQt6-Qt6`）**：Python 侧是模块动态导入、
  Qt 侧是 DLL 动态加载，用户都能自行替换；`LGPL-2.1-or-later` 本身也允许升级到 GPL，因此不构成冲突。
