"""模型记录：一条模型登记项的字段、序列化与展示辅助。

`id` 形如 `local/<slug>` 或 `api/<slug>`，创建后不可改——其它插件按 id 或能力取用模型，
改名只改 `name`，不动磁盘目录，避免权重搬家。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.sdk.data import human_size

from .constants import (
    ADAPTER_OPENAI,
    ADAPTER_WORKER,
    CAPABILITY_LABELS,
    KIND_EXTERNAL,
    KIND_LABELS,
    KIND_LOCAL,
    KINDS,
    PLUGIN_ID,
    STATE_DRAFT,
    STATE_LABELS,
    STATE_READY,
)

__all__ = ["ModelRecord", "make_id", "now_text", "slugify"]

_SLUG_KEEP = re.compile(r"[^a-z0-9._-]+")
_SLUG_DASH = re.compile(r"-{2,}")


def slugify(name: str) -> str:
    """把模型名变成目录 / id 可用的 slug：小写、只留字母数字与 `._-`。"""
    text = str(name or "").strip().lower()
    text = _SLUG_KEEP.sub("-", text.replace(" ", "-"))
    text = _SLUG_DASH.sub("-", text).strip("-._")
    return text[:48] or "model"


def make_id(kind: str, name: str, existing=()) -> str:
    """生成 `kind/slug`；与已有 id 冲突时追加 `-2`、`-3`……"""
    prefix = kind if kind in KINDS else KIND_LOCAL
    base = slugify(name)
    taken = {str(item) for item in existing}
    candidate = f"{prefix}/{base}"
    index = 2
    while candidate in taken:
        candidate = f"{prefix}/{base}-{index}"
        index += 1
    return candidate


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


#: 权重文件后缀：`primary_file()` 先认这些（`files` 按名字排序，`.json` 会排在 `.bin` 前面）。
WEIGHT_SUFFIXES = (
    ".bin",
    ".safetensors",
    ".gguf",
    ".onnx",
    ".pt",
    ".pth",
    ".ckpt",
    ".msgpack",
    ".h5",
    ".tflite",
    ".mlmodel",
    ".npz",
    ".model",
)


@dataclass
class ModelRecord:
    """一条模型登记项。"""

    id: str
    name: str
    kind: str = KIND_LOCAL
    state: str = STATE_DRAFT
    capabilities: tuple[str, ...] = ()
    description: str = ""
    files: tuple[str, ...] = ()
    size_bytes: int = 0
    sha256: str = ""
    source: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)
    api: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------ 构造
    @classmethod
    def new_local(cls, name: str, *, existing=(), **fields) -> "ModelRecord":
        return cls(id=make_id(KIND_LOCAL, name, existing), name=name, kind=KIND_LOCAL, **fields).touch()

    @classmethod
    def new_external(cls, name: str, *, existing=(), **fields) -> "ModelRecord":
        return cls(id=make_id(KIND_EXTERNAL, name, existing), name=name, kind=KIND_EXTERNAL, **fields).touch()

    @classmethod
    def from_dict(cls, data: dict) -> "ModelRecord":
        payload = data if isinstance(data, dict) else {}
        capabilities = payload.get("capabilities") or ()
        if isinstance(capabilities, str):
            capabilities = [item.strip() for item in capabilities.split(",") if item.strip()]

        def _dict(key: str) -> dict:
            value = payload.get(key)
            return dict(value) if isinstance(value, dict) else {}

        return cls(
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            kind=str(payload.get("kind") or KIND_LOCAL),
            state=str(payload.get("state") or STATE_DRAFT),
            capabilities=tuple(str(item) for item in capabilities),
            description=str(payload.get("description") or ""),
            files=tuple(str(item) for item in (payload.get("files") or ())),
            size_bytes=int(payload.get("size_bytes") or 0),
            sha256=str(payload.get("sha256") or ""),
            source=_dict("source"),
            runtime=_dict("runtime"),
            api=_dict("api"),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "capabilities": list(self.capabilities),
            "description": self.description,
            "files": list(self.files),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "source": dict(self.source),
            "runtime": dict(self.runtime),
            "api": dict(self.api),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def touch(self) -> "ModelRecord":
        stamp = now_text()
        self.created_at = self.created_at or stamp
        self.updated_at = stamp
        return self

    # ------------------------------------------------------------ 展示
    @property
    def is_local(self) -> bool:
        return self.kind == KIND_LOCAL

    @property
    def is_external(self) -> bool:
        return self.kind == KIND_EXTERNAL

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def size_text(self) -> str:
        return human_size(self.size_bytes) if self.size_bytes else "—"

    @property
    def capability_text(self) -> str:
        return "、".join(CAPABILITY_LABELS.get(item, item) for item in self.capabilities) or "未标注能力"

    @property
    def adapter(self) -> str:
        if self.is_external:
            return str(self.api.get("adapter") or ADAPTER_OPENAI)
        return str(self.runtime.get("adapter") or ADAPTER_WORKER)

    @property
    def backend(self) -> str:
        return str(self.runtime.get("backend") or "")

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY

    @property
    def plugin_id(self) -> str:
        """提供这条记录的插件 id（门面协议 `ModelInfo.plugin_id`）。"""
        return PLUGIN_ID

    # ------------------------------------------------------------ 查询
    def matches(self, kind: str = "", capability: str = "") -> bool:
        if kind and self.kind != kind:
            return False
        if capability and capability not in self.capabilities:
            return False
        return True

    def local_path(self):
        """本地模型的权重目录；外部模型返回 None。

        扫描登记的模型（`source.path`）留在用户原来的目录，下载的模型才用
        `.resources/models/local/<id>`——否则「扫描目录」登记完就等于把用户的目录搬走了。
        """
        if not self.is_local:
            return None
        from .paths import local_dir

        scanned = str(self.source.get("path") or "")
        if scanned and Path(scanned).is_dir():
            return Path(scanned)
        return local_dir(self.id)

    def primary_file(self) -> Path | None:
        """本地模型的主文件：先挑权重后缀，再退回第一个存在的文件。

        `files` 按名字排序，`config.json` 会排在 `.bin` 前面——直接把第一个当主文件会把配置喂给
        推理后端（faster-whisper 会拿它当尺寸名，报「Invalid model size …」），所以先认权重后缀。
        """
        base = self.local_path()
        if base is None:
            return None
        for name in self.files:
            candidate = base / name
            if candidate.is_file() and candidate.suffix.lower() in WEIGHT_SUFFIXES:
                return candidate
        for name in self.files:
            candidate = base / name
            if candidate.is_file():
                return candidate
        if base.is_dir():
            found = sorted(item for item in base.rglob("*") if item.is_file())
            if found:
                return found[0]
        return None

    def file_names(self) -> tuple[str, ...]:
        """权重目录里的文件（相对目录名），用于登记与校验。"""
        base = self.local_path()
        if base is None or not base.is_dir():
            return ()
        return tuple(sorted(item.relative_to(base).as_posix() for item in base.rglob("*") if item.is_file()))

    def sync_files(self) -> "ModelRecord":
        """按磁盘实际内容刷新 files 与大小（扫描 / 下载完成后调用）。"""
        names = self.file_names()
        self.files = names
        base = self.local_path()
        if base is not None:
            self.size_bytes = sum((base / name).stat().st_size for name in names if (base / name).is_file())
        return self.touch()

    # ------------------------------------------------------------ 外部模型
    def api_headers(self) -> dict:
        headers = dict(self.api.get("headers") or {})
        return {str(key): str(value) for key, value in headers.items()}

    def api_params(self) -> dict:
        params = self.api.get("params")
        return dict(params) if isinstance(params, dict) else {}
