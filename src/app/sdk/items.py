"""数据扩展接口：插件读条目、读写标签与关键词的统一门面。

程序本体自己不实现任何「批量打标签」的逻辑，只把现成的数据服务包一层交给插件：

1. 没注册实现时，读接口返回空、写接口抛 `SdkError`；
2. 程序本体通过扩展接口 `items.open` 提供实现（见 `app.services.item_api`）；
3. 插件只认 `ItemRef` 这种只读快照，不接触数据库会话，也不能改条目内容。

约定：

- 所有写操作由程序侧统一提交事务，并在真的改了东西之后广播一次条目变更，
  界面因此自动刷新；插件不需要（也不应该）自己去碰数据库；
- `ItemRef.preview` 是可选的正文预览，只在显式要求时才填充，避免批量场景白白读文件；
- 本模块不导入 Qt，插件的后台线程可以直接用。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from .errors import SdkError

__all__ = [
    "ITEMS_EXTENSION",
    "ImportContext",
    "ItemRef",
    "ItemsApi",
    "SelectionContext",
    "add_keywords",
    "available",
    "current_user_id",
    "ensure_tags",
    "get_item",
    "item_id_for_path",
    "list_items",
    "notify_changed",
    "notify_tags_changed",
    "provider",
    "read_text",
    "remove_keywords",
    "set_cover",
    "suffixes_in_use",
    "tag_items",
    "tag_names",
    "untag_items",
]

#: 数据接口：实现方需提供下面 `ItemsApi` 里的方法。
ITEMS_EXTENSION = "items.open"


@dataclass(frozen=True)
class ItemRef:
    """数据条目的只读快照（插件拿到的就是这么一份，改它不会影响数据库）。"""

    id: int
    name: str
    type: str = ""
    suffix: str = ""
    size: int = 0
    keywords: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    file_path: str = ""
    #: 盘上的绝对路径（库内条目 = 库根 + `file_path`，外部条目 = `source_path`）。
    #: 只有真的要打开文件的人用得上（例如给图片跑图像描述），界面展示仍用 `file_path`。
    abs_path: str = ""
    category_id: int | None = None
    user_id: int | None = None
    preview: str = ""


@dataclass(frozen=True)
class SelectionContext:
    """数据管理页里，插件按钮（`app.ui.manage.toolbar`）收到的上下文。

    回调写成 0 个参数时按旧行为调用（只是被点了一下）；写成 1 个参数时收到本对象，
    于是插件能拿到「用户当前选中/勾选了哪些条目」。
    """

    items: tuple[ItemRef, ...] = ()
    user_id: int | None = None
    refresh: Callable[[], None] | None = None

    @property
    def item_ids(self) -> tuple[int, ...]:
        return tuple(item.id for item in self.items)

    @property
    def count(self) -> int:
        return len(self.items)

    def do_refresh(self) -> None:
        """让管理页重新拉一次数据（插件自己改完库后调用）。"""
        if callable(self.refresh):
            try:
                self.refresh()
            except Exception:
                logger.exception("插件请求刷新数据管理页失败")


@dataclass
class ImportContext:
    """数据导入页里，插件按钮（`app.ui.import.action`）收到的上下文。

    插件只能「往导入表单里补建议」（预填标签 / 关键词），导入哪些文件由导入页决定。
    """

    paths: tuple[Path, ...] = ()
    user_id: int | None = None
    category_id: int | None = None
    add_tags: Callable[[Iterable[str]], int] | None = None
    add_keywords: Callable[[Iterable[str]], int] | None = None
    notify: Callable[[str], None] | None = None
    scan: Callable[[], tuple[Path, ...]] | None = None
    extra: dict = field(default_factory=dict)

    def apply_tags(self, names: Iterable[str]) -> int:
        """把标签填进导入页的标签框，返回实际新增的个数。"""
        return int(self._call(self.add_tags, names))

    def apply_keywords(self, words: Iterable[str]) -> int:
        """把关键词填进导入页的关键词框，返回实际新增的个数。"""
        return int(self._call(self.add_keywords, words))

    def toast(self, message: str) -> None:
        """在导入页提示一行（例如「已预填 3 个标签」）。"""
        if callable(self.notify):
            try:
                self.notify(str(message))
            except Exception:
                logger.exception("导入页提示失败")

    def recent_paths(self) -> tuple[Path, ...]:
        """当前的待导入文件清单；`scan` 可用时先让导入页刷新一次。"""
        if callable(self.scan):
            try:
                self.paths = tuple(self.scan())
            except Exception:
                logger.exception("读取待导入文件失败")
        return tuple(self.paths)

    @staticmethod
    def _call(handler: Callable[..., Any] | None, values: Iterable[str]) -> int:
        if not callable(handler):
            return 0
        items = [str(value).strip() for value in values if str(value).strip()]
        if not items:
            return 0
        try:
            return int(handler(items))
        except Exception:
            logger.exception("写入导入页失败")
            return 0


class ItemsApi(Protocol):
    """数据接口的形状（由程序本体实现）。"""

    def current_user_id(self) -> int | None: ...

    def list_items(
        self,
        *,
        ids: Iterable[int] | None = None,
        user_id: int | None = None,
        suffix: Iterable[str] = (),
        type: str = "",
        include_hidden: bool = False,
        include_deleted: bool = False,
        exclude_tags: Iterable[str] = (),
        preview_bytes: int = 0,
        limit: int = 0,
    ) -> list[ItemRef]: ...

    def get_item(self, item_id: int) -> ItemRef | None: ...

    def suffixes_in_use(self, user_id: int | None = None) -> dict[str, int]: ...

    def tag_names(self, user_id: int | None = None) -> list[str]: ...

    def ensure_tags(self, names: Iterable[str], user_id: int | None = None) -> list[str]: ...

    def tag_items(self, item_ids: Iterable[int], names: Iterable[str]) -> int: ...

    def untag_items(self, item_ids: Iterable[int], names: Iterable[str]) -> int: ...

    def add_keywords(self, item_ids: Iterable[int], words: Iterable[str]) -> int: ...

    def remove_keywords(self, item_ids: Iterable[int], words: Iterable[str]) -> int: ...

    def read_text(self, item_id: int, limit: int = 4096) -> tuple[str, str, bool]: ...

    def item_id_for_path(self, path: str) -> int | None: ...

    def set_cover(self, item_id: int, source: str = "") -> str: ...

    def notify_changed(self) -> None: ...

    def notify_tags_changed(self) -> None: ...


# --------------------------------------------------------------------- 门面
def provider() -> object | None:
    """程序提供的数据接口（`items.open`）；没有（脚本、测试）时为 None。"""
    try:
        from ..core.plugins.extensions import extension_registry
    except Exception:
        return None
    return extension_registry.provider(ITEMS_EXTENSION)


def available() -> bool:
    """有没有可用的数据接口（插件页据此提示「程序未提供数据接口」）。"""
    return provider() is not None


def _api() -> Any:
    api = provider()
    if api is None:
        raise SdkError("程序没有提供数据接口 items.open")
    return api


def current_user_id() -> int | None:
    """当前用户 id；拿不到时返回 None。"""
    api = provider()
    if api is None:
        return None
    try:
        return api.current_user_id()
    except Exception:
        logger.exception("读取当前用户失败")
        return None


def list_items(
    *,
    ids: Iterable[int] | None = None,
    user_id: int | None = None,
    suffix: Iterable[str] = (),
    type: str = "",
    include_hidden: bool = False,
    include_deleted: bool = False,
    exclude_tags: Iterable[str] = (),
    preview_bytes: int = 0,
    limit: int = 0,
) -> tuple[ItemRef, ...]:
    """列出数据条目。

    - `ids`：只要这些条目（给了就忽略其它筛选条件）；
    - `suffix` / `type`：按文件后缀、数据类型筛选；
    - `exclude_tags`：跳过已经带有其中任意一个标签的条目（批量时用来跳过做过的）；
    - `preview_bytes`：要正文预览时给个字节数（0 表示不读内容，批量场景更快）；
    - `limit`：最多返回几条（0 表示不限）。
    """
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(
            api.list_items(
                ids=ids,
                user_id=user_id,
                suffix=suffix,
                type=type,
                include_hidden=include_hidden,
                include_deleted=include_deleted,
                exclude_tags=exclude_tags,
                preview_bytes=preview_bytes,
                limit=limit,
            )
        )
    except Exception:
        logger.exception("读取数据条目失败")
        return ()


def get_item(item_id: int) -> ItemRef | None:
    """按 id 取一条数据；没有时返回 None。"""
    api = provider()
    if api is None:
        return None
    try:
        return api.get_item(int(item_id))
    except Exception:
        logger.exception("读取数据条目失败：{}", item_id)
        return None


def suffixes_in_use(user_id: int | None = None) -> dict[str, int]:
    """库里实际出现的文件后缀（小写、不带点）→ 数量。"""
    api = provider()
    if api is None:
        return {}
    try:
        return {str(key): int(value) for key, value in api.suffixes_in_use(user_id).items()}
    except Exception:
        logger.exception("读取库内文件格式失败")
        return {}


def tag_names(user_id: int | None = None) -> tuple[str, ...]:
    """当前可见的全部标签名（按名称排序）。"""
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(str(name) for name in api.tag_names(user_id))
    except Exception:
        logger.exception("读取标签列表失败")
        return ()


def ensure_tags(names: Iterable[str], user_id: int | None = None) -> tuple[str, ...]:
    """确保这些标签存在（不挂到任何条目上），返回去掉重复后的名字。"""
    api = _api()
    try:
        return tuple(str(name) for name in api.ensure_tags(names, user_id=user_id))
    except Exception as exc:
        logger.exception("创建标签失败")
        raise SdkError(f"创建标签失败：{exc}") from exc


def tag_items(item_ids: Iterable[int], names: Iterable[str]) -> int:
    """给一批条目挂标签，返回被改动的条目数（已经有的不会重复挂）。"""
    api = _api()
    try:
        return int(api.tag_items(item_ids, names))
    except Exception as exc:
        logger.exception("挂标签失败")
        raise SdkError(f"挂标签失败：{exc}") from exc


def untag_items(item_ids: Iterable[int], names: Iterable[str]) -> int:
    """从一批条目上摘掉标签，返回被改动的条目数。"""
    api = _api()
    try:
        return int(api.untag_items(item_ids, names))
    except Exception as exc:
        logger.exception("摘标签失败")
        raise SdkError(f"摘标签失败：{exc}") from exc


def add_keywords(item_ids: Iterable[int], words: Iterable[str]) -> int:
    """给一批条目追加关键词，返回被改动的条目数。"""
    api = _api()
    try:
        return int(api.add_keywords(item_ids, words))
    except Exception as exc:
        logger.exception("追加关键词失败")
        raise SdkError(f"追加关键词失败：{exc}") from exc


def remove_keywords(item_ids: Iterable[int], words: Iterable[str]) -> int:
    """从一批条目上删掉关键词，返回被改动的条目数。"""
    api = _api()
    try:
        return int(api.remove_keywords(item_ids, words))
    except Exception as exc:
        logger.exception("删除关键词失败")
        raise SdkError(f"删除关键词失败：{exc}") from exc


def read_text(item_id: int, limit: int = 4096) -> tuple[str, str, bool]:
    """读一条数据的正文头：返回 `(文本, 编码, 是否被截断)`。

    文本条目直接给数据库里存的内容；其余条目读磁盘文件的头部并按常见编码解码。
    """
    api = provider()
    if api is None:
        return "", "", False
    try:
        text, encoding, truncated = api.read_text(int(item_id), int(limit))
        return str(text), str(encoding), bool(truncated)
    except Exception:
        logger.exception("读取正文失败：{}", item_id)
        return "", "", False


def item_id_for_path(path: str) -> int | None:
    """按磁盘上的绝对路径找数据项 id；找不到（或程序没提供接口）时返回 None。

    插件手里只有「正在打开的那个文件」，这一步把它对回库里的数据项，才能改它的封面
    （用户 m02499 第 2 条）。
    """
    api = provider()
    if api is None:
        return None
    lookup = getattr(api, "item_id_for_path", None)
    if not callable(lookup):
        return None
    try:
        found = lookup(str(path))
    except Exception:
        logger.exception("按路径查数据条目失败：{}", path)
        return None
    return int(found) if found else None


def set_cover(item_id: int, source: str = "") -> str:
    """给数据项换封面：`source` 给本地图片路径就用它当封面，给空串恢复默认封面。

    返回新的封面文件路径（插件不必关心它；拿不到时返回空串）。跨插件的工作流（例如
    视频查看器「用当前帧作封面」）用它把结果写回数据项。
    """
    api = _api()
    try:
        return str(api.set_cover(int(item_id), str(source or "")))
    except Exception as exc:
        logger.exception("设置封面失败：{}", item_id)
        raise SdkError(f"设置封面失败：{exc}") from exc


def notify_changed() -> None:
    """告诉程序「库里的东西变了」，让界面刷新列表。"""
    api = provider()
    if api is None:
        return
    try:
        api.notify_changed()
    except Exception:
        logger.exception("广播条目变更失败")


def notify_tags_changed() -> None:
    """告诉程序「标签库变了」（新建了标签、标签用量变了），让标签页与标签列表刷新。

    批处理（一键补全、按规则挂标签）会连续写很多次标签，**整批写完之后调一次**就够：
    每写一条都发信号会让标签页重建很多遍（用户 m07851）。
    """
    api = provider()
    if api is None:
        return
    notify = getattr(api, "notify_tags_changed", None)
    if not callable(notify):
        return
    try:
        notify()
    except Exception:
        logger.exception("广播标签变更失败")
