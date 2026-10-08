"""L2 页面检查：下载管理页 —— 「下载列表」与「下载设置」两个 Tab。

不联网：列表渲染用一份「把固定字节当下载结果」的队列桩（覆盖 `_fetch`），
镜像规则的增删改只落到清单文件上，规则编辑弹窗本身不在这里驱动。
"""

from __future__ import annotations

import time

from PyQt6.QtWidgets import QApplication

from .harness import Case, check, dispose_window, ensure_app

PAYLOAD = b"selfcheck-download-payload"
ROUTE = "downloadPage"
TAB_ROUTES = ("list", "settings")
#: 内置默认规则里应当出现的主机名（HuggingFace 与 GitHub 各一条）。
DEFAULT_PATTERNS = ("huggingface.co", "github")


def _expect(problems: list[str], ok: bool, message: str) -> None:
    if not ok:
        problems.append(message)


def _build_page():
    from app.ui.main_window import MainWindow

    ensure_app()
    window = MainWindow()
    return window, window.download_page


@check("download_open_dir", "pages")
def download_open_dir(case: Case) -> None:
    """「打开下载目录」按钮：点下去要按当前下载路径调 open_path，不能炸在 config 上。"""
    from app.core.config import download_dir
    from app.ui.pages import download_page as download_page_module

    window, page = _build_page()
    opened: list[str] = []
    original = download_page_module.open_path
    download_page_module.open_path = lambda path: (opened.append(str(path)), True)[1]
    try:
        page.open_dir_button.click()
    finally:
        download_page_module.open_path = original
        dispose_window(window)

    expected = str(download_dir())
    assert opened, "点「打开下载目录」没有调用 open_path"
    assert opened[0] == expected, f"打开的应当是 {expected}，实际 {opened[0]}"


@check("download_page_tabs", "pages")
def download_page_tabs(case: Case) -> None:
    """下载管理页：两个 Tab、列表页与设置页都装配好，切 Tab 跟着切堆叠页。"""
    from app.core.config import config
    from app.ui.pages.download_page import TAB_INDEX, TAB_LIST, TAB_SETTINGS

    window, page = _build_page()
    problems: list[str] = []
    try:
        _expect(problems, page.objectName() == ROUTE, f"objectName 应为 {ROUTE}，实际 {page.objectName()}")
        routes = tuple(page.tabs.items)
        _expect(problems, routes == TAB_ROUTES, f"两个 Tab 的路由应为 {TAB_ROUTES}，实际 {routes}")
        _expect(problems, page.stack.count() == 2, f"堆叠页应有 2 页，实际 {page.stack.count()}")
        _expect(
            problems,
            page.stack.widget(TAB_INDEX[TAB_LIST]) is page.list_view,
            "第一页应当是下载列表",
        )
        _expect(
            problems,
            page.stack.widget(TAB_INDEX[TAB_SETTINGS]) is page.settings_area,
            "第二页应当是下载设置",
        )

        page.tabs.setCurrentItem(TAB_SETTINGS)
        _expect(
            problems,
            page.stack.currentIndex() == TAB_INDEX[TAB_SETTINGS],
            f"切到「下载设置」后应显示第 {TAB_INDEX[TAB_SETTINGS]} 页，实际 {page.stack.currentIndex()}",
        )
        page.tabs.setCurrentItem(TAB_LIST)
        _expect(
            problems,
            page.stack.currentIndex() == TAB_INDEX[TAB_LIST],
            "切回「下载列表」后应显示第一页",
        )

        _expect(problems, bool(page.path_card.contentLabel.text()), "「默认下载路径」卡片应显示当前路径")
        _expect(
            problems,
            page.sequential_card.configItem is config.downloadSequential,
            "「顺序下载」开关应直接绑到配置项 downloadSequential",
        )
        for card, item in (
            (page.concurrent_card, config.downloadConcurrent),
            (page.timeout_card, config.downloadTimeout),
            (page.retries_card, config.downloadRetries),
        ):
            _expect(
                problems,
                card.slider.value() == item.value,
                f"数字设置卡应显示配置项 {item.key} 的值 {item.value}，实际 {card.slider.value()}",
            )

        patterns = [page.ruleTable.item(row, 1).text() for row in range(page.ruleTable.rowCount())]
        _expect(problems, len(patterns) == 2, f"默认应有 2 条镜像规则，实际 {len(patterns)}：{patterns}")
        for want in DEFAULT_PATTERNS:
            _expect(
                problems,
                any(want in pattern for pattern in patterns),
                f"默认规则里应有一条匹配 {want}，实际 {patterns}",
            )
        _expect(
            problems,
            page.settings_empty.isHidden(),
            "有规则时「还没有下载规则」的空状态应当收起来",
        )
    finally:
        dispose_window(window)
    assert not problems, "下载管理页未通过：" + "；".join(problems)


