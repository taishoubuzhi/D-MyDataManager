"""查看器窗口调度：把查看器界面放进宿主扩展接口的弹窗里。

宿主默认是 UI 工具库提供的 `dialog`（独立的弹出窗口）；查看器插件在
`.data/viewer.json` 里写别的 `host` 就能换宿主，程序本体不需要认识具体窗口。
"""

from __future__ import annotations

from pathlib import Path

from app.sdk.console import console_for

_console = console_for("builtin.lib.viewer")

#: 没有特别指定时的宿主扩展接口名
DEFAULT_HOST = "dialog"


def host_name(viewer) -> str:
    return str(getattr(viewer, "host", "") or DEFAULT_HOST)


def build_window(
    target: Path, factory, name: str, container, sources: tuple[Path, ...] = ()
):
    """在容器里造出查看器内容（延迟导入，避免插件载入阶段就拉起 Qt）。"""
    from .viewer_window import ViewerWindow

    return ViewerWindow(
        target, lambda holder: factory(target, holder), name, container, sources=sources
    )


def _call_with_sources(call, args: tuple, sources) -> tuple[bool, str]:
    """带「当前列表顺序」调用插件自带的打开函数；不认这个关键字就退回老签名。"""
    items = tuple(sources)
    if not items:
        return call(*args)
    try:
        return call(*args, sources=items)
    except TypeError:
        _console.debug("查看器打开函数不认 sources 参数，按老签名重试")
        return call(*args)


def open_page_via_host(
    ctx, target: Path, factory, name: str, host: str, parent=None, sources=()
) -> tuple[bool, str]:
    """把查看器内容交给宿主扩展接口弹出；返回 (是否成功, 说明)。"""
    try:
        window_host = ctx.require(host)
    except Exception:
        return False, f"缺少界面工具库（{host}），请到「插件」页启用后重试"
    try:
        window_host.open_page(
            title=target.name,
            content_factory=lambda container: build_window(
                target, factory, name, container, sources=tuple(sources)
            ),
            meta=name,
        )
    except Exception as exc:  # 查看器异常不应影响主界面
        _console.exception(f"打开查看器失败：{target}")
        return False, f"打开查看器失败：{exc}"
    return True, name


def open_viewer(ctx, viewer, path: str | Path, parent=None, sources=()) -> tuple[bool, str]:
    """按查看器登记的信息打开文件：插件自带打开函数时用它，否则自己套宿主窗口。

    `sources` 是宿主「当前列表里的文件顺序」，透传给内容页做上一张 / 下一张。
    """
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    opener = getattr(viewer, "opener", None)
    if opener is not None:
        try:
            return _call_with_sources(opener, (target, parent), sources)
        except Exception as exc:
            _console.exception(f"查看器打开失败：{target}")
            return False, f"打开查看器失败：{exc}"
    factory = getattr(viewer, "factory", None)
    if factory is None:
        return False, f"查看器 {getattr(viewer, 'name', viewer)} 没有实现显示逻辑"
    return open_page_via_host(
        ctx,
        target,
        factory,
        str(getattr(viewer, "name", "") or ""),
        host_name(viewer),
        parent,
        sources=sources,
    )


__all__ = ["DEFAULT_HOST", "build_window", "host_name", "open_page_via_host", "open_viewer"]
