"""编辑器工具库（也是入口文件）：EditorPlugin 基类、对外导出、入口类 EditorLibraryPlugin。

具体编辑器插件继承 `EditorPlugin`，只实现 `create_editor()`；注册、规则、窗口都由本库负责。
本库在 setup() 里把调度接口 `editor.open` 暴露成 `EditorOpenApi`，程序侧 `app.services.editor_service` 只做转调。
"""

from __future__ import annotations

from pathlib import Path

from app.sdk import Plugin, PluginError, SdkError
from app.sdk.manifest import record_of
#: 本库提供的扩展点：程序侧（`app.services.editor_service`）与别的插件按它取调度接口
EDITOR_EXTENSION = "editor.open"

from .config_page import EditorConfigPage
from .editor_window import EditorWindow
from .provider import EditorOpenApi
from .registry import (
    KIND_EXTERNAL,
    KIND_INTERNAL,
    KINDS,
    Editor,
    EditorRegistry,
    editor_registry,
    normalize_suffix,
    reset as reset_registry,
)
from .rules import (
    MODE_ASK,
    MODE_BUILTIN,
    MODE_CUSTOM,
    MODE_INHERIT,
    MODE_LABELS,
    MODES,
    EditorDecision,
    EditorRule,
    EditorRules,
)
from .window import DEFAULT_HOST, build_window, edit_editor, host_name, open_page_via_host

CONFIG_PAGE_KEY = "editor_config"
CONFIG_PAGE_TITLE = "编辑器"


class EditorPlugin(Plugin):
    """编辑器插件基类：`.data/editor.json`（统一清单格式，`key` = 插件 id）声明 kind /
    extensions / host，子类实现 create_editor()。"""

    #: 子类可覆盖：编辑器类型（内置控件 / 外部程序）与默认窗口宿主、排序
    default_kind = KIND_INTERNAL
    default_host = DEFAULT_HOST
    default_order = 100

    def setup(self, ctx) -> None:
        self._ctx = ctx
        data = record_of(ctx.data("editor"), self.id)
        if not data:
            raise SdkError(f"插件 {self.id} 的编辑器数据缺少记录：.data/editor.json 的 items 里要有 key = {self.id}")
        host = str(data.get("host") or self.default_host)
        if host:
            ctx.require(host)  # 内置编辑器要弹窗，缺界面工具库就注册失败
        self._host = host
        self.editor_kind = str(data.get("kind") or self.default_kind)
        self.extensions = self._extensions(data.get("extensions"))
        self.editor_name = str(data.get("name") or self.name)
        ctx.add_editor(
            self.editor_name,
            extensions=self.extensions,
            factory=self.create_editor,
            opener=self.open_editor,
            kind=self.editor_kind,
            host=host,
            description=str(data.get("description") or ""),
            capabilities=data.get("capabilities") or (),
            editor_id=self.id,
            order=int(data.get("order") or self.default_order),
        )

    def option(self, key: str, default=None):
        """读清单 options 声明的插件选项。"""
        return self._ctx.option(key, default) if getattr(self, "_ctx", None) is not None else default

    def create_editor(self, path, parent=None):
        """子类必须实现：返回自己的编辑器控件（内部编辑器）。"""
        raise NotImplementedError(f"插件 {self.id} 没有实现 create_editor()")

    def open_editor(self, path, parent=None) -> tuple[bool, str]:
        """打开编辑器：外部编辑器交给系统程序，内部编辑器弹自定义窗口。"""
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        if self.editor_kind == KIND_EXTERNAL:
            from app.sdk import ui

            if ui.open_default(target):
                return True, "已交给系统默认编辑器"
            return False, "系统无法打开该文件"
        return open_page_via_host(
            self._ctx,
            target,
            self.create_editor,
            self.editor_name,
            self._host,
            parent,
            on_saved=self._on_saved,
        )

    def _on_saved(self, path) -> None:
        """保存后同步：让程序按磁盘文件重算条目指纹并广播刷新。"""
        host = getattr(self._ctx, "host", None)
        refresh = getattr(host, "refresh_path", None)
        if callable(refresh):
            refresh(str(path))
            return
        from app.sdk import ui

        ui.notify_items_changed()

    @staticmethod
    def _extensions(value) -> tuple[str, ...]:
        clean: list[str] = []
        for item in value or ():
            suffix = normalize_suffix(item)
            if suffix and suffix not in clean:
                clean.append(suffix)
        return tuple(clean)


class EditorLibraryPlugin(EditorPlugin):
    """编辑器工具库入口：注册 `editor.open` 接口与「编辑器」配置页。

    数据管理页右键的「编辑器 ▸」子菜单由程序本体（`ManagePage`）按 `app.services.editor_service`
    搭出来，所以本插件不贡献菜单项——编辑器库被禁用时那个子菜单照样在，只是只剩
    「系统默认程序 / 交给系统选择…」。
    """

    def setup(self, ctx) -> None:
        self._ctx = ctx
        ctx.provide(EDITOR_EXTENSION, EditorOpenApi(ctx))
        try:
            ctx.add_page(
                CONFIG_PAGE_KEY,
                CONFIG_PAGE_TITLE,
                lambda: EditorConfigPage(ctx, ctx.require(EDITOR_EXTENSION)),
                icon="EDIT",
                order=200,
            )
        except PluginError as exc:
            ctx.log.warning("宿主没有提供界面接口，编辑器配置页未注册：{}", exc)

    def teardown(self) -> None:
        reset_registry()


__all__ = [
    "CONFIG_PAGE_KEY",
    "CONFIG_PAGE_TITLE",
    "Editor",
    "EditorConfigPage",
    "EditorDecision",
    "EditorLibraryPlugin",
    "EditorOpenApi",
    "EditorPlugin",
    "EditorRegistry",
    "EditorRule",
    "EditorRules",
    "EditorWindow",
    "KINDS",
    "KIND_EXTERNAL",
    "KIND_INTERNAL",
    "MODE_ASK",
    "MODE_BUILTIN",
    "MODE_CUSTOM",
    "MODE_INHERIT",
    "MODE_LABELS",
    "MODES",
    "build_window",
    "edit_editor",
    "editor_registry",
    "host_name",
    "normalize_suffix",
    "open_page_via_host",
    "reset_registry",
]
