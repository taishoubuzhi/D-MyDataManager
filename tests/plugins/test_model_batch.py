"""模型批量调度与预定义方案：离线用例，不联网、不装包。

批量接口的重点是「同一模型只加载一次」：这里用假适配器数 `start()` 次数，
再验组内串行、组间并行、单条失败隔离、取消与进度回调。
预定义方案（`templates` / `create_from_template` / `requirements`）只读 JSON 与登记表，
用假 ctx 提供 `.data/model_list.json`（统一清单格式），不碰磁盘上的权重。
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
import types
import unittest
from pathlib import Path

from tests.harness import IsolatedCase


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.lib.model` 包（目录名带点，只能手工注册）。"""
    root = Path(__file__).resolve().parents[2] / "plugins" / "lib.model"
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.model", root),
    ):
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__path__ = [] if folder is None else [str(folder), str(Path(folder) / ".plugin")]
            sys.modules[name] = module
        # 手工塞进 sys.modules 的包不会有父子属性，`dm_plugin.lib.x` 这类按属性取值会 AttributeError
        parent, _, leaf = name.rpartition(".")
        if parent:
            setattr(sys.modules[parent], leaf, module)


_register_plugin_namespace()

from dm_plugin.lib.model import provider as provider_module  # noqa: E402
from dm_plugin.lib.model.batch import (  # noqa: E402
    MAX_PARALLEL,
    BatchItem,
    BatchOutcome,
    as_items,
    run_batch,
)
from dm_plugin.lib.model import manager as manager_module  # noqa: E402
from dm_plugin.lib.model.constants import (  # noqa: E402
    STATE_DRAFT,
    STATE_READY,
)
from dm_plugin.lib.model.manager import ModelManager  # noqa: E402
from dm_plugin.lib.model.paths import local_dir  # noqa: E402
from dm_plugin.lib.model.record import ModelRecord  # noqa: E402
from dm_plugin.lib.model.registry import ModelRegistry  # noqa: E402


class FakeAdapter:
    """假适配器：记下加载 / 调用 / 并发，让批量调度逻辑可以离线验证。

    `bad_payload` 命中的调用抛异常，用来验证单条失败不打断整批；
    `delay` 拉开并发窗口，`on_invoke` 给用例一个钩子（例如置取消标志）。
    """

    name = "fake"

    def __init__(self, record, settings=None) -> None:
        self.record = record
        self.settings = settings
        self.started = 0
        self.stopped = 0
        self.calls: list[tuple[str, dict | None]] = []
        self.bad_payload: object = None
        self.delay = 0.0
        self.on_invoke = None
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def available(self) -> bool:
        return True

    def start(self) -> None:
        self.started += 1
        self._loaded = True

    def invoke(self, task, payload=None, *, stream=False, timeout=None):
        self.calls.append((task, payload))
        if self.on_invoke is not None:
            self.on_invoke(self, task, payload)
        if self.delay:
            time.sleep(self.delay)
        if self.bad_payload is not None and (payload or {}).get("bad") == self.bad_payload:
            raise RuntimeError(f"假适配器拒绝：{task}")
        return {"task": task, "echo": payload, "record": self.record.id}

    def stop(self) -> None:
        self.stopped += 1
        self._loaded = False

    def cancel(self) -> None:
        pass

    def info(self) -> dict:
        return {"adapter": self.name, "loaded": self.loaded, "record": self.record.id}


class FakeCtx:
    """`ModelOpenApi` 用到的 ctx 面：只读 `data(key, default)`，`emit` 记录事件。"""

    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(data or {})
        self.notes: list[tuple[str, dict]] = []

    def data(self, key: str, default=None):
        value = self._data.get(key, default)
        return {} if value is None else value

    def emit(self, name: str, payload: dict | None = None) -> None:
        self.notes.append((name, payload or {}))


