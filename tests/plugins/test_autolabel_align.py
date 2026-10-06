"""自动标注共享库：数据类型 ↔ 模型对齐表（离线用例，不联网）。"""

from __future__ import annotations

import json
import sys
import types
import unittest
from dataclasses import replace
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "lib.autolabel"


def _register_plugin_namespace() -> None:
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.builtin.lib", None),
        ("dm_plugin.builtin.lib.ui", PLUGIN_DIR.parent / "builtin.lib.ui"),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.autolabel", PLUGIN_DIR),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder), str(Path(folder) / ".plugin")]
        sys.modules[name] = module


_register_plugin_namespace()

from dm_plugin.lib.autolabel.align import (  # noqa: E402
    DATATYPES,
    PURPOSE_KEYWORD,
    PURPOSE_LABEL,
    PURPOSE_LABELS,
    PURPOSES,
    AlignBook,
    AlignRow,
    AlignTable,
    dump_tables,
    normalize_datatype,
    parse_table,
    registered_map,
)

try:  # 界面工具库依赖 PyQt6：装不上就跳过 AlignRowsCase
    from dm_plugin.lib.autolabel.ui import controls as align_controls  # noqa: E402
except Exception:  # pragma: no cover - 取决于本机是否装了 PyQt6
    align_controls = None


def factory_tables() -> tuple[AlignTable, ...]:
    payload = json.loads((PLUGIN_DIR / ".data" / "align.json").read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = {}
    for item in payload.get("items") or []:
        row = {key: value for key, value in item.items() if key not in ("key", "purpose")}
        grouped.setdefault(str(item.get("purpose") or ""), []).append(row)
    tables = []
    for purpose in PURPOSES:
        rows, errors = parse_table(grouped.get(purpose, []), purpose=purpose)
        tables.append(AlignTable(purpose=purpose, rows=rows, errors=tuple(errors)))
    return tuple(tables)


class NormalizeCase(unittest.TestCase):
    def test_normalize_datatype(self) -> None:
        self.assertEqual(normalize_datatype("image"), "IMAGE")
        self.assertEqual(normalize_datatype(" Spreadsheet "), "SPREADSHEET")
        self.assertEqual(normalize_datatype(""), "OTHER")
        self.assertEqual(normalize_datatype(None), "OTHER")
        self.assertEqual(normalize_datatype("不认识"), "OTHER")


class AlignRowCase(unittest.TestCase):
    def test_resolved_prefers_explicit_model(self) -> None:
        row = AlignRow(datatype="IMAGE", model_id="local/explicit", template="tpl")
        self.assertEqual(row.resolved(registered={"tpl": "local/from-template"}), "local/explicit")

    def test_resolved_falls_back_to_template(self) -> None:
        row = AlignRow(datatype="IMAGE", template="tpl")
        self.assertEqual(row.resolved(registered={"tpl": "local/from-template"}), "local/from-template")
        self.assertEqual(row.resolved(registered={}), "")
        self.assertEqual(AlignRow(datatype="IMAGE").resolved(), "")

    def test_resolved_align_prefers_explicit_model(self) -> None:
        row = AlignRow(datatype="IMAGE", align_template="clip", align_model="local/clip")
        self.assertEqual(row.resolved_align(registered={"clip": "local/other"}), "local/clip")
        self.assertEqual(AlignRow(datatype="IMAGE", align_template="clip").resolved_align(registered={"clip": "c"}), "c")
        self.assertEqual(AlignRow(datatype="IMAGE").align_text, "不用")

    def test_problems_and_labels(self) -> None:
        self.assertEqual(AlignRow(datatype="IMAGE", template="t").problems, ())
        self.assertEqual(AlignRow(datatype="IMAGE", enabled=False).problems, ())
        self.assertEqual(AlignRow(datatype="IMAGE").problems, ("没有选模型",))
        self.assertTrue(any("未知的数据类型" in text for text in AlignRow(datatype="WEIRD").problems))
        self.assertEqual(AlignRow(datatype="IMAGE").datatype_label, "图片")

    def test_dict_round_trip(self) -> None:
        row = AlignRow(datatype="AUDIO", template="tpl", align_template="whisper", note="备注")
        self.assertEqual(AlignRow.from_dict(row.to_dict()), row)
        self.assertTrue(row.same_as(AlignRow.from_dict(row.to_dict())))
        self.assertIsNone(AlignRow.from_dict({"model_id": "x"}))
        self.assertIsNone(AlignRow.from_dict("nope"))


class ParseTableCase(unittest.TestCase):
    def test_rows_form(self) -> None:
        rows, errors = parse_table({"rows": [{"datatype": "image", "template": "t"}]}, purpose=PURPOSE_LABEL)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0].datatype, "IMAGE")

    def test_datatype_keyed_form(self) -> None:
        rows, errors = parse_table({"IMAGE": {"template": "t"}, "TEXT": {}}, purpose=PURPOSE_LABEL)
        self.assertEqual(errors, [])
        self.assertEqual([row.datatype for row in rows], ["IMAGE", "TEXT"])

    def test_broken_rows_are_reported(self) -> None:
        rows, errors = parse_table([{"template": "t"}, {"datatype": "IMAGE"}, {"datatype": "IMAGE"}], purpose=PURPOSE_LABEL)
        self.assertEqual([row.datatype for row in rows], ["IMAGE"])
        self.assertTrue(any("第 1 行缺少 datatype" in text for text in errors))
        self.assertTrue(any("数据类型重复" in text for text in errors))

    def test_non_list_payload(self) -> None:
        self.assertEqual(parse_table("nope", purpose=PURPOSE_LABEL)[0], ())
        self.assertEqual(parse_table(None, purpose=PURPOSE_LABEL)[0], ())


