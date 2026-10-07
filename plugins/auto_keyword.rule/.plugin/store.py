"""自动关键词（规则）插件的持久化：关键词库与关键词规则都落在程序配置目录里。

程序本体没有关键词库——库是这个插件自己的东西：

- `.configs/autolabel.keywords.json`：关键词库（用户手工增删 + 扫描现有条目得来）。
- `.configs/autolabel.keyword-rules.json`：用户规则（`RuleSet.user_payload`）。**刻意和标签那份
  `autolabel.rules.json` 分开存**：两边共用同一套规则模型，但标签规则与关键词规则是两回事，
  混在一个文件里会互相顶掉。

出厂规则写在代码里（`FACTORY_RULES`），不占 `.data/`：这样插件根目录里只有
`plugin.py` / `plugin.json` / `PLUGIN.md` / `.plugin/`，符合插件布局自检。
规则模型与匹配引擎直接复用共享库 `dm_plugin.lib.autolabel.rules`。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.sdk import storage

from dm_plugin.lib.autolabel.rules import (
    FIELD_NAME,
    FIELD_SUFFIX,
    KIND_MATCH,
    OP_CONTAINS,
    OP_IN,
    SOURCE_FACTORY,
    Rule,
    RuleSet,
)

__all__ = [
    "FACTORY_RULES",
    "KEYWORDS_FILE",
    "RULES_FILE",
    "keywords_file",
    "load_library",
    "load_rules",
    "merge_keywords",
    "normalize_keywords",
    "rules_file",
    "save_library",
    "save_rules",
]

#: 关键词库文件（程序配置目录下）。
KEYWORDS_FILE = "autolabel.keywords.json"
#: 关键词规则文件（程序配置目录下）。
RULES_FILE = "autolabel.keyword-rules.json"
#: 两个文件的版本号，以后改结构时用得上。
LIBRARY_VERSION = 1


def keywords_file():
    """关键词库文件路径。"""
    return storage.config_file(KEYWORDS_FILE)


def rules_file():
    """关键词规则文件路径。"""
    return storage.config_file(RULES_FILE)


def _factory_rule(
    key: str,
    name: str,
    *,
    field: str = FIELD_SUFFIX,
    op: str = OP_IN,
    pattern: str = "",
    keywords: Sequence[str] = (),
) -> Rule:
    return Rule(
        key=key,
        name=name,
        kind=KIND_MATCH,
        enabled=True,
        field=field,
        op=op,
        pattern=pattern,
        tags=tuple(keywords),
        source=SOURCE_FACTORY,
    )


#: 出厂规则：按文件后缀（以及一条截图文件名）挂常见关键词。用户改过就存进 `.configs`，
#: 恢复出厂只删用户那份覆盖，不动这里的代码。
FACTORY_RULES: tuple[Rule, ...] = (
    _factory_rule(
        "image",
        "图片文件",
        pattern="jpg,jpeg,png,gif,bmp,webp,svg,heic,tif,tiff",
        keywords=("图片",),
    ),
    _factory_rule(
        "video",
        "视频文件",
        pattern="mp4,mkv,avi,mov,wmv,flv,webm,m4v",
        keywords=("视频",),
    ),
    _factory_rule(
        "audio",
        "音频文件",
        pattern="mp3,wav,flac,aac,ogg,m4a,wma",
        keywords=("音频",),
    ),
    _factory_rule(
        "document",
        "文档文件",
        pattern="doc,docx,pdf,txt,md,rtf,odt",
        keywords=("文档",),
    ),
    _factory_rule(
        "sheet",
        "表格文件",
        pattern="xls,xlsx,csv,ods",
        keywords=("表格",),
    ),
    _factory_rule(
        "slide",
        "演示文件",
        pattern="ppt,pptx,odp",
        keywords=("演示文稿",),
    ),
    _factory_rule(
        "archive",
        "压缩包",
        pattern="zip,rar,7z,tar,gz,bz2,xz",
        keywords=("压缩包",),
    ),
    _factory_rule(
        "code",
        "代码文件",
        pattern="py,js,ts,java,c,cpp,h,hpp,cs,go,rs,rb,php,sh,ps1,bat,sql,html,css,json,yaml,yml,toml",
        keywords=("代码",),
    ),
    _factory_rule(
        "screenshot",
        "文件名带「截图」",
        field=FIELD_NAME,
        op=OP_CONTAINS,
        pattern="截图",
        keywords=("截图",),
    ),
)


def normalize_keywords(words: Iterable[str], *, limit: int = 0) -> tuple[str, ...]:
    """去掉空白与重复（不区分大小写）后的关键词，保持出现顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for word in words:
        text = str(word).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    if limit > 0:
        return tuple(result[: int(limit)])
    return tuple(result)


def merge_keywords(*groups: Iterable[str]) -> tuple[str, ...]:
    """把几组关键词并成一个去重列表（库里已有的 + 这次新加的）。"""
    merged: list[str] = []
    for group in groups:
        merged.extend(group)
    return normalize_keywords(merged)


def _payload_words(payload: Any) -> tuple[str, ...]:
    if isinstance(payload, dict):
        return normalize_keywords(payload.get("keywords") or ())
    if isinstance(payload, (list, tuple)):
        return normalize_keywords(payload)
    return ()


def load_library() -> tuple[str, ...]:
    """读关键词库；文件不存在或坏了就当作空库。"""
    try:
        payload = storage.read_json(keywords_file(), None)
    except Exception:  # noqa: BLE001 - 读库失败不该让页面打不开
        return ()
    return _payload_words(payload)


def save_library(words: Iterable[str]) -> bool:
    """写关键词库；失败返回 False（调用方给提示）。"""
    payload = {"version": LIBRARY_VERSION, "keywords": list(normalize_keywords(words))}
    try:
        return bool(storage.write_json(keywords_file(), payload))
    except Exception:  # noqa: BLE001 - 写失败只报告，不冒泡
        return False


def load_rules() -> RuleSet:
    """读出「出厂规则 + 用户改过的部分」；文件坏了就只剩下出厂规则。"""
    try:
        payload = storage.read_json(rules_file(), None)
    except Exception:  # noqa: BLE001 - 同上
        payload = None
    if not isinstance(payload, dict):
        return RuleSet(factory=FACTORY_RULES)
    return RuleSet.from_payload(payload, factory=FACTORY_RULES)


def save_rules(rule_set: RuleSet) -> bool:
    """写用户规则（与出厂不同的部分 + 被藏起来的出厂 key）。"""
    try:
        return bool(storage.write_json(rules_file(), rule_set.user_payload))
    except Exception:  # noqa: BLE001 - 同上
        return False
