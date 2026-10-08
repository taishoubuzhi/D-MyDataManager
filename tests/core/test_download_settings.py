"""下载设置：镜像规则的清单读写，以及 QConfig ↔ 下载引擎之间的桥。

规则落在清单 `core.download_mirrors`（`.configs/download_mirrors.json`），标量落在
QConfig 的 `Download` 组；这组用例盯的就是这两条边的往返与兜底。
"""

from __future__ import annotations

import json
import unittest

from app.core.config import config, download_dir
from app.core.download import mirror_store
from app.core.download.engine import DownloadManager
from app.core.download.mirrors import (
    DEFAULT_MANIFEST_ID,
    MODE_LABELS,
    MODE_MIRROR,
    MODE_SEQUENTIAL,
    MODES,
    MirrorRule,
    MirrorRules,
    default_rules,
)
from app.core.download.settings import apply_options, default_target, options_from_config
from app.core.manifest import manifest_kit
from app.core.runtime import paths
from tests.harness import IsolatedCase


def _custom() -> MirrorRules:
    return MirrorRules(
        rules=(
            MirrorRule(
                id="hf",
                title="HuggingFace",
                pattern="huggingface.co",
                official="https://huggingface.co",
                mirrors=("https://hf-mirror.com",),
                mode=MODE_MIRROR,
            ),
            MirrorRule(
                id="gitee",
                title="Gitee 镜像",
                pattern="gitee.com",
                official="https://gitee.com",
                mirrors=("https://mirror.example.com/{url}",),
            ),
        )
    )


class MirrorStoreCase(IsolatedCase):
    """规则清单：还没写过、往返、坏文件、恢复默认。"""

    def setUp(self) -> None:
        super().setUp()
        self.path = mirror_store.manifest_path()
        self._forget()

    def tearDown(self) -> None:
        self._forget()
        super().tearDown()

    def _forget(self) -> None:
        """把规则清单恢复成「还没写过」的样子。"""
        self.path.unlink(missing_ok=True)
        if DEFAULT_MANIFEST_ID in manifest_kit.ids():
            manifest_kit.drop(DEFAULT_MANIFEST_ID)

    # ---------------------------------------------------------------- 默认
    def test_missing_file_gives_builtin_defaults(self) -> None:
        rules = mirror_store.load_rules()

        self.assertEqual([rule.id for rule in rules.rules], ["huggingface", "github"])
        # 只是读一下，不该把清单登记进登记表（登记表是内存里的，登记了就要有文件）
        self.assertNotIn(DEFAULT_MANIFEST_ID, manifest_kit.ids())

    def test_helpers_describe_the_modes(self) -> None:
        self.assertEqual(mirror_store.mode_options(), [(mode, MODE_LABELS[mode]) for mode in MODES])
        self.assertEqual(mirror_store.placeholder(), "{url}")
        self.assertIn("{url}", mirror_store.rule_hint())
        self.assertIn("子域", mirror_store.rule_hint())

    # ---------------------------------------------------------------- 往返
    def test_save_writes_registers_and_reads_back(self) -> None:
        saved = mirror_store.save_rules(_custom())

        self.assertTrue(self.path.is_file())
        self.assertIn(DEFAULT_MANIFEST_ID, manifest_kit.ids())
        self.assertEqual(saved.as_items(), _custom().as_items())

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["id"], DEFAULT_MANIFEST_ID)
        self.assertEqual(payload["kind"], mirror_store.MANIFEST_KIND)
        self.assertEqual([item["key"] for item in payload["items"]], ["hf", "gitee"])
        # 顺序就是优先级，落盘不能被重排
        self.assertEqual(mirror_store.load_rules().as_items(), _custom().as_items())

    def test_save_is_idempotent_about_registration(self) -> None:
        self.assertTrue(mirror_store.ensure_registered())
        self.assertFalse(mirror_store.ensure_registered())

        mirror_store.save_rules(_custom())
        self.assertEqual(mirror_store.load_rules().rules[0].mode, MODE_MIRROR)

    def test_replacing_rules_keeps_the_file_single(self) -> None:
        mirror_store.save_rules(_custom())
        mirror_store.save_rules(default_rules())

        self.assertEqual([rule.id for rule in mirror_store.load_rules().rules], ["huggingface", "github"])

    # ---------------------------------------------------------------- 兜底
    def test_broken_file_falls_back_to_defaults(self) -> None:
        mirror_store.save_rules(_custom())
        self.path.write_text("{ 坏掉的 JSON", encoding="utf-8")

        self.assertEqual(mirror_store.load_rules().as_items(), default_rules().as_items())

    def test_non_object_payload_falls_back_to_defaults(self) -> None:
        mirror_store.save_rules(_custom())
        self.path.write_text("[1, 2, 3]", encoding="utf-8")

        self.assertEqual(mirror_store.load_rules().as_items(), default_rules().as_items())

    def test_empty_items_mean_no_rule_matches(self) -> None:
        # 用户把规则全删了是合法状态：没有规则命中，地址就按原样使用
        mirror_store.save_rules(MirrorRules(rules=()))

        rules = mirror_store.load_rules()
        self.assertEqual(rules.rules, ())
        self.assertEqual(rules.candidates("https://example.com/a.bin"), ["https://example.com/a.bin"])

    def test_reset_goes_back_to_defaults(self) -> None:
        mirror_store.save_rules(_custom())

        rules = mirror_store.reset_rules()

        self.assertFalse(self.path.is_file())
        self.assertNotIn(DEFAULT_MANIFEST_ID, manifest_kit.ids())
        self.assertEqual(rules.as_items(), default_rules().as_items())


