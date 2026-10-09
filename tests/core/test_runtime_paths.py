"""检出根判定：打包在用户机上按 tag 克隆仓库，也必须找到正确的根目录。

回归的是「打包版内置插件一个都不出现、导入后还被标成外部」那条问题：
旧判定依赖 CLAUDE.md / TODO.md，而这两个文件都不在版本库里。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app.core.runtime import paths
from tmpenv import tests_tmp

#: 临时目录由 scripts/tmpenv.py 统一管理（进程退出时整体删除），测试内不再自行清理
_TMP = tests_tmp("runtime-paths")
#: 真实检出根：此文件位于 <根>/src/app/core/runtime/paths.py
_CHECKOUT_ROOT = Path(paths.__file__).resolve().parents[4]


class ProjectRootCase(unittest.TestCase):
    def setUp(self):
        self.tmp = _TMP / self._testMethodName
        self.tmp.mkdir(parents=True, exist_ok=True)

    def _make_checkout(self, *, with_requirements: bool = True) -> Path:
        """造一棵「打包后的检出」：有 src/main.py 与 requirements.txt，没有开发机标记文件。"""
        (self.tmp / "src" / "app" / "core" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.tmp / "src" / "main.py").write_text("", encoding="utf-8")
        if with_requirements:
            (self.tmp / "requirements.txt").write_text("", encoding="utf-8")
        (self.tmp / "plugins" / "builtin.lib.ui").mkdir(parents=True, exist_ok=True)
        return self.tmp / "src" / "app" / "core" / "runtime" / "paths.py"

    def test_checkout_without_marker_files_is_recognised(self):
        probe = self._make_checkout()
        self.assertTrue(paths.looks_like_project_root(self.tmp))
        self.assertEqual(paths._find_project_root(probe), self.tmp)

    def test_missing_requirements_is_not_a_project_root(self):
        probe = self._make_checkout(with_requirements=False)
        self.assertFalse(paths.looks_like_project_root(self.tmp))
        self.assertNotEqual(paths._find_project_root(probe), self.tmp)

    def test_fallback_reaches_checkout_root_when_nothing_matches(self):
        probe = self._make_checkout()
        with mock.patch.object(paths, "looks_like_project_root", return_value=False):
            self.assertEqual(paths._find_project_root(probe), _CHECKOUT_ROOT)

    def test_plugin_dir_sits_under_project_root(self):
        # 真实加载路径下：判定必须认得出检出根，插件目录随之正确（内置插件随 git 克隆自带）。
        # 注意查函数而不是 paths.ROOT —— 测试进程里 tmpenv.redirect_paths() 会把 paths.ROOT
        # 指向临时根，直接断言全局会随导入顺序时好时坏。
        self.assertTrue(paths.looks_like_project_root(_CHECKOUT_ROOT))
        self.assertEqual(paths._find_project_root(Path(paths.__file__).resolve()), _CHECKOUT_ROOT)
        self.assertTrue((_CHECKOUT_ROOT / "plugins" / "builtin.lib.ui" / "plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
