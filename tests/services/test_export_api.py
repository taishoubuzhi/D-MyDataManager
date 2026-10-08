"""导出接口（`app.sdk.export` + `app.services.export_api`）的用例。

**不弹真对话框**：确认框被换成假的（或者直接 `confirm=False`），重点看接口层——默认目录、
计划（只算不写）、快照映射、用户取消时的取舍、以及「选中的条目都不在了」这类边界。
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.core.config import export_dir
from app.core.plugins.extensions import extension_registry
from app.sdk import export as export_sdk
from app.sdk.errors import SdkError
from app.services import export_api
from app.services.export_api import ExportApi, _clean_ids, to_ref
from app.services.export_service import PackageExportResult, PackageResult

from tests.harness import IsolatedCase

_MOMENT = dt.datetime(2026, 10, 8, 21, 0, 0)


class PureTests(unittest.TestCase):
    """不碰数据库的纯函数。"""

    def test_clean_ids_drops_noise_and_keeps_order(self) -> None:
        self.assertEqual(_clean_ids([3, "1", 3, 0, None, "x", "2"]), [3, 1, 2])
        self.assertEqual(_clean_ids(None), [])

    def test_to_ref_maps_every_field(self) -> None:
        result = PackageExportResult(
            directory=Path("out"),
            packages=(
                PackageResult(label="影视", path=Path("out/影视.zip"), exported=2, missing=1, warnings=("w",)),
            ),
            warnings=("影视.zip：w",),
        )
        ref = to_ref(result)
        self.assertEqual(ref.directory, str(Path("out")))
        self.assertEqual(len(ref.packages), 1)
        row = ref.packages[0]
        self.assertEqual(row.label, "影视")
        self.assertEqual(row.path, str(Path("out/影视.zip")))
        self.assertEqual(row.exported, 2)
        self.assertEqual(row.missing, 1)
        self.assertEqual(row.warnings, ("w",))
        self.assertEqual(ref.warnings, ("影视.zip：w",))
        self.assertEqual(ref.exported, 2)
        self.assertEqual(ref.missing, 1)
        self.assertTrue(ref.finished)
        self.assertEqual(ref.paths, (str(Path("out/影视.zip")),))
        self.assertTrue(ref.summary())

    def test_ref_summary_of_empty_result(self) -> None:
        ref = export_sdk.ExportRef()
        self.assertIn("没有要导出的数据项", ref.summary())


class FakeDialog:
    """替掉 `ExportConfirmDialog`：记下被问到的内容，按脚本回答。"""

    accepted = True
    chosen = ""

    instances: list["FakeDialog"] = []

    def __init__(self, parent=None, **kwargs) -> None:
        self.parent = parent
        self.kwargs = kwargs
        FakeDialog.instances.append(self)
        self._dir = kwargs.get("directory", "")

    def exec(self) -> bool:
        return bool(FakeDialog.accepted)

    def directory(self) -> str:
        return FakeDialog.chosen or self._dir

    def deleteLater(self) -> None:
        pass


class ExportApiCase(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.api: ExportApi = export_api.api()
        self.library = self.default_library()
        self.user = self.current_user()
        self._temp = tempfile.TemporaryDirectory(prefix="dm_export_api_")
        self.out = Path(self._temp.name)
        self._previous = extension_registry.provider(export_sdk.EXPORT_EXTENSION)
        self._previous_owner = extension_registry.provider_plugin(export_sdk.EXPORT_EXTENSION)
        extension_registry.provide(export_sdk.EXPORT_EXTENSION, self.api, "selfcheck")
        FakeDialog.accepted = True
        FakeDialog.chosen = ""
        FakeDialog.instances = []

    def tearDown(self) -> None:
        extension_registry.provide(export_sdk.EXPORT_EXTENSION, self._previous, self._previous_owner)
        self._temp.cleanup()
        super().tearDown()

    # ------------------------------------------------------------------ 便捷
    def _import(self, name: str, *, category: str = ""):
        category_id = None
        if category:
            category_id = self._make_category(category)
        item = self.importer(library=self.library).import_text(
            name, f"{name} 的正文", user_id=self.user.id, category_id=category_id
        )
        # 接口层每次调用自建会话（插件在工作线程里调用就是这个场景），所以这里必须真提交
        self.session.commit()
        return item

    def _make_category(self, path: str) -> int:
        from app.db.models import Category

        parent = None
        for part in path.split("/"):
            node = Category(name=part, parent_id=parent, user_id=self.user.id)
            self.session.add(node)
            self.session.flush()
            parent = node.id
        self.session.commit()
        return parent

    # ------------------------------------------------------------------ 设置
    def test_directory_follows_config(self) -> None:
        self.assertEqual(self.api.directory(), str(export_dir()))

    def test_choose_directory_delegates_to_dialog(self) -> None:
        with mock.patch(
            "app.ui.components.export_dialog.choose_export_directory", return_value="X:/选中的"
        ) as chosen:
            self.assertEqual(self.api.choose_directory("D:/起点"), "X:/选中的")
        self.assertEqual(chosen.call_args.args[1], "D:/起点")

    # ------------------------------------------------------------------ 计划
    def test_plan_single_counts_without_writing(self) -> None:
        items = [self._import("甲"), self._import("乙")]
        plan = self.api.plan([item.id for item in items], directory=str(self.out), now=_MOMENT)
        self.assertEqual(plan.mode, "single")
        self.assertEqual(len(plan.packages), 1)
        self.assertEqual(plan.packages[0].exported, 2)
        self.assertEqual(plan.packages[0].path, str(self.out / "导出-2026-10-08-1.zip"))
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertIn("将要导出 1 个压缩包", plan.summary())

    def test_plan_top_splits_and_numbers(self) -> None:
        items = [self._import("甲", category="影视"), self._import("乙", category="音乐")]
        plan = self.api.plan(
            [item.id for item in items], mode="top", template="{number,3,2}-{category}",
            directory=str(self.out), now=_MOMENT,
        )
        self.assertEqual([Path(row.path).name for row in plan.packages], ["3-影视.zip", "5-音乐.zip"])
        self.assertEqual(plan.mode, "top")

    def test_plan_empty_selection(self) -> None:
        plan = self.api.plan([], directory=str(self.out))
        self.assertEqual(plan.packages, ())

    def test_plan_ignores_missing_and_deleted_ids(self) -> None:
        item = self._import("甲")
        plan = self.api.plan([item.id, 999999], directory=str(self.out), now=_MOMENT)
        self.assertEqual(plan.packages[0].exported, 1)

    # ------------------------------------------------------------------ 导出
    def test_packages_without_confirm_writes_files(self) -> None:
        items = [self._import("甲"), self._import("乙")]
        ref = self.api.packages(
            [item.id for item in items], directory=str(self.out), confirm=False
        )
        self.assertIsInstance(ref, export_sdk.ExportRef)
        self.assertEqual(ref.exported, 2)
        self.assertEqual(len(ref.packages), 1)
        self.assertTrue(Path(ref.packages[0].path).is_file())
        with zipfile.ZipFile(ref.packages[0].path) as archive:
            self.assertIn("甲.txt", archive.namelist())

    def test_packages_uses_default_directory_when_blank(self) -> None:
        item = self._import("甲")
        ref = self.api.packages([item.id], confirm=False)
        self.assertEqual(ref.directory, str(export_dir()))

    def test_packages_raises_on_empty_selection(self) -> None:
        with self.assertRaises(SdkError):
            self.api.packages([], directory=str(self.out))

    def test_confirm_cancel_returns_none_and_writes_nothing(self) -> None:
        item = self._import("甲")
        FakeDialog.accepted = False
        with mock.patch("app.ui.components.export_dialog.ExportConfirmDialog", FakeDialog):
            self.assertIsNone(self.api.packages([item.id], directory=str(self.out)))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_confirm_can_change_directory(self) -> None:
        item = self._import("甲")
        FakeDialog.chosen = str(self.out / "改过的")
        with mock.patch("app.ui.components.export_dialog.ExportConfirmDialog", FakeDialog):
            ref = self.api.packages([item.id], directory=str(self.out), message="来自插件")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.directory, str(self.out / "改过的"))
        self.assertTrue(Path(ref.packages[0].path).is_file())
        self.assertEqual(FakeDialog.instances[0].kwargs["summary"], "来自插件")

    def test_confirm_dialog_lists_package_names(self) -> None:
        items = [self._import("甲", category="影视"), self._import("乙", category="音乐")]
        with mock.patch("app.ui.components.export_dialog.ExportConfirmDialog", FakeDialog):
            self.api.packages(
                [item.id for item in items], directory=str(self.out), mode="top"
            )
        names = FakeDialog.instances[0].kwargs["names"]
        self.assertEqual(len(names), 2)
        self.assertTrue(all(name.endswith(".zip") for name in names))

    # ------------------------------------------------------------------ SDK 门面
    def test_sdk_available_and_directory(self) -> None:
        self.assertTrue(export_sdk.available())
        self.assertEqual(export_sdk.directory(), str(export_dir()))
        self.assertTrue(export_sdk.variables())

    def test_sdk_packages_round_trip(self) -> None:
        item = self._import("甲")
        ref = export_sdk.packages([item.id], directory=str(self.out), confirm=False)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.exported, 1)

    def test_sdk_plan_round_trip(self) -> None:
        item = self._import("甲")
        plan = export_sdk.plan([item.id], directory=str(self.out), now=_MOMENT)
        self.assertEqual(plan.total, 1)
        self.assertEqual([Path(name).name for name in plan.names], ["导出-2026-10-08-1.zip"])

    def test_sdk_reports_missing_extension(self) -> None:
        extension_registry.provide(export_sdk.EXPORT_EXTENSION, None, "")
        self.assertFalse(export_sdk.available())
        with self.assertRaises(SdkError):
            export_sdk.directory()
        with self.assertRaises(SdkError):
            export_sdk.packages([1], confirm=False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
