"""批量改名规则与关键词批量增减的用例：四种改名方式、重名序号、扩展名保护。"""

from __future__ import annotations

import unittest

from app.core.naming import (
    NUMBER_STYLE_KEYS,
    RENAME_MODE_KEYS,
    RenameRule,
    alpha_index,
    build_plan,
    number_text,
    split_suffix,
)
from app.services import ItemService

from tests.harness import IsolatedCase

THREE = ["abc.md", "bdh.md", "kjc.md"]


def plan(names: list[str], rule: RenameRule, selected: list[bool] | None = None, reserved=None):
    return build_plan(names, rule, selected if selected is not None else [True] * len(names), reserved or set())


class NamingRuleCase(unittest.TestCase):
    """纯文本规则，不碰数据库。"""

    def test_modes_are_listed_for_the_dialog(self):
        self.assertEqual(RENAME_MODE_KEYS, ("replace", "overwrite", "insert", "delete"))
        self.assertEqual(NUMBER_STYLE_KEYS, ("number", "lower", "upper"))

    def test_split_suffix_keeps_dotfile_whole(self):
        self.assertEqual(split_suffix("abc.md"), ("abc", ".md"))
        self.assertEqual(split_suffix(".gitignore"), (".gitignore", ""))
        self.assertEqual(split_suffix("没有后缀"), ("没有后缀", ""))

    def test_numbers(self):
        self.assertEqual(number_text(1, "number", 3), "001")
        self.assertEqual(number_text(7), "7")
        self.assertEqual(number_text(1, "lower"), "a")
        self.assertEqual(number_text(1, "upper"), "A")
        self.assertEqual(alpha_index(26), "z")
        self.assertEqual(alpha_index(27), "aa")
        self.assertEqual(alpha_index(27, upper=True), "AA")

    def test_replace_substring(self):
        rule = RenameRule(mode="replace", find="b", replace="j")
        self.assertEqual(plan(THREE, rule), ["ajc.md", "jdh.md", "kjc.md"])

    def test_replace_into_empty_deletes_the_text(self):
        rule = RenameRule(mode="replace", find="b", replace="")
        self.assertEqual(plan(["abc.md"], rule), ["ac.md"])

    def test_overwrite_with_numbers(self):
        rule = RenameRule(mode="overwrite", base="照片", start=1, width=2)
        self.assertEqual(plan(THREE, rule), ["照片01.md", "照片02.md", "照片03.md"])

    def test_overwrite_with_letters(self):
        rule = RenameRule(mode="overwrite", base="图", style="lower")
        self.assertEqual(plan(["图1.txt", "图2.txt", "图3.txt"], rule), ["图a.txt", "图b.txt", "图c.txt"])

    def test_overwrite_without_numbering(self):
        rule = RenameRule(mode="overwrite", base="同名", numbered=False)
        self.assertEqual(plan(THREE, rule), ["同名.md", "同名-1.md", "同名-2.md"])

    def test_reserved_names_are_taken_into_account(self):
        rule = RenameRule(mode="overwrite", base="同名", numbered=False)
        self.assertEqual(plan(["a.md"], rule, reserved={"同名.md"}), ["同名-1.md"])

    def test_unselected_rows_keep_their_name(self):
        rule = RenameRule(mode="overwrite", base="新", numbered=False)
        self.assertEqual(plan(THREE, rule, [True, False, True]), ["新.md", "bdh.md", "新-1.md"])

    def test_insert_at_position(self):
        rule = RenameRule(mode="insert", text="_", position=2)
        self.assertEqual(plan(["abc.md"], rule), ["a_bc.md"])
        self.assertEqual(plan(["abc.md"], RenameRule(mode="insert", text="新-", position=1)), ["新-abc.md"])

    def test_insert_beyond_the_name_appends(self):
        rule = RenameRule(mode="insert", text="尾", position=99)
        self.assertEqual(plan(["abc.md"], rule), ["abc尾.md"])

    def test_delete_at_position(self):
        rule = RenameRule(mode="delete", position=2, length=1)
        self.assertEqual(plan(THREE, rule), ["ac.md", "bh.md", "kc.md"])

    def test_delete_out_of_range_skips(self):
        rule = RenameRule(mode="delete", position=99, length=1)
        self.assertEqual(plan(["abc.md"], rule), ["abc.md"])

    def test_empty_parameters_leave_the_name_alone(self):
        for rule in (
            RenameRule(mode="overwrite"),
            RenameRule(mode="insert"),
            RenameRule(mode="replace", find=""),
            RenameRule(mode="overwrite", base="   "),
        ):
            self.assertEqual(plan(["abc.md"], rule), ["abc.md"])

    def test_extension_stays_at_the_end(self):
        rule = RenameRule(mode="overwrite", base="长名字", numbered=False)
        self.assertEqual(plan(["abc.md"], rule), ["长名字.md"])
        # 只认最后一段后缀（和库内改名一致）：abc.tar.gz → 长名字.gz，清单里会照实显示。
        self.assertEqual(plan(["abc.tar.gz"], rule), ["长名字.gz"])

    def test_blank_base_is_treated_as_empty(self):
        self.assertEqual(plan(["abc.md"], RenameRule(mode="overwrite", base="   ")), ["abc.md"])


class KeywordBatchCase(IsolatedCase):
    """关键词的批量增减：合并去重、原地剔除、不改动时不写库。"""

    def setUp(self):
        super().setUp()
        self.library = self.default_library()
        self.items = ItemService(self.session)

    def _item(self, name: str, keywords=None):
        item = self.importer(library=self.library).import_text(
            name, f"{name} 的正文", user_id=self.current_user().id, keywords=list(keywords or [])
        )
        self.session.flush()
        return item

    def test_add_keywords_merges_without_duplicates(self):
        first = self._item("甲", ["共同"])
        second = self._item("乙", [])
        changed = self.items.add_keywords([first, second], ["共同", "新词", "  ", "新词"])
        self.session.flush()
        self.assertEqual(changed, 2)
        self.assertEqual(first.keywords, ["共同", "新词"])
        # 新增的词会加到所选数据的每一条上，已有的不重复。
        self.assertEqual(second.keywords, ["共同", "新词"])

    def test_remove_keywords_only_drops_listed_words(self):
        first = self._item("甲", ["共同", "只甲"])
        changed = self.items.remove_keywords([first], ["只甲", "没有的"])
        self.session.flush()
        self.assertEqual(changed, 1)
        self.assertEqual(first.keywords, ["共同"])

    def test_nothing_to_do_returns_zero(self):
        first = self._item("甲", ["共同"])
        self.assertEqual(self.items.add_keywords([first], ["共同"]), 0)
        self.assertEqual(self.items.remove_keywords([first], ["没有的"]), 0)
        self.assertEqual(self.items.add_keywords([], ["新词"]), 0)
        self.assertEqual(self.items.remove_keywords([first], []), 0)
