"""镜像规则的单测：纯字符串推算，不碰网络与界面。"""

from __future__ import annotations

import unittest

from app.core.download.mirrors import (
    DEFAULT_RULE_ID,
    MODE_MIRROR,
    MODE_SEQUENTIAL,
    MirrorRule,
    MirrorRules,
    default_rules,
    host_of,
)

_HF = "https://huggingface.co/bert-base/resolve/main/config.json"
_HF_MIRROR = "https://hf-mirror.com/bert-base/resolve/main/config.json"
_GH = "https://github.com/owner/repo/releases/download/v1/model.bin"


class HostTests(unittest.TestCase):
    def test_host_of(self) -> None:
        self.assertEqual(host_of(_HF), "huggingface.co")
        self.assertEqual(host_of("随便一段字"), "随便一段字")
        self.assertEqual(host_of(""), "")


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = default_rules()

    def test_sequential_puts_official_first(self) -> None:
        self.assertEqual(self.rules.candidates(_HF), [_HF, _HF_MIRROR])

    def test_mirror_mode_skips_official(self) -> None:
        rules = MirrorRules(
            rules=(
                MirrorRule(
                    id="hf",
                    pattern="huggingface.co",
                    official="https://huggingface.co",
                    mirrors=("https://hf-mirror.com",),
                    mode=MODE_MIRROR,
                ),
            )
        )
        self.assertEqual(rules.candidates(_HF), [_HF_MIRROR])

    def test_github_uses_url_placeholder(self) -> None:
        self.assertEqual(
            self.rules.candidates(_GH),
            [
                _GH,
                f"https://ghproxy.net/{_GH}",
                f"https://gh-proxy.com/{_GH}",
                f"https://ghfast.top/{_GH}",
            ],
        )

    def test_subdomain_hits_the_domain_rule(self) -> None:
        url = "https://cdn-lfs.huggingface.co/repo/resolve/main/w.bin"
        self.assertEqual(self.rules.match(url).id, "huggingface")
        self.assertEqual(
            self.rules.candidates(url),
            [url, f"https://hf-mirror.com/repo/resolve/main/w.bin"],
        )

    def test_raw_githubusercontent_without_prefix_swap_keeps_official(self) -> None:
        url = "https://raw.githubusercontent.com/owner/repo/main/a.py"
        self.assertEqual(self.rules.match(url).id, "github")
        candidates = self.rules.candidates(url)
        self.assertEqual(candidates[0], url)
        self.assertEqual(candidates[1], f"https://ghproxy.net/{url}")

    def test_unknown_host_falls_back_to_default_rule(self) -> None:
        url = "https://example.com/a.bin"
        rule = self.rules.match(url)
        self.assertEqual(rule.id, DEFAULT_RULE_ID)
        self.assertEqual(self.rules.candidates(url), [url])

    def test_empty_url_has_no_candidates(self) -> None:
        self.assertEqual(self.rules.candidates(""), [])
        self.assertEqual(self.rules.candidates("   "), [])

    def test_mirror_mode_without_rewritable_mirror_returns_nothing(self) -> None:
        rules = MirrorRules(
            rules=(
                MirrorRule(
                    id="hf",
                    pattern="example.com",
                    official="https://huggingface.co",
                    mirrors=("https://hf-mirror.com",),
                    mode=MODE_MIRROR,
                ),
            )
        )
        # 命中了规则，但镜像项既不是 {url} 写法、官方前缀也对不上、主机也不是官方主机
        # 或它的子域，所以一条镜像都拼不出来——镜像模式下就该报「没有可用的下载地址」
        self.assertEqual(rules.candidates("https://cdn.example.com/a.bin"), [])

    def test_first_matching_rule_wins(self) -> None:
        rules = MirrorRules(
            rules=(
                MirrorRule(
                    id="special",
                    pattern="huggingface.co",
                    official="https://huggingface.co",
                    mirrors=("https://first.example",),
                ),
                MirrorRule(
                    id="general",
                    pattern="huggingface.co",
                    official="https://huggingface.co",
                    mirrors=("https://second.example",),
                ),
            )
        )
        self.assertEqual(rules.candidates(_HF), [_HF, "https://first.example/bert-base/resolve/main/config.json"])

    def test_disabled_rule_is_skipped(self) -> None:
        rules = default_rules().with_rule(
            MirrorRule(
                id="huggingface",
                title="HuggingFace",
                pattern="huggingface.co",
                official="https://huggingface.co",
                mirrors=("https://hf-mirror.com",),
                enabled=False,
            )
        )
        self.assertEqual(rules.candidates(_HF), [_HF])

    def test_full_url_pattern_with_wildcard(self) -> None:
        rules = MirrorRules(
            rules=(
                MirrorRule(
                    id="hf-api",
                    pattern="https://*/api/models/*",
                    official="https://huggingface.co/api/models/",
                    mirrors=("https://hf-mirror.com/api/models/",),
                ),
            )
        )
        url = "https://huggingface.co/api/models/bert"
        self.assertEqual(rules.candidates(url), [url, "https://hf-mirror.com/api/models/bert"])

    def test_explain_reports_rule_and_candidates(self) -> None:
        info = self.rules.explain(_HF)
        self.assertEqual(info["rule"], "huggingface")
        self.assertEqual(info["mode"], MODE_SEQUENTIAL)
        self.assertEqual(info["mode_label"], "顺序模式")
        self.assertEqual(info["candidates"], [_HF, _HF_MIRROR])


