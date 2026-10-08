"""下载管理页：下载列表 + 下载设置两个 Tab。

- 「下载列表」：程序本体里启动的所有下载都在这张表里，逐任务暂停 / 继续 / 取消 / 重试 /
  清理，用户也可以自己填一个地址交给它下载；列表控件本身在
  `app/ui/components/download_view.py`，插件要复用视图的话从那里取。
- 「下载设置」：下载路径、并行数、顺序下载、超时、重试、代理，以及下载镜像规则
  （一条规则 = 匹配 + 官方网址 + 镜像网址列表 + 启用模式）。标量设置直接写 QConfig，
  规则是结构化列表、写清单 `core.download_mirrors`。

两边的改动都要热生效：改完立刻拿 `app.core.download.service.configure()` 灌进正在跑的队列，
不然用户改了并行数还要重启程序才生效。
"""

from __future__ import annotations

from uuid import uuid4

from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    ScrollArea,
    SegmentedWidget,
    SettingCardGroup,
    SubtitleLabel,
    SwitchSettingCard,
    TableWidget,
    TextEdit,
)

from ...core.config import config, download_dir
from ...core.download import (
    MODE_LABELS,
    MODE_SEQUENTIAL,
    MODES,
    MirrorRule,
    MirrorRules,
)
from ...core.download import mirror_store
from ...core.download import service as download_service
from ..components.data_table import fit_columns, prepare_table
from ..components.download_view import DownloadListView
from ..dialogs import TextInputDialog
from ..framework import (
    CARD_SPACING,
    PANEL_MARGINS,
    ActionCard,
    IconTextButton,
    NumberSettingCard,
    Page,
    clear_scroll_background,
    confirm,
    empty_state,
    open_path,
)

# 两个 Tab：(路由键, 标题)；顺序与 QStackedWidget 的页序一致。
TAB_LIST = "list"
TAB_SETTINGS = "settings"
DOWNLOAD_TABS = ((TAB_LIST, "下载列表"), (TAB_SETTINGS, "下载设置"))
TAB_INDEX = {route_key: index for index, (route_key, _) in enumerate(DOWNLOAD_TABS)}

#: 规则表的列：名称 / 匹配 / 模式 / 官方地址 / 镜像地址 / 状态
RULE_HEADERS = ("名称", "匹配", "模式", "官方地址", "镜像地址", "状态")
COL_RULE_NAME, COL_RULE_PATTERN, COL_RULE_MODE, COL_RULE_OFFICIAL, COL_RULE_MIRRORS, COL_RULE_STATE = range(6)


def tab_index(route_key: str) -> int:
    """路由键对应的堆栈页号；未知键回退到第一个 Tab。"""
    return TAB_INDEX.get(str(route_key), 0)


