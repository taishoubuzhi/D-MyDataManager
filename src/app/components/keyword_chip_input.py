# coding: utf-8
"""可复用的关键词胶囊输入组件。

每个关键词显示为带删除按钮的胶囊；末尾有「+」按钮用于新增，
编辑态输入框右侧带确认按钮，回车或点确认均可添加。
关键词非空时末尾出现「清空」按钮一键清除全部。
适用于数据导入、编辑等需要管理一组字符串的场景。
"""
import os

from PyQt6.QtCore import Qt, QSize, QEvent, pyqtSignal, QTimer
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QFrame, QSizePolicy,
                             QApplication)
from qfluentwidgets import (FlowLayout, LineEdit, CaptionLabel,
                            TransparentToolButton, FluentIcon as FIF,
                            setCustomStyleSheet)


class _KeywordChip(QFrame):
    """单个关键词胶囊：文字 + 删除按钮"""

    removeRequested = pyqtSignal(str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text
        self.setObjectName('keywordChip')

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(4)

        label = CaptionLabel(text, self)
        label.setObjectName('keywordChipLabel')
        layout.addWidget(label)

        btn = TransparentToolButton(FIF.CLOSE, self)
        btn.setFixedSize(18, 18)
        btn.setIconSize(QSize(11, 11))
        btn.clicked.connect(lambda: self.removeRequested.emit(self._text))
        layout.addWidget(btn)

        self.setFixedHeight(26)


class KeywordChipInput(QWidget):
    """可复用的关键词胶囊输入组件。

    Public API:
        get_keywords() -> list[str]
        set_keywords(keywords: list[str])
        clear()
        keywordsChanged 信号
    """

    keywordsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('keywordChipInput')
        self._keywords = []          # 有序关键词列表
        self._chips = {}             # text -> _KeywordChip
        self._trailing = None        # 当前末尾控件（+ 按钮或编辑行）

        # isTight=True：隐藏控件不占位，末尾控件通过 show/hide 切换，
        # 避免 removeWidget/addWidget 反复触发 FlowLayout 动画重建
        self._flow = FlowLayout(self, needAni=True, isTight=True)
        self._flow.setSpacing(6)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)

        # 末尾「+」按钮
        self._addBtn = TransparentToolButton(FIF.ADD, self)
        self._addBtn.setFixedSize(26, 26)
        self._addBtn.setIconSize(QSize(14, 14))
        self._addBtn.clicked.connect(self._showEdit)

        # 编辑行：输入框 + 确认按钮
        self._editRow = QWidget(self)
        editLayout = QHBoxLayout(self._editRow)
        editLayout.setContentsMargins(0, 0, 0, 0)
        editLayout.setSpacing(4)

        self._edit = LineEdit(self._editRow)
        self._edit.setPlaceholderText("输入关键词后回车或点确认")
        self._edit.setFixedWidth(160)
        self._edit.returnPressed.connect(self._commitEdit)
        self._edit.textChanged.connect(self._onEditTextChanged)
        self._edit.installEventFilter(self)
        editLayout.addWidget(self._edit)

        self._confirmBtn = TransparentToolButton(FIF.ACCEPT, self._editRow)
        self._confirmBtn.setFixedSize(26, 26)
        self._confirmBtn.setIconSize(QSize(14, 14))
        # NoFocus：点击确认不夺走输入框焦点，避免 FocusOut 提前收起
        self._confirmBtn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._confirmBtn.clicked.connect(self._commitEdit)
        self._confirmBtn.hide()  # 有内容时才显示
        editLayout.addWidget(self._confirmBtn)

        self._cancelBtn = TransparentToolButton(FIF.CANCEL, self._editRow)
        self._cancelBtn.setFixedSize(26, 26)
        self._cancelBtn.setIconSize(QSize(14, 14))
        self._cancelBtn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancelBtn.clicked.connect(self._showAddBtn)
        editLayout.addWidget(self._cancelBtn)

        # 清空按钮（仅关键词非空时显示）
        self._clearBtn = TransparentToolButton(FIF.DELETE, self)
        self._clearBtn.setFixedSize(26, 26)
        self._clearBtn.setIconSize(QSize(14, 14))
        self._clearBtn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clearBtn.setToolTip("清空所有关键词")
        self._clearBtn.clicked.connect(self._clearAll)

        # 一次性加入所有末尾控件，顺序固定：[+按钮, 编辑行, 清空按钮]
        # isTight 会跳过隐藏控件，通过 show/hide 切换可见末尾
        self._flow.addWidget(self._addBtn)
        self._flow.addWidget(self._editRow)
        self._flow.addWidget(self._clearBtn)
        self._editRow.hide()
        self._clearBtn.hide()
        self._trailing = self._addBtn

        self._applyQss()

    # ===== 样式 =====
    def _applyQss(self):
        """从文件系统加载明暗 qss，自包含适配主题"""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        qss_dir = os.path.join(base, "resource", "qss")
        light = self._read_qss(os.path.join(qss_dir, "light",
                                            "keyword_chip_input.qss"))
        dark = self._read_qss(os.path.join(qss_dir, "dark",
                                           "keyword_chip_input.qss"))
        setCustomStyleSheet(self, light, dark)

    @staticmethod
    def _read_qss(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    # ===== 事件处理 =====
    def eventFilter(self, obj, event):
        if obj is self._edit:
            if (event.type() == QEvent.Type.KeyPress
                    and event.key() == Qt.Key.Key_Escape):
                self._showAddBtn()
                return True
            if event.type() == QEvent.Type.FocusOut:
                # 延迟到事件循环空闲再检查，避免布局动画触发的瞬时 FocusOut
                QTimer.singleShot(0, self._maybeCollapse)
                return False
        return super().eventFilter(obj, event)

    def _maybeCollapse(self):
        """焦点确实离开本组件时才收起编辑行"""
        if self._trailing is not self._editRow:
            return
        # 输入框有内容时不收起，避免点确认按钮被 FocusOut 干扰
        if self._edit.text().strip():
            return
        focus = QApplication.focusWidget()
        if focus is None or not self.isAncestorOf(focus):
            self._showAddBtn()

    def _onEditTextChanged(self, text):
        """输入框内容变化：有内容时显示确认按钮"""
        self._confirmBtn.setVisible(bool(text.strip()))

    # ===== 末尾控件切换（仅 show/hide，不增删布局项）=====
    def _showEdit(self):
        """+ 按钮 -> 编辑行"""
        if self._trailing is self._editRow:
            return
        self._addBtn.hide()
        self._editRow.show()
        self._edit.clear()  # 清空输入框，触发 textChanged 隐藏确认按钮
        self._trailing = self._editRow
        # 延迟到布局完成后再聚焦，避免 FlowLayout 动画过程触发 FocusOut
        QTimer.singleShot(0, self._edit.setFocus)

    def _showAddBtn(self):
        """编辑行 -> + 按钮"""
        if self._trailing is self._addBtn:
            return
        self._editRow.hide()
        self._edit.clear()
        self._addBtn.show()
        self._trailing = self._addBtn

    def _updateClearBtn(self):
        """关键词非空时显示清空按钮，否则隐藏"""
        if self._keywords:
            self._clearBtn.show()
        else:
            self._clearBtn.hide()

    # ===== 增删 =====
    def _commitEdit(self):
        """回车/确认：非空且不重复则新增，清空输入框保持聚焦"""
        text = self._edit.text().strip()
        if text and text not in self._chips:
            self._addChip(text)
        self._edit.clear()
        self._edit.setFocus()

    def _addChip(self, text):
        chip = _KeywordChip(text, self)
        chip.removeRequested.connect(self._removeChip)
        self._chips[text] = chip
        self._keywords.append(text)
        # 在 _addBtn 前插入胶囊，保持 [chips..., +按钮, 编辑行, 清空按钮] 顺序
        self._flow.insertWidget(self._flow.indexOf(self._addBtn), chip)
        self._updateClearBtn()
        self.keywordsChanged.emit()

    def _removeChip(self, text):
        chip = self._chips.pop(text, None)
        if chip is None:
            return
        self._flow.removeWidget(chip)
        chip.deleteLater()
        if text in self._keywords:
            self._keywords.remove(text)
        self._updateClearBtn()
        self.keywordsChanged.emit()

    def _clearAll(self):
        """清空按钮回调：清空全部并发射信号"""
        self.clear()
        self.keywordsChanged.emit()

    # ===== 公共 API =====
    def get_keywords(self):
        return list(self._keywords)

    def set_keywords(self, keywords):
        self.clear()
        for kw in keywords or []:
            kw = str(kw).strip()
            if kw and kw not in self._chips:
                self._addChip(kw)

    def clear(self):
        """清空所有胶囊并收起到 + 按钮（不发射信号）"""
        for chip in list(self._chips.values()):
            self._flow.removeWidget(chip)
            chip.deleteLater()
        self._chips.clear()
        self._keywords.clear()
        # 收起编辑行（若展开）
        if self._trailing is self._editRow:
            self._editRow.hide()
            self._edit.clear()
            self._addBtn.show()
            self._trailing = self._addBtn
        self._clearBtn.hide()
