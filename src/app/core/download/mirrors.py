"""下载镜像规则：按网址匹配规则，把请求地址展开成「官方 + 镜像」的有序候选列表。

规则是全局有序的——请求地址按顺序找第一条命中的规则，都没命中就用默认规则兜底。
规则里的每个地址项有两条写法：

* 含 `{url}` 占位符：整段替换请求地址（镜像前缀式），例如 GitHub 的
  `https://ghproxy.net/{url}`；
* 不含占位符：当作「官方前缀」，用「把官方前缀换成该项」的方式改写，例如
  HuggingFace 的 `https://hf-mirror.com`。

规则有两种启用模式：顺序模式先试官方、失败后按镜像列表顺序切换；镜像模式不碰官方，
直接用镜像列表顺序切换。

这里只做纯字符串推算，不碰网络、不碰界面，方便单测。
"""

from __future__ import annotations

import fnmatch
import urllib.parse
from dataclasses import dataclass, replace
from typing import Any, Iterable

__all__ = [
    "DEFAULT_MANIFEST_ID",
    "DEFAULT_RULE_ID",
    "MODE_LABELS",
    "MODE_MIRROR",
    "MODE_SEQUENTIAL",
    "MODES",
    "MirrorRule",
    "MirrorRules",
    "URL_PLACEHOLDER",
    "default_rules",
    "host_of",
]

#: 顺序模式：先试官方地址，失败后按镜像列表顺序切换
MODE_SEQUENTIAL = "sequential"
#: 镜像模式：不尝试官方地址，直接按镜像列表顺序切换
MODE_MIRROR = "mirror"
MODES = (MODE_SEQUENTIAL, MODE_MIRROR)
MODE_LABELS = {MODE_SEQUENTIAL: "顺序模式", MODE_MIRROR: "镜像模式"}

#: 地址项里的占位符：出现即代表「整段替换请求地址」
URL_PLACEHOLDER = "{url}"

#: 规则清单的清单 id（放 .configs/manifest 体系里，见 docs/MANIFEST_PROTOCOL.md）
DEFAULT_MANIFEST_ID = "core.download_mirrors"
#: 默认规则的固定 id（`pattern` 为空的规则就是默认规则）
DEFAULT_RULE_ID = "default"


def host_of(url: str) -> str:
    """取地址里的主机名（小写）；取不到就返回原串。"""
    return (urllib.parse.urlsplit(str(url or "")).netloc or str(url or "")).lower()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Iterable[Any]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values or ():
        text = _clean(value)
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _split_patterns(pattern: str) -> tuple[str, ...]:
    """一个规则可以写多个匹配模式，用空白或逗号分隔。"""
    text = str(pattern or "").replace(",", " ")
    return tuple(part for part in text.split() if part)


def _matches(pattern: str, url: str) -> bool:
    """按规则判断地址是否命中。

    * 模式里含 `/`：当完整网址匹配（支持 `*`、`?` 通配）；
    * 模式里含通配符：当主机名匹配；
    * 纯域名：主机名相同，或者是它的子域（`huggingface.co` 也命中
      `cdn-lfs.huggingface.co`）。
    """
    text = _clean(pattern)
    if not text:
        return False
    host = host_of(url)
    text_url = str(url or "").lower()
    for part in _split_patterns(text):
        item = part.lower()
        if "/" in item:
            if fnmatch.fnmatchcase(text_url, item):
                return True
        elif any(char in item for char in "*?["):
            if fnmatch.fnmatchcase(host, item):
                return True
        elif host == item or host.endswith("." + item):
            return True
    return False


