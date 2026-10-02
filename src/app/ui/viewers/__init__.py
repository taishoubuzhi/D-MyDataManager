"""查看器调度层：查看器控件与窗口外壳都在插件里，这里只保留「谁来打开」。"""

from .open_flow import open_path, open_system, open_viewer_with
from .window import DEFAULT_HOST, host_api, open_viewer

__all__ = ["DEFAULT_HOST", "host_api", "open_path", "open_system", "open_viewer", "open_viewer_with"]
