"""查看器工具库（builtin.lib.viewer）：查看器的公共工具箱与调度接口。

别的插件只允许从这里导入：

    from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin, ViewerWindow

内容分三块：
- `ViewerPlugin`：查看器插件基类（读 `.data/viewer.json`、登记查看器、提供选项读取、负责弹窗）；
- `ViewerWindow`：查看器内容页外壳（文件名 + 查看器名 + 「用系统程序打开」「定位文件」+ 内容区）；
- `ViewerOpenApi` + `ViewerRules` + `viewer_registry`：`viewer.open` 扩展接口、规则读写与注册表，
  程序本体与别的插件都靠它；具体的播放页 / 文本页等「某个查看器怎么画」的实现属于各自的
  查看器插件，本库不提供。

本插件同时注册两样东西：扩展接口 `viewer.open`（谁打开文件都走它）与配置页面
「查看器」（原程序内置的查看器页搬到这里）。界面由 UI 工具库（builtin.lib.ui）
的页面模板搭出来。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.sdk import Plugin, PluginError, SdkError
from app.sdk.manifest import record_of

#: 本库提供的扩展点：程序侧（`app.services.viewer_service`）与别的插件按它取调度接口
VIEWER_EXTENSION = "viewer.open"

from .config_page import ViewerConfigPage
from .provider import ViewerOpenApi
from .registry import Viewer, ViewerRegistry, reset as reset_registry, viewer_registry
from .rules import (
    LEGACY_RULES_FILE_NAME,
    MODE_ASK,
    MODE_BUILTIN,
    MODE_CUSTOM,
    MODE_INHERIT,
    MODE_LABELS,
    MODES,
    RULES_FILE_NAME,
    STATE_VERSION,
    ViewerDecision,
    ViewerRule,
    ViewerRules,
)
from .viewer_window import DEFAULT_HOST, ViewerWindow
from .window import build_window, host_name, open_page_via_host, open_viewer

__all__ = [
    "DEFAULT_HOST",
    "LEGACY_RULES_FILE_NAME",
    "MODE_ASK",
    "MODE_BUILTIN",
    "MODE_CUSTOM",
    "MODE_INHERIT",
    "MODE_LABELS",
    "MODES",
    "RULES_FILE_NAME",
    "STATE_VERSION",
    "Viewer",
    "ViewerConfigPage",
    "ViewerDecision",
    "ViewerLibraryPlugin",
    "ViewerOpenApi",
    "ViewerPlugin",
    "ViewerRegistry",
    "ViewerRule",
    "ViewerRules",
    "ViewerWindow",
    "build_window",
    "host_name",
    "open_page_via_host",
    "open_viewer",
    "reset_registry",
    "viewer_registry",
]

#: 查看器配置页的路由 key（最终路由是 `plugin.viewer_config`）
CONFIG_PAGE_KEY = "viewer_config"
CONFIG_PAGE_TITLE = "查看器"


class ViewerPlugin(Plugin):
    """查看器插件基类：元数据、查看器登记与弹窗都由它包办。

    子类只写「怎么把文件画出来」：

        class TextPlugin(ViewerPlugin):
            def create_view(self, path, parent=None):
                return TextViewer(path, parent)

    扩展名、类型、显示名等来自插件的 `.data/viewer.json`（统一清单格式，只有一条记录，
    `key` 就是插件 id），所以程序侧不需要认识任何具体格式。
    """

    #: `.data/viewer.json` 缺省值，可被子类覆盖
    default_kind = "text"
    default_host = DEFAULT_HOST
    default_order = 100

    def setup(self, ctx) -> None:
        self._ctx = ctx
        data = record_of(ctx.data("viewer"), self.id)
        if not data:
            raise SdkError(f"插件 {self.id} 的查看器数据缺少记录：.data/viewer.json 的 items 里要有 key = {self.id}")
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
        """在自己的文件夹里造好视图，再让宿主扩展接口把它弹出来。"""
        ctx = getattr(self, "_ctx", None)
        if ctx is None:
            return False, "插件还没有载入完成"
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        name = self.view_name or self.name
        host = getattr(self, "_host", self.default_host)
        return open_page_via_host(ctx, target, self.create_view, name, host, parent)

    @staticmethod
    def _extensions(value: Iterable) -> tuple[str, ...]:
        clean: list[str] = []
        for item in value:
            suffix = str(item).strip().lower().lstrip(".")
            if suffix and suffix not in clean:
                clean.append(suffix)
        return tuple(clean)


class ViewerLibraryPlugin(ViewerPlugin):
    """入口类：登记 `viewer.open` 接口，并把「查看器」配置页挂进导航。

    清单里用 `"class": "ViewerLibraryPlugin"` 指定本类；真正的查看器
    （图片 / 视频 / 文本……）由其它插件各自注册，这里只提供共用的调度与配置界面。
    """

    def setup(self, ctx) -> None:
        self._ctx = ctx
        ctx.provide(VIEWER_EXTENSION, ViewerOpenApi(ctx))
        try:
            ctx.add_page(
                CONFIG_PAGE_KEY,
                CONFIG_PAGE_TITLE,
                lambda: ViewerConfigPage(ctx, ctx.require(VIEWER_EXTENSION)),
                icon="VIEW",
                order=200,
            )
        except PluginError as exc:
            # 没有界面接口的宿主（命令行、服务层自检）里查看器照样可用，只是没有配置页
            ctx.log.warning("宿主没有提供界面接口，查看器配置页未注册：{}", exc)

    def teardown(self) -> None:
        """卸载时清空注册表：查看器随本插件一起消失，重新载入时再登记。"""
        reset_registry()
