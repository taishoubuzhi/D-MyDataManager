"""内置图片查看器：页面由「内置弹窗页面」插件（builtin.dialog）承载。

协议要点：
- 清单里 depends 声明依赖 builtin.dialog，register 时用 api.require("dialog") 确认该弹窗插件已注册；
- viewer 的 host="dialog" 让界面把内容放进该插件提供的弹窗外壳里；
- 清单里的 options 会在插件页生成「插件选项」配置，register 时用 api.option("键") 读回，
  工厂闭包捕获这些取值；插件选项改变后 load_viewers() 会重新注册插件，新参数随即生效。
"""

from __future__ import annotations

from pathlib import Path

from app.core.viewer_data import IMAGE_EXTENSIONS

PLUGIN_NAME = "图片查看器"
VIEWER_KIND = "image"
CAPABILITIES = ("缩放", "旋转", "适应窗口", "图片信息")
DESCRIPTION = "查看 png / jpg / gif 等图片，支持缩放、旋转与适应窗口。"

DEFAULT_FIT_ON_OPEN = True
DEFAULT_ZOOM_STEP = 1.25
DEFAULT_SMOOTH = True


def _factory(path: Path, parent=None, *, fit_on_open: bool, zoom_step: float, smooth: bool):
    from app.ui.viewers.image_view import ImageViewer

    return ImageViewer(path, parent, fit_on_open=fit_on_open, zoom_step=zoom_step, smooth=smooth)


def register(api) -> None:
    api.require("dialog")
    fit_on_open = bool(api.option("fit_on_open", DEFAULT_FIT_ON_OPEN))
    try:
        zoom_step = float(api.option("zoom_step", DEFAULT_ZOOM_STEP))
    except (TypeError, ValueError):
        zoom_step = DEFAULT_ZOOM_STEP
    smooth = bool(api.option("smooth_scaling", DEFAULT_SMOOTH))

    def factory(path: Path, parent=None):
        return _factory(path, parent, fit_on_open=fit_on_open, zoom_step=zoom_step, smooth=smooth)

    api.add_viewer(
        PLUGIN_NAME,
        extensions=IMAGE_EXTENSIONS,
        factory=factory,
        kind=VIEWER_KIND,
        host="dialog",
        description=DESCRIPTION,
        capabilities=CAPABILITIES,
    )
