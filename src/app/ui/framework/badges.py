"""状态徽章：把类型、来源、状态这类短标签画成带底色的小胶囊。

长句说明都挂在悬停提示上了，界面上剩下的短标签（已启用 / 库插件 / 内置）用一个圆角
底色块显示，比纯文本更容易扫读。页面里不要直接写 QSS（自检 style_uniformity 会拦），
样式与绘制都从这里取，列表项自绘与普通控件用的是同一套尺寸。
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

__all__ = [
    "BADGE_GAP",
    "BADGE_HEIGHT",
    "BADGE_MARGIN",
    "BADGE_PADDING",
    "BADGE_TONES",
    "StatusBadge",
    "badge_brush",
    "badge_qss",
    "badge_row",
    "badge_width",
    "badges_width",
    "paint_badges",
]

# 色调 → (红, 绿, 蓝, 透明度)：底色统一半透明，深浅色主题都能用
BADGE_TONES: dict[str, tuple[int, int, int, float]] = {
    "plain": (128, 128, 128, 0.18),
    "ok": (15, 157, 88, 0.18),
    "warn": (157, 93, 0, 0.18),
    "error": (196, 43, 28, 0.18),
}

BADGE_HEIGHT = 18
BADGE_PADDING = 7
BADGE_GAP = 4
BADGE_MARGIN = 8


def _tone(tone: str) -> tuple[int, int, int, float]:
    return BADGE_TONES.get(tone, BADGE_TONES["plain"])


def badge_qss(tone: str = "plain") -> str:
    """徽章样式：底色按色调取，文字颜色跟随主题调色板。"""
    red, green, blue, alpha = _tone(tone)
    return (
        "color: palette(text);"
        f"background-color: rgba({red}, {green}, {blue}, {alpha});"
        f"border-radius: {BADGE_HEIGHT // 2}px;"
        f"padding: 1px {BADGE_PADDING}px;"
        "font-size: 11px;"
    )


def badge_brush(tone: str = "plain") -> QColor:
    """自绘用的底色画刷。"""
    red, green, blue, alpha = _tone(tone)
    return QColor(red, green, blue, round(alpha * 255))


def badge_width(metrics: QFontMetrics, text: str) -> int:
    return metrics.horizontalAdvance(text) + 2 * BADGE_PADDING


def badges_width(metrics: QFontMetrics, badges) -> int:
    """一组徽章占用的宽度（含间距），列表项据此给文字让位。"""
    if not badges:
        return 0
    return sum(badge_width(metrics, text) for text, _tone in badges) + BADGE_GAP * (len(badges) - 1)


def paint_badges(
    painter: QPainter,
    right: int,
    center_y: float,
    badges,
    metrics: QFontMetrics,
    text_color: QColor,
) -> None:
    """从右往左画一组徽章，右缘对齐 right。"""
    x = right - BADGE_MARGIN
    for text, tone in reversed(list(badges)):
        width = badge_width(metrics, text)
        x -= width
        rect = QRectF(x, center_y - BADGE_HEIGHT / 2 + 0.5, width, BADGE_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(badge_brush(tone))
        painter.drawRoundedRect(rect, BADGE_HEIGHT / 2, BADGE_HEIGHT / 2)
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        x -= BADGE_GAP


class StatusBadge(QLabel):
    """短标签徽章：set_badge() 一次换文本与色调，空文本自动隐藏。"""

    def __init__(self, text: str = "", tone: str = "plain", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tone = tone if tone in BADGE_TONES else "plain"
        self.setStyleSheet(badge_qss(self._tone))
        self.setVisible(bool(text))

    @property
    def tone(self) -> str:
        return self._tone

    def set_tone(self, tone: str) -> None:
        self._tone = tone if tone in BADGE_TONES else "plain"
        self.setStyleSheet(badge_qss(self._tone))

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setVisible(bool(text))

    def set_badge(self, text: str, tone: str = "plain") -> None:
        self.set_tone(tone)
        self.setText(text)


def badge_row(*badges: StatusBadge, spacing: int = 6) -> QHBoxLayout:
    """把若干徽章左对齐排成一行（末尾留弹簧，徽章不会被拉开）。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for badge in badges:
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
    row.addStretch(1)
    return row
