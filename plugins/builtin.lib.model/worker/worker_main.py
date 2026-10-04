"""worker 子进程入口：stdin/stdout 走 JSON-Lines 协议，stderr 走日志。

这个文件在**目标运行环境的独立解释器**里运行，因此：

- 只能用标准库 + 目标 venv 里装的包，**不能**用插件的相对导入（`python worker_main.py` 时
  包上下文根本不存在）；
- 所有重依赖（llama_cpp / transformers / sentence_transformers）都在**函数内部延迟 import**，
  缺包时回一帧 `{"ok": false, "error": "缺少依赖 …，请在运行环境页安装"}`，绝不让进程崩掉；
- 协议帧见 MODEL_PLUGIN.md §4.1：
  请求 `{"id":1,"op":"load","payload":{…}}`，
  回复 `{"id":1,"ok":true,"result":{…}}` / `{"id":1,"ok":false,"error":"…"}`，
  流式先 `{"id":1,"stream":"delta","text":"…"}` 再一帧最终 result。

`cancel` 只是置一个全局事件，流式循环每步检查它，尽快停下。
"""

from __future__ import annotations

import argparse
import gc
import importlib
import inspect
import json
import os
import queue
import sys
import tempfile
import threading
import traceback
from typing import Any

#: 协议与日志固定用 UTF-8：目标环境的控制台可能是 GBK，中文会变成乱码或读不出来。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

#: 协议输出专用句柄：把 `sys.stdout` 换成 stderr，免得第三方库不小心 print 到 stdout 污染协议。
_PROTOCOL_OUT = sys.stdout
sys.stdout = sys.stderr

#: 缺包时展示给用户的 pip 包名
_PIP_NAMES = {
    "llama_cpp": "llama-cpp-python",
    "transformers": "transformers",
    "torch": "torch",
    "sentence_transformers": "sentence-transformers",
    "PIL": "Pillow",
    "numpy": "numpy",
    "faster_whisper": "faster-whisper",
    "piper": "piper-tts",
    "diffusers": "diffusers",
    "rapidocr": "rapidocr（或 rapidocr-onnxruntime）",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime（或 rapidocr）",
    "onnxruntime": "onnxruntime（要靠 GPU 就换 onnxruntime-gpu，同一个包不再装两份）",
    "onnxruntime_gpu": "onnxruntime-gpu",
}

_SEND_LOCK = threading.Lock()

#: 置位后流式循环尽快结束
_CANCEL = threading.Event()

#: 当前已加载的后端实例（同一时刻只保留一个）
_BACKEND: Any = None
#: 已加载后端的名字 / 模型路径 / 设备，用于 info / ping
_STATE: dict[str, Any] = {"backend": "", "model_path": "", "device": "auto", "params": {}}


# ------------------------------------------------------------------ 协议 IO


def _log(text: str) -> None:
    """日志一律写 stderr。"""
    print(text, file=sys.stderr, flush=True)


