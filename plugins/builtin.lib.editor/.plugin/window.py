"""编辑器的打开调度：内置编辑器经界面工具库弹出窗口，外部编辑器交给系统程序。"""

from __future__ import annotations

from pathlib import Path

from app.sdk.console import console_for

_console = console_for("builtin.lib.editor")

DEFAULT_HOST = "dialog"


def host_name(editor) -> str:
    """编辑器声明的显示宿主（默认 builtin.lib.ui 的弹窗）。"""
    return str(getattr(editor, "host", "") or DEFAULT_HOST)


def build_window(target, factory, name, container, on_saved=None):
    """在给定容器里搭好编辑器窗口（延迟 import Qt）。"""
    from .editor_window import EditorWindow

    return EditorWindow(target, lambda holder: factory(target, holder), name, on_saved=on_saved, parent=container)


def open_page_via_host(ctx, target, factory, name, host, parent=None, on_saved=None) -> tuple[bool, str]:
    """经界面工具库（`builtin.lib.ui` 的 `open_page`）弹出编辑器窗口。"""
    try:
        window_host = ctx.require(host)
    except Exception:
        return False, f"缺少界面工具库（{host}），请到「插件」页启用后重试"
    try:
        window_host.open_page(
            title=target.name,
            content_factory=lambda container: build_window(target, factory, name, container, on_saved),
            meta=name,
        )
    except Exception as exc:
        _console.exception(f"打开编辑器失败：{target}")
        return False, f"打开编辑器失败：{exc}"
    return True, name


def edit_editor(ctx, editor, path, parent=None) -> tuple[bool, str]:
    """打开一个具体编辑器：内部编辑器建窗口，外部编辑器交给系统默认程序。"""
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    opener = getattr(editor, "opener", None)
    if opener is not None:
        try:
            return opener(target, parent)
        except Exception as exc:
            _console.exception(f"编辑器打开文件失败：{target}")
            return False, f"打开编辑器失败：{exc}"
    factory = getattr(editor, "factory", None)
    name = str(getattr(editor, "name", "") or "")
    if factory is None:
        return False, f"编辑器 {name} 没有实现编辑逻辑"
    return open_page_via_host(ctx, target, factory, name, host_name(editor), parent)


__all__ = ["DEFAULT_HOST", "build_window", "edit_editor", "host_name", "open_page_via_host"]