def _catalog(manifest_id: str, records: list[dict]) -> dict:
    """把记录列表包成统一清单格式（插件 `.data/` 的现行形状）。"""
    return {
        "manifest": "1",
        "id": manifest_id,
        "version": "1",
        "kind": "catalog",
        "items": [{"key": str(record.get("id") or ""), **record} for record in records],
    }


TEMPLATES = _catalog(
    "lib.model.model_list",
    [
        {
            "id": "big-chat",
            "name": "大会话模型",
            "capabilities": ["chat"],
            "params": "7B",
            "size_bytes": 4_000_000_000,
            "source": {"provider": "huggingface", "repo": "org/big", "file": "big.gguf"},
            "runtime": {"adapter": "llama_cpp", "backend": "llama_cpp", "profile": "cpu-llama"},
        },
        {
            "id": "small-chat",
            "name": "小会话模型",
            "capabilities": ["chat"],
            "params": "0.6B",
            "size_bytes": 600_000_000,
            "lightweight": True,
            "source": {"provider": "huggingface", "repo": "org/small", "file": "small.gguf"},
            "runtime": {"adapter": "llama_cpp", "backend": "llama_cpp", "profile": ""},
        },
        {
            "id": "tiny-embed",
            "name": "小向量模型",
            "capabilities": ["embedding"],
            "params": "22M",
            "size_bytes": 90_000_000,
            "lightweight": True,
            "source": {"provider": "huggingface", "repo": "org/embed", "files": ["model.safetensors", "config.json"]},
            "runtime": {"adapter": "inprocess", "backend": "sentence_transformers", "profile": "cpu-torch"},
        },
    ],
)

#: 空的运行环境清单（同样走统一清单格式）
NO_PROFILES = {"manifest": "1", "id": "lib.model.runtime_profiles", "version": "1", "kind": "profiles", "items": []}


class BatchItemCase(unittest.TestCase):
    """`as_items`：对象 / 字典都收，缺 task 的条目跳过。"""

    def test_reads_objects_and_dicts(self) -> None:
        items = as_items([
            BatchItem(task="chat", payload={"a": 1}, key="k1", model_id="m1"),
            {"task": "embedding", "payload": {"b": 2}, "key": "k2"},
            {"task": "", "key": "skip"},
            {"task": "rerank", "payload": "不是字典"},
        ])
        self.assertEqual([item.key for item in items], ["k1", "k2", "4"])
        self.assertEqual(items[0].model_id, "m1")
        self.assertEqual(items[1].payload, {"b": 2})
        self.assertEqual(items[2].payload, {})

    def test_missing_key_falls_back_to_index(self) -> None:
        items = as_items([{"task": "chat"}, {"task": "chat"}], default_model_id="local/甲")
        self.assertEqual([item.key for item in items], ["1", "2"])
        self.assertEqual([item.model_id for item in items], ["local/甲", "local/甲"])

    def test_empty_requests(self) -> None:
        self.assertEqual(as_items(None), [])
        self.assertEqual(as_items([{"task": "  "}]), [])