def _send(frame: dict[str, Any]) -> None:
    """写一帧 JSON-Lines 到协议 stdout（多线程下串行化）。"""
    try:
        line = json.dumps(frame, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        line = json.dumps({"id": frame.get("id"), "ok": False, "error": "结果无法序列化为 JSON"})
    with _SEND_LOCK:
        _PROTOCOL_OUT.write(line + "\n")
        _PROTOCOL_OUT.flush()


def _ok(req_id: Any, result: Any = None) -> None:
    _send({"id": req_id, "ok": True, "result": {} if result is None else result})


def _fail(req_id: Any, message: str) -> None:
    _send({"id": req_id, "ok": False, "error": message})


def _delta(req_id: Any, text: str) -> None:
    _send({"id": req_id, "stream": "delta", "text": text})


class _MissingDep(Exception):
    """缺少第三方依赖。"""

    def __init__(self, module: str) -> None:
        self.module = module
        super().__init__(module)

    @property
    def message(self) -> str:
        return f"缺少依赖 {_PIP_NAMES.get(self.module, self.module)}，请在运行环境页安装"


#: 这些包按系统 DLL 搜索路径找 CUDA 运行库，import 之前先把目录挂上
_DLL_MODULES = frozenset(
    {"llama_cpp", "faster_whisper", "ctranslate2", "onnxruntime", "onnxruntime_gpu", "torch", "diffusers"}
)
#: CUDA 版 llama.cpp 的链接提示：DLL 找不到时补一句人话
_CUDA_DLL_HINT = (
    "CUDA 版 llama.cpp 要 CUDA 12 的运行库：装 nvidia-cuda-runtime-cu12 与 nvidia-cublas-cu12"
    "（装在程序环境或这个运行环境里都行），或者改用 CPU 版运行环境 llama-cpp。"
)
_DLL_DIRS_READY = False


def _prepare_dll_dirs() -> list[str]:
    """把解释器 site-packages 里 `nvidia/*/bin` 挂进 DLL 搜索路径（只做一次）。

    `nvidia-*` 轮子把 cudart / cublas / cudnn 放在那里，而 llama.cpp、ctranslate2、
    onnxruntime-gpu 都按系统搜索路径找它们；默认没有人替我们加这几个目录。
    """
    global _DLL_DIRS_READY
    if _DLL_DIRS_READY or os.name != "nt":
        return []
    _DLL_DIRS_READY = True
    added: list[str] = []
    roots: list[str] = []
    try:
        import site as _site

        roots.extend(str(item) for item in _site.getsitepackages() if item)
    except Exception:  # pragma: no cover - 取决于解释器
        pass
    roots.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    for root in roots:
        try:
            names = os.listdir(os.path.join(root, "nvidia"))
        except OSError:
            continue
        for name in names:
            folder = os.path.join(root, "nvidia", name, "bin")
            if not os.path.isdir(folder) or folder in added:
                continue
            try:
                os.add_dll_directory(folder)
            except OSError:
                pass
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
            added.append(folder)
    if added:
        _log(f"[worker] CUDA 运行库目录：{'；'.join(added)}")
    return added


def _require(module: str) -> Any:
    """延迟 import；缺包抛 `_MissingDep`。"""
    if module in _DLL_MODULES:
        _prepare_dll_dirs()
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - 取决于目标 venv
        raise _MissingDep(module) from exc


# ------------------------------------------------------------------ 后端：llama.cpp


def _clean_params(params: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """只保留目标库认识且不是 None 的参数。"""
    return {key: value for key, value in (params or {}).items() if key in allowed and value is not None}


#: 请求级字段：由调用处显式传给后端库（`prompt=` / `messages=` / `input=`），
#: 留在 `params` 里再被 `**options` 展开一次就会撞成「重复的关键字参数」
_REQUEST_KEYS = ("task", "input", "messages", "prompt", "stream")


def _options(payload: dict[str, Any]) -> dict[str, Any]:
    """一次推理的调用参数：先剔除请求级字段，剩下的才是能直接展开给后端库的 kwargs。"""
    options = dict(payload.get("params") or {})
    for key in _REQUEST_KEYS:
        options.pop(key, None)
    return options


def _device_parts(device: str) -> tuple[str, int | None]:
    """把 `auto` / `cpu` / `cuda` / `cuda:1` / `mps` / `xpu:0` 拆成（类型, 序号）。"""
    text = str(device or "").strip().lower() or "auto"
    kind, _, index = text.partition(":")
    kind = kind.strip() or "auto"
    try:
        number = int(index.strip()) if index.strip() else None
    except ValueError:
        number = None
    return kind, number


def _mps_ok(torch: Any) -> bool:
    """苹果 MPS 是否可用（旧版本 torch 没有 `backends.mps`）。"""
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    try:
        return bool(mps is not None and mps.is_available())
    except Exception:
        return False


def _torch_device(torch: Any, device: str) -> str:
    """把设置里的设备名翻译成 torch 的设备串；`auto` 按 cuda → mps → cpu 挑。"""
    kind, index = _device_parts(device)
    if kind == "auto":
        if bool(getattr(torch, "cuda", None) is not None and torch.cuda.is_available()):
            kind, index = "cuda", None
        elif _mps_ok(torch):
            kind = "mps"
        else:
            kind = "cpu"
    if kind == "cuda" and not bool(torch.cuda.is_available()):
        kind, index = "cpu", None
    if kind == "mps" and not _mps_ok(torch):
        kind, index = "cpu", None
    if kind not in ("cpu", "cuda", "mps", "xpu"):
        kind, index = "cpu", None
    return f"{kind}:{index}" if index is not None else kind


def _require_llama_cpp() -> Any:
    """import llama_cpp；动态库没链接上时补一句「缺哪个包」。"""
    try:
        return _require("llama_cpp")
    except RuntimeError as exc:
        raise RuntimeError(f"{exc}｜{_CUDA_DLL_HINT}") from exc


def _llama_kwargs(params: dict[str, Any], device: str) -> dict[str, Any]:
    """llama.cpp 的构造参数：CPU 强制 n_gpu_layers=0；选了 GPU 又没写层数时默认全部上卡（-1）。"""
    kwargs = _clean_params(
        params,
        {
            "n_ctx", "n_batch", "n_threads", "n_gpu_layers", "main_gpu", "seed", "verbose",
            "chat_format", "rope_scaling", "logits_all", "embedding", "flash_attn",
        },
    )
    kind, index = _device_parts(device)
    if kind == "cpu":
        kwargs["n_gpu_layers"] = 0
    else:
        kwargs.setdefault("n_gpu_layers", -1)
        if index is not None:
            kwargs.setdefault("main_gpu", index)
    kwargs.setdefault("verbose", False)
    return kwargs


class LlamaCppBackend:
    """llama-cpp-python：GGUF 单文件模型，chat / completion / embedding。"""

    name = "llama_cpp"

    def __init__(self) -> None:
        self._llm: Any = None

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        llama_cpp = _require_llama_cpp()
        kwargs = _llama_kwargs(params, device)
        try:
            self._llm = llama_cpp.Llama(model_path=model_path, **kwargs)
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}｜{_CUDA_DLL_HINT}") from exc

    # -- 调用 ----------------------------------------------------------

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        llm = self._llm
        options = _options(payload)

        if task == "chat":
            messages = _as_messages(payload.get("input"))
            if stream:
                chunks = llm.create_chat_completion(messages=messages, stream=True, **options)
                parts: list[str] = []
                for chunk in chunks:
                    if _CANCEL.is_set():
                        break
                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        parts.append(text)
                        _delta(req_id, text)
                return {"text": "".join(parts), "cancelled": _CANCEL.is_set()}
            result = llm.create_chat_completion(messages=messages, **options)
            choice = (result.get("choices") or [{}])[0]
            return {"text": (choice.get("message") or {}).get("content") or "", "usage": result.get("usage")}

        if task == "completion":
            prompt = _as_text(payload.get("input"))
            if stream:
                chunks = llm.create_completion(prompt=prompt, stream=True, **options)
                parts = []
                for chunk in chunks:
                    if _CANCEL.is_set():
                        break
                    text = ((chunk.get("choices") or [{}])[0].get("text")) or ""
                    if text:
                        parts.append(text)
                        _delta(req_id, text)
                return {"text": "".join(parts), "cancelled": _CANCEL.is_set()}
            result = llm.create_completion(prompt=prompt, **options)
            choice = (result.get("choices") or [{}])[0]
            return {"text": choice.get("text") or "", "usage": result.get("usage")}

        if task == "embedding":
            result = llm.create_embedding(input=payload.get("input"), **options)
            data = result.get("data") or []
            if not data:
                return {"embedding": [], "embeddings": []}
            vectors = [item.get("embedding") for item in data]
            return {"embedding": vectors[0], "embeddings": vectors}

        raise ValueError(f"llama_cpp 后端不支持任务 {task}")

    def unload(self) -> None:
        self._llm = None


# ------------------------------------------------------------------ 后端：transformers


class TransformersBackend:
    """HuggingFace transformers：chat / completion 走 generate，其余用 pipeline 兜底。"""

    name = "transformers"

    #: task → pipeline 任务名
    PIPELINES = {
        "classify": "text-classification",
        "vision": "image-classification",
        "ocr": "image-to-text",
        "asr": "automatic-speech-recognition",
    }

    def __init__(self) -> None:
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device = ""
        self._pipelines: dict[str, Any] = {}

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        transformers = _require("transformers")
        torch = _require("torch")
        self._torch = torch
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(model_path)
        target = _torch_device(torch, device)
        if target != "cpu":
            self._model = transformers.AutoModelForCausalLM.from_pretrained(model_path, torch_dtype="auto").to(target)
            self._device = target
        else:
            self._model = transformers.AutoModelForCausalLM.from_pretrained(model_path)
            self._model.to("cpu")
            self._device = ""

    # -- 调用 ----------------------------------------------------------

    def _generate(self, prompt: str, options: dict[str, Any], stream: bool, req_id: Any) -> dict[str, Any]:
        import contextlib

        model, tokenizer, torch = self._model, self._tokenizer, self._torch
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        kwargs = _clean_params(
            options,
            {"max_new_tokens", "temperature", "top_p", "top_k", "repetition_penalty", "do_sample"},
        )
        kwargs.setdefault("max_new_tokens", 256)
        # 把 stream 对象塞进 generate 时要吞掉它自己的 stdout 输出
        with contextlib.redirect_stdout(sys.stderr):
            if not stream:
                with torch.no_grad():
                    output = model.generate(**inputs, **kwargs)
                text = tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
                return {"text": text}

            streamer = _require("transformers").TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
            kwargs["streamer"] = streamer
            holder: list[Any] = []

            def _run() -> None:
                with torch.no_grad():
                    holder.append(model.generate(**inputs, **kwargs))

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            parts: list[str] = []
            for piece in streamer:
                if _CANCEL.is_set():
                    break
                if piece:
                    parts.append(piece)
                    _delta(req_id, piece)
            thread.join(timeout=1)
            return {"text": "".join(parts), "cancelled": _CANCEL.is_set()}

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        options = _options(payload)
        if task in ("chat", "completion"):
            prompt = _build_prompt(self._tokenizer, task, payload.get("input"))
            return self._generate(prompt, options, stream, req_id)

        pipeline_task = options.pop("pipeline_task", None) or self.PIPELINES.get(task)
        if pipeline_task is None:
            raise ValueError(f"transformers 后端不支持任务 {task}")
        if pipeline_task not in self._pipelines:
            hf_pipeline = _require("transformers").pipeline
            kwargs: dict[str, Any] = {}
            if self._device:
                kwargs["device"] = self._device
            self._pipelines[pipeline_task] = hf_pipeline(pipeline_task, model=self._model, tokenizer=self._tokenizer, **kwargs)
        result = self._pipelines[pipeline_task](payload.get("input"), **options)
        return {"result": result}

    def unload(self) -> None:
        self._tokenizer = None
        self._model = None
        self._pipelines = {}


# ------------------------------------------------------------------ 后端：sentence-transformers


class SentenceTransformersBackend:
    """sentence-transformers：embedding（SentenceTransformer）与 rerank（CrossEncoder）。"""

    name = "sentence_transformers"

    def __init__(self) -> None:
        self._model: Any = None
        self._cross: Any = None
        self._model_path = ""

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        sentence_transformers = _require("sentence_transformers")
        self._model_path = model_path
        kwargs = _clean_params(params, {"trust_remote_code", "cache_folder", "local_files_only"})
        kind, index = _device_parts(device)
        if kind in ("cpu", "cuda", "mps"):
            kwargs["device"] = f"{kind}:{index}" if kind == "cuda" and index is not None else kind
        self._model = sentence_transformers.SentenceTransformer(model_path, **kwargs)

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        options = _options(payload)
        if task == "embedding":
            vectors = self._model.encode(payload.get("input"), **options)
            data = vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)
            if data and not isinstance(data[0], list):
                return {"embedding": data, "embeddings": [data]}
            return {"embedding": data[0] if data else [], "embeddings": data}

        if task == "rerank":
            if self._cross is None:
                sentence_transformers = _require("sentence_transformers")
                self._cross = sentence_transformers.CrossEncoder(self._model_path)
            query = str(options.pop("query", "") or "")
            documents = payload.get("input")
            if isinstance(documents, str):
                documents = [documents]
            pairs = [[query, str(doc)] for doc in (documents or [])]
            scores = self._cross.predict(pairs, **options)
            values = scores.tolist() if hasattr(scores, "tolist") else list(scores)
            ranked = sorted(
                ({"index": index, "score": float(score)} for index, score in enumerate(values)),
                key=lambda item: item["score"],
                reverse=True,
            )
            return {"scores": [float(value) for value in values], "ranking": ranked}

        raise ValueError(f"sentence_transformers 后端不支持任务 {task}")

    def unload(self) -> None:
        self._model = None
        self._cross = None


