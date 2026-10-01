"""界面交互烟测（离屏）：导入 → 编辑 → 加标签 → 隐藏 → 删除/还原/彻底删除 → 存档。

用法：.venv\\Scripts\\python.exe scripts\\dev_check_flow.py
对话框与确认框被替换为桩实现，避免阻塞。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_check_guard import run_guarded  # noqa: E402

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.core import paths  # noqa: E402
from app.core.config import config  # noqa: E402
from app.core.logging_setup import setup_logging  # noqa: E402
from app.db.database import init_db, session_scope  # noqa: E402
from app.db.seed import seed  # noqa: E402


class _FakeDialog:
    """替身：始终"确认"，标签输入返回固定文本，编辑对话框返回固定字段。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def exec(self) -> bool:
        return True

    def value(self) -> str:
        return "烟测标签A, 烟测标签B"

    def values(self) -> dict:
        return {
            "name": "改名后的烟测笔记",
            "category_id": None,
            "tags": ["烟测标签"],
            "keywords": ["烟", "测"],
            "is_hidden": False,
        }


def main() -> int:  # noqa: C901
    paths.ensure_dirs()
    setup_logging()
    init_db(force=True)
    with session_scope() as session:
        seed(session)

    app = QApplication(sys.argv)

    import app.ui.pages.manage_page as mp  # noqa: PLC0415
    from app.ui.main_window import MainWindow  # noqa: PLC0415

    mp.confirm = lambda *args, **kwargs: True
    mp.ItemEditDialog = _FakeDialog
    mp.TextInputDialog = _FakeDialog

    window = MainWindow()
    imp, manage, archive, home = (window.import_page, window.manage_page, window.archive_page, window.home_page)

    failures = 0

    def step(label: str, fn) -> None:
        nonlocal failures
        try:
            print(f"{label}: {fn()}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"{label}: FAILED")
            traceback.print_exc()

    def add_text(name: str, content: str) -> None:
        imp._set_mode("text")
        imp.name_edit.setText(name)
        imp.text_edit.setPlainText(content)
        imp.tag_input.clear()
        imp.keyword_input.clear()
        imp.import_now()

    def do_import() -> str:
        imp._set_mode("text")
        imp.name_edit.setText("烟测笔记")
        imp.text_edit.setPlainText("第一行\n第二行")
        imp.tag_input.set_keywords(["烟测标签"])
        imp.keyword_input.set_keywords(["烟", "测"])
        imp.import_now()
        return f"count={manage.item_repo.stats()['total']}"

    step("import text", do_import)

    def do_render() -> str:
        manage.refresh()
        manage._set_mode("card")
        cards = len(manage._items)
        manage._set_mode("list")
        return f"items={cards} list_rows={len(manage._items)}"

    step("render list/card", do_render)

    def do_edit() -> str:
        manage._selected = {manage._items[0].id}
        manage._on_edit()
        return f"name={manage._items[0].name!r}"

    step("edit item", do_edit)

    def do_tags() -> str:
        manage._selected = {manage._items[0].id}
        manage._on_add_tags()
        return f"tags={manage._items[0].tag_names}"

    step("add tags", do_tags)

    def do_hidden() -> str:
        item_id = manage._items[0].id
        manage._selected = {item_id}
        picked = len(manage.selected_items())
        manage._on_toggle_hidden()
        hidden = manage.item_repo.get(item_id).is_hidden
        manage.filter_panel.hidden_box.setChecked(True)
        manage._selected = {item_id}
        manage._on_toggle_hidden()
        restored = manage.item_repo.get(item_id).is_hidden
        manage.filter_panel.hidden_box.setChecked(False)
        return f"picked={picked} id={item_id} hidden={hidden} restored={restored}"

    step("toggle hidden", do_hidden)

    def do_trash() -> str:
        if not manage._items:
            manage.filter_panel.hidden_box.setChecked(True)
        manage._selected = {manage._items[0].id}
        manage._on_delete()
        manage.filter_panel.trash_box.setChecked(True)
        trashed = len(manage._items)
        manage._selected = {item.id for item in manage._items}
        manage._on_restore()
        manage.filter_panel.trash_box.setChecked(False)
        return f"trashed_view={trashed} visible_after_restore={len(manage._items)}"

    step("delete -> trash -> restore", do_trash)

    def do_purge() -> str:
        add_text("待彻底删除", "x")
        manage.refresh()
        manage._selected = {item.id for item in manage._items if item.name == "待彻底删除"}
        manage._on_delete()
        manage.filter_panel.trash_box.setChecked(True)
        manage._selected = {item.id for item in manage._items}
        manage._on_purge()
        manage.filter_panel.trash_box.setChecked(False)
        return f"remaining={len(manage._items)}"

    step("purge", do_purge)

    def do_search() -> str:
        manage.filter_panel.search.setText("烟测笔记")
        by_name = len(manage._items)
        manage.filter_panel.search.setText("第二行 烟")
        by_content = len(manage._items)
        manage.filter_panel.search.setText("绝对不存在的词")
        none_found = len(manage._items)
        manage.filter_panel.search.setText("")
        return f"name={by_name} content={by_content} none={none_found}"

    step("full-text search", do_search)

    def do_categories() -> str:
        category = manage.taxonomy.create_category("烟测分类")
        manage.session.commit()
        manage.refresh()
        manage._on_tree_action("add", category.id if category else None)
        return f"tree_categories={len(manage.taxonomy.tree())}"

    step("category tree action", do_categories)

    def do_archive() -> str:
        archive.service.create(note="烟测存档")
        archive.session.commit()
        archive._reload_archives()
        archive.archive_list.setCurrentRow(0)
        return f"archives={len(archive._archives)} rows={archive.table.rowCount()} diff={archive.diff_label.text()!r}"

    step("archive create + inspect", do_archive)

    def do_archive_cleanup() -> str:
        archive._on_cleanup()
        return "ok"

    step("archive cleanup orphans", do_archive_cleanup)

    def do_settings_prune() -> str:
        page = window.settings_page
        page._on_prune_mode_changed("size")
        enabled = (page._keep_size_card.isEnabled(), page._keep_versions_card.isEnabled())
        page._on_prune_mode_changed("count")
        assert enabled == (True, False), enabled
        assert page._keep_versions_card.isEnabled() and not page._keep_size_card.isEnabled()
        return f"size_mode_cards={enabled}"

    step("settings prune policy cards", do_settings_prune)

    def do_archive_prune() -> str:
        archive.service.create(note="修剪自检 A")
        archive.service.create(note="修剪自检 B")
        archive.session.commit()
        archive._reload_archives()
        config.set(config.pruneMode, "size")
        config.set(config.keepSize, 64)
        archive._refresh_policy()
        caption = archive.policy_label.text()
        archive._on_prune()
        remaining = len(archive._archives)
        config.set(config.pruneMode, "count")
        archive._reload_archives()
        assert "容量上限 64 MB" in caption, caption
        assert "保留最近 10 个存档" in archive.policy_label.text(), archive.policy_label.text()
        assert remaining >= 1, remaining
        return f"caption={caption!r} remaining={remaining}"

    step("archive prune policy", do_archive_prune)

    def do_home() -> str:
        home.refresh()
        return "ok"

    step("home refresh", do_home)

    def do_settings() -> str:
        page = window.settings_page
        page._on_theme_changed("dark")
        page._on_theme_changed("auto")
        return "ok"

    step("settings theme switch", do_settings)

    def do_privacy() -> str:
        from app.services import UserService  # noqa: PLC0415

        users = UserService(manage.session)
        owner = users.current()
        users.set_password(owner, "烟测口令")
        manage.session.commit()
        verified = users.verify(owner, "烟测口令")
        manage._unlocked = False
        manage.filter_panel.hidden_box.setChecked(True)
        blocked = not manage._unlocked
        users.set_password(owner, "")
        manage.session.commit()
        return f"verify={verified} locked_after_prompt={blocked}"

    step("privacy gate", do_privacy)

    def do_users() -> str:
        from app.repositories import TagRepository  # noqa: PLC0415
        from app.services import UserService  # noqa: PLC0415

        service = UserService(manage.session)
        tags = TagRepository(manage.session)
        for info in service.list_users():
            if info.name == "烟测用户":
                assert service.delete(info.user), "清理失败"
        manage.session.commit()
        first = service.current()
        first_tags = sorted(tags.names(user_id=first.id))
        second = service.create("烟测用户", password="烟测口令")
        manage.session.commit()
        second_tags = sorted(tags.names(user_id=second.id))
        assert set(second_tags) == {"重要", "待整理", "收藏"}, second_tags
        assert set(second_tags) & set(first_tags), first_tags
        service.set_current(second)
        manage.session.commit()
        manage.refresh()
        isolated = len(manage._items)
        visible_tags = sorted(tags.names(user_id=second.id))
        service.set_current(first)
        manage.session.commit()
        manage.refresh()
        back = len(manage._items)
        window.user_page.refresh()
        assert service.is_admin(first) and not service.is_admin(second), "管理员标记异常"
        assert not service.delete(first), "默认用户不应可删除"
        service.delete(second)
        manage.session.commit()
        merged_tags = sorted(tags.names(user_id=first.id))
        assert set(merged_tags) == set(first_tags), (first_tags, merged_tags)
        return (
            f"isolated_items={isolated} back_items={back} users={len(service.list_users())} "
            f"tags_second={len(visible_tags)} tags_after_delete={len(merged_tags)}"
        )

    step("user switch isolation", do_users)

    def do_watcher() -> str:
        from PyQt6.QtCore import QEventLoop, QTimer  # noqa: PLC0415

        from app.core import paths  # noqa: PLC0415

        watcher = window.library_watcher
        watcher.resume()
        seen: list[int] = []
        handler = lambda: seen.append(1)
        watcher.changed.connect(handler)
        probe = paths.DEFAULT_LIBRARY_DIR / "监听探针.txt"

        def wait(ms: int) -> None:
            loop = QEventLoop()
            QTimer.singleShot(ms, loop.quit)
            loop.exec()

        try:
            probe.write_text("监听", encoding="utf-8")
            wait(2600)
        finally:
            watcher.changed.disconnect(handler)
            probe.unlink(missing_ok=True)
        assert seen, "库文件夹外部新增未触发提醒"
        return f"watched={watcher.watched} events={len(seen)}"

    step("library folder watching", do_watcher)

    def do_paging() -> str:
        from app.services import ImportService  # noqa: PLC0415

        manage.filter_panel.search.setText("")
        manage.filter_panel.trash_box.setChecked(False)
        manage.filter_panel.hidden_box.setChecked(False)
        manage._category_id = None

        service = ImportService(manage.session)
        user_id = manage.user_service.current_id()
        extra = [
            service.import_text(f"分页探针{index}", f"分页探针内容 {index}", user_id=user_id)
            for index in range(3)
        ]
        manage.session.commit()

        manage._page_size = 2
        manage._page = 0
        manage.refresh()
        total = manage._total
        pages = manage.pager.pages
        first_page = len(manage._items)
        assert pages == -(-total // 2), (total, pages)
        assert pages > 1, total
        assert first_page == 2, first_page

        manage._on_item_activated(manage._items[0])
        manage._on_page_changed(1)
        second_page = len(manage._items)
        kept = len(manage.selected_items())
        assert kept == 1, kept

        manage.item_service.purge([item for item in extra if item is not None])
        manage.session.commit()
        manage._page_size = 100
        manage._page = 0
        manage.refresh()
        return (
            f"total={total} pages={pages} first={first_page} second={second_page} "
            f"kept_selection={kept} after_purge={manage._total}"
        )

    step("pagination", do_paging)

    def do_alignment() -> str:
        from qfluentwidgets import ToolButton  # noqa: PLC0415
        from qfluentwidgets.components.layout.flow_layout import AdaptiveFlowLayout  # noqa: PLC0415

        from app.ui.common import BusyTip  # noqa: PLC0415
        from app.ui.widgets.keyword_input import KeywordChip, KeywordInput  # noqa: PLC0415

        from app.services import ImportService  # noqa: PLC0415

        window.show()
        window.switchTo(manage)  # 卡片必须可见，isTight 的流式布局才会排布
        app.processEvents()

        service = ImportService(manage.session)
        user_id = manage.user_service.current_id()
        probes = []
        while len(manage._items) < 5:  # 至少 5 张卡，保证能排出多列
            probes.append(service.import_text(f"对齐探针{len(probes)}", "对齐探针内容", user_id=user_id))
            manage.session.commit()
            manage.refresh()

        assert isinstance(manage.card_layout, AdaptiveFlowLayout), type(manage.card_layout)
        assert not isinstance(manage.list_layout, AdaptiveFlowLayout)
        manage._set_mode("card")
        host = manage.card_view.widget()
        host.resize(1200, 600)
        app.processEvents()
        manage.card_layout.setGeometry(host.rect())
        cards = [manage.card_layout.itemAt(i).widget() for i in range(manage.card_layout.count())]
        widths = {card.width() for card in cards}
        columns = len({card.x() for card in cards})
        assert len(widths) == 1 and next(iter(widths)) > 0, widths
        assert columns > 1, columns
        manage._set_mode("list")

        assert isinstance(home._total_card.parent().layout(), AdaptiveFlowLayout)

        field = KeywordInput()
        field.resize(320, 80)
        field.set_keywords(["对齐检查"])
        field.show()
        app.processEvents()
        chips = field.findChildren(KeywordChip)
        assert len(chips) == 1, len(chips)
        chip_tooltip = chips[0].findChild(ToolButton).toolTip()
        assert chip_tooltip == "移除该关键词", chip_tooltip
        assert field.chips_height() > 0, field.chips_height()
        field.close()

        busy = BusyTip(window, "对齐检查", "进行中")
        busy.update("仍在进行")
        busy.finish("完成")
        assert busy._tip is not None

        assert not hasattr(window, "_manage_badge"), "导航蓝色徽标应已移除"
        manage.restore_button.setEnabled(True)
        manage._selected.clear()
        manage._update_count_label()
        assert not manage.restore_button.isEnabled(), "未选中数据时不应可还原"
        probe = probes[0]
        assert probe is not None and not probe.is_deleted, probe
        manage._selected = {probe.id}
        manage._update_count_label()
        assert not manage.restore_button.isEnabled(), "未删除的数据不应可还原"
        manage.item_service.delete([probe])
        manage.session.commit()
        manage._selected = {probe.id}
        manage._update_count_label()
        assert manage.restore_button.isEnabled(), "已删除的数据应可还原"
        manage.item_service.restore([probe])
        manage.session.commit()
        expected = manage.item_repo.stats(manage._duplicate_scope())["duplicate_groups"]
        hint = manage._buttons["duplicates"].toolTip()
        text = hint

        manage.item_service.purge([item for item in probes if item is not None])
        manage.session.commit()
        manage.refresh()
        return (
            f"card_width={next(iter(widths))} columns={columns} chip_tooltip={chip_tooltip!r} "
            f"duplicate_hint={text!r} duplicates={expected}"
        )

    step("framework alignment", do_alignment)

    print(f"RESULT failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_guarded(main))
