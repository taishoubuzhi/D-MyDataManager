"""批量导入任务（临时清单）的用例：快照、逐项提交、暂停 / 继续 / 取消、崩溃恢复。

导入容错靠三件事：待导入清单先落盘、每个文件单独提交、恢复时按「已登记的认回来 →
库里的孤儿文件收养 → 其余重做」三步核对。这里逐条验证，外加「所有待导入项都到终态
（导入完或已取消）才删清单」这条规则。
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from app.core.journals import ITEM_CANCELLED, manifest_kit
from app.core.runtime import paths
from app.services.import_job import (
    STOP_CANCELLED,
    STOP_DONE,
    STOP_PAUSED,
    ImportControl,
    ImportPlanItem,
    create_job,
    import_store,
    open_journals,
    plan_folder,
    resume_job,
)

from tests.harness import IsolatedCase


class ImportJournalCase(IsolatedCase):
    """清单驱动的批量导入。"""

    def setUp(self) -> None:
        super().setUp()
        self.library = self.default_library()
        self.service = self.importer(library=self.library)
        self._clean_journals()
        # 同一测试类共用一个临时根目录：源目录必须清空，否则别的用例写进去的文件会被
        # 快照用例一起拍下来（plan_folder 扫的就是整个目录）。
        self.sources = self.root / "sources"
        shutil.rmtree(self.sources, ignore_errors=True)
        self.sources.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 工具
    def _clean_journals(self) -> None:
        """清掉上一个用例留下的临时清单（同一测试类共用一个临时根目录）。"""
        shutil.rmtree(paths.CONFIG_DIR / "journals", ignore_errors=True)
        for manifest_id in list(manifest_kit.ids()):
            if manifest_id.startswith("core.journal."):
                manifest_kit.drop(manifest_id)

    def _write(self, name: str, text: str) -> Path:
        path = self.sources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _plan(self, *files: Path) -> list[ImportPlanItem]:
        return [
            ImportPlanItem(key=f"{index:06d}", source=str(path))
            for index, path in enumerate(files)
        ]

    def _library_paths(self) -> set[str]:
        return set(self.service.items.library_paths(self.library.id))

    def _exists(self, job) -> bool:
        return Path(job.journal.path).exists()

    # ------------------------------------------------------------------ 快照
    def test_plan_folder_is_a_sorted_snapshot_with_subdirs(self) -> None:
        self._write("a.txt", "甲")
        self._write("sub/b.txt", "乙")
        (self.sources / "__pycache__").mkdir(exist_ok=True)
        (self.sources / "__pycache__" / "c.pyc").write_bytes(b"x")
        (self.sources / ".隐藏").write_text("h", encoding="utf-8")

        items = plan_folder(self.sources)

        self.assertEqual([Path(item.source).name for item in items], ["a.txt", "b.txt"])
        self.assertEqual([item.subdir for item in items], ["", "sub"])
        self.assertEqual([item.key for item in items], ["000000", "000001"])

    # ------------------------------------------------------------------ 正常跑完
    def test_run_imports_every_file_then_deletes_the_journal(self) -> None:
        files = [self._write(f"run{i}.txt", f"内容{i}") for i in range(3)]
        job = create_job(self._plan(*files))
        self.assertTrue(self._exists(job))

        result = job.run(self.service)

        self.assertEqual((result.added, result.remaining, result.stopped), (3, 0, STOP_DONE))
        self.assertEqual(len(self._library_paths()), 3)
        # 待导入项都为空的清单按规则删除（文件与登记一起走）
        self.assertFalse(self._exists(job))
        self.assertNotIn(job.id, manifest_kit.ids())

    # ------------------------------------------------------------------ 暂停 / 继续
    def test_pause_keeps_the_journal_and_resume_finishes_it(self) -> None:
        files = [self._write(f"pause{i}.txt", f"内容{i}") for i in range(3)]
        job = create_job(self._plan(*files))
        control = ImportControl()
        control.request_pause()

        paused = job.run(self.service, control=control)

        self.assertEqual((paused.added, paused.remaining, paused.stopped), (0, 3, STOP_PAUSED))
        self.assertTrue(self._exists(job))
        self.assertEqual(self._library_paths(), set())

        store = import_store()
        resumed = resume_job(store.load(job.id), store=store)
        control.request_resume()
        second = resumed.run(self.service, control=control, adopt=True)

        self.assertEqual((second.added, second.remaining), (3, 0))
        self.assertEqual(len(self._library_paths()), 3)
        self.assertFalse(self._exists(job))

    # ------------------------------------------------------------------ 取消
    def test_cancel_one_item_only_skips_that_item(self) -> None:
        files = [self._write(f"one{i}.txt", f"内容{i}") for i in range(2)]
        job = create_job(self._plan(*files))
        self.assertTrue(job.cancel_item("000001"))

        result = job.run(self.service)

        self.assertEqual((result.added, result.cancelled, result.remaining), (1, 1, 0))
        self.assertFalse(self._exists(job))
        self.assertEqual(len(self._library_paths()), 1)

    def test_cancelling_the_batch_settles_despite_leaving_nothing_imported(self) -> None:
        files = [self._write(f"all{i}.txt", f"内容{i}") for i in range(2)]
        job = create_job(self._plan(*files))
        control = ImportControl()
        control.request_cancel()

        result = job.run(self.service, control=control)

        self.assertEqual(result.stopped, STOP_CANCELLED)
        self.assertEqual((result.added, result.cancelled), (0, 2))
        self.assertEqual(self._library_paths(), set())
        self.assertFalse(self._exists(job))

    def test_abandon_marks_the_rest_cancelled_and_deletes_the_journal(self) -> None:
        files = [self._write(f"give{i}.txt", f"内容{i}") for i in range(2)]
        job = create_job(self._plan(*files))

        job.abandon()

        self.assertFalse(self._exists(job))
        self.assertEqual(
            [str(item.get("status")) for item in job.journal.items],
            [ITEM_CANCELLED, ITEM_CANCELLED],
        )

    # ------------------------------------------------------------ 崩溃恢复
    def test_adopt_orphan_file_instead_of_copying_it_again(self) -> None:
        source = self._write("orphan.txt", "孤儿内容")
        item = self.service.import_file(str(source), user_id=self.current_user().id)
        self.assertIsNotNone(item)
        orphan_rel = item.file_path
        # 只回滚数据库：磁盘上的副本留下 —— 正好是「复制完还没提交」的崩溃现场
        self.session.rollback()
        self.assertEqual(self._library_paths(), set())

        job = create_job([ImportPlanItem(key="000000", source=str(source))])
        result = job.run(self.service, adopt=True)

        self.assertEqual((result.adopted, result.added, result.remaining), (1, 1, 0))
        # 收养的就是原来那一份，不会再复制出一个 `_1`
        self.assertEqual(self._library_paths(), {orphan_rel})

    def test_registered_item_is_recognised_on_resume(self) -> None:
        source = self._write("done.txt", "已经入库")
        self.service.import_file(str(source), user_id=self.current_user().id)
        self.session.commit()

        job = create_job([ImportPlanItem(key="000000", source=str(source))])
        result = job.run(self.service, adopt=True)

        self.assertEqual((result.adopted, result.added), (1, 1))
        self.assertEqual(len(self._library_paths()), 1)

    def test_pending_item_is_redone_on_resume(self) -> None:
        source = self._write("redo.txt", "还没轮到")

        job = create_job([ImportPlanItem(key="000000", source=str(source))])
        result = job.run(self.service, adopt=True)

        self.assertEqual((result.adopted, result.added), (0, 1))
        self.assertEqual(len(self._library_paths()), 1)

    def test_open_journals_reregisters_the_file_after_restart(self) -> None:
        files = [self._write(f"restart{i}.txt", f"内容{i}") for i in range(2)]
        job = create_job(self._plan(*files))
        control = ImportControl()
        control.request_pause()
        job.run(self.service, control=control)

        # 模拟重启：登记表只在内存里，进程一退就没了
        manifest_kit.drop(job.id)
        self.assertNotIn(job.id, manifest_kit.ids())

        leftovers = open_journals()

        self.assertEqual([journal.id for journal in leftovers], [job.id])
        self.assertIn(job.id, manifest_kit.ids())


if __name__ == "__main__":
    unittest.main()