# ------------------------------------------------------------------ 后端：自定义


class CustomBackend:
    """自定义后端：按 `params.entry = "pkg.mod:callable"` 动态加载一个可调用对象。

    约定：对象在 `load` 时用 `(model_path, params, device)` 调用一次；`invoke` 时用
    `(task, payload)` 调用，返回值原样回给调用方。
    """

    name = "custom"

    def __init__(self) -> None:
        self._target: Any = None
        self._loaded: Any = None

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        entry = str((params or {}).get("entry") or "").strip()
        if not entry:
            raise ValueError("custom 后端需要在 params.entry 指定 \"模块:可调用对象\"")
        module_name, _, attr = entry.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise _MissingDep(module_name) from exc
        try:
            target = getattr(module, attr) if attr else module
        except AttributeError as exc:
            raise ValueError(f"模块 {module_name} 里没有 {attr}") from exc
        self._target = target
        self._loaded = target(model_path, params, device)

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        if self._target is None:
            raise ValueError("custom 后端尚未加载")
        result = self._target(task, payload)
        if isinstance(result, dict):
            return result
        return {"result": result}

    def unload(self) -> None:
        self._target = None
        self._loaded = None


# ------------------------------------------------------------------ 后端：faster-whisper


class FasterWhisperBackend:
    """faster-whisper：语音转文字（asr）。

    `model_path` 可以是本地目录，也可以是 `tiny` / `base` / `small` 这类官方尺寸名。
    """

    name = "faster_whisper"

    def __init__(self) -> None:
        self._model: Any = None

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        faster_whisper = _require("faster_whisper")
        options = _clean_params(
            params,
            {"device", "device_index", "compute_type", "cpu_threads", "num_workers", "download_root", "local_files_only"},
        )
        kind, index = _device_parts(device)
        if kind == "cuda":
            options.setdefault("device", "cuda")
            if index is not None:
                options.setdefault("device_index", index)
        else:
            options.setdefault("device", "cpu" if kind == "cpu" else "auto")
        options.setdefault("compute_type", "default")
        size = str(params.get("model") or model_path or "small")
        self._model = faster_whisper.WhisperModel(size, **options)

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        if task not in ("asr", "transcribe"):
            raise ValueError(f"faster_whisper 后端不支持任务 {task}")
        options = _clean_params(
            payload.get("params") or {},
            {
                "language", "task", "beam_size", "best_of", "patience", "temperature", "vad_filter",
                "word_timestamps", "initial_prompt", "condition_on_previous_text", "no_speech_threshold",
                "without_timestamps",
            },
        )
        segments, info = self._model.transcribe(payload.get("input"), **options)
        rows = [
            {
                "start": float(getattr(segment, "start", 0.0) or 0.0),
                "end": float(getattr(segment, "end", 0.0) or 0.0),
                "text": str(getattr(segment, "text", "") or ""),
            }
            for segment in segments
        ]
        return {
            "text": "".join(row["text"] for row in rows).strip(),
            "segments": rows,
            "language": str(getattr(info, "language", "") or ""),
            "duration": float(getattr(info, "duration", 0.0) or 0.0),
        }

    def unload(self) -> None:
        self._model = None