class StorageTests(unittest.TestCase):
    def test_round_trip_keeps_order(self) -> None:
        rules = default_rules()
        restored = MirrorRules.from_items(rules.as_items())
        self.assertEqual([rule.id for rule in restored.rules], ["huggingface", "github"])
        self.assertEqual(restored.candidates(_GH), rules.candidates(_GH))

    def test_from_items_accepts_key_and_string_mirrors(self) -> None:
        restored = MirrorRules.from_items(
            [
                {"key": "one", "pattern": "a.com", "official": "https://a.com", "mirrors": "https://m1"},
                {"key": "default", "title": "默认", "mode": "不存在的模式"},
            ]
        )
        self.assertEqual([rule.id for rule in restored.rules], ["one", "default"])
        self.assertEqual(restored.rules[0].mirrors, ("https://m1",))
        self.assertEqual(restored.rules[1].mode, MODE_SEQUENTIAL)
        self.assertTrue(restored.rules[1].is_default)

    def test_from_items_ignores_junk_rows(self) -> None:
        restored = MirrorRules.from_items(["不是字典", {}, {"key": ""}])
        self.assertEqual(restored.rules, ())

    def test_rule_dict_uses_key(self) -> None:
        row = default_rules().rules[0].as_dict()
        self.assertEqual(row["key"], "huggingface")
        self.assertNotIn("id", row)


class EditingTests(unittest.TestCase):
    def test_with_rule_replaces_in_place(self) -> None:
        rules = default_rules()
        updated = rules.with_rule(
            MirrorRule(
                id="huggingface",
                title="HuggingFace",
                pattern="huggingface.co",
                official="https://huggingface.co",
                mirrors=("https://other.example",),
            )
        )
        self.assertEqual([rule.id for rule in updated.rules], ["huggingface", "github"])
        self.assertEqual(updated.rules[0].mirrors, ("https://other.example",))

    def test_with_rule_appends_and_inserts(self) -> None:
        rules = default_rules()
        appended = rules.with_rule(MirrorRule(id="extra", pattern="x.com"))
        self.assertEqual([rule.id for rule in appended.rules], ["huggingface", "github", "extra"])
        inserted = rules.with_rule(MirrorRule(id="first", pattern="y.com"), index=0)
        self.assertEqual([rule.id for rule in inserted.rules], ["first", "huggingface", "github"])

    def test_without_rule(self) -> None:
        rules = default_rules().without_rule("github")
        self.assertEqual([rule.id for rule in rules.rules], ["huggingface"])

    def test_moved_changes_priority(self) -> None:
        rules = default_rules().moved("github", -1)
        self.assertEqual([rule.id for rule in rules.rules], ["github", "huggingface"])
        same = rules.moved("github", -1)
        self.assertEqual([rule.id for rule in same.rules], ["github", "huggingface"])
        unknown = rules.moved("missing", 1)
        self.assertEqual([rule.id for rule in unknown.rules], ["github", "huggingface"])

    def test_mirror_order_inside_a_rule_matters(self) -> None:
        rules = MirrorRules(
            rules=(
                MirrorRule(
                    id="github",
                    pattern="github.com",
                    official="https://github.com",
                    mirrors=("https://ghfast.top/{url}", "https://ghproxy.net/{url}"),
                ),
            )
        )
        self.assertEqual(
            rules.candidates(_GH),
            [_GH, f"https://ghfast.top/{_GH}", f"https://ghproxy.net/{_GH}"],
        )


if __name__ == "__main__":
    unittest.main()
