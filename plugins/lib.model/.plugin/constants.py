"""模型工具库的常量：模型类型、状态、能力、任务、适配器与页面参数。

页面、注册表、适配器、下载器都从这里取字面量，避免各处写死字符串后对不上。
"""

from __future__ import annotations

__all__ = [
    "ADAPTERS",
    "ADAPTER_INPROCESS",
    "ADAPTER_LLAMA_SERVER",
    "ADAPTER_OLLAMA",
    "ADAPTER_OPENAI",
    "ADAPTER_WORKER",
    "CAPABILITIES",
    "CAPABILITY_BACKENDS",
    "CAPABILITY_LABELS",
    "BACKENDS",
    "BACKEND_LABELS",
    "PLUGIN_ID",
    "DEFAULT_CONCURRENT",
    "DEFAULT_HEARTBEAT_SEC",
    "DEFAULT_IDLE_UNLOAD_SEC",
    "DEFAULT_MAX_RESIDENT",
    "KINDS",
    "KIND_EXTERNAL",
    "KIND_LABELS",
    "KIND_LOCAL",
    "PAGE_ICON",
    "PAGE_KEY",
    "PAGE_ORDER",
    "PAGE_TITLE",
    "STATES",
    "STATE_DRAFT",
    "STATE_DOWNLOADING",
    "STATE_ERROR",
    "STATE_INCOMPLETE",
    "STATE_LABELS",
    "STATE_LOADING",
    "STATE_READY",
    "TASKS",
    "TASK_CHAT",
]

# ------------------------------------------------------------------ 模型类型
KIND_LOCAL = "local"
KIND_EXTERNAL = "external"
KINDS = (KIND_LOCAL, KIND_EXTERNAL)
KIND_LABELS = {KIND_LOCAL: "本地模型", KIND_EXTERNAL: "外部模型"}

# ------------------------------------------------------------------ 模型状态
STATE_DRAFT = "draft"
STATE_DOWNLOADING = "downloading"
STATE_READY = "ready"
STATE_LOADING = "loading"
STATE_ERROR = "error"
#: 盘上有文件、但不齐（缺 config / 分词器 / 词表，或仓库清单里还差几个）：不能算「已就绪」。
STATE_INCOMPLETE = "incomplete"
STATES = (STATE_DRAFT, STATE_DOWNLOADING, STATE_READY, STATE_LOADING, STATE_ERROR, STATE_INCOMPLETE)
STATE_LABELS = {
    STATE_DRAFT: "未填充",
    STATE_DOWNLOADING: "下载中",
    STATE_READY: "已就绪",
    STATE_LOADING: "加载中",
    STATE_ERROR: "错误",
    STATE_INCOMPLETE: "文件不完全",
}

# ------------------------------------------------------------------ 能力目录
TASK_CHAT = "chat"
TASKS = (
    "chat",
    "completion",
    "embedding",
    "rerank",
    "vision",
    "asr",
    "tts",
    "image",
    "ocr",
    "classify",
)
CAPABILITIES = TASKS
CAPABILITY_LABELS = {
    "chat": "对话",
    "completion": "续写",
    "embedding": "向量化",
    "rerank": "重排",
    "vision": "看图",
    "asr": "语音识别",
    "tts": "语音合成",
    "image": "图片生成",
    "ocr": "文字识别",
    "classify": "分类",
}
#: 每个能力默认用哪个后端跑（worker 的 backend 名，或 server 族适配器名）
CAPABILITY_BACKENDS = {
    "chat": "llama_cpp",
    "completion": "llama_cpp",
    "embedding": "sentence_transformers",
    "rerank": "sentence_transformers",
    "vision": "transformers",
    "asr": "faster_whisper",
    "tts": "piper",
    "image": "diffusers",
    "ocr": "rapidocr",
    "classify": "transformers",
}

#: worker 子进程认识的后端
BACKENDS = ("llama_cpp", "transformers", "sentence_transformers", "diffusers", "faster_whisper", "piper", "rapidocr", "custom")
BACKEND_LABELS = {
    "llama_cpp": "llama.cpp",
    "transformers": "transformers",
    "sentence_transformers": "sentence-transformers",
    "diffusers": "diffusers",
    "faster_whisper": "faster-whisper",
    "piper": "piper",
    "rapidocr": "rapidocr",
    "custom": "自定义入口",
}

# ------------------------------------------------------------------ 适配器
ADAPTER_OPENAI = "openai_compat"
ADAPTER_OLLAMA = "ollama"
ADAPTER_LLAMA_SERVER = "llama_server"
ADAPTER_WORKER = "worker"
ADAPTER_INPROCESS = "inprocess"
ADAPTERS = (
    ADAPTER_OPENAI,
    ADAPTER_OLLAMA,
    ADAPTER_LLAMA_SERVER,
    ADAPTER_WORKER,
    ADAPTER_INPROCESS,
)
ADAPTER_LABELS = {
    ADAPTER_OPENAI: "OpenAI 兼容接口",
    ADAPTER_OLLAMA: "Ollama",
    ADAPTER_LLAMA_SERVER: "llama-server 子进程",
    ADAPTER_WORKER: "本地 worker 子进程",
    ADAPTER_INPROCESS: "进程内（高级）",
}

# ------------------------------------------------------------------ 默认值
DEFAULT_MAX_RESIDENT = 1
DEFAULT_IDLE_UNLOAD_SEC = 600
DEFAULT_HEARTBEAT_SEC = 5
DEFAULT_CONCURRENT = 2

# ------------------------------------------------------------------ 页面
PAGE_KEY = "model_manager"
PAGE_TITLE = "模型"
PAGE_ICON = "ROBOT"
PAGE_ORDER = 150

#: 本插件 id（记录里的 plugin_id 用它，方便其它插件判断是谁提供的模型）
PLUGIN_ID = "lib.model"
