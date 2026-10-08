"""镜像规则的清单读写。

规则是一份有序列表（列表顺序就是匹配优先级），结构化、可能被外部替换，按
`docs/MANIFEST_PROTOCOL.md` §1 的边界进清单 `core.download_mirrors`
（`.configs/download_mirrors.json`）；并行数、代理这些标量留在 QConfig 的 `Download` 组。

登记和临时清单（`app.core.journals`）走同一条路：运行期登记、进程一退登记就没，所以
这里每次都按 id 现查 —— 文件还在、登记没了就重新登记，读不出来就退回内置默认规则
（一份坏 JSON 不该让下载功能起不来）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..manifest import (
    MANIFEST_VERSION,
    ManifestEntry,
    ManifestError,
    manifest_kit,
)
from .mirrors import (
    DEFAULT_MANIFEST_ID,
    MODE_LABELS,
    MODES,
    URL_PLACEHOLDER,
    MirrorRules,
    default_rules,
)

#: 清单类型：这里存的是「注册表」（一组枚举出来的规则），不是模块数据
MANIFEST_KIND = "registry"
MANIFEST_OWNER = "core"
MANIFEST_FILE_NAME = "download_mirrors.json"
MANIFEST_TITLE = "下载镜像规则"

_logger = logging.getLogger(__name__)


def manifest_path() -> Path:
    """规则文件的位置（`.configs/download_mirrors.json`，随配置一起搬）。"""
    from ..runtime import paths

    return paths.CONFIG_DIR / MANIFEST_FILE_NAME


def _entry() -> ManifestEntry:
    return ManifestEntry(
        id=DEFAULT_MANIFEST_ID,
        path=manifest_path(),
        kind=MANIFEST_KIND,
        owner=MANIFEST_OWNER,
        description="下载镜像规则：官方网址、镜像网址列表、启用模式与顺序",
    )


def ensure_registered() -> bool:
    """保证规则清单已在登记表里；返回是否由这次调用登记上的。"""
    if DEFAULT_MANIFEST_ID in manifest_kit.ids():
        return False
    try:
        manifest_kit.register(_entry(), source=__name__)
    except ValueError:  # 并发下另一个线程刚登记过，等价于登记成功
        _logger.debug("镜像规则清单已登记：%s", DEFAULT_MANIFEST_ID)
        return False
    return True


# --------------------------------------------------------------------------- 读
def load_rules() -> MirrorRules:
    """读镜像规则。

    还没写过（文件不存在）或者文件坏了，都用 `default_rules()`：内置的 HuggingFace
    与 GitHub 两套规则，保证「第一次打开就有东西可选」。
    """
    if not manifest_path().is_file():
        return default_rules()
    ensure_registered()
    try:
        payload: Any = manifest_kit.raw(DEFAULT_MANIFEST_ID)
    except ManifestError as exc:
        _logger.warning("镜像规则读不出来，先用内置默认规则：%s", exc)
        return default_rules()
    if not isinstance(payload, dict):
        return default_rules()
    return MirrorRules.from_items(payload.get("items") or ())


# --------------------------------------------------------------------------- 写
def payload_for(rules: MirrorRules) -> dict:
    """把规则转成一份完整清单内容（写盘前先过 schema 校验）。"""
    return {
        "manifest": MANIFEST_VERSION,
        "id": DEFAULT_MANIFEST_ID,
        "version": "1",
        "kind": MANIFEST_KIND,
        "title": MANIFEST_TITLE,
        "description": "下载镜像规则：官方网址、镜像网址列表、启用模式与顺序",
        "items": rules.as_items(),
    }


def save_rules(rules: MirrorRules) -> MirrorRules:
    """整份覆盖落盘（每次变更前由清单工具包自动备份），返回落盘后的规则。"""
    ensure_registered()
    manifest_kit.write(DEFAULT_MANIFEST_ID, payload_for(rules))
    return load_rules()


def reset_rules() -> MirrorRules:
    """恢复内置默认规则（删掉清单文件，回到「还没写过」的状态）。"""
    path = manifest_path()
    if path.is_file():
        path.unlink()
    if DEFAULT_MANIFEST_ID in manifest_kit.ids():
        manifest_kit.drop(DEFAULT_MANIFEST_ID)
    return default_rules()


# --------------------------------------------------------------- 给界面用的只读信息
def mode_options() -> list[tuple[str, str]]:
    """启用模式的可选项（值, 展示名），顺序即界面顺序。"""
    return [(mode, MODE_LABELS.get(mode, mode)) for mode in MODES]


def placeholder() -> str:
    """镜像网址里可用的占位符（`{url}` 表示原始地址整段替换）。"""
    return URL_PLACEHOLDER


def rule_hint() -> str:
    """规则「匹配范围」输入框的说明文案。"""
    return (
        "写域名（huggingface.co）即匹配该域名及其子域；写 *.example.com 匹配主机名；"
        f"写完整网址前缀即按前缀替换。镜像网址里的 {URL_PLACEHOLDER} 表示原始下载地址。"
    )


__all__ = [
    "MANIFEST_TITLE",
    "ensure_registered",
    "load_rules",
    "manifest_path",
    "mode_options",
    "payload_for",
    "placeholder",
    "reset_rules",
    "rule_hint",
    "save_rules",
]
