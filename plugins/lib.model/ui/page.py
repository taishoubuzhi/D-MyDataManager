"""模型管理页：本地 / 外部模型登记、下载队列、运行环境与设置。"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk.data import human_size

from dm_plugin.builtin.lib.ui.plugin import (
    ScrollPageTemplate,
    caption,
    check_box,
    clear_layout,
    combo_box,
    confirm,
    empty_state,
    form_row,
    line_edit,
    list_view,
    primary_button,
    progress_bar,
    push_button,
    release_widget,
    spin_box,
    status_label,
    strong_label,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
    widget_column,
    widget_row,
)

from ..constants import BACKEND_LABELS, PAGE_TITLE, PLUGIN_ID, STATE_INCOMPLETE, STATE_READY
from ..paths import clear_model_logs, download_dir, local_dir, local_root, model_log_file, model_log_files
from ..plugin import data_templates
from ..record import WEIGHT_SUFFIXES, ModelRecord
from ..settings import DOWNLOAD_SOURCES, GITHUB_SOURCES, PIP_MIRRORS, github_prefixes, load_settings, pip_mirror_url
from .cards import ModelCard
from .dialogs import (
    CleanupWeightsDialog,
    CompleteRuntimesDialog,
    DeleteModelDialog,
    ExternalModelDialog,
    LocalModelDialog,
    ModelLogDialog,
    ReplaceWeightsDialog,
)

__all__ = ["ModelPage"]

_SUBTITLE = "本地权重与外部接口统一登记：其他插件按 id 或能力取用，用到才加载，不用就卸载。"
_CARD_WIDTH = 300
#: 「日志」弹窗最多显示这么多字符（日志只留最新一份，正常不会超）
_LOG_VIEW_LIMIT = 200_000
#: 下载任务的终态（与 downloader 里的状态串一致；这里不导入是为了页面不被下载模块拖累）
_FINAL_JOB_STATES = ("done", "error", "cancelled")


def _expected_files(record) -> tuple[str, ...]:
    """按仓库信息，这条模型应该有哪几个文件（和 `_download()` 用的是同一套来源）。"""
    source = record.source or {}
    names = [
        str(item).strip().strip("/") for item in (source.get("files") or []) if str(item).strip()
    ]
    if not names:
        single = str(source.get("file") or "").strip().strip("/")
        names = [single] if single else []
    return tuple(names)


def _missing_model_files(record) -> tuple[str, ...]:
    """盘上还缺哪些**关键**文件——网络失败时下载可能只落下一部分，用户要能看见缺什么。

    只把「权重本体 + `config.json`」当成硬要求：其它 sidecar（分词器 / 词表 / 子目录里的
    配置）各仓库命名不一（例如 whisper 仓库是 `vocabulary.json` 而不是 `vocabulary.txt`），
    把它们也算作「缺」会让状态永远停在「文件不完全」（用户 m02143 的 whisper）。按相对路径比对，
    子目录里的同名文件（如 `1_Pooling/config.json`）不会被根目录的同名文件顶掉。
    """
    expected = _expected_files(record)
    if not expected:
        return ()
    have = {str(name).replace("\\", "/").lstrip("./").lower() for name in record.files}
    missing: list[str] = []
    for name in expected:
        key = str(name).replace("\\", "/").lstrip("./").lower()
        if key in have:
            continue
        if Path(key).suffix in WEIGHT_SUFFIXES or key == "config.json":
            missing.append(name)
    return tuple(missing)


#: 目录型后端：靠同目录的 config.json / 分词器 / 词表一起工作。
#: 单文件后端（`llama_cpp` / `piper` 等）不需要这些，用户拿自己的 gguf / onnx 建模型也能加载。
_DIRECTORY_BACKENDS = ("transformers", "faster_whisper", "sentence_transformers", "diffusers")


def _needs_directory(record) -> bool:
    """这条模型是不是「必须有一整套文件」的目录型后端。"""
    backend = str((record.runtime or {}).get("backend") or "")
    return backend in _DIRECTORY_BACKENDS


def _sidecar_files(entries, skip: set[str]) -> list[tuple[str, int]]:
    """仓库清单里还需要下载的必需文件（配置 / 分词器 / 词表 / 权重本体）。

    `skip` 是盘上已有或已经入队的文件名（小写）；`entries` 是 `hub_file_list()` 的结果。
    """
    wanted: list[tuple[str, int]] = []
    for item in entries:
        raw = str(item.get("path") or "").replace("\\", "/")
        if not raw:
            continue
        name = Path(raw).name
        if name.lower() in skip:
            continue
        suffix = Path(name).suffix.lower()
        if suffix in _WEIGHT_SIDECAR or suffix in WEIGHT_SUFFIXES:
            wanted.append((raw, int(item.get("size") or 0)))
    return wanted


def _absent_files(record) -> tuple[str, ...]:
    """严格清单：`source.files` 里**所有**不在盘上的名字（连 sidecar 一起）。

    和 `_missing_model_files()`（只看权重 + `config.json`，给状态判定用）分工不同：
    「点下载要不要去仓库核对」看的是这套清单是否齐 —— 部分文件下载失败时它必须非空，
    否则就会出现「明明缺文件却提示已完整」（用户 m02234）。
    """
    expected = _expected_files(record)
    if not expected:
        return ()
    have = {str(name).replace("\\", "/").lstrip("./").lower() for name in record.files}
    return tuple(
        name for name in expected if str(name).replace("\\", "/").lstrip("./").lower() not in have
    )


def _broken_json_files(record) -> list[str]:
    """模型目录里坏掉的 JSON（网络中断时可能把错误页写成了 config.json）。"""
    import json as _json

    base = record.local_path() if hasattr(record, "local_path") else None
    if base is None:
        return []
    broken: list[str] = []
    for name in record.files:
        if not str(name).lower().endswith(".json"):
            continue
        try:
            _json.loads((base / name).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            broken.append(str(name))
    return broken


class _EventsControl:
    """兜底开关：老版 runtime 没有 `Control` 时用（只置位，由 runtime 的轮询线程收子进程）。"""

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._pause = threading.Event()

    def reset(self) -> None:
        self._cancel.clear()
        self._pause.clear()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()

    def pause(self) -> None:
        self._pause.set()

    def clear_pause(self) -> None:
        self._pause.clear()

    def should_cancel(self) -> bool:
        return self._cancel.is_set()

    def should_pause(self) -> bool:
        return self._pause.is_set()

    def bind(self, process) -> None:
        return None

    def unbind(self, process) -> None:
        return None
_AUTO_DEVICE = "auto"
_AUTO_DEVICE_LABEL = "自动选择（按可用性挑最快的）"
_CPU_DEVICE = ("cpu", "CPU（处理器）")

#: 设备探测结果的进程内缓存：`import torch` 很慢，同一个进程只探一次；「检测设备」可强制重探。
_DEVICE_PROBE_CACHE: dict | None = None
_WEIGHT_FILTER = (
    "模型权重 (*.gguf *.safetensors *.bin *.onnx *.pt *.pth *.ckpt *.msgpack *.h5 *.tflite *.mlmodel *.npz);;所有文件 (*)"
)
#: 各后端认的权重后缀：换权重时拿它校验「这批文件和这条模型对不对得上」
_WEIGHT_FORMATS: dict[str, tuple[str, ...]] = {
    "llama_cpp": (".gguf",),
    "faster_whisper": (".bin",),
    "piper": (".onnx", ".json"),
    "rapidocr": (".onnx", ".bin", ".json", ".txt"),
    "diffusers": (".safetensors", ".bin", ".ckpt", ".json", ".txt"),
    "transformers": (".safetensors", ".bin", ".pt", ".pth", ".h5", ".tflite", ".mlmodel", ".npz", ".onnx"),
    "sentence_transformers": (".safetensors", ".bin", ".pt", ".pth", ".onnx"),
}
#: 配套文件（config / tokenizer / 词表）任何后端都收
_WEIGHT_SIDECAR = (".json", ".txt", ".model", ".tiktoken", ".vocab", ".bpe", ".spm")


class _Bridge(QObject):
    """把工作线程的结果搬回界面线程。"""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class _CardGrid(QWidget):
    """按宽度自动换列的卡片网格（复用卡片控件，不整页重建）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)
        self._cards: list[ModelCard] = []
        self._columns = 0

    def set_cards(self, cards: list[ModelCard]) -> None:
        self._cards = list(cards)
        self._relayout(self._column_count())

    def _column_count(self) -> int:
        width = max(self.width(), 480)
        return max(1, (width + 12) // (_CARD_WIDTH + 12))

    def _relayout(self, columns: int) -> None:
        columns = max(1, columns)
        if columns == self._columns and self._grid.count() == len(self._cards):
            return
        self._columns = columns
        for card in self._cards:
            self._grid.removeWidget(card)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 0)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout(self._column_count())