# ------------------------------------------------------------------ 后端：piper


class PiperBackend:
    """piper-tts：文本转语音（tts），结果写成 wav 文件，帧里回文件路径。"""

    name = "piper"

    def __init__(self) -> None:
        self._voice: Any = None
        self._wave: Any = None
        self._model_path = ""

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        piper = _require("piper")
        voice_cls = getattr(piper, "PiperVoice", None)
        if voice_cls is None:  # pragma: no cover - 取决于 piper 版本
            voice_cls = getattr(importlib.import_module("piper.voice"), "PiperVoice")
        self._wave = importlib.import_module("wave")
        self._model_path = str(params.get("model") or model_path or "")
        if not self._model_path:
            raise ValueError("piper 后端需要 .onnx 语音模型路径")
        self._voice = voice_cls.load(self._model_path) if hasattr(voice_cls, "load") else voice_cls(self._model_path)

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        if task != "tts":
            raise ValueError(f"piper 后端不支持任务 {task}")
        options = _options(payload)
        output = str(options.pop("output", "") or options.pop("output_path", "") or "").strip()
        if not output:
            handle, output = tempfile.mkstemp(prefix="piper-", suffix=".wav")
            os.close(handle)
        with self._wave.open(output, "wb") as wav_file:
            self._voice.synthesize(_as_text(payload.get("input")), wav_file, **options)
        return {"file": output, "size": os.path.getsize(output)}

    def unload(self) -> None:
        self._voice = None


