"""L4 流程检查：跨服务的真实旅程，以及真数据上的界面刷新。

服务层检查关注单个服务的行为；这一层关注「用户按顺序用下来」是否说得通，
以及页面在真实数据上刷新时不会崩、不会漏。
"""

from __future__ import annotations

import shutil

from . import fixtures
from .checks_pages import PAGE_ATTRS, dispose_window
from .harness import Case, check, ensure_app


@check("user_journey", "flows")
def user_journey(case: Case) -> None:
    """导入目录 → 改名打标 → 重复检测 → 存档对比 → 导出清单 → 概览统计。"""
    from app.repositories import CategoryRepository, ItemRepository
    from app.services import (
        ArchiveService,
        ExportService,
        ImportService,
        ItemService,
        overview,
        storage_usage,
        type_breakdown,
    )

    session = case.session
    handle = fixtures.build(case)

    inbox = case.root / "inbox"
    (inbox / "sub").mkdir(parents=True, exist_ok=True)
    (inbox / "a.txt").write_text("旅程文件甲\n第二行", encoding="utf-8")
    (inbox / "sub" / "b.txt").write_text("旅程文件乙", encoding="utf-8")
    if fixtures.SAMPLE_IMAGE.exists():
        shutil.copy2(fixtures.SAMPLE_IMAGE, inbox / "c.png")

    importer = ImportService(session)
    result = importer.import_folder(inbox, category_id=handle.category_child, tags=[handle.tag])
    assert not result.failed, f"导入目录时出错：{result.failed}"
    assert len(result.added) >= 3, f"目录导入数量不对：{result.summary()}"
    assert result.skipped == [], f"不应跳过任何文件：{result.skipped}"

    items = ItemRepository(session)
    target = sorted(result.added, key=lambda item: item.name)[0]
    ItemService(session).update(target, name="旅程改名", tags=[handle.tag, "重要"])
    session.commit()
    assert target.name == "旅程改名", f"改名失败：{target.name}"
    assert "重要" in target.tag_names, f"追加标签失败：{target.tag_names}"

    counts = CategoryRepository(session).item_counts()
    assert counts.get(handle.category_child, 0) >= 3, f"子分类计数不对：{counts}"
    duplicates = ItemService(session).duplicate_map()
    assert isinstance(duplicates, dict), f"重复检测返回类型不对：{type(duplicates).__name__}"

    archives = ArchiveService(session)
    archive = archives.create(name="旅程存档", note="流程检查")
    session.commit()
    history = archives.history()
    assert history and history[-1].id == archive.id, "最新存档没有排在历史末尾"
    assert archives.compare(archive).is_empty, "刚建的存档本应与当前库一致"

    late = case.root / "late.txt"
    late.write_text("存档之后才导入的文件", encoding="utf-8")
    late_result = importer.import_files([late])
    session.commit()
    assert late_result.added, "补导入失败"
    diff = archives.compare(archive)
    assert not diff.is_empty, "存档之后新增的数据应报告差异"
    assert late_result.added[0].name in diff.added, f"差异里没有新增项：{diff.added}"

    restored = archives.restore_all(archive)
    session.commit()
    assert set(restored) == {"restored", "skipped"}, f"整档还原返回结构不对：{restored}"

    assert isinstance(archives.orphans(), list), "孤立记录查询返回类型不对"
    assert archives.policy_summary(), "清理策略摘要为空"

    export_dir = case.root / "export"
    report = ExportService(session).export_items(list(items.all()), export_dir)
    manifest = report.manifest
    assert manifest is not None and manifest.exists(), f"导出清单没落盘：{manifest}"
    manifest_text = manifest.read_text(encoding="utf-8")
    assert "旅程改名" in manifest_text, "清单里没有导出项"

    stats = overview(session)
    assert stats["total"] >= 4, f"总览统计偏少：{stats['total']}"
    assert stats["total_size"] > 0, "总览统计里没有体积"
    assert stats["categories"] >= 2, f"分类数量不对：{stats['categories']}"
    assert stats["tags"] >= 1, f"标签数量不对：{stats['tags']}"
    assert type_breakdown(session), "类型分布为空"
    assert storage_usage(session), "存储占用为空"


def _row_text(row) -> str:
    """把表格行渲染成可搜索的文本（行里可能是 ORM 对象）。"""
    parts = []
    for cell in row if isinstance(row, (list, tuple)) else [row]:
        parts.append(str(getattr(cell, "name", cell)))
    return " ".join(parts)


@check("pages_on_real_data", "flows")
def pages_on_real_data(case: Case) -> None:
    """真数据装配主窗口：九个页面都能刷新，标签页与存档页跟着数据走。"""
    from app.services import ArchiveService

    from app.ui.main_window import MainWindow

    ensure_app()
    handle = fixtures.build(case)
    window = MainWindow()
    try:
        for attr in PAGE_ATTRS:
            getattr(window, attr).refresh()

        rows = [_row_text(row) for row in window.tag_page._visible_rows()]
        assert any(handle.tag in row for row in rows), f"标签页没有列出「{handle.tag}」：{rows[:3]}"
        assert window.home_page.kpi_cards, "首页没有 KPI 卡片"

        archives = ArchiveService(case.session)
        archives.create(name="流程存档", note="界面刷新")
        case.session.commit()
        window.archive_page.refresh()

        window.tag_page.select_all()
        assert window.tag_page.checked_tags(), "标签页全选后没有勾选项"
        window.tag_page.select_none()
        assert not window.tag_page.checked_tags(), "标签页取消全选后仍有勾选项"
    finally:
        dispose_window(window)