class ModelPage(ScrollPageTemplate):
    """模型管理页。"""

    jobsChanged = pyqtSignal()

    def __init__(self, ctx, api, parent: QWidget | None = None) -> None:
        super().__init__(PAGE_TITLE, _SUBTITLE, parent)
        self._ctx = ctx
        self._api = api
        self._settings = load_settings()
        self._device_options: list[tuple[str, str]] = [(_AUTO_DEVICE, _AUTO_DEVICE_LABEL), _CPU_DEVICE]
        self._device_probed = False
        #: 探测到的可用 CUDA：True 有、False 没有、None 还没探
        self._device_has_cuda: bool | None = None
        #: 「一键补全」的待装队列（按设备挑好的一套，一个个装）
        self._complete_queue: list[dict] = []
        #: 「一键补全」是不是挨个装（False = 并发装）
        self._complete_sequential = False
        self._program_env_cache: dict[str, str] = {}
        self._state_tooltip: dict[str, str] = {}
        self._probe_pending: dict[str, dict] = {}
        self._probe_job: str | None = None
        self._runtime_rows: dict[str, dict] = {}
        self._card_map: dict[str, ModelCard] = {}
        self._job_rows: dict[str, QWidget] = {}
        #: 正在安装的运行环境：id → {name, start, lines, state, active}，安装期间那一行渲染进度条
        self._installing: dict[str, dict] = {}
        #: 安装线程的暂停 / 取消开关：id → {cancel: Event, pause: Event}
        self._install_ctl: dict[str, object] = {}
        #: 已经弹过失败提示的下载 id，避免每 0.7 s 刷一条提示
        self._failed_notified: set[str] = set()
        #: 已经「下载完成」的通知过的任务：完成时同步记录并刷新卡片（每次都刷会打断用户）。
        self._done_notified: set[str] = set()
        self._downloads = None
        attach = getattr(api, "attach_downloads", None)
        if callable(attach):
            # 其它插件（自动标签 / 关键词）的「一键补全」要往这条队列里排权重下载。
            attach(self._download_manager)
        self._tasks: set[_Bridge] = set()
        self._loading_settings = False  # 回填设置时别触发「即改即存」的处理器
        self._leases: dict[str, object] = {}
        self.jobsChanged.connect(self._refresh_queue)
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._refresh_queue)
        self._install_timer = QTimer(self)
        self._install_timer.setInterval(500)
        self._install_timer.timeout.connect(self._tick_install)

        self.setAcceptDrops(True)
        self._build_models()
        self._build_queue()
        self._build_runtime()
        self._build_settings()
        self.refresh()
        self._recover_downloads()

    # ============================================================ 分区
    def _build_models(self) -> None:
        card, layout = self.add_section("模型", "一张卡片就是一条模型记录；本地模型可以是下载的，也可以是本地已有的权重目录。")
        bar, bar_layout = QWidget(card), None
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)
        bar_layout.addWidget(primary_button(bar, FluentIcon.ADD, "新建本地模型", lambda: self._new_local()))
        bar_layout.addWidget(primary_button(bar, FluentIcon.GLOBE, "新建外部模型", lambda: self._new_external()))
        bar_layout.addWidget(push_button(bar, "扫描目录", lambda: self._scan()))
        bar_layout.addWidget(push_button(bar, "选择权重文件", lambda: self._pick_files()))
        bar_layout.addWidget(push_button(bar, "清理未使用的权重", lambda: self._cleanup_weights()))
        bar_layout.addWidget(push_button(bar, "刷新", lambda: self.refresh()))
        bar_layout.addStretch(1)
        layout.addWidget(bar)

        self._grid = _CardGrid(card)
        layout.addWidget(self._grid)
        self._empty = empty_state(card, "还没有模型：点「新建本地模型」或「新建外部模型」；已有权重可以「扫描目录」「选择权重文件」，或直接拖进窗口。", icon=FluentIcon.ROBOT)
        layout.addWidget(self._empty)

    def _build_queue(self) -> None:
        card, layout = self.add_section("下载队列", "下载支持断点续传、镜像回退与 sha256 校验；校验不过会自动删掉重下。")
        head = QWidget(card)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(8)
        head_layout.addStretch(1)
        self._resume_all_button = push_button(head, "全部继续", lambda: self._resume_all_jobs())
        self._pause_all_button = push_button(head, "全部暂停", lambda: self._pause_all_jobs())
        self._cancel_all_button = push_button(head, "全部取消", lambda: self._cancel_all_jobs())
        self._retry_all_button = push_button(head, "重试失败", lambda: self._retry_all_jobs())
        for button in (
            self._resume_all_button,
            self._pause_all_button,
            self._cancel_all_button,
            self._retry_all_button,
        ):
            button.setEnabled(False)
            head_layout.addWidget(button)
        self._clear_jobs_button = push_button(head, "清空已结束", lambda: self._clear_finished_jobs())
        self._clear_jobs_button.setEnabled(False)
        head_layout.addWidget(self._clear_jobs_button)
        layout.addWidget(head)
        self._queue_layout = QVBoxLayout()
        self._queue_layout.setContentsMargins(0, 0, 0, 0)
        self._queue_layout.setSpacing(6)
        layout.addLayout(self._queue_layout)
        self._queue_empty = caption(card, "当前没有下载任务。")
        layout.addWidget(self._queue_empty)

    def _build_runtime(self) -> None:
        card, layout = self.add_section("运行环境", "本地推理默认装在独立的 venv 里，不会污染程序自己的环境；装之前会先弹确认。")
        layout.addWidget(
            caption(
                card,
                "GPU 版本自带 CPU 版本，无需重复下载：有可用显卡时装带「CUDA / GPU」的那一份就行（没显卡会自动退回 CPU）。"
                "不打算用本地模型的，不必装运行环境。",
            )
        )
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        toolbar.addWidget(push_button(card, "一键补全", lambda: self._complete_runtimes()))
        self._runtime_toolbar_hint = caption(card, "")
        toolbar.addWidget(self._runtime_toolbar_hint, 1)
        layout.addLayout(toolbar)
        self._runtime_layout = QVBoxLayout()
        self._runtime_layout.setContentsMargins(0, 0, 0, 0)
        self._runtime_layout.setSpacing(6)
        layout.addLayout(self._runtime_layout)
        self._runtime_log = None
        self._fill_runtime()

    def _build_settings(self) -> None:
        card, layout = self.add_section(
            "设置",
            "下载走哪个地址由「下载源（模型）」决定，GitHub 上的资源（含装运行环境的轮子）"
            "按「下载源（GitHub）」取址；这一区每一项都是即改即用，改完立刻存盘，不用再点保存。"
            "密钥只写本地配置文件 .configs/models.json，日志里会打码。",
        )
        self.proxy_edit = line_edit(card, placeholder="代理（可留空），例如 http://127.0.0.1:7890")
        self.proxy_edit.editingFinished.connect(self._on_proxy_changed)
        self.concurrent_box = spin_box(card, value=2, minimum=1, maximum=12, on_change=self._on_concurrent_changed)
        self.max_resident_box = spin_box(card, value=1, minimum=1, maximum=8, on_change=self._on_max_resident_changed)
        self.idle_box = spin_box(
            card,
            value=1800,
            minimum=0,
            maximum=24 * 3600,
            step=60,
            suffix="秒",
            on_change=self._on_idle_changed,
        )
        self.device_box = combo_box(card, on_change=self._on_device_changed)
        self.device_button = push_button(card, "检测设备", lambda: self._detect_devices(force=True))
        self._device_hint = caption(card, "打开页面时会自动检测这台机器的 CPU 与显卡型号。")
        device_column = widget_column(
            card,
            widget_row(card, self.device_box, self.device_button, stretches=(1, 0)),
            self._device_hint,
        )
        self.system_env_box = check_box(
            card,
            text="允许把依赖装进程序自己的 Python 环境（高级选项）",
            on_change=self._on_system_env_toggled,
        )

        # 运行环境安装源：pip 装依赖时用的 --index-url。profile 自带 index_url 的以 profile 为准。
        self.pip_mirror_box = combo_box(
            card,
            items=tuple(label for _key, label, _url in PIP_MIRRORS),
            data=tuple(key for key, _label, _url in PIP_MIRRORS),
            on_change=self._on_pip_mirror_changed,
        )
        self.pip_mirror_custom = line_edit(card, placeholder="自定义安装源，例如 https://pypi.example.com/simple")
        self.pip_mirror_custom.editingFinished.connect(self._save_pip_mirror)
        self._pip_mirror_hint = caption(card, "")
        mirror_column = widget_column(
            card,
            widget_row(card, self.pip_mirror_box, self.pip_mirror_custom, stretches=(0, 1)),
            self._pip_mirror_hint,
        )

        # GitHub 下载源：GitHub 上的资源（release 资产、raw 文件）直连官方还是套镜像前缀。
        self.github_box = combo_box(
            card,
            items=tuple(label for _key, label in GITHUB_SOURCES),
            data=tuple(key for key, _label in GITHUB_SOURCES),
            on_change=self._on_github_source_changed,
        )
        self.github_custom = line_edit(card, placeholder="自定义镜像前缀，例如 https://ghproxy.net")
        self.github_custom.editingFinished.connect(self._save_github_source)
        self._github_hint = caption(card, "")
        github_column = widget_column(
            card,
            widget_row(card, self.github_box, self.github_custom, stretches=(0, 1)),
            self._github_hint,
        )

        # 下载源：用官方总站、HF-Mirror 镜像，还是自己「添加下载路径」攒起来的地址。
        # 官方 / 镜像地址都是程序给的默认值，界面上只读展示，不给改（免得改乱了找不回来）。
        self.source_box = combo_box(
            card,
            items=tuple(label for _key, label in DOWNLOAD_SOURCES),
            data=tuple(key for key, _label in DOWNLOAD_SOURCES),
            on_change=self._on_download_source_changed,
        )
        self.source_custom_edit = line_edit(card, placeholder="自定义下载网址，例如 https://mirror.example.com")
        self.source_add_button = push_button(card, "添加下载路径", lambda: self._add_download_source())
        self.source_remove_button = push_button(card, "删除选中", lambda: self._remove_download_source())
        self.source_list = list_view(card, minimum_height=78)
        # 固定高度：不固定时 QListWidget 默认高度会把这一项撑得很高，下面留一大片空白。
        self.source_list.setFixedHeight(78)
        self.source_list.currentRowChanged.connect(lambda _row: self._sync_download_source_row())
        self._download_source_hint = caption(card, "")
        source_column = widget_column(
            card,
            # 排法与「安装源（pip）」一致：选择框与输入框同一行对齐，下面一行写当前会去取的地址
            widget_row(
                card,
                self.source_box,
                self.source_custom_edit,
                self.source_add_button,
                self.source_remove_button,
                stretches=(0, 1, 0, 0),
            ),
            self._download_source_hint,
            self.source_list,
            spacing=4,
        )

        # 这些行的控件是多行高块（选择框 + 提示 + 列表），标签必须贴行顶，
        # 否则默认的垂直居中会把标签顶到中间，看着跟选择项没对齐。
        tall_rows = {"下载源（模型）", "安装源（pip）", "下载源（GitHub）", "推理设备"}
        for label, widget in (
            ("代理", self.proxy_edit),
            ("下载源（模型）", source_column),
            ("安装源（pip）", mirror_column),
            ("下载源（GitHub）", github_column),
            ("下载并发", self.concurrent_box),
            ("同时常驻模型数", self.max_resident_box),
            ("空闲多久卸载", self.idle_box),
            ("推理设备", device_column),
            ("依赖安装", self.system_env_box),
        ):
            layout.addWidget(
                form_row(
                    card,
                    label,
                    widget,
                    label_width=150,
                    strong=True,
                    align_top=label in tall_rows,
                )
            )
        layout.addWidget(caption(card, "「同时常驻模型数」超出后会按最久没用到的顺序卸载，卸载等于杀掉进程，显存会真的还回去。"))
        self._load_settings_into_form()

    # ============================================================ 刷新
    def refresh(self) -> None:
        try:
            self._api.reload()
        except Exception as exc:  # pragma: no cover - 登记表损坏时不应该让页面崩
            toast_error(self, "读取模型登记表失败", str(exc))
        self._render_cards()
        self._fill_runtime()
        self._refresh_queue()

    def _render_cards(self) -> None:
        records = list(self._api.list_models())
        ids = {record.id for record in records}
        # 先按最新登记收掉不存在的卡片。这里千万不能提前把 _card_map 筛一遍：
        # 筛掉之后下面的释放循环再也遍历不到它们，删掉的卡片会留在网格上变成点不动的僵尸。
        for model_id, card in list(self._card_map.items()):
            if model_id not in ids:
                release_widget(card)
                del self._card_map[model_id]
        running = self._api.loaded()
        cards: list[ModelCard] = []
        for record in records:
            card = self._card_map.get(record.id)
            if card is None:
                card = ModelCard(record, self._grid)
                card.actionRequested.connect(self._on_action)
                self._card_map[record.id] = card
            else:
                card.setParent(self._grid)
            card.set_record(record, running=record.id in running, has_log=bool(model_log_files(record.id)))
            cards.append(card)
        self._grid.set_cards(cards)
        self._empty.setVisible(not records)

    def _refresh_queue(self) -> None:
        jobs = list(self._downloads.jobs()) if self._downloads is not None else []
        if not jobs:
            clear_layout(self._queue_layout)
            self._job_rows = {}
            self._failed_notified.clear()
            self._done_notified.clear()
            self._clear_jobs_button.setEnabled(False)
            for button in (
                self._resume_all_button,
                self._pause_all_button,
                self._cancel_all_button,
                self._retry_all_button,
            ):
                button.setEnabled(False)
            self._queue_empty.setVisible(True)
            self._timer.stop()
            return
        self._queue_empty.setVisible(False)
        self._clear_jobs_button.setEnabled(any(job.state in _FINAL_JOB_STATES for job in jobs))
        states = [job.state for job in jobs]
        self._resume_all_button.setEnabled("paused" in states)
        self._pause_all_button.setEnabled(any(item in ("running", "queued") for item in states))
        self._cancel_all_button.setEnabled(any(item in ("running", "queued", "paused") for item in states))
        self._retry_all_button.setEnabled("error" in states)
        if not self._timer.isActive():
            self._timer.start()
        alive: set[str] = set()
        for job in jobs:
            alive.add(job.id)
            row = self._job_rows.get(job.id)
            if row is None:
                row = self._build_job_row(job)
                self._queue_layout.addWidget(row)
                self._job_rows[job.id] = row
            self._update_job_row(row, job)
        for job_id in list(self._job_rows):
            if job_id not in alive:
                release_widget(self._job_rows.pop(job_id))
                self._failed_notified.discard(job_id)
        for job in jobs:
            if job.state == "error" and job.id not in self._failed_notified:
                self._failed_notified.add(job.id)
                self._announce_job_failure(job)
            if job.state == "done" and job.id not in self._done_notified:
                self._done_notified.add(job.id)
                self._on_job_done(job)

    def _on_job_done(self, job) -> None:
        """一个下载任务收尾：把盘上的权重同步进模型记录，队列空下来后刷新模型区。

        少了这一步记录还是「未填充」：`record.files` 要 `sync_files()` 之后才认盘上的文件，
        `state` 也得跟着从草稿变成就绪，否则还得手动「更换权重」才用得上。
        """
        model_id = str(getattr(job, "model_id", "") or "")
        record = self._api.model_by_id(model_id) if model_id else None
        if record is None:
            return
        try:
            record.sync_files()
        except Exception as exc:
            self._console_error(f"[下载权重] 同步盘上文件失败：{exc}")
        if record.files:
            # 只下到一部分不能显示「已就绪」：缺文件（按记录清单，或目录型后端缺 config.json）
            # 就是「文件不完全」。
            have = {
                str(name).replace("\\", "/").lstrip("./").lower() for name in record.files
            }
            gap = bool(_missing_model_files(record)) or (
                _needs_directory(record) and "config.json" not in have
            )
            record.state = STATE_INCOMPLETE if gap else STATE_READY
        try:
            self._api.update(record)
        except Exception as exc:
            self._console_error(f"[下载权重] 更新模型记录失败：{exc}")
        manager = self._downloads
        active = manager.active() if manager is not None else 0
        self._console_info(
            f"[下载权重] {record.name} 下载结束：盘上 {len(record.files)} 个文件"
            + ("" if active else "，模型区已刷新")
        )
        if not active:
            # 队列空了才谈「下完没下完」：中途每个文件完成都报一次会吓人。
            missing_files = _missing_model_files(record)
            if missing_files:
                shown = "、".join(missing_files[:4]) + ("…" if len(missing_files) > 4 else "")
                self._console_error(f"[下载权重] {record.name} 还没下完，还缺：{shown}")
                toast_warning(
                    self,
                    "还没下完",
                    f"还缺 {len(missing_files)} 个文件：{shown}；失败的任务可以再点一次「下载」补齐。",
                )
            broken = _broken_json_files(record)
            if broken:
                names = "、".join(broken[:3])
                self._console_error(f"[下载权重] 这些文件不是合法 JSON（可能没下全）：{names}")
                toast_warning(
                    self,
                    "有文件没下全",
                    f"{names} 的内容不像 JSON；删掉这条模型重新下载一次通常就好。",
                )
            self.refresh()

    def _resume_all_jobs(self) -> None:
        """一键继续：把队列里所有「已暂停」的任务重新排进去（`.part` 续传）。"""
        manager = self._downloads
        if manager is None:
            return
        resumed = 0
        for job in manager.jobs():
            if job.state == "paused":
                job.resume()
                resumed += 1
        self._console_info(f"[下载队列] 一键继续：{resumed} 个任务重新排队")
        toast_info(self, "已继续下载", f"{resumed} 个任务" if resumed else "没有暂停中的任务")
        self._refresh_queue()

    def _pause_all_jobs(self) -> None:
        """一键暂停：正在下载与排队的都停下，分片留着可续传。"""
        manager = self._downloads
        if manager is None:
            return
        count = int(manager.pause_all() or 0)
        self._console_info(f"[下载队列] 一键暂停：{count} 个任务")
        toast_info(self, "已暂停下载", f"{count} 个任务" if count else "没有正在下载的任务")
        self._refresh_queue()

    def _cancel_all_jobs(self) -> None:
        """一键取消：停掉所有未结束的任务，半成品分片一起删（权重文件本身不动）。"""
        manager = self._downloads
        if manager is None:
            return
        if not self.confirm(
            "全部取消下载？", "未结束的任务都会停下，已下载的分片会被删掉（已完成的权重文件不动）。"
        ):
            return
        count = len([job for job in manager.jobs() if job.state not in _FINAL_JOB_STATES])
        manager.cancel_all()
        self._console_info(f"[下载队列] 一键取消：{count} 个任务")
        toast_info(self, "已取消下载", f"{count} 个任务" if count else "没有可取消的任务")
        self._refresh_queue()

    def _build_job_row(self, job) -> QWidget:
        # 一行装不下：名字 / 状态 / 按钮摆第一行，进度条单独占第二行铺满整宽。
        # 早先全都挤在一行，右侧的按钮和状态会被卡片边缘裁掉。
        row = QWidget(self)
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        name = strong_label(row, job.label)
        name.setMinimumWidth(160)
        name.setWordWrap(True)
        name.setToolTip(job.label)
        state = status_label(row)
        state.setWordWrap(True)
        state.setToolTip("")
        head.addWidget(name, 3)
        head.addWidget(state, 5)
        row._job_id = job.id  # type: ignore[attr-defined]
        row._name = name  # type: ignore[attr-defined]
        row._state = state  # type: ignore[attr-defined]
        row._pause = push_button(row, "暂停", lambda job=job: job.pause())
        row._resume = push_button(row, "继续", lambda job=job: job.resume())
        row._cancel = push_button(row, "取消", lambda job=job: job.cancel())
        row._forget = push_button(row, "移除记录", lambda job_id=job.id: self._forget_job(job_id))
        row._retry = push_button(row, "重试", lambda job=job: self._retry_job(job))
        for button in (row._pause, row._resume, row._cancel, row._retry, row._forget):
            head.addWidget(button)
        bar = progress_bar(row)
        bar.setMinimumWidth(120)
        row._bar = bar  # type: ignore[attr-defined]
        outer.addLayout(head)
        outer.addWidget(bar)
        return row

    def _update_job_row(self, row: QWidget, job) -> None:
        row._name.setText(job.label)  # type: ignore[attr-defined]
        row._name.setToolTip(job.label)  # type: ignore[attr-defined]
        finished = job.state in _FINAL_JOB_STATES
        known = int(job.total_bytes) > 0
        if job.state == "running" and not known:
            # 总量还不知道（有些源不给 Content-Length）：忙等条胜过一直卡在 0%
            row._bar.setRange(0, 0)  # type: ignore[attr-defined]
        else:
            row._bar.setRange(0, 100)  # type: ignore[attr-defined]
            row._bar.setValue(int(job.progress * 100))  # type: ignore[attr-defined]
        parts = [job.state_label]
        done_text = human_size(job.done_bytes)
        if known:
            parts.append(f"{done_text} / {human_size(job.total_bytes)}（{job.progress * 100:.0f}%）")
        elif job.done_bytes:
            parts.append(f"已下载 {done_text}")
        detail = job.detail_label
        if detail:
            parts.append(detail)
        if job.error:
            parts.append(str(job.error))
        speed = f"{job.speed / 1024 / 1024:.2f} MB/s" if job.speed else ""
        if speed:
            parts.append(speed)
        if job.eta:
            parts.append(f"剩余 {job.eta:.0f}s")
        text = " · ".join(parts)
        row._state.setText(text)  # type: ignore[attr-defined]
        row._state.setToolTip(text)  # type: ignore[attr-defined]
        row._pause.setEnabled(job.state in ("running", "queued"))  # type: ignore[attr-defined]
        row._resume.setEnabled(job.state == "paused")  # type: ignore[attr-defined]
        row._cancel.setEnabled(job.state in ("running", "queued", "paused"))  # type: ignore[attr-defined]
        row._forget.setEnabled(finished)  # type: ignore[attr-defined]
        # 失败才出现「重试」：点它会把这条失败记录清掉、用同一组地址重新排队（不会下重复）。
        row._retry.setVisible(job.state == "error")  # type: ignore[attr-defined]

    def _retry_job(self, job) -> None:
        """重下一条失败的任务：先清掉这条失败记录（避免队列里留两条重复），再用同一组地址重排。"""
        manager = self._downloads
        if manager is None:
            return
        urls = list(getattr(job, "urls", []) or [])
        if not urls:
            toast_warning(self, "这条记录没有下载地址", "重新点卡片上的「下载」吧。")
            return
        manager.forget(job.id)
        try:
            manager.enqueue(
                job.model_id,
                urls,
                job.target,
                sha256=str(getattr(job, "sha256", "") or ""),
                total_bytes=int(job.total_bytes or 0),
                label=str(job.label or ""),
            )
        except Exception as exc:
            toast_error(self, "重试没排上", str(exc))
            self._refresh_queue()
            return
        self._console_info(f"[下载队列] 重试 {job.target.name}（旧的失败记录已清掉）")
        self._refresh_queue()

    def _retry_all_jobs(self) -> None:
        """一键重试：所有失败的任务各清掉旧记录再重排。"""
        manager = self._downloads
        if manager is None:
            return
        failed = [job for job in manager.jobs() if job.state == "error"]
        if not failed:
            toast_info(self, "没有失败的下载", "队列里没有失败的任务。")
            return
        for job in failed:
            self._retry_job(job)
        toast_info(self, "已重试失败的任务", f"{len(failed)} 条；失败记录已用新任务替代。")

    def _forget_job(self, job_id: str) -> None:
        manager = self._downloads
        if manager is None or not manager.forget(job_id):
            return
        self._failed_notified.discard(str(job_id))
        self._done_notified.discard(str(job_id))
        self._refresh_queue()

    def _clear_finished_jobs(self) -> None:
        manager = self._downloads
        if manager is None:
            return
        removed = manager.clear_finished()
        if removed:
            self._console_info(f"下载列表：清掉 {removed} 条已结束的记录（.part 保留，还能续传）")
            toast_info(self, "已清空", f"清掉 {removed} 条已结束的记录。")
        self._refresh_queue()

    def _announce_job_failure(self, job) -> None:
        """下载失败第一次出现时弹提示：失败原因不该只留在日志里。"""
        reason = str(job.error or "下载失败")
        self._console_error(f"下载失败：{job.label} —— {reason}")
        hint = ""
        if any(word in reason for word in ("连接", "timed out", "超时", "拒绝", "Unreachable")):
            hint = "（连不上下载源：检查网络 / 代理，或在「设置」里换镜像）"
        toast_error(self, "下载失败", f"{job.label}：{reason}{hint}")


    def _fill_runtime(self) -> None:
        clear_layout(self._runtime_layout)
        self._runtime_rows.clear()
        profiles = self._runtime_profiles()
        if not profiles:
            self._runtime_layout.addWidget(caption(self, "没有读到运行环境清单。"))
            return
        for profile in profiles:
            if str(profile.get("id") or "") in self._installing:
                self._runtime_layout.addWidget(self._build_installing_row(profile))
                continue
            row = QWidget(self)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            full_name = str(profile.get("name") or profile.get("id"))
            title = strong_label(row, full_name)
            # 环境名最长 588 px（sentence-transformers（…，CUDA torch）），窄宽度会截断；留宽折行展示，完整名字挂悬停提示
            title.setFixedWidth(300)
            title.setWordWrap(True)
            title.setToolTip(full_name)
            description = str(profile.get("description") or "")
            detail = caption(row, description)
            detail.setFixedWidth(300)
            detail.setToolTip(description)
            layout.addWidget(title)
            layout.addWidget(detail)
            state_text = self._profile_state(profile)
            key = str(profile.get("id") or "")
            state = status_label(row, state_text)
            # 行内只留短话（「缺 xxx 等 4 个包」），完整依赖清单挂悬停提示，免得每行都撑成三行高
            state.setMinimumWidth(150)
            state.setMaximumWidth(300)
            state.setWordWrap(True)
            state.setToolTip(self._state_tooltip.get(key, state_text))
            layout.addWidget(state)
            row_buttons = {
                "profile": profile,
                "state_text": state_text,
                "install": push_button(row, "安装", lambda p=profile: self._install_profile(p)),
                "uninstall": push_button(row, "卸载", lambda p=profile: self._uninstall_profile(p)),
                "log": push_button(row, "日志", lambda p=profile: self._show_profile_log(p)),
                "whl": push_button(row, "本地 whl…", lambda p=profile: self._install_wheels(p)),
            }
            row_buttons["whl"].setToolTip("用手上已有的 .whl 文件装这个运行环境（离线 / 内网用）")
            for name in ("install", "uninstall", "log", "whl"):
                layout.addWidget(row_buttons[name])
            layout.addStretch(1)
            self._runtime_layout.addWidget(row)
            self._runtime_rows[key] = row_buttons
        self._pump_probe()
        self._sync_runtime_buttons()
        try:
            # 行都建好了再看还差哪些（不用再跑一遍解释器探测）
            self._runtime_toolbar_hint.setText(self._complete_hint())
        except Exception:  # pragma: no cover - 提示文案不该拖垮刷新
            pass

    def _build_installing_row(self, profile: dict) -> QWidget:
        """安装中的那一行：进度条 / 已暂停 + 最后一行输出 + 暂停（继续）/ 取消 / 日志。"""
        key = str(profile.get("id") or "")
        info = self._installing.get(key) or {}
        state = str(info.get("state") or "running")
        paused = state == "paused"
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        full_name = str(profile.get("name") or key)
        title = strong_label(row, full_name)
        title.setFixedWidth(300)
        title.setWordWrap(True)
        title.setToolTip(full_name)
        layout.addWidget(title)
        if paused:
            state_widget: QWidget = status_label(row, "已暂停")
        else:
            state_widget = progress_bar(row, indeterminate=True)
        state_widget.setFixedWidth(200)
        layout.addWidget(state_widget)
        if paused:
            message_text = "已暂停，点「继续」接着装。"
        elif state == "pausing":
            message_text = "正在暂停…（pip 马上停）"
        elif state == "cancelling":
            message_text = "正在取消…（pip 马上停，随后删掉半成品）"
        else:
            message_text = "安装中…"
        message = status_label(row, message_text)
        message.setFixedWidth(320)
        message.setWordWrap(True)
        tip = "pip 的输出一行行回报在这里；暂停不会白装——缓存和半成品 venv 都留着。"
        source_note = str(info.get("source") or "")
        if source_note:
            tip = f"来源：{source_note}\n{tip}"
        message.setToolTip(tip)
        layout.addWidget(message)
        pause_button = push_button(
            row,
            "继续" if paused else "暂停",
            (lambda p=profile: self._resume_profile(p)) if paused else (lambda k=key: self._pause_profile(k)),
        )
        pause_button.setToolTip(
            "接着装剩下的依赖" if paused else "先停下 pip；缓存和半成品 venv 会留着，之后可以继续"
        )
        cancel_button = push_button(row, "取消", lambda k=key: self._cancel_install(k))
        cancel_button.setToolTip("停掉 pip 并删掉半成品运行环境，回到未安装")
        log_button = push_button(row, "日志", lambda p=profile: self._show_profile_log(p))
        log_button.setEnabled(self._profile_log_path(profile) is not None)
        if state in ("pausing", "cancelling"):
            # 已经喊停了：按钮锁住，免得连点
            pause_button.setEnabled(False)
            cancel_button.setEnabled(False)
        for button in (pause_button, cancel_button, log_button):
            layout.addWidget(button)
        layout.addStretch(1)
        self._runtime_rows[key] = {
            "profile": profile,
            "state_text": message_text if not paused else "已暂停",
            "install": pause_button,
            "uninstall": cancel_button,
            "log": log_button,
            "install_state": message,
        }
        self._tick_install()
        return row

    def _tick_install(self) -> None:
        """每 0.5 s 把「安装中」那行的文案换成最新一行 pip 输出 + 已用时长（暂停的不动）。"""
        if not self._installing:
            self._install_timer.stop()
            return
        for key, info in list(self._installing.items()):
            if str(info.get("state") or "running") != "running":
                continue
            row = self._runtime_rows.get(key) or {}
            label = row.get("install_state")
            if label is None:
                continue
            lines = info.get("lines") or []
            last = str(lines[-1]).strip() if lines else ""
            seconds = int(time.monotonic() - float(info.get("start") or 0.0))
            text = f"安装中… {seconds}s"
            if last:
                text = f"{text} · {last}"
            label.setText(text[:200])

    def _install_control(self, key: str):
        """安装的停止开关（`runtime.Control`）：没有就现建；暂停后继续也走同一个对象。

        页面点「暂停 / 取消」时直接调它的 `pause()` / `cancel()`——pip 子进程当场被
        收掉，不用后台线程每 0.2 秒轮询。老版 runtime 没有 `Control` 时退回只置位的兜底开关。
        """
        control = self._install_ctl.get(key)
        if control is None:
            module = self._runtime_module()
            maker = getattr(module, "Control", None) if module is not None else None
            control = maker() if callable(maker) else _EventsControl()
            self._install_ctl[key] = control
        return control

    def _pause_profile(self, key: str) -> None:
        """暂停：只让安装线程收掉 pip 子进程，半成品 venv 与 pip 缓存都留着。"""
        info = self._installing.get(key)
        if info is None or str(info.get("state") or "") in ("pausing", "paused", "cancelling"):
            return
        self._install_control(key).pause()
        info["state"] = "pausing"
        self._console_info(f"正在暂停运行环境安装：{info.get('name') or key}")
        self._fill_runtime()

    def _resume_profile(self, profile: dict) -> None:
        """继续：清掉暂停位再跑一遍 `ensure()`，已有的 venv 与 pip 缓存直接复用。"""
        key = str(profile.get("id") or "")
        control = self._install_ctl.get(key)
        if control is not None:
            control.clear_pause()
        self._install_profile(profile, resume=True)

    def _cancel_install(self, key: str) -> None:
        """取消：停掉 pip 并删掉半成品 venv 与日志（装好的那种不会被误删——它没有安装行）。"""
        control = self._install_ctl.get(key)
        if control is not None:
            control.cancel()
        info = self._installing.get(key)
        if info is None:
            self._install_ctl.pop(key, None)  # 线程早结束了：开关也别留着
            self._drop_half_install(key)  # 直接收尾
            return
        info["state"] = "cancelling"
        self._console_info(f"正在取消运行环境安装：{info.get('name') or key}")
        self._fill_runtime()

    def _drop_half_install(self, key: str) -> None:
        """取消 / 失败后的收尾：删掉半成品 venv 与 requirements，日志一起清。"""
        module = self._runtime_module()
        if module is not None and hasattr(module, "discard"):
            try:
                module.discard(key, logs=True)
            except Exception as exc:
                self._console_error(f"清理运行环境「{key}」失败：{exc}")
        clear = getattr(module, "clear_pending", None) if module is not None else None
        if callable(clear):
            try:
                clear(key)
            except Exception as exc:
                self._console_error(f"清理运行环境「{key}」的安装标记失败：{exc}")
        self._program_env_cache.clear()
        self._state_tooltip.clear()

    def _runtime_profiles(self) -> list[dict]:
        module = self._runtime_module()
        if module is not None:
            try:
                found = module.profiles(self._ctx)
            except Exception as exc:
                toast_warning(self, "读不到运行环境清单", str(exc))
            else:
                if found:
                    return [
                        {
                            "id": item.id,
                            "name": item.name,
                            "description": item.description,
                            "packages": list(item.packages),
                            "index_url": item.index_url,
                            "size_hint": item.size_hint,
                        }
                        for item in found
                    ]
        return self._template_list("runtime_profiles", "profiles")

    def _template_list(self, key: str, field: str) -> list[dict]:
        """模板 JSON 形状是 `{version, note, <field>: [...]}`，这里只取列表部分。"""
        payload = data_templates(self._ctx, key)
        items = payload.get(field) if isinstance(payload, dict) else payload
        return [dict(item) for item in (items or []) if isinstance(item, dict)]

    def _runtime_module(self):
        try:
            from .. import runtime
        except Exception:
            return None
        return runtime

    def _profile_state(self, profile: dict) -> str:
        module = self._runtime_module()
        if module is None:
            return "运行环境模块未就绪"
        try:
            key = str(profile.get("id") or "")
            found = module.profile_of(key, self._ctx)
            if found is None:
                return "未登记"
            if module.installed(key):
                return "已安装"
            # 上次装到一半程序就没了（强杀 / 断电 / 关窗口）：盘上留着半个 venv 和
            # 「正在安装」标记。既不是装好了，也不该按「手工放置」算——点安装能接着装。
            pending = getattr(module, "interrupted", None)
            if callable(pending):
                try:
                    if pending(key):
                        return "未完成（上次安装中断）"
                except Exception:
                    pass
            # 用户自己往运行环境目录里塞过依赖（没走页面安装）：没有完成标记但有东西，
            # 那就按「已安装」算——否则会出现「显示未安装、卸载却是亮的」这种自相矛盾。
            checker = getattr(module, "has_dir", None)
            try:
                manual = bool(checker(key)) if callable(checker) else Path(module.venv_dir(key)).exists()
            except Exception:
                manual = False
            if manual:
                return "已安装（手工放置，没找到完成标记）"
            if not getattr(self._settings, "allow_system_env", False):
                return "未安装"
            if callable(pending):
                try:
                    if pending(key, system=True):
                        return "未完成（上次安装中断）"
                except Exception:
                    pass
            return self._program_env_text(profile)
        except Exception as exc:
            return f"状态未知（{exc}）"

    # ============================================================ 动作
    def _on_action(self, model_id: str, action: str) -> None:
        record = self._api.model_by_id(model_id)
        if record is None:
            toast_warning(self, "找不到这条模型", model_id)
            return
        handler = {
            "download": self._download,
            "replace": self._replace_weights,
            "load": self._load,
            "unload": self._unload,
            "test": self._test,
            "log": self._show_log,
            "open_dir": self._open_dir,
            "edit": self._edit,
            "delete": self._delete,
        }.get(action)
        if handler is None:
            return
        handler(record)

    def _new_local(self) -> None:
        dialog = LocalModelDialog(self.window(), templates=self._template_list("model_list", "models"), profiles=self._runtime_profiles())
        if not dialog.exec():
            return
        values = dialog.values()
        if not values["name"]:
            toast_warning(self, "还没有起名字", "给这张卡片填个名字再保存。")
            return
        record = self._api.add_local(
            values["name"],
            capabilities=values["capabilities"],
            description=values["description"],
            source=values["source"],
            runtime=values["runtime"],
        )
        self.refresh()
        toast_success(self, "已添加本地模型", "点卡片上的「下载」拉权重，或把权重放进目录后点「扫描目录」。")
        if not record.files:
            return

    def _new_external(self) -> None:
        dialog = ExternalModelDialog(self.window(), templates=self._template_list("api_templates", "templates"))
        if not dialog.exec():
            return
        values = dialog.values()
        if not values["name"] or not values["base_url"]:
            toast_warning(self, "信息不全", "名称和接口地址是必填的。")
            return
        self._api.add_external(
            values["name"],
            base_url=values["base_url"],
            model=values["model"],
            capabilities=values["capabilities"],
            description=values["description"],
            adapter=values["adapter"],
            api_key_ref=values["api_key_ref"],
            params=values["params"],
        )
        if values["api_key"] and values["api_key_ref"]:
            self._settings.set_secret(values["api_key_ref"], values["api_key"])
            self._settings.save()
        self.refresh()
        toast_success(self, "已添加外部模型", "点卡片上的「测试」看看能不能调通。")

    def _edit(self, record: ModelRecord) -> None:
        if record.is_local:
            dialog = LocalModelDialog(self.window(), templates=self._template_list("model_list", "models"), profiles=self._runtime_profiles(), record=record)
            if not dialog.exec():
                return
            values = dialog.values()
            record.name = values["name"] or record.name
            record.capabilities = list(values["capabilities"])
            record.description = values["description"]
            record.source = values["source"]
            record.runtime = values["runtime"]
        else:
            dialog = ExternalModelDialog(self.window(), templates=self._template_list("api_templates", "templates"), record=record)
            if not dialog.exec():
                return
            values = dialog.values()
            record.name = values["name"] or record.name
            record.capabilities = list(values["capabilities"])
            record.description = values["description"]
            record.api["adapter"] = values["adapter"]
            record.api["base_url"] = values["base_url"].rstrip("/")
            record.api["model"] = values["model"]
            record.api["api_key_ref"] = values["api_key_ref"]
            record.api["params"] = values["params"]
            if values["api_key"] and values["api_key_ref"]:
                self._settings.set_secret(values["api_key_ref"], values["api_key"])
                self._settings.save()
        self._api.update(record)
        self.refresh()

    def _deletable_dir(self, record: ModelRecord) -> Path | None:
        """能删的权重目录：只有 `.resources/models/local/<id>`。

        「扫描目录」登记进来的模型留在用户原来的目录（`record.source["path"]`），
        删登记绝不动它——否则等于替用户删了他自己的文件。
        """
        if not record.is_local:
            return None
        if str((record.source or {}).get("path") or "").strip():
            return None
        from ..paths import local_dir

        return local_dir(record.id)

    def _delete(self, record: ModelRecord) -> None:
        folder = self._deletable_dir(record)
        note = ""
        if not record.is_local:
            note = "外部模型只有登记信息，删掉登记就完了。"
        elif folder is None:
            note = "这条是「扫描目录」登记的，权重留在你原来的目录里，删登记不会动它。"
        dialog = DeleteModelDialog(self.window(), name=record.name, folder=folder, note=note)
        if not dialog.exec():
            return
        # 权重一律不跟着登记删：删错几个 G 得重下，要清理走「清理未使用的权重」
        self._api.remove(record.id, delete_files=False)
        # 模型没了，日志也别留着（否则 logs/ 里一直躺着删掉的模型的日志）
        clear_model_logs(record.id)
        self._leases.pop(record.id, None)
        self.refresh()
        toast_info(self, "已删除", f"{record.name}（权重文件保留）")

    def _show_log(self, record: ModelRecord) -> None:
        """给用户看这条模型最新的运行日志（一个模型只有一份）。"""
        files = model_log_files(record.id)
        path = files[-1] if files else model_log_file(record.id)
        text = ""
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                toast_warning(self, "读不到日志", str(exc))
        if len(text) > _LOG_VIEW_LIMIT:
            text = "…（只显示最后一部分）\n" + text[-_LOG_VIEW_LIMIT:]
        dialog = ModelLogDialog(self.window(), name=record.name, path=path if path.is_file() else None, text=text)
        dialog.exec()

    def _open_dir(self, record: ModelRecord) -> None:
        folder = record.local_path()
        if folder is None:
            return
        folder.mkdir(parents=True, exist_ok=True)
        try:
            from app.sdk import ui as sdk_ui

            sdk_ui.reveal(str(folder))
        except Exception as exc:
            toast_warning(self, "打不开目录", f"{folder}（{exc}）")

    def _load(self, record: ModelRecord) -> None:
        # 先按盘上的真实文件刷新一次：记录里的 files 过期会导致「加载说不完整、下载说完整」
        # 这种自相矛盾的判断（用户 m02143）。
        try:
            record.sync_files()
        except Exception:
            pass
        # 文件没下全时别去加载：transformers / faster-whisper 报的是英文内部错误，
        # 用户看不出「其实是没下完」，这里先把缺什么说清楚。
        missing = _missing_model_files(record)
        if missing:
            shown = "、".join(missing[:4]) + ("…" if len(missing) > 4 else "")
            toast_warning(self, "还有文件没下完", f"先点卡片上的「下载」补齐：{shown}")
            return
        # 记录里只写了一个权重、目录里没有 config.json 时也一样加载不了（transformers 会报
        # 「Should have a model_type key」，faster-whisper 会报找不到词表）：先补齐再加载。
        # 只对目录型后端这么做——用户拿自己的 gguf / onnx 建的单文件模型没有 config.json 也能跑。
        have = {str(name).replace("\\", "/").lstrip("./").lower() for name in record.files}
        if have and "config.json" not in have and _needs_directory(record):
            toast_warning(
                self,
                "还缺配套文件",
                "这条模型只有权重、没有 config.json（还有分词器等）：点卡片上的「下载」会自动补齐。",
            )
            return

        self._console_stage(f"[加载] 开始加载「{record.name}」…")
        toast_info(self, "开始加载", f"{record.name}：正在加载到内存…")

        def work():
            return self._api.acquire(record.id, timeout=600)

        def done(lease) -> None:
            self._leases[record.id] = lease
            self._render_cards()
            self._console_info(f"[加载] 「{record.name}」已加载")
            toast_success(self, "已加载", record.name)

        def failed(message: str) -> None:
            self._console_error(f"[加载] 「{record.name}」失败：{message}")
            toast_error(self, "加载失败", str(message)[:200])

        self._run_async(work, done, on_failed=failed)

    def _unload(self, record: ModelRecord) -> None:
        lease = self._leases.pop(record.id, None)
        if lease is not None:
            try:
                lease.close()
            except Exception:
                pass
        self._api.unload(record.id)
        self._render_cards()
        toast_info(self, "已卸载", record.name)

    def _test(self, record: ModelRecord) -> None:
        """测一下这条模型能不能跑：按能力挑任务，并把「开始了」写进控制台与弹窗。

        以前一律发 `chat`：CLIP 这类没有对话头的模型会把文本当成「图片源」，报
        `Incorrect image source …`（用户 m02103）。这里按能力选任务，媒体类任务先说明
        测试需要什么输入。
        """
        capabilities = tuple(getattr(record, "capabilities", ()) or ())
        # 只有这几个任务能「拿一段文本直接测」；其余（图像分类 / 视觉 / OCR / 语音）需要
        # 图片或音频，硬塞文本正是 CLIP 报 `Incorrect image source … Got ping` 的原因
        # （用户 m02143）。这类模型只验证「能不能加载」。
        text_tasks = ("chat", "completion", "embedding")
        media_caps = {"classify", "vision", "ocr", "asr", "tts"}
        task = next((name for name in text_tasks if name in capabilities), "")
        only_load = not task and any(name in media_caps for name in capabilities)
        if only_load:
            kinds = "/".join(sorted(media_caps & set(capabilities)))
            self._console_stage(f"[测试] 「{record.name}」是「{kinds}」类模型，只验证能否加载…")
            toast_info(
                self,
                "这条模型要图片 / 音频输入",
                f"{record.name}：这里只验证它能不能加载；正式用要给它对应的{ '图片' if {'vision','ocr','classify'} & set(capabilities) else '音频' }。",
            )
        else:
            self._console_stage(f"[测试] 开始测试「{record.name}」（任务：{task or 'chat'}）…")
            toast_info(self, "开始测试", f"{record.name}：正在加载并调用（任务 {task or 'chat'}）…")

        def work():
            lease = self._api.acquire(record.id, timeout=120)
            try:
                if only_load:
                    return {"loaded": True, "note": "只验证了加载：这类模型需要图片 / 音频输入"}
                payload = (
                    {"messages": [{"role": "user", "content": "ping"}]}
                    if task in ("chat", "completion")
                    else {"input": "ping"}
                )
                return lease.invoke(task or "chat", payload, timeout=60)
            finally:
                lease.close()

        def done(result) -> None:
            self._console_info(f"[测试] 「{record.name}」通过：{str(result)[:120]}")
            toast_success(self, "测试通过", str(result)[:160])

        def failed(message: str) -> None:
            self._console_error(f"[测试] 「{record.name}」失败：{message}")
            toast_error(self, "测试失败", str(message)[:200])

        self._run_async(work, done, self._render_cards, on_failed=failed)

    def _download(self, record: ModelRecord, *, replace: bool = False) -> None:
        manager = self._download_manager()
        if manager is None:
            toast_warning(self, "下载模块未就绪", "插件里的 download 模块没加载成功，先手动放权重再扫描。")
            return
        # 「下载」只补缺的文件（已有的不动）；要换一整组权重走卡片上的「更换权重」按钮。
        try:
            record.sync_files()  # 先按盘上真实文件刷新，判定才和加载侧一致
        except Exception:
            pass
        source = record.source or {}
        repo = str(source.get("repo") or "")
        revision = str(source.get("revision") or "main")
        if not repo:
            folder = local_dir(record.id)
            folder.mkdir(parents=True, exist_ok=True)
            toast_info(self, "这条模型没有仓库信息", f"把权重文件放进 {folder} 后点「扫描目录」。")
            return
        names = [str(item).strip().strip("/") for item in (source.get("files") or []) if str(item).strip()]
        if not names:
            single = str(source.get("file") or "").strip().strip("/")
            names = [single] if single else []
        if not names:
            toast_warning(self, "没有文件名", "模板没给文件名：把仓库里的文件名填进「仓库内文件」，或改用「更换权重」挑文件。")
            return
        custom_urls = [str(item).strip() for item in (source.get("urls") or []) if str(item).strip()]
        from ..download import hub_file_list, resolve_urls
        from ..download.downloader import describe_error

        folder = local_dir(record.id)

        def build_plan(
            overwrite: set[str],
            wanted: list[str],
            sizes: dict[str, int],
        ) -> tuple[list[tuple[list[str], Path, int]], list[str], list[str]]:
            """算这次要下哪些文件；`overwrite` 里的已存在文件先删掉再下（覆盖式，不会存副本）。"""
            plan: list[tuple[list[str], Path, int]] = []
            bad: list[str] = []
            existing: list[str] = []
            for name in wanted:
                target = folder / Path(name)
                if not replace and target.is_file():
                    if str(name) not in overwrite:
                        existing.append(str(name))
                        continue
                    try:
                        target.unlink()  # 覆盖式下载：先删旧的，下载器就不会加 `_1` 存副本
                    except OSError as exc:
                        bad.append(f"{target.name}（删不掉旧文件：{exc}）")
                        continue
                try:
                    link = list(custom_urls) or resolve_urls(
                        self._settings.download_base,
                        self._settings.download_mirrors,
                        repo,
                        name,
                        revision,
                    )
                except Exception as exc:
                    bad.append(f"{name}（{describe_error(exc)}）")
                    continue
                if not link:
                    bad.append(f"{name}（没有可用的下载地址）")
                    continue
                size = sizes.get(str(name), 0) or (
                    int(record.size_bytes or 0) if len(wanted) == 1 else 0
                )
                plan.append((link, target, size))
            return plan, bad, existing

        # 先用本地信息算一遍（**不联网**）：文件都齐了就只提示一句，不再弹覆盖确认。
        local_plan, _local_bad, existing = build_plan(set(), list(names), {})
        have_names = {
            str(name).replace("\\", "/").lstrip("./").lower() for name in record.files
        }
        # 目录型后端只下到权重（没有 config.json）时不算「文件完整」：必须去仓库核对补齐，
        # 否则就会出现「加载说不完整、点下载说完整」的矛盾（用户 m02143 的 whisper）。
        directory_gap = _needs_directory(record) and "config.json" not in have_names
        if (
            not local_plan
            and existing
            and not replace
            and not _absent_files(record)
            and not directory_gap
        ):
            toast_info(self, "文件已经完整了", "要换一组权重用卡片上的「更换权重」。")
            return
        overwrite: set[str] = set()
        if existing and not replace:
            # 已经有的文件不悄悄跳过：说清楚再问一句，确认了才覆盖（否则只补缺的）。
            shown = "、".join(existing[:4]) + ("…" if len(existing) > 4 else "")
            if confirm(
                self, "盘上已经有这些文件", f"{shown}。要覆盖下载吗？点「否」就只下缺的文件。"
            ):
                overwrite = set(existing)
        # 「点一下就得进队列」：本地能算出来的先排队（界面马上看到任务），仓库清单在后台核对。
        local_plan, local_bad, _again = build_plan(overwrite, list(names), {})
        if replace:
            self._drop_weights(record)  # 重新下载：先把旧的清掉，免得新旧文件混在一起
        queued_now = self._enqueue_plan(manager, record, local_plan, local_bad)
        self._refresh_queue()
        self._console_info(
            "[下载权重] 下载源："
            + str(self._settings.download_base or "（未设置）")
            + "；镜像："
            + ("、".join(self._settings.download_mirrors) or "无")
        )
        if queued_now:
            toast_info(self, "已加入下载队列", f"{queued_now} 个文件；正在核对仓库里还缺哪些。")
        elif local_bad:
            toast_error(self, "没能加入下载队列", "；".join(local_bad[:3]))

        def work():
            """后台查仓库清单，把「这套模型该有的文件」算齐，再决定这次下哪些（不卡界面）。"""
            sizes: dict[str, int] = {}
            wanted = list(names)
            if not custom_urls:
                try:
                    entries = hub_file_list(repo, revision, self._settings)
                except Exception as exc:
                    self._console_info(
                        f"[下载权重] 没能列仓库文件，这次只按记录里写的文件下载：{describe_error(exc)}"
                    )
                    entries = []
                if entries:
                    have = {
                        str(item).replace("\\", "/").lstrip("./").lower() for item in wanted
                    }
                    sizes = {
                        str(item.get("path") or ""): int(item.get("size") or 0)
                        for item in entries
                    }
                    wanted = wanted + [name for name, _size in _sidecar_files(entries, have)]
            return build_plan(overwrite, wanted, sizes)

        def done(result) -> None:
            plan, bad, _again = result
            added = self._enqueue_plan(manager, record, plan, bad)
            self._refresh_queue()
            if added:
                self._console_info(f"[下载权重] 后台核对后又补了 {added} 个文件进队列")
                toast_info(self, "补上了配套文件", f"仓库里还缺的 {added} 个文件已排进队列。")
            elif not bad:
                self._console_info("[下载权重] 后台核对完毕：盘上文件与仓库清单一致，没有要补的")
                toast_info(self, "核对完毕", "盘上文件与仓库清单一致，没有要补的。")
            if bad:
                self._console_error("[下载权重] 没排上的文件：" + "；".join(bad[:5]))
                toast_error(self, "有文件没排上", "；".join(bad[:3]))

        self._run_async(work, done, on_failed=self._hub_list_failed)

    def _enqueue_plan(self, manager, record, plan, bad) -> int:
        """把计划里这批文件排进下载队列，返回排上的条数（排不上的写进 `bad`）。"""
        queued = 0
        for link, target, size in plan:
            try:
                # 仓库里有子目录文件（如 `1_Pooling/config.json`）：先建好目录，保真落盘。
                target.parent.mkdir(parents=True, exist_ok=True)
                manager.enqueue(
                    record.id,
                    link,
                    target,
                    sha256=str(record.sha256 or ""),
                    total_bytes=size,
                    label=f"{record.name} · {target.name}",
                )
            except Exception as exc:
                bad.append(f"{target.name}（{exc}）")
                continue
            queued += 1
            self._console_info(f"[下载权重] {target.name} ← {link[0]}（存到 {target}）")
        return queued

    def _hub_list_failed(self, message: str) -> None:
        """列仓库文件失败：明确说「这次只排了记录里写明的文件」，别让用户以为下全了。"""
        self._console_error(f"[下载权重] 没能列仓库文件，配套文件这次没补：{message}")
        toast_warning(
            self,
            "没能列仓库文件",
            "这次只排了记录里写明的文件；config / 分词器这类配套文件可能要再点一次「下载」，"
            "或换一个能连上的下载源。",
        )

    def _pick_files(self) -> None:
        start = str(local_root())
        chosen, _filter = QFileDialog.getOpenFileNames(self, "选择本地模型文件（会复制进插件权重目录）", start, _WEIGHT_FILTER)
        if chosen:
            self._import_paths(list(chosen))

    def _scan(self) -> None:
        start = str(local_root())
        chosen = QFileDialog.getExistingDirectory(self, "选择权重目录（也可以直接把目录拖进窗口）", start)
        if chosen:
            self._import_paths([chosen])

    def _import_paths(self, paths: list[str]) -> None:
        added = 0
        for raw in paths:
            path = Path(raw)
            try:
                if path.is_dir():
                    added += len(self._api.scan_directory(path))
                elif path.is_file():
                    record = self._api.add_local(path.stem, capabilities=(), description="从拖入的文件导入")
                    target = local_dir(record.id)
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target / path.name)
                    record.sync_files()
                    record.state = STATE_READY if record.files else record.state
                    self._api.update(record)
                    added += 1
            except Exception as exc:
                toast_error(self, "导入失败", f"{path.name}：{exc}")
        self.refresh()
        if added:
            toast_success(self, "已登记", f"新增 / 更新了 {added} 条模型记录。")
        else:
            toast_info(self, "没有找到可用的权重", "目录里需要有 .gguf / .safetensors / .onnx / .bin 这类文件。")

    # ============================================================ 权重维护
    def _replace_weights(self, record: ModelRecord) -> None:
        """权重已经有了：换一组磁盘上的文件，或者按仓库信息重新下载。"""
        folder = record.local_path()
        note = ""
        if str((record.source or {}).get("path") or "").strip():
            note = "这条是「扫描目录」登记的：换文件只是重新登记，不会动你原来那个目录里的东西。"
        dialog = ReplaceWeightsDialog(self.window(), name=record.name, files=record.files, folder=folder, note=note)
        if not dialog.exec():
            return
        mode, paths = dialog.choice()
        if mode == "files":
            self._use_weight_files(record, paths)
        elif mode == "download":
            self._download(record, replace=True)

    def _weights_fit(self, record: ModelRecord, paths: list[str]) -> tuple[bool, str]:
        """挑的文件和这条模型的类型对不对得上。

        配套文件（config / tokenizer / 词表）谁都收；权重本体按后端认后缀，
        对不上就回 False，调用方据此决定是清空旧权重还是按模板换后端。
        """
        backend = str(record.runtime.get("backend") or "")
        allowed = _WEIGHT_FORMATS.get(backend, ())
        bad: list[str] = []
        good = 0
        for raw in paths:
            suffix = Path(raw).suffix.lower()
            if suffix in _WEIGHT_SIDECAR or not allowed or suffix in allowed:
                good += 1
                continue
            bad.append(Path(raw).name)
        if bad:
            label = BACKEND_LABELS.get(backend, backend or "未标注")
            return False, f"{'、'.join(bad)} 不像「{label}」的权重（这一档认：{'、'.join(allowed) or '任意'}）"
        return (good > 0), "这些文件里没有能当权重用的。"

    def _template_for_suffixes(self, suffixes: set[str]) -> dict | None:
        """从本地模板里找一个能收这批后缀的条目（换权重时用来「自动匹配」）。"""
        wanted = {item for item in suffixes if item not in _WEIGHT_SIDECAR}
        if not wanted:
            return None
        for entry in self._template_list("model_list", "models"):
            runtime = entry.get("runtime") or {}
            allowed = set(_WEIGHT_FORMATS.get(str(runtime.get("backend") or ""), ()))
            if allowed and wanted <= allowed:
                return entry
        return None

    def _use_weight_files(self, record: ModelRecord, paths: list[str]) -> None:
        """把挑好的文件收成这条模型的权重；对不上就先清空或按模板改后端。"""
        if not paths:
            toast_warning(self, "还没有选文件", "先点「选择文件…」挑一组权重。")
            return
        fits, reason = self._weights_fit(record, paths)
        if not fits:
            suffixes = {Path(item).suffix.lower() for item in paths}
            template = self._template_for_suffixes(suffixes)
            if template is not None:
                runtime = template.get("runtime") or {}
                detail = (
                    f"{reason}\n\n"
                    f"模板「{template.get('name') or template.get('id')}」认这批文件"
                    f"（后端：{BACKEND_LABELS.get(str(runtime.get('backend') or ''), runtime.get('backend') or '未标注')}）。\n"
                    "要按这条模板改掉运行方式，然后收下这些文件吗？"
                )
                if not self.confirm(f"「{record.name}」的类型对不上", detail):
                    toast_info(self, "没有改动", "文件、模型登记都没动。")
                    return
                record.runtime = dict(runtime)
                caps = template.get("capabilities") or []
                if caps:
                    record.capabilities = tuple(str(item) for item in caps)
            else:
                detail = f"{reason}\n\n直接把这条模型现在的权重清空，再收下新文件吗？"
                if not self.confirm("清空原来的权重？", detail):
                    toast_info(self, "没有改动", "文件、模型登记都没动。")
                    return
            self._drop_weights(record)
        self._apply_weight_files(record, paths)

    def _apply_weight_files(self, record: ModelRecord, paths: list[str]) -> None:
        """复制新文件进权重目录，再清掉目录里没被收下的旧文件。"""
        target = record.local_path()
        if target is None:
            toast_warning(self, "这条模型没有权重目录", record.name)
            return
        scanned = bool(str((record.source or {}).get("path") or "").strip())
        try:
            target.mkdir(parents=True, exist_ok=True)
            keep = {Path(item).name for item in paths}
            if not scanned:
                # 扫描登记的目录是用户自己的：只登记，别往里写、也别删他的东西
                for raw in paths:
                    source_path = Path(raw)
                    if not source_path.is_file():
                        continue
                    destination = target / source_path.name
                    if source_path.resolve() != destination.resolve():
                        shutil.copy2(source_path, destination)
                self._prune_dir(target, keep)
            record.sync_files()
            record.state = STATE_READY if record.files else record.state
            self._api.update(record)
        except Exception as exc:
            toast_error(self, "换权重失败", str(exc))
            return
        self.refresh()
        self._console_info(f"[更换权重] {record.name}：现在的文件 = {'、'.join(record.files) or '（空）'}")
        toast_success(self, "权重已更换", f"{record.name}：{len(record.files)} 个文件。")

    def _drop_weights(self, record: ModelRecord) -> None:
        """清掉这条模型权重目录里的文件（「扫描目录」登记的绝不碰）。"""
        target = record.local_path()
        if target is None or str((record.source or {}).get("path") or "").strip():
            return
        if not target.is_dir():
            return
        self._prune_dir(target, set())
        record.sync_files()
        self._api.update(record)
        self._console_info(f"[更换权重] 已清空 {target}")

    def _prune_dir(self, target: Path, keep: set[str]) -> None:
        """删掉目录里不在 `keep` 里的文件，再收拾空目录（只删空的）。"""
        for item in sorted(target.rglob("*"), reverse=True):
            if item.is_file() and item.name not in keep:
                try:
                    item.unlink()
                except OSError:
                    pass
            elif item.is_dir():
                try:
                    item.rmdir()
                except OSError:
                    pass

    def _cleanup_candidates(self) -> list[dict]:
        """没人登记的权重目录 + 下载临时目录（正在下载的不算）。"""
        records = list(self._api.list_models())
        keep = {local_dir(item.id).name for item in records if item.is_local}
        busy = set()
        if self._downloads is not None:
            # `DownloadManager.active()` 返回的是条数，不是任务对象；正在跑的才占住目录。
            for job in self._downloads.jobs():
                if job.state in _FINAL_JOB_STATES:
                    continue
                busy.add(str(getattr(job, "model_id", "") or ""))
                busy.add(str(getattr(job, "target", "") or ""))
        entries: list[dict] = []
        for base, kind in ((local_root(), "权重目录"), (download_dir(), "下载临时目录")):
            try:
                children = sorted(item for item in base.iterdir() if item.is_dir())
            except OSError:
                continue
            for child in children:
                if child.name in keep or child.name in busy:
                    continue
                size = 0
                for item in child.rglob("*"):
                    if item.is_file():
                        try:
                            size += item.stat().st_size
                        except OSError:
                            pass
                entries.append(
                    {
                        "name": child.name,
                        "path": child,
                        "size": size,
                        "size_text": human_size(size) if size else "大小未知",
                        "kind": kind,
                    }
                )
        return entries

    def _cleanup_weights(self) -> None:
        """列出没人登记的权重目录与下载临时目录，勾选后删掉。"""
        entries = self._cleanup_candidates()
        if not entries:
            toast_info(self, "没有要清理的", "登记在用的权重目录都在名单里，下载临时目录也是空的。")
            return
        dialog = CleanupWeightsDialog(self.window(), entries=entries)
        if not dialog.exec():
            return
        chosen = dialog.selected()
        if not chosen:
            toast_info(self, "一项都没选", "什么都没删。")
            return
        failed: list[str] = []
        for entry in chosen:
            try:
                shutil.rmtree(entry["path"])
            except OSError as exc:
                failed.append(f"{entry['name']}（{exc}）")
                continue
            self._console_info(f"[清理权重] 已删除 {entry['path']}（{entry['size_text']}）")
        self.refresh()
        if failed:
            toast_warning(self, "有些没删掉", "、".join(failed)[:200])
        else:
            toast_success(self, "清理完成", f"删掉了 {len(chosen)} 项。")


    # ============================================================ 下载队列
    def _download_manager(self):
        if self._downloads is not None:
            return self._downloads
        try:
            from ..download import DownloadManager
        except Exception:
            return None
        self._downloads = DownloadManager(self._settings, on_change=self._notify_jobs)
        return self._downloads

    def _notify_jobs(self) -> None:
        self.jobsChanged.emit()

    def _recover_downloads(self) -> None:
        """进场时接上上次没下完的权重：异常退出（关窗口 / 断电）不该让几个 G 白下。"""
        manager = self._download_manager()
        if manager is None:
            return
        try:
            resumed = manager.resume_leftovers()
        except Exception as exc:
            self._console_error(f"恢复未完成的下载失败：{exc}")
            return
        if not resumed:
            return
        self._console_info(f"找到 {resumed} 个没下完的文件，已排回下载队列接着下")
        toast_info(self, "接着下载", f"上次有 {resumed} 个文件没下完，已排回队列续传。")
        self._refresh_queue()

    # ============================================================ 运行环境
    def _effective_index_url(self, found) -> str:
        """这次安装真正会用的源：profile 自带 > 页面选的安装源 > pip 默认。"""
        return (
            str(getattr(found, "index_url", "") or "").strip()
            or self._settings.index_url
            or "pip 默认源（pypi.org）"
        )

    # ============================================================ 一键补全
    def _complete_plan(self) -> list[dict]:
        """按设备挑一套：有可用显卡优先 GPU 版，没有就 CPU 版；装好的跳过。"""
        plan: list[dict] = []
        for profile in self._runtime_profiles():
            key = str(profile.get("id") or "")
            if key.endswith("-gpu"):
                continue  # GPU 版与 CPU 版二选一，下面按设备决定装哪一个
            picked = profile
            if self._device_has_cuda is True:
                twin = self._gpu_twin(profile)
                if twin is not None:
                    picked = twin
            if self._state_is_installed(self._profile_state(picked)):
                continue
            plan.append(picked)
        return plan

    def _complete_hint(self) -> str:
        """运行环境那一行右边的小字：告诉用户按当前设备还差什么。

        看的是刚建好的行状态，不再跑一遍探测。
        """
        if self._device_has_cuda is None:
            return "还不知道这台机器有没有可用的 GPU：先点上面的「检测设备」，再点「一键补全」。"
        missing: list[str] = []
        for row in self._runtime_rows.values():
            profile = row.get("profile") or {}
            key = str(profile.get("id") or "")
            if key.endswith("-gpu") and self._device_has_cuda is not True:
                continue  # 没有可用显卡时不看 GPU 版
            if self._state_is_installed(str(row.get("state_text") or "")):
                continue
            if self._device_has_cuda is True and not key.endswith("-gpu") and self._gpu_twin(profile) is not None:
                continue  # 有显卡：同一个 backend 只看 GPU 版装没装
            missing.append(str(profile.get("name") or key))
        if not missing:
            return "按这台机器的设备看，该装的都装好了。"
        return f"还差 {len(missing)} 个：" + "、".join(missing)

    def _complete_runtimes(self) -> None:
        """一键补全：按设备（有 GPU 优先 GPU 版）把没装的补上，装好的跳过。

        开始前问一句「并发装还是挨个装」，选择只影响这一轮的调度。
        """
        if self._runtime_module() is None:
            toast_warning(self, "运行环境模块未就绪", "插件里的 runtime 模块没加载成功。")
            return
        if self._device_has_cuda is None:
            toast_info(self, "先探测一下设备", "不知道有没有可用的显卡：点上面的「检测设备」，回来再点「一键补全」。")
            self._detect_devices()
            return
        plan = self._complete_plan()
        if not plan:
            toast_success(self, "运行环境已经齐了", "按这台机器的设备看，该装的都装好了。")
            return
        names = "、".join(str(item.get("name") or item.get("id")) for item in plan)
        has_cuda = bool(self._device_has_cuda)
        detail = (
            f"这台机器{'有可用的 GPU' if has_cuda else '没有可用的 GPU'}，所以"
            f"{'优先装 GPU 版（自带 CPU 内核）' if has_cuda else '装 CPU 版'}。\n"
            f"待补全（{len(plan)} 个）：{names}\n\n"
            "装好的会自动跳过；中途可以在对应那一行点「暂停」或「取消」。"
        )
        dialog = CompleteRuntimesDialog(self.window(), detail=detail, count=len(plan))
        if not dialog.exec():
            return
        self._complete_sequential = dialog.sequential()
        mode = "挨个装" if self._complete_sequential else "并发装"
        if self._installing:
            # 已经在装的那些不再排一次：`_install_profile` 会挡住重复的
            self._console_stage("一键补全", f"{mode}，待装 {len(plan)} 个（其中正在装的会跳过）：{names}")
        else:
            self._console_stage("一键补全", f"{mode}，待装 {len(plan)} 个：{names}")
        self._complete_queue = list(plan)
        self._pump_complete_queue()

    def _pump_complete_queue(self) -> None:
        """把「一键补全」排下的环境开起来。

        并发：一次全开（每个 profile 一条安装线程，互不等待）。
        挨个：一次只开一个，上一个收尾时会再调这里接下一个。
        """
        if self._complete_sequential and self._installing:
            return
        while self._complete_queue:
            self._install_profile(self._complete_queue.pop(0), auto=True)
            if self._complete_sequential:
                break

    def _warn_device_mismatch(self, profile: dict) -> bool:
        """装之前按设备提醒一声，让用户自己定要不要继续。

        - GPU 版碰上没有可用 CUDA 的设备：说清楚「装上也用不上 GPU」，可继续可取消；
        - CPU 版碰上能用 CUDA 的设备：说清楚「GPU 版自带 CPU 内核」，可继续可改装 GPU 版。

        返回 True 表示继续装这一份，False 表示这次不装了。
        """
        name = str(profile.get("name") or profile.get("id"))
        twin = self._gpu_twin(profile)
        if self._profile_is_gpu(profile):
            if self._device_has_cuda is False:
                return self.confirm(
                    "设备没有 GPU，无法使用 GPU 版本",
                    f"「{name}」要装的是 GPU 版依赖，但这台机器没检测到可用的 CUDA 设备。\n"
                    "装上去通常会自动退回 CPU 跑，体积还更大。\n\n"
                    "要继续装吗？取消的话可以回列表改选 CPU 版。",
                )
            return True
        if self._device_has_cuda is True and twin is not None:
            keep_cpu = self.confirm(
                "这台机器有可用的 GPU",
                f"「{name}」是 CPU 版，而这台机器的显卡能用 CUDA。\n"
                "GPU 版本的依赖自带 CPU 内核，装了 GPU 版就不用再装 CPU 版。\n\n"
                "要继续装 CPU 版吗？（选「否」可以改装 GPU 版）",
            )
            if keep_cpu:
                return True
            twin_name = str(twin.get("name") or twin.get("id"))
            if self.confirm(f"改装 GPU 版「{twin_name}」？", "接下来按 GPU 版清单再确认一遍。"):
                self._install_profile(twin)
            return False
        return True

    def _install_wheels(self, profile: dict) -> None:
        """用本地 .whl 文件装这个运行环境：挑文件 → 查一遍对不对 → 走同一条安装线程。"""
        module = self._runtime_module()
        if module is None:
            toast_warning(self, "运行环境模块未就绪", "稍后再试或手动 pip 安装。")
            return
        name = str(profile.get("name") or profile.get("id") or "")
        files, _selected = QFileDialog.getOpenFileNames(self, f"给「{name}」选 whl 文件", "", "Python 轮子 (*.whl)")
        if not files:
            return
        listed = "、".join(Path(item).name for item in files[:8])
        if len(files) > 8:
            listed += f" 等 {len(files)} 个文件"
        self._console_stage("本地 whl 安装", f"{name} ← 本地文件：{listed}")
        problems: list[str] = []
        checker = getattr(module, "check_wheels", None)
        if callable(checker):
            try:
                problems = [str(item) for item in checker(files)]
            except Exception as exc:
                self._console_error(f"检查 whl 文件失败：{exc}")
        if problems:
            self._console_error("whl 文件看着不对：" + "；".join(problems))
            if not self.confirm("这些 whl 文件看着不对", "\n".join(problems[:8]) + "\n\n确定还是要装吗？"):
                return
        self._install_profile(profile, wheels=list(files))

    def _install_profile(
        self, profile: dict, *, resume: bool = False, auto: bool = False, wheels: list[str] | None = None
    ) -> None:
        """装运行环境。

        `resume=True` 是「继续」：跳过确认和「已经装好了」的守卫；
        `auto=True` 是「一键补全」排好的队：确认已经点过，跳过确认与 GPU / CPU 提示；
        `wheels` 是本地 whl 清单：装的是这些文件（一律装进独立 venv），不是清单里的包。
        """
        module = self._runtime_module()
        if module is None:
            toast_warning(self, "运行环境模块未就绪", "稍后再试或手动 pip 安装。")
            if auto:
                self._pump_complete_queue()
            return
        key = str(profile.get("id") or "")
        if not resume and key in self._installing:
            # 并行安装：同一个运行环境不重复开线程
            toast_info(self, "这个运行环境正在装", f"{profile.get('name') or key}：等它装完，或者先点「取消」。")
            return
        try:
            found = module.profile_of(key, self._ctx)
        except Exception as exc:
            toast_error(self, "读不到这个运行环境", str(exc))
            if auto:
                self._pump_complete_queue()
            return
        if found is None:
            toast_warning(self, "未知运行环境", key)
            if auto:
                self._pump_complete_queue()
            return
        if not resume and not wheels and self._state_is_installed(self._profile_state(profile)):
            if not auto:
                toast_info(self, "已经装好了", f"{profile.get('name') or key}：不用再装一次。")
            else:
                self._pump_complete_queue()  # 队里这个已经装过了：跳过，接着下一个
            return
        packages = "、".join(str(item) for item in (profile.get("packages") or [])) or "（清单为空）"
        # 本地 whl 一律装进这个运行环境自己的 venv：不碰程序环境（和上面的开关无关）
        system_mode = bool(getattr(self._settings, "allow_system_env", False)) and not wheels
        target = module.system_python() if system_mode else module.venv_dir(key)
        index = self._effective_index_url(found)
        if not resume and not auto:
            if not wheels and not self._warn_device_mismatch(profile):
                return
            if wheels:
                listed = "\n".join(f"· {Path(item).name}" for item in wheels[:10])
                more = f"\n（还有 {len(wheels) - 10} 个没列出来）" if len(wheels) > 10 else ""
                detail = (
                    f"用这些本地文件装：\n{listed}{more}\n"
                    f"安装位置：{target}\n"
                    f"安装源（补依赖用）：{index}\n\n"
                    "装完这个运行环境就算装好了；缺的依赖 pip 会按上面的安装源去补。确定继续吗？"
                )
            elif system_mode:
                detail = (
                    f"需要安装：{packages}\n"
                    f"安装位置：程序自己的 Python 环境（{target}）\n"
                    f"安装源：{index}\n"
                    f"预计占用：{profile.get('size_hint') or '未知'}\n\n"
                    "这些包会装进程序自己的环境，和程序共用一套依赖，出问题要自己收拾（在本区点「卸载」可以卸掉）。"
                )
            else:
                detail = (
                    f"需要安装：{packages}\n"
                    f"安装位置：{target}\n"
                    f"安装源：{index}\n"
                    f"预计占用：{profile.get('size_hint') or '未知'}\n\n"
                    "安装会新建独立虚拟环境，不会动程序自己的环境；过程中那一行有「暂停」和「取消」。确定继续吗？"
                )
            if not self.confirm(f"安装运行环境「{profile.get('name') or key}」？", detail):
                return
            if system_mode:
                again = (
                    f"再确认一次：这些包会直接装进程序自己的环境（{target}）。\n"
                    "装进去之后程序的依赖会变多；之后在本区点「卸载」会把这些包从这个环境里卸掉。\n\n"
                    "确定继续吗？"
                )
                if not self.confirm("确认装进程序自己的环境？", again):
                    return

        control = self._install_control(key)
        control.reset()
        lines: list[str] = []
        stopped: list[str] = []

        def on_line(text: str) -> None:
            lines.append(str(text))

        name = str(profile.get("name") or key)

        def work():
            kwargs = {
                "on_line": on_line,
                "should_cancel": control.should_cancel,
                "should_pause": control.should_pause,
                "control": control,
                "index_url": self._settings.index_url,
                "github_prefixes": self._settings.github_prefixes,
            }
            try:
                if wheels:
                    return module.install_wheels(found, wheels, **kwargs)
                if system_mode:
                    return module.ensure_system(found, **kwargs)
                return module.ensure(found, **kwargs)
            except Exception as exc:
                stopped_type = getattr(module, "RuntimeStopped", None)
                if stopped_type is not None and isinstance(exc, stopped_type):
                    # 暂停 / 取消不是失败：交给 failed 分支分别播报，别记成红色错误
                    stopped.append("paused" if getattr(exc, "paused", False) else "cancelled")
                else:
                    self._console_error(f"运行环境「{name}」安装失败：{exc}")
                raise

        def done(_result) -> None:
            self._program_env_cache.clear()
            self._state_tooltip.clear()
            self._probe_pending.clear()
            tail = "\n".join(lines[-6:]) or "（没有输出）"
            self._console_info(f"运行环境「{name}」安装完成（{len(lines)} 行输出）")
            toast_success(self, "安装完成", tail[-200:])
            if system_mode:
                self._detect_devices()

        def failed(message: str) -> None:
            """被暂停 / 取消不算失败；真出错才弹红字。"""
            reason = stopped[-1] if stopped else ""
            if reason == "cancelled":
                self._installing.pop(key, None)
                self._drop_half_install(key)
                if self._complete_queue:
                    self._complete_queue.clear()
                    self._console_info("一键补全已取消：后面的运行环境不再继续装")
                self._console_info(f"运行环境「{name}」安装已取消，半成品已清理")
                toast_info(self, "已取消安装", f"{name}：pip 已停，半成品运行环境已删掉。")
                return
            if reason == "paused":
                info = self._installing.get(key)
                if info is not None:
                    info["state"] = "paused"
                self._console_info(f"运行环境「{name}」安装已暂停（pip 缓存与半成品 venv 保留）")
                toast_info(self, "已暂停安装", f"{name}：点「继续」接着装，缓存不会白费。")
                return
            self._installing.pop(key, None)
            toast_error(self, "安装失败", f"{name}：{message}")

        def cleanup() -> None:
            """收尾：装完 / 失败 / 取消都撤掉这一行；只有暂停留着给「继续」。"""
            forget = getattr(self._api, "forget_runtime_cache", None)
            if callable(forget):
                # 运行环境变了：让「检查缺什么」下次重新读盘，而不是吃缓存里的旧结论。
                forget()
            info = self._installing.get(key)
            paused = str((info or {}).get("state") or "") == "paused"
            if not paused:
                self._installing.pop(key, None)
                self._install_ctl.pop(key, None)
            if not self._installing:
                self._install_timer.stop()
            try:
                self._fill_runtime()
            except Exception as exc:  # pragma: no cover - 刷新失败也不该吞掉完成播报
                self._console_error(f"刷新运行环境列表失败：{exc}")
            if not self._installing:
                self._pump_complete_queue()

        # 先把这一行换成「安装中…」：pip 十几分钟不出声，不占一行用户会以为没反应
        if wheels:
            names = "、".join(Path(item).name for item in wheels[:4])
            if len(wheels) > 4:
                names += f" 等 {len(wheels)} 个文件"
            source_note = f"本地文件：{names}"
        else:
            source_note = f"pip 源：{index}"
        self._installing[key] = {
            "name": name,
            "start": time.monotonic(),
            "lines": lines,
            "state": "running",
            "source": source_note,
        }
        self._console_stage("安装运行环境", f"{name} → {target}（来源：{source_note}）")
        toast_info(
            self,
            "继续安装" if resume else "开始安装",
            f"{name}：进度在这一行的状态栏里，可以「暂停」或「取消」。",
        )
        self._fill_runtime()
        self._install_timer.start()
        self._run_async(work, done, cleanup, failed)

    def _uninstall_profile(self, profile: dict) -> None:
        module = self._runtime_module()
        if module is None:
            toast_warning(self, "运行环境模块未就绪", "")
            return
        key = str(profile.get("id") or "")
        found = module.profile_of(key, self._ctx)
        if found is None:
            return
        name = str(profile.get("name") or key)
        system_mode = bool(getattr(self._settings, "allow_system_env", False))
        if system_mode:
            # 程序环境模式：卸载也要落在程序解释器上，和这里的「安装」对称
            packages = "、".join(str(item) for item in (profile.get("packages") or [])) or "（清单为空）"
            detail = (
                f"会从程序自己的 Python 环境里卸载：{packages}\n"
                f"环境：{module.system_python()}\n\n"
                "只卸这个运行环境声明的包，程序自己的其它依赖不动。确定继续吗？"
            )
            if not self.confirm(f"从程序环境卸载「{name}」？", detail):
                return
            lines: list[str] = []

            def work_system():
                try:
                    return module.uninstall_system(found, on_line=lines.append)
                except Exception as exc:
                    self._console_error(f"运行环境「{name}」卸载失败：{exc}")
                    raise

            def done_system(_result) -> None:
                self._program_env_cache.clear()
                self._state_tooltip.clear()
                self._probe_pending.clear()
                # 卸载了就别留着旧日志：顺手删掉程序环境与隔离环境的安装日志
                clear_logs = getattr(module, "clear_logs", None)
                if callable(clear_logs):
                    try:
                        clear_logs(key, system=True)
                    except Exception as exc:
                        self._console_error(f"清理运行环境「{name}」的日志失败：{exc}")
                self._fill_runtime()
                self._console_info(f"运行环境「{name}」已从程序环境卸载（{len(lines)} 行输出）")
                toast_success(self, "已卸载", name)

            self._console_stage("从程序环境卸载", f"{name} → {module.system_python()}")
            self._run_async(work_system, done_system, self._fill_runtime)
            return
        if not self.confirm(f"删除运行环境「{name}」？", f"会删掉整个目录：{module.venv_dir(key)}"):
            return
        self._console_stage("删除运行环境", f"{name} → {module.venv_dir(key)}")
        try:
            removed = bool(module.uninstall(key))
        except Exception as exc:
            self._console_error(f"运行环境「{name}」删除失败：{exc}")
            toast_error(self, "删除失败", str(exc))
            return
        # 卸载之后日志没用了：一并清掉（否则「日志」入口还点得动，看着像没卸干净）
        clear_logs = getattr(module, "clear_logs", None)
        if callable(clear_logs):
            try:
                clear_logs(key, system=True)
            except Exception as exc:
                self._console_error(f"清理运行环境「{name}」的日志失败：{exc}")
        self._fill_runtime()
        if not removed:
            detail = (
                f"{module.venv_dir(key)} 里还有文件没删掉（多半是 pip / venv 里的进程还占着）。\n"
                "关掉相关进程后再点一次「卸载」。\n"
            )
            self._console_error(f"运行环境「{name}」没删干净：{detail}")
            toast_error(self, "没删干净", detail)
            return
        self._console_info(f"运行环境「{name}」已删除")
        toast_info(self, "已删除", name)

    @staticmethod
    def _state_is_installed(state: str) -> bool:
        return state.startswith("已安装") or state.startswith("使用程序环境（已装）")

    @staticmethod
    def _profile_is_gpu(profile: dict) -> bool:
        """这份清单是不是 GPU 版：id 带 `-gpu`，或者包里带 `gpu` 的发行包。"""
        key = str(profile.get("id") or "").lower()
        packages = [str(item).lower() for item in (profile.get("packages") or [])]
        return key.endswith("-gpu") or any("gpu" in item for item in packages)

    def _gpu_twin(self, profile: dict) -> dict | None:
        """同一个 backend 的另一个版本（`xxx` ↔ `xxx-gpu`）。"""
        key = str(profile.get("id") or "")
        if not key:
            return None
        target = key[:-4] if key.endswith("-gpu") else key + "-gpu"
        for item in self._runtime_profiles():
            if str(item.get("id") or "") == target:
                return item
        return None

    def _profile_log_path(self, profile: dict, module=None) -> Path | None:
        """这个运行环境最近一次安装的输出日志；程序环境模式优先看 system 日志。"""
        if module is None:
            module = self._runtime_module()
        if module is None:
            return None
        key = str(profile.get("id") or "")
        candidates: list[Path] = []
        if bool(getattr(self._settings, "allow_system_env", False)):
            candidates.append(Path(module.system_log_file(key)))
        candidates.append(Path(module.log_file(key)))
        for path in candidates:
            if path.exists():
                return path
        return None

    def _sync_runtime_buttons(self) -> None:
        """按钮跟着安装状态走：装好了不能再装，没装（或目录不在）不能卸；卸载之后日志也不再可点。"""
        module = self._runtime_module()
        hints = {
            "install": "已经装好了",
            "uninstall": "还没装（或运行环境目录不在）",
            "log": "还没装（或者已经卸载了），没有日志可看",
            "whl": "已经装好了：要换成本地的 .whl，先点「卸载」再装",
        }
        for row in self._runtime_rows.values():
            if row.get("install_state") is not None:
                continue  # 安装中 / 已暂停那一行：按钮是「暂停（继续）/ 取消」，它自己管启用状态
            installed = self._state_is_installed(str(row.get("state_text") or ""))
            profile = row.get("profile") or {}
            has_dir = False
            if module is not None:
                key = str(profile.get("id") or "")
                checker = getattr(module, "has_dir", None)
                try:
                    has_dir = bool(checker(key)) if callable(checker) else Path(module.venv_dir(key)).exists()
                except Exception:
                    has_dir = False
            log_ready = (installed or has_dir) and self._profile_log_path(profile, module) is not None
            for name, enabled in (
                ("install", not installed),
                ("uninstall", installed or has_dir),
                ("log", log_ready),
                ("whl", not installed and not has_dir),
            ):
                button = row.get(name)
                if button is None:
                    continue
                button.setEnabled(enabled)
                button.setToolTip("" if enabled else hints[name])

    def _show_profile_log(self, profile: dict) -> None:
        module = self._runtime_module()
        if module is None:
            return
        key = str(profile.get("id") or "")
        if module.profile_of(key, self._ctx) is None:
            return
        path = self._profile_log_path(profile, module)
        if path is None:
            toast_info(self, "还没有日志", "装过一次之后才会有日志。")
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            toast_error(self, "读日志失败", str(exc))
            return
        toast_info(self, f"最近日志：{Path(path).name}", text[-300:])

    # ============================================================ 推理设备
    def _probe_module(self):
        from ..runtime import probe

        return probe

    # ============================================================ 控制台播报
    def _console(self):
        """控制台输出（SDK 的 ctx.console）：拿不到就返回 None，播报失败绝不影响主流程。"""
        ctx = getattr(self, "_ctx", None)
        console = getattr(ctx, "console", None)
        if console is not None:
            return console
        try:
            from app.sdk import console as console_api
        except Exception:
            return None
        return console_api.console_for(PLUGIN_ID)

    def _console_stage(self, name: str, message: str = "") -> None:
        console = self._console()
        if console is None:
            return
        try:
            console.stage(name, message)
        except Exception:
            pass

    def _console_info(self, message: str) -> None:
        console = self._console()
        if console is None:
            return
        try:
            console.info(message)
        except Exception:
            pass

    def _console_error(self, message: str) -> None:
        console = self._console()
        if console is None:
            return
        try:
            console.error(message)
        except Exception:
            pass

    def _probe_pythons(self) -> list[Path]:
        """先试程序自己的解释器，再试已安装的运行环境 venv（谁装了 torch 就用谁的设备表）。"""
        import sys as _sys

        found = [Path(_sys.executable)]
        module = self._runtime_module()
        if module is not None:
            for profile in self._runtime_profiles():
                key = str(profile.get("id") or "")
                try:
                    if module.installed(key):
                        path = module.python_path(key)
                        if path.exists():
                            found.append(path)
                except Exception:
                    continue
        return found

    def _selected_device(self) -> str:
        return str(self.device_box.currentData() or _AUTO_DEVICE)

    def _fill_device_box(self, options, current: str = "") -> None:
        """重建下拉项：保留当前选择，旧设置里的设备即使没探测到也补一项。"""
        keep = str(current or self._selected_device() or _AUTO_DEVICE)
        items = [(str(key), str(label)) for key, label in options if str(key)]
        if keep not in [key for key, _ in items]:
            items.append((keep, keep))
        self._device_options = items
        self.device_box.blockSignals(True)
        self.device_box.clear()
        for key, label in items:
            self.device_box.addItem(label, userData=key)
        self.device_box.blockSignals(False)
        index = next((position for position, (key, _) in enumerate(items) if key == keep), 0)
        self.device_box.setCurrentIndex(index)

    def _select_device(self, device: str) -> None:
        key = str(device or "").strip() or _AUTO_DEVICE
        self._fill_device_box(self._device_options, key)

    def _detect_devices(self, force: bool = False) -> None:
        """后台探测真实可用设备（import torch 很慢，绝不放在界面线程）。

        结果缓存在模块级 `_DEVICE_PROBE_CACHE`：同一个进程反复进出模型页不再重新起
        子进程；「检测设备」按钮传 `force=True` 才强制重探。
        """
        global _DEVICE_PROBE_CACHE
        if not force and _DEVICE_PROBE_CACHE is not None:
            self._apply_probe_result(_DEVICE_PROBE_CACHE)
            return
        try:
            probe = self._probe_module()
        except Exception as exc:
            toast_warning(self, "设备探测不可用", str(exc))
            return
        pythons = self._probe_pythons()
        self._console_stage("探测设备", "读取 CPU / 显卡型号与当前解释器可用的推理设备…")
        self.device_button.setEnabled(False)

        def done(found) -> None:
            global _DEVICE_PROBE_CACHE
            payload = found if isinstance(found, dict) else {}
            _DEVICE_PROBE_CACHE = payload
            names = self._apply_probe_result(payload)
            toast_info(self, "已检测到推理设备", names or "只有 CPU")
            self._console_info(f"可用推理设备：{names or '只有 CPU'}")
            blocked = self._probe_blocked(payload)
            if blocked:
                self._console_info("检测到但当前解释器用不了：" + "、".join(blocked))

        self._run_async(
            lambda: probe.detect_hardware(pythons),
            on_done=done,
            on_finally=lambda: self.device_button.setEnabled(True),
        )

    @staticmethod
    def _probe_blocked(payload: dict) -> list[str]:
        """探测到、但当前解释器用不了的设备名（下拉里不列，只在提示里说明）。"""
        blocked = [
            str(item.get("name") or item.get("id") or "")
            for item in payload.get("devices") or ()
            if isinstance(item, dict) and not item.get("usable", True)
        ]
        return [name for name in blocked if name]

    def _apply_probe_result(self, payload: dict) -> str:
        """把探测结果填进设备下拉与提示，返回可用设备名（缓存命中时也走这里）。"""
        options = [(_AUTO_DEVICE, _AUTO_DEVICE_LABEL)]
        blocked = self._probe_blocked(payload)
        for item in payload.get("devices") or ():
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "")
            name = str(item.get("name") or key)
            if not key or key == _AUTO_DEVICE or key in [old for old, _ in options]:
                continue
            if not item.get("usable", True):
                continue  # 当前解释器用不了的别塞进下拉，免得选了却悄悄退 CPU
            options.append((key, name))
        if "cpu" not in [key for key, _ in options]:
            options.append(_CPU_DEVICE)
        self._fill_device_box(options)
        names = "、".join(label for _key, label in options[1:])
        has_cuda = any(key.startswith("cuda") for key, _ in options)
        self._device_has_cuda = has_cuda
        self._device_hint.setText(
            self._device_hint_text(
                names,
                payload.get("others"),
                has_cuda=has_cuda,
                blocked=blocked,
            )
        )
        try:
            self._runtime_toolbar_hint.setText(self._complete_hint())
        except Exception:  # pragma: no cover - 提示文案不该拖垮探测
            pass
        return names

    @staticmethod
    def _device_hint_text(names: str, others, *, has_cuda: bool, blocked=()) -> str:
        """把「装了但当前 Python 用不了」的显卡说清楚，别让人以为漏检。"""
        text = "可用：" + (names or "只有 CPU")
        stop = [str(name).strip() for name in (blocked or ()) if str(name).strip()]
        extra = [str(name).strip() for name in (others or ()) if str(name).strip()]
        if has_cuda:
            extra = [name for name in extra if "nvidia" not in name.lower()]
        guess = [name for name in extra if "nvidia" in name.lower()]
        for name in guess:
            if name not in stop:
                stop.append(name)
        rest = [name for name in extra if name not in guess]
        if stop:
            text += "。检测到 " + "、".join(stop) + "，但当前 Python 里的 torch 用不了 CUDA（装了 CUDA 版 torch 才能拿它推理）"
        if rest:
            text += "。这台机器上本程序不拿来推理的显卡：" + "、".join(rest)
        return text

    def _program_env_text(self, profile: dict) -> str:
        """高级选项打开时，程序环境里到底有没有这套依赖——探测丢后台，先显示检测中。"""
        key = str(profile.get("id") or "")
        cached = self._program_env_cache.get(key)
        if cached is not None:
            return cached
        self._probe_pending[key] = profile
        return "使用程序环境（检测中…）"

    @staticmethod
    def _program_env_state_text(found) -> str:
        """行内只放短话：整串依赖会把这行撑爆，完整清单进悬停提示。"""
        names = [str(name) for name in found]
        if not names:
            return "使用程序环境（已装）"
        head = names[0]
        more = f" 等 {len(names)} 个包" if len(names) > 1 else ""
        return f"使用程序环境（缺 {head}{more}）"

    @staticmethod
    def _program_env_tooltip(found) -> str:
        names = [str(name) for name in found]
        if not names:
            return "程序环境里这套依赖都装齐了。"
        return "程序环境缺：" + "、".join(names) + "（点右侧「安装」补上）"

    def _pump_probe(self) -> None:
        """一次把排队的依赖都探完：所有 profile 共用一个子进程，别一套排一个解释器。"""
        if self._installing:
            # pip 正在装：这时候探出来也不准，还要白抢一个解释器，等装完那轮再探
            return
        if self._probe_job is not None or not self._probe_pending:
            return
        try:
            probe = self._probe_module()
        except Exception:
            return
        pending = dict(self._probe_pending)
        self._probe_pending.clear()
        self._probe_job = "batch"
        groups = {key: list(profile.get("packages") or ()) for key, profile in pending.items()}

        def done(report) -> None:
            # 先放开占用，_fill_runtime() 里的 _pump_probe() 才能接下一批
            self._probe_job = None
            data = report if isinstance(report, dict) else None
            missing: list[str] = []
            for key in pending:
                if data is None:
                    self._program_env_cache[key] = "使用程序环境（未检测）"
                    self._state_tooltip[key] = "依赖探测没跑起来，点「日志」看细节。"
                    continue
                found = tuple(str(name) for name in (data.get(key) or ()))
                self._program_env_cache[key] = self._program_env_state_text(found)
                self._state_tooltip[key] = self._program_env_tooltip(found)
                if found:
                    missing.append(key)
            if data is None:
                self._console_info("程序环境依赖检查没跑起来（不影响使用）")
            elif missing:
                head = "、".join(missing[:4])
                more = f" 等 {len(missing)} 套" if len(missing) > 4 else ""
                self._console_info(f"程序环境缺依赖：{head}{more}（点对应行的「安装」补上）")
            else:
                self._console_info("程序环境依赖齐全")
            self._fill_runtime()

        def finished() -> None:
            # 任务结束（成功或失败）都放开占用再排下一批，避免整队停在「检测中…」
            if self._probe_job is not None:
                self._probe_job = None
            self._pump_probe()

        self._run_async(
            lambda: probe.missing_program_group(groups),
            on_done=done,
            on_finally=finished,
        )

    # ============================================================ 设置
    def _select_pip_mirror(self, key: str) -> None:
        index = self.pip_mirror_box.findData(str(key or ""))
        self.pip_mirror_box.blockSignals(True)
        self.pip_mirror_box.setCurrentIndex(index if index >= 0 else 0)
        self.pip_mirror_box.blockSignals(False)

    def _sync_pip_mirror_row(self) -> None:
        """只有「自定义地址」才让填；下面一行写出真正会传给 pip 的 index-url。"""
        key = str(self.pip_mirror_box.currentData() or "official")
        custom = key == "custom"
        self.pip_mirror_custom.setEnabled(custom)
        if custom:
            url = pip_mirror_url("custom", self.pip_mirror_custom.text())
            text = f"安装依赖时用：{url}" if url else "还没填地址，这次安装会退回 pip 默认源。"
        else:
            url = pip_mirror_url(key)
            text = f"安装依赖时用：{url}" if url else "安装依赖时用 pip 默认源（pypi.org）。"
        self._pip_mirror_hint.setText(text)

    def _on_pip_mirror_changed(self, _index: int = -1) -> None:
        self._sync_pip_mirror_row()
        if str(self.pip_mirror_box.currentData() or "") == "custom":
            return  # 地址还没填，等编辑框收尾再存
        self._save_pip_mirror()

    def _save_pip_mirror(self) -> None:
        """换安装源立刻存盘：用户不该猜「是不是还得点保存」。"""
        settings = self._settings
        settings.pip_mirror = str(self.pip_mirror_box.currentData() or "")
        settings.pip_mirror_custom = self.pip_mirror_custom.text()
        self._sync_pip_mirror_row()
        if not settings.save():
            toast_error(self, "保存失败", "写配置文件出错，看看日志。")
            return
        self._console_info(f"运行环境安装源已设为：{settings.index_url or 'pip 默认源（pypi.org）'}")
        toast_info(self, "安装源已更新", "下次装运行环境就用这个源。")

    def _select_github_source(self, key: str) -> None:
        index = self.github_box.findData(str(key or ""))
        self.github_box.blockSignals(True)
        self.github_box.setCurrentIndex(index if index >= 0 else 0)
        self.github_box.blockSignals(False)

    def _sync_github_row(self) -> None:
        """只有「自定义网址」才让填；下面一行写出真正会用的前缀链（随选项变）。"""
        key = str(self.github_box.currentData() or "official")
        self.github_custom.setEnabled(key == "custom")
        chain = github_prefixes(key, self.github_custom.text())
        shown = "、".join(item or "官方直连" for item in chain)
        self._github_hint.setText(f"GitHub 资源按这个顺序取：{shown}")

    def _on_github_source_changed(self, _index: int = -1) -> None:
        self._sync_github_row()
        if str(self.github_box.currentData() or "") == "custom":
            return  # 前缀还没填，等编辑框收尾再存
        self._save_github_source()

    def _save_github_source(self) -> None:
        """换 GitHub 下载源立刻存盘：用户不该猜「是不是还得点保存」。"""
        settings = self._settings
        settings.github_source = str(self.github_box.currentData() or "")
        settings.github_custom = self.github_custom.text()
        self._sync_github_row()
        if not settings.save():
            toast_error(self, "保存失败", "写配置文件出错，看看日志。")
            return
        shown = "、".join(item or "官方直连" for item in settings.github_prefixes)
        self._console_info(f"下载源（GitHub）已设为：{settings.github_source}（{shown}）")
        toast_info(self, "下载源（GitHub）已更新", "下次下载 GitHub 上的资源就用它。")

    def _select_download_source(self, key: str) -> None:
        index = self.source_box.findData(str(key or ""))
        self.source_box.blockSignals(True)
        self.source_box.setCurrentIndex(index if index >= 0 else 0)
        self.source_box.blockSignals(False)

    def _sync_download_source_row(self) -> None:
        """只有「自定义」才让编辑清单；下面一行按选项写出当前会去取的地址。"""
        settings = self._settings
        key = str(self.source_box.currentData() or "official")
        custom = key == "custom"
        self.source_custom_edit.setEnabled(custom)
        self.source_add_button.setEnabled(custom)
        self.source_list.setEnabled(custom)
        self.source_remove_button.setEnabled(custom and self.source_list.count() > 0)
        if key == "mirror":
            mirrors = "、".join(settings.mirrors) or "（没配镜像）"
            text = f"从 HF-Mirror 镜像下载：{mirrors}（程序提供的地址，不可修改）"
        elif key == "custom":
            first = self.source_list.item(0).text().strip() if self.source_list.count() else ""
            text = f"从自定义地址下载：{first}" if first else "还没添加下载路径，下载会退回官方与镜像。"
        else:
            text = f"从 HuggingFace 官方下载：{settings.base_url}（程序提供的地址，不可修改）"
        self._download_source_hint.setText(text)

    def _on_download_source_changed(self, _index: int = -1) -> None:
        """换下载源立刻存盘：用户不该猜「是不是还得点保存」。"""
        settings = self._settings
        settings.download_source = str(self.source_box.currentData() or "")
        self._sync_download_source_row()
        if not settings.save():
            toast_error(self, "保存失败", "写配置文件出错，看看日志。")
            return
        self._console_info(f"下载源（模型）已设为：{settings.download_base}（{settings.download_source}）")
        toast_info(self, "下载源（模型）已更新", f"下次下载走 {settings.download_base}")

    def _add_download_source(self) -> None:
        url = self.source_custom_edit.text().strip().rstrip("/")
        if not url:
            toast_warning(self, "地址是空的", "先把下载网址填进左边的输入框。")
            return
        if not url.startswith(("http://", "https://")):
            toast_warning(self, "地址看起来不对", "下载网址要以 http:// 或 https:// 开头。")
            return
        for index in range(self.source_list.count()):
            if self.source_list.item(index).text().strip() == url:
                toast_info(self, "已经加过了", url)
                return
        self.source_list.addItem(url)
        self.source_custom_edit.clear()
        self.source_list.setCurrentRow(0)
        self._save_download_sources()
        self._console_info(f"已添加下载地址：{url}")

    def _remove_download_source(self) -> None:
        row = self.source_list.currentRow()
        if row < 0:
            toast_info(self, "先选一条", "在清单里点一下要删掉的地址。")
            return
        removed = self.source_list.takeItem(row)
        self._save_download_sources()
        if removed is not None:
            self._console_info(f"已移除下载地址：{removed.text()}")

    def _save_download_sources(self) -> None:
        settings = self._settings
        settings.set_custom_downloads([self.source_list.item(i).text() for i in range(self.source_list.count())])
        self._sync_download_source_row()
        if not settings.save():
            toast_error(self, "保存失败", "写配置文件出错，看看日志。")
            return
        self._console_info(f"下载地址清单已更新：{len(settings.custom_downloads)} 条")

    def _load_settings_into_form(self) -> None:
        """把设置文件里的值填回界面。填的期间不触发「即改即存」，免得平白写一遍盘。"""
        settings = self._settings
        self._loading_settings = True
        try:
            self.source_list.clear()
            for url in settings.custom_downloads:
                self.source_list.addItem(url)
            self._select_download_source(settings.download_source)
            self._sync_download_source_row()
            self.proxy_edit.setText(settings.proxy)
            self.pip_mirror_custom.setText(settings.pip_mirror_custom)
            self._select_pip_mirror(settings.pip_mirror)
            self._sync_pip_mirror_row()
            self.github_custom.setText(settings.github_custom)
            self._select_github_source(settings.github_source)
            self._sync_github_row()
            self.concurrent_box.setValue(settings.concurrent)
            self.max_resident_box.setValue(settings.max_resident)
            self.idle_box.setValue(settings.idle_unload_sec)
            self._select_device(settings.device)
            self.system_env_box.blockSignals(True)
            self.system_env_box.setChecked(settings.allow_system_env)
            self.system_env_box.blockSignals(False)
        finally:
            self._loading_settings = False

    def _store_setting(self, group: str, key: str, value, message: str) -> bool:
        """即改即存：界面上动一下就写进设置文件；值没变就不写盘。返回是否可用继续。"""
        settings = self._settings
        bucket = settings.download if group == "download" else settings.runtime
        if bucket.get(key) == value:
            return True
        bucket[key] = value
        if not settings.save():
            toast_error(self, "保存失败", "写配置文件出错，看看日志。")
            self._console_error(f"保存设置失败：{group}.{key} = {value!r}")
            return False
        self._console_info(message)
        return True

    def _on_proxy_changed(self) -> None:
        if self._loading_settings:
            return
        text = self.proxy_edit.text().strip()
        self._store_setting("download", "proxy", text, f"下载代理已设为：{text or '（不使用代理）'}")

    def _on_concurrent_changed(self, value) -> None:
        if self._loading_settings:
            return
        count = int(value)
        self._store_setting("download", "concurrent", count, f"下载并发数已设为：{count}")

    def _on_max_resident_changed(self, value) -> None:
        if self._loading_settings:
            return
        count = int(value)
        self._store_setting("runtime", "max_resident", count, f"同时常驻模型数已设为：{count}")

    def _on_idle_changed(self, value) -> None:
        if self._loading_settings:
            return
        seconds = int(value)
        self._store_setting("runtime", "idle_unload_sec", seconds, f"空闲多久卸载已设为：{seconds} 秒")

    def _on_device_changed(self, _value=None) -> None:
        if self._loading_settings:
            return
        device = self._selected_device()
        if not self._store_setting("runtime", "device", device, f"推理设备已设为：{device}"):
            return
        self._program_env_cache.clear()
        self._state_tooltip.clear()
        self._probe_pending.clear()
        self._fill_runtime()

    def _on_system_env_toggled(self, checked: bool) -> None:
        """勾上/取消就立刻生效并存盘——用户不该猜「还得再点一下保存」。"""
        settings = self._settings
        settings.runtime["allow_system_env"] = bool(checked)
        if not settings.save():
            toast_error(self, "保存失败", "写配置文件出错，看看日志。")
            return
        self._program_env_cache.clear()
        self._state_tooltip.clear()
        self._probe_pending.clear()
        self._fill_runtime()
        toast_info(self, "依赖安装方式已更新", "运行环境区已按新设置刷新。")

    # ============================================================ 线程与拖拽
    def _run_async(self, work, on_done=None, on_finally=None, on_failed=None) -> None:
        """把耗时活丢到工作线程，结果回到界面线程再改界面。

        `on_failed(message)` 自己接管失败提示（安装被取消 / 暂停不是「操作失败」，
        不该弹红字），不给就还是默认的 `_on_task_failed`。
        """
        task = _Bridge(self)
        self._tasks.add(task)

        def finish() -> None:
            if on_finally is not None:
                try:
                    on_finally()
                except Exception:
                    pass
            self._tasks.discard(task)
            task.deleteLater()

        def handle_done(result) -> None:
            if on_done is not None:
                try:
                    on_done(result)
                except Exception as exc:
                    toast_error(self, "操作失败", str(exc))
            finish()

        task.done.connect(handle_done)
        task.failed.connect(on_failed or self._on_task_failed)
        task.failed.connect(lambda _message: finish())

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                try:
                    task.failed.emit(str(exc))
                except RuntimeError:
                    pass  # 页面已销毁，信号桥没了
            else:
                try:
                    task.done.emit(result)
                except RuntimeError:
                    pass

        threading.Thread(target=runner, name="model-page-task", daemon=True).start()

    def _on_task_failed(self, message: str) -> None:
        toast_error(self, "操作失败", message)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self._import_paths(paths)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._device_probed:
            self._device_probed = True
            self._detect_devices()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._install_timer.stop()
        if self._downloads is not None:
            self._downloads.shutdown(wait=1.0)
        super().closeEvent(event)