class AlignTableCase(unittest.TestCase):
    def test_set_row_keeps_datatype_order(self) -> None:
        table = AlignTable(purpose=PURPOSE_LABEL)
        table = table.set_row(AlignRow(datatype="TEXT", template="t"))
        table = table.set_row(AlignRow(datatype="IMAGE", template="t"))
        self.assertEqual([row.datatype for row in table.rows], ["IMAGE", "TEXT"])
        table = table.set_row(AlignRow(datatype="IMAGE", template="other"))
        self.assertEqual([row.datatype for row in table.rows], ["IMAGE", "TEXT"])
        self.assertEqual(table.row("IMAGE").template, "other")

    def test_resolved_skips_disabled_rows(self) -> None:
        table = AlignTable(purpose=PURPOSE_LABEL, rows=(AlignRow(datatype="IMAGE", template="t"),))
        self.assertEqual(table.resolved("IMAGE", registered={"t": "local/m"}), "local/m")
        self.assertEqual(table.resolved("VIDEO", registered={"t": "local/m"}), "")
        disabled = AlignTable(purpose=PURPOSE_LABEL, rows=(AlignRow(datatype="IMAGE", template="t", enabled=False),))
        self.assertEqual(disabled.resolved("IMAGE", registered={"t": "local/m"}), "")

    def test_ready_and_missing(self) -> None:
        table = AlignTable(
            purpose=PURPOSE_LABEL,
            rows=(AlignRow(datatype="IMAGE", template="t"), AlignRow(datatype="VIDEO", template="v")),
        )
        self.assertEqual([row.datatype for row in table.ready_rows(registered={"t": "local/m"})], ["IMAGE"])
        self.assertEqual([row.datatype for row in table.missing(registered={"t": "local/m"})], ["VIDEO"])

    def test_prerequisite_model_counts_as_missing(self) -> None:
        row = AlignRow(datatype="IMAGE", template="t", align_template="clip")
        self.assertEqual(row.missing_parts(registered={"t": "local/m"}), ("对齐模型",))
        self.assertEqual(row.status_text(registered={"t": "local/m"}), "对齐模型未登记：clip")
        table = AlignTable(purpose=PURPOSE_LABEL, rows=(row,))
        self.assertEqual(table.ready_rows(registered={"t": "local/m"}), ())
        self.assertEqual(
            [item.datatype for item in table.missing(registered={"t": "local/m"})], ["IMAGE"]
        )
        both = {"t": "local/m", "clip": "local/clip"}
        self.assertEqual(row.missing_parts(registered=both), ())
        self.assertEqual(row.status_text(registered=both), "就绪")

    def test_status_text_lists_both_and_respects_disabled(self) -> None:
        row = AlignRow(datatype="IMAGE", template="t", align_template="clip")
        self.assertEqual(row.missing_parts(registered={}), ("对齐模型", "模型"))
        self.assertEqual(row.status_text(registered={}), "对齐模型未登记：clip；主模型未登记：t")
        disabled = AlignRow(datatype="IMAGE", template="t", enabled=False)
        self.assertEqual(disabled.missing_parts(registered={}), ())
        self.assertEqual(disabled.status_text(registered={}), "已关闭")
        self.assertEqual(
            AlignTable(purpose=PURPOSE_LABEL, rows=(disabled,)).missing(registered={}), ()
        )