class MirrorRuleDialog(MessageBoxBase):
    """新增 / 编辑一条下载镜像规则。"""

    def __init__(self, parent: QWidget | None = None, *, rule: MirrorRule | None = None) -> None:
        super().__init__(parent)
        current = (rule or MirrorRule(id="", title="")).normalized()
        self._rule_id = current.id
        self._enabled = current.enabled

        self.titleLabel = SubtitleLabel("编辑下载规则" if rule else "新增下载规则", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(
            BodyLabel(
                "一条规则包含：匹配哪些地址、官方网址、镜像网址列表、以及启用模式。"
                "镜像地址里写 {url} 表示原始地址。",
                self,
            )
        )

        self.nameEdit = LineEdit(self)
        self.nameEdit.setText(current.title)
        self.nameEdit.setPlaceholderText("规则名称（例如 HuggingFace）")
        self.viewLayout.addWidget(self.nameEdit)

        self.patternEdit = LineEdit(self)
        self.patternEdit.setText(current.pattern)
        self.patternEdit.setPlaceholderText("匹配规则：huggingface.co / *.example.com / 完整网址前缀")
        self.patternEdit.textChanged.connect(self._sync_buttons)
        self.viewLayout.addWidget(self.patternEdit)

        self.officialEdit = LineEdit(self)
        self.officialEdit.setText(current.official)
        self.officialEdit.setPlaceholderText("官方网址（例如 https://huggingface.co）")
        self.viewLayout.addWidget(self.officialEdit)

        self.viewLayout.addWidget(BodyLabel("镜像网址（一行一个，按顺序回退，可用 {url} 表示原地址）：", self))
        self.mirrorsEdit = TextEdit(self)
        self.mirrorsEdit.setPlainText("\n".join(current.mirrors))
        self.mirrorsEdit.setMinimumHeight(110)
        self.mirrorsEdit.textChanged.connect(self._sync_buttons)
        self.viewLayout.addWidget(self.mirrorsEdit)

        mode_row = QHBoxLayout()
        mode_row.addWidget(BodyLabel("启用模式：", self))
        self.modeCombo = ComboBox(self)
        for mode in MODES:
            self.modeCombo.addItem(MODE_LABELS.get(mode, mode), userData=mode)
        for index in range(self.modeCombo.count()):
            if self.modeCombo.itemData(index) == current.mode:
                self.modeCombo.setCurrentIndex(index)
                break
        mode_row.addWidget(self.modeCombo, 1)
        self.viewLayout.addLayout(mode_row)

        self.viewLayout.addWidget(
            BodyLabel("顺序模式：先试官方网址，失败后按顺序换镜像；镜像模式：不碰官方，直接用镜像。", self)
        )

        self.enabledCheck = CheckBox("启用这条规则", self)
        self.enabledCheck.setChecked(current.enabled)
        self.viewLayout.addWidget(self.enabledCheck)

        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(560)
        self._sync_buttons()

    # ------------------------------------------------------------------ 取值
    def mirrors(self) -> tuple[str, ...]:
        return tuple(line.strip() for line in self.mirrorsEdit.toPlainText().splitlines() if line.strip())

    def rule(self) -> MirrorRule:
        return MirrorRule(
            id=self._rule_id or f"rule-{uuid4().hex[:8]}",
            pattern=self.patternEdit.text(),
            title=self.nameEdit.text(),
            official=self.officialEdit.text(),
            mirrors=self.mirrors(),
            mode=self.modeCombo.currentData() or MODE_SEQUENTIAL,
            enabled=self.enabledCheck.isChecked(),
        ).normalized()

    def _sync_buttons(self) -> None:
        usable = bool(self.patternEdit.text().strip() or self.mirrors())
        self.yesButton.setEnabled(usable)


class DownloadPage(Page):
    page_name = "downloadPage"
    page_title = "下载管理"
    page_subtitle = "程序本体里启动的下载都在这里；镜像规则与相关设置走「下载设置」"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rules = mirror_store.load_rules()

        open_dir = IconTextButton(FluentIcon.FOLDER, "打开下载目录", self)
        open_dir.clicked.connect(self._on_open_download_dir)
        self.header.add_action(open_dir)
        self.open_dir_button = open_dir

        self.tabs = SegmentedWidget(self)
        for route_key, label in DOWNLOAD_TABS:
            self.tabs.addItem(route_key, label)
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self.tabs)
        tabs_row.addStretch(1)
        self.add_row(tabs_row)

        self.stack = QStackedWidget(self)
        self.add_widget(self.stack, 1)

        self.list_view = DownloadListView(self.stack)
        self.stack.addWidget(self.list_view)

        self.settings_area = ScrollArea(self.stack)
        self.settings_area.setWidgetResizable(True)
        self.settings_host = QWidget(self.settings_area)
        self.settings_host.setObjectName("downloadSettingsHost")
        self.settings_body = QVBoxLayout(self.settings_host)
        self.settings_body.setContentsMargins(*PANEL_MARGINS)
        self.settings_body.setSpacing(CARD_SPACING)
        self.settings_area.setWidget(self.settings_host)
        clear_scroll_background(self.settings_area)
        self.stack.addWidget(self.settings_area)

        self._build_settings()

        self.tabs.currentItemChanged.connect(self._on_tab_changed)
        self.tabs.setCurrentItem(TAB_LIST)

    # ------------------------------------------------------------------ 装配
    def _build_settings(self) -> None:
        body = self.settings_body

        path_group = SettingCardGroup("下载路径", self.settings_host)
        self.path_card = ActionCard("选择目录", FluentIcon.FOLDER, "默认下载路径", "", path_group)
        self.path_card.clicked.connect(self._choose_download_dir)
        path_group.addSettingCard(self.path_card)
        body.addWidget(path_group)

        speed_group = SettingCardGroup("下载方式", self.settings_host)
        self.sequential_card = SwitchSettingCard(
            FluentIcon.SYNC,
            "顺序下载",
            "一次只跑一个任务；关闭后按并行数同时下载（切换后立即生效）",
            configItem=config.downloadSequential,
            parent=speed_group,
        )
        self.sequential_card.checkedChanged.connect(lambda *_: self._apply_settings())
        speed_group.addSettingCard(self.sequential_card)

        self.concurrent_card = NumberSettingCard(
            config.downloadConcurrent,
            FluentIcon.SPEED_HIGH,
            "并行下载数",
            "同时下载的任务数上限；超出上限的任务自动暂停，调大后按列表顺序自动接着下",
            speed_group,
        )
        self.concurrent_card.valueChanged.connect(lambda *_: self._apply_settings())
        speed_group.addSettingCard(self.concurrent_card)

        self.timeout_card = NumberSettingCard(
            config.downloadTimeout,
            FluentIcon.HISTORY,
            "连接超时",
            "单次网络请求的等待秒数，超时后按重试次数重试",
            speed_group,
        )
        self.timeout_card.valueChanged.connect(lambda *_: self._apply_settings())
        speed_group.addSettingCard(self.timeout_card)

        self.retries_card = NumberSettingCard(
            config.downloadRetries,
            FluentIcon.UPDATE,
            "失败重试次数",
            "一个地址失败后最多再试几次，全部失败才换下一个地址",
            speed_group,
        )
        self.retries_card.valueChanged.connect(lambda *_: self._apply_settings())
        speed_group.addSettingCard(self.retries_card)
        body.addWidget(speed_group)

        proxy_group = SettingCardGroup("网络", self.settings_host)
        self.proxy_card = ActionCard("修改代理", FluentIcon.GLOBE, "下载代理", "", proxy_group)
        self.proxy_card.clicked.connect(self._choose_proxy)
        proxy_group.addSettingCard(self.proxy_card)
        body.addWidget(proxy_group)

        rules_group = SettingCardGroup("下载规则", self.settings_host)
        self.newRuleCard = ActionCard("新建规则", FluentIcon.ADD, "下载镜像规则", "按匹配顺序回退到镜像网址", rules_group)
        self.newRuleCard.clicked.connect(self._add_rule)
        rules_group.addSettingCard(self.newRuleCard)
        body.addWidget(rules_group)

        self.ruleTable = TableWidget(self.settings_host)
        self.ruleTable.setColumnCount(len(RULE_HEADERS))
        self.ruleTable.setHorizontalHeaderLabels(list(RULE_HEADERS))
        prepare_table(self.ruleTable, movable=True)
        self.ruleTable.setBorderVisible(True)
        self.ruleTable.setBorderRadius(8)
        self.ruleTable.setMinimumHeight(220)
        self.ruleTable.currentCellChanged.connect(lambda *_: self._sync_rule_buttons())
        self.ruleTable.doubleClicked.connect(lambda *_: self._edit_rule())
        body.addWidget(self.ruleTable)

        rule_actions = QHBoxLayout()
        rule_actions.setSpacing(CARD_SPACING)
        self.addRuleButton = IconTextButton(FluentIcon.ADD, "新增", self.settings_host)
        self.addRuleButton.clicked.connect(self._add_rule)
        rule_actions.addWidget(self.addRuleButton)
        self.editRuleButton = IconTextButton(FluentIcon.EDIT, "编辑", self.settings_host)
        self.editRuleButton.clicked.connect(self._edit_rule)
        rule_actions.addWidget(self.editRuleButton)
        self.removeRuleButton = IconTextButton(FluentIcon.DELETE, "删除", self.settings_host)
        self.removeRuleButton.clicked.connect(self._delete_rule)
        rule_actions.addWidget(self.removeRuleButton)
        self.upRuleButton = IconTextButton(FluentIcon.UP, "上移", self.settings_host)
        self.upRuleButton.clicked.connect(lambda: self._move_rule(-1))
        rule_actions.addWidget(self.upRuleButton)
        self.downRuleButton = IconTextButton(FluentIcon.DOWN, "下移", self.settings_host)
        self.downRuleButton.clicked.connect(lambda: self._move_rule(1))
        rule_actions.addWidget(self.downRuleButton)
        rule_actions.addStretch(1)
        self.resetRuleButton = IconTextButton(FluentIcon.UPDATE, "恢复默认规则", self.settings_host)
        self.resetRuleButton.setToolTip("把镜像规则恢复成内置的 HuggingFace / GitHub 两条")
        self.resetRuleButton.clicked.connect(self._reset_rules)
        rule_actions.addWidget(self.resetRuleButton)
        body.addLayout(rule_actions)

        self.ruleHintLabel = BodyLabel("", self.settings_host)
        self.ruleHintLabel.setWordWrap(True)
        body.addWidget(self.ruleHintLabel)

        self.settings_empty = empty_state(self.settings_host, "还没有下载规则", icon=FluentIcon.GLOBE)
        self.settings_empty.setVisible(False)
        body.addWidget(self.settings_empty)
        body.addStretch(1)

        self._sync_settings_labels()
        self._refresh_rules_table()

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        self.list_view.refresh()
        self._sync_settings_labels()
        self._refresh_rules_table()

    def showEvent(self, event) -> None:  # noqa: N802
        Page.showEvent(self, event)
        self.list_view.refresh()
        self._flush_pending_refresh()

    def _sync_settings_labels(self) -> None:
        path = download_dir()
        self.path_card.setContent(str(path) if config.downloadPath.value else f"{path}（默认）")
        proxy = config.downloadProxy.value.strip()
        self.proxy_card.setContent(proxy or "未设置代理：跟随系统设置")
        self.ruleHintLabel.setText(
            f"镜像规则：一个规则包含官方网址与其镜像网址，按列表顺序回退；" f"{mirror_store.rule_hint()}。"
        )

    def _refresh_rules_table(self) -> None:
        rules = list(self._rules.rules)
        self.ruleTable.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            item = rule.normalized()
            values = (
                item.label,
                item.pattern or "（默认规则）",
                item.mode_label,
                item.official or "—",
                "；".join(item.mirrors) or "—",
                "启用" if item.enabled else "停用",
            )
            for column, text in enumerate(values):
                cell = self.ruleTable.item(row, column)
                if cell is None:
                    cell = QTableWidgetItem("")
                    self.ruleTable.setItem(row, column, cell)
                cell.setText(text)
            self.ruleTable.setRowHeight(row, 28)
        fit_columns(self.ruleTable, min_width=80, max_width=280, weights={COL_RULE_MIRRORS: 0.4})
        self.settings_empty.setVisible(not rules)
        self.ruleTable.setVisible(bool(rules))
        self._sync_rule_buttons()

    def _selected_rule(self) -> tuple[int, MirrorRule] | tuple[None, None]:
        row = self.ruleTable.currentRow()
        rules = list(self._rules.rules)
        if 0 <= row < len(rules):
            return row, rules[row]
        return None, None

    def _sync_rule_buttons(self) -> None:
        row, _rule = self._selected_rule()
        selected = row is not None
        self.editRuleButton.setEnabled(selected)
        self.removeRuleButton.setEnabled(selected)
        self.upRuleButton.setEnabled(selected and row > 0)
        self.downRuleButton.setEnabled(selected and row < len(self._rules.rules) - 1)

    # ------------------------------------------------------------------ 规则操作
    def _save_rules(self, rules: MirrorRules, message: str = "下载规则已保存") -> None:
        self._rules = mirror_store.save_rules(rules)
        self._refresh_rules_table()
        self._apply_settings()
        self.toast_success(message)

    def _add_rule(self) -> None:
        dialog = MirrorRuleDialog(self, rule=None)
        if not dialog.exec():
            return
        self._save_rules(self._rules.with_rule(dialog.rule()), "已新增下载规则")

    def _edit_rule(self) -> None:
        row, rule = self._selected_rule()
        if rule is None:
            self.toast_info("请先选择一条规则")
            return
        dialog = MirrorRuleDialog(self, rule=rule)
        if not dialog.exec():
            return
        self._save_rules(self._rules.with_rule(dialog.rule(), index=row))

    def _delete_rule(self) -> None:
        _row, rule = self._selected_rule()
        if rule is None:
            return
        if not confirm(self, "删除下载规则", f"确定删除规则「{rule.label}」？删除后该地址不再走它的镜像。"):
            return
        self._save_rules(self._rules.without_rule(rule.normalized().id), "已删除下载规则")

    def _move_rule(self, offset: int) -> None:
        _row, rule = self._selected_rule()
        if rule is None:
            return
        moved = self._rules.moved(rule.normalized().id, offset)
        if moved is self._rules:
            return
        self._save_rules(moved, "已调整规则顺序")

    def _reset_rules(self) -> None:
        if not confirm(self, "恢复默认规则", "会把镜像规则整份换回内置的 HuggingFace / GitHub 两条，自定规则会丢失。"):
            return
        self._rules = mirror_store.reset_rules()
        self._refresh_rules_table()
        self._apply_settings()
        self.toast_success("已恢复默认规则")

    # ------------------------------------------------------------------ 设置操作
    def _apply_settings(self) -> None:
        """把改完的设置灌进正在跑的队列（没跑起来就等下次建队列时读取）。"""
        if not download_service.is_running():
            return
        try:
            download_service.configure()
        except Exception:
            self.toast_error("下载设置没有立即生效", "请到「设置 → 日志」查看原因")

    def _choose_download_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择默认下载路径", str(download_dir()))
        if not chosen:
            return
        config.set(config.downloadPath, chosen)
        self._sync_settings_labels()
        self.toast_success("已更新默认下载路径", chosen)

    def _choose_proxy(self) -> None:
        dialog = TextInputDialog(
            "下载代理",
            "http://127.0.0.1:7890",
            config.downloadProxy.value,
            self,
            hint="留空表示不设置代理、跟随系统。代理对所有下载任务生效，改完立即生效。",
        )
        if not dialog.exec():
            return
        config.set(config.downloadProxy, dialog.value())
        self._sync_settings_labels()
        self._apply_settings()
        self.toast_success("已更新下载代理")

    def _on_open_download_dir(self) -> None:
        folder = download_dir()
        if not open_path(folder):
            self.toast_warning("打开下载目录失败", str(folder))

    def _on_tab_changed(self, route_key: str) -> None:
        self.stack.setCurrentIndex(tab_index(route_key))


__all__ = ["DOWNLOAD_TABS", "DownloadPage", "MirrorRuleDialog", "tab_index"]