def _rewrite(base: str, official: str, url: str) -> str:
    """按一个地址项改写请求地址；改不出来返回空串。

    三条路子依次试：带 `{url}` 占位符的直接整段替换；官方前缀对得上的换前缀；
    最后只对「官方主机本身或它的子域」退一步换主机名（这样 `cdn-lfs.huggingface.co`
    这类子域地址也能换到镜像站，而不会把别的站点误改）。
    """
    text = _clean(base)
    if not text:
        return ""
    if URL_PLACEHOLDER in text:
        return text.replace(URL_PLACEHOLDER, url)
    head = _clean(official).rstrip("/")
    if head and url.startswith(head):
        return text.rstrip("/") + url[len(head) :]
    base_parts = urllib.parse.urlsplit(text.rstrip("/"))
    head_parts = urllib.parse.urlsplit(head)
    url_parts = urllib.parse.urlsplit(url)
    if not (base_parts.netloc and head_parts.netloc and not base_parts.path):
        return ""
    host = url_parts.netloc.lower()
    target = head_parts.netloc.lower()
    if host != target and not host.endswith("." + target):
        return ""
    return urllib.parse.urlunsplit(
        (url_parts.scheme, base_parts.netloc, url_parts.path, url_parts.query, url_parts.fragment)
    )


