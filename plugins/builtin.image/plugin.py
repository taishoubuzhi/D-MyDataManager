"""内置图片查看器：继承查看器基类，窗口由「内置弹窗工具库」承载。

插件选项（清单里的 options）在载入时读回，create_view() 每次都用当前取值构造控件，
所以改完选项重新载入插件即生效。
"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class ImageViewerPlugin(ViewerPlugin):
    """按扩展名打开图片；缩放 / 旋转 / 适应窗口的默认值由插件选项覆盖。"""

    DEFAULT_FIT_ON_OPEN = True
    DEFAULT_ZOOM_STEP = 1.25
    DEFAULT_SMOOTH = True

    def create_view(self, path, parent=None):
        from .image_view import ImageViewer

        try:
            zoom_step = float(self.option("zoom_step", self.DEFAULT_ZOOM_STEP))
        except (TypeError, ValueError):
            zoom_step = self.DEFAULT_ZOOM_STEP
        return ImageViewer(
            path,
            parent,
            extensions=self.extensions,
            fit_on_open=bool(self.option("fit_on_open", self.DEFAULT_FIT_ON_OPEN)),
            zoom_step=zoom_step,
            smooth=bool(self.option("smooth_scaling", self.DEFAULT_SMOOTH)),
        )