class AlignBookCase(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = (
            AlignTable(purpose=PURPOSE_LABEL, rows=(AlignRow(datatype="IMAGE", template="t"),)),
            AlignTable(purpose=PURPOSE_KEYWORD, rows=(AlignRow(datatype="IMAGE", template="t"),)),
        )

    def test_user_row_overrides_factory(self) -> None:
        book = AlignBook(factory=self.factory).set_row(PURPOSE_LABEL, AlignRow(datatype="IMAGE", model_id="local/x"))
        self.assertEqual(book.table(PURPOSE_LABEL).row("IMAGE").model_id, "local/x")
        self.assertEqual(book.table(PURPOSE_KEYWORD).row("IMAGE").template, "t")

    def test_payload_is_a_full_snapshot(self) -> None:
        """清单 = 「表格看到的东西」：每段写全部数据类型，而不是只写与出厂不同的行。"""
        book = AlignBook(factory=self.factory)
        payload = book.payload
        self.assertEqual(payload["version"], 1)
        for purpose in (PURPOSE_LABEL, PURPOSE_KEYWORD):
            self.assertEqual([row["datatype"] for row in payload[purpose]], ["IMAGE"])
        changed = book.set_row(PURPOSE_LABEL, AlignRow(datatype="IMAGE", model_id="local/x"))
        image = next(row for row in changed.payload[PURPOSE_LABEL] if row["datatype"] == "IMAGE")
        self.assertEqual(image["model_id"], "local/x")

    def test_uncovered_factory_row_follows_the_preset(self) -> None:
        """清单里没写到的数据类型 = 用户没单独设过 = 跟随系统方案（方案列显示「启用」）。"""
        book = AlignBook(factory=self.factory)
        self.assertTrue(book.table(PURPOSE_LABEL).row("IMAGE").use_preset)
        manual = book.set_row(
            PURPOSE_LABEL, AlignRow(datatype="IMAGE", template="t", use_preset=False)
        )
        self.assertFalse(manual.table(PURPOSE_LABEL).row("IMAGE").use_preset)

    def test_cancelled_preset_stays_in_the_snapshot(self) -> None:
        """取消勾选「启用系统方案」、内容又恰好与出厂相同时，这一行也得留在清单里。

        否则刷新读回的是出厂行，表格的方案列就会显示成「启用」，和刚做的设置对不上。
        """
        book = AlignBook(factory=self.factory).set_row(
            PURPOSE_LABEL, AlignRow(datatype="IMAGE", template="t", use_preset=True)
        )
        manual = book.set_row(
            PURPOSE_LABEL, AlignRow(datatype="IMAGE", template="t", use_preset=False)
        )
        image = next(row for row in manual.payload[PURPOSE_LABEL] if row["datatype"] == "IMAGE")
        self.assertFalse(image["use_preset"])
        again = AlignBook.from_payload(manual.payload, self.factory)
        self.assertFalse(again.table(PURPOSE_LABEL).row("IMAGE").use_preset)

    def test_reset_and_clear(self) -> None:
        book = AlignBook(factory=self.factory).set_row(PURPOSE_LABEL, AlignRow(datatype="IMAGE", model_id="local/x"))
        self.assertEqual(book.reset_row(PURPOSE_LABEL, "IMAGE").table(PURPOSE_LABEL).row("IMAGE").template, "t")
        self.assertEqual(book.clear_user().table(PURPOSE_LABEL).row("IMAGE").template, "t")
        self.assertEqual(
            [row["datatype"] for row in book.clear_user().payload[PURPOSE_LABEL]], ["IMAGE"]
        )

    def test_from_payload_reads_the_whole_snapshot(self) -> None:
        payload = {
            "version": 1,
            PURPOSE_LABEL: [
                AlignRow(datatype="IMAGE", template="t").to_dict(),
                AlignRow(datatype="TEXT", template="t2").to_dict(),
            ],
        }
        book = AlignBook.from_payload(payload, self.factory)
        self.assertEqual([row.datatype for row in book.user[PURPOSE_LABEL]], ["IMAGE", "TEXT"])
        self.assertEqual(book.errors, ())
        self.assertEqual(book.table(PURPOSE_LABEL).row("TEXT").template, "t2")

    def test_factory_table_falls_back_to_empty(self) -> None:
        book = AlignBook(factory=self.factory)
        self.assertEqual(book.factory_table(PURPOSE_KEYWORD).purpose, PURPOSE_KEYWORD)
        self.assertEqual(book.factory_table("nope").rows, ())
        self.assertEqual(book.purposes, (PURPOSE_LABEL, PURPOSE_KEYWORD))

    def test_use_preset_follows_factory_models(self) -> None:
        """勾了「跟随系统方案」的行用出厂模型，用户自己填的不生效；改回手动才生效。"""
        book = AlignBook(factory=self.factory).set_row(
            PURPOSE_LABEL,
            AlignRow(datatype="IMAGE", model_id="local/mine", template="mine", use_preset=True),
        )
        row = book.table(PURPOSE_LABEL).row("IMAGE")
        self.assertTrue(row.use_preset)
        self.assertEqual((row.template, row.align_template), ("t", ""))
        self.assertEqual(row.model_id, "")

        manual = book.set_row(PURPOSE_LABEL, replace(row, use_preset=False, model_id="local/mine"))
        again = manual.table(PURPOSE_LABEL).row("IMAGE")
        self.assertFalse(again.use_preset)
        self.assertEqual(again.model_id, "local/mine")

    def test_use_preset_survives_payload_round_trip(self) -> None:
        """「跟随」这个选择要写进用户文件并在读回来时还在（出厂换模型时这些行跟着换）。"""
        book = AlignBook(factory=self.factory).set_row(
            PURPOSE_LABEL, AlignRow(datatype="IMAGE", use_preset=True)
        )
        payload = book.payload
        image = next(row for row in payload[PURPOSE_LABEL] if row["datatype"] == "IMAGE")
        self.assertTrue(image["use_preset"])
        again = AlignBook.from_payload(payload, self.factory)
        self.assertTrue(again.table(PURPOSE_LABEL).row("IMAGE").use_preset)
        self.assertEqual(again.table(PURPOSE_LABEL).row("IMAGE").template, "t")


class RegisteredMapCase(unittest.TestCase):
    def test_registered_map(self) -> None:
        rows = (
            {"id": "tpl-a", "registered_id": "local/a"},
            {"id": "tpl-b"},
            {"registered_id": "local/c"},
        )
        self.assertEqual(registered_map(rows), {"tpl-a": "local/a"})
        self.assertEqual(registered_map(()), {})


class FactoryAlignCase(unittest.TestCase):
    def test_factory_tables_cover_every_datatype(self) -> None:
        tables = factory_tables()
        self.assertEqual([table.purpose for table in tables], list(PURPOSES))
        for table in tables:
            with self.subTest(purpose=table.purpose):
                self.assertEqual(table.errors, ())
                self.assertEqual([row.datatype for row in table.rows], list(DATATYPES))
                self.assertTrue(all(row.enabled for row in table.rows))
                self.assertTrue(all(row.problems == () for row in table.rows))

    def test_default_templates_are_registered_by_key(self) -> None:
        keys = {
            row.template
            for table in factory_tables()
            for row in table.rows
            if row.template
        }
        self.assertEqual(keys, {"qwen2.5-1.5b-instruct-gguf"})
        labels = {table.purpose: {row.datatype: row.align_template for row in table.rows} for table in factory_tables()}
        self.assertEqual(labels[PURPOSE_LABEL]["IMAGE"], "blip-image-captioning-base")
        self.assertEqual(labels[PURPOSE_LABEL]["AUDIO"], "whisper-base")

    def test_dump_tables_round_trip(self) -> None:
        tables = factory_tables()
        payload = dump_tables(tables)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(list(payload.keys()), ["version", *PURPOSES])
        again = []
        for purpose in PURPOSES:
            rows, errors = parse_table(payload.get(purpose), purpose=purpose)
            self.assertEqual(errors, [])
            again.append(AlignTable(purpose=purpose, rows=rows))
        self.assertEqual(again, list(tables))
        self.assertEqual(PURPOSE_LABELS[PURPOSE_LABEL], "自动标签")


@unittest.skipIf(align_controls is None, "需要 PyQt6 与界面工具库")
class AlignRowsCase(unittest.TestCase):
    """对齐表的五列：方案（启用 / 自定义）、主模型、对齐模型、状态。

    每一行都是**这个数据类型自己的设置**，可以各不相同；状态列按这一行自己的主模型与
    对齐模型有没有登记来说清楚缺什么。
    """

    FACTORY = AlignRow(datatype="IMAGE", template="tpl", align_template="clip")
    REGISTERED = {"tpl": "local/from-template", "clip": "local/clip-model"}

    def test_preset_row_shows_its_own_settings(self) -> None:
        row = replace(self.FACTORY, use_preset=True)
        cells = align_controls.align_rows(
            [row], registered=self.REGISTERED, factory={"IMAGE": self.FACTORY}
        )
        self.assertEqual(len(cells[0]), 5)
        self.assertEqual(cells[0][1], "启用")
        self.assertEqual(cells[0][2], "local/from-template")
        self.assertEqual(cells[0][3], "local/clip-model")
        self.assertEqual(cells[0][4], "就绪")

    def test_rows_are_independent(self) -> None:
        """不同类型可以各配各的：表里十行是十份独立设置，不是同一份的复制。"""
        rows = [
            AlignRow(datatype="IMAGE", model_id="local/blip", align_model="local/blip"),
            AlignRow(datatype="VIDEO", model_id="local/other", align_model="local/clip"),
        ]
        cells = align_controls.align_rows(rows, registered=self.REGISTERED, factory={})
        self.assertEqual([cell[0] for cell in cells], ["图片", "视频"])
        self.assertEqual([cell[1] for cell in cells], ["自定义", "自定义"])
        self.assertEqual([cell[2] for cell in cells], ["local/blip", "local/other"])
        self.assertEqual([cell[3] for cell in cells], ["local/blip", "local/clip"])
        self.assertEqual([cell[4] for cell in cells], ["就绪", "就绪"])

    def test_same_content_but_not_preset_shows_custom(self) -> None:
        """内容与出厂相同、但没有勾「启用系统方案」时，方案列必须显示「自定义」。"""
        row = AlignRow(datatype="VIDEO", template="tpl", align_template="clip", use_preset=False)
        cells = align_controls.align_rows(
            [row], registered=self.REGISTERED, factory={"VIDEO": row}
        )
        self.assertEqual(cells[0][1], "自定义")

    def test_unregistered_model_is_named_in_status(self) -> None:
        cells = align_controls.align_rows(
            [AlignRow(datatype="TEXT", template="tpl")], registered={}, factory={}
        )
        self.assertEqual(cells[0][2], "未登记：tpl")
        self.assertEqual(cells[0][4], "主模型未登记：tpl")

    def test_status_lists_alignment_model_first(self) -> None:
        cells = align_controls.align_rows(
            [AlignRow(datatype="IMAGE", template="tpl", align_template="clip")],
            registered={},
            factory={},
        )
        self.assertEqual(cells[0][4], "对齐模型未登记：clip；主模型未登记：tpl")

    def test_names_replace_model_ids(self) -> None:
        """表格与编辑框显示同一个名字：登记过就显示登记名，别一会儿 id 一会儿方案 key。"""
        row = AlignRow(datatype="IMAGE", model_id="local/blip-base", align_model="local/blip-base")
        cells = align_controls.align_rows(
            [row], registered={}, factory={}, names={"local/blip-base": "BLIP 图像描述（base）"}
        )
        self.assertEqual(cells[0][2], "BLIP 图像描述（base）")
        self.assertEqual(cells[0][3], "BLIP 图像描述（base）")

    def test_model_choices_hide_registered_templates(self) -> None:
        """已经登记成模型的出厂方案不再以「系统方案」形态出现（同一个模型只有一个名字）。"""
        items, values = align_controls._model_choices(
            ["BLIP 图像描述（base）"],
            ["local/blip-base"],
            ["图像描述 blip-image-captioning-base", "对话 qwen2.5-1.5b-instruct-gguf"],
            ["blip-image-captioning-base", "qwen2.5-1.5b-instruct-gguf"],
            blank="（不用）",
            registered={"blip-image-captioning-base": "local/blip-base"},
        )
        self.assertNotIn("preset:blip-image-captioning-base", values)
        self.assertIn("preset:qwen2.5-1.5b-instruct-gguf", values)
        self.assertIn("model:local/blip-base", values)
        self.assertEqual(len(items), len(values))

    def test_disabled_row_shows_stopped_in_plan_column(self) -> None:
        """关掉「启用这个数据类型」时方案列要显示「已停用」，否则用户看不出自己改了什么。"""
        row = AlignRow(datatype="IMAGE", template="tpl", align_template="clip", enabled=False)
        cells = align_controls.align_rows(
            [row], registered=self.REGISTERED, factory={"IMAGE": self.FACTORY}
        )
        self.assertEqual(cells[0][1], "已停用")
        self.assertEqual(cells[0][4], "已关闭")


if __name__ == "__main__":
    unittest.main()
