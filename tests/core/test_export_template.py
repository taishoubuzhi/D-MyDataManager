"""导出命名模板的单测：纯字符串，不碰界面、数据库与文件系统。"""

from __future__ import annotations

import datetime as dt
import unittest

from app.core.export.template import (
    DEFAULT_TEMPLATE,
    RenderContext,
    TemplateError,
    VARIABLES,
    alpha_name,
    alpha_ordinal,
    apply_numbering,
    from_roman,
    numbered_tokens,
    ordinal,
    parse_start,
    render,
    safe_filename,
    to_roman,
    token_names,
    unique_name,
    unknown_names,
)

_MOMENT = dt.datetime(2026, 10, 8, 20, 3, 5)


def _context(**changes) -> RenderContext:
    base = {"now": _MOMENT, "index": 1, "count": 3, "user": "默认用户", "creator": "甲用户"}
    base.update(changes)
    return RenderContext(**base)


class RomanTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        for value in (1, 4, 9, 14, 40, 90, 400, 1990, 2026, 3999):
            self.assertEqual(from_roman(to_roman(value)), value)

    def test_known_names(self) -> None:
        self.assertEqual(to_roman(4), "IV")
        self.assertEqual(to_roman(2026), "MMXXVI")

    def test_out_of_range(self) -> None:
        with self.assertRaises(TemplateError):
            to_roman(0)
        with self.assertRaises(TemplateError):
            to_roman(4000)

    def test_rejects_loose_spelling(self) -> None:
        with self.assertRaises(TemplateError):
            from_roman("IIII")
        with self.assertRaises(TemplateError):
            from_roman("")
        with self.assertRaises(TemplateError):
            from_roman("ABC")


class AlphaTests(unittest.TestCase):
    def test_ordinal_and_name(self) -> None:
        self.assertEqual(alpha_ordinal("a"), 1)
        self.assertEqual(alpha_ordinal("z"), 26)
        self.assertEqual(alpha_ordinal("aa"), 27)
        self.assertEqual(alpha_name(1), "a")
        self.assertEqual(alpha_name(26), "z")
        self.assertEqual(alpha_name(27), "aa")
        self.assertEqual(alpha_name(52), "az")
        self.assertEqual(alpha_name(53), "ba")

    def test_case_insensitive_start(self) -> None:
        self.assertEqual(alpha_ordinal("B"), 2)

    def test_bad_start(self) -> None:
        with self.assertRaises(TemplateError):
            alpha_ordinal("3")
        with self.assertRaises(TemplateError):
            alpha_ordinal("")


class OrdinalTests(unittest.TestCase):
    def test_defaults_are_omittable(self) -> None:
        """起点与间隔不写就是默认：{number} 等价于 {number,1,1}。"""
        self.assertEqual(parse_start("number", ""), 1)
        self.assertEqual(parse_start("alpha", ""), 1)
        self.assertEqual(parse_start("roman", ""), 1)
        self.assertEqual(ordinal("number", 1), "1")
        self.assertEqual(ordinal("alpha", 1), "a")
        self.assertEqual(ordinal("roman", 1), "i")

    def test_number_width(self) -> None:
        self.assertEqual(ordinal("number", 7, 3), "007")
        self.assertEqual(ordinal("number", 1234, 3), "1234")

    def test_uppercase_kinds(self) -> None:
        self.assertEqual(ordinal("ALPHA", 2), "B")
        self.assertEqual(ordinal("ROMAN", 4), "IV")


class RenderTests(unittest.TestCase):
    def test_creator_text_time(self) -> None:
        result = render("{creator}-自定义文本-{time}", _context())
        self.assertEqual(result.text, "甲用户-自定义文本-20-03-05")
        self.assertTrue(result.ok)

    def test_numbering_with_start_and_step(self) -> None:
        self.assertEqual(render("{number,3,2}", _context(index=1)).text, "3")
        self.assertEqual(render("{number,3,2}", _context(index=2)).text, "5")
        self.assertEqual(render("{number,3,2}", _context(index=3)).text, "7")
        self.assertEqual(render("{alpha,b,2}", _context(index=3)).text, "f")
        self.assertEqual(render("{roman,iv,2}", _context(index=2)).text, "vi")
        self.assertEqual(render("{ROMAN,IV,2}", _context(index=2)).text, "VI")

    def test_start_without_step(self) -> None:
        self.assertEqual(render("{number,5}", _context(index=3)).text, "7")
        self.assertEqual(render("{number,1,1,3}", _context(index=7)).text, "007")

    def test_text_variables(self) -> None:
        context = _context(category="影视", top="影视", name="示例数据")
        self.assertEqual(render("{user}/{creator}/{category}/{top}/{name}/{count}", context).text,
                         "默认用户/甲用户/影视/影视/示例数据/3")

    def test_chinese_aliases(self) -> None:
        self.assertEqual(render("{创建用户}-{年}{月}{日}", _context()).text, "甲用户-20261008")

    def test_time_variables_accept_format(self) -> None:
        self.assertEqual(render("{date}", _context()).text, "2026-10-08")
        self.assertEqual(render("{date,%Y%m%d}", _context()).text, "20261008")
        self.assertEqual(render("{datetime,%Y.%m.%d %H%M}", _context()).text, "2026.10.08 2003")
        self.assertEqual(render("{year}/{month}/{day}", _context()).text, "2026/10/08")

    def test_unknown_variable_kept_with_warning(self) -> None:
        result = render("前-{nope}-后", _context())
        self.assertEqual(result.text, "前-{nope}-后")
        self.assertEqual(result.warnings, ("认不出的变量：{nope}",))
        self.assertFalse(result.ok)

    def test_bad_step_warns_and_falls_back(self) -> None:
        result = render("{number,1,x}", _context(index=4))
        self.assertEqual(result.text, "4")
        self.assertTrue(any("间隔" in note for note in result.warnings))

    def test_zero_step_warns(self) -> None:
        result = render("{number,1,0}", _context(index=3))
        self.assertEqual(result.text, "3")
        self.assertTrue(any("间隔不能小于 1" in note for note in result.warnings))

    def test_bad_start_for_roman_warns(self) -> None:
        result = render("{roman,abc}", _context())
        self.assertTrue(any("罗马数字" in note for note in result.warnings))

    def test_text_variable_args_warn(self) -> None:
        result = render("{user,3}", _context())
        self.assertEqual(result.text, "默认用户")
        self.assertTrue(any("不接受参数" in note for note in result.warnings))

    def test_too_many_args_warn(self) -> None:
        result = render("{number,1,1,2,9}", _context())
        self.assertTrue(any("参数太多" in note for note in result.warnings))

    def test_user_name_lowercase(self) -> None:
        self.assertEqual(render("{User}", _context()).text, "默认用户")

    def test_token_and_unknown_names(self) -> None:
        template = "{creator}-{time}-{nope}-{creator}"
        self.assertEqual(unknown_names(template), ("nope",))
        self.assertEqual(token_names(template), ("creator", "time", "nope"))

    def test_default_template_renders(self) -> None:
        self.assertEqual(render(DEFAULT_TEMPLATE, _context()).text, "导出-2026-10-08-1")

    def test_variable_table_is_unique(self) -> None:
        names = [item.name for item in VARIABLES]
        self.assertEqual(len(names), len(set(names)))


