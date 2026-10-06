"""系统默认打开 / 定位文件的命令行与回退逻辑（`app.core.runtime.shell`）。

本机（Windows 11 + Store 版记事本这类 AppX 关联）实测：`os.startfile()` 走的本进程
`ShellExecuteEx` 对任何路径都返回 `[WinError 5] 拒绝访问`，让资源管理器代开才正常，
所以这里重点断言「被拒绝时回退到 `explorer.exe <路径>`」，以及参数模板拼出来的命令行。
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.runtime import shell

WINDOWS_ONLY = unittest.skipUnless(shell.WINDOWS, "仅在 Windows 上走 Explorer 回退")


class CommandCase(unittest.TestCase):
    """参数模板 + 文件路径拼出来的命令行。"""

    def test_build_command_appends_path_without_placeholder(self):
        target = Path("C:/tmp/甲.txt")
        self.assertEqual(shell.build_command("notepad.exe", "", target), ["notepad.exe", str(target)])

    def test_build_command_replaces_placeholder(self):
        target = Path("C:/tmp/甲 乙.txt")
        argv = shell.build_command("code", '--goto "{path}":1', target)
        # 引号由 shlex 吃掉：参数直接进 Popen，不经 shell，空格不用再转义
        self.assertEqual(argv, ["code", "--goto", f"{target}:1"])

    def test_split_args_survives_broken_quotes(self):
        self.assertEqual(shell.split_args('--flag "{path}"'), ["--flag", "{path}"])
        self.assertEqual(shell.split_args('a "b'), ["a", '"b'])


class OpenDefaultCase(unittest.TestCase):
    """`open_default()`：文件不存在直接 False；系统拒绝时回退资源管理器。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.target = Path(self._dir.name) / "甲.txt"
        self.target.write_text("内容", encoding="utf-8")

    def test_missing_target_returns_false(self):
        with mock.patch.object(shell.subprocess, "Popen") as popen:
            self.assertFalse(shell.open_default(Path(self._dir.name) / "没有这个文件.txt"))
        popen.assert_not_called()

    @WINDOWS_ONLY
    def test_startfile_success_does_not_spawn_explorer(self):
        with mock.patch.object(shell.os, "startfile") as startfile, mock.patch.object(
            shell.subprocess, "Popen"
        ) as popen:
            self.assertTrue(shell.open_default(self.target))
        startfile.assert_called_once_with(str(self.target))
        popen.assert_not_called()

    @WINDOWS_ONLY
    def test_startfile_denied_falls_back_to_explorer(self):
        denied = PermissionError(13, "拒绝访问。")
        denied.winerror = 5  # type: ignore[attr-defined]
        explorer = mock.Mock()
        explorer.wait.return_value = shell.EXPLORER_DELIVERED  # 退 1 = 请求交出去了
        with mock.patch.object(
            shell.os, "startfile", side_effect=denied
        ), mock.patch.object(shell.subprocess, "Popen", return_value=explorer) as popen:
            self.assertTrue(shell.open_default(self.target))
        popen.assert_called_once_with(["explorer", str(self.target)])

    @WINDOWS_ONLY
    def test_explorer_dll_init_failure_reports_false(self):
        """本机实测：代开进程起来就死在 0xC0000142（DLL 初始化失败），等于没打开。"""
        denied = PermissionError(13, "拒绝访问。")
        explorer = mock.Mock()
        explorer.wait.return_value = 3221225794  # 0xC0000142
        with mock.patch.object(
            shell.os, "startfile", side_effect=denied
        ), mock.patch.object(shell.subprocess, "Popen", return_value=explorer):
            self.assertFalse(shell.open_default(self.target))

    @WINDOWS_ONLY
    def test_explorer_still_running_reports_false(self):
        denied = PermissionError(13, "拒绝访问。")
        explorer = mock.Mock()
        explorer.wait.side_effect = subprocess.TimeoutExpired("explorer", 1.0)
        with mock.patch.object(
            shell.os, "startfile", side_effect=denied
        ), mock.patch.object(shell.subprocess, "Popen", return_value=explorer):
            self.assertFalse(shell.open_default(self.target))

    @WINDOWS_ONLY
    def test_explorer_failure_reports_false(self):
        denied = PermissionError(13, "拒绝访问。")
        with mock.patch.object(
            shell.os, "startfile", side_effect=denied
        ), mock.patch.object(shell.subprocess, "Popen", side_effect=OSError("no explorer")):
            self.assertFalse(shell.open_default(self.target))


class ProcessHintCase(unittest.TestCase):
    """排查用的进程信息：非 Windows 一律「未提权、会话 0」，Windows 上按系统调用结果取。"""

    def test_off_windows_reports_defaults(self):
        with mock.patch.object(shell, "WINDOWS", False):
            self.assertFalse(shell.is_elevated())
            self.assertEqual(shell.session_id(), 0)
            self.assertEqual(shell._process_hint(), "未提权、会话 0")

    @WINDOWS_ONLY
    def test_windows_reads_shell_and_session(self):
        import ctypes

        with mock.patch.object(
            ctypes.windll.shell32, "IsUserAnAdmin", return_value=1
        ), mock.patch.object(ctypes.windll.kernel32, "ProcessIdToSessionId", return_value=1):
            self.assertTrue(shell.is_elevated())
            self.assertIsInstance(shell.session_id(), int)
            self.assertIn("已提权", shell._process_hint())


if __name__ == "__main__":
    unittest.main()
