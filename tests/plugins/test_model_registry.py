"""模型登记表、记录、调度器与设置：离线用例，不联网、不装包。

用 `IsolatedCase` 把程序根目录（`paths.ROOT`）、`.resources/` 与 `.configs/` 重定向到
`tests/.tmp/`，所以模型目录（默认 `<根>/.models`）与 `.configs/models.json` 写的都是临时目录。适配器用假实现替换，
`acquire()` 的真实加载路径（worker 子进程 / 独立 venv）由 `test_model_runtime.py` 覆盖。
"""

from __future__ import annotations

import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import IsolatedCase


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.lib.model` 包（目录名带点，只能手工注册）。"""
    root = Path(__file__).resolve().parents[2] / "plugins" / "lib.model"
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.builtin.lib", None),
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

from dm_plugin.lib.model import manager as manager_module  # noqa: E402
from dm_plugin.lib.model import provider as provider_module  # noqa: E402
from dm_plugin.lib.model import registry as registry_module  # noqa: E402
from dm_plugin.lib.model import settings as settings_module  # noqa: E402
from dm_plugin.lib.model.constants import (  # noqa: E402
    KIND_EXTERNAL,
    KIND_LOCAL,
    STATE_DRAFT,
    STATE_INCOMPLETE,
    STATE_READY,
)
from dm_plugin.lib.model.manager import ModelManager  # noqa: E402
from dm_plugin.lib.model.paths import local_dir  # noqa: E402
from dm_plugin.lib.model.record import ModelRecord, make_id, slugify  # noqa: E402
from dm_plugin.lib.model.registry import ModelRegistry, reset  # noqa: E402


class FakeAdapter:
    """假适配器：记下 start / stop / invoke，让调度器逻辑可以离线验证。"""

    name = "fake"

    def __init__(self, record, settings=None) -> None:
        self.record = record
        self.settings = settings
        self.started = 0
        self.stopped = 0
        self.calls: list[tuple[str, dict | None]] = []
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
        return {"task": task, "echo": payload, "record": self.record.id}

    def stop(self) -> None:
        self.stopped += 1
        self._loaded = False

    def cancel(self) -> None:
        pass

    def info(self) -> dict:
        return {"adapter": self.name, "loaded": self.loaded, "record": self.record.id}


class FakeCtx:
    """`ModelOpenApi` 只把 ctx 存起来；这里给个最小替身。"""

    def __init__(self) -> None:
        self.notes: list[tuple[str, dict]] = []

    def emit(self, name: str, payload: dict | None = None) -> None:
        self.notes.append((name, payload or {}))


class ModelRegistryCase(IsolatedCase):
    """登记表 / 记录 / 设置的往返。"""

    def setUp(self) -> None:
        super().setUp()
        reset()

    def test_slug_and_id(self) -> None:
        self.assertEqual(slugify("Qwen 2.5 7B!!"), "qwen-2.5-7b")
        self.assertEqual(slugify("   "), "model")
        # slugify 只保留 ASCII，纯中文名（如「甲」）会退化成 "model"
        self.assertEqual(make_id(KIND_LOCAL, "甲"), "local/model")
        self.assertEqual(make_id(KIND_LOCAL, "甲", existing=["local/model"]), "local/model-2")
        self.assertEqual(make_id(KIND_EXTERNAL, "DeepSeek"), "external/deepseek")

    def test_registry_roundtrip(self) -> None:
        path = self.root / "models" / "registry.json"
        registry = ModelRegistry(path)
        self.assertEqual(len(registry), 0)
        local = ModelRecord.new_local("本地甲", capabilities=("chat",))
        local.state = STATE_READY
        external = ModelRecord.new_external("外部乙", capabilities=("chat", "embedding"), api={"base_url": "http://127.0.0.1:1"})
        external.state = STATE_READY
        registry.add(local)
        registry.add(external)
        self.assertTrue(registry.save())
        self.assertTrue(path.is_file())

        again = ModelRegistry(path)
        again.load()  # 构造不自动读盘
        self.assertEqual(again.ids(), tuple(sorted((local.id, external.id))))  # all() 按 id 排序
        self.assertEqual(again.get(local.id).name, "本地甲")
        self.assertEqual([item.id for item in again.find(kind=KIND_EXTERNAL)], [external.id])
        self.assertEqual([item.id for item in again.find(capability="embedding")], [external.id])
        self.assertIn("chat", again.capabilities())
        self.assertTrue(again.remove(external.id))
        self.assertFalse(again.get(external.id) is not None)
        self.assertEqual(len(again), 1)
        self.assertIn(local.id, again)

    def test_registry_reloads_when_file_vanishes(self) -> None:
        path = self.root / "models" / "gone.json"
        registry = ModelRegistry(path)
        registry.add(ModelRecord.new_external("甲", api={"base_url": "x"}))
        registry.save()
        path.unlink()
        registry.load()
        self.assertEqual(len(registry), 0)

    def test_record_sync_files_and_paths(self) -> None:
        record = ModelRecord.new_local("乙", capabilities=("embedding",))
        folder = local_dir(record.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.gguf").write_bytes(b"1234567890")
        (folder / "config.json").write_text("{}", encoding="utf-8")
        record.sync_files()
        self.assertEqual(record.files, ("config.json", "model.gguf"))
        self.assertEqual(record.size_bytes, 12)
        self.assertEqual(record.local_path(), folder)
        # primary_file 先挑权重后缀（config.json 这种配置不能当主文件喂给推理后端），
        # 没有权重才退回第一个存在的文件
        self.assertEqual(record.primary_file(), folder / "model.gguf")
        record.files = ("config.json",)
        self.assertEqual(record.primary_file(), folder / "config.json")
        self.assertEqual(record.state, STATE_DRAFT)  # sync_files 不动状态
        self.assertTrue(record.plugin_id)
        self.assertEqual(record.kind_label, "本地模型")
        self.assertEqual(record.capability_text, "向量化")

    def test_local_and_external_display(self) -> None:
        local = ModelRecord.new_local("甲")
        self.assertEqual(local.size_text, "—")
        self.assertEqual(local.capability_text, "未标注能力")
        external = ModelRecord.new_external("乙", api={"base_url": "http://a/", "adapter": "ollama", "model": "m"})
        self.assertEqual(external.adapter, "ollama")
        self.assertEqual(external.api_params(), {})
        self.assertEqual(external.api_headers(), {})

    def test_settings_roundtrip_and_secret(self) -> None:
        settings = settings_module.load_settings()
        settings.set_mirrors(["https://a", "https://b"])
        settings.download["concurrent"] = 9  # 夹到 1–12
        settings.runtime["max_resident"] = 0  # 至少 1
        settings.set_secret("kimi", "sk-1234567890")
        self.assertTrue(settings.save())
        again = settings_module.load_settings()
        self.assertEqual(again.mirrors, ("https://a", "https://b"))
        self.assertEqual(again.concurrent, 9)
        self.assertEqual(again.max_resident, 1)
        self.assertEqual(again.secret("kimi"), "sk-1234567890")
        self.assertNotIn("sk-1234567890", settings_module.mask_secret("sk-1234567890"))
        self.assertEqual(settings_module.mask_secret(""), "")
        self.assertEqual(again.proxy, "")

    def test_settings_partial_write_keeps_other_keys(self) -> None:
        """即改即存：界面上只改一项就存盘，别的键（含只读展示的 base_url/mirrors）不受影响。"""
        settings = settings_module.load_settings()
        settings.download["proxy"] = "http://127.0.0.1:7890"
        self.assertTrue(settings.save())
        settings.download["concurrent"] = 2
        settings.runtime["device"] = "cpu"
        settings.runtime["allow_system_env"] = True
        self.assertTrue(settings.save())
        again = settings_module.load_settings()
        self.assertEqual(again.proxy, "http://127.0.0.1:7890")
        self.assertEqual(again.concurrent, 2)
        self.assertEqual(again.device, "cpu")
        self.assertTrue(again.allow_system_env)
        self.assertEqual(again.idle_unload_sec, settings.idle_unload_sec)
        self.assertEqual(again.base_url, settings_module.DEFAULT_BASE_URL)
        self.assertEqual(tuple(again.mirrors), tuple(settings_module.DEFAULT_MIRRORS))
        # 同一个用例类共用一个临时根目录，改完得把设置还原，不然会污染后面的用例。
        settings.download["proxy"] = ""
        settings.download["concurrent"] = 1
        settings.runtime["device"] = "auto"
        settings.runtime["allow_system_env"] = False
        self.assertTrue(settings.save())

    def test_github_source_settings_and_urls(self) -> None:
        """GitHub 下载源：三类选项各自的取址顺序，非 GitHub 地址原样。"""
        settings = settings_module.load_settings()
        self.assertEqual(dict(settings_module.DOWNLOAD_SOURCES)["official"], "HuggingFace官方")
        self.assertEqual(dict(settings_module.DOWNLOAD_SOURCES)["mirror"], "HF-Mirror镜像")
        self.assertEqual(dict(settings_module.DOWNLOAD_SOURCES)["custom"], "自定义")
        self.assertEqual(dict(settings_module.GITHUB_SOURCES)["official"], "Github官方")
        self.assertEqual(settings.github_source, "official")
        self.assertEqual(settings.github_prefixes, ("",))
        raw = "https://github.com/a/b/releases/download/v1/x.whl"
        self.assertEqual(settings.github_urls(raw), (raw,))
        # 三个镜像各是一个独立选项：选谁就只拼谁，镜像是前缀式，最后兜底官方直连
        for key, prefix in settings_module.GITHUB_MIRRORS.items():
            settings.github_source = key
            self.assertEqual(settings.github_prefixes, (prefix, ""))
            self.assertEqual(settings.github_urls(raw), (f"{prefix}/{raw}", raw))
        settings.github_source = "mirror"  # 老版本的单档「镜像」：读回来迁移成第一个镜像
        self.assertEqual(settings.github_source, settings_module.LEGACY_GITHUB_SOURCES["mirror"])
        self.assertEqual(settings.github_source, "ghproxy")
        settings.github_source = "custom"
        settings.github_custom = "https://ghproxy.net/"
        self.assertEqual(settings.github_custom, "https://ghproxy.net")  # 存的时候去掉尾斜杠
        self.assertEqual(settings.github_urls(raw), (f"https://ghproxy.net/{raw}", raw))
        settings.github_custom = ""  # 没填就是官方直连，不许拼出空前缀
        self.assertEqual(settings.github_urls(raw), (raw,))
        self.assertEqual(
            settings.github_urls("https://pypi.org/simple/x/"), ("https://pypi.org/simple/x/",)
        )
        self.assertTrue(settings_module.is_github_url("https://codeload.github.com/a/b/tar.gz/main"))
        self.assertFalse(settings_module.is_github_url("https://pypi.org/x"))
        # 同一个用例类共用一个临时根目录，改完得把设置还原。
        settings.github_source = settings_module.DEFAULT_GITHUB_SOURCE
        settings.github_custom = ""
        self.assertTrue(settings.save())

    def test_registry_module_singleton_writes_under_resources(self) -> None:
        record = ModelRecord.new_external("丙", api={"base_url": "http://127.0.0.1:1"})
        registry_module.model_registry.add(record)
        registry_module.model_registry.save()
        self.assertTrue(registry_module.model_registry.path.is_file())
        self.assertIn(str(self.root), str(registry_module.model_registry.path))


class ModelManagerCase(IsolatedCase):
    """调度器：假适配器验证单飞、引用计数、淘汰、扫描与报错。"""

    def setUp(self) -> None:
        super().setUp()
        # 同一用例类共用一个临时根目录：先清掉上一个用例留下的权重文件，
        # 否则新登记会被磁盘上的老文件直接判成「已就绪」。
        shutil.rmtree(self.root / ".resources" / "models", ignore_errors=True)
        self.registry = ModelRegistry(self.root / "models" / f"{self._testMethodName}.json")
        self.settings = settings_module.ModelSettings()
        self.settings.runtime["max_resident"] = 1
        self.manager = ModelManager(self.registry, self.settings)
        self.adapters: list[FakeAdapter] = []
        self._original = manager_module.build_adapter

        def factory(record, settings):
            adapter = FakeAdapter(record, settings)
            self.adapters.append(adapter)
            return adapter

        manager_module.build_adapter = factory

    def tearDown(self) -> None:
        manager_module.build_adapter = self._original
        self.manager.shutdown()
        super().tearDown()

    def _local(self, name: str, capability: str = "chat") -> ModelRecord:
        record = self.manager.add_local(name, capabilities=(capability,), files=("model.gguf",))
        folder = local_dir(record.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.gguf").write_bytes(b"gguf")
        record.sync_files()
        record.state = STATE_READY if record.files else record.state
        self.manager.update(record)
        return record

    def test_add_local_and_external(self) -> None:
        local = self._local("甲")
        self.assertEqual(local.kind, KIND_LOCAL)
        self.assertEqual(local.state, STATE_READY)
        external = self.manager.add_external("乙", base_url="http://127.0.0.1:9/", model="m")
        self.assertEqual(external.api["base_url"], "http://127.0.0.1:9")
        self.assertEqual(external.state, STATE_READY)
        self.assertEqual(len(self.manager.list_models(kind=KIND_LOCAL)), 1)
        self.assertEqual(self.manager.capabilities(), ("chat",))

    def test_acquire_release_and_refcount(self) -> None:
        record = self._local("甲")
        lease = self.manager.acquire(record.id)
        self.assertEqual(lease.model_id, record.id)
        self.assertEqual(lease.capability, "chat")
        self.assertEqual(self.manager.loaded(), (record.id,))
        self.assertEqual(self.adapters[-1].started, 1)
        self.assertEqual(lease.invoke("chat", {"input": "hi"})["echo"], {"input": "hi"})
        second = self.manager.acquire(record.id)
        lease.close()
        self.assertEqual(self.manager.loaded(), (record.id,))  # 还有人用着
        second.close()
        self.assertEqual(self.manager.loaded(), (record.id,))  # release 不卸载
        self.assertTrue(self.manager.unload(record.id))
        self.assertEqual(self.adapters[-1].stopped, 1)
        self.assertEqual(self.manager.loaded(), ())
        lease.close()  # 幂等
        self.assertFalse(self.manager.unload(record.id))

    def test_lease_invoke_after_unload_raises(self) -> None:
        from dm_plugin.lib.model.errors import ModelError

        record = self._local("甲")
        lease = self.manager.acquire(record.id)
        self.manager.unload(record.id)
        with self.assertRaises(ModelError):
            lease.invoke("chat", {})
        lease.close()

    def test_eviction_by_max_resident(self) -> None:
        first = self._local("甲")
        second = self._local("乙")
        lease = self.manager.acquire(first.id)
        lease.close()
        other = self.manager.acquire(second.id)
        self.assertEqual(self.manager.loaded(), (second.id,))
        self.assertEqual(self.adapters[0].stopped, 1)  # 最久未用的被淘汰
        other.close()

    def test_acquire_by_capability_and_errors(self) -> None:
        from dm_plugin.lib.model.errors import ModelError

        record = self._local("甲", capability="embedding")
        lease = self.manager.acquire(capability="embedding")
        self.assertEqual(lease.model_id, record.id)
        lease.close()
        lease = self.manager.acquire(task="embedding")
        self.assertEqual(lease.model_id, record.id)
        lease.close()
        with self.assertRaises(ModelError):
            self.manager.acquire(model_id="local/没有这个")
        with self.assertRaises(ModelError):
            self.manager.acquire(capability="ocr")

    def test_draft_local_model_rejected(self) -> None:
        from dm_plugin.lib.model.errors import ModelError

        record = self.manager.add_local("草稿")
        self.assertEqual(record.state, STATE_DRAFT)
        with self.assertRaises(ModelError):
            self.manager.acquire(record.id)

    def test_scan_directory_registers_once(self) -> None:
        folder = self.root / "拖进来的" / "某个模型"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "weights.gguf").write_bytes(b"abc")
        (folder / "config.json").write_text("{}", encoding="utf-8")
        found = self.manager.scan_directory(self.root / "拖进来的", capabilities=("chat",))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].source["provider"], "local")
        self.assertEqual(found[0].state, STATE_READY)
        self.assertIn("weights.gguf", found[0].files)
        again = self.manager.scan_directory(self.root / "拖进来的", capabilities=("chat",))
        self.assertEqual([item.id for item in again], [item.id for item in found])
        self.assertEqual(len(self.manager.list_models()), 1)

    def test_remove_deletes_files_when_asked(self) -> None:
        record = self._local("甲")
        folder = local_dir(record.id)
        self.assertTrue(folder.is_dir())
        self.assertTrue(self.manager.remove(record.id, delete_files=True))
        self.assertFalse(folder.exists())
        self.assertEqual(len(self.manager.list_models()), 0)

    def test_sweep_idle_unloads(self) -> None:
        record = self._local("甲")
        lease = self.manager.acquire(record.id)
        lease.close()
        self.settings.runtime["idle_unload_sec"] = 0
        self.assertEqual(self.manager.sweep_idle(), 0)  # 0 = 关闭
        self.settings.runtime["idle_unload_sec"] = -1
        self.assertEqual(self.manager.sweep_idle(), 0)
        self.assertEqual(self.manager.loaded(), (record.id,))


class ModelProviderCase(IsolatedCase):
    """`model.open` 接口面：页面与其它插件都从这里进来。"""

    def setUp(self) -> None:
        super().setUp()
        shutil.rmtree(self.root / ".resources" / "models", ignore_errors=True)
        self.registry = ModelRegistry(self.root / "models" / f"{self._testMethodName}.json")
        self.manager = ModelManager(self.registry, settings_module.ModelSettings())
        self.adapters: list[FakeAdapter] = []
        self._original = manager_module.build_adapter
        manager_module.build_adapter = lambda record, settings: self.adapters.append(FakeAdapter(record, settings)) or self.adapters[-1]
        self.ctx = FakeCtx()
        self.api = provider_module.ModelOpenApi(self.ctx, self.manager)

    def tearDown(self) -> None:
        manager_module.build_adapter = self._original
        self.manager.shutdown()
        super().tearDown()

    def test_api_surface(self) -> None:
        external = self.api.add_external("云端甲", base_url="http://127.0.0.1:8", model="m", capabilities=("chat",))
        self.assertEqual(self.api.model_by_id(external.id).name, "云端甲")
        self.assertEqual([item.id for item in self.api.list_models(kind=KIND_EXTERNAL)], [external.id])
        self.assertEqual(self.api.capabilities(), ("chat",))
        self.assertEqual(self.api.loaded(), ())
        result = self.api.invoke(model_id=external.id, task="chat", payload={"input": "hi"})
        self.assertEqual(result["record"], external.id)
        # invoke 只是把引用计数还回去，模型本身仍常驻（等 LRU 或空闲卸载）
        self.assertEqual(self.api.loaded(), (external.id,))
        lease = self.api.acquire(model_id=external.id)
        self.assertEqual(self.api.loaded(), (external.id,))
        lease.close()
        self.assertTrue(self.api.unload_all() >= 0)
        self.assertEqual(self.api.running_info(), {})
        self.assertTrue(self.api.save())
        self.api.reload()
        self.assertIsNotNone(self.api.model_by_id(external.id))

    def test_api_update_and_remove(self) -> None:
        record = self.api.add_local("本地甲", capabilities=("chat",), files=("model.gguf",))
        folder = local_dir(record.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.gguf").write_bytes(b"x")
        record.sync_files()
        record.name = "本地甲改名"
        self.api.update(record)
        self.assertEqual(self.api.model_by_id(record.id).name, "本地甲改名")
        self.assertTrue(self.api.remove(record.id))
        self.assertIsNone(self.api.model_by_id(record.id))

    def test_requirements_accepts_gpu_twin(self) -> None:
        """模型写 CPU 版 profile、机器只装了 GPU 版时，也算装好（孪生回退）。"""
        from dm_plugin.lib.model import runtime as runtime_module

        record = self.api.add_local("本地孪生", capabilities=("chat",), files=("m.gguf",))
        record.runtime = {"profile": "llama-cpp"}
        record.files = ["m.gguf"]
        self.api.update(record)

        profile = types.SimpleNamespace(name="llama.cpp（GPU）", id="llama-cpp-gpu")
        with mock.patch.object(runtime_module, "resolve_id", lambda pid: "llama-cpp-gpu"):
            with mock.patch.object(runtime_module, "installed", lambda pid: pid == "llama-cpp-gpu"):
                with mock.patch.object(runtime_module, "profile_of", lambda pid, ctx=None: profile):
                    gap = self.api.requirements(record.id)
        self.assertTrue(gap["ok"], gap)
        self.assertEqual(gap["profile"], "llama-cpp")
        self.assertEqual(gap["profile_effective"], "llama-cpp-gpu")
        self.assertIn("llama.cpp（GPU）", gap["hint"])

    def test_requirements_caches_runtime_probe(self) -> None:
        """`installed()` 会起子进程跑 python --version：同一 profile 只问一次。"""
        from dm_plugin.lib.model import runtime as runtime_module

        record = self.api.add_local("本地缓存", capabilities=("chat",), files=("m.gguf",))
        record.runtime = {"profile": "llama-cpp"}
        record.files = ["m.gguf"]
        self.api.update(record)

        calls: list[str] = []

        def fake_installed(pid):
            calls.append(pid)
            return False

        with mock.patch.object(runtime_module, "resolve_id", lambda pid: pid):
            with mock.patch.object(runtime_module, "installed", fake_installed):
                self.api.requirements(record.id)
                self.api.requirements(record.id)
        self.assertEqual(calls, ["llama-cpp"])
        self.api.forget_runtime_cache()
        with mock.patch.object(runtime_module, "resolve_id", lambda pid: pid):
            with mock.patch.object(runtime_module, "installed", fake_installed):
                self.api.requirements(record.id)
        self.assertEqual(calls, ["llama-cpp", "llama-cpp"])

    def test_download_queues_missing_weights(self) -> None:
        """一键下载：把 source 里声明、盘上还没有的文件排进模型页的下载队列。"""
        record = self.api.add_local("本地缺权重", capabilities=("chat",))
        record.source = {"repo": "org/repo", "files": ["a.gguf", "b.gguf"]}
        record.files = []
        self.api.update(record)

        jobs: list[tuple] = []

        class FakeQueue:
            def enqueue(self, model_id, urls, target, **kwargs):
                jobs.append((model_id, tuple(urls), str(target), kwargs))

        self.api.attach_downloads(lambda: FakeQueue())
        with mock.patch(
            "dm_plugin.lib.model.download.resolve_urls",
            lambda base, mirrors, repo, name, revision: [f"https://example.invalid/{name}"],
        ):
            result = self.api.download(record.id)
        self.assertTrue(result["ok"], result)
        self.assertEqual(sorted(result["queued"]), ["a.gguf", "b.gguf"])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0][0], record.id)
        self.assertTrue(jobs[0][1][0].startswith("https://example.invalid/"))

        external = self.api.add_external("云端乙", base_url="http://127.0.0.1:8", model="m", capabilities=("chat",))
        refused = self.api.download(external.id)
        self.assertFalse(refused["ok"])
        self.assertIn("外部模型", refused["hint"])

    def test_download_without_model_page_says_so(self) -> None:
        record = self.api.add_local("本地没模型页", capabilities=("chat",))
        record.source = {"repo": "org/repo", "files": ["c.gguf"]}
        record.files = []
        self.api.update(record)
        self.api.attach_downloads(None)
        result = self.api.download(record.id)
        self.assertFalse(result["ok"])
        self.assertIn("模型页", result["hint"])

    def test_requirements_groups_missing_files(self) -> None:
        """缺口按「权重 / 配置 / 分词器」分组说清楚：transformers 的模型目录是成套的。"""
        from dm_plugin.lib.model import runtime as runtime_module

        record = self.api.add_local("本地成套", capabilities=("chat",))
        record.source = {
            "repo": "org/clip",
            "files": [
                "pytorch_model.bin",
                "config.json",
                "preprocessor_config.json",
                "tokenizer.json",
                "vocab.json",
                "merges.txt",
            ],
        }
        record.files = []
        record.runtime = {"profile": "llama-cpp"}
        self.api.update(record)

        with mock.patch.object(runtime_module, "resolve_id", lambda pid: pid):
            with mock.patch.object(runtime_module, "installed", lambda pid: True):
                gap = self.api.requirements(record.id)
        self.assertFalse(gap["ok"])
        text = gap["missing"][0]
        self.assertIn("缺模型文件：权重（pytorch_model.bin）", text)
        self.assertIn("配置（config.json、preprocessor_config.json）", text)
        self.assertIn("分词器（tokenizer.json、vocab.json、merges.txt）", text)

    def test_job_done_syncs_record_and_refreshes(self) -> None:
        """下载任务收尾要让记录认盘上的权重（否则一直是「未填充」，还得手动换权重）。"""
        from PyQt6.QtWidgets import QApplication, QWidget

        self.app = QApplication.instance() or QApplication([])
        # 模型页 import 界面工具库：测试里补挂 `dm_plugin.builtin.lib.ui` 命名空间
        ui_pkg = "dm_plugin.builtin.lib.ui"
        if ui_pkg not in sys.modules:
            ui_module = types.ModuleType(ui_pkg)
            ui_module.__path__ = [
                str(Path(__file__).resolve().parents[2] / "plugins" / "builtin.lib.ui"),
                str(Path(__file__).resolve().parents[2] / "plugins" / "builtin.lib.ui" / ".plugin"),
            ]
            sys.modules[ui_pkg] = ui_module
            setattr(sys.modules["dm_plugin.builtin.lib"], "ui", ui_module)
        from dm_plugin.lib.model.ui.page import ModelPage

        record = self.api.add_local("本地下载完", capabilities=("embedding",))
        record.source = {"repo": "org/clip", "files": ["a.bin", "b.json"]}
        record.files = []
        self.api.update(record)

        folder = local_dir(record.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "a.bin").write_bytes(b"x")

        refreshed: list[str] = []

        class FakeApi:
            def model_by_id(self, model_id):
                return record if model_id == record.id else None

            def update(self, item):
                return item

        host = QWidget()
        host._api = FakeApi()
        host._downloads = types.SimpleNamespace(active=lambda: 0)
        host._console_info = lambda *a, **k: None
        host._console_error = lambda *a, **k: None
        host.refresh = lambda: refreshed.append("refresh")

        ModelPage._on_job_done(host, types.SimpleNamespace(model_id=record.id, state="done"))
        self.assertIn("a.bin", record.files)
        # 只缺非关键 sidecar（b.json）时不算「文件不完全」：各仓库的 sidecar 名字不一，
        # 把它们都算「缺」会让 whisper 那种 `vocabulary.txt` / `vocabulary.json` 永远不就绪。
        self.assertEqual(record.state, STATE_READY)
        self.assertEqual(refreshed, ["refresh"])

        # 下载只落下一部分时要能报出还缺哪些文件（加载前会据此拦住，给出可读提示）
        from dm_plugin.lib.model.ui.page import _missing_model_files

        record.source = {"repo": "org/clip", "files": ["a.bin", "config.json"]}
        record.files = ("a.bin",)
        self.assertEqual(_missing_model_files(record), ("config.json",))
        record.files = ("a.bin", "config.json")
        self.assertEqual(_missing_model_files(record), ())

        # 仓库清单里「还需要的必需文件」：配置 / 分词器 / 权重本体都要，README 之类不要
        from dm_plugin.lib.model.ui.page import _sidecar_files

        entries = [
            {"path": "pytorch_model.bin", "size": 600},
            {"path": "config.json", "size": 1},
            {"path": "tokenizer.json", "size": 2},
            {"path": "README.md", "size": 3},
        ]
        self.assertEqual(
            _sidecar_files(entries, {"pytorch_model.bin"}),
            [("config.json", 1), ("tokenizer.json", 2)],
        )

        # 列仓库文件要跟下载源一致：选镜像时不能去打官方 API（官方连不上就列不出文件）
        from dm_plugin.lib.model.download import hub as hub_module

        self.assertEqual(
            hub_module._api_base(
                types.SimpleNamespace(
                    download_base="https://hf-mirror.com", base_url="https://huggingface.co"
                )
            ),
            "https://hf-mirror.com",
        )
        self.assertEqual(hub_module._api_base(None), hub_module.HF_BASE_URL)

        # 「目录型后端」才需要 config.json 一整套；自己的 gguf（llama_cpp）不需要
        from dm_plugin.lib.model.ui.page import _needs_directory

        self.assertTrue(_needs_directory(types.SimpleNamespace(runtime={"backend": "transformers"})))
        self.assertFalse(_needs_directory(types.SimpleNamespace(runtime={"backend": "llama_cpp"})))

    def test_templates_download_contract(self) -> None:
        """预定义模板的下载清单要能「一次下全」，避免再出现下载不全的模板。

        - 每个模板至少要有一个权重来源（`file` 或 `files`）；
        - 目录型后端（transformers / faster_whisper / sentence_transformers / diffusers）
          必须带 `config.json` 与至少一个权重后缀文件；
        - 不允许清单里出现重复名（同一个落盘路径）——子目录同名文件要靠路径保真区分。
        """
        import json
        from pathlib import Path as _Path

        root = _Path(__file__).resolve().parents[2]
        payload = json.loads(
            (root / "plugins" / "lib.model" / ".data" / "model_list.json").read_text("utf-8")
        )
        items = payload.get("items") or []
        self.assertTrue(items, "模板清单读不到")
        weights = (".bin", ".safetensors", ".gguf", ".onnx", ".pt", ".pth", ".msgpack", ".h5")
        for item in items:
            source = item.get("source") or {}
            backend = str((item.get("runtime") or {}).get("backend") or "")
            names = [str(name) for name in (source.get("files") or [])]
            single = str(source.get("file") or "")
            label = str(item.get("id") or "?")
            self.assertTrue(single or names, f"{label}：既没有 file 也没有 files")
            lowered = [name.replace("\\", "/").lstrip("./").lower() for name in names]
            self.assertEqual(len(lowered), len(set(lowered)), f"{label}：清单里有重复文件名")
            if backend in ("transformers", "faster_whisper", "sentence_transformers", "diffusers"):
                self.assertIn("config.json", lowered, f"{label}：目录型后端必须带 config.json")
                self.assertTrue(
                    any(_Path(name).suffix.lower() in weights for name in lowered),
                    f"{label}：目录型后端必须带权重文件",
                )


class ModelLogCase(IsolatedCase):
    """模型日志：一个模型一个文件（老版本的时间戳文件也认），删模型连日志一起清。"""

    def test_model_log_file_is_single(self) -> None:
        from dm_plugin.lib.model import paths

        path = paths.model_log_file("local/demo")
        self.assertEqual(path.parent, paths.logs_dir())
        self.assertEqual(path.name, "local-demo.log")
        self.assertEqual(paths.model_log_files("local/demo"), [])

    def test_clear_model_logs_takes_new_and_legacy(self) -> None:
        from dm_plugin.lib.model import paths

        stable = paths.model_log_file("local/demo")
        legacy = paths.logs_dir() / "local-demo-20260101-010101.log"
        other = paths.logs_dir() / "local-demo-extra-20260101-010101.log"  # 别的模型，不许误删
        for item in (stable, legacy, other):
            item.write_text("x", encoding="utf-8")
        self.assertEqual(paths.model_log_files("local/demo")[-1], stable)
        removed = paths.clear_model_logs("local/demo")
        self.assertIn(stable, removed)
        self.assertIn(legacy, removed)
        self.assertFalse(stable.exists())
        self.assertFalse(legacy.exists())
        self.assertTrue(other.exists())

if __name__ == "__main__":
    unittest.main()