# ------------------------------------------------------------------ 后端：diffusers


class DiffusersBackend:
    """diffusers：文生图 / 图生图（image），结果写成图片文件，帧里回文件路径。"""

    name = "diffusers"

    def __init__(self) -> None:
        self._pipe: Any = None

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        diffusers = _require("diffusers")
        options = _clean_params(
            params,
            {"torch_dtype", "variant", "use_safetensors", "local_files_only", "cache_dir", "revision"},
        )
        dtype = options.get("torch_dtype")
        if isinstance(dtype, str):
            options["torch_dtype"] = getattr(_require("torch"), dtype)
        class_name = str(params.get("pipeline") or "AutoPipelineForText2Image")
        pipeline_cls = getattr(diffusers, class_name, None)
        if pipeline_cls is None:
            raise ValueError(f"diffusers 里没有 {class_name}")
        source = str(params.get("model") or model_path or "")
        if not source:
            raise ValueError("diffusers 后端需要仓库 id 或本地流水线目录")
        self._pipe = pipeline_cls.from_pretrained(source, **options)
        target = _torch_device(_require("torch"), device)
        if target != "cpu":
            self._pipe.to(target)

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        if task not in ("image", "text2image", "txt2img"):
            raise ValueError(f"diffusers 后端不支持任务 {task}")
        options = _options(payload)
        output = str(options.pop("output", "") or options.pop("output_path", "") or "").strip()
        if not output:
            handle, output = tempfile.mkstemp(prefix="diffusers-", suffix=".png")
            os.close(handle)
        result = self._pipe(prompt=_as_text(payload.get("input")), **options)
        images = list(getattr(result, "images", None) or [])
        if not images:
            raise ValueError("diffusers 流水线没有返回图片")
        images[0].save(output)
        return {"file": output, "size": os.path.getsize(output), "count": len(images)}

    def unload(self) -> None:
        self._pipe = None


