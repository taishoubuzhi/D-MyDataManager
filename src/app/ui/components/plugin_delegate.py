"""插件列表项的自绘：左侧两行文字（插件名 + 类型 / 来源 / 贡献），右侧状态徽章。

列表项文本被既有断言依赖（要含「内置」、全角间隔点与贡献名），所以短标签不塞进文本里，改在右侧空白处画。
基类 `ListItemDelegate.paint()` 会在 `super().paint()` 里重新 `initStyleOption()`，在 `paint()` 里改
`opt.text` 影响不到它画出来的文字，所以这里把文本置空、按两行自己画：第一行插件名（半粗），
第二行类型 · 来源 · 贡献（小字淡色）；宽度不够就用省略号收尾，给徽章留出的空白不会被文字压住。
徽章贴可视右缘：列表项比视口宽时可以横向滚动看全文字，徽章始终留在视野里。
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
from qfluentwidgets import ListItemDelegate

from ..framework.badges import BADGE_MARGIN, badges_width, paint_badges

__all__ = ["BADGES_ROLE", "SUBTITLE_ROLE", "TITLE_ROLE", "PluginItemDelegate"]

#: 列表项上放的徽章列表：[(文本, 色调), ...]
BADGES_ROLE = int(Qt.ItemDataRole.UserRole) + 1
#: 自绘第一行：插件名
TITLE_ROLE = BADGES_ROLE + 1
#: 自绘第二行：类型 · 来源 · 贡献
SUBTITLE_ROLE = BADGES_ROLE + 2

#: 两行文字之外另留的高度（上下留白）
ROW_PADDING = 16
#: 两行文字的最小行高：字体小时也要留出两行的空间
MIN_ROW_HEIGHT = 46
#: 第二行比第一行小多少磅
SUB_FONT_DELTA = 1.0
#: 第二行的透明度（0-255），淡一点突出插件名
SUB_ALPHA = 150


class PluginItemDelegate(ListItemDelegate):
    """两行文字 + 右侧徽章：勾选框、悬停与选中背景仍交给基类画。"""

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt 接口
        """清掉基类要画的文本：文字交给 `_paint_lines()` 按两行画。

        `ListItemDelegate.paint()` 会在 `super().paint()` 里重新调用本方法，只在 `paint()` 里改
        `opt.text` 拦不住它，必须在这里改。
        """
        super().initStyleOption(option, index)
        option.text = ""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 接口
        size = super().sizeHint(option, index)
        widget = option.widget
        width = widget.viewport().width() if widget is not None else size.width()
        title = QFontMetrics(self._title_font(option.font))
        sub = QFontMetrics(self._sub_font(option.font))
        height = title.height() + sub.height() + ROW_PADDING
        return QSize(max(size.width(), width), max(size.height(), MIN_ROW_HEIGHT, height))

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 接口
        badges = index.data(BADGES_ROLE) or ()
        metrics = QFontMetrics(option.font)
        reserve = badges_width(metrics, badges) + BADGE_MARGIN if badges else 0
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        fallback = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        super().paint(painter, opt, index)

        visible = self._visible_rect(opt)
        area = self._text_rect(opt)
        area.setRight(min(area.right(), visible.right() - reserve))
        if area.width() > 0:
            self._paint_lines(painter, area, index, fallback, opt)
        if not badges:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_badges(
            painter,
            visible.right(),
            area.center().y() + 0.5,
            badges,
            metrics,
            opt.palette.color(QPalette.ColorRole.Text),
        )
        painter.restore()

    def _paint_lines(self, painter, area: QRect, index, fallback: str, opt) -> None:
        """画两行文字：插件名在上，类型 / 来源 / 贡献在下，宽了用省略号收尾。"""
        title = str(index.data(TITLE_ROLE) or fallback or "")
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        title_font = self._title_font(opt.font)
        title_metrics = QFontMetrics(title_font)
        sub_font = self._sub_font(opt.font)
        sub_metrics = QFontMetrics(sub_font)
        block = title_metrics.height() + (sub_metrics.height() if subtitle else 0)
        top = area.top() + max(0, (area.height() - block) // 2)
        width = area.width()
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        painter.save()
        # 裁剪到文字区：字体度量与实际字形不一致时也不会压到右侧徽章上
        painter.setClipRect(area)
        painter.setFont(title_font)
        painter.setPen(opt.palette.color(QPalette.ColorRole.Text))
        painter.drawText(
            QRect(area.left(), top, width, title_metrics.height()),
            flags,
            title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, width),
        )
        if subtitle:
            color = QColor(opt.palette.color(QPalette.ColorRole.Text))
            color.setAlpha(SUB_ALPHA)
            painter.setFont(sub_font)
            painter.setPen(color)
            painter.drawText(
                QRect(area.left(), top + title_metrics.height(), width, sub_metrics.height()),
                flags,
                sub_metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, width),
            )
        painter.restore()

    def _title_font(self, base: QFont) -> QFont:
        font = QFont(base)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _sub_font(self, base: QFont) -> QFont:
        """第二行的小字：比正文小一点，字号有下限，避免离屏环境算出 0 号字。"""
        font = QFont(base)
        font.setPointSizeF(max(7.0, base.pointSizeF() - SUB_FONT_DELTA))
        return font

    def _visible_rect(self, option: QStyleOptionViewItem) -> QRect:
        """看得见的那部分：列表项可能比视口宽，徽章要贴可视右缘而不是项右缘。"""
        rect = QRect(option.rect)
        widget = option.widget
        viewport = widget.viewport() if widget is not None else None
        if viewport is not None:
            rect.setRight(min(rect.right(), viewport.width() - 1))
        return rect

    def _text_rect(self, option: QStyleOptionViewItem) -> QRect:
        """文字区域：交给当前样式算，勾选框与图标的位置自然被避开。"""
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        return style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, widget)