@check("download_rules_edit", "pages")
def download_rules_edit(case: Case) -> None:
    """镜像规则的增、删、改、排序都落进清单文件，并且能被队列直接拿去用。"""
    from app.core.download import MODE_MIRROR, MirrorRule, mirror_store
    from app.core.manifest import manifest_kit

    window, page = _build_page()
    problems: list[str] = []
    rule = MirrorRule(
        id="rule-selfcheck",
        pattern="example.com",
        title="自检规则",
        official="https://example.com",
        mirrors=("https://mirror.example.com",),
        mode=MODE_MIRROR,
    )
    try:
        _expect(problems, page.ruleTable.rowCount() == 2, "默认应有 2 条规则")

        page._save_rules(page._rules.with_rule(rule))
        _expect(problems, page.ruleTable.rowCount() == 3, "新增规则后表里应有 3 行")
        _expect(
            problems,
            "core.download_mirrors" in manifest_kit.ids(),
            "保存规则后应动态登记 core.download_mirrors 清单",
        )
        loaded = mirror_store.load_rules()
        _expect(problems, len(loaded.rules) == 3, f"回读应有 3 条规则，实际 {len(loaded.rules)}")
        matched = loaded.match("https://example.com/a.bin")
        _expect(problems, matched.id == rule.id, f"example.com 应命中新规则，实际 {matched.id}")
        _expect(
            problems,
            loaded.candidates("https://example.com/a.bin") == ["https://mirror.example.com/a.bin"],
            "镜像模式应只给出镜像地址",
        )

        # 上移一条：顺序会落进清单，回读后仍然一致。
        before = [item.id for item in page._rules.ordered()]
        page._save_rules(page._rules.moved(rule.id, -1), "已上移")
        after = [item.id for item in mirror_store.load_rules().ordered()]
        _expect(problems, after != before, "上移之后启用规则的顺序应当变化")
        _expect(problems, after[1] == rule.id, f"上移一次后新规则应排到第二位，实际 {after}")

        page._save_rules(page._rules.without_rule(rule.id), "已删除")
        _expect(problems, page.ruleTable.rowCount() == 2, "删除规则后表里应回到 2 行")
        _expect(
            problems,
            all(item.id != rule.id for item in mirror_store.load_rules().rules),
            "删除的规则不应再出现在清单里",
        )

        # 「恢复默认」前面有确认框：自检里把它换掉，否则模态框会一直等下去。
        from app.ui.pages import download_page as download_page_module

        original_confirm = download_page_module.confirm
        download_page_module.confirm = lambda *args, **kwargs: True
        try:
            page._reset_rules()
        finally:
            download_page_module.confirm = original_confirm
        _expect(problems, page.ruleTable.rowCount() == 2, "恢复默认后应回到 2 条内置规则")
        defaults = mirror_store.load_rules()
        _expect(
            problems,
            [item.id for item in defaults.ordered()] == ["huggingface", "github"],
            f"恢复默认应是内置两条，实际 {[item.id for item in defaults.ordered()]}",
        )
    finally:
        mirror_store.reset_rules()
        dispose_window(window)
    assert not problems, "下载规则编辑未通过：" + "；".join(problems)


