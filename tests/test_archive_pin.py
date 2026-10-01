"""标记存档：已标记的快照不参与自动清理，只能取消标记或手动删除。"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import config
from app.db.models import Archive
from app.services import ArchiveService, LibraryService, UserService
from tests.harness import IsolatedCase


class ArchivePinCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.users = UserService(self.session)
        self.libraries = LibraryService(self.session)
        self.libraries.ensure_default()
        self.importer = self.importer()
        self.user = self.users.current()
        self.service = ArchiveService(self.session)
        for name in ("a", "b", "c"):
            self.importer.import_text(f"笔记 {name}", f"内容 {name}", user_id=self.user.id)
            self.session.commit()
            self.service.create(name)
            self.session.commit()
        self.newest, self.middle, self.oldest = self.service.history()

    def _names(self):
        return [archive.name for archive in self.service.history()]

    def test_pin_state_round_trips(self):
        self.assertFalse(self.oldest.pinned)
        self.assertTrue(self.service.set_pinned(self.oldest, True))
        self.session.commit()
        self.session.expire_all()
        self.assertTrue(self.session.get(Archive, self.oldest.id).pinned)

    def test_count_prune_skips_pinned(self):
        self.service.set_pinned(self.oldest, True)
        self.session.commit()
        self.assertEqual(self.service.prune(keep=1), 1)
        self.session.commit()
        self.assertEqual(sorted(self._names()), ["a", "c"])

    def test_unpin_lets_prune_delete_it(self):
        self.service.set_pinned(self.oldest, True)
        self.session.commit()
        self.service.prune(keep=1)
        self.session.commit()
        self.service.set_pinned(self.oldest, False)
        self.session.commit()
        self.assertEqual(self.service.prune(keep=1), 1)
        self.session.commit()
        self.assertEqual(self._names(), ["c"])

    def test_age_prune_skips_pinned(self):
        old = dt.datetime.now() - dt.timedelta(days=60)
        self.oldest.created_at = old
        self.middle.created_at = old
        self.service.set_pinned(self.oldest, True)
        self.session.commit()
        self.assertEqual(self.service.prune_by_age(30), 1)
        self.session.commit()
        self.assertEqual(sorted(self._names()), ["a", "c"])

    def test_size_prune_skips_pinned(self):
        self.service.set_pinned(self.oldest, True)
        self.session.commit()
        self.assertEqual(self.service.prune_by_size(0), 1)
        self.session.commit()
        self.assertEqual(sorted(self._names()), ["a", "c"])

    def test_manual_delete_removes_pinned_archive(self):
        self.service.set_pinned(self.oldest, True)
        self.session.commit()
        self.service.delete(self.oldest)
        self.session.commit()
        self.assertEqual(sorted(self._names()), ["b", "c"])

    def test_auto_prune_respects_pin(self):
        config.set(config.pruneMode, "count")
        config.set(config.keepVersions, 1)
        self.service.set_pinned(self.oldest, True)
        self.session.commit()
        removed, _freed = self.service.auto_prune()
        self.session.commit()
        self.assertEqual(removed, 1)
        self.assertEqual(sorted(self._names()), ["a", "c"])
