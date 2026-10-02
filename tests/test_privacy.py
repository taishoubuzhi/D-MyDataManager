"""资源文件夹保护（静态保护模型）与启动自愈的用例。

保护模型：程序启动时整场放行、退出时锁定，异常退出后靠启动自愈放行。
这些用例都在 `tests/_tmp/` 的隔离目录里跑，并用假的 `acl` 记录 icacls 调用，
不会真的改动真实资源文件夹的权限。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.core import acl, paths
from app.core.config import config, resources_root
from app.services.privacy_service import PrivacyService

from tests.harness import IsolatedCase, TMP_ROOT, TempDir


class AclCommandCase(unittest.TestCase):
    """icacls 命令行本身：锁 / 放行 / 路径不存在 / 失败原因。"""

    def setUp(self):
        self.temp = TempDir("acl")
        self.calls: list[list[str]] = []
        self.results: list[tuple[int, str]] = []
        self._run = acl.subprocess.run

    def tearDown(self):
        acl.subprocess.run = self._run
        self.temp.cleanup()

    def _fake_run(self, command, **_kwargs):
        self.calls.append(list(command))
        code, text = self.results.pop(0) if self.results else (0, "")
        return type("Result", (), {"returncode": code, "stdout": text, "stderr": ""})()

    def test_lock_closes_inheritance_and_denies_everyone(self):
        acl.subprocess.run = self._fake_run
        ok, message = acl.lock(self.temp.path)
        self.assertTrue(ok, message)
        self.assertEqual(
            self.calls,
            [["icacls", str(self.temp.path), "/inheritance:r", "/deny", "*S-1-1-0:(OI)(CI)(RX)"]],
        )

    def test_unlock_removes_deny_then_restores_inheritance(self):
        acl.subprocess.run = self._fake_run
        ok, message = acl.unlock(self.temp.path)
        self.assertTrue(ok, message)
        self.assertEqual(
            self.calls,
            [
                ["icacls", str(self.temp.path), "/remove:d", "*S-1-1-0"],
                ["icacls", str(self.temp.path), "/inheritance:e"],
            ],
        )

    def test_missing_path_is_skipped_without_calling_icacls(self):
        acl.subprocess.run = self._fake_run
        ok, message = acl.lock(self.temp.path / "没有这个目录")
        self.assertTrue(ok)
        self.assertEqual(message, "路径不存在，跳过")
        self.assertEqual(self.calls, [])

    def test_failure_returns_last_output_line(self):
        acl.subprocess.run = self._fake_run
        self.results = [(5, "已成功处理 0 个文件\n拒绝访问。")]
        ok, message = acl.lock(self.temp.path)
        self.assertFalse(ok)
        self.assertEqual(message, "拒绝访问。")


class PrivacyServiceCase(IsolatedCase):
    """服务层：目标目录、锁定/放行、会话、状态文案、缓存。"""

    def setUp(self):
        super().setUp()
        self.privacy = PrivacyService()
        self.unlocked: list[Path] = []
        self.locked: list[Path] = []
        self._lock, self._unlock, self._supported = acl.lock, acl.unlock, acl.is_supported
        acl.is_supported = lambda: True
        acl.lock = lambda path: (self.locked.append(Path(path)), (True, ""))[1]
        acl.unlock = lambda path: (self.unlocked.append(Path(path)), (True, ""))[1]

    def tearDown(self):
        acl.lock, acl.unlock, acl.is_supported = self._lock, self._unlock, self._supported
        super().tearDown()

    def _make_hidden_dir(self) -> Path:
        hidden = paths.DEFAULT_LIBRARY_DIR / "默认用户" / "影音资料" / paths.HIDDEN_DIR_NAME
        hidden.mkdir(parents=True, exist_ok=True)
        return hidden

    def test_targets_follow_config_switches(self):
        self.assertEqual(self.privacy.targets(), [])

        config.set(config.resourceProtected, True)
        self.assertEqual(self.privacy.targets(), [resources_root()])
        self.assertTrue(self.privacy.enabled())

        config.set(config.resourceProtected, False)
        config.set(config.hiddenProtected, True)
        hidden = self._make_hidden_dir()
        self.privacy.invalidate()
        self.assertEqual(self.privacy.targets(), [hidden])

        # 资源文件夹保护覆盖隐藏目录，两个开关同开时不再单独列举
        config.set(config.resourceProtected, True)
        self.assertEqual(self.privacy.targets(), [resources_root()])

    def test_lock_does_nothing_without_protection(self):
        count, message = self.privacy.lock()
        self.assertEqual((count, message), (0, "没有开启保护"))
        self.assertEqual(self.locked, [])

    def test_unlock_releases_standard_dirs_without_protection(self):
        # 用户关掉开关时设置已经是关的，所以放行不能依赖 targets()
        count, _ = self.privacy.unlock()
        self.assertGreaterEqual(count, 1)
        self.assertIn(resources_root(), self.unlocked)
        self.assertIn(paths.DATA_DIR, self.unlocked)

    def test_unlock_releases_hidden_dirs_too(self):
        self.privacy.invalidate()
        hidden = self._make_hidden_dir()
        self.privacy.unlock()
        self.assertIn(hidden, self.unlocked)

    def test_unsupported_platform_reports_reason(self):
        acl.is_supported = lambda: False
        config.set(config.resourceProtected, True)
        self.assertEqual(self.privacy.lock(), (0, "当前系统不支持 ACL 锁定"))
        self.assertEqual(self.privacy.unlock(), (0, "当前系统不支持 ACL 锁定"))
        self.assertEqual(self.locked, [])

    def test_lock_and_unlock_follow_targets(self):
        config.set(config.resourceProtected, True)
        self.assertEqual(self.privacy.lock(), (1, ""))
        self.assertEqual(self.locked, [resources_root()])
        self.privacy.unlock()
        self.assertIn(resources_root(), self.unlocked)
        self.assertIn(paths.DATA_DIR, self.unlocked)

    def test_begin_session_releases_even_when_switches_are_off(self):
        count, _ = self.privacy.begin_session()
        self.assertGreaterEqual(count, 1)
        self.assertIn(resources_root(), self.unlocked)
        self.assertTrue(self.privacy.in_session())

    def test_begin_session_releases_hidden_dirs_too(self):
        config.set(config.hiddenProtected, True)
        hidden = self._make_hidden_dir()
        self.privacy.begin_session()
        self.assertIn(hidden, self.unlocked)

    def test_end_session_locks_when_protection_is_on(self):
        config.set(config.resourceProtected, True)
        self.privacy.begin_session()
        count, message = self.privacy.end_session()
        self.assertEqual((count, message), (1, ""))
        self.assertIn(resources_root(), self.locked)
        self.assertFalse(self.privacy.in_session())

    def test_end_session_does_nothing_without_protection(self):
        self.privacy.begin_session()
        self.assertEqual(self.privacy.end_session(), (0, "没有开启保护"))

    def test_state_text_describes_scope(self):
        self.assertEqual(self.privacy.state_text(), "未开启保护")
        config.set(config.resourceProtected, True)
        self.assertIn("资源文件夹", self.privacy.state_text())
        config.set(config.resourceProtected, False)
        config.set(config.hiddenProtected, True)
        self.assertIn("隐藏目录", self.privacy.state_text())
        acl.is_supported = lambda: False
        self.assertIn("不支持", self.privacy.state_text())

    def test_hidden_dirs_cache_needs_invalidate(self):
        config.set(config.hiddenProtected, True)
        self.assertEqual(self.privacy.hidden_dirs(), [])
        hidden = self._make_hidden_dir()
        self.assertEqual(self.privacy.hidden_dirs(), [])  # 缓存未失效
        self.privacy.invalidate()
        self.assertEqual(self.privacy.hidden_dirs(), [hidden])

    def test_guard_does_not_touch_acl(self):
        config.set(config.resourceProtected, True)
        with self.privacy.guard():
            pass
        self.assertEqual((self.locked, self.unlocked), ([], []))


class PathsBootstrapCase(IsolatedCase):
    """paths：放行只做一次、建目录不致命。"""

    def setUp(self):
        super().setUp()
        self.unlocked: list[Path] = []
        self._unlock, self._supported, self._released = acl.unlock, acl.is_supported, paths._released
        acl.is_supported = lambda: True
        acl.unlock = lambda path: (self.unlocked.append(Path(path)), (True, ""))[1]
        paths._released = False

    def tearDown(self):
        acl.unlock, acl.is_supported = self._unlock, self._supported
        paths._released = self._released
        super().tearDown()

    def test_release_locked_root_unlocks_once_per_process(self):
        config.set(config.resourceProtected, True)
        paths.release_locked_root()
        first = list(self.unlocked)
        self.assertIn(resources_root(), first)
        paths.release_locked_root()
        self.assertEqual(self.unlocked, first)  # 第二次不再调用 icacls

    def test_release_locked_root_skips_when_protection_is_off(self):
        paths.release_locked_root()
        self.assertEqual(self.unlocked, [])
        self.assertFalse(paths._released)

    def test_make_dir_is_not_fatal_when_target_is_a_file(self):
        blocked = TMP_ROOT / "blocked-dir"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_text("占位", encoding="utf-8")
        try:
            self.assertEqual(paths.make_dir(blocked), blocked)
        finally:
            blocked.unlink(missing_ok=True)

    def test_ensure_dirs_builds_layout(self):
        paths.ensure_dirs()
        for folder in global_dirs():
            self.assertTrue(folder.is_dir(), folder)


def global_dirs() -> list[Path]:
    return [paths.DATA_DIR, paths.DEFAULT_LIBRARY_DIR, *paths.global_subdirs(paths.DEFAULT_LIBRARY_DIR)]


class SessionMarkerCase(IsolatedCase):
    """启动自愈：会话标记与退出锁定。"""

    def setUp(self):
        super().setUp()
        import main

        self.main = main
        self.unlocked: list[Path] = []
        self.locked: list[Path] = []
        self._lock, self._unlock, self._supported = acl.lock, acl.unlock, acl.is_supported
        acl.is_supported = lambda: True
        acl.lock = lambda path: (self.locked.append(Path(path)), (True, ""))[1]
        acl.unlock = lambda path: (self.unlocked.append(Path(path)), (True, ""))[1]
        self._dispose = main.dispose_engine
        main.dispose_engine = lambda: None
        main._locked = False

    def tearDown(self):
        acl.lock, acl.unlock, acl.is_supported = self._lock, self._unlock, self._supported
        self.main.dispose_engine = self._dispose
        self.main._locked = False
        super().tearDown()

    def test_marker_round_trip(self):
        self.main._write_session_marker()
        self.assertTrue(paths.SESSION_FILE.is_file())
        marker = self.main._previous_session_marker()
        self.assertEqual(marker["resource_protected"], bool(config.resourceProtected.value))
        self.main._clear_session_marker()
        self.assertEqual(self.main._previous_session_marker(), {})
        self.assertFalse(paths.SESSION_FILE.exists())

    def test_report_previous_session_clears_stale_marker(self):
        self.main._write_session_marker()
        self.main._report_previous_session()
        self.assertFalse(paths.SESSION_FILE.exists())
        self.main._report_previous_session()  # 没有标记时静默返回

    def test_lock_on_exit_locks_once_and_clears_marker(self):
        config.set(config.resourceProtected, True)
        self.main._write_session_marker()
        self.main._lock_on_exit()
        self.main._lock_on_exit()  # 幂等
        self.assertEqual(self.locked, [resources_root()])
        self.assertFalse(paths.SESSION_FILE.exists())

    def test_unlock_only_forces_release(self):
        config.set(config.resourceProtected, True)
        self.assertEqual(self.main._unlock_only(), 0)
        self.assertIn(resources_root(), self.unlocked)
        self.assertTrue(config.resourceProtected.value)  # 只是放行，不改设置

    def test_degrade_turns_switches_off_and_releases(self):
        config.set(config.resourceProtected, True)
        config.set(config.hiddenProtected, True)
        self.main._degrade_protection(RuntimeError("放行失败"))
        self.assertFalse(config.resourceProtected.value)
        self.assertFalse(config.hiddenProtected.value)
        self.assertIn(resources_root(), self.unlocked)


if __name__ == "__main__":
    unittest.main()
