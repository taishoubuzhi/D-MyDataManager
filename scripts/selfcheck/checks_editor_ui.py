"""编辑器扩展机制自检：注册表与查询、app.editor 贡献可见、无插件时的系统默认退路、内置文本编辑器保存落盘与刷新链路。"""

from __future__ import annotations

from pathlib import Path

from .harness import Case, check, install_builtin_plugins


def _text_file(case: Case, name: str, text: str) -> Path:
    path = case.root / name
    path.write_text(text, encoding="utf-8")
    return path


@check("editor_registry_query", "services")
def editor_registry_query(case: Case) -> None:
    """编辑器工具库载入后，注册表能按 id / 后缀查询内置编辑器。"""
    from app.sdk.editors import editor_api

    install_builtin_plugins()
    api = editor_api()
    assert api is not None, "载入内置插件后 editor.open 接口不可用"
    text = api.editor_by_id("builtin.editor.text")
    office = api.editor_by_id("builtin.editor.office")
    assert text is not None and office is not None, "内置编辑器没有注册"
    assert "md" in text.extensions and "json" in text.extensions, f"文本编辑器后缀不全：{text.extensions}"
    assert office.kind == "external", f"Office 编辑器应是 external：{office.kind}"
    assert "docx" in office.extensions, f"Office 扩展名缺失：{office.extensions}"
    assert api.editor_for(case.root / "sample.md") is text, "editor_for 没有命中文本编辑器"
    assert api.editors_for("docx"), "editors_for 查不到 docx 编辑器"
    assert "builtin.editor.text" in api.plugin_ids(), "plugin_ids 不含文本编辑器"


@check("editor_extension_contribution", "services")
def editor_extension_contribution(case: Case) -> None:
    """app.editor 扩展点上的贡献可见（key 为插件 id）。"""
    from app.sdk import ExtensionPoint
    from app.services.plugin_service import plugin_service

    install_builtin_plugins()
    items = plugin_service.point_items(ExtensionPoint.EDITOR)
    keys = {item.key for item in items}
    assert {"builtin.editor.text", "builtin.editor.office"} <= keys, f"编辑器贡献缺失：{keys}"
    for item in items:
        assert callable(getattr(item.value, "matches", None)), f"编辑器贡献不可用：{item.key}"


@check("editor_system_fallback", "services")
def editor_system_fallback(case: Case) -> None:
    """没有编辑器插件时，edit_path 退回系统默认编辑器。"""
    from app.sdk import editors
    from app.sdk import ui as sdk_ui
    from app.services.plugin_service import plugin_service

    plugin_service.load()  # 隔离目录为空：清掉上一个用例残留的 editor.open 接口
    assert editors.editor_api() is None, "隔离环境里不应有编辑器插件"
    assert not editors.rules_available(), "rules_available 应为 False"
    target = _text_file(case, "fallback.txt", "内容")
    seen: list[str] = []
    original = sdk_ui.open_default
    sdk_ui.open_default = lambda path: (seen.append(str(path)), True)[1]
    try:
        ok, message = editors.edit_path(target)
    finally:
        sdk_ui.open_default = original
    assert ok and message == "已交给系统默认编辑器", f"退路结果不对：{ok}, {message}"
    assert seen and Path(seen[0]) == target, f"没有交给系统默认编辑器：{seen}"


@check("editor_text_open_save", "services")
def editor_text_open_save(case: Case) -> None:
    """内置文本编辑器能打开文件、编辑并保存回磁盘。"""
    from app.sdk.editors import editor_api

    from .harness import ensure_app

    install_builtin_plugins()
    ensure_app()
    api = editor_api()
    editor = api.editor_by_id("builtin.editor.text")
    path = _text_file(case, "edit-me.txt", "原始内容")
    widget = editor.factory(path, None)
    try:
        assert widget.caption.startswith("编码"), f"控件没有读到文件：{widget.caption}"
        widget.area.setPlainText("改过的内容")
        assert widget.is_dirty(), "修改后 is_dirty() 应为 True"
        ok, message = widget.save()
        assert ok, f"保存失败：{message}"
        assert path.read_text(encoding="utf-8") == "改过的内容", "保存没有落盘"
    finally:
        _dispose(widget)


def _dispose(widget) -> None:
    """确定性销毁插件控件：只 deleteLater 会在后续界面检查里留下悬空引用。"""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QApplication

    widget.close()
    widget.setParent(None)
    sip.delete(widget)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


@check("editor_save_sync", "services")
def editor_save_sync(case: Case) -> None:
    """库内文件被编辑后，refresh_file 重算 checksum / size 并回读文本内容。"""
    from app.services import ImportService, ItemService

    source = _text_file(case, "sync.txt", "第一版")
    item = ImportService(case.session).import_files([source]).added[0]
    before = item.checksum
    target = ItemService(case.session).file_path_of(item)
    assert target is not None, "条目没有库内文件"
    target.write_text("第二版内容", encoding="utf-8")
    assert ItemService(case.session).refresh_file(item) is True, "refresh_file 返回失败"
    assert item.checksum != before, "checksum 没有更新"
    assert item.content == "第二版内容", f"文本内容没有同步：{item.content!r}"

@check("editor_config_format_list", "pages")
def editor_config_format_list(case: Case) -> None:
    """编辑器配置页左侧格式列表带标注：打开方式 + 库中数量，且能按关键词过滤。"""
    from app.core.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services import ImportService
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()
    source = _text_file(case, "标注.txt", "内容")
    markdown = _text_file(case, "标注.md", "# 标题")
    ImportService(case.session).import_files([source, markdown])
    case.session.commit()

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get("editor_config")
        assert page is not None, "编辑器插件应把配置页注册成插件页面 editor_config"
        window.switchTo(page)
        page.reload()

        joined = "\n".join(
            page.suffix_list.item(row).text() for row in range(page.suffix_list.count())
        )
        if "txt" in page._suffixes and ".txt — " not in joined:
            problems.append(f"txt 条目应带打开方式标注，实际 {joined}")
        if "库中 1 项" not in joined:
            problems.append(f"条目应带上库中数量，实际 {joined}")
        if "使用内置编辑器" not in joined:
            problems.append(f"有内置编辑器时标注应写明，实际 {joined}")
        count_text = page.count_label.text()
        if "/" not in count_text or not count_text.endswith("个格式"):
            problems.append(f"格式计数文案应为「N / M 个格式」，实际 {count_text!r}")

        page.search.setText("xlsx")
        filtered = [page.suffix_list.item(row).text() for row in range(page.suffix_list.count())]
        if len(filtered) != 1 or not filtered[0].startswith(".xlsx "):
            problems.append(f"搜索 xlsx 后应只剩 xlsx，实际 {filtered}")
        page.search.setText("")

        assert not problems, "编辑器配置页格式列表：" + "；".join(problems[:12])
    finally:
        dispose_window(window)
