"""插件服务：发现、导入、启用 / 禁用、修改、删除与加载插件。

* 内置插件来自 `app.plugins.builtin_plugins()`（随程序分发，不能删除）；
* 外部插件是运行期 `plugins/` 目录下的子目录，必须有 `plugin.json`；
* 启用状态与名称 / 说明的修改记在 `config/plugins.json`，插件自己的清单保持只读。

加载插件就是重建 `viewer_registry`：禁用的插件不再贡献查看器，已打开的文件不受影响。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

from loguru import logger

from ..core import paths
from ..core.plugins import (
    KIND_VIEWER,
    MANIFEST_NAME,
    PLUGIN_KINDS,
    PluginError,
    PluginInfo,
    load_manifest,
)
from ..core.viewers import Viewer, normalize_suffix, viewer_registry
from ..plugins import builtin_plugins

STATE_VERSION = 1

#: 界面上「筛选」用的取值
STATE_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "全部状态"),
    ("enabled", "已启用"),
    ("disabled", "已禁用"),
    ("error", "异常"),
)


class PluginApi:
    """传给插件 `register(api)` 的注册入口。"""

    def __init__(self, plugin: PluginInfo) -> None:
        self.plugin_id = plugin.id
        self.plugin_name = plugin.name
        self.default_extensions = tuple(plugin.extensions)
        self.registered: list[Viewer] = []

    def add_viewer(
        self,
        name: str,
        extensions,
        factory=None,
        kind: str = "text",
        description: str = "",
        capabilities=(),
        viewer_id: str = "",
    ) -> Viewer:
        """注册一个查看器；未写扩展名时沿用插件清单里的扩展名。"""
        suffixes = tuple(
            suffix for suffix in dict.fromkeys(normalize_suffix(item) for item in extensions) if suffix
        ) or self.default_extensions
        if not suffixes:
            raise PluginError(f"查看器「{name}」没有声明扩展名")
        if not callable(factory):
            raise PluginError(f"查看器「{name}」没有可用的控件工厂（插件需自行创建视图）")
        viewer = Viewer(
            id=viewer_id or f"{self.plugin_id}.{len(self.registered) + 1}",
            name=str(name),
            extensions=suffixes,
            kind=kind,
            plugin_id=self.plugin_id,
            factory=factory,
            description=str(description),
            capabilities=tuple(str(item) for item in capabilities),
        )
        self.registered.append(viewer)
        return viewer


class PluginService:
    """插件目录 + 状态文件；不依赖数据库，测试里可以直接指向临时目录。"""

    def __init__(self, plugin_dir: Path | None = None, state_file: Path | None = None) -> None:
        self._plugin_dir = Path(plugin_dir) if plugin_dir is not None else None
        self._state_file = Path(state_file) if state_file is not None else None

    @property
    def plugin_dir(self) -> Path:
        return self._plugin_dir or paths.PLUGIN_DIR

    @property
    def state_file(self) -> Path:
        return self._state_file or paths.PLUGIN_STATE_FILE

    # -------------------------------------------------------------- 状态文件
    def _read_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("插件状态文件损坏，已忽略：{}（{}）", self.state_file, exc)
            return {}
        plugins = data.get("plugins") if isinstance(data, dict) else None
        return plugins if isinstance(plugins, dict) else {}

    def _write_state(self, state: dict) -> None:
        payload = {"version": STATE_VERSION, "plugins": state}
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_state_for(self, plugin_id: str, **fields) -> None:
        state = self._read_state()
        entry = state.get(plugin_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry.update(fields)
        state[plugin_id] = entry
        self._write_state(state)

    # ---------------------------------------------------------------- 发现
    def builtin(self) -> list[PluginInfo]:
        return [info for info, _register in builtin_plugins()]

    def _external(self) -> list[PluginInfo]:
        root = self.plugin_dir
        if not root.is_dir():
            return []
        found: list[PluginInfo] = []
        for folder in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            try:
                found.append(load_manifest(folder))
            except PluginError as exc:
                logger.warning("插件清单有问题：{}（{}）", folder.name, exc)
                found.append(
                    PluginInfo(
                        id=folder.name,
                        name=folder.name,
                        path=folder,
                        enabled=False,
                        error=str(exc),
                    )
                )
        return found

    def discover(self) -> list[PluginInfo]:
        """内置 + 外部插件，并套用状态文件里的启用状态与名称 / 说明覆盖。"""
        state = self._read_state()
        resolved: list[PluginInfo] = []
        for info in [*self.builtin(), *self._external()]:
            entry = state.get(info.id)
            entry = entry if isinstance(entry, dict) else {}
            enabled = bool(entry.get("enabled", info.enabled)) and not info.error
            resolved.append(
                info.clone(
                    name=str(entry.get("name") or info.name),
                    description=str(entry.get("description") or info.description),
                    note=str(entry.get("note") or info.note),
                    enabled=enabled,
                )
            )
        return resolved

    def all(
        self, kind: str = "", query: str = "", state: str = "", source: str = ""
    ) -> list[PluginInfo]:
        """按类型 / 关键字 / 状态 / 来源筛选（都为空时返回全部）。"""
        plugins = self.discover()
        text = (query or "").strip().lower()
        result: list[PluginInfo] = []
        for info in plugins:
            if kind and info.kind != kind:
                continue
            if source and info.source != source:
                continue
            if state == "enabled" and not info.enabled:
                continue
            if state == "disabled" and (info.enabled or info.error):
                continue
            if state == "error" and not info.error:
                continue
            if text and text not in self._search_text(info):
                continue
            result.append(info)
        return result

    @staticmethod
    def _search_text(info: PluginInfo) -> str:
        """搜索用的拼接文本：名称、id、说明、作者、扩展名与备注。"""
        parts = [
            info.name,
            info.id,
            info.description,
            info.author,
            info.note,
            info.kind_label,
            " ".join(info.extensions),
        ]
        return " ".join(parts).lower()

    def get(self, plugin_id: str) -> PluginInfo | None:
        for info in self.discover():
            if info.id == plugin_id:
                return info
        return None

    def viewers_of(self, plugin_id: str) -> list[Viewer]:
        """某个插件当前注册到注册表里的查看器（禁用后为空）。"""
        return [viewer for viewer in viewer_registry.all() if viewer.plugin_id == plugin_id]

    # ---------------------------------------------------------------- 操作
    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginInfo | None:
        info = self.get(plugin_id)
        if info is None:
            return None
        if info.error and enabled:
            logger.warning("插件异常，无法启用：{}（{}）", info.name, info.error)
            return info
        self._save_state_for(plugin_id, enabled=bool(enabled))
        logger.info("插件「{}」{}", info.name, "已启用" if enabled else "已禁用")
        self.load_viewers()
        return self.get(plugin_id)

    def update(
        self,
        plugin_id: str,
        name: str | None = None,
        description: str | None = None,
        note: str | None = None,
    ) -> PluginInfo | None:
        """修改显示名称 / 说明 / 备注（内置插件也允许，只影响本机显示）。"""
        info = self.get(plugin_id)
        if info is None:
            return None
        fields = {
            key: str(value).strip()
            for key, value in (("name", name), ("description", description), ("note", note))
            if value is not None
        }
        if not fields:
            return info
        self._save_state_for(plugin_id, **fields)
        logger.info("插件「{}」的信息已更新：{}", info.name, "、".join(fields))
        return self.get(plugin_id)

    def remove(self, plugin_id: str) -> bool:
        """删除外部插件目录；内置插件不允许删除。"""
        info = self.get(plugin_id)
        if info is None or info.builtin or info.path is None:
            logger.warning("不允许删除：{}", plugin_id)
            return False
        shutil.rmtree(info.path, ignore_errors=True)
        state = self._read_state()
        state.pop(plugin_id, None)
        self._write_state(state)
        logger.info("已删除插件「{}」", info.name)
        self.load_viewers()
        return True

    def import_plugin(self, source: Path, overwrite: bool = False) -> PluginInfo:
        """从插件目录或 `.zip` 导入；`overwrite` 为真时覆盖同名插件。"""
        source = Path(source)
        if not source.exists():
            raise PluginError(f"找不到要导入的内容：{source}")
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            folder = source
            if source.is_file():
                if source.suffix.lower() != ".zip":
                    raise PluginError("只支持导入插件目录或 .zip 压缩包")
                temporary = self.plugin_dir / f".import-{uuid.uuid4().hex[:8]}"
                temporary.mkdir(parents=True, exist_ok=True)
                self._extract_zip(source, temporary)
                folder = self._find_plugin_root(temporary)
            info = load_manifest(folder)
            if self.get(info.id) is not None:
                if not overwrite:
                    raise PluginError(f"插件已存在：{info.id}（可勾选覆盖安装）")
                existing = self.get(info.id)
                if existing is not None and existing.path is not None:
                    shutil.rmtree(existing.path, ignore_errors=True)
            target = self.plugin_dir / info.id
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(folder, target)
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
        installed = load_manifest(target)
        self._save_state_for(installed.id, enabled=True)
        self.load_viewers()
        logger.info("已导入插件「{}」（{}）", installed.name, installed.id)
        return self.get(installed.id) or installed

    @staticmethod
    def _extract_zip(source: Path, target: Path) -> None:
        try:
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    name = member.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise PluginError(f"压缩包里有不安全的路径：{member.filename}")
                archive.extractall(target)
        except zipfile.BadZipFile as exc:
            raise PluginError(f"无法读取压缩包：{exc}") from exc

    @staticmethod
    def _find_plugin_root(folder: Path) -> Path:
        if (folder / MANIFEST_NAME).is_file():
            return folder
        candidates = [
            child
            for child in sorted(folder.iterdir())
            if child.is_dir() and (child / MANIFEST_NAME).is_file()
        ]
        if len(candidates) == 1:
            return candidates[0]
        raise PluginError(f"压缩包里没有找到 {MANIFEST_NAME}")

    # ---------------------------------------------------------------- 加载
    def load_viewers(self) -> int:
        """按启用状态重建查看器注册表，返回注册成功的查看器数量。"""
        viewer_registry.clear()
        count = 0
        for info in self.discover():
            if not info.enabled or info.kind != KIND_VIEWER:
                continue
            try:
                for viewer in self._load_plugin(info):
                    viewer_registry.register(viewer)
                    count += 1
            except PluginError as exc:
                logger.error("插件加载失败：{}（{}）", info.name, exc)
            except Exception as exc:  # 第三方插件代码，出错只记录
                logger.exception("插件加载异常：{}（{}）", info.name, exc)
        logger.debug("已加载 {} 个查看器，来自 {} 个插件", count, len(viewer_registry.plugin_ids()))
        return count

    def _load_plugin(self, info: PluginInfo) -> list[Viewer]:
        api = PluginApi(info)
        register = self._resolve_register(info)
        register(api)
        return api.registered

    def _resolve_register(self, info: PluginInfo):
        for candidate, register in builtin_plugins():
            if candidate.id == info.id:
                return register
        if info.path is None or not info.entry:
            raise PluginError(f"插件 {info.id} 缺少入口文件")
        module_path = Path(info.path) / info.entry
        module_name = "dm_plugin_" + info.id.replace(".", "_").replace("-", "_")
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise PluginError(f"无法加载插件入口：{module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            raise PluginError(f"入口模块必须定义 register(api)：{info.entry}")
        return register


#: 全局插件服务：界面与服务共用一份
plugin_service = PluginService()

__all__ = ["KIND_VIEWER", "PLUGIN_KINDS", "PluginApi", "PluginError", "PluginInfo", "PluginService", "plugin_service"]
