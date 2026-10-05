"""模型工具库（也是入口文件）：登记表、按需加载、下载与运行环境都在这个插件里。

对外只暴露一个扩展接口 `model.open`（`ModelOpenApi`）：其它插件通过 `app.sdk.models`
按模型 id 或能力取租约，模型由本插件按需加载、按引用计数与空闲策略卸载。
页面侧提供「模型」管理页；模板数据放在 `data/`，只读，用户配置落 `.configs/models.json`。
"""

from __future__ import annotations

from app.sdk import Plugin, PluginError
from app.sdk.models import MODEL_EXTENSION

from .constants import PAGE_ICON, PAGE_KEY, PAGE_ORDER, PAGE_TITLE, PLUGIN_ID
from .manager import model_manager
from .provider import ModelOpenApi
from .registry import reset as reset_registry
from .settings import load_settings

__all__ = ["CONFIG_PAGE_TITLE", "ModelLibraryPlugin", "ModelOpenApi", "PAGE_KEY", "data_templates"]


#: 页面上允许编辑、但真正落盘在 `.configs/models.json` 的项
CONFIG_PAGE_TITLE = PAGE_TITLE


class ModelLibraryPlugin(Plugin):
    """模型工具库入口：注册 `model.open`、管理页与模板数据。"""

    def setup(self, ctx) -> None:
        self._ctx = ctx
        model_manager.registry.load()
        model_manager.settings = load_settings()
        ctx.provide(MODEL_EXTENSION, ModelOpenApi(ctx, model_manager))
        try:
            from .ui.page import ModelPage

            ctx.add_page(
                PAGE_KEY,
                PAGE_TITLE,
                lambda: ModelPage(ctx, ctx.require(MODEL_EXTENSION)),
                icon=PAGE_ICON,
                order=PAGE_ORDER,
            )
        except PluginError as exc:
            ctx.log.warning("宿主没有提供界面接口，模型管理页未注册：{}", exc)

    def teardown(self) -> None:
        try:
            model_manager.shutdown()
        finally:
            reset_registry()

    # ------------------------------------------------------------ 模板数据
    def templates(self) -> dict:
        """`data/model_list.json` 里的本地模型模板。"""
        return _payload(self._ctx, "model_list")

    def api_templates(self) -> dict:
        """`data/api_templates.json` 里的外部模型模板。"""
        return _payload(self._ctx, "api_templates")

    def runtime_profiles(self) -> dict:
        """`data/runtime_profiles.json` 里的运行环境 profile。"""
        return _payload(self._ctx, "runtime_profiles")


def data_templates(ctx, key: str) -> dict:
    """读插件 `data/<key>.json` 模板（页面在没有 plugin 实例时也能读）。"""
    return _payload(ctx, key)


def _payload(ctx, key: str) -> dict:
    try:
        value = ctx.data(key, {})
    except Exception:  # 模板缺失不该让整个插件挂掉
        return {}
    return value if isinstance(value, dict) else {}
