"""插件设置：读写 `.configs/models.json`（下载源、镜像、代理、运行策略与密钥）。

密钥只在这里落盘，`registry.json` 只存引用名；页面与日志一律用 `masked_secret()` 脱敏。
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from app.sdk import storage

from .constants import DEFAULT_CONCURRENT, DEFAULT_HEARTBEAT_SEC, DEFAULT_IDLE_UNLOAD_SEC, DEFAULT_MAX_RESIDENT
from .paths import settings_file

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_DOWNLOAD_SOURCE",
    "DEFAULT_GITHUB_MIRRORS",
    "DEFAULT_GITHUB_SOURCE",
    "DEFAULT_MIRRORS",
    "DEFAULT_PIP_MIRROR",
    "DOWNLOAD_SOURCES",
    "DOWNLOAD_SOURCE_KEYS",
    "GITHUB_HOSTS",
    "GITHUB_MIRRORS",
    "GITHUB_SOURCES",
    "GITHUB_SOURCE_KEYS",
    "LEGACY_GITHUB_SOURCES",
    "PIP_MIRRORS",
    "PIP_MIRROR_KEYS",
    "ModelSettings",
    "github_prefixes",
    "github_url",
    "load_settings",
    "mask_secret",
    "normalize_github_source",
    "pip_mirror_url",
]

#: 默认总模型下载网址（用户可在设置里改）
DEFAULT_BASE_URL = "https://huggingface.co"
#: 默认总镜像下载网址（按顺序回退）
DEFAULT_MIRRORS = ("https://hf-mirror.com",)
#: 模型下载源：设置页下拉的固定选项，(key, 显示名)
DOWNLOAD_SOURCES: tuple[tuple[str, str], ...] = (
    ("official", "HuggingFace官方"),
    ("mirror", "HF-Mirror镜像"),
    ("custom", "自定义"),
)
DOWNLOAD_SOURCE_KEYS = tuple(item[0] for item in DOWNLOAD_SOURCES)
DEFAULT_DOWNLOAD_SOURCE = "official"
#: pip 安装源：设置页下拉的固定选项，(key, 显示名, index-url)；官方源留空走 pip 自己的默认
PIP_MIRRORS: tuple[tuple[str, str, str], ...] = (
    ("official", "官方 PyPI（默认）", ""),
    ("tuna", "清华 TUNA", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("aliyun", "阿里云", "https://mirrors.aliyun.com/pypi/simple/"),
    ("ustc", "中科大", "https://pypi.mirrors.ustc.edu.cn/simple/"),
    ("custom", "自定义地址", ""),
)
PIP_MIRROR_KEYS = tuple(item[0] for item in PIP_MIRRORS)
DEFAULT_PIP_MIRROR = "official"


#: GitHub 下载源：设置页下拉的固定选项，(key, 显示名)；每个镜像单独一项，选哪项就用哪个前缀
GITHUB_SOURCES: tuple[tuple[str, str], ...] = (
    ("official", "Github官方"),
    ("ghproxy", "ghproxy镜像"),
    ("gh_proxy", "gh-proxy镜像"),
    ("ghfast", "ghfast镜像"),
    ("custom", "自定义网址"),
)
GITHUB_SOURCE_KEYS = tuple(item[0] for item in GITHUB_SOURCES)
DEFAULT_GITHUB_SOURCE = "official"
#: 镜像选项 → **前缀式**代理地址：把原始 GitHub 地址直接接在后面即可，
#: 例如 `https://ghproxy.net/https://github.com/owner/repo/releases/download/v1/pack.whl`。
GITHUB_MIRRORS: dict[str, str] = {
    "ghproxy": "https://ghproxy.net",
    "gh_proxy": "https://gh-proxy.com",
    "ghfast": "https://ghfast.top",
}
#: 程序内置的全部镜像前缀（按 `GITHUB_SOURCES` 的顺序）
DEFAULT_GITHUB_MIRRORS = tuple(GITHUB_MIRRORS[key] for key in GITHUB_SOURCE_KEYS if key in GITHUB_MIRRORS)
#: 旧版本只有一个「程序提供的 GitHub 镜像」选项，读回旧值时落到第一个镜像
LEGACY_GITHUB_SOURCES = {"mirror": "ghproxy"}
#: 会被当成「GitHub 资源」而套上镜像前缀的域名
GITHUB_HOSTS = (
    "github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "gist.githubusercontent.com",
)


def is_github_url(url: str) -> bool:
    """地址是不是 GitHub 上的资源（决定要不要套镜像前缀）。"""
    host = urllib.parse.urlsplit(str(url or "").strip()).netloc.lower().split(":")[0]
    return any(host == item or host.endswith("." + item) for item in GITHUB_HOSTS)


def normalize_github_source(value: str) -> str:
    """把设置里读回的值规整成合法的 GitHub 下载源 key：旧值迁移，认不出就回落官方。"""
    key = str(value or "").strip()
    key = LEGACY_GITHUB_SOURCES.get(key, key)
    return key if key in GITHUB_SOURCE_KEYS else DEFAULT_GITHUB_SOURCE


def github_prefixes(source: str, custom: str = "") -> tuple[str, ...]:
    """把「GitHub 下载源」换算成一串前缀，按顺序回退；空串代表直连官方。

    官方 = 只用原地址；选中的镜像 = 该镜像 + 官方直连兜底；
    自定义 = 用户填的地址 + 官方直连兜底（没填就只剩官方）。
    """
    key = normalize_github_source(source)
    prefix = GITHUB_MIRRORS.get(key)
    if prefix:
        return (prefix, "")
    if key == "custom":
        clean = str(custom or "").strip().rstrip("/")
        return (clean, "") if clean else ("",)
    return ("",)


def github_url(url: str, source: str = DEFAULT_GITHUB_SOURCE, custom: str = "") -> tuple[str, ...]:
    """把一个 GitHub 地址展开成「按设置好的下载源」排好序的候选地址。

    非 GitHub 地址原样返回一条；空地址返回空。
    """
    text = str(url or "").strip()
    if not text:
        return ()
    if not is_github_url(text):
        return (text,)
    result: list[str] = []
    for prefix in github_prefixes(source, custom):
        candidate = text if not prefix else f"{prefix}/{text}"
        if candidate not in result:
            result.append(candidate)
    return tuple(result)


def pip_mirror_url(key: str, custom: str = "") -> str:
    """把设置里的安装源换算成 pip 的 --index-url；返回空串表示交给 pip 自己的默认源。"""
    name = str(key or DEFAULT_PIP_MIRROR)
    if name == "custom":
        return str(custom or "").strip()
    for item_key, _label, url in PIP_MIRRORS:
        if item_key == name:
            return url
    return ""


def mask_secret(value: str) -> str:
    """日志 / 页面显示用的脱敏串：只留前 3 位。"""
    text = str(value or "")
    if not text:
        return ""
    return text[:3] + "***" if len(text) > 3 else "***"


@dataclass
class ModelSettings:
    """插件设置的内存视图：三段配置 + 密钥表。"""

    download: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------- 读
    @classmethod
    def parse(cls, payload: Any) -> "ModelSettings":
        data = payload if isinstance(payload, dict) else {}
        download = data.get("download") if isinstance(data.get("download"), dict) else {}
        runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
        secrets = data.get("secrets") if isinstance(data.get("secrets"), dict) else {}
        return cls(
            download=dict(download),
            runtime=dict(runtime),
            secrets={str(key): str(value) for key, value in secrets.items()},
        )

    @classmethod
    def load(cls) -> "ModelSettings":
        return cls.parse(storage.read_json(settings_file(), {}))

    def to_dict(self) -> dict[str, Any]:
        return {"download": dict(self.download), "runtime": dict(self.runtime), "secrets": dict(self.secrets)}

    def save(self) -> bool:
        return storage.write_json(settings_file(), self.to_dict())

    # ------------------------------------------------------------- 下载段
    @property
    def base_url(self) -> str:
        return str(self.download.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    @base_url.setter
    def base_url(self, value: str) -> None:
        self.download["base_url"] = str(value or "") or DEFAULT_BASE_URL

    @property
    def mirrors(self) -> tuple[str, ...]:
        raw = self.download.get("mirrors")
        if raw is None:
            raw = list(DEFAULT_MIRRORS)
        if isinstance(raw, str):
            raw = [item.strip() for item in raw.split(",")]
        return tuple(str(item).rstrip("/") for item in raw or () if str(item).strip())

    def set_mirrors(self, mirrors) -> None:
        self.download["mirrors"] = [str(item).strip() for item in mirrors or () if str(item).strip()]

    @property
    def download_source(self) -> str:
        """下载源（`DOWNLOAD_SOURCES` 里的 key）：总站 / 镜像 / 自定义。"""
        value = str(self.download.get("source") or DEFAULT_DOWNLOAD_SOURCE)
        return value if value in DOWNLOAD_SOURCE_KEYS else DEFAULT_DOWNLOAD_SOURCE

    @download_source.setter
    def download_source(self, value: str) -> None:
        key = str(value or DEFAULT_DOWNLOAD_SOURCE)
        self.download["source"] = key if key in DOWNLOAD_SOURCE_KEYS else DEFAULT_DOWNLOAD_SOURCE

    @property
    def custom_downloads(self) -> tuple[str, ...]:
        """用户自己「添加下载路径」攒起来的地址，按添加顺序回退。"""
        raw = self.download.get("custom")
        if isinstance(raw, str):
            raw = [item.strip() for item in raw.split(",")]
        return tuple(str(item).rstrip("/") for item in (raw or ()) if str(item).strip())

    def set_custom_downloads(self, values) -> None:
        self.download["custom"] = [str(item).strip() for item in values or () if str(item).strip()]

    @property
    def download_base(self) -> str:
        """当前生效的下载主地址：跟着「下载源」下拉走。"""
        source = self.download_source
        if source == "mirror":
            mirrors = self.mirrors
            return mirrors[0] if mirrors else DEFAULT_BASE_URL
        if source == "custom":
            custom = self.custom_downloads
            return custom[0] if custom else self.base_url
        return self.base_url

    @property
    def download_mirrors(self) -> tuple[str, ...]:
        """主地址之外的备用地址，按顺序回退；不会把主地址自己再列一遍。"""
        pool = self.mirrors
        if self.download_source == "custom":
            pool = self.custom_downloads + self.mirrors
        base = self.download_base
        return tuple(item for item in pool if item and item != base)

    @property
    def github_source(self) -> str:
        """GitHub 下载源（`GITHUB_SOURCES` 里的 key）：官方 / 某个镜像 / 自定义网址。"""
        return normalize_github_source(self.download.get("github_source"))

    @github_source.setter
    def github_source(self, value: str) -> None:
        self.download["github_source"] = normalize_github_source(value)

    @property
    def github_custom(self) -> str:
        """自定义 GitHub 镜像前缀（只有 `github_source == "custom"` 时才会被用上）。"""
        return str(self.download.get("github_custom") or "").strip()

    @github_custom.setter
    def github_custom(self, value: str) -> None:
        self.download["github_custom"] = str(value or "").strip().rstrip("/")

    @property
    def github_prefixes(self) -> tuple[str, ...]:
        """当前生效的 GitHub 前缀链（第一个是首选，最后兜底回官方直连）。"""
        return github_prefixes(self.github_source, self.github_custom)

    def github_urls(self, url: str) -> tuple[str, ...]:
        """按当前 GitHub 下载源展开一个地址；非 GitHub 地址原样返回。"""
        return github_url(url, self.github_source, self.github_custom)

    @property
    def proxy(self) -> str:
        return str(self.download.get("proxy") or "")

    @property
    def concurrent(self) -> int:
        try:
            return max(1, min(3, int(self.download.get("concurrent") or DEFAULT_CONCURRENT)))
        except (TypeError, ValueError):
            return DEFAULT_CONCURRENT

    # ------------------------------------------------------------- 运行段
    @property
    def max_resident(self) -> int:
        try:
            return max(1, int(self.runtime.get("max_resident") or DEFAULT_MAX_RESIDENT))
        except (TypeError, ValueError):
            return DEFAULT_MAX_RESIDENT

    @property
    def idle_unload_sec(self) -> int:
        try:
            return max(0, int(self.runtime.get("idle_unload_sec", DEFAULT_IDLE_UNLOAD_SEC)))
        except (TypeError, ValueError):
            return DEFAULT_IDLE_UNLOAD_SEC

    @property
    def heartbeat_sec(self) -> int:
        try:
            return max(1, int(self.runtime.get("heartbeat_sec") or DEFAULT_HEARTBEAT_SEC))
        except (TypeError, ValueError):
            return DEFAULT_HEARTBEAT_SEC

    @property
    def device(self) -> str:
        return str(self.runtime.get("device") or "auto")

    @property
    def allow_system_env(self) -> bool:
        return bool(self.runtime.get("allow_system_env"))

    @property
    def pip_mirror(self) -> str:
        """pip 安装源（`PIP_MIRRORS` 里的 key）。"""
        value = str(self.runtime.get("pip_mirror") or DEFAULT_PIP_MIRROR)
        return value if value in PIP_MIRROR_KEYS else DEFAULT_PIP_MIRROR

    @pip_mirror.setter
    def pip_mirror(self, value: str) -> None:
        key = str(value or DEFAULT_PIP_MIRROR)
        self.runtime["pip_mirror"] = key if key in PIP_MIRROR_KEYS else DEFAULT_PIP_MIRROR

    @property
    def pip_mirror_custom(self) -> str:
        """自定义安装源地址（只有 `pip_mirror == "custom"` 时才会被用上）。"""
        return str(self.runtime.get("pip_mirror_custom") or "")

    @pip_mirror_custom.setter
    def pip_mirror_custom(self, value: str) -> None:
        self.runtime["pip_mirror_custom"] = str(value or "").strip()

    @property
    def index_url(self) -> str:
        """最终拼进 pip 安装命令的索引地址；空串表示用 pip 默认源。"""
        return pip_mirror_url(self.pip_mirror, self.pip_mirror_custom)

    # ------------------------------------------------------------- 密钥
    def secret(self, ref: str) -> str:
        return str(self.secrets.get(str(ref or ""), ""))

    def set_secret(self, ref: str, value: str) -> None:
        key = str(ref or "")
        if not key:
            return
        if value:
            self.secrets[key] = str(value)
        else:
            self.secrets.pop(key, None)

    def masked(self, ref: str) -> str:
        return mask_secret(self.secret(ref))


def load_settings() -> ModelSettings:
    """读设置；坏了就用默认值，不影响插件启动。"""
    return ModelSettings.load()
