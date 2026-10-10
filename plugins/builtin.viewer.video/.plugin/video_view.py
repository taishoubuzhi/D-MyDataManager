"""内置视频播放器：QtMultimedia 负责播放，周边能力统一走程序本体的媒体接口。

分工（顶层约定，插件不重复实现）：

* 播放、进度、音量、倍速由 QtMultimedia 提供（零新依赖，能播就用系统解码器）；
* 探测、截图、逐帧步进、内嵌字幕、换音轨，以及"系统解码器播不了"时的转封装 /
  转码兜底，全部经 `app.sdk.media`——真正的解码由程序本体的媒体引擎（PyAV）完成，
  插件不 import av，也不自己起子进程调 ffmpeg；
* 播放偏好（音量 / 静音 / 倍速 / 循环 / 画面比例 / 字幕开关）走插件选项，
  由 `PlayerPanel` 的 `on_option` 回调进来，插件负责写回。

兜底流程：`QMediaPlayer` 报错 → 弹窗问用户是否允许转封装 / 转码到临时文件 →
同意则后台线程把原文件重新封装（先试、只换容器）或转码成 mp4 再播；产物落在
`paths.MEDIA_TMP_DIR`，窗口关闭时立即删除，进程退出还有一层兜底清理。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu

from app.sdk import items, media, ui
from app.sdk.console import console_for
from dm_plugin.builtin.lib.ui.plugin import (
    MEDIA_RATES,
    PlayerPanel,
    confirm,
    format_time,
    status_label,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
)

_console = console_for("builtin.viewer.video")

#: 兜底产物的容器：mp4 是系统解码器支持最广的那个
FALLBACK_CONTAINER = "mp4"


def _stamp() -> str:
    """截图文件名里的时间戳（`20261009-224512`）。"""
    return time.strftime("%Y%m%d-%H%M%S")


class _FallbackWorker(QThread):
    """后台把播不了的视频重新封装 / 转码到临时文件（只调 SDK，不碰界面）。"""

    progress = pyqtSignal(float)
    done = pyqtSignal(str, str)  # (临时文件, 说明)
    failed = pyqtSignal(str)

    def __init__(
        self,
        source: str | Path,
        *,
        keep_audio: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = Path(source)
        self._keep_audio = keep_audio
        self.cancel = threading.Event()

    def run(self) -> None:  # noqa: D102 - QThread 入口
        target = media.temp_path(f".{FALLBACK_CONTAINER}")
        cancelled = self.cancel.is_set
        try:
            media.remux(
                self._source,
                target,
                container=FALLBACK_CONTAINER,
                keep_audio=self._keep_audio,
                cancel=cancelled,
            )
        except media.MediaCancelled:
            media.cleanup_temp(target)
            self.failed.emit("已取消")
            return
        except Exception:  # noqa: BLE001 - 换容器失败就退回转码
            self._transcode(target, cancelled)
            return
        self.done.emit(str(target), "已重新封装到临时文件")

    def _transcode(self, target: str, cancelled) -> None:
        """第二道兜底：真正解码重编码，慢但几乎什么格式都能变成能播的 mp4。"""
        try:
            media.transcode(self._source, target, on_progress=self.progress.emit, cancel=cancelled)
        except media.MediaCancelled:
            media.cleanup_temp(target)
            self.failed.emit("已取消")
        except Exception as exc:  # noqa: BLE001 - 统一成一句中文提示
            media.cleanup_temp(target)
            self.failed.emit(str(exc))
        else:
            self.done.emit(str(target), "已转码到临时文件（画质略有损失）")


class VideoViewer(PlayerPanel):
    """视频画面 + 播放条 + 探测信息 + 截图 / 逐帧 / 字幕 / 音轨 / 全屏。"""

    shows_video = True

    def __init__(
        self,
        path: Path,
        parent: QWidget | None = None,
        *,
        options: dict | None = None,
        on_option=None,
    ) -> None:
        values = dict(options or {})
        self._subtitle_on = bool(values.get("subtitle", True))
        self._info: media.MediaInfo | None = None
        self._cues: list = []
        self._cue_index = 0
        self._subtitle_index: int | None = None
        self._worker: _FallbackWorker | None = None
        self._fallback_path: str = ""
        self._fallback_tried = False
        self._popup = None
        #: 已经建在标题栏（或退回播放条）上的常用动作按钮，换外壳时要先清掉
        self._top_buttons: list = []
        self._top_on_popup = False
        super().__init__(path, parent, options=values, on_option=on_option)
        self._probe()
        self._setup_actions()

    # ------------------------------------------------------------------ 界面扩展
    def _build_stage_extra(self, layout) -> None:
        """画面与播放条之间放一条字幕显示位（没有字幕时不占高度）。"""
        self._subtitle_label = status_label(None, "")
        self._subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setVisible(False)
        layout.addWidget(self._subtitle_label)

    def attach_popup(self, popup) -> None:
        """外壳把弹窗交给内容页：常用动作进标题栏，Esc 先给视频全屏用，关窗清理临时产物。"""
        changed = popup is not self._popup
        self._popup = popup
        if changed:
            self._rebuild_top_actions()
        popup.set_escape_handler(self._on_escape)
        try:
            popup.destroyed.connect(lambda *_: self._release())
        except Exception:  # noqa: BLE001 - 外壳没有该信号也不影响播放
            pass

    def showEvent(self, event) -> None:  # noqa: D102 - 见 PlayerPanel
        super().showEvent(event)
        self._ensure_top_actions()

    def _on_escape(self) -> bool:
        """全屏时 Esc 退出全屏（返回 True 表示窗口别关）。"""
        window = self.window()
        if window is not None and window.isFullScreen():
            window.showNormal()
            self._set_bar_visible(True)
            self._sync_fullscreen_button()
            return True
        return False

    # ------------------------------------------------------------------ 探测信息
    def _probe(self) -> None:
        """问程序本体要一份媒体信息：分辨率、时长、编码、字幕轨、音轨。"""
        info = media.probe(self._path)
        self._info = info
        if info is None:
            if not media.available():
                self.set_caption(f"{self._path.name} · 未启用媒体引擎（只影响截图 / 逐帧 / 字幕 / 转码）")
            return
        if info.videos:
            stream = info.videos[0]
            self.set_frame_rate(stream.fps)
            self.set_native_size(stream.width, stream.height)
        summary = info.summary_text
        self.set_caption(f"{self._path.name} · {summary}" if summary else self._path.name)
        self._subtitle_index = info.subtitles[0].index if info.subtitles else None
        self._load_cues()

    def _load_cues(self) -> None:
        """把内嵌字幕轨解成按时间排序的字幕条目（没有轨就是空列表）。"""
        if self._subtitle_index is None:
            self._cues = []
            return
        self._cues = list(media.subtitle(self._path, stream=self._subtitle_index))

    # ------------------------------------------------------------------ 动作按钮
    def _setup_actions(self) -> None:
        """播放条只留播放相关的动作：首末帧与前后一秒贴着播放按钮，其余能力进标题栏。"""
        # 播放按钮两侧：跳到第一帧 / 后退一秒 | 播放 | 前进一秒 / 跳到最后一帧
        self.add_action(
            FluentIcon.PAGE_LEFT, "跳到第一帧（Home）", self._go_first, anchor="before-play"
        )
        self.add_action(FluentIcon.LEFT_ARROW, "后退一秒（F）", self._seek_back, anchor="before-play")
        self.add_action(FluentIcon.RIGHT_ARROW, "前进一秒（G）", self._seek_forward, anchor="after-play")
        self.add_action(
            FluentIcon.PAGE_RIGHT, "跳到最后一帧（End）", self._go_last, anchor="after-play"
        )

        QShortcut(QKeySequence("F"), self, activated=self._seek_back)
        QShortcut(QKeySequence("G"), self, activated=self._seek_forward)
        QShortcut(QKeySequence("B"), self, activated=self._toggle_subtitle)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Home"), self, activated=self._go_first)
        QShortcut(QKeySequence("End"), self, activated=self._go_last)
        QShortcut(QKeySequence("Ctrl+="), self, activated=self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, activated=self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self._zoom_reset)

        self.zoomChanged.connect(self._sync_zoom_buttons)
        self._build_top_actions()
        self._sync_zoom_buttons()

    def _ensure_top_actions(self) -> None:
        """常用动作只建一次：优先标题栏，没有外壳时才落到播放条上。"""
        if self._top_buttons:
            return
        self._build_top_actions()
        self._sync_zoom_buttons()

    def _rebuild_top_actions(self) -> None:
        """换外壳（播放条 ↔ 标题栏）时把常用动作重排一遍。"""
        on_popup = self._top_on_popup
        popup = self._popup
        for button in list(self._top_buttons):
            try:
                removed = False
                if on_popup and popup is not None:
                    remover = getattr(popup, "remove_action", None)
                    if callable(remover):
                        removed = bool(remover(button))
                if not removed:
                    self.remove_action(button)
            except RuntimeError:  # 按钮已被 Qt 回收，跳过即可
                continue
        self._top_buttons = []
        self._build_top_actions()
        self._sync_zoom_buttons()

    def _build_top_actions(self) -> None:
        """常用能力：截图 / 导出音轨 / 音轨选择 / 字幕 / 循环 / 缩放 / 全屏。"""
        self._zoom_out_button = self._add_top_action(FluentIcon.ZOOM_OUT, "缩小画面", self._zoom_out)
        self._zoom_in_button = self._add_top_action(FluentIcon.ZOOM_IN, "放大画面", self._zoom_in)
        self._zoom_reset_button = self._add_top_action(FluentIcon.ZOOM, "重置缩放", self._zoom_reset)

        self._loop_button = self._add_top_action(FluentIcon.SYNC, "循环播放", self._toggle_loop)
        self._loop_button.setCheckable(True)
        self._loop_button.setChecked(self.loop)

        self._screenshot_button = self._add_top_action(
            FluentIcon.CAMERA, "截取当前画面", self._grab_frame
        )
        self._cover_button = self._add_top_action(
            FluentIcon.PHOTO, "用当前画面作封面", self._use_frame_as_cover
        )
        self._export_button = self._add_top_action(
            FluentIcon.SAVE, "把音轨导出成音频文件", self._export_audio
        )

        info = self._info
        if info is not None and len(info.audios) > 1:
            self._audio_button = self._add_top_action(
                FluentIcon.MUSIC, "选择音轨", self._show_audio_menu
            )
        if info is not None and info.subtitles:
            self._subtitle_button = self._add_top_action(
                FluentIcon.FONT, "显示内嵌字幕（B）", self._toggle_subtitle
            )
            self._subtitle_button.setCheckable(True)
            self._subtitle_button.setChecked(self._subtitle_on and bool(self._cues))
        self._fullscreen_button = self._add_top_action(
            FluentIcon.FULL_SCREEN, "全屏（F11）", self._toggle_fullscreen
        )

        self._sync_fullscreen_button()

    def _add_top_action(self, icon, tooltip: str, callback):
        """建一个常用动作按钮：有弹窗外壳就放标题栏，没有就退回播放条。"""
        popup = self._popup
        adder = getattr(popup, "add_action", None)
        if callable(adder):
            try:
                button = adder(icon, tooltip, callback)
            except Exception:  # noqa: BLE001 - 外壳加不了就退回播放条
                _console.exception("标题栏动作挂载失败，退回播放条")
            else:
                self._top_on_popup = True
                self._top_buttons.append(button)
                return button
        self._top_on_popup = False
        button = self.add_action(icon, tooltip, callback)
        self._top_buttons.append(button)
        return button

    # ------------------------------------------------------------------ 播放位置
    def _go_first(self) -> None:
        """跳到第一帧，并说一声当前落在哪。"""
        toast_info(self, "已跳到第一帧", f"当前位置 {format_time(self.go_first_frame())}")

    def _go_last(self) -> None:
        """跳到最后一帧，并说一声当前落在哪。"""
        toast_info(self, "已跳到最后一帧", f"当前位置 {format_time(self.go_last_frame())}")

    def _seek_back(self) -> None:
        """后退一秒，并说一声当前落在哪。"""
        toast_info(self, "后退一秒", f"当前位置 {format_time(self.seek_by(-1.0))}")

    def _seek_forward(self) -> None:
        """前进一秒，并说一声当前落在哪。"""
        toast_info(self, "前进一秒", f"当前位置 {format_time(self.seek_by(1.0))}")

    # ------------------------------------------------------------------ 缩放
    def _zoom_in(self) -> None:
        self.zoom_in()
        self._sync_zoom_buttons()

    def _zoom_out(self) -> None:
        self.zoom_out()
        self._sync_zoom_buttons()

    def _zoom_reset(self) -> None:
        self.reset_zoom()
        self._sync_zoom_buttons()

    def _sync_zoom_buttons(self, *_args) -> None:
        """倍率到最小 / 最大时禁掉对应的缩放按钮（重置按钮始终可用）。"""
        out_button = getattr(self, "_zoom_out_button", None)
        if out_button is not None:
            out_button.setEnabled(self.can_zoom_out())
        in_button = getattr(self, "_zoom_in_button", None)
        if in_button is not None:
            in_button.setEnabled(self.can_zoom_in())

    # ------------------------------------------------------------------ 循环 / 全屏
    def _toggle_loop(self) -> None:
        """切换循环播放，并把当前状态说出来（按钮的勾选状态同步）。"""
        self.set_loop(not self.loop)
        self._loop_button.setChecked(self.loop)
        self.save_option("loop", self.loop)
        if self.loop:
            toast_info(self, "循环播放：开", "播完自动从头再来")
        else:
            toast_info(self, "循环播放：关", "播完停在最后一帧")

    def _toggle_fullscreen(self) -> None:
        """全屏 / 还原：标题栏留着（全屏按钮就在上面，得能点回来），按钮换成「退出全屏」。"""
        window = self.window()
        if window is None:
            return
        if window.isFullScreen():
            window.showNormal()
        else:
            window.showFullScreen()
        self._set_bar_visible(True)
        self._sync_fullscreen_button()

    def _sync_fullscreen_button(self) -> None:
        """全屏按钮显示「当前还能做什么」：没全屏就是进全屏，全屏了就是退出全屏。"""
        button = getattr(self, "_fullscreen_button", None)
        if button is None:
            return
        window = self.window()
        fullscreen = bool(window is not None and window.isFullScreen())
        button.setIcon(FluentIcon.BACK_TO_WINDOW if fullscreen else FluentIcon.FULL_SCREEN)
        button.setToolTip("退出全屏（F11）" if fullscreen else "全屏（F11）")

    def _set_bar_visible(self, visible: bool) -> None:
        popup = self._popup
        setter = getattr(popup, "set_bar_visible", None)
        if callable(setter):
            setter(visible)

    # ------------------------------------------------------------------ 字幕
    def _toggle_subtitle(self) -> None:
        self._subtitle_on = not self._subtitle_on
        button = getattr(self, "_subtitle_button", None)
        if button is not None:
            button.setChecked(self._subtitle_on)
        if not self._subtitle_on:
            self._subtitle_label.setVisible(False)
        return None

    def _update_subtitle(self, seconds: float) -> None:
        """按播放位置滚动字幕条（字幕条目已按时间排好，顺序往前走）。"""
        cues = self._cues
        if not cues or not self._subtitle_on:
            return
        index = self._cue_index
        while index < len(cues) - 1 and seconds >= cues[index].end:
            index += 1
        while index > 0 and seconds < cues[index].start:
            index -= 1
        self._cue_index = index
        cue = cues[index]
        if cue.is_active(seconds):
            self._subtitle_label.setText(cue.text)
            self._subtitle_label.setVisible(True)
        else:
            self._subtitle_label.setVisible(False)

    def _on_position(self, position: int) -> None:
        super()._on_position(position)
        self._update_subtitle(position / 1000.0)

    # ------------------------------------------------------------------ 截图 / 导出
    def _grab_frame(self) -> None:
        """把"当前播放位置的那一帧"存成图片（从原文件解，不看屏幕截图）。"""
        target = self._save_target("保存当前画面", f"{self._path.stem}-{_stamp()}.png")
        if not target:
            return
        self._wait(True)
        try:
            shot = media.frame(self._path, target, at=self._player.position() / 1000.0, size=None)
        finally:
            self._wait(False)
        if shot:
            toast_success(self, "已保存当前画面", Path(shot).name)
            ui.reveal(shot)
        else:
            toast_error(self, "截图失败", "这一帧解不出来（文件损坏或媒体引擎不可用）")

    def _use_frame_as_cover(self) -> None:
        """把当前播放位置的那一帧写成这条数据的封面（用户 m02499 第 2 条）。

        帧先落在媒体临时目录，交给数据接口缩放并搬进封面目录，临时文件随后删掉——
        播放器只负责「解这一帧」，封面怎么存由程序本体说了算。
        """
        item_id = items.item_id_for_path(str(self._path))
        if not item_id:
            # 播放的可能不是库里的数据（外部文件、收件箱），没有可改封面的对象
            toast_warning(self, "这个文件不在数据库里", "只有库内的视频才能用画面当封面")
            return
        self._wait(True)
        temp = None
        try:
            temp = media.temp_path(".png")
            shot = media.frame(self._path, temp, at=self._player.position() / 1000.0, size=None)
        except Exception as exc:  # noqa: BLE001 - 媒体接口拿不到临时目录时只提示
            toast_error(self, "封面设置失败", str(exc))
            return
        finally:
            self._wait(False)
        if not shot:
            toast_error(self, "封面设置失败", "这一帧解不出来（文件损坏或媒体引擎不可用）")
            return
        try:
            items.set_cover(item_id, str(shot))
        except Exception as exc:  # noqa: BLE001 - 写封面失败不影响继续播放
            toast_error(self, "封面设置失败", str(exc))
            return
        finally:
            media.cleanup_temp(temp)
        toast_success(self, "已设为封面", "回数据管理页看到的就是这一帧")

    def _export_audio(self) -> None:
        """把音轨导出成音频文件（能直接搬包就不重编码）。"""
        if self._info is not None and not self._info.has_audio:
            toast_warning(self, "这个文件里没有音轨", "")
            return
        target = self._save_target(
            "导出音轨",
            f"{self._path.stem}.m4a",
            ("音频文件 (*.m4a *.mp3 *.wav *.flac *.ogg)", "所有文件 (*.*)"),
        )
        if not target:
            return
        self._wait(True)
        try:
            media.extract_audio(self._path, target)
        except Exception as exc:  # noqa: BLE001 - 失败只提示，不影响播放
            toast_error(self, "导出音轨失败", str(exc))
        else:
            toast_success(self, "已导出音轨", Path(target).name)
            ui.reveal(target)
        finally:
            self._wait(False)

    def _save_target(self, title: str, default_name: str, filters=None) -> str:
        """问用户要保存路径（默认落在视频旁边）。"""
        parent_dir = self._path.parent
        patterns = filters or ("图片 (*.png *.jpg *.jpeg *.bmp)", "所有文件 (*.*)")
        path, _ = QFileDialog.getSaveFileName(
            self, title, str(parent_dir / default_name), ";;".join(patterns)
        )
        return path or ""

    def _wait(self, busy: bool) -> None:
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    # ------------------------------------------------------------------ 音轨
    def _show_audio_menu(self) -> None:
        """列出所有音轨，换轨 = 把选中的那条单独封装到临时文件再播。"""
        info = self._info
        if info is None or len(info.audios) < 2:
            return
        menu = RoundMenu(parent=self)
        for stream in info.audios:
            text = stream.describe() or f"音轨 {stream.index}"
            action = Action(text)
            action.setCheckable(True)
            action.setChecked(stream.index == getattr(self, "_current_audio", info.audios[0].index))
            action.triggered.connect(lambda _=False, index=stream.index: self._switch_audio(index))
            menu.addAction(action)
        menu.exec(QCursor.pos())

    def _switch_audio(self, index: int) -> None:
        """切换音轨：重封装到临时文件（只保留这一条音轨）后重新播放。"""
        self._current_audio = index
        self._fallback_tried = False
        self._start_fallback(keep_audio=index, reason="按你选择的音轨重新封装到临时文件")

    # ------------------------------------------------------------------ 播不了时的兜底
    def _on_error(self, error, message: str = "") -> None:
        super()._on_error(error, message)
        if error == self._player_cls.Error.NoError or self._fallback_tried:
            return
        self._fallback_tried = True
        self._ask_fallback(message or "该格式无法播放")

    def _ask_fallback(self, reason: str) -> None:
        """问用户要不要把文件转成能播的格式：不同意就保持"播不了"的提示。"""
        if not media.available():
            self.set_caption(f"{self._path.name} · 播放失败：{reason}（媒体引擎不可用，无法转码兜底）")
            return
        if not confirm(
            self,
            "这个格式系统播放器解不了",
            f"{reason}\n\n是否让程序把它重新封装 / 转码到临时文件后再播放？\n"
            "临时文件会在关闭窗口后自动删除。",
        ):
            self.set_caption(f"{self._path.name} · 播放失败：{reason}（已取消转码）")
            return
        self._start_fallback(reason=reason)

    def _start_fallback(self, *, keep_audio: int | None = None, reason: str = "") -> None:
        """起后台线程做重封装 / 转码，完成后换成临时文件接着播。"""
        if self._worker is not None and self._worker.isRunning():
            return
        self.set_caption(f"{self._path.name} · 正在准备可播放的临时文件…")
        worker = _FallbackWorker(self._path, keep_audio=keep_audio, parent=self)
        worker.progress.connect(self._on_fallback_progress)
        worker.done.connect(self._on_fallback_done)
        worker.failed.connect(self._on_fallback_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_fallback_progress(self, value: float) -> None:
        self.set_caption(f"{self._path.name} · 正在转码 {value * 100:.0f}%")

    def _on_fallback_done(self, path: str, note: str) -> None:
        self._drop_fallback()
        self._fallback_path = path
        self._bar.button.setEnabled(True)
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()
        self.set_caption(f"{self._path.name} · {note}")
        toast_success(self, "已开始播放临时文件", note)

    def _on_fallback_failed(self, message: str) -> None:
        self._drop_fallback()
        self.set_caption(f"{self._path.name} · 播放失败：{message}")
        toast_error(self, "这个文件放不了", message)

    def _drop_fallback(self) -> None:
        self._worker = None

    def _release(self) -> None:
        """窗口关掉：停播、取消后台任务、删掉临时文件。"""
        worker = self._worker
        if worker is not None:
            worker.cancel.set()
            worker.wait(2000)
            self._worker = None
        if self._fallback_path:
            target = self._fallback_path
            self._fallback_path = ""
            self._close_player()
            # 播放器松开文件句柄是异步的：一边转事件循环一边重试；仍不行就交给退出时的兜底清理
            for _ in range(5):
                if media.cleanup_temp(target):
                    break
                time.sleep(0.1)
                self._pump_events()
            else:
                _console.info(f"临时文件仍被播放器占用，留给退出清理：{target}")
        try:
            self.stop()
        except Exception:  # noqa: BLE001 - 关窗路径上的一切异常都不该打断
            pass

    def _close_player(self) -> None:
        """停播并清空播放源：Windows 上不松手就删不掉临时文件。"""
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except Exception:  # noqa: BLE001 - 播放器已经没了也无所谓
            pass
        self._pump_events()

    def _pump_events(self) -> None:
        """给播放器一个处理异步消息的机会（source 变更后要转一下事件循环才松手）。"""
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    # ------------------------------------------------------------------ 设置面板
    def settings_items(self) -> list[dict]:
        """设置入口里的项：播放偏好 + 缩放；改一下立即生效并写回插件选项。"""
        return [
            {
                "key": "volume",
                "label": "默认音量",
                "kind": "int",
                "value": int(round(self._volume * 100)),
                "minimum": 0,
                "maximum": 100,
                "step": 5,
                "suffix": "%",
                "description": "下次打开视频时的音量。",
                "on_change": self._pick_volume,
            },
            {
                "key": "muted",
                "label": "默认静音",
                "kind": "bool",
                "value": bool(self._muted),
                "description": "下次打开视频时先不出声，播放条上随时能打开。",
                "on_change": self._pick_muted,
            },
            {
                "key": "rate",
                "label": "默认倍速",
                "kind": "choice",
                "value": f"{self._rate:g}",
                "choices": {f"{item:g}": f"{item:g}×" for item in MEDIA_RATES},
                "description": "下次打开视频时的播放速度。",
                "on_change": self._pick_rate,
            },
            {
                "key": "loop",
                "label": "循环播放",
                "kind": "bool",
                "value": bool(self.loop),
                "description": "播完自动从头再来。",
                "on_change": self._pick_loop,
            },
            {
                "key": "autoplay",
                "label": "进入就播放",
                "kind": "bool",
                "value": bool(self.autoplay),
                "description": "开着时打开视频直接开播（当前这个窗口也会马上开始）；关掉就保持等你自己点播放。",
                "on_change": self._pick_autoplay,
            },
            {
                "key": "aspect",
                "label": "画面比例",
                "kind": "choice",
                "value": self.aspect,
                "choices": {
                    "fit": "适应窗口（保持比例，可能留黑边）",
                    "stretch": "拉伸铺满（可能变形）",
                },
                "description": "放大 / 缩小时画面仍按这里的比例摆放。",
                "on_change": self._pick_aspect,
            },
            {
                "key": "subtitle",
                "label": "显示内嵌字幕",
                "kind": "bool",
                "value": bool(self._subtitle_on),
                "description": "文件里带字幕轨时默认显示。",
                "on_change": self._pick_subtitle,
            },
            {
                "key": "zoom_step",
                "label": "缩放步长",
                "kind": "choice",
                "value": self._zoom_step_choice(),
                "choices": {"1.15": "1.15×（细腻）", "1.25": "1.25×（默认）", "1.5": "1.5×（快速）"},
                "description": "每次放大 / 缩小的幅度。",
                "on_change": self._pick_zoom_step,
            },
            {
                "key": "zoom_hint",
                "label": "倍率提示时长",
                "kind": "int",
                "value": int(self._zoom_hint),
                "minimum": 200,
                "maximum": 5000,
                "step": 100,
                "suffix": "毫秒",
                "description": "缩放后右下角那行倍率提示停留多久。",
                "on_change": self._pick_zoom_hint,
            },
        ]

    def _zoom_step_choice(self) -> str:
        """当前缩放步长对应的选项值（不在候选里就报默认档）。"""
        for text in ("1.15", "1.25", "1.5"):
            if abs(self._zoom_step - float(text)) < 1e-6:
                return text
        return "1.25"

    def _pick_volume(self, value) -> None:
        self._on_volume(max(0.0, min(1.0, float(value) / 100.0)))

    def _pick_muted(self, value) -> None:
        self._on_mute(bool(value))

    def _pick_rate(self, value) -> None:
        self._on_rate(float(value))

    def _pick_loop(self, value) -> None:
        self.set_loop(bool(value))
        button = getattr(self, "_loop_button", None)
        if button is not None:
            button.setChecked(self.loop)
        self.save_option("loop", self.loop)

    def _pick_autoplay(self, value) -> None:
        self.set_autoplay(bool(value))
        self.save_option("autoplay", self.autoplay)

    def _pick_aspect(self, value) -> None:
        self.set_aspect(str(value))
        self.save_option("aspect", str(value))

    def _pick_subtitle(self, value) -> None:
        self._subtitle_on = bool(value)
        button = getattr(self, "_subtitle_button", None)
        if button is not None:
            button.setChecked(self._subtitle_on)
        if not self._subtitle_on:
            self._subtitle_label.setVisible(False)
        self.save_option("subtitle", self._subtitle_on)

    def _pick_zoom_step(self, value) -> None:
        self.set_zoom_step(float(value))
        self.save_option("zoom_step", f"{float(value):g}")

    def _pick_zoom_hint(self, value) -> None:
        self.set_zoom_hint(int(value))
        self.save_option("zoom_hint", int(value))

    # ------------------------------------------------------------------ 选项回写
    def _emit_option(self, key: str, value) -> None:  # noqa: D102 - 见 PlayerPanel
        if key == "subtitle":
            self._subtitle_on = bool(value)
        super()._emit_option(key, value)
