"""插件基类：所有插件都必须继承 :class:`Plugin`。

元信息（id/name/version/路径/清单）由核心在构造之后注入，插件只写行为：

    from dm_plugin.builtin.lib.viewer.viewer_kind import ViewerPlugin

    class ImagePlugin(ViewerPlugin):
        def create_view(self, path, parent=None):
            ...
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping

from loguru import logger

from .errors import SdkError

__all__ = ["Plugin", "collect_plugin_classes"]

_MISSING = object()


class Plugin:
    """插件父类：生命周期钩子 + 清单数据访问。"""

    # 以下属性由核心在构造后注入（只读）
    id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    path: Path = Path(".")
    manifest: Mapping[str, Any] = {}

    def __init__(self) -> None:
        self._data_cache: dict[str, Any] = {}

    def attach(
        self,
        *,
        id: str,
        name: str = "",
        version: str = "",
        description: str = "",
        author: str = "",
        path: Path | None = None,
        manifest: Mapping[str, Any] | None = None,
    ) -> None:
        """由核心在构造之后注入元信息（插件自己不调用）。"""
        self.id = str(id)
        self.name = str(name or id)
        self.version = str(version)
        self.description = str(description)
        self.author = str(author)
        if path is not None:
            self.path = Path(path)
        self.manifest = dict(manifest or {})
        self._data_cache = {}

    # ---- 生命周期（子类按需重写） -----------------------------------

    def setup(self, ctx: "Any") -> None:
        """载入时注册贡献、读配置；默认什么都不做。"""

    def teardown(self) -> None:
        """卸载时撤销自己的东西；默认什么都不做（上下文里的贡献由核心自动撤销）。"""

    def describe(self) -> list[tuple[str, str]]:
        """插件页展示的额外信息行 `[(标题, 内容), ...]`；默认空。"""
        return []

    # ---- 插件目录与数据文件 ----------------------------------------

    @property
    def log(self):
        """带插件 id 的日志器。"""
        return logger.bind(plugin=self.id or "?")

    @property
    def data_dir(self) -> Path:
        """插件的数据目录（协议 v2 固定为 `<插件目录>/.data`）。"""
        return Path(self.path) / ".data"

    def data_path(self, key: str) -> Path | None:
        """`key` 对应的数据文件：`.data/<key>`，或 `.data/<key>.json`。

        需要完整文件名时直接写 `data_path("name.json")`；两者都不存在返回 None。
        """
        folder = self.data_dir
        name = str(key)
        if not name:
            return None
        direct = folder / name
        if direct.is_file():
            return direct
        if not direct.suffix:
            candidate = direct.with_suffix(".json")
            if candidate.is_file():
                return candidate
            return candidate
        return direct

    def data(self, key: str, default: Any = _MISSING) -> Any:
        """读取并缓存数据文件（.json 自动解析，其余按文本读）。"""
        path = self.data_path(key)
        if path is None or not path.exists():
            if default is _MISSING:
                raise SdkError(f"插件 {self.id or '?'} 缺少数据文件：{key}")
            return default
        cache_key = str(key)
        if cache_key in self._data_cache:
            return self._data_cache[cache_key]
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".json":
                from ..core.runtime import jsonio  # 数据文件按 orjson 解析（缺依赖自动退回标准库）

                value = jsonio.loads(text)
            else:
                value = text
        except Exception as exc:  # 数据文件坏了要报清楚是哪个插件
            raise SdkError(f"插件 {self.id or '?'} 的数据文件读取失败：{path.name}（{exc}）") from exc
        self._data_cache[cache_key] = value
        return value


def collect_plugin_classes(module: ModuleType, base: type = Plugin) -> list[type]:
    """取模块里定义的插件类（模块内定义的 base 子类，按名字排序）。"""
    found: list[type] = []
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, base) and value is not base:
            if value.__module__ == module.__name__:
                found.append(value)
    return sorted(found, key=lambda item: item.__name__)


def plugin_class_names(classes: Iterable[type]) -> str:
    """插件类名的展示文本。"""
    return "、".join(item.__name__ for item in classes) or "—"