# ------------------------------------------------------------------ 后端：rapidocr

def _has_module(name: str) -> bool:
    """只查能不能 import，不真的 import（免得把重包拖进来）。"""
    from importlib.util import find_spec

    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _parameters(func: Any) -> set[str]:
    """函数显式声明的参数名（拿不到签名时返回空集）。"""
    try:
        return set(inspect.signature(func).parameters)
    except (TypeError, ValueError):
        return set()


def _plain_value(value: Any) -> Any:
    """numpy 数组之类转成能进 JSON 的 list。"""
    tolist = getattr(value, "tolist", None)
    return tolist() if callable(tolist) else value


def _v3_rows(result: Any) -> list[dict[str, Any]]:
    """把 rapidocr 3.x 的 RapidOCROutput 摊平成 1.x 那样的行。"""
    texts = getattr(result, "txts", None)
    if texts is None:
        return []
    boxes = getattr(result, "boxes", None)
    scores = getattr(result, "scores", None)
    rows: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        box = boxes[index] if boxes is not None and index < len(boxes) else None
        score = scores[index] if scores is not None and index < len(scores) else None
        rows.append({
            "box": _plain_value(box),
            "text": str(text),
            "score": float(score) if score is not None else 0.0,
        })
    return rows


class RapidOCRBackend:
    """rapidocr-onnxruntime（1.x）或 rapidocr（3.x）：图片文字识别（ocr）。"""

    name = "rapidocr"

    #: 1.x 的构造参数是平铺 kwargs
    _V1_OPTIONS = {
        "det_model_path", "cls_model_path", "rec_model_path", "use_det", "use_cls", "use_rec",
        "intra_op_num_threads", "text_score",
    }
    #: 3.x 的构造参数是 "Global.text_score" 这种点号键
    _V3_KEYS = {
        "text_score": "Global.text_score",
        "use_det": "Global.use_det",
        "use_cls": "Global.use_cls",
        "use_rec": "Global.use_rec",
        "det_model_path": "Det.model_path",
        "cls_model_path": "Cls.model_path",
        "rec_model_path": "Rec.model_path",
        "intra_op_num_threads": "EngineConfig.onnxruntime.intra_op_num_threads",
    }

    def __init__(self) -> None:
        self._engine: Any = None
        self._v3 = False

    def load(self, model_path: str, params: dict[str, Any], device: str) -> None:
        module = None
        for name in ("rapidocr_onnxruntime", "rapidocr"):
            try:
                module = importlib.import_module(name)
                break
            except ImportError:
                continue
        if module is None:
            raise _MissingDep("rapidocr")
        if "params" in _parameters(module.RapidOCR):
            # rapidocr 3.x：参数走 params 字典，键是点号路径
            self._v3 = True
            self._engine = module.RapidOCR(params=self._v3_params(params or {}))
        else:
            self._engine = module.RapidOCR(**_clean_params(params, self._V1_OPTIONS))

    def _v3_params(self, params: dict[str, Any]) -> dict[str, Any]:
        options = _clean_params(params, set(self._V3_KEYS) | {"engine_type"})
        engine = str(options.pop("engine_type", "") or "")
        if not engine and not _has_module("onnxruntime") and _has_module("torch"):
            # 3.x 默认引擎是 onnxruntime；没装就用 torch 顶上（都没有时让它自己报错）
            engine = "torch"
        if engine:
            for task in ("Det", "Cls", "Rec"):
                options[f"{task}.engine_type"] = engine
        return {self._V3_KEYS[key]: value for key, value in options.items() if key in self._V3_KEYS}

    def invoke(self, req_id: Any, task: str, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        if task != "ocr":
            raise ValueError(f"rapidocr 后端不支持任务 {task}")
        options = _options(payload)
        if self._v3:
            allowed = _parameters(type(self._engine).__call__)
            result = self._engine(payload.get("input"), **{k: v for k, v in options.items() if k in allowed})
            rows = _v3_rows(result)
        else:
            result = self._engine(payload.get("input"), **options)
            lines = result[0] if isinstance(result, tuple) else result
            rows = []
            for item in lines or []:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                score = float(item[2]) if len(item) > 2 and item[2] is not None else 0.0
                rows.append({"box": item[0], "text": str(item[1]), "score": score})
        return {"lines": rows, "text": "\n".join(row["text"] for row in rows)}

    def unload(self) -> None:
        self._engine = None

_BACKENDS = {
    LlamaCppBackend.name: LlamaCppBackend,
    TransformersBackend.name: TransformersBackend,
    SentenceTransformersBackend.name: SentenceTransformersBackend,
    FasterWhisperBackend.name: FasterWhisperBackend,
    PiperBackend.name: PiperBackend,
    DiffusersBackend.name: DiffusersBackend,
    RapidOCRBackend.name: RapidOCRBackend,
    CustomBackend.name: CustomBackend,
}


# ------------------------------------------------------------------ 输入整形


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)


