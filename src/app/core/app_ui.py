"""程序本体提供给插件的界面扩展接口（扩展接口名 `app.ui`，由 `src/main.py` 通过
`plugin_service.bootstrap("app.ui", AppUiApi())` 暴露）。

大型插件往往需要自己的导航页面；插件在 `register(api)` 里 `api.require("app.ui")` 拿到
本对象后调用 `add_page()` 登记页面，程序本体负责创建控件、放进侧边导航，并随插件启停同步。
本模块不依赖 Qt：页面工厂只在窗口装配导航项时才被调用，因此插件可以在载入阶段安全登记。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from loguru import logger

#: 程序本体界面扩展接口的接口名
APP_UI_EXTENSION = "app.ui"

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.\-]{1,63}$")


@dataclass(frozen=True)
class PageSpec:
    """一个待装配的导航页面。"""

    key: str
    title: str
    factory: Callable[[], object]
    icon: str = ""
    bottom: bool = False
    plugin_id: str = ""

    @property
    def route(self) -> str:
        """导航路由名（同时用作页面控件的 objectName）。"""
        return f"plugin.{self.key}"


class AppUiApi:
    """`app.ui` 扩展接口：插件登记自己的导航页面。"""

    def __init__(self) -> None:
        self._pages: dict[str, PageSpec] = {}
        self._navigator: Callable[[str], None] | None = None

    def set_navigator(self, callback: Callable[[str], None] | None) -> None:
        """主窗口登记「切页面」回调；插件不直接用它，走 `app.sdk.ui.open_page()`。"""
        self._navigator = callback

    def routes(self) -> tuple[str, ...]:
        """当前登记的全部页面路由（`plugin.<key>`）。"""
        return tuple(spec.route for spec in self._pages.values())

    def open_page(self, route: str) -> bool:
        """切到某个已登记的页面，返回是否真的跳过去；路由可写 key 或 `plugin.<key>`。"""
        spec = self._resolve(route)
        if spec is None or self._navigator is None:
            return False
        try:
            self._navigator(spec.route)
        except Exception:
            logger.exception("跳转插件页面失败：{}", spec.route)
            return False
        return True

    def _resolve(self, route: str) -> PageSpec | None:
        name = str(route or "").strip()
        if name.startswith("plugin."):
            name = name[len("plugin.") :]
        return self._pages.get(name) if name else None

    def add_page(
        self,
        key: str,
        title: str,
        factory: Callable[[], object],
        icon: str = "",
        bottom: bool = False,
        plugin_id: str = "",
    ) -> PageSpec:
        """登记一个导航页面；同一插件重复登记同一个 key 视为更新（插件重载不会产生重复项）。"""
        name = str(key or "").strip()
        if not _KEY_PATTERN.match(name):
            raise ValueError(f"页面 key 不合法：{key!r}（小写字母开头，可含数字、下划线、点和连字符）")
        if not str(title or "").strip():
            raise ValueError(f"页面标题不能为空：{name}")
        if not callable(factory):
            raise ValueError(f"页面工厂必须可调用（无参并返回一个 QWidget）：{name}")
        current = self._pages.get(name)
        if current is not None and current.plugin_id and current.plugin_id != plugin_id:
            raise ValueError(f"页面 key 已被插件 {current.plugin_id} 使用：{name}")
        spec = PageSpec(
            key=name,
            title=str(title).strip(),
            factory=factory,
            icon=str(icon or "").strip(),
            bottom=bool(bottom),
            plugin_id=plugin_id,
        )
        self._pages[name] = spec
        return spec

    def remove_page(self, key: str) -> bool:
        """移除一个页面，返回是否确实移除了。"""
        return self._pages.pop(str(key or "").strip(), None) is not None

    def pages(self) -> tuple[PageSpec, ...]:
        """按登记顺序返回所有页面。"""
        return tuple(self._pages.values())

    def sync_plugins(self, plugin_ids: Sequence[str]) -> tuple[str, ...]:
        """插件载入完成后由插件服务调用：清掉已不在集合里的插件登记的页面。"""
        alive = {str(item) for item in plugin_ids}
        gone = tuple(
            key for key, spec in self._pages.items() if spec.plugin_id and spec.plugin_id not in alive
        )
        for key in gone:
            self._pages.pop(key, None)
        return gone

    def clear(self) -> None:
        self._pages.clear()

    def __len__(self) -> int:
        return len(self._pages)


__all__ = ["APP_UI_EXTENSION", "AppUiApi", "PageSpec"]
