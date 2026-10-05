"""`model.open` 接口实现：把管理器暴露给程序门面与其它插件。

跨插件调用方只走 `app.sdk.models`（→ 本对象的同名方法），所以这里的签名要和
`src/app/sdk/models.py` 的 `ModelInfo` / `ModelLease` 协议一致。
"""

from __future__ import annotations

from typing import Any, Iterable

from app.sdk.errors import ModelError

from .batch import run_batch as run_batch_items
from .constants import PAGE_KEY
from .manager import Lease, ModelManager
from .record import ModelRecord

__all__ = ["ModelOpenApi"]

#: 真·权重文件的后缀；其余是 transformers / llama.cpp 那套「模型目录」里必须同目录的配置与分词器。
_WEIGHT_SUFFIXES = (
    ".bin",
    ".safetensors",
    ".gguf",
    ".onnx",
    ".pt",
    ".pth",
    ".msgpack",
    ".h5",
    ".tflite",
    ".model",
)


def _file_kind(name: str) -> str:
    """模型目录里的文件是干什么的：权重 / 配置 / 分词器 / 其它。

    `pytorch_model.bin`、`model.bin` 是真的参数；`config.json`（结构）、
    `preprocessor_config.json`（图像预处理）、`tokenizer*.json` / `vocab.json` /
    `merges.txt`（分词器）都是这套目录的必需品——`from_pretrained(本地目录)` 按约定
    在同一个目录里读它们，缺一个就直接报错，所以它们也得一起下。
    """
    base = str(name or "").strip().lower().rsplit("/", 1)[-1]
    if base.endswith(_WEIGHT_SUFFIXES):
        return "权重"
    if any(key in base for key in ("token", "vocab", "merges", "sentencepiece")):
        return "分词器"
    if base.endswith((".json", ".txt")):
        return "配置"
    return "其它"


def _missing_text(names: Iterable[str]) -> str:
    """把缺口按「权重 / 配置 / 分词器」分组写成一句人话。"""
    groups: dict[str, list[str]] = {}
    for name in names or ():
        text = str(name or "").strip()
        if text:
            groups.setdefault(_file_kind(text), []).append(text)
    parts = [
        f"{kind}（{'、'.join(groups[kind])}）"
        for kind in ("权重", "配置", "分词器", "其它")
        if groups.get(kind)
    ]
    return "；".join(parts)