def _as_messages(value: Any) -> list[dict[str, str]]:
    """把 chat 输入整形为 OpenAI 风格 messages。"""
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    messages: list[dict[str, str]] = []
    for item in value or []:
        if isinstance(item, dict):
            messages.append({"role": str(item.get("role") or "user"), "content": _as_text(item.get("content"))})
        else:
            messages.append({"role": "user", "content": _as_text(item)})
    if not messages:
        messages = [{"role": "user", "content": ""}]
    return messages


def _build_prompt(tokenizer: Any, task: str, value: Any) -> str:
    """chat 优先用 tokenizer 的 chat 模板，否则退化成 `role: content` 拼接。"""
    if task == "chat" and tokenizer is not None and getattr(tokenizer, "chat_template", None):
        try:
            return tokenizer.apply_chat_template(_as_messages(value), tokenize=False, add_generation_prompt=True)
        except Exception:  # pragma: no cover - 模板差异
            pass
    if task == "chat":
        return "\n".join(f"{item['role']}: {item['content']}" for item in _as_messages(value)) + "\nassistant:"
    return _as_text(value)


# ------------------------------------------------------------------ ops


def op_ping(payload: dict[str, Any]) -> dict[str, Any]:
    """探活：回解释器版本与当前后端。"""
    return {
        "pong": True,
        "python": sys.version,
        "executable": sys.executable,
        "backend": _STATE.get("backend") or "",
        "loaded": _BACKEND is not None,
    }


def op_info(payload: dict[str, Any]) -> dict[str, Any]:
    """当前加载状态。"""
    return {
        "backend": _STATE.get("backend") or "",
        "model_path": _STATE.get("model_path") or "",
        "device": _STATE.get("device") or "auto",
        "params": _STATE.get("params") or {},
        "loaded": _BACKEND is not None,
        "python": sys.version,
        "executable": sys.executable,
        "pid": os.getpid(),
    }


def op_load(payload: dict[str, Any]) -> dict[str, Any]:
    """加载模型：先卸掉旧的，再按 backend 名实例化。"""
    global _BACKEND
    backend_name = str(payload.get("backend") or "").strip()
    if backend_name not in _BACKENDS:
        raise ValueError(f"未知后端 {backend_name or '(空)'}（当前 worker 支持：{'、'.join(sorted(_BACKENDS))}）")
    if _BACKEND is not None:
        op_unload({})

    model_path = str(payload.get("model_path") or "").strip()
    params = dict(payload.get("params") or {})
    device = str(payload.get("device") or "auto")
    backend = _BACKENDS[backend_name]()
    backend.load(model_path, params, device)

    _BACKEND = backend
    _STATE.update({"backend": backend_name, "model_path": model_path, "device": device, "params": params})
    _log(f"[worker] 已加载 {backend_name}：{model_path or '(空路径)'}")
    return {"backend": backend_name, "model_path": model_path, "device": device, "loaded": True}


