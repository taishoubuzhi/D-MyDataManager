"""L2 页面检查：标签页与用户页的行语义、权限门槛、设置页与首页最近项。"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from .harness import ROOT, Case, build_window, check, dispose_window, ensure_app


# ------------------------------------------------------------------ 共用工具
def _widget_texts(root: QWidget) -> list[str]:
    """收集控件树里所有可读文本（沿用旧门禁 `_widget_texts` 的做法）。"""
    texts: list[str] = []
    for widget in root.findChildren(QWidget):
        getter = getattr(widget, "text", None)
        if not callable(getter):
            continue
        try:
            texts.append(str(getter() or ""))
        except Exception:  # 少数控件的 text() 需要参数，跳过即可
            continue
    return texts


def _fail(prefix: str, problems: list[str]) -> None:
    """把收集到的问题一次性抛出（沿用旧门禁的 problems 风格）。"""
    detail = "；".join(problems[:12])
    if len(problems) > 12:
        detail += f"；…另有 {len(problems) - 12} 项"
    assert not problems, f"{prefix}：{detail}"


def _set_usage_filter(page, mode: str, first: str, second: str = "") -> None:
    """像用户那样驱动「数据项数」的数值筛选（选模式 + 填输入框）。"""
    from app.ui.components.data_table import NUMBER_MODES

    field = page.filter_bar._fields["usage"]
    field.combo.setCurrentIndex([item[1] for item in NUMBER_MODES].index(mode))
    field.line.setText(first)
    field.line2.setText(second)
    page.filter_bar._sync_number(field.combo, field.line2)


def _boom(*args, **kwargs):
    """普通用户不该走到的确认框 / 输入框。"""
    raise AssertionError("普通用户不应看到系统级确认框")


class _ForbiddenFactory:
    """构造即代表越权触发了系统级服务。"""

    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("普通用户不应触发系统级操作")


class _ForbiddenService:
    """任何属性访问都代表越权调用了插件服务。"""

    def __getattr__(self, name: str):
        raise AssertionError("普通用户不应调用插件服务")


# ------------------------------------------------------------------ 标签页
@check("tag_page_rows", "pages")
def tag_page_rows(case: Case) -> None:
    """标签页行语义：表格列内容、全选/全不选与名称、归属筛选条联动。"""
    from app.repositories import TagRepository
    from app.services import TaxonomyService, UserService
    from app.ui.pages.tag_page import TAG_CHECK_COLUMN, TAG_HEADERS

    fixture, window = build_window(case)
    problems: list[str] = []
    name_own, name_other = "自检我的标签", "自检他人标签"
    try:
        page = window.tag_page
        users = UserService(page.session)
        repository = TagRepository(page.session)
        taxonomy = TaxonomyService(page.session)
        current_id = int(users.current_id() or 0)
        # 夹具造出来的标签默认是全局标签，这里补两个个人标签才能校验归属与可见性。
        mine = taxonomy.create_tag(name_own, user_id=current_id, is_global=False)
        theirs = taxonomy.create_tag(name_other, user_id=fixture.user_id, is_global=False)
        page.session.commit()
        if mine is None or theirs is None:
            problems.append("无法造出个人标签，归属与可见性判据不完整")
        page.refresh()
        tags = repository.all() if users.is_admin() else repository.all(user_id=current_id)
        table = page.table

        if table.rowCount() != len(tags):
            problems.append(f"标签表格行数 {table.rowCount()} 与可见标签数 {len(tags)} 不一致")
        if table.columnCount() != len(TAG_HEADERS):
            problems.append(f"标签表格列数 {table.columnCount()} 应为 {len(TAG_HEADERS)}")
        header = table.horizontalHeaderItem(TAG_CHECK_COLUMN)
        if header is None or header.text() != TAG_HEADERS[TAG_CHECK_COLUMN]:
            problems.append(f"第 {TAG_CHECK_COLUMN} 列表头应为「{TAG_HEADERS[TAG_CHECK_COLUMN]}」")

        for row, tag in enumerate(tags):
            if row >= table.rowCount():
                break
            cells = [table.item(row, column) for column in range(table.columnCount())]
            if any(cell is None for cell in cells):
                problems.append(f"第 {row} 行存在空单元格")
                continue
            if cells[1].text() != tag.name:
                problems.append(f"第 {row} 行名称 {cells[1].text()!r} 应为 {tag.name!r}")
            scope = "全局" if tag.is_global else "个人"
            if not cells[2].text().startswith(scope):
                problems.append(f"标签「{tag.name}」的归属 {cells[2].text()!r} 未以「{scope}」开头")
            if not cells[3].text().strip():
                problems.append(f"标签「{tag.name}」没有显示创建者")
            if tag.name == fixture.tag and cells[4].text() != "1":
                problems.append(f"标签「{fixture.tag}」的数据项数应为 1，实际 {cells[4].text()!r}")

        page.select_all()
        if len(page.checked_tags()) != len(page._visible_rows()):
            problems.append("全选后勾选数与可见行数不一致")
        page.select_none()
        if page.checked_tags():
            problems.append("全不选后仍保留勾选")

        global_names = set(repository.global_names())
        if not global_names:
            problems.append("库里没有全局标签，无法校验可见性")
        for info in users.list_users():
            user_id = int(info.user.id)
            names = {tag.name for tag in repository.all(user_id=user_id)}
            missing = global_names - names
            if missing:
                problems.append(f"用户 {info.name} 看不到全局标签 {sorted(missing)}")
            if str(info.name) != fixture.user_name and name_other in names:
                problems.append(f"用户 {info.name} 看到了「{fixture.user_name}」的个人标签「{name_other}」")
            if user_id == fixture.user_id and name_other not in names:
                problems.append(f"用户 {info.name} 看不到自己的个人标签「{name_other}」")
            if user_id != current_id and name_own in names:
                problems.append(f"用户 {info.name} 看到了他人的个人标签「{name_own}」")

        if not table.horizontalHeader().sectionsMovable():
            problems.append("标签表格表头不允许拖动列")
        counts: dict[str, int] = {}
        for tag in tags:
            counts[tag.name] = counts.get(tag.name, 0) + 1
        unique = next((name for name, count in counts.items() if count == 1), None)
        if unique is None:
            problems.append("没有名称唯一的标签，无法校验名称筛选")
        else:
            page.filter_bar.set_filter("name", unique)
            visible = page._visible_rows()
            if len(visible) != 1:
                problems.append(f"按名称筛选「{unique}」后可见行数应为 1，实际 {len(visible)}")
            expected = f"显示 1 / {len(tags)} 个标签"
            if page.filter_caption.text() != expected:
                problems.append(f"筛选提示 {page.filter_caption.text()!r} 应为 {expected!r}")
        page.filter_bar.set_filter("name", "绝不存在的标签")
        if page._visible_rows():
            problems.append("筛选一个不存在的标签后仍有可见行")
        page._on_reset_filters()
        if len(page._visible_rows()) != len(tags):
            problems.append("重置筛选后没有恢复全部标签")
        if table.selectionModel().hasSelection():
            problems.append("重置筛选后仍保留表格选择")
        page.filter_bar.set_filter("scope", "全局")
        for row, row_tag in page._visible_rows():
            if not row_tag.is_global:
                problems.append(f"按归属筛选「全局」后可见行里出现了个人标签「{row_tag.name}」")
            cell = table.item(row, 2)
            if cell is None or cell.text() != "全局":
                problems.append(f"按归属筛选「全局」后第 {row} 行的归属不是「全局」")
        page._on_reset_filters()

        usages = {index: float(number["usage"]) for index, number in enumerate(page._row_numbers)}
        top_use = max(usages.values())
        _set_usage_filter(page, "eq", str(int(top_use)))
        visible = {row for row, _tag in page._visible_rows()}
        expected = {row for row, value in usages.items() if value == top_use}
        if visible != expected:
            problems.append(f"「数据项数 等于 {int(top_use)}」命中行 {sorted(visible)} 应为 {sorted(expected)}")
        _set_usage_filter(page, "between", "0", str(int(top_use)))
        visible = {row for row, _tag in page._visible_rows()}
        expected = {row for row, value in usages.items() if 0 < value < top_use}
        if visible != expected:
            problems.append(
                f"「数据项数 区间 0~{int(top_use)}」命中行 {sorted(visible)} 应为 {sorted(expected)}"
            )
        _set_usage_filter(page, "lt", "0")
        if page._visible_rows():
            problems.append("「数据项数 小于 0」不应有可见行")
        page._on_reset_filters()
        if len(page._visible_rows()) != len(tags):
            problems.append("重置「数据项数」筛选后没有恢复全部标签")
    finally:
        dispose_window(window)
    _fail("标签页行语义", problems)


@check("global_tag_marks", "pages")
def global_tag_marks(case: Case) -> None:
    """全局标签在管理页筛选面板与导入页标签选择器上带「（全局）」标记且集合一致。"""
    from app.repositories import TagRepository
    from app.services import TaxonomyService, UserService

    _, window = build_window(case)
    problems: list[str] = []
    try:
        session = window.tag_page.session
        users = UserService(session)
        repository = TagRepository(session)
        # 夹具只造全局标签，补一个个人标签才能区分「（全局）」标记。
        created = TaxonomyService(session).create_tag(
            "自检个人标签", user_id=int(users.current_id() or 0), is_global=False
        )
        session.commit()
        window.manage_page.refresh()
        window.import_page.refresh()
        if created is None:
            problems.append("无法造出个人标签，无法区分全局与个人标记")
        names = set(repository.names(user_id=users.current_id()))
        global_names = set(repository.global_names())
        if not names:
            problems.append("当前用户没有任何可见标签，无法校验全局标记")
        if not global_names:
            problems.append("库里没有全局标签，无法校验全局标记")
        if not names - global_names:
            problems.append("当前用户没有个人标签，无法区分全局与个人标记")

        expected = {name: f"{name}（全局）" if name in global_names else name for name in names}
        boxes = dict(window.manage_page.filter_panel._tag_boxes)
        if set(boxes) != names:
            problems.append(
                "标签筛选面板的选项集合不一致：缺少 "
                f"{sorted(names - set(boxes))}，多出 {sorted(set(boxes) - names)}"
            )
        for name, box in boxes.items():
            want = expected.get(name, name)
            if box.text() != want:
                problems.append(f"筛选面板「{name}」的文本 {box.text()!r} 应为 {want!r}")

        picker = window.import_page.tag_input
        known = set(picker.known_tags())
        if known != names:
            problems.append(
                f"标签选择器的集合不一致：缺少 {sorted(names - known)}，多出 {sorted(known - names)}"
            )
        labels = {action.text() for action in picker._build_menu().actions()}
        for name in names:
            if expected[name] not in labels:
                problems.append(f"标签选择器缺少选项 {expected[name]!r}")
    finally:
        dispose_window(window)
    _fail("全局标签标记", problems)


# ------------------------------------------------------------------ 用户页
@check("user_page_rows", "pages")
def user_page_rows(case: Case) -> None:
    """用户页卡片行语义：当前用户标记、删除入口门槛、清空口令后任意口令可通过。"""
    from qfluentwidgets import PushButton

    from app.core.config import config
    from app.ui.pages import user_page as user_module
    from app.ui.pages.user_page import AVATAR_SIZE, CARD_SPACING, CARD_WIDTH, grid_columns

    _, window = build_window(case, show=True)
    app = ensure_app()
    problems: list[str] = []
    page = window.user_page
    original_text_input = user_module.TextInputDialog
    original_confirm = user_module.confirm
    original_mode = config.simpleDisplay.value
    original_state = (page._is_admin, page._user_id)
    service_state = (page.service.current_id, page.service.is_admin)
    try:
        # offscreen 下窗口不会自动撑开：先切到用户页并给足宽度，卡片几何才有意义。
        window.resize(1280, 900)
        window.switchTo(page)
        page.refresh()
        app.processEvents()
        if page.objectName() != "userPage":
            problems.append(f"用户页 objectName 为 {page.objectName()!r}，应为「userPage」")
        texts = _widget_texts(page)
        if not any("当前用户" in text for text in texts):
            problems.append("用户页上看不到「当前用户」标记")
        if not any("新建用户" in text for text in texts):
            problems.append("管理员看不到「新建用户」入口")

        def info_of(user_id: int):
            return next(
                (item for item in page.service.list_users() if int(item.user.id) == user_id), None
            )

        def card_of(user_id: int):
            return next((card for card in page.cards() if int(card.user_id) == user_id), None)

        def delete_button(card):
            return getattr(card, "delete_button", None)

        infos = page.service.list_users()
        default_info = next((item for item in infos if item.is_default), None)
        member_info = next((item for item in infos if not item.is_default), None)
        cards = page.cards()
        if len(cards) != len(infos):
            problems.append(f"用户卡片数 {len(cards)} 与用户数 {len(infos)} 不一致")
        current = page.current_card()
        if current is None:
            problems.append("找不到当前用户卡片")
        elif not current.is_highlighted():
            problems.append("当前用户卡片没有高亮")

        page._layout_cards(force=True)
        app.processEvents()
        viewport = page.scroll.viewport().width()
        heights = {card.height() for card in cards}
        if len(heights) > 1:
            problems.append(f"用户卡片高度不一致：{sorted(heights)}")
        for card in cards:
            right = card.geometry().right()
            if right > viewport:
                problems.append(f"用户卡片 {card.user_id} 越出滚动区域（右边界 {right} > 视口 {viewport}）")
            elif card.width() != CARD_WIDTH:
                problems.append(f"用户卡片 {card.user_id} 宽度应为 {CARD_WIDTH}，实际 {card.width()}")
            avatars = [
                label
                for label in card.findChildren(QLabel)
                if label.width() == AVATAR_SIZE and label.height() == AVATAR_SIZE
            ]
            if len(avatars) != 1:
                problems.append(f"用户卡片 {card.user_id} 的首字头像数量应为 1，实际 {len(avatars)}")
            from PyQt6.QtCore import QPoint

            from app.ui.components import FlowArea

            buttons = card.findChildren(PushButton)
            if not buttons:
                problems.append(f"用户卡片 {card.user_id} 缺少操作按钮")
            if not isinstance(getattr(card, "actions_area", None), FlowArea):
                problems.append(f"用户卡片 {card.user_id} 的操作按钮没有放在流式容器里")
            for button in buttons:
                # 按钮的父控件是流式容器，位置要先映射回卡片坐标再比
                point = button.mapTo(card, QPoint(0, 0))
                if (
                    point.x() + button.width() > card.width()
                    or point.y() + button.height() > card.height()
                ):
                    problems.append(f"用户卡片 {card.user_id} 的按钮被裁出卡片范围")

        # 页面不可见时切挡位：操作区那时量不到宽度，重新进入用户页必须再等高一次，否则按钮被裁
        window.switchTo(window.settings_page)
        app.processEvents()
        config.set(config.simpleDisplay, "full")
        app.processEvents()
        config.set(config.simpleDisplay, "none")
        app.processEvents()
        window.switchTo(page)
        for _ in range(4):
            app.processEvents()
        from PyQt6.QtCore import QPoint

        for card in page.cards():
            bottom = 0
            for button in card.findChildren(PushButton):
                point = button.mapTo(card, QPoint(0, 0))
                bottom = max(bottom, point.y() + button.height())
            if bottom > card.height():
                problems.append(
                    f"用户卡片 {card.user_id} 从别的页面切回来没有重新等高，按钮被裁出卡片"
                )

        if page.grid.columnStretch(page._columns) != 1:
            problems.append("用户卡片网格缺少占位伸缩列，卡片不会被左对齐")
        if page.grid.columnStretch(0) != 0:
            problems.append("用户卡片列被设置了拉伸，卡片宽度会随窗口变化")
        available = 700 - 24
        columns = min(grid_columns(available, card_width=CARD_WIDTH), len(cards))
        needed = columns * CARD_WIDTH + (columns - 1) * CARD_SPACING
        if needed > available:
            problems.append(f"700px 视口下卡片网格放不下：需要 {needed}px > {available}px")

        if default_info is not None and delete_button(card_of(int(default_info.user.id))) is not None:
            problems.append("默认用户卡片不该有「删除」入口")
        if member_info is not None:
            button = delete_button(card_of(int(member_info.user.id)))
            if button is None or not button.isEnabled():
                problems.append("默认用户应能删除其他用户，卡片上的「删除」按钮却不可用")

        def _no_confirm(*args, **kwargs):
            raise AssertionError("删除当前用户时不应弹确认框")

        if member_info is not None:
            member_id = int(member_info.user.id)
            page.service.current_id = lambda: member_id
            page.service.is_admin = lambda: False
            page.refresh()
            app.processEvents()
            card = card_of(member_id)
            if card is None:
                problems.append("切换当前用户后找不到他的卡片")
            else:
                if delete_button(card) is None or delete_button(card).isEnabled():
                    problems.append("当前用户卡片上的「删除」按钮应存在但禁用")
                if any(
                    delete_button(other) is not None and delete_button(other).isEnabled()
                    for other in page.cards()
                ):
                    problems.append("普通用户的卡片上不应有可用的「删除」按钮")
                user_module.confirm = _no_confirm
                try:
                    page._delete_user(card._info)
                except AssertionError as exc:
                    problems.append(f"普通用户删除自己时未被拒绝：{exc}")
                finally:
                    user_module.confirm = original_confirm
        page.service.current_id, page.service.is_admin = service_state
        page._is_admin, page._user_id = original_state
        page.refresh()
        app.processEvents()

        if member_info is None:
            problems.append("夹具里没有第二个用户，无法校验口令与删除门槛")
        else:
            member_id = int(member_info.user.id)
            page.service.set_password(info_of(member_id).user, "临时口令")
            page.session.commit()
            page.refresh()
            app.processEvents()
            labels = {str(button.text() or "") for button in page.findChildren(QPushButton)}
            if not any("清除口令" in text for text in labels):
                problems.append("已设口令的用户卡片上看不到「清除口令」入口")
            if not info_of(member_id).protected:
                problems.append("设置口令后用户仍被标记为未设口令")
            if page.service.verify(info_of(member_id).user, "错误口令"):
                problems.append("错误口令通过了校验")
            default_id = int(default_info.user.id) if default_info is not None else 0
            page._is_admin, page._user_id = False, default_id
            page._clear_password(info_of(member_id))
            if not info_of(member_id).protected:
                problems.append("普通用户可以清掉其他用户的口令")
            page._is_admin, page._user_id = original_state
            user_module.confirm = lambda *args, **kwargs: True
            try:
                page._clear_password(info_of(member_id))
            finally:
                user_module.confirm = original_confirm
            if info_of(member_id).protected:
                problems.append("默认用户清除口令失败，用户仍显示已设口令")
            if not page.service.verify(info_of(member_id).user, "任意口令"):
                problems.append("清空口令后任意口令仍不能通过校验")
    finally:
        user_module.TextInputDialog = original_text_input
        user_module.confirm = original_confirm
        config.set(config.simpleDisplay, original_mode)
        page.service.current_id, page.service.is_admin = service_state
        page._is_admin, page._user_id = original_state
        dispose_window(window)
    _fail("用户页行语义", problems)


# ------------------------------------------------------------------ 权限门槛
@check("superuser_permissions", "pages")
def superuser_permissions(case: Case) -> None:
    """非管理员在设置页 / 插件页 / 标签页 / 用户页被正确限制，越权操作被拒绝而不报错。"""
    from app.ui.pages import plugin_page as plugin_module
    from app.ui.pages import settings_page as settings_module
    from app.ui.pages import tag_page as tag_module
    from app.ui.pages import user_page as user_module

    fixture, window = build_window(case)
    app = ensure_app()
    problems: list[str] = []
    settings_page = window.settings_page
    plugin_page = window.plugin_page
    tag_page = window.tag_page
    user_page = window.user_page
    settings_admin = settings_page._is_admin
    plugin_admin = plugin_page._is_admin
    tag_state = (tag_page._is_admin, tag_page._user_id)
    user_state = (user_page._is_admin, user_page._user_id)
    plugin_service = plugin_page.service
    settings_factory = settings_module.LibraryService
    settings_confirm = settings_module.confirm
    plugin_input = plugin_module.TextInputDialog
    plugin_confirm = plugin_module.confirm
    tag_input = tag_module.TextInputDialog
    tag_confirm = tag_module.confirm
    user_input = user_module.TextInputDialog
    user_confirm = user_module.confirm
    member_id = fixture.user_id
    try:
        # ---- 设置页：系统级卡片只对默认用户开放
        settings_page._is_admin = False
        settings_page._apply_permissions()
        for label, card in (
            ("更改资料库位置", settings_page._path_card),
            ("扫描并登记", settings_page._scan_card),
            ("重建目录结构", settings_page._rebuild_card),
            ("恢复初始化", settings_page._reset_card),
        ):
            if card.isEnabled():
                problems.append(f"普通用户仍可点击设置页的「{label}」")
        for label, card in (
            ("资料库权限", settings_page._library_permission_card),
            ("系统维护权限", settings_page._maintenance_permission_card),
        ):
            if card.isHidden():
                problems.append(f"普通用户看不到设置页的「{label}」说明")
        settings_module.LibraryService = _ForbiddenFactory
        settings_module.confirm = _boom
        try:
            for label, action in (
                ("恢复初始化", settings_page._reset_to_defaults),
                ("扫描并登记", settings_page._scan_library),
                ("重建目录结构", settings_page._rebuild_layout),
            ):
                try:
                    action()
                except AssertionError as exc:
                    problems.append(f"普通用户绕过了设置页的系统级权限校验（{label}）：{exc}")
        finally:
            settings_module.LibraryService = settings_factory
            settings_module.confirm = settings_confirm
        settings_page._is_admin = settings_admin
        settings_page._apply_permissions()
        for label, card in (
            ("更改资料库位置", settings_page._path_card),
            ("扫描并登记", settings_page._scan_card),
            ("重建目录结构", settings_page._rebuild_card),
            ("恢复初始化", settings_page._reset_card),
        ):
            if not card.isEnabled():
                problems.append(f"管理员无法「{label}」")
        for label, card in (
            ("资料库权限", settings_page._library_permission_card),
            ("系统维护权限", settings_page._maintenance_permission_card),
        ):
            if not card.isHidden():
                problems.append(f"管理员仍看到「{label}」说明")

        # ---- 插件页：插件是系统级资源，只有默认用户可以改动
        # 回归点 1：插件列表为空（currentRow() == -1）时详情按钮没有作用对象，必须统一
        # 禁用，不能停在构造时的可用状态。
        plugin_page.refresh()
        app.processEvents()
        if plugin_page.service.all():
            problems.append("隔离夹具里插件列表不为空，无法校验空列表下的插件页权限态")
        else:
            if plugin_page.plugin_list.currentRow() != -1:
                problems.append(
                    f"插件列表为空时 currentRow() 应为 -1，实际 {plugin_page.plugin_list.currentRow()}"
                )
            plugin_page._is_admin = False
            plugin_page.select_all()
            plugin_page._apply_permissions()
            for label, button in (
                ("启用 / 停用插件", plugin_page.toggle_button),
                ("插件更多选项", plugin_page.options_button),
                ("删除插件", plugin_page.delete_button),
                ("定位插件文件夹", plugin_page.reveal_button),
            ):
                if button.isEnabled():
                    problems.append(f"插件列表为空时普通用户仍可点击插件页的「{label}」")

        # 回归点 2：装了插件、有选中行时，非管理员下 11 个改动按钮全部禁用。
        # 隔离夹具的插件目录是空的，先导入一个仓库内置插件让列表非空、有选中行，
        # 否则 _sync_detail 不会同步「启用 / 更多选项 / 删除」的可用态。
        sample = ROOT / "plugins" / "builtin.lib.ui"
        if not sample.exists():
            problems.append(f"找不到内置插件目录 {sample}，插件页权限判据不完整")
        else:
            try:
                plugin_page.service.import_plugin(sample)
            except Exception as exc:  # 导入失败必须让检查失败，而不是跳过判据
                problems.append(f"无法导入内置插件以供权限检查：{type(exc).__name__}: {exc}")
            plugin_page.refresh()
            app.processEvents()
        if plugin_page.plugin_list.currentRow() < 0:
            problems.append("插件列表没有选中行，无法校验插件页的权限态")
        plugin_page._is_admin = False
        plugin_page.select_all()
        plugin_page._apply_permissions()
        for label, button in (
            ("打包插件", plugin_page._zip_button),
            ("打开文件夹", plugin_page._folder_button),
            ("重命名插件", plugin_page._name_button),
            ("修改插件描述", plugin_page._description_button),
            ("编辑插件备注", plugin_page._note_button),
            ("批量启用", plugin_page.batch_enable_button),
            ("批量停用", plugin_page.batch_disable_button),
            ("批量删除", plugin_page.batch_remove_button),
        ):
            if button.isEnabled():
                problems.append(f"普通用户仍可点击插件页的「{label}」")
        for label, button in (
            ("启用 / 停用插件", plugin_page.toggle_button),
            ("插件更多选项", plugin_page.options_button),
            ("删除插件", plugin_page.delete_button),
        ):
            if button.isEnabled():
                problems.append(f"普通用户仍可点击插件页的「{label}」")
        if not plugin_page.reveal_button.isEnabled():
            problems.append("普通用户应能定位插件文件夹")
        if plugin_page.permission_hint.isHidden():
            problems.append("普通用户看不到插件页的权限说明")
        plugin_module.TextInputDialog = _boom
        plugin_module.confirm = _boom
        plugin_page.service = _ForbiddenService()
        try:
            for label, action in (
                ("重命名插件", lambda: plugin_page._on_edit("name")),
                ("删除插件", plugin_page._on_remove),
                ("批量删除", plugin_page._on_batch_remove),
                ("批量停用", lambda: plugin_page._on_batch_toggle(True)),
                ("启用 / 停用插件", plugin_page._on_toggle),
                ("安装插件", lambda: plugin_page._install(str(case.root), "自检")),
            ):
                try:
                    action()
                except AssertionError as exc:
                    problems.append(f"普通用户绕过了插件页的权限校验（{label}）：{exc}")
        finally:
            plugin_page.service = plugin_service
            plugin_module.TextInputDialog = plugin_input
            plugin_module.confirm = plugin_confirm
        plugin_page._is_admin = plugin_admin
        plugin_page._apply_permissions()
        if not plugin_page._zip_button.isEnabled():
            problems.append("管理员无法打包插件")
        if not plugin_page._name_button.isEnabled():
            problems.append("管理员无法重命名插件")
        if not plugin_page.permission_hint.isHidden():
            problems.append("管理员仍看到插件页的权限说明")

        # ---- 标签页：普通用户只能管理自己创建的标签
        # refresh() 会从服务层重读身份，必须刷新之后再降级成普通用户。
        tag_page.refresh()
        tag_page._is_admin, tag_page._user_id = False, member_id
        global_tag = next(
            (tag for _, tag in tag_page._visible_rows() if bool(tag.is_global)), None
        )
        if global_tag is None:
            problems.append("普通用户看不到全局标签，无法校验标签页权限")
        else:
            if tag_page._require_manage(global_tag):
                problems.append("普通用户可以管理全局标签")
            row = next(
                (
                    index
                    for index, (_, tag) in enumerate(tag_page._visible_rows())
                    if int(tag.id) == int(global_tag.id)
                ),
                None,
            )
            if row is None:
                problems.append("在标签表格里找不到刚选中的全局标签")
            else:
                tag_page.table.setCurrentCell(row, 0)
            tag_module.TextInputDialog = _boom
            tag_module.confirm = _boom
            tag_page.select_all()
            try:
                for label, action in (
                    ("重命名标签", tag_page._on_rename),
                    ("删除标签", tag_page._on_delete),
                    ("转为个人标签", tag_page._on_to_personal),
                    ("转为全局标签", tag_page._on_to_global),
                    ("批量删除标签", tag_page._on_batch_delete),
                    ("批量转为全局标签", lambda: tag_page._on_batch_switch_global(True)),
                ):
                    try:
                        action()
                    except AssertionError as exc:
                        problems.append(f"普通用户在标签页越权（{label}）：{exc}")
            finally:
                tag_module.TextInputDialog = tag_input
                tag_module.confirm = tag_confirm

        # ---- 用户页：普通用户只能动自己
        user_page.refresh()
        user_page._is_admin, user_page._user_id = False, member_id
        app.processEvents()
        other_info = next(
            (item for item in user_page.service.list_users() if int(item.user.id) != member_id),
            None,
        )
        if other_info is None:
            problems.append("只有当前用户，无法校验用户页越权拦截")
        else:
            user_module.TextInputDialog = _boom
            user_module.confirm = _boom
            try:
                for label, action in (
                    ("重命名用户", lambda: user_page._rename_user(other_info)),
                    ("修改口令", lambda: user_page._set_password(other_info)),
                    ("删除用户", lambda: user_page._delete_user(other_info)),
                ):
                    try:
                        action()
                    except AssertionError as exc:
                        problems.append(f"普通用户在用户页越权（{label}）：{exc}")
            finally:
                user_module.TextInputDialog = user_input
                user_module.confirm = user_confirm
    finally:
        settings_module.LibraryService = settings_factory
        settings_module.confirm = settings_confirm
        plugin_module.TextInputDialog = plugin_input
        plugin_module.confirm = plugin_confirm
        tag_module.TextInputDialog = tag_input
        tag_module.confirm = tag_confirm
        user_module.TextInputDialog = user_input
        user_module.confirm = user_confirm
        plugin_page.service = plugin_service
        settings_page._is_admin = settings_admin
        plugin_page._is_admin = plugin_admin
        tag_page._is_admin, tag_page._user_id = tag_state
        user_page._is_admin, user_page._user_id = user_state
        try:
            settings_page._apply_permissions()
            plugin_page._apply_permissions()
            tag_page.refresh()
            user_page.refresh()
        except Exception:
            pass
        dispose_window(window)
    _fail("权限门槛", problems)


# ------------------------------------------------------------------ 设置页
@check("settings_privacy_group", "pages")
def settings_privacy_group(case: Case) -> None:
    """设置页附加项与隐私分组：日志模式联动、资源卡片指向 .resources、开关只记设置。"""
    from app.core import acl, logging_setup
    from app.core.config import config, resources_root
    from app.services.privacy_service import privacy
    from app.ui.framework import restart_application

    _, window = build_window(case)
    problems: list[str] = []
    page = window.settings_page
    protected_key = bool(config.resourceProtected.value)
    hidden_key = bool(config.hiddenProtected.value)
    original_mode = str(config.logMode.value)
    real_lock, real_unlock = acl.lock, acl.unlock
    real_children, real_release = acl._children, acl.remove_deny
    calls: list[str] = []
    try:
        texts = _widget_texts(page)
        if any("数据仓库" in text for text in texts):
            problems.append("设置页仍然显示已删除的「数据仓库」配置项")
        if not any("日志文件模式" in text for text in texts):
            problems.append("设置页缺少「日志文件模式」选项")
        for name in (
            "_log_keep_files_card",
            "_log_max_file_card",
            "_log_keep_days_card",
            "_log_total_card",
        ):
            if not hasattr(page, name):
                problems.append(f"设置页缺少日志卡片 {name}")
        if not callable(restart_application):
            problems.append("app.ui.framework.restart_application 不可调用")
        page._on_log_mode_changed(logging_setup.MODE_DAILY)
        if not page._log_keep_days_card.isEnabled():
            problems.append("按天切分时没有启用「日志保留天数」")
        if page._log_max_file_card.isEnabled():
            problems.append("按天切分时仍启用「单个日志文件大小上限」")
        page._on_log_mode_changed(logging_setup.MODE_SIZE)
        if not page._log_max_file_card.isEnabled():
            problems.append("按大小切分时没有启用「单个日志文件大小上限」")
        if page._log_keep_days_card.isEnabled():
            problems.append("按大小切分时仍启用「日志保留天数」")
        page._on_log_mode_changed(original_mode)

        for name in ("_resource_switch", "_hidden_switch", "_path_card"):
            if not hasattr(page, name):
                problems.append(f"设置页缺少隐私相关控件 {name}")
        texts = _widget_texts(page)
        for wanted in ("保护资源文件夹", "保护隐藏文件"):
            if not any(wanted in text for text in texts):
                problems.append(f"设置页缺少「{wanted}」")
        joined = "".join(texts)
        for unused in ("立即锁定", "立即放行"):
            if unused in joined:
                problems.append(f"隐私分组仍显示无用的「{unused}」按钮")
        if resources_root().name != ".resources":
            problems.append(f"资源文件夹未落在 .resources：{resources_root()}")
        if not page._path_card.contentLabel.text().endswith(".resources"):
            problems.append(f"资源卡片未显示资源文件夹：{page._path_card.contentLabel.text()!r}")
        if not privacy.state_text():
            problems.append("隐私状态文案为空")

        acl.lock = lambda path, **kwargs: (calls.append(f"lock:{path}"), (True, ""))[1]
        acl.unlock = lambda path: (calls.append(f"unlock:{path}"), (True, ""))[1]
        acl._children = lambda root: []
        acl.remove_deny = lambda path: (calls.append(f"release:{path}"), (True, ""))[1]
        config.set(config.resourceProtected, True)
        config.set(config.hiddenProtected, True)
        page._normalize_privacy()
        page._refresh_privacy()
        if bool(config.hiddenProtected.value):
            problems.append("资源文件夹已受保护时仍保留隐藏文件夹保护开关")
        if page._hidden_switch.isEnabled():
            problems.append("资源文件夹已受保护时隐藏文件夹开关仍可用")
        if "退出后" not in "".join(_widget_texts(page)):
            problems.append("隐私分组没有说明「退出后」才锁定")
        page._on_protection_changed()
        if calls:
            problems.append(f"打开保护开关不应该立刻改动 ACL，却调用了 {calls}")
        config.set(config.resourceProtected, False)
        page._on_protection_changed()
        if not any(call.startswith("unlock:") for call in calls):
            problems.append("关闭资源文件夹保护后没有立刻放行")
        page._refresh_privacy()
        if not page._hidden_switch.isEnabled():
            problems.append("资源文件夹不再受保护后隐藏文件夹开关仍不可用")
    finally:
        config.set(config.resourceProtected, protected_key)
        config.set(config.hiddenProtected, hidden_key)
        acl.lock, acl.unlock = real_lock, real_unlock
        acl._children, acl.remove_deny = real_children, real_release
        try:
            page._refresh_privacy()
        except Exception:
            pass
        dispose_window(window)
    _fail("设置页与隐私分组", problems)


# ------------------------------------------------------------------ 首页最近项
@check("recent_focus", "flows")
def recent_focus(case: Case) -> None:
    """首页「最近」条目点击后跳到数据管理页并聚焦该数据项。"""
    from app.core.signals import signalBus
    from app.ui.pages.home_page import _Row

    _, window = build_window(case, show=True)
    app = ensure_app()
    problems: list[str] = []
    try:
        home = window.home_page
        window.switchTo(home)
        home.refresh()
        app.processEvents()
        rows = home.findChildren(_Row)
        if not rows:
            problems.append("首页「最近」区没有条目")
        else:
            item_id = int(rows[0]._item_id)
            manage = window.manage_page
            item = manage.item_repo.get(item_id)
            captured: list[int] = []
            slot = captured.append
            signalBus.focusItem.connect(slot)
            try:
                rows[0].clicked.emit()
                app.processEvents()
            finally:
                try:
                    signalBus.focusItem.disconnect(slot)
                except TypeError:
                    pass
            if captured != [item_id]:
                problems.append(f"点击最近条目发出的 item_id 为 {captured}，应为 [{item_id}]")
            if window.stackedWidget.currentWidget() is not manage:
                problems.append("点击最近条目后没有跳到数据管理页")
            if manage._selected != {item_id}:
                problems.append(f"数据管理页的选中项为 {manage._selected}，应为 {{{item_id}}}")
            if item is not None:
                if manage._category_id != item.category_id:
                    problems.append(
                        f"数据管理页的分类为 {manage._category_id}，应为 {item.category_id}"
                    )
                if item.category_id is not None and manage.tree.current_category() != item.category_id:
                    problems.append("数据管理页分类树没有聚焦到该数据项所在分类")
    finally:
        dispose_window(window)
    _fail("首页最近项聚焦", problems)
