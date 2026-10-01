"""维护操作的回归测试：恢复初始化会清空运行期数据并把设置重置为默认值。"""

from __future__ import annotations

from app.core import paths
from app.core.config import config
from app.db import database
from app.services import LibraryService, TaxonomyService, UserService
from app.services.maintenance import DEMO_DIR_NAME, reset_config, reset_runtime_data

from .harness import IsolatedCase


class MaintenanceCase(IsolatedCase):
    def test_reset_config_restores_defaults(self) -> None:
        config.set(config.coverSize, 512)
        config.set(config.nameByTime, True)
        config.set(config.currentUserId, 12345)
        config.set(config.theme, "dark")

        reset_config()

        for item in (config.coverSize, config.nameByTime, config.currentUserId, config.theme):
            self.assertEqual(item.value, item.defaultValue)

    def test_reset_runtime_data_clears_and_reseeds(self) -> None:
        library = self.default_library()
        leftover = paths.DEFAULT_LIBRARY_DIR / library.name
        leftover.mkdir(parents=True, exist_ok=True)
        (leftover / "残留.txt").write_text("x", encoding="utf-8")
        demo_dir = paths.DATA_DIR / DEMO_DIR_NAME
        demo_dir.mkdir(parents=True, exist_ok=True)
        (demo_dir / "demo.txt").write_text("x", encoding="utf-8")

        reset_runtime_data()

        self.assertFalse(demo_dir.exists())
        self.assertFalse((leftover / "残留.txt").exists())
        self.assertTrue(paths.DB_FILE.exists())
        session = database.new_session()
        try:
            self.assertIsNotNone(UserService(session).current())
            self.assertGreater(len(TaxonomyService(session).tree()), 0)
            self.assertIsNotNone(LibraryService(session).ensure_default())
        finally:
            session.close()