@check("download_list_view", "pages")
def download_list_view(case: Case) -> None:
    """下载列表：队列里有任务就出表、出进度，终态任务的按钮各归各位。"""
    from app.core.download import (
        STATE_DONE,
        STATE_LABELS,
        DownloadManager,
        DownloadOptions,
    )
    from app.ui.components.download_view import COL_NAME, COL_STATE, DownloadListView

    class _StubManager(DownloadManager):
        """不出网的任务队列：把固定字节当成下载结果。"""

        def _fetch(self, job, url, part):
            part.write_bytes(PAYLOAD)
            job.done_bytes = len(PAYLOAD)
            job.total_bytes = len(PAYLOAD)

    ensure_app()
    manager = _StubManager(DownloadOptions(concurrent=1), index=None, name="selfcheck")
    view = DownloadListView(provider=lambda: manager, reader=lambda: manager)
    problems: list[str] = []
    try:
        _expect(problems, view.table.rowCount() == 0, "空队列不该有任务行")
        _expect(problems, not view.empty.isHidden(), "空队列应当显示空状态")
        _expect(problems, view.table.isHidden(), "空队列不显示表格")

        target = case.root / "downloads" / "a.bin"
        job = manager.enqueue(["https://example.com/pkg/a.bin"], target, label="自检文件")
        deadline = time.monotonic() + 15.0
        while manager.find(job.id).state != STATE_DONE and time.monotonic() < deadline:
            time.sleep(0.02)
        final = manager.find(job.id)
        _expect(problems, final.state == STATE_DONE, f"任务应当跑完，实际 {final.state}（{final.error}）")
        _expect(problems, target.is_file(), f"应当落到 {target}")

        view.refresh()
        _expect(problems, view.table.rowCount() == 1, f"表格应有 1 行，实际 {view.table.rowCount()}")
        _expect(
            problems,
            view.table.item(0, COL_NAME).text() == "自检文件",
            f"名称列应显示任务标签，实际 {view.table.item(0, COL_NAME).text()}",
        )
        _expect(
            problems,
            view.table.item(0, COL_STATE).text() == STATE_LABELS[STATE_DONE],
            f"状态列应显示「{STATE_LABELS[STATE_DONE]}」，实际 {view.table.item(0, COL_STATE).text()}",
        )
        _expect(problems, view.empty.isHidden(), "有任务时应当收起空状态")
        _expect(problems, view.table.isVisible() or not view.table.isHidden(), "有任务时应当显示表格")
        _expect(problems, "1 项" in view.summaryLabel.text(), f"摘要有 1 项，实际 {view.summaryLabel.text()!r}")

        view.table.selectRow(0)
        view._sync_buttons()
        _expect(problems, not view.pauseButton.isEnabled(), "已完成的任务不该能暂停")
        _expect(problems, not view.cancelButton.isEnabled(), "已完成的任务不该能取消")
        _expect(problems, view.removeButton.isEnabled(), "已完成的任务应当能移除")
        _expect(problems, not view.retryButton.isEnabled(), "已完成的任务不该能重试")
        _expect(problems, not view.startAllButton.isEnabled(), "没有可继续的任务时「全部继续」应禁用")
        _expect(problems, view.clearButton.isEnabled(), "有已结束任务时「清理已结束」应可用")

        _expect(problems, manager.forget(job.id) is True, "终态任务应当能移除")
        view.refresh()
        _expect(problems, view.table.rowCount() == 0, "移除之后表格应清空")
    finally:
        manager.shutdown(wait=1.0)
        QApplication.processEvents()
        dispose_window(view)
    assert not problems, "下载列表未通过：" + "；".join(problems)