class SafeNameTests(unittest.TestCase):
    def test_strips_illegal_characters(self) -> None:
        self.assertEqual(safe_filename('a<b>c:d"e/f\\g|h?i*j'), "abcdefghij")

    def test_collapses_spaces_and_trims_dots(self) -> None:
        self.assertEqual(safe_filename("  多个   空格.. "), "多个 空格")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(safe_filename("///"), "导出")
        self.assertEqual(safe_filename("", fallback="包"), "包")

    def test_reserved_device_names(self) -> None:
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("lpt1.zip"), "_lpt1.zip")

    def test_length_limit(self) -> None:
        self.assertEqual(len(safe_filename("字" * 400)), 150)

    def test_unique_name_numbers_collisions(self) -> None:
        used: set[str] = set()
        self.assertEqual(unique_name("包.zip", used), "包.zip")
        self.assertEqual(unique_name("包.zip", used), "包_1.zip")
        self.assertEqual(unique_name("包.zip", used), "包_2.zip")

    def test_unique_name_keeps_directories(self) -> None:
        used: set[str] = set()
        self.assertEqual(unique_name("a/清单.csv", used), "a/清单.csv")
        self.assertEqual(unique_name("a/清单.csv", used), "a/清单_1.csv")

    def test_unique_name_without_suffix(self) -> None:
        used: set[str] = set()
        self.assertEqual(unique_name("包", used), "包")
        self.assertEqual(unique_name("包", used), "包_1")


class NumberingTests(unittest.TestCase):
    """`numbered_tokens` / `apply_numbering`：界面上的起点与间隔控件写回模板原文。"""

    def test_numbered_tokens_only_coding_variables(self) -> None:
        names = [token.name for token in numbered_tokens("{date}-{number}-{alpha}-{时间}-{数字}")]
        self.assertEqual(names, ["number", "alpha", "数字"])

    def test_apply_numbering_writes_start_and_step(self) -> None:
        self.assertEqual(apply_numbering("导出-{number}", 3, 2), "导出-{number,3,2}")
        self.assertEqual(apply_numbering("{alpha}", 2, 3), "{alpha,2,3}")

    def test_apply_numbering_keeps_padding_width(self) -> None:
        self.assertEqual(apply_numbering("{number,1,1,3}", 5, 5), "{number,5,5,3}")
        self.assertEqual(apply_numbering("{number,1,1,3}-{number}", 2, 2), "{number,2,2,3}-{number,2,2}")

    def test_apply_numbering_normalizes_aliases(self) -> None:
        self.assertEqual(apply_numbering("{数字,2}", 4, 1), "{number,4,1}")
        self.assertEqual(apply_numbering("{大写罗马}", 2, 1), "{ROMAN,2,1}")

    def test_apply_numbering_leaves_other_tokens(self) -> None:
        text = "{date,%Y%m%d}-{user}-{没有这个} -{number}"
        self.assertEqual(
            apply_numbering(text, 3, 2), "{date,%Y%m%d}-{user}-{没有这个} -{number,3,2}"
        )

    def test_apply_numbering_clamps_arguments(self) -> None:
        self.assertEqual(apply_numbering("{number}", 0, 0), "{number,1,1}")

    def test_apply_numbering_result_still_renders(self) -> None:
        numbered = apply_numbering(DEFAULT_TEMPLATE, 3, 2)
        first = render(numbered, _context(index=1))
        second = render(numbered, _context(index=2))
        self.assertIn("3", first.text)
        self.assertIn("5", second.text)
        self.assertEqual(second.warnings, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