class ModelOpenApi:
    """模型调度接口：查登记表、取租约、直接调用、管理登记项。"""

    def __init__(self, ctx, manager: ModelManager) -> None:
        self.ctx = ctx
        self.manager = manager
        #: 运行环境是否装好的进程内缓存：`runtime.installed()` 会起子进程跑
        #: `python --version`，「检查缺什么」会按数据类型反复问同一批 profile。
        self._runtime_cache: dict[str, bool] = {}
        #: 模型页把它的下载队列工厂挂上来，其它插件就能「一键下载」缺的权重。
        self._downloads_factory = None

    def _runtime_installed(self, profile_id: str) -> bool:
        """运行环境装好了没有（按 profile id 缓存）。"""
        key = str(profile_id or "")
        if not key:
            return False
        hit = self._runtime_cache.get(key)
        if hit is None:
            from . import runtime as runtime_module

            hit = bool(runtime_module.installed(key))
            self._runtime_cache[key] = hit
        return hit

    def forget_runtime_cache(self) -> None:
        """装 / 卸运行环境之后由模型页调用，让下一次检查重新读盘。"""
        self._runtime_cache.clear()

    # ------------------------------------------------------------ 查询
    def list_models(self, kind: str = "", capability: str = ""):
        return self.manager.list_models(kind=kind, capability=capability)

    def model_by_id(self, model_id: str):
        return self.manager.model_by_id(model_id)

    def capabilities(self) -> tuple[str, ...]:
        return self.manager.capabilities()

    def loaded(self) -> tuple[str, ...]:
        return self.manager.loaded()

    # ------------------------------------------------------------ 取用
    def acquire(self, model_id: str = "", capability: str = "", task: str = "", timeout: float | None = None) -> Lease:
        return self.manager.acquire(model_id=model_id, capability=capability, task=task, timeout=timeout)

    def invoke(
        self,
        model_id: str = "",
        capability: str = "",
        task: str = "",
        payload: dict | None = None,
        *,
        stream: bool = False,
        timeout: float | None = None,
    ) -> Any:
        return self.manager.invoke(
            model_id=model_id,
            capability=capability,
            task=task,
            payload=payload,
            stream=stream,
            timeout=timeout,
        )

    # ------------------------------------------------------------ 批量
    def run_batch(
        self,
        requests,
        *,
        model_id: str = "",
        capability: str = "",
        on_progress=None,
        cancel=None,
        max_workers: int | None = None,
    ) -> tuple:
        """一批请求统一跑：同一个模型只加载一次，组间并行、组内串行。

        签名与 `app.sdk.models.run_batch` 一致，调用方（自动标签 / 自动关键词等）不需要
        自己取租约，也不会为几千个条目反复加载、切换模型。
        """
        return run_batch_items(
            self.manager,
            requests,
            model_id=model_id,
            capability=capability,
            on_progress=on_progress,
            cancel=cancel,
            max_workers=max_workers,
        )

    # ------------------------------------------------------------ 预定义方案
    def templates(self, capability: str = "", lightweight_only: bool = False) -> list[dict]:
        """本地模型模板（`data/model_list.json`），按能力 / 轻量筛选，附带当前登记状态。

        其它插件用它给用户「预定义方案」：`lightweight` 表示核显 / 纯 CPU 也能跑，
        排序把轻量档放前面、再按体积从小到大。
        """
        found: list[dict] = []
        for item in _template_models(self.ctx):
            capabilities = [str(value) for value in (item.get("capabilities") or ())]
            if capability and capability not in capabilities:
                continue
            lightweight = bool(item.get("lightweight"))
            if lightweight_only and not lightweight:
                continue
            row = dict(item)
            row["capabilities"] = capabilities
            row["lightweight"] = lightweight
            record = self._template_record(row)
            row["registered_id"] = record.id if record is not None else ""
            row["state"] = record.state if record is not None else ""
            row["state_label"] = record.state_label if record is not None else "未登记"
            found.append(row)
        found.sort(key=lambda row: (not row["lightweight"], int(row.get("size_bytes") or 0), str(row.get("id") or "")))
        return found

    def create_from_template(self, key: str, name: str = "") -> ModelRecord:
        """按模板登记一条模型草稿：**只写登记表，不下载、不装包**。

        用户拿着这条草稿去模型页确认下载；已经登记过同一仓库的模板时直接返回旧记录。
        """
        row = _find_template(self.ctx, key)
        if row is None:
            raise ModelError(f"没有这个模型模板：{key}")
        existing = self._template_record(row)
        if existing is not None:
            return existing
        source = dict(row.get("source") or {})
        # 记下模板出处：以后按它精确认领，不用再猜文件名（BLIP / CLIP 的目录长得很像）
        source["template"] = str(row.get("id") or key)
        record = self.manager.add_local(
            name or str(row.get("name") or key),
            capabilities=tuple(row.get("capabilities") or ()),
            description=str(row.get("description") or ""),
            files=_template_files(source),
            source=source,
            runtime=dict(row.get("runtime") or {}),
        )
        return record

    def page_route(self) -> str:
        """模型管理页的路由，调用方可以直接 `ui.open_page(models.page_route())`。"""
        return f"plugin.{PAGE_KEY}"

    def requirements(self, model_id: str) -> dict:
        """这个模型还缺什么：权重文件、运行环境 profile 是否装好。

        `ok` 为真表示现在就能加载；否则 `missing` 里是人类可读的缺口清单。
        """
        record = self.manager.model_by_id(model_id)
        if record is None:
            return {"ok": False, "model_id": model_id, "missing": ["模型没有登记"], "hint": f"没有登记这个模型：{model_id}"}
        if record.is_external:
            return {"ok": True, "model_id": record.id, "state": record.state, "missing": [], "hint": "外部模型走 API，不需要本地运行环境"}
        source = dict(record.source or {})
        wanted = _template_files(source) or tuple(record.files)
        have = set(record.files)
        missing_files = [name for name in wanted if name not in have]
        profile_id = str((record.runtime or {}).get("profile") or "")
        from . import runtime as runtime_module

        profile = runtime_module.profile_of(profile_id, self.ctx) if profile_id else None
        # 同一后端的 CPU / GPU 两套 profile 装的是同一批包（GPU 版只是索引不同、多几个
        # CUDA 运行库），所以模型声明 `llama-cpp`、机器上只装了 `llama-cpp-gpu` 时也算装好：
        # 按实际命中的那套判状态，否则会报「缺 CPU 版运行环境」。
        effective_id = runtime_module.resolve_id(profile_id) if profile_id else ""
        installed = self._runtime_installed(effective_id) if effective_id else False
        used_id = effective_id if installed else ""
        missing: list[str] = []
        if missing_files:
            missing.append(f"缺模型文件：{_missing_text(missing_files)}")
        if not profile_id:
            missing.append("没登记推理后端 profile")
        elif profile is None:
            missing.append(f"运行环境清单里没有这个 profile：{profile_id}")
        elif not installed:
            missing.append(f"运行环境没装好：{profile.name if profile is not None else profile_id}")
        hint = "、".join(missing) or "已就绪"
        if not missing and used_id and used_id != profile_id:
            used = runtime_module.profile_of(used_id, self.ctx)
            hint = f"已就绪（用的是 {used.name if used is not None else used_id} 这套环境，与登记的 {profile_id} 共用同一批包）"
        return {
            "ok": not missing,
            "model_id": record.id,
            "state": record.state,
            "state_label": record.state_label,
            "profile": profile_id,
            "profile_effective": used_id or effective_id,
            "profile_ready": installed,
            "missing": missing,
            "missing_files": missing_files,
            "hint": hint,
        }

    def attach_downloads(self, factory) -> None:
        """模型页把自己的下载队列工厂（`ModelPage._download_manager`）挂上来。

        其它插件（自动标签 / 关键词）据此「一键下载」缺的权重，而不是自己开一套队列。
        """
        self._downloads_factory = factory

    def download(self, model_id: str) -> dict:
        """把某个本地模型缺的权重排进模型页那套下载队列（HF 主站 + 镜像回退、续传）。

        返回 `{"ok": bool, "model_id": str, "queued": [文件名], "hint": str}`；
        调用方负责先做一次用户确认——这里不弹窗、不替用户决定。
        """
        record = self.manager.model_by_id(model_id)
        if record is None:
            return {"ok": False, "model_id": model_id, "queued": [], "hint": f"没有登记这个模型：{model_id}"}
        if record.is_external:
            return {"ok": False, "model_id": record.id, "queued": [], "hint": "外部模型走 API，不需要下载权重"}
        factory = self._downloads_factory
        manager = factory() if callable(factory) else None
        if manager is None:
            return {
                "ok": False,
                "model_id": record.id,
                "queued": [],
                "hint": "模型页还没打开过，先打开一次「模型」页再试",
            }
        source = dict(record.source or {})
        wanted = _template_files(source) or tuple(record.files)
        have = set(record.files)
        missing = [name for name in wanted if name not in have]
        if not missing:
            return {"ok": True, "model_id": record.id, "queued": [], "hint": "权重已经齐了"}
        from pathlib import Path

        from .download import resolve_urls
        from .paths import local_dir
        from .settings import load_settings

        settings = load_settings()
        folder = local_dir(record.id)
        repo = str(source.get("repo") or "")
        revision = str(source.get("revision") or "main")
        queued: list[str] = []
        for name in missing:
            urls = resolve_urls(settings.download_base, settings.download_mirrors, repo, name, revision)
            target = folder / Path(name).name
            manager.enqueue(
                record.id,
                urls,
                target,
                sha256=str(record.sha256 or ""),
                total_bytes=int(record.size_bytes or 0) if len(missing) == 1 else 0,
                label=f"{record.name} · {target.name}",
            )
            queued.append(target.name)
        return {
            "ok": True,
            "model_id": record.id,
            "queued": queued,
            "hint": f"已加入下载队列：{_missing_text(queued)}",
        }

    def _template_record(self, row: dict) -> ModelRecord | None:
        """模板对应的已登记模型：按「登记出处 → 来源仓库 → 文件清单 → 名字」逐轮精确匹配。

        「文件名有交集就算同一个模型」这条判据必须去掉：BLIP 与 CLIP 的目录里都有 `config.json` /
        `preprocessor_config.json` / `tokenizer.json`，而登记表里第一条恰好是 BLIP——两个判据共用
        一次遍历时，任何模板扫到第一条记录就在交集上命中它，于是 `registered_id` 全被填成 blip，
        不同类型都显示成同一个模型（用户 m42266）。现在分四轮，每轮只认完全相等；都匹配不上返回
        `None`，让它显示「未登记」，也好过认错模型。
        """
        source = row.get("source") or {}
        key = str(row.get("id") or "")
        repo = str(source.get("repo") or "")
        wanted = set(_template_files(source))
        name = str(row.get("name") or "")
        records = list(self.manager.registry.all())
        for record in records:
            if key and str((record.source or {}).get("template") or "") == key:
                return record
        for record in records:
            if repo and str((record.source or {}).get("repo") or "") == repo:
                return record
        for record in records:
            # 只有「来源都没记下来」的老记录才靠文件清单认领：whisper-base 与 whisper-small 的清单一模一样
            if not str((record.source or {}).get("repo") or "") and wanted and set(record.files or ()) == wanted:
                return record
        for record in records:
            if name and record.name == name:
                return record
        return None

    # ------------------------------------------------------------ 管理（页面用）
    def add_local(self, *args, **kwargs):
        return self.manager.add_local(*args, **kwargs)

    def add_external(self, *args, **kwargs):
        return self.manager.add_external(*args, **kwargs)

    def update(self, record):
        return self.manager.update(record)

    def remove(self, model_id: str, *, delete_files: bool = False) -> bool:
        return self.manager.remove(model_id, delete_files=delete_files)

    def scan_directory(self, path, **kwargs):
        return self.manager.scan_directory(path, **kwargs)

    def unload(self, model_id: str) -> bool:
        return self.manager.unload(model_id)

    def unload_all(self) -> int:
        return self.manager.unload_all()

    def sweep_idle(self) -> int:
        return self.manager.sweep_idle()

    def running_info(self) -> dict:
        return self.manager.running_info()

    def save(self) -> bool:
        return self.manager.save()

    def reload(self) -> None:
        self.manager.reload()
        self.forget_runtime_cache()


# ------------------------------------------------------------------ 模板
def _template_models(ctx) -> list[dict]:
    """`data/model_list.json` 里的模板列表；读不到就返回空表。"""
    try:
        payload = ctx.data("model_list", {})
    except Exception:
        return []
    items = payload.get("models") if isinstance(payload, dict) else payload
    return [dict(item) for item in (items or []) if isinstance(item, dict)]


def _find_template(ctx, key: str) -> dict | None:
    wanted = str(key or "").strip().lower()
    for item in _template_models(ctx):
        for value in (item.get("id"), item.get("name")):
            if wanted and str(value or "").strip().lower() == wanted:
                return item
    return None


def _template_files(source: dict) -> tuple[str, ...]:
    """模板声明的权重文件名：单文件 `file` 与成套 `files` 合并去重。"""
    names: list[str] = []
    single = str(source.get("file") or "").strip()
    if single:
        names.append(single)
    for item in source.get("files") or ():
        text = str(item).strip()
        if text and text not in names:
            names.append(text)
    return tuple(names)