def op_invoke(payload: dict[str, Any], req_id: Any = None) -> dict[str, Any]:
    """执行一次推理；`stream=true` 时先逐段发 delta 再给完整结果。"""
    if _BACKEND is None:
        raise ValueError(f"尚未加载模型（backend={_STATE.get('backend') or '(空)'}）")
    _CANCEL.clear()
    task = str(payload.get("task") or "").strip()
    if not task:
        raise ValueError("invoke 缺少 task")
    stream = bool(payload.get("stream"))
    return _BACKEND.invoke(req_id, task, payload, stream)


def op_unload(payload: dict[str, Any]) -> dict[str, Any]:
    """释放后端引用并尽量回收显存/内存。"""
    global _BACKEND
    backend = _BACKEND
    _BACKEND = None
    _STATE.update({"backend": _STATE.get("backend") or "", "model_path": "", "params": {}})
    if backend is not None:
        try:
            backend.unload()
        except Exception as exc:  # pragma: no cover - 卸载失败不该崩
            _log(f"[worker] unload 失败：{exc}")
    gc.collect()
    return {"loaded": False}


def op_cancel(payload: dict[str, Any]) -> dict[str, Any]:
    """置取消标志，流式循环会尽快停下。"""
    _CANCEL.set()
    return {"cancelled": True}


_HANDLERS = {
    "ping": op_ping,
    "info": op_info,
    "load": op_load,
    "invoke": op_invoke,
    "unload": op_unload,
}


def _handle(frame: dict[str, Any], req_id: Any) -> None:
    """处理一帧非 cancel 请求（在 worker 线程里串行执行）。"""
    op = str(frame.get("op") or "").strip()
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    handler = _HANDLERS.get(op)
    if handler is None:
        _fail(req_id, f"未知操作 {op or '(空)'}")
        return
    try:
        if op == "invoke":
            # invoke 需要 req_id 才能把流式 delta 帧标上同一个 id
            result = handler(payload, req_id)
        else:
            result = handler(payload)
    except _MissingDep as exc:
        _fail(req_id, exc.message)
    except ValueError as exc:
        _fail(req_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - 任何异常都转成协议错误帧
        _log(traceback.format_exc())
        _fail(req_id, f"{type(exc).__name__}: {exc}")
    else:
        _ok(req_id, result)


# ------------------------------------------------------------------ 主循环


def _run_queue(tasks: "queue.Queue[dict[str, Any] | None]") -> None:
    """按序执行请求帧，保证 load 先于 invoke。"""
    while True:
        frame = tasks.get()
        if frame is None:
            return
        req_id = frame.get("id")
        try:
            _handle(frame, req_id)
        except Exception as exc:  # pragma: no cover - 兜底
            _log(traceback.format_exc())
            _fail(req_id, f"{type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MyDataManager 模型 worker")
    parser.add_argument("--backend", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)

    _STATE.update({"backend": args.backend, "model_path": args.model, "device": args.device})
    _log(f"[worker] 启动：backend={args.backend or '(空)'} model={args.model or '(空)'} python={sys.executable}")

    tasks: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
    worker = threading.Thread(target=_run_queue, args=(tasks,), name="worker-ops", daemon=True)
    worker.start()

    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            frame = json.loads(text)
        except ValueError:
            _fail(None, "请求不是合法 JSON")
            continue
        if not isinstance(frame, dict):
            _fail(None, "请求必须是 JSON 对象")
            continue
        if str(frame.get("op") or "") == "cancel":
            # 立刻置标志，不等前面的 invoke 跑完
            _ok(frame.get("id"), op_cancel(frame.get("payload") or {}))
            continue
        tasks.put(frame)

    tasks.put(None)
    # 收完 stdin 不代表活干完了（load 可能要导入 torch，动辄十几秒）：等队列排空再退，
    # 否则调用方会拿不到任何应答帧。
    worker.join()
    return 0


# op_invoke 由 _HANDLERS 统一调度，多带一个 req_id 参数（见 `_handle`）


if __name__ == "__main__":
    raise SystemExit(main())
