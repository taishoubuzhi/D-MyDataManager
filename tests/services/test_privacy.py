"""资源文件夹保护（静态保护模型）与启动自愈的用例。

保护模型：程序启动时整场放行、退出时锁定，异常退出后靠启动自愈放行。
这些用例都在 `tests/.tmp/` 的隔离目录里跑，并用假的 `acl` 记录 icacls 调用，
不会真的改动真实资源文件夹的权限。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.core.runtime import acl, paths
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

    def test_lock_denies_everyone_without_touching_inheritance(self):
        # 只挂拒绝项、不关继承：/inheritance:r 会让放行时的 /inheritance:e 再传播一遍全子树
        acl.subprocess.run = self._fake_run
        ok, message = acl.lock(self.temp.path)
        self.assertTrue(ok, message)
        self.assertEqual(
            self.calls,
            [["icacls", str(self.temp.path), "/deny", "*S-1-1-0:(OI)(CI)(RX)"]],
        )

    def test_lock_shallow_only_denies_the_object_itself(self):
        # 资源根用浅拒绝：不带继承标志，拒绝项不会传播到十几万个文件上
        acl.subprocess.run = self._fake_run
        ok, message = acl.lock(self.temp.path, deep=False)
        self.assertTrue(ok, message)
        self.assertEqual(
            self.calls,
            [["icacls", str(self.temp.path), "/deny", "*S-1-1-0:(RX)"]],
        )

    def test_lock_file_never_gets_inherit_flags(self):
        acl.subprocess.run = self._fake_run
        target = self.temp.path / "data.db"
        target.write_text("x", encoding="utf-8")
        ok, message = acl.lock(target)
        self.assertTrue(ok, message)
        self.assertEqual(
            self.calls,
            [["icacls", str(target), "/deny", "*S-1-1-0:(RX)"]],
        )

    def test_lock_tree_denies_children_first_then_root_shallowly(self):
        # 根必须最后锁：拒绝项连遍历一起挡，先锁根就列不出子项（子目录会全漏掉）
        acl.subprocess.run = self._fake_run
        root = self.temp.path
        (root / "library").mkdir()
        (root / "models").mkdir()
        (root / "data.db").write_text("x", encoding="utf-8")
        count, message = acl.lock_tree(root, skip=("models",))
        self.assertEqual((count, message), (3, ""))
        self.assertEqual(
            self.calls,
            [
                ["icacls", str(root / "data.db"), "/deny", "*S-1-1-0:(RX)"],
                ["icacls", str(root / "library"), "/deny", "*S-1-1-0:(OI)(CI)(RX)"],
                ["icacls", str(root), "/deny", "*S-1-1-0:(RX)"],
            ],
        )

    def test_unlock_tree_releases_children_without_restoring_inheritance(self):
        # 子项只摘拒绝项：/inheritance:e 会在子项上再传播一遍（models/ 有十几万个文件）
        acl.subprocess.run = self._fake_run
        root = self.temp.path
        (root / "library").mkdir()
        (root / "data.db").write_text("x", encoding="utf-8")
        self.results = [(0, ""), (0, f"{root} Everyone:(I)(OI)(CI)(F)")]
        count, message = acl.unlock_tree(root)
        self.assertEqual((count, message), (3, ""))
        self.assertEqual(
            self.calls,
            [
                ["icacls", str(root), "/remove:d", "*S-1-1-0"],
                ["icacls", str(root)],
                ["icacls", str(root / "data.db"), "/remove:d", "*S-1-1-0"],
                ["icacls", str(root / "library"), "/remove:d", "*S-1-1-0"],
            ],
        )

    def test_unlock_tree_skips_unprotected_children(self):
        # models/ 从没被锁过：多执行一次 icacls 会重写它的 DACL，触发十几万对象的 ACE 传播
        acl.subprocess.run = self._fake_run
        root = self.temp.path
        (root / "library").mkdir()
        (root / "models").mkdir()
        self.results = [(0, ""), (0, f"{root} Everyone:(I)(OI)(CI)(F)")]
        count, message = acl.unlock_tree(root, skip=("models",))
        self.assertEqual((count, message), (2, ""))
        self.assertEqual(
            self.calls,
            [
                ["icacls", str(root), "/remove:d", "*S-1-1-0"],
                ["icacls", str(root)],
                ["icacls", str(root / "library"), "/remove:d", "*S-1-1-0"],
            ],
        )

    def test_icacls_gives_up_after_timeout(self):
        # 改 ACE 可能触发全子树传播：超时必须放弃，不能让启动路径无声挂住
        def _timeout(command, **_kwargs):
            self.calls.append(list(command))
            raise acl.subprocess.TimeoutExpired(command, 60)

        acl.subprocess.run = _timeout
        ok, message = acl.unlock(self.temp.path)
        self.assertFalse(ok)
        self.assertIn("超时", message)

    def test_unlock_removes_deny_then_restores_inheritance(self):
        acl.subprocess.run = self._fake_run
        ok, message = acl.unlock(self.temp.path)
        self.assertTrue(ok, message)
        self.assertEqual(
            self.calls,
            [
                ["icacls", str(self.temp.path), "/remove:d", "*S-1-1-0"],
                ["icacls", str(self.temp.path)],
                ["icacls", str(self.temp.path), "/inheritance:e"],
            ],
        )

    def test_unlock_skips_inheritance_when_already_inheriting(self):
        # 已经在继承时 /inheritance:e 是空操作，却要把整棵子树再传播一遍（十几万个文件）
        acl.subprocess.run = self._fake_run
        self.results = [(0, ""), (0, f"{self.temp.path} Everyone:(I)(OI)(CI)(RX)")]
        ok, message = acl.unlock(self.temp.path)
        self.assertTrue(ok, message)
        self.assertEqual(
            self.calls,
            [
                ["icacls", str(self.temp.path), "/remove:d", "*S-1-1-0"],
                ["icacls", str(self.temp.path)],
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
        self.released: list[Path] = []
        self.shallow: list[Path] = []
        self.order: list[str] = []
        self._lock, self._unlock, self._supported = acl.lock, acl.unlock, acl.is_supported
        self._remove_deny = acl.remove_deny
        acl.is_supported = lambda: True
        acl.lock = self._recorder("lock", self.locked)
        acl.unlock = self._recorder("unlock", self.unlocked)
        acl.remove_deny = self._recorder("remove_deny", self.released)

    def _recorder(self, kind: str, bucket: list[Path]):
        def call(path, **kwargs):
            bucket.append(Path(path))
            self.order.append(kind)
            if not kwargs.get("deep", True):
                self.shallow.append(Path(path))
            return True, ""

        return call

    def tearDown(self):
        acl.lock, acl.unlock, acl.is_supported = self._lock, self._unlock, self._supported
        acl.remove_deny = self._remove_deny
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

    def test_lock_skips_models_and_denies_children(self):
        # models/（运行环境 venv + 模型权重）不参与保护：浅层锁直接跳过，一个 ACE 都不碰
        config.set(config.resourceProtected, True)
        root = resources_root()
        models = root / "models"
        models.mkdir(parents=True, exist_ok=True)
        library = root / "library"
        library.mkdir(parents=True, exist_ok=True)
        self.privacy.lock()
        self.assertEqual(self.locked[-1], root)  # 根最后锁
        self.assertIn(library, self.locked)
        self.assertNotIn(models, self.locked)
        self.assertEqual(self.shallow, [root])  # 根是浅拒绝，只有子项带继承标志

    def test_hidden_only_lock_does_not_touch_the_resource_root(self):
        # 只保护隐藏目录时不碰资源根：它不是隐藏目录，也不在锁定路径上
        config.set(config.hiddenProtected, True)
        hidden = self._make_hidden_dir()
        self.privacy.invalidate()
        self.privacy.lock()
        self.assertEqual(self.locked, [hidden])

    def test_lock_and_unlock_follow_targets(self):
        config.set(config.resourceProtected, True)
        count, message = self.privacy.lock()
        self.assertEqual((count, message), (len(self.locked), ""))
        self.assertEqual(self.locked[-1], resources_root())  # 根最后锁
        self.privacy.unlock()
        self.assertIn(resources_root(), self.unlocked)
        self.assertIn(paths.DATA_DIR, self.unlocked)

    def test_begin_session_does_nothing_without_protection(self):
        # 开关全关时一次 icacls 都不能跑：白放行一次要给整棵 .resources 传播继承，启动会卡住
        count, message = self.privacy.begin_session()
        self.assertEqual((count, message), (0, "没有开启保护"))
        self.assertEqual(self.unlocked, [])
        self.assertTrue(self.privacy.in_session())

    def test_begin_session_releases_when_protection_is_on(self):
        config.set(config.resourceProtected, True)
        count, _ = self.privacy.begin_session()
        self.assertGreaterEqual(count, 1)
        self.assertIn(resources_root(), self.unlocked)

    def test_begin_session_releases_hidden_dirs_too(self):
        config.set(config.hiddenProtected, True)
        hidden = self._make_hidden_dir()
        self.privacy.begin_session()
        self.assertIn(hidden, self.unlocked)

    def test_end_session_locks_when_protection_is_on(self):
        config.set(config.resourceProtected, True)
        self.privacy.begin_session()
        count, message = self.privacy.end_session()
        self.assertEqual((count, message), (len(self.locked), ""))
        self.assertEqual(self.locked[-1], resources_root())  # 根最后锁
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
        self._remove_deny = acl.remove_deny
        acl.is_supported = lambda: True
        acl.unlock = lambda path: (self.unlocked.append(Path(path)), (True, ""))[1]
        acl.remove_deny = lambda path: (self.unlocked.append(Path(path)), (True, ""))[1]
        paths._released = False

    def tearDown(self):
        acl.unlock, acl.is_supported = self._unlock, self._supported
        acl.remove_deny = self._remove_deny
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
        self._remove_deny = acl.remove_deny
        acl.is_supported = lambda: True
        acl.lock = lambda path, **kwargs: (self.locked.append(Path(path)), (True, ""))[1]
        acl.unlock = lambda path: (self.unlocked.append(Path(path)), (True, ""))[1]
        acl.remove_deny = lambda path: (self.unlocked.append(Path(path)), (True, ""))[1]
        self._dispose = main.dispose_engine
        main.dispose_engine = lambda: None
        main._locked = False

    def tearDown(self):
        acl.lock, acl.unlock, acl.is_supported = self._lock, self._unlock, self._supported
        acl.remove_deny = self._remove_deny
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
        self.assertEqual(self.locked[-1], resources_root())  # 根最后锁
        first = list(self.locked)
        self.main._lock_on_exit()  # 幂等
        self.assertEqual(self.locked, first)
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
