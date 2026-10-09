"""封面规范：图片直接用自己、视频抽第一帧、其它类型不生成，以及「重置封面」的重建逻辑。

封面规则集中在 `app.services.cover_service`，导入、扫描登记、设置页「重置封面」三处共用。
这里覆盖：按类型的生成策略、没有媒体引擎（PyAV）时的优雅退回、重置时清空旧封面并按新规范重建，
以及界面侧 `item_card.cover_source()` 对图片的回退（`cover_path` 为空时用原文件）。
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from app.db.models import DataType
from app.services import ImportService, LibraryService
from app.services import cover_service
from app.ui.components.item_card import cover_source

from tests.harness import IsolatedCase

try:  # 生成一张真图片；缺 Pillow 时跳过依赖它的用例
    from PIL import Image
except ImportError:  # pragma: no cover - 取决于运行环境
    Image = None


class CoverBuildCase(IsolatedCase):
    """`build_cover()` 按类型决定封面：只有视频写封面文件。"""

    def test_image_has_no_cover_file(self) -> None:
        """图片不再生成封面副本：`cover_path` 留空，卡片直接用原文件。"""
        result = cover_service.build_cover("C:/任意/图片.png", "abc123", DataType.IMAGE)
        self.assertEqual(result, "", "图片不该再有封面文件")

    def test_other_types_have_no_cover_file(self) -> None:
        """文档 / 音频等类型没有封面概念。"""
        for data_type in (DataType.AUDIO, DataType.DOCUMENT, DataType.OTHER):
            with self.subTest(data_type=data_type):
                self.assertEqual(cover_service.build_cover("x", "abc123", data_type), "")

    def test_video_without_engine_falls_back_to_empty(self) -> None:
        """取不到媒体引擎（PyAV）时抽帧失败、返回空串，由界面回退到类型图标，而不是抛错。"""
        with mock.patch.object(cover_service.media_service, "available", return_value=False):
            result = cover_service.build_cover("C:/任意/视频.mp4", "abc123", DataType.VIDEO)
        self.assertEqual(result, "", "没有媒体引擎时应退回空封面")


class CoverResetCase(IsolatedCase):
    """`reset_covers()`：清空旧封面与旧记录，再按当前规范重建。"""

    def setUp(self) -> None:
        super().setUp()
        self.libraries = LibraryService(self.session)
        self.library = self.libraries.ensure_default()

    def _import_image(self, name: str = "封面图.png"):
        self.assertIsNotNone(Image, "缺少 Pillow，无法生成测试图片")
        source = self.root / name
        Image.new("RGB", (64, 64), (10, 120, 200)).save(source)
        return ImportService(self.session).import_files([source])

    def test_reset_clears_old_cover_records_and_files(self) -> None:
        """重置会清掉 `全局/covers/` 里的旧文件，并把旧的 `cover_path` 清空。"""
        from app.core.config import cover_dir

        result = self._import_image()
        item = result.added[0]
        # 造一份「旧规范」留下的封面：文件与记录都要被重置清掉
        covers = cover_dir()
        covers.mkdir(parents=True, exist_ok=True)
        stale = covers / "stale.png"
        stale.write_bytes(b"old-cover")
        item.cover_path = str(stale)
        self.session.commit()

        stats = cover_service.reset_covers(self.session)
        self.session.commit()

        self.assertFalse(stale.exists(), "旧封面文件应被清掉")
        self.assertEqual(item.cover_path, "", "图片的旧封面记录应被清空")
        self.assertEqual(stats["cleared"], 1, "应报告清掉了 1 个旧封面文件")
        self.assertEqual(stats["covers"], 0, "图片不再生成封面文件")

    def test_reset_reports_no_failure_without_videos(self) -> None:
        """没有视频时重置不该报失败。"""
        self._import_image()
        stats = cover_service.reset_covers(self.session)
        self.assertEqual(stats["failed"], 0)


class CoverSourceCase(IsolatedCase):
    """界面侧 `cover_source()`：图片在 `cover_path` 为空时回退到原文件。"""

    def _import_image(self, name: str):
        self.assertIsNotNone(Image, "缺少 Pillow，无法生成测试图片")
        source = self.root / name
        Image.new("RGB", (48, 48), (200, 60, 60)).save(source)
        result = ImportService(self.session).import_files([source])
        self.assertFalse(result.failed, f"导入失败：{result.failed}")
        return source, result.added[0]

    def test_image_falls_back_to_original_file(self) -> None:
        """图片的封面路径是可读的库内原文件。"""
        library = LibraryService(self.session).ensure_default()
        _source, item = self._import_image("回退图.png")
        self.assertEqual(item.cover_path, "", "图片导入后不该有封面副本")

        path = cover_source(item)
        self.assertTrue(path, "图片应回退到原文件作为封面")
        self.assertTrue(Path(path).is_file(), f"封面路径应指向真实文件：{path}")
        self.assertTrue(LibraryService(self.session).abs_path(item), "库内应有该文件")
        self.assertIsNotNone(library)

    def test_image_uses_library_copy_after_source_is_gone(self) -> None:
        """导入来源被删掉后，图片仍要用**库内那一份**当封面（用户 m01544 第 2 条）。

        以前走 `item_api._absolute_path()`，它优先返回导入时的外部 `source_path`；原文件一被移走，
        图片就退回默认类型图标——「重置封面」之后这个缺陷会集中暴露出来。
        """
        source, item = self._import_image("来源会消失.png")
        library_path = LibraryService(self.session).abs_path(item)
        self.assertIsNotNone(library_path)
        self.assertTrue(Path(library_path).is_file(), "库内文件应该存在")

        source.unlink()  # 模拟用户清理掉导入来源目录
        self.assertFalse(source.exists())

        path = cover_source(item)
        self.assertEqual(
            Path(path).resolve(),
            Path(library_path).resolve(),
            "来源不存在时必须退回库内那一份作封面",
        )

    def test_source_of_prefers_library_copy(self) -> None:
        """`_source_of()` 也优先库内那一份：来源删掉后视频抽帧仍要能拿到文件。"""
        source, item = self._import_image("来源会消失_抽帧.png")
        library_path = LibraryService(self.session).abs_path(item)
        self.assertEqual(
            Path(cover_service._source_of(item)).resolve(),
            Path(library_path).resolve(),
            "库内文件在时 should 用库内那一份",
        )
        source.unlink()
        self.assertEqual(
            Path(cover_service._source_of(item)).resolve(),
            Path(library_path).resolve(),
            "来源删掉后仍要用库内那一份",
        )

    def test_image_without_any_file_falls_back_to_icon(self) -> None:
        """库内与来源都取不到时返回空串，由界面显示默认类型图标。"""
        _source, item = self._import_image("两边都没了.png")
        library_path = LibraryService(self.session).abs_path(item)
        Path(library_path).unlink()
        item.source_path = str(self.root / "根本不存在.png")
        self.session.commit()

        self.assertEqual(cover_source(item), "", "两处都取不到时不该给死路径")