class BatchRunCase(IsolatedCase):
    """批量调度：合批只加载一次、组内串行、组间并行、失败隔离、取消与进度。"""

    def setUp(self) -> None:
        super().setUp()
        # 同一用例类共用一个临时根目录：两个位置的权重都要清掉，否则新登记会被
        # 磁盘上的老文件直接判成「已就绪」（旧位置 `.resources/models`、现位置 `.models`）。
        shutil.rmtree(self.root / ".resources" / "models", ignore_errors=True)
        shutil.rmtree(self.root / ".models", ignore_errors=True)
        self.registry = ModelRegistry(self.root / "models" / f"{self._testMethodName}.json")
        self.settings = manager_module.ModelSettings()
        self.manager = ModelManager(self.registry, self.settings)
        self.adapters: dict[str, FakeAdapter] = {}
        self.started_log: list[str] = []
        self._original = manager_module.build_adapter

        def factory(record, settings):
            adapter = FakeAdapter(record, settings)
            adapter.on_invoke = lambda *_: self.started_log.append(record.id)
            self.adapters[record.id] = adapter
            return adapter

        manager_module.build_adapter = factory

    def tearDown(self) -> None:
        manager_module.build_adapter = self._original
        self.manager.shutdown()
        super().tearDown()

    def _local(self, name: str, capability: str = "chat", **runtime) -> ModelRecord:
        record = self.manager.add_local(
            name,
            capabilities=(capability,),
            files=("model.gguf",),
            source={"provider": "local"},
            runtime=dict(runtime),
        )
        folder = local_dir(record.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.gguf").write_bytes(b"gguf")
        record.sync_files()
        record.state = STATE_READY if record.files else record.state
        self.manager.update(record)
        return record

    def test_same_model_loads_once_and_keeps_order(self) -> None:
        record = self._local("甲")
        results = run_batch(self.manager, [{"task": "chat", "payload": {"i": i}, "key": f"k{i}"} for i in range(4)])
        self.assertEqual([item.ok for item in results], [True] * 4)
        self.assertEqual([item.key for item in results], ["k0", "k1", "k2", "k3"])
        self.assertEqual([item.model_id for item in results], [record.id] * 4)
        self.assertEqual(self.adapters[record.id].started, 1)
        self.assertEqual([call[1]["i"] for call in self.adapters[record.id].calls], [0, 1, 2, 3])
        self.assertEqual(self.manager.loaded(), (record.id,))

    def test_model_id_per_request_is_used(self) -> None:
        first = self._local("甲")
        second = self._local("乙", capability="embedding")
        results = run_batch(self.manager, [
            {"task": "chat", "model_id": first.id},
            {"task": "embedding", "model_id": second.id},
        ])
        self.assertEqual([item.model_id for item in results], [first.id, second.id])
        self.assertEqual(sorted(self.started_log), sorted([first.id, second.id]))

    def test_capability_selects_model_when_id_missing(self) -> None:
        record = self._local("乙", capability="embedding")
        results = run_batch(self.manager, [{"task": "embedding"}], capability="embedding")
        self.assertEqual(results[0].model_id, record.id)
        self.assertTrue(results[0].ok)

    def test_groups_run_in_parallel(self) -> None:
        """两组（常驻上限 2）应当真的同时在跑：用并发计数验证，不靠时间断言。"""
        self.settings.runtime["max_resident"] = 2
        first = self._local("甲")
        second = self._local("乙", capability="embedding")
        peak = 0
        active = 0
        guard = threading.Lock()
        built: list[str] = []

        def track(adapter, task, payload):
            nonlocal peak, active
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)
            with guard:
                active -= 1

        def factory(record, settings):
            adapter = FakeAdapter(record, settings)
            adapter.on_invoke = track
            built.append(record.id)
            return adapter

        manager_module.build_adapter = factory
        results = run_batch(self.manager, [
            {"task": "chat", "model_id": first.id},
            {"task": "embedding", "model_id": second.id},
        ])
        self.assertEqual([item.ok for item in results], [True, True])
        self.assertEqual(sorted(built), sorted([first.id, second.id]))
        self.assertGreaterEqual(peak, 2)

    def test_failure_isolated_and_batch_continues(self) -> None:
        record = self._local("甲")
        requests = [
            {"task": "chat", "payload": {"i": i, "bad": i == 1}, "key": f"k{i}"}
            for i in range(4)
        ]
        self.adapters.setdefault(record.id, None)

        def factory(record_, settings):
            adapter = FakeAdapter(record_, settings)
            adapter.bad_payload = True
            return adapter

        manager_module.build_adapter = factory
        self.manager.unload_all()
        results = run_batch(self.manager, requests)
        self.assertEqual([item.ok for item in results], [True, False, True, True])
        self.assertEqual(results[1].key, "k1")
        self.assertIn("假适配器拒绝", results[1].error)
        self.assertEqual(results[1].model_id, record.id)
        self.assertEqual(results[3].value["echo"], {"i": 3, "bad": False})

    def test_unresolvable_request_reports_error_without_stopping_others(self) -> None:
        record = self._local("甲")
        results = run_batch(self.manager, [
            {"task": "chat", "model_id": "local/不存在", "key": "bad"},
            {"task": "chat", "model_id": record.id, "key": "good"},
        ])
        self.assertFalse(results[0].ok)
        self.assertIn("没有找到可用模型", results[0].error)
        self.assertTrue(results[1].ok)

    def test_cancel_marks_remaining_requests(self) -> None:
        record = self._local("甲")
        flag = {"cancel": False}

        def factory(record_, settings):
            adapter = FakeAdapter(record_, settings)
            adapter.on_invoke = lambda *_: flag.__setitem__("cancel", True)
            return adapter

        manager_module.build_adapter = factory
        self.manager.unload_all()
        results = run_batch(
            self.manager,
            [{"task": "chat", "key": f"k{i}"} for i in range(3)],
            cancel=lambda: flag["cancel"],
        )
        self.assertTrue(results[0].ok)
        self.assertEqual([item.error for item in results[1:]], ["已取消", "已取消"])

    def test_cancel_before_start_skips_everything(self) -> None:
        record = self._local("甲")
        results = run_batch(self.manager, [{"task": "chat"}], cancel=lambda: True)
        self.assertEqual([item.error for item in results], ["已取消"])
        self.assertNotIn(record.id, self.adapters)  # 连加载都没发生
        self.assertEqual(self.manager.loaded(), ())

    def test_progress_callback_counts_every_request(self) -> None:
        self._local("甲")
        seen: list[tuple[int, int]] = []
        run_batch(self.manager, [{"task": "chat"} for _ in range(3)], on_progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_progress_callback_errors_are_ignored(self) -> None:
        self._local("甲")

        def boom(done, total):
            raise RuntimeError("进度回调炸了")

        results = run_batch(self.manager, [{"task": "chat"}], on_progress=boom)
        self.assertTrue(results[0].ok)

    def test_all_requests_without_task_return_empty(self) -> None:
        self._local("甲")
        self.assertEqual(run_batch(self.manager, [{"task": ""}]), ())

    def test_workers_respects_max_resident(self) -> None:
        from dm_plugin.lib.model.batch import _workers

        self.settings.runtime["max_resident"] = 2
        self.assertEqual(_workers(self.manager, 5, None), 2)
        self.assertEqual(_workers(self.manager, 5, 3), 2)
        self.assertEqual(_workers(self.manager, 1, None), 1)
        self.assertLessEqual(MAX_PARALLEL, 8)

    def test_provider_run_batch_forwards(self) -> None:
        record = self._local("甲")
        api = provider_module.ModelOpenApi(FakeCtx(), self.manager)
        results = api.run_batch([{"task": "chat", "key": "k"}], capability="chat")
        self.assertEqual(results[0].model_id, record.id)
        self.assertIsInstance(results[0], BatchOutcome)


class TemplateCase(IsolatedCase):
    """预定义方案：模板筛选、登记状态、草稿登记与缺口清单。"""

    def setUp(self) -> None:
        super().setUp()
        # 同一用例类共用一个临时根目录：两个位置的权重都要清掉，否则新登记会被
        # 磁盘上的老文件直接判成「已就绪」（旧位置 `.resources/models`、现位置 `.models`）。
        shutil.rmtree(self.root / ".resources" / "models", ignore_errors=True)
        shutil.rmtree(self.root / ".models", ignore_errors=True)
        self.registry = ModelRegistry(self.root / "models" / f"{self._testMethodName}.json")
        self.settings = manager_module.ModelSettings()
        self.manager = ModelManager(self.registry, self.settings)
        self.ctx = FakeCtx({"model_list": TEMPLATES, "runtime_profiles": NO_PROFILES})
        self.api = provider_module.ModelOpenApi(self.ctx, self.manager)

    def tearDown(self) -> None:
        self.manager.shutdown()
        super().tearDown()

    def test_templates_prefer_lightweight_then_size(self) -> None:
        rows = self.api.templates()
        self.assertEqual([row["id"] for row in rows], ["tiny-embed", "small-chat", "big-chat"])
        self.assertEqual([row["lightweight"] for row in rows], [True, True, False])
        self.assertEqual(rows[0]["capabilities"], ["embedding"])
        self.assertEqual(rows[0]["state_label"], "未登记")
        self.assertEqual(rows[0]["registered_id"], "")

    def test_templates_filter_by_capability_and_lightweight(self) -> None:
        self.assertEqual([row["id"] for row in self.api.templates(capability="chat")], ["small-chat", "big-chat"])
        self.assertEqual([row["id"] for row in self.api.templates(lightweight_only=True)], ["tiny-embed", "small-chat"])
        self.assertEqual(self.api.templates(capability="asr"), [])

    def test_templates_report_registered_state(self) -> None:
        record = self.manager.add_local(
            "小会话模型",
            capabilities=("chat",),
            files=("small.gguf",),
            source={"provider": "huggingface", "repo": "org/small", "file": "small.gguf"},
        )
        row = [item for item in self.api.templates() if item["id"] == "small-chat"][0]
        self.assertEqual(row["registered_id"], record.id)
        self.assertEqual(row["state"], record.state)
        self.assertEqual(row["state_label"], record.state_label)

    def test_template_lookup_does_not_mix_similar_repos(self) -> None:
        """文件清单相近（都有 config.json / tokenizer.json）的两个模板不能互相认领。

        真事故：BLIP 与 CLIP 的目录长得几乎一样，旧逻辑用「文件名有交集」判同一个模型，
        于是除了图片，其他类型的对齐模型全被显示成 blip（用户 m42266）。
        """
        blip = {
            "id": "cap-blip",
            "name": "图像描述",
            "capabilities": ["vision"],
            "lightweight": True,
            "size_bytes": 100,
            "source": {
                "provider": "huggingface",
                "repo": "org/blip",
                "file": "pytorch_model.bin",
                "files": ["config.json", "pytorch_model.bin", "tokenizer.json"],
            },
        }
        clip = {
            "id": "cap-clip",
            "name": "图文匹配",
            "capabilities": ["vision"],
            "lightweight": True,
            "size_bytes": 200,
            "source": {
                "provider": "huggingface",
                "repo": "org/clip",
                "file": "pytorch_model.bin",
                "files": ["config.json", "pytorch_model.bin", "vocab.json"],
            },
        }
        ctx = FakeCtx({"model_list": _catalog("lib.model.model_list", [blip, clip]), "runtime_profiles": NO_PROFILES})
        api = provider_module.ModelOpenApi(ctx, self.manager)
        self.manager.add_local(
            "图像描述草稿",
            capabilities=("vision",),
            files=("config.json", "pytorch_model.bin", "tokenizer.json"),
            source={"provider": "huggingface", "repo": "org/blip", "file": "pytorch_model.bin"},
        )
        rows = {row["id"]: row for row in api.templates()}
        self.assertTrue(rows["cap-blip"]["registered_id"])
        self.assertEqual(rows["cap-clip"]["registered_id"], "")

    def test_create_from_template_records_its_source_key(self) -> None:
        """登记时把模板 key 写进 source：以后按它精确认领，不用再猜文件名。"""
        record = self.api.create_from_template("small-chat")
        self.assertEqual((record.source or {}).get("template"), "small-chat")

    def test_create_from_template_registers_a_draft_only(self) -> None:
        record = self.api.create_from_template("small-chat")
        self.assertEqual(record.state, STATE_DRAFT)  # 只写登记表，不下载
        self.assertEqual(record.source["repo"], "org/small")
        self.assertEqual(self.api.create_from_template("小会话模型").id, record.id)  # 名字也能查
        self.assertEqual(len(self.manager.registry), 1)  # 不重复登记
        self.assertEqual(record.local_path(), local_dir(record.id))

    def test_create_from_template_unknown_key_raises(self) -> None:
        with self.assertRaises(provider_module.ModelError):
            self.api.create_from_template("不存在")

    def test_create_from_template_accepts_custom_name(self) -> None:
        record = self.api.create_from_template("tiny-embed", name="我的向量模型")
        self.assertEqual(record.name, "我的向量模型")
        self.assertEqual(record.capabilities, ("embedding",))

    def test_page_route_points_at_the_model_page(self) -> None:
        self.assertEqual(self.api.page_route(), "plugin.model_manager")

    def test_requirements_for_unknown_model(self) -> None:
        report = self.api.requirements("local/不存在")
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing"], ["模型没有登记"])

    def test_requirements_for_external_model_is_ok(self) -> None:
        external = self.manager.add_external("云端", base_url="http://127.0.0.1:8", model="m", capabilities=("chat",))
        report = self.api.requirements(external.id)
        self.assertTrue(report["ok"])
        self.assertEqual(report["missing"], [])

    def test_requirements_lists_missing_files_and_profile(self) -> None:
        record = self.api.create_from_template("small-chat")
        report = self.api.requirements(record.id)
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_files"], ["small.gguf"])
        self.assertEqual(report["profile"], "")
        self.assertIn("缺模型文件：权重（small.gguf）", report["missing"])
        self.assertIn("没登记推理后端 profile", report["missing"])

    def test_requirements_reports_unknown_profile(self) -> None:
        record = self.api.create_from_template("big-chat")
        report = self.api.requirements(record.id)
        self.assertEqual(report["profile"], "cpu-llama")
        self.assertFalse(report["profile_ready"])
        self.assertTrue(any("运行环境清单里没有这个 profile" in item for item in report["missing"]))


class BatchSdkCase(IsolatedCase):
    """`dm_plugin.lib.model.api` 门面：把批量请求转给插件，旧插件报 SdkError。"""

    def setUp(self) -> None:
        super().setUp()
        from dm_plugin.lib.model import api

        self.models = api
        self.addCleanup(self.models.attach, None)

    def _provide(self, api) -> None:
        """门面是用注入的方式拿到调度接口的（原来是扩展登记表）。"""
        self.models.attach(api)

    def test_run_batch_forwards_to_plugin(self) -> None:
        seen: dict = {}
        models = self.models

        class FakeApi:
            def run_batch(self, requests, **kwargs):
                seen["requests"] = list(requests)
                seen["kwargs"] = kwargs
                return [models.BatchResult(key="k", ok=True, value={"v": 1}, model_id="m")]

        api = FakeApi()
        self._provide(api)
        self.assertTrue(self.models.supports_batch())
        results = self.models.run_batch([self.models.BatchRequest(task="chat", key="k")], capability="chat")
        self.assertEqual(results[0].value, {"v": 1})
        self.assertEqual(seen["requests"][0].task, "chat")
        self.assertEqual(seen["kwargs"]["capability"], "chat")

    def test_missing_run_batch_raises_sdk_error(self) -> None:
        from app.sdk.errors import SdkError

        class OldApi:
            def list_models(self, **kwargs):
                return ()

        self._provide(OldApi())
        self.assertFalse(self.models.supports_batch())
        with self.assertRaises(SdkError):
            self.models.run_batch([])

    def test_plugin_exception_becomes_model_error(self) -> None:
        class BoomApi:
            def run_batch(self, requests, **kwargs):
                raise RuntimeError("炸了")

        self._provide(BoomApi())
        # 用门面自己那份 ModelError：插件重载会重建 dm_plugin.* 模块，这里不能缓存类对象
        with self.assertRaises(self.models.ModelError):
            self.models.run_batch([])


if __name__ == "__main__":
    unittest.main()