def _pump(times: int = 6) -> None:
    """空转几圈事件循环，让推迟到布局稳定之后的列宽重排真的跑完。"""
    for _ in range(times):
        QApplication.processEvents()
        time.sleep(0.01)


@check("download_list_columns", "pages")
def download_list_columns(case: Case) -> None:
    """下载列表的排版：空状态排在功能按钮上面，列宽跟着表格宽度铺满、不留右侧空白。"""
    from PyQt6.QtCore import Qt

    from app.core.download import STATE_DONE, DownloadManager, DownloadOptions
    from app.ui.components.download_view import (
        FIXED_COLUMNS,
        TEXT_COLUMNS,
        TEXT_MIN_WIDTH,
        DownloadListView,
    )

    class _StubManager(DownloadManager):
        """不出网的任务队列：把固定字节当成下载结果。"""

        def _fetch(self, job, url, part):
            part.write_bytes(PAYLOAD)
            job.done_bytes = len(PAYLOAD)
            job.total_bytes = len(PAYLOAD)

    def widths() -> list[int]:
        table = view.table
        return [table.columnWidth(column) for column in range(table.columnCount())]

    ensure_app()
    manager = _StubManager(DownloadOptions(concurrent=1), index=None, name="selfcheck-columns")
    view = DownloadListView(provider=lambda: manager, reader=lambda: manager)
    problems: list[str] = []
    try:
        layout = view.layout()
        _expect(problems, layout.itemAt(0).widget() is view.empty, "空状态应当排在工具条上面")
        _expect(problems, layout.stretch(0) == 1, "空状态应当分到多余的高度")
        _expect(problems, layout.indexOf(view.table) > 1, "表格应当排在工具条下面")

        # 造一个任务让表格真的露出来（表格藏着的时候没有列宽可言）。
        target = case.root / "downloads" / "columns.bin"
        job = manager.enqueue(["https://example.com/pkg/columns.bin"], target, label="排版自检")
        deadline = time.monotonic() + 15.0
        while manager.find(job.id).state != STATE_DONE and time.monotonic() < deadline:
            time.sleep(0.02)
        view.refresh()
        _expect(problems, not view.table.isHidden(), "有任务时表格应当露出来")

        # 控件要从「藏着的顶层窗口」变成「算得出真实宽度」：屏幕外 show 一次，布局才会跟着 resize 走。
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.show()

        view.resize(1000, 460)
        _pump()
        wide = view.table.viewport().width()
        first = widths()
        # 宽度是按表格真实宽度算出来的，整数除法可能差几像素，但不该出现一大条空白或横向溢出。
        _expect(problems, 0 <= wide - sum(first) <= len(TEXT_COLUMNS), f"列宽应当铺满表格（{sum(first)} / {wide}）")
        for column, value in FIXED_COLUMNS.items():
            _expect(problems, first[column] == value, f"第 {column} 列应固定成 {value}，实际 {first[column]}")
        for column in TEXT_COLUMNS:
            _expect(problems, first[column] >= TEXT_MIN_WIDTH, f"第 {column} 列不该窄于 {TEXT_MIN_WIDTH}")

        # 变窄之后列宽要跟着重排（这正是原先把宽度只量一次、右边空一大条的毛病）。
        view.resize(900, 460)
        _pump()
        narrow = view.table.viewport().width()
        second = widths()
        _expect(problems, narrow < wide, f"窗口变窄后表格也该变窄（view {view.width()}/{view.minimumSizeHint().width()} 表格 {narrow} !< {wide}）")
        _expect(problems, second != first, "窗口变窄后列宽应当重排一次")
        _expect(problems, 0 <= narrow - sum(second) <= len(TEXT_COLUMNS), f"变窄后列宽仍应铺满（{sum(second)} / {narrow}）")
    finally:
        manager.shutdown(wait=1.0)
        dispose_window(view)
    assert not problems, "下载列表排版未通过：" + "；".join(problems)
