"""查看器工具库（builtin.lib.viewer）：查看器插件的公共工具箱。

对外只暴露本模块——别的插件只允许：

    from dm_plugin.builtin.lib.viewer.plugin import MediaViewer, ViewerPlugin, ViewerWindow

内容分三块：
- `ViewerPlugin`：查看器插件基类（读 data/viewer.json、登记查看器、提供选项读取、负责弹窗）；
- `ViewerWindow`：查看器内容页外壳（文件名 + 查看器名 + 「用系统程序打开」「定位文件」+ 内容区）；
- `MediaViewer`：媒体播放页面，音频 / 视频插件继承它即可。

本插件不注册任何贡献，纯粹当库用；需要弹窗时通过 `ctx.require("dialog")` 向弹窗工具库
（builtin.lib.dialog）要，自己不认识主程序。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from app.sdk import Plugin, SdkError

from .media_panel import MediaViewer, format_time
from .viewer_window import DEFAULT_HOST, ViewerWindow

__all__ = [
    "DEFAULT_HOST",
    "MediaViewer",
    "ViewerLibraryPlugin",
    "ViewerPlugin",
    "ViewerWindow",
    "format_time",
]


class ViewerPlugin(Plugin):
    """查看器插件基类：元数据、查看器登记与弹窗都由它包办。

    子类只写「怎么把文件画出来」：

        class TextPlugin(ViewerPlugin):
            def create_view(self, path, parent=None):
                return TextViewer(path, parent)

    扩展名、类型、显示名等来自插件的 `data/viewer.json`（清单 data 段声明），
    所以程序侧不需要认识任何具体格式。
    """

    #: data/viewer.json 缺省值，可被子类覆盖
    default_kind = "text"
    default_host = DEFAULT_HOST
    default_order = 100

    def setup(self, ctx) -> None:
        self._ctx = ctx
        data = ctx.data("viewer")
        if not isinstance(data, Mapping):
            raise SdkError(f"插件 {self.id} 的查看器数据必须是对象：data/viewer.json")
        host = str(data.get("host") or self.default_host)
        ctx.require(host)
        self._host = host
        self.extensions = self._extensions(data.get("extensions") or ())
        self.view_name = str(data.get("name") or self.name)
        ctx.add_viewer(
            self.view_name,
            extensions=self.extensions,
            factory=self.create_view,
            opener=self.open_view,
            kind=str(data.get("kind") or self.default_kind),
            host=host,
            description=str(data.get("description") or self.description),
            capabilities=tuple(str(item) for item in (data.get("capabilities") or ())),
            viewer_id=self.id,
            order=int(data.get("order", self.default_order)),
        )

    def option(self, key: str, default=None):
        """读插件选项（清单 options 段声明的可配置项）。"""
        ctx = getattr(self, "_ctx", None)
        return ctx.option(key, default) if ctx is not None else default

    def create_view(self, path, parent=None):
        """造出自己的视图控件：子类必须实现。"""
        raise NotImplementedError(f"插件 {self.id} 没有实现 create_view()")

    def open_view(self, path, parent=None) -> tuple[bool, str]:
        """在自己的文件夹里造好视图，再让弹窗工具库把它弹出来。"""
        ctx = getattr(self, "_ctx", None)
        if ctx is None:
            return False, "插件还没有载入完成"
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        name = self.view_name or self.name
        host_name = getattr(self, "_host", self.default_host)
        try:
            host = ctx.require(host_name)
        except Exception:
            return False, f"缺少弹窗工具库（{host_name}），请到「插件」页启用后重试"
        try:
            host.open_page(
                title=target.name,
                content_factory=lambda container: ViewerWindow(
                    target, lambda holder: self.create_view(target, holder), name, container
                ),
                meta=name,
            )
        except Exception as exc:  # 查看器异常不应影响主界面
            self.log.exception("打开查看器失败：{}", target)
            return False, f"打开查看器失败：{exc}"
        return True, name

    @staticmethod
    def _extensions(value: Iterable) -> tuple[str, ...]:
        clean: list[str] = []
        for item in value:
            suffix = str(item).strip().lower().lstrip(".")
            if suffix and suffix not in clean:
                clean.append(suffix)
        return tuple(clean)


#: 基类本身也是可实例化的：本插件用这个「只当库用」的实例充当入口类
Library = ViewerPlugin()


class ViewerLibraryPlugin(ViewerPlugin):
    """入口类：查看器工具库自身不做任何贡献，只是把工具交给其他插件。

    清单里用 `"class": "ViewerLibraryPlugin"` 指定本类；`setup()` 留空 ——
    数据文件（data/viewer.json）与贡献都属于真正的查看器插件。
    """

    def setup(self, ctx) -> None:
        self._ctx = ctx