class SettingsCase(IsolatedCase):
    """配置 ↔ 引擎：标量跟 QConfig 走，规则跟清单走。"""

    def setUp(self) -> None:
        super().setUp()
        self._manager: DownloadManager | None = None
        # 规则文件可能被别的用例留下，这里保证从「还没写过」开始
        path = mirror_store.manifest_path()
        path.unlink(missing_ok=True)
        if DEFAULT_MANIFEST_ID in manifest_kit.ids():
            manifest_kit.drop(DEFAULT_MANIFEST_ID)

    def tearDown(self) -> None:
        if self._manager is not None:
            self._manager.shutdown(wait=1.0)
            self._manager = None
        super().tearDown()

    def test_options_follow_config(self) -> None:
        config.set(config.downloadConcurrent, 4)
        config.set(config.downloadSequential, False)
        config.set(config.downloadTimeout, 30)
        config.set(config.downloadRetries, 5)
        config.set(config.downloadProxy, "  http://127.0.0.1:1080  ")

        options = options_from_config()

        self.assertEqual(options.proxy, "http://127.0.0.1:1080")
        self.assertEqual(options.concurrent, 4)
        self.assertEqual(options.timeout, 30.0)
        self.assertEqual(options.retries, 5)
        self.assertEqual(options.limit, 4)
        self.assertFalse(options.sequential)
        self.assertEqual(options.mirrors.as_items(), default_rules().as_items())

    def test_sequential_mode_locks_the_limit_to_one(self) -> None:
        config.set(config.downloadConcurrent, 6)
        config.set(config.downloadSequential, True)

        options = options_from_config()

        self.assertEqual(options.concurrent, 6)  # 用户设的并行数留着
        self.assertEqual(options.limit, 1)  # 顺序模式下有效并发就是 1

    def test_apply_options_takes_effect_immediately(self) -> None:
        config.set(config.downloadConcurrent, 2)
        manager = DownloadManager(options_from_config())
        self._manager = manager
        self.assertEqual(manager.limit, 2)

        config.set(config.downloadConcurrent, 5)
        applied = apply_options(manager)

        self.assertEqual(applied.limit, 5)
        self.assertEqual(manager.limit, 5)

    def test_default_target_uses_the_configured_download_dir(self) -> None:
        self.assertEqual(download_dir(), paths.DEFAULT_DOWNLOAD_DIR)
        self.assertEqual(default_target("a.bin"), paths.DEFAULT_DOWNLOAD_DIR / "a.bin")
        # 只取文件名：下载名里带了路径也不能跑到下载目录外面去
        self.assertEqual(default_target("sub/dir/a.bin"), paths.DEFAULT_DOWNLOAD_DIR / "a.bin")

        config.set(config.downloadPath, "dldata")

        self.assertEqual(download_dir(), paths.ROOT / "dldata")
        self.assertEqual(default_target("a.bin"), paths.ROOT / "dldata" / "a.bin")

    def test_mirror_rules_round_trip_through_config(self) -> None:
        mirror_store.save_rules(_custom())
        try:
            options = options_from_config()
        finally:
            mirror_store.reset_rules()

        self.assertEqual([rule.id for rule in options.mirrors.rules], ["hf", "gitee"])
        self.assertEqual(options.mirrors.rules[0].mode, MODE_MIRROR)
        self.assertEqual(options.mirrors.rules[1].mode, MODE_SEQUENTIAL)


if __name__ == "__main__":
    unittest.main()