@dataclass(frozen=True)
class MirrorRule:
    """一条镜像规则：匹配模式 + 官方地址 + 镜像列表 + 启用模式。"""

    id: str
    pattern: str = ""
    title: str = ""
    official: str = ""
    mirrors: tuple[str, ...] = ()
    mode: str = MODE_SEQUENTIAL
    enabled: bool = True

    @property
    def is_default(self) -> bool:
        """模式为空的规则就是兜底的默认规则。"""
        return not _clean(self.pattern)

    @property
    def label(self) -> str:
        """界面上的名字：优先用标题，其次用匹配模式。"""
        return _clean(self.title) or _clean(self.pattern) or "默认规则"

    @property
    def mode_label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    def normalized(self) -> MirrorRule:
        """把外部来的值收拾干净：去空白、去重地址项、非法模式退回顺序模式。"""
        return replace(
            self,
            id=_clean(self.id),
            pattern=_clean(self.pattern),
            title=_clean(self.title),
            official=_clean(self.official),
            mirrors=_clean_list(self.mirrors),
            mode=self.mode if self.mode in MODES else MODE_SEQUENTIAL,
        )

    def as_dict(self) -> dict:
        """转成可落盘的普通字典（清单项用 `key` 而不是 `id`）。"""
        return {
            "key": self.id,
            "title": self.title,
            "pattern": self.pattern,
            "official": self.official,
            "mirrors": list(self.mirrors),
            "mode": self.mode,
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> MirrorRule:
        """从清单项还原；`key` / `id` 都认。"""
        row = dict(payload) if isinstance(payload, dict) else {}
        mirrors = row.get("mirrors")
        if isinstance(mirrors, str):
            mirrors = [mirrors]
        return cls(
            id=_clean(row.get("id") or row.get("key")),
            pattern=_clean(row.get("pattern")),
            title=_clean(row.get("title")),
            official=_clean(row.get("official")),
            mirrors=_clean_list(mirrors if isinstance(mirrors, (list, tuple)) else ()),
            mode=_clean(row.get("mode")) or MODE_SEQUENTIAL,
            enabled=bool(row.get("enabled", True)),
        ).normalized()


#: 没写默认规则时用的兜底规则：不改写地址，原样使用
DEFAULT_RULE = MirrorRule(id=DEFAULT_RULE_ID, title="默认规则")


@dataclass(frozen=True)
class MirrorRules:
    """一组有序规则；顺序就是匹配优先级，`pattern` 为空的那条是默认规则。"""

    rules: tuple[MirrorRule, ...] = ()

    # ------------------------------------------------------------- 查询
    def ordered(self) -> tuple[MirrorRule, ...]:
        """参与匹配的规则（启用、非默认），按优先级。"""
        return tuple(rule.normalized() for rule in self.rules if rule.enabled and not rule.is_default)

    def default_rule(self) -> MirrorRule:
        """默认规则；用户没配就返回内置兜底规则。"""
        for rule in self.rules:
            if rule.is_default:
                return rule.normalized()
        return DEFAULT_RULE.normalized()

    def match(self, url: str) -> MirrorRule:
        """第一条命中的启用规则；都没命中返回默认规则。"""
        for rule in self.ordered():
            if _matches(rule.pattern, url):
                return rule
        return self.default_rule()

    def candidates(self, url: str) -> list[str]:
        """把地址展开成按顺序尝试的候选地址列表。

        顺序模式：官方（原地址）在前，镜像按列表顺序跟在后面；
        镜像模式：只给镜像，不碰官方。

        镜像模式下如果一条镜像都拼不出来，返回空列表——调用方据此报
        「没有可用的下载地址」，而不是悄悄退回官方。
        """
        text = _clean(url)
        if not text:
            return []
        rule = self.match(text)
        out: list[str] = []
        if rule.mode == MODE_SEQUENTIAL:
            # 官方地址就是调用方给的地址本身，原样排在最前（不做改写：改写的目的是
            # 把官方地址换到镜像站，而不是反过来把地址换成官方站）
            out.append(text)
        for base in rule.mirrors:
            item = _rewrite(base, rule.official, text)
            if item and item not in out:
                out.append(item)
        return out

    def explain(self, url: str) -> dict:
        """给界面用的一句话说明：这条地址会走哪条规则、按什么顺序试。"""
        rule = self.match(url)
        return {
            "rule": rule.id,
            "label": rule.label,
            "mode": rule.mode,
            "mode_label": rule.mode_label,
            "official": rule.official,
            "mirrors": list(rule.mirrors),
            "candidates": self.candidates(url),
        }

    # ------------------------------------------------------------- 落盘
    def as_items(self) -> list[dict]:
        """按当前顺序转成清单项列表。"""
        return [rule.normalized().as_dict() for rule in self.rules]

    @classmethod
    def from_items(cls, items: Iterable[Any]) -> MirrorRules:
        """从清单项列表还原；`items` 的文件顺序就是优先级。"""
        rules: list[MirrorRule] = []
        for row in items or ():
            rule = MirrorRule.from_dict(row)
            if rule.id:
                rules.append(rule)
        return cls(rules=tuple(rules))

    # ------------------------------------------------------------- 增删改
    def with_rule(self, rule: MirrorRule, *, index: int | None = None) -> MirrorRules:
        """新增或替换一条规则；同 id 覆盖原位置，`index` 指定则插到该位置。"""
        item = rule.normalized()
        rules = list(self.rules)
        for position, existing in enumerate(rules):
            if existing.normalized().id == item.id:
                if index is None:
                    rules[position] = item
                    return MirrorRules(rules=tuple(rules))
                del rules[position]
                break
        if index is None:
            rules.append(item)
        else:
            rules.insert(max(0, min(int(index), len(rules))), item)
        return MirrorRules(rules=tuple(rules))

    def without_rule(self, rule_id: str) -> MirrorRules:
        """删掉一条规则。"""
        key = _clean(rule_id)
        return MirrorRules(rules=tuple(rule for rule in self.rules if rule.normalized().id != key))

    def moved(self, rule_id: str, offset: int) -> MirrorRules:
        """把一条规则往上（负数）或往下（正数）挪；挪不动就原样返回。

        规则的先后就是优先级，所以「修改网址项顺序」既可以在规则内部调地址项，
        也可以在规则之间调先后。
        """
        key = _clean(rule_id)
        rules = [rule.normalized() for rule in self.rules]
        position = next((index for index, rule in enumerate(rules) if rule.id == key), -1)
        if position < 0:
            return self
        target = max(0, min(position + int(offset), len(rules) - 1))
        if target == position:
            return self
        rule = rules.pop(position)
        rules.insert(target, rule)
        return MirrorRules(rules=tuple(rules))


def default_rules() -> MirrorRules:
    """内置默认规则：HuggingFace 与 GitHub 各一条，改动前先兜底。"""
    return MirrorRules(
        rules=(
            MirrorRule(
                id="huggingface",
                title="HuggingFace",
                pattern="huggingface.co",
                official="https://huggingface.co",
                mirrors=("https://hf-mirror.com",),
            ),
            MirrorRule(
                id="github",
                title="GitHub",
                pattern="github.com githubusercontent.com",
                official="https://github.com",
                mirrors=(
                    "https://ghproxy.net/{url}",
                    "https://gh-proxy.com/{url}",
                    "https://ghfast.top/{url}",
                ),
            ),
        )
    )
