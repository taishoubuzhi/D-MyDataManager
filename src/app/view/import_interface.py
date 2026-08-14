# coding: utf-8
"""数据导入界面。

支持两种导入模式（用户选择数据类别，类型自动决定）：
- 文本导入：文本内容直接存入 Data.content（类型=TEXT）
- 文件导入：源文件复制到 Data-Store-Path，content 存复制后路径（类型按扩展名自动识别）

模式切换全程保留已输入文本与已选文件。
命名支持导入时间戳（YYYYMMDD-HHMMSS）：文本留空自动用时间戳；
文件模式可由「按时间命名」开关控制，开关状态与配置项双向同步并持久化。

导入成功后通过 signalBus.dataImported 通知数据管理页面刷新。
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget)
from qfluentwidgets import (ScrollArea, SegmentedWidget, LineEdit, TextEdit,
                            ComboBox, SwitchButton, PrimaryPushButton,
                            BodyLabel, StrongBodyLabel, CardWidget,
                            FluentIcon as FIF, InfoBar, setCustomStyleSheet,
                            PillPushButton, FlowLayout, CheckBox)
from loguru import logger

from ..common.config import config
from ..common.init.init_db import get_session
from ..common.signal_bus import signalBus
from ..common.data_importer import (import_text_data, import_file_data,
                                     generate_timestamp_name, detect_data_type)
from ..components.import_drop_area import ImportDropArea
from ..components.keyword_chip_input import KeywordChipInput
from ..model.Data import DataType, get_type_name
from ..model.User import User
from ..model.DataBase import DataBase
from ..model.Tag import Tag


class ImportInterface(ScrollArea):
    """数据导入界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._mode = "text"        # 当前模式: text / file
        self._files = []           # 文件模式下选中的文件路径列表
        self._users = []           # 用户 ComboBox 对应的 (id, name)
        self._databases = []       # 数据库 ComboBox 对应的 (id, name)

        self.scrollWidget = QWidget()
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.titleLabel = StrongBodyLabel("数据导入", self)

        self.__initWidget()
        self.__initLayout()
        self.__loadCombos()
        self.__loadTags()
        self.__connectConfig()
        self.__applyQss()
        # 初始为文本模式：隐藏「按时间命名」开关
        self.toggleWidget.hide()
        self._updateTypeLabel()

    # ===== 初始化 =====
    def __initWidget(self):
        self.setObjectName('importInterface')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        self.scrollWidget.setObjectName('view')
        self.titleLabel.setObjectName('importTitle')

    def __initLayout(self):
        self.titleLabel.move(36, 30)

        self.vBoxLayout.setContentsMargins(36, 10, 36, 36)
        self.vBoxLayout.setSpacing(16)

        # --- 模式切换（即数据类别选择：文本 / 文件）---
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(12)
        self.modeSwitch = SegmentedWidget(self)
        self.modeSwitch.addItem("text", "文本导入", lambda: self._switchMode("text"))
        self.modeSwitch.addItem("file", "文件导入", lambda: self._switchMode("file"))
        self.modeSwitch.setCurrentItem("text")
        mode_row.addWidget(self.modeSwitch)
        mode_row.addStretch(1)
        self.vBoxLayout.addLayout(mode_row)

        # --- 内容输入堆栈 ---
        self.contentStack = QStackedWidget(self)
        self._buildTextPage()
        self._buildFilePage()
        self.vBoxLayout.addWidget(self.contentStack)

        # --- 元数据表单 ---
        self.formCard = CardWidget(self)
        self.formCard.setObjectName('formCard')
        self.formLayout = QVBoxLayout(self.formCard)
        self.formLayout.setContentsMargins(24, 16, 24, 16)
        self.formLayout.setSpacing(12)

        # 名称行：输入框 + 「按时间命名」开关（开关仅文件模式显示）
        self.nameEdit = LineEdit(self.formCard)
        self.nameEdit.setPlaceholderText("留空则按时间自动命名")
        self.nameByTimeSwitch = SwitchButton(self.formCard)
        self.nameByTimeSwitch.setOnText("")
        self.nameByTimeSwitch.setOffText("")
        self.nameByTimeLabel = BodyLabel("按时间命名", self.formCard)
        self.toggleWidget = QWidget(self.formCard)   # 开关容器，文本模式隐藏
        th = QHBoxLayout(self.toggleWidget)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(6)
        th.addWidget(self.nameByTimeLabel)
        th.addWidget(self.nameByTimeSwitch)
        nameWidget = QWidget(self.formCard)
        nh = QHBoxLayout(nameWidget)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.setSpacing(10)
        nh.addWidget(self.nameEdit, 1)
        nh.addWidget(self.toggleWidget)
        self._addFormRow("名称", nameWidget)
        self.nameByTimeSwitch.checkedChanged.connect(self._onNameByTimeToggled)

        # 类型：只读展示（文本模式=文本；文件模式按所选文件自动识别）
        self.typeLabel = StrongBodyLabel("—", self.formCard)
        self._addFormRow("类型", self.typeLabel)

        self.keywordInput = KeywordChipInput(self.formCard)
        self._addFormRow("关键词", self.keywordInput)

        self._tagButtons = {}
        self._tagUpdating = False
        self.tagWidget = QWidget(self.formCard)
        tagHLayout = QHBoxLayout(self.tagWidget)
        tagHLayout.setContentsMargins(0, 0, 0, 0)
        tagHLayout.setSpacing(6)

        self.tagFlowWidget = QWidget(self.tagWidget)
        self.tagFlowWidget.setObjectName('tagFlowWidget')
        self.tagLayout = FlowLayout(self.tagFlowWidget, needAni=True)
        self.tagLayout.setSpacing(6)
        self.tagLayout.setContentsMargins(0, 0, 0, 0)
        tagHLayout.addWidget(self.tagFlowWidget, 1)

        self.tagSelectAllCb = CheckBox("全选", self.tagWidget)
        self.tagSelectAllCb.setTristate(True)
        self.tagSelectAllCb.setCheckState(Qt.CheckState.Unchecked)
        self.tagSelectAllCb.stateChanged.connect(self._onTagTriStateChanged)
        tagHLayout.addWidget(self.tagSelectAllCb)

        self._addFormRow("标签", self.tagWidget)

        self.hiddenSwitch = SwitchButton(self.formCard)
        self._addFormRow("隐藏", self.hiddenSwitch)

        self.userCombo = ComboBox(self.formCard)
        self._addFormRow("用户", self.userCombo)

        self.databaseCombo = ComboBox(self.formCard)
        self._addFormRow("数据库", self.databaseCombo)

        self.vBoxLayout.addWidget(self.formCard)

        # --- 导入按钮 ---
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch(1)
        self.importBtn = PrimaryPushButton(FIF.SEND, "导入", self)
        self.importBtn.setFixedHeight(36)
        self.importBtn.clicked.connect(self._onImport)
        btn_row.addWidget(self.importBtn)
        btn_row.addStretch(1)
        self.vBoxLayout.addLayout(btn_row)

        self.vBoxLayout.addStretch(1)

    def _buildTextPage(self):
        """构建文本输入页"""
        self.textPage = QWidget(self)
        layout = QVBoxLayout(self.textPage)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(BodyLabel("文本内容", self.textPage))
        self.textEdit = TextEdit(self.textPage)
        self.textEdit.setPlaceholderText("在此输入或粘贴文本内容...")
        self.textEdit.setMinimumHeight(160)
        layout.addWidget(self.textEdit)

        self.contentStack.addWidget(self.textPage)

    def _buildFilePage(self):
        """构建文件选择页"""
        self.filePage = QWidget(self)
        layout = QVBoxLayout(self.filePage)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.dropArea = ImportDropArea(self.filePage)
        self.dropArea.filesSelected.connect(self._onFilesSelected)
        layout.addWidget(self.dropArea)

        self.contentStack.addWidget(self.filePage)

    def _addFormRow(self, label_text: str, widget):
        """在表单卡片中添加一行：固定宽度标签 + 控件"""
        row = QWidget(self.formCard)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        lbl = BodyLabel(label_text, row)
        lbl.setFixedWidth(64)
        h.addWidget(lbl)
        h.addWidget(widget, 1)
        self.formLayout.addWidget(row)

    def __loadCombos(self):
        """加载用户/数据库下拉选项"""
        session = get_session()()
        try:
            self._users = [(u.id, u.name) for u in session.query(User).all()]
            self._databases = [(d.id, d.name) for d in session.query(DataBase).all()]
        finally:
            session.close()

        self.userCombo.addItems([name for _, name in self._users])
        self.databaseCombo.addItems([name for _, name in self._databases])

    def __loadTags(self):
        """从数据库加载已有标签为可切换的胶囊按钮"""
        session = get_session()()
        try:
            tags = session.query(Tag).all()
        finally:
            session.close()
        for tag in tags:
            btn = PillPushButton(tag.name, self.tagFlowWidget)
            self._tagButtons[tag.name] = btn
            self.tagLayout.addWidget(btn)
            btn.toggled.connect(self._onTagToggled)
        self._syncTagTriState()

    def _onTagToggled(self, checked):
        """单个标签切换：同步三态复选框（批量设置时跳过避免回环）"""
        if self._tagUpdating:
            return
        self._syncTagTriState()

    def _onTagTriStateChanged(self, state):
        """三态复选框变化：全选/全不选控制子按钮，部分选中不改"""
        if self._tagUpdating:
            return
        self._tagUpdating = True
        if state == Qt.CheckState.Checked.value:
            for btn in self._tagButtons.values():
                btn.setChecked(True)
        elif state == Qt.CheckState.Unchecked.value:
            for btn in self._tagButtons.values():
                btn.setChecked(False)
        else:
            # PartiallyChecked：用户点击半选态，当作全选
            for btn in self._tagButtons.values():
                btn.setChecked(True)
        self._tagUpdating = False
        self._syncTagTriState()

    def _syncTagTriState(self):
        """按已选标签数同步三态复选框状态"""
        if not self._tagButtons:
            return
        self._tagUpdating = True
        total = len(self._tagButtons)
        checked = sum(1 for b in self._tagButtons.values() if b.isChecked())
        if checked == 0:
            self.tagSelectAllCb.setCheckState(Qt.CheckState.Unchecked)
        elif checked == total:
            self.tagSelectAllCb.setCheckState(Qt.CheckState.Checked)
        else:
            self.tagSelectAllCb.setCheckState(Qt.CheckState.PartiallyChecked)
        self._tagUpdating = False

    def __connectConfig(self):
        """配置项变更 -> 同步导入页开关（与配置页双向同步）"""
        config.file_import_name_by_time.valueChanged.connect(self._onNameByTimeConfigChanged)

    def __applyQss(self):
        """从文件系统加载明暗 qss 并通过 setCustomStyleSheet 自动适配主题

        不依赖 :/app 资源系统，无需重编译 resource.py。
        """
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # src/app
        qss_dir = os.path.join(base, "resource", "qss")
        light_qss = self._read_qss(os.path.join(qss_dir, "light", "import_interface.qss"))
        dark_qss = self._read_qss(os.path.join(qss_dir, "dark", "import_interface.qss"))
        # 参数顺序: (widget, lightQss, darkQss)
        setCustomStyleSheet(self, light_qss, dark_qss)

    @staticmethod
    def _read_qss(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    # ===== 模式切换（不清空文本/文件，保留状态）=====
    def _switchMode(self, mode: str):
        self._mode = mode
        self.contentStack.setCurrentIndex(0 if mode == "text" else 1)
        if mode == "text":
            self.toggleWidget.hide()
            self.nameEdit.setEnabled(True)
            self.nameEdit.setPlaceholderText("留空则按时间自动命名")
        else:
            self.toggleWidget.show()
            # 开关勾选同步自配置（屏蔽信号避免触发副作用）
            self.nameByTimeSwitch.blockSignals(True)
            self.nameByTimeSwitch.setChecked(config.get(config.file_import_name_by_time))
            self.nameByTimeSwitch.blockSignals(False)
            self._applyNameFieldState()
        self._updateTypeLabel()

    def _applyNameFieldState(self):
        """根据当前模式/开关/已选文件刷新名称框可用性与内容"""
        if self._mode != "file":
            return
        by_time = self.nameByTimeSwitch.isChecked()
        if by_time:
            self.nameEdit.setEnabled(False)
            self.nameEdit.setText("")
            self.nameEdit.setPlaceholderText("导入时按时间自动命名")
        else:
            if len(self._files) == 1:
                self.nameEdit.setEnabled(True)
                self.nameEdit.setText(os.path.splitext(os.path.basename(self._files[0]))[0])
                self.nameEdit.setPlaceholderText("留空则使用文件名")
            elif len(self._files) > 1:
                self.nameEdit.setEnabled(False)
                self.nameEdit.setText("")
                self.nameEdit.setPlaceholderText(f"已选 {len(self._files)} 个文件，将使用各自文件名")
            else:
                self.nameEdit.setEnabled(True)
                self.nameEdit.setText("")
                self.nameEdit.setPlaceholderText("选择文件后填充文件名")

    # ===== 类型只读展示 =====
    def _updateTypeLabel(self):
        """根据当前模式/已选文件刷新类型展示"""
        if self._mode == "text":
            self.typeLabel.setText(get_type_name(DataType.TEXT))
        else:
            self.typeLabel.setText(self._files_type_label(self._files))

    @staticmethod
    def _files_type_label(files: list) -> str:
        """文件模式下：无文件=—；单类型=类型名；多类型=多种类型"""
        if not files:
            return "—"
        types = {detect_data_type(fp) for fp in files}
        if len(types) == 1:
            return get_type_name(next(iter(types)))
        return "多种类型"

    # ===== 「按时间命名」开关回调 =====
    def _onNameByTimeToggled(self, checked: bool):
        # 同步到配置（持久化 + 通知配置页）；config.set 对相同值短路，不会回环
        config.set(config.file_import_name_by_time, checked)
        self._applyNameFieldState()

    def _onNameByTimeConfigChanged(self, value):
        # 配置页改动 -> 同步本开关（屏蔽信号避免回环）
        self.nameByTimeSwitch.blockSignals(True)
        self.nameByTimeSwitch.setChecked(bool(value))
        self.nameByTimeSwitch.blockSignals(False)
        self._applyNameFieldState()

    # ===== 文件选择回调 =====
    def _onFilesSelected(self, files: list):
        self._files = files
        self._applyNameFieldState()
        self._updateTypeLabel()

    # ===== 导入主流程 =====
    def _onImport(self):
        keywords = self.keywordInput.get_keywords()
        tags = [name for name, btn in self._tagButtons.items() if btn.isChecked()]
        is_hidden = self.hiddenSwitch.isChecked()
        user_id = self._selected_user_id()
        database_id = self._selected_database_id()

        if self._mode == "text":
            name = self.nameEdit.text().strip() or generate_timestamp_name()
            self._doTextImport(name, keywords, tags, is_hidden, user_id, database_id)
        else:
            if self.nameByTimeSwitch.isChecked():
                name = generate_timestamp_name()   # 整批共用同一时间戳
            else:
                name = self.nameEdit.text().strip()  # 单文件=用户输入(可空回退文件名); 多文件=空(各用文件名)
            self._doFileImport(name, keywords, tags, is_hidden, user_id, database_id)

    def _doTextImport(self, name, keywords, tags, is_hidden, user_id, database_id):
        content = self.textEdit.toPlainText().strip()
        if not content:
            self._error("文本内容不能为空")
            return

        try:
            session = get_session()()
            try:
                import_text_data(session, name, content, keywords, tags,
                                 is_hidden, user_id, database_id)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            logger.exception("文本导入失败")
            self._error(f"导入失败：{e}")
            return

        self._success("文本导入成功")
        self._resetForm()
        signalBus.dataImported.emit()

    def _doFileImport(self, name, keywords, tags, is_hidden, user_id, database_id):
        if not self._files:
            self._error("请先选择文件")
            return

        count = 0
        try:
            session = get_session()()
            try:
                for fp in self._files:
                    import_file_data(session, fp, name, keywords, tags,
                                     is_hidden, user_id, database_id)
                    count += 1
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as e:
            logger.exception("文件导入失败")
            self._error(f"导入失败：{e}")
            return

        self._success(f"成功导入 {count} 条数据")
        self._resetForm()
        signalBus.dataImported.emit()

    # ===== 工具方法 =====
    def _selected_user_id(self):
        idx = self.userCombo.currentIndex()
        if 0 <= idx < len(self._users):
            return self._users[idx][0]
        return None

    def _selected_database_id(self):
        idx = self.databaseCombo.currentIndex()
        if 0 <= idx < len(self._databases):
            return self._databases[idx][0]
        return None

    def _resetForm(self):
        """清空表单内容（保留当前模式与开关状态）"""
        self.textEdit.clear()
        self.nameEdit.clear()
        self.keywordInput.clear()
        self._tagUpdating = True
        for btn in self._tagButtons.values():
            btn.setChecked(False)
        self._tagUpdating = False
        self._syncTagTriState()
        self.hiddenSwitch.setChecked(False)
        self._files = []
        self.dropArea.reset()
        self._applyNameFieldState()   # 按当前模式/开关重置名称框状态
        self._updateTypeLabel()

    def _success(self, msg: str):
        InfoBar.success(self.tr("成功"), msg, duration=2000, parent=self)

    def _error(self, msg: str):
        InfoBar.error(self.tr("错误"), msg, duration=3000, parent=self)
