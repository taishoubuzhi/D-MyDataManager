"""服务层检查：导入 / 导出 / 存档 / 用户 / 统计的公开契约。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from .fixtures import SAMPLE_IMAGE
from .harness import Case, check


@check("import_text_features", "services")
def import_text_features(case: Case) -> None:
    """导入文本项：名字 / 大小 / 校验和落库，同级同名分类被拒绝，关键词生成特征。"""
    from app.db.models import Feature
    from app.repositories import CategoryRepository
    from app.services import ImportService, TaxonomyService

    session = case.session
    taxonomy = TaxonomyService(session)
    root = CategoryRepository(session).by_name("学习资料")
    assert root is not None, "默认分类「学习资料」缺失"
    child = taxonomy.create_category("Python", parent_id=root.id)
    assert child is not None, "创建子分类失败"
    assert taxonomy.create_category("Python", parent_id=root.id) is None, "同级同名分类应被拒绝"
    assert taxonomy.create_tag("示例标签", description="自检用") is not None, "创建标签失败"

    item = ImportService(session).import_text(
        "测试笔记", "第一行\n第二行", category_id=child.id, keywords=["笔记"], tags=["示例标签"]
    )
    session.commit()
    assert item is not None, "import_text 返回空"
    assert item.name == "测试笔记", f"名字不对：{item.name}"
    assert item.size > 0, "文本项大小应为正"
    assert len(item.checksum) == 64, f"校验和长度不对：{item.checksum!r}"
    assert "示例标签" in item.tag_names, f"标签没挂上：{item.tag_names}"
    assert "笔记" in (item.keywords or []), f"关键词没落库：{item.keywords}"
    features = [
        (feature.kind, str(feature.value))
        for feature in session.query(Feature).filter(Feature.item_id == item.id)
    ]
    assert features, "导入后没有生成任何特征"
    kinds = {kind for kind, _ in features}
    assert "text" in kinds, f"文本特征缺失：{features}"
    assert any("lines" in value for kind, value in features if kind == "text"), f"文本特征内容异常：{features}"


@check("import_file_cover", "services")
def import_file_cover(case: Case) -> None:
    """导入真实图片：类型 / 大小 / 封面 / 库内文件都在。"""
    from app.services import ImportService, LibraryService

    session = case.session
    assert SAMPLE_IMAGE.exists(), f"缺少示例图片：{SAMPLE_IMAGE}"
    result = ImportService(session).import_files([SAMPLE_IMAGE])
    session.commit()
    assert not result.failed, f"导入失败：{result.failed}"
    assert result.added, "没有导入任何项"
    item = result.added[0]
    assert item.type_name, "缺少类型名"
    assert item.size > 0, "文件项大小应为正"
    assert item.cover_path, "图片项没有生成封面"
    path = LibraryService(session).abs_path(item)
    assert path is not None and Path(path).exists(), f"库内文件不存在：{path}"


@check("item_lifecycle", "services")
def item_lifecycle(case: Case) -> None:
    """改名 / 打标签 / 删除进回收站 / 还原 / 重复检测。"""
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ImportService, ItemService, TaxonomyService

    session = case.session
    assert TaxonomyService(session).create_tag("自检标签乙") is not None, "创建标签失败"
    item = ImportService(session).import_text("原始笔记", "内容", keywords=["笔记"])
    session.commit()
    assert item is not None, "导入文本项失败"

    service = ItemService(session)
    service.update(item, name="改名后的笔记", tags=["自检标签乙"])
    session.commit()
    assert item.name == "改名后的笔记", f"改名失败：{item.name}"
    assert "自检标签乙" in item.tag_names, f"标签没挂上：{item.tag_names}"

    items = ItemRepository(session)
    service.delete([item])
    session.commit()
    assert items.count(ItemFilter(only_deleted=True)) == 1, "删除后回收站应有 1 项"
    service.restore([item])
    session.commit()
    assert items.count(ItemFilter(only_deleted=True)) == 0, "还原后回收站应为空"
    assert isinstance(service.duplicate_map(), dict), "重复检测应返回字典"


@check("export_manifest", "services")
def export_manifest(case: Case) -> None:
    """导出：报告非空、清单文件落盘、导出目录里有内容。"""
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ExportService, ImportService

    session = case.session
    assert ImportService(session).import_text("导出笔记", "导出内容") is not None, "导入文本项失败"
    session.commit()
    target = case.root / "exports"
    report = ExportService(session).export_items(ItemRepository(session).query(ItemFilter()), target)
    assert report.summary(), "导出报告为空"
    assert report.manifest is not None and Path(report.manifest).exists(), "缺少清单文件"
    assert list(target.iterdir()), "导出目录为空"


@check("archive_lifecycle", "services")
def archive_lifecycle(case: Case) -> None:
    """存档：条目数、刚建即无差异、孤儿清理、历史可见、可删除。"""
    from app.services import ArchiveService, ImportService

    session = case.session
    assert ImportService(session).import_text("存档笔记", "内容") is not None, "导入文本项失败"
    session.commit()

    archives = ArchiveService(session)
    archive = archives.create(note="自检快照")
    session.commit()
    assert archive.item_count >= 1, f"存档条目数不对：{archive.item_count}"
    assert archive.name, "存档名为空"
    assert archives.compare(archive).is_empty, "刚创建的存档应无差异"
    assert isinstance(archives.orphans(), list), "孤儿列表应是列表"
    assert isinstance(archives.cleanup_orphans(), tuple), "孤儿清理应返回计数元组"
    assert any(entry.id == archive.id for entry in archives.history()), "历史里找不到刚建的存档"
    assert archives.delete(archive), "删除存档失败"
    session.commit()


@check("archive_prune_policy", "services")
def archive_prune_policy(case: Case) -> None:
    """存档清理策略：按时间 / 按容量，以及 count / size / age / none 四种模式。"""
    from app.core.config import config
    from app.services import ArchiveService, ImportService

    session = case.session
    assert ImportService(session).import_text("策略笔记", "内容") is not None, "导入文本项失败"
    session.commit()
    archives = ArchiveService(session)
    archives.create(note="基线")
    session.commit()

    old = archives.create(note="旧快照")
    session.commit()
    old.created_at = dt.datetime.now() - dt.timedelta(days=30)
    session.flush()
    removed = archives.prune_by_age(7)
    session.commit()
    assert removed == 1, f"prune_by_age(7) 应清掉 1 个，实际 {removed}"
    assert all(entry.note != "旧快照" for entry in archives.history()), "超过保留期的存档没被清掉"

    archives.create(note="新快照 A")
    archives.create(note="新快照 B")
    session.commit()
    removed = archives.prune_by_size(0)
    session.commit()
    assert removed >= 1, "prune_by_size(0) 应清掉多余存档"
    remaining = [entry.note for entry in archives.history()]
    assert remaining == ["新快照 B"], f"按容量清理后应只剩最新一个：{remaining}"

    archives.create(note="再建一个")
    session.commit()
    summaries: dict[str, str] = {}
    for mode in ("size", "age", "none", "count"):
        config.set(config.pruneMode, mode)
        archives.auto_prune()
        session.commit()
        summaries[mode] = archives.policy_summary()
    assert all(summaries.values()), f"策略摘要为空：{summaries}"


@check("user_accounts", "services")
def user_accounts(case: Case) -> None:
    """用户：密码设置与校验、保护标记、新建与删除、数据计数。"""
    from app.services import ImportService, UserService

    session = case.session
    users = UserService(session)
    owner = users.current()
    users.set_password(owner, "secret")
    session.commit()
    assert users.verify(owner, "secret"), "正确密码校验失败"
    assert not users.verify(owner, "wrong"), "错误密码通过了校验"
    infos = {info.name: info for info in users.list_users()}
    assert owner.name in infos, f"用户列表里没有默认用户：{sorted(infos)}"
    assert infos[owner.name].protected, "默认用户应带保护标记"

    guest = users.create("自检用户")
    session.commit()
    assert guest is not None, "创建用户失败"
    assert not users.is_admin(guest), "新建用户不应是管理员"
    assert users.item_count(guest) == 0 and users.data_count(guest) == 0, "新用户不应有数据"

    assert ImportService(session).import_text("用户的笔记", "内容", user_id=owner.id) is not None
    session.commit()
    assert users.data_count(owner) >= 1, "默认用户的数据计数没有增加"
    assert users.delete(guest), "空用户应可直接删除"
    users.set_password(owner, "")
    session.commit()
    assert owner.password_hash == "", "清空密码后不应留下散列"
    assert users.verify(owner, "secret"), "无密码时视为不校验（与 verify_hash 契约一致）"


@check("overview_stats", "services")
def overview_stats(case: Case) -> None:
    """统计接口：概览键齐全、类型分布与存储占用随导入变化。"""
    from app.services import ImportService, overview, storage_usage, type_breakdown

    session = case.session
    assert ImportService(session).import_text("统计笔记", "内容") is not None, "导入文本项失败"
    session.commit()
    stats = overview(session)
    for key in ("total", "total_size", "categories", "tags", "today", "duplicate_groups"):
        assert key in stats, f"概览缺少键：{key}"
    assert stats["total"] >= 1, f"总数为 {stats['total']}"
    assert stats["total_size"] > 0, "总大小应为正"
    assert stats["categories"] >= 1, "分类数应为正"
    breakdown = type_breakdown(session)
    assert breakdown, "类型分布为空"
    assert any(isinstance(row, dict) and row for row in breakdown), f"类型分布异常：{breakdown}"
    usage = storage_usage(session)
    assert isinstance(usage, dict) and usage, "存储占用为空"


@check("db_init_migration", "services")
def db_init_migration(case: Case) -> None:
    """数据库初始化与迁移：schema 版本落库、重复初始化不丢数据、未分类归位且幂等。"""
    from sqlalchemy import text

    from app.db import database
    from app.db.seed import UNCATEGORIZED_NAME
    from app.services import ImportService
    from app.services.layout_migration import migrate_uncategorized

    session = case.session
    version = session.execute(text("select value from app_meta where key='schema_version'")).scalar_one()
    assert int(version) == database.SCHEMA_VERSION, f"schema 版本不对：{version}"

    item = ImportService(session).import_text("迁移笔记", "迁移内容")
    session.commit()
    assert item is not None, "导入文本项失败"
    assert item.category_id is not None, "未指定分类时应归入「未分类」"

    database.init_db()
    session.commit()
    assert session.execute(text("select count(*) from items")).scalar_one() == 1, "重复初始化不应丢数据"

    item.category_id = None
    session.commit()
    stats = migrate_uncategorized(session)
    session.commit()
    assert stats["updated"] >= 1, f"未分类数据没有归位：{stats}"
    assert item.category is not None, "归位后仍没有分类"
    assert item.category.name == UNCATEGORIZED_NAME, f"归位分类不对：{item.category.name}"
    assert migrate_uncategorized(session)["updated"] == 0, "未分类归位应幂等"


@check("tag_permissions", "services")
def tag_permissions(case: Case) -> None:
    """标签权限：管理员可管任意标签，普通用户只能管自己创建的，越权删除返回 -1。"""
    from app.repositories import TagRepository
    from app.services import ImportService, TaxonomyService, UserService

    session = case.session
    users = UserService(session)
    admin = users.current()
    guest = users.create("标签用户")
    session.commit()
    assert guest is not None, "创建用户失败"

    taxonomy = TaxonomyService(session)
    private = taxonomy.create_tag("私有标签", user_id=guest.id)
    shared = taxonomy.create_tag("全局标签", user_id=admin.id, is_global=True)
    session.commit()
    assert private is not None and shared is not None, "创建标签失败"
    assert shared.is_global, "全局标签没有标成全局"

    tags = TagRepository(session)
    assert tags.can_manage(private, guest.id, False), "创建者应能管理自己的标签"
    assert not tags.can_manage(private, admin.id, False), "普通用户不应能管理他人标签"
    assert tags.can_manage(private, admin.id, True), "管理员应能管理他人标签"
    assert tags.can_manage(private, None, False), "无归属用户视为可管理"

    assert taxonomy.delete_tag(private, user_id=admin.id, is_admin=False) == -1, "越权删除应返回 -1"
    assert not taxonomy.rename_tag(private, "越权改名", user_id=admin.id, is_admin=False), "越权改名不该成功"
    assert taxonomy.rename_tag(private, "私有标签乙", user_id=guest.id), "创建者改名失败"
    assert taxonomy.set_tag_global(private, True, user_id=guest.id, is_admin=False), "创建者应能切换全局"
    session.commit()
    assert tags.by_name("私有标签乙", user_id=guest.id) is not None, "改名没有落库"

    assert (
        ImportService(session).import_text("标签笔记", "内容", user_id=guest.id, tags=["使用中的标签"])
        is not None
    )
    session.commit()
    assert taxonomy.usage().get("使用中的标签", 0) >= 1, "标签使用计数不对"
    taxonomy.cleanup_unused(user_id=guest.id, is_admin=False)
    session.commit()
    assert tags.by_name("使用中的标签", guest.id) is not None, "使用中的标签不应被清理"


@check("user_delete_transfer", "services")
def user_delete_transfer(case: Case) -> None:
    """删除用户：默认用户不可删；普通用户的数据、分类与标签全部转交默认用户。"""
    from pathlib import Path

    from app.repositories import CategoryRepository, ItemFilter, ItemRepository, TagRepository
    from app.services import ImportService, LibraryService, TaxonomyService, UserService

    session = case.session
    users = UserService(session)
    owner = users.current()
    guest = users.create("待删用户")
    session.commit()
    assert guest is not None, "创建用户失败"

    taxonomy = TaxonomyService(session)
    folder = taxonomy.create_category("访客资料", user_id=guest.id)
    assert folder is not None, "创建分类失败"
    assert taxonomy.create_tag("访客标签", user_id=guest.id) is not None, "创建标签失败"

    importer = ImportService(session)
    note = importer.import_text(
        "访客笔记", "访客内容", user_id=guest.id, category_id=folder.id, tags=["访客标签"]
    )
    assert note is not None, "导入文本失败"
    if SAMPLE_IMAGE.exists():
        assert importer.import_files([SAMPLE_IMAGE], user_id=guest.id, category_id=folder.id).added, "导入图片失败"
    session.commit()
    assert users.data_count(guest) >= 1, "新用户的数据计数不对"

    assert not users.delete(owner), "默认用户不应被删除"
    assert users.delete(guest), "普通用户应可删除"
    session.commit()

    assert all(info.name != "待删用户" for info in users.list_users()), "用户没有被删掉"
    items = ItemRepository(session).query(ItemFilter(user_ids={owner.id}))
    assert "访客笔记" in {item.name for item in items}, f"数据没有转移：{sorted(item.name for item in items)}"
    library = LibraryService(session)
    for item in items:
        path = library.abs_path(item)
        assert path is not None and Path(path).exists(), f"转移后库内文件丢失：{item.name} -> {path}"
    categories = CategoryRepository(session)
    paths = {categories.path_of(item.category) for item in items if item.category is not None}
    assert any(path.split(" / ")[0] == "待删用户" for path in paths), f"分类没有镜像到目标用户下：{sorted(paths)}"
    assert TagRepository(session).by_name("访客标签", owner.id) is not None, "标签没有转交"


@check("privacy_state", "services")
def privacy_state(case: Case) -> None:
    """隐私保护：开关只改状态、隐藏数据进出 `.hiddens`、隐藏目录枚举可失效。"""
    from pathlib import Path

    from app.core import paths
    from app.core.config import config, resources_root
    from app.services import ImportService, ItemService, LibraryService
    from app.services.privacy_service import privacy

    session = case.session
    assert not privacy.enabled(), "默认不应开启保护"
    assert privacy.state_text(), "状态文本不应为空"
    # 开关全关时启动不能碰 ACL：白放行一次要给整棵 .resources 传播继承（十几万个文件），
    # 表现就是「启动 3/8」卡住几分钟起不来
    from app.core import acl

    calls: list[str] = []
    real_unlock = acl.unlock
    acl.unlock = lambda path: (calls.append(str(path)), (True, ""))[1]
    try:
        count, message = privacy.begin_session()
    finally:
        acl.unlock = real_unlock
    assert (count, message) == (0, "没有开启保护"), f"没开保护时启动不应放行：{(count, message)}"
    assert not calls, f"没开保护却调用了 icacls：{calls}"
    config.set(config.resourceProtected, True)
    assert privacy.enabled(), "资源保护开关没有生效"
    assert privacy.targets() == [resources_root()], f"资源保护应对准资源根：{privacy.targets()}"
    # models/（运行环境 venv + 模型权重）不参与保护：浅层锁直接跳过它，一个 ACE 都不碰；
    # 资源根只挂不带继承标志的拒绝项，否则 Windows 会把 ACE 传播进整棵子树（十几万个文件）
    real_lock, real_children = acl.lock, acl._children
    locks: list[tuple[str, bool]] = []
    acl.lock = lambda path, *, deep=True: (locks.append((str(path), deep)), (True, ""))[1]
    try:
        models = resources_root() / "models"
        models.mkdir(parents=True, exist_ok=True)
        library = resources_root() / "library"
        library.mkdir(parents=True, exist_ok=True)
        acl._children = lambda root: [models, library]
        privacy.lock()
        # 根必须最后锁：拒绝项连「遍历」一起挡，先锁根就列不出子项（子目录会全漏掉）
        assert locks and locks[-1] == (str(resources_root()), False), f"资源根必须最后浅锁：{locks}"
        assert all(name != str(models) for name, _deep in locks), f"models/ 不该被锁：{locks}"
        assert (str(library), True) in locks, f"资源根的子目录应深锁：{locks}"
        # 放行侧要用同一份跳过名单：对从没锁过的 models/ 跑 icacls 会重写它的 DACL，
        # Windows 顺势把可继承的 ACE 传播进十几万个对象，启动就这样卡住好几分钟
        real_unlock, real_release = acl.unlock, acl.remove_deny
        released: list[str] = []
        acl.unlock = lambda path: (True, "")
        acl.remove_deny = lambda path: (released.append(str(path)), (True, ""))[1]
        try:
            privacy.unlock()
        finally:
            acl.unlock, acl.remove_deny = real_unlock, real_release
        assert str(library) in released, f"放行时应摘掉子项的拒绝项：{released}"
        assert str(models) not in released, f"放行时不该对 models/ 跑 icacls：{released}"
    finally:
        acl.lock, acl._children = real_lock, real_children
    config.set(config.resourceProtected, False)
    config.set(config.hiddenProtected, True)
    assert privacy.enabled(), "隐藏目录保护开关没有生效"
    config.set(config.hiddenProtected, False)

    item = ImportService(session).import_text("隐藏笔记", "隐藏内容")
    session.commit()
    assert item is not None, "导入文本失败"
    library = LibraryService(session)
    before = library.abs_path(item)
    service = ItemService(session)
    assert service.set_hidden([item], True) == 1, "隐藏操作应影响 1 项"
    session.commit()
    assert item.is_hidden, "数据没有被标记为隐藏"
    hidden_path = library.abs_path(item)
    assert hidden_path is not None and Path(hidden_path).exists(), f"隐藏后文件丢失：{hidden_path}"
    assert paths.HIDDEN_DIR_NAME in Path(hidden_path).parts, f"隐藏文件不在 .hiddens 里：{hidden_path}"
    assert Path(hidden_path) != Path(before), "隐藏后文件路径应变化"
    privacy.invalidate()
    assert any(path.name == paths.HIDDEN_DIR_NAME for path in privacy.hidden_dirs()), "隐藏目录没被枚举到"

    assert service.set_hidden([item], False) == 1, "取消隐藏应影响 1 项"
    session.commit()
    assert not item.is_hidden, "隐藏标记没有清掉"
    shown = library.abs_path(item)
    assert shown is not None and Path(shown).exists(), f"取消隐藏后文件丢失：{shown}"
    assert paths.HIDDEN_DIR_NAME not in Path(shown).parts, f"文件仍在 .hiddens 里：{shown}"
    privacy.invalidate()
    assert not privacy.hidden_dirs(), f"空 .hiddens 目录应被清掉：{privacy.hidden_dirs()}"


@check("import_duplicate_policy", "services")
def import_duplicate_policy(case: Case) -> None:
    """重复内容的三种导入策略：默认重命名另存、skip 跳过、overwrite 复用已有项。"""
    from app.core.config import config
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ImportService

    session = case.session
    source = case.root / "重复样例.txt"
    source.write_text("重复内容", encoding="utf-8")
    importer = ImportService(session)
    items = ItemRepository(session)
    assert config.duplicatePolicy.value == "rename", f"默认策略应为 rename：{config.duplicatePolicy.value}"

    first = importer.import_files([source])
    session.commit()
    assert first.added and not first.failed, f"首次导入失败：{first.summary()}"

    second = importer.import_files([source])
    session.commit()
    assert second.added and second.added[0].id != first.added[0].id, "rename 策略应另存一份"
    assert items.count(ItemFilter()) == 2, "重命名导入后应有 2 项"

    config.set(config.duplicatePolicy, "skip")
    third = importer.import_files([source])
    session.commit()
    assert not third.added and third.skipped, f"skip 策略应跳过：{third.summary()}"
    assert items.count(ItemFilter()) == 2, "跳过后数量不应变化"

    config.set(config.duplicatePolicy, "overwrite")
    fourth = importer.import_files([source])
    session.commit()
    assert len(fourth.added) == 1, f"overwrite 策略应复用已有项：{fourth.summary()}"
    assert fourth.added[0].id in {first.added[0].id, second.added[0].id}, "覆盖导入应落在已有项上"
    assert items.count(ItemFilter()) == 2, "覆盖导入不应新增数据项"


@check("export_manifest_fields", "services")
def export_manifest_fields(case: Case) -> None:
    """导出清单：字段齐全、JSON 可解析、内容仓库缺文件时计入 missing。"""
    import json
    from pathlib import Path

    from sqlalchemy import text

    from app.core.config import store_dir
    from app.repositories import ItemFilter, ItemRepository
    from app.services import BlobStore, ExportService, ImportService
    from app.services.export_service import MANIFEST_FIELDS

    session = case.session
    importer = ImportService(session)
    assert importer.import_text("清单笔记", "清单内容", keywords=["清单"], tags=["重要"]) is not None, "导入文本失败"
    assert SAMPLE_IMAGE.exists(), f"缺少示例图片：{SAMPLE_IMAGE}"
    assert importer.import_files([SAMPLE_IMAGE]).added, "导入图片失败"
    session.commit()

    items = ItemRepository(session).query(ItemFilter())
    report = ExportService(session).export_items(items, case.root / "export-json", manifest_format="json")
    session.commit()
    assert report.exported >= 2, f"导出数量不对：{report.summary()}"
    manifest = Path(report.manifest)
    assert manifest.exists() and manifest.suffix == ".json", f"清单文件不对：{manifest}"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(payload, list) and payload, "JSON 清单应是列表"
    for entry in payload:
        missing = [name for name in MANIFEST_FIELDS if name not in entry]
        assert not missing, f"清单缺列：{missing}"
    assert "清单笔记" in {entry.get("名称") for entry in payload}, "清单里没有文本项"

    store = BlobStore(store_dir())
    rel_paths = session.execute(text("select rel_path from blobs")).scalars().all()
    removed = [rel for rel in rel_paths if store.remove(rel)]
    assert removed, "内容仓库里没有可删除的文件"
    later = ExportService(session).export_items(items, case.root / "export-missing")
    assert later.missing >= 1, f"内容缺失应计入 missing：{later.summary()}"


@check("archive_diff_and_restore", "services")
def archive_diff_and_restore(case: Case) -> None:
    """存档差异与整档还原：改内容记 changed、删数据记 removed、还原补回文件与分类。"""
    from pathlib import Path

    from app.repositories import CategoryRepository, ItemFilter, ItemRepository
    from app.services import ArchiveService, ImportService, ItemService, LibraryService, TaxonomyService, UserService

    session = case.session
    root = CategoryRepository(session).by_name("学习资料")
    assert root is not None, "默认分类「学习资料」缺失"
    owner = UserService(session).current()
    # 分类归属当前用户：还原时按「分类 / 路径」重建会同名复用，不会撞上无归属的同名分类。
    folder = TaxonomyService(session).create_category("存档分类", parent_id=root.id, user_id=owner.id)
    assert folder is not None, "创建分类失败"
    item = ImportService(session).import_text("存档笔记", "原始内容", category_id=folder.id, tags=["重要"])
    session.commit()
    assert item is not None, "导入文本失败"

    archives = ArchiveService(session)
    archive = archives.create(note="差异快照")
    session.commit()
    assert archives.compare(archive).is_empty, "刚创建的存档应无差异"
    entries = archives.entries(archive)
    assert len(entries) == 1, f"存档条目数不对：{len(entries)}"
    entry = entries[0]
    assert archives.entry_state(entry) == "same", f"初始状态应为 same：{archives.entry_state(entry)}"

    ItemService(session).update(item, content="改过的内容")
    session.commit()
    assert "存档笔记" in archives.compare(archive).changed, "内容变化应记 changed"
    assert archives.entry_state(entry) == "changed", f"改内容后应为 changed：{archives.entry_state(entry)}"

    ItemService(session).purge([item])
    session.commit()
    assert archives.entry_state(entry) == "removed", f"删除后应为 removed：{archives.entry_state(entry)}"
    assert "存档笔记" in archives.compare(archive).removed, "删除数据应记 removed"

    result = archives.restore_all(archive)
    session.commit()
    assert result == {"restored": 1, "skipped": 0}, f"整档还原结果不对：{result}"
    restored = [row for row in ItemRepository(session).query(ItemFilter()) if row.name == "存档笔记"]
    assert len(restored) == 1, "还原后应有一条同名数据项"
    back = restored[0]
    path = LibraryService(session).abs_path(back)
    assert path is not None and Path(path).exists(), f"还原后文件没写回库：{path}"
    categories = CategoryRepository(session)
    assert back.category is not None and categories.path_of(back.category).endswith("存档分类"), "还原后分类不对"
    assert "重要" in back.tag_names, f"还原后标签丢失：{back.tag_names}"
    assert archives.restore_all(archive) == {"restored": 0, "skipped": 1}, "重复还原应跳过"


@check("archive_whole_store", "services")
def archive_whole_store(case: Case) -> None:
    """整份存储：一份内容落一个文件、同内容不重复占空间、导出与回档字节一致。"""
    import hashlib
    from pathlib import Path

    from sqlalchemy import select

    from app.db.models import Blob, Content
    from app.repositories import ItemRepository
    from app.services import ArchiveService, ExportService, ImportService
    from app.services.content_store import ContentStore, preferred_codec

    mb = 1 << 20
    session = case.session
    store = ContentStore(session)
    service = ArchiveService(session, store=store)

    source = case.root / "whole.txt"
    payload = ("整份存储的自检正文，重复出现以便观察压缩效果。\n" * 40000).encode("utf-8")
    assert len(payload) > mb, "样本应大于 1 MiB"
    source.write_bytes(payload)
    result = ImportService(session, store=store).import_files([source])
    session.commit()
    assert result.added_count == 1 and not result.failed, f"导入大文件失败：{result.summary()}"
    item = result.added[0]
    assert item.checksum == hashlib.sha256(payload).hexdigest(), "校验和应为原始字节的 sha256"

    usage = store.usage()
    row = session.scalar(select(Content).where(Content.checksum == item.checksum))
    assert row is not None and row.size == len(payload), "内容行应记录原始大小"
    assert usage["contents"] == 1 and usage["files"] == 1, f"一份内容只该落一个文件：{usage}"
    assert usage["stored_bytes"] == usage["total_bytes"] < len(payload), f"文本内容应压缩后落盘：{usage}"
    assert row.codec == preferred_codec(), f"可压缩内容应用首选编码：{row.codec}"
    blob = session.scalar(select(Blob).where(Blob.checksum == item.checksum))
    assert blob is not None and blob.rel_path == store.rel_path_for(item.checksum), "Blob 应指向内容文件"
    assert store.loose_rel_path(item.checksum) == store.rel_path_for(item.checksum), "整份内容就在哈希目录里"

    assert b"".join(store.iter_content(item.checksum)) == payload, "流式读回的字节与原文件不一致"
    target = case.root / "whole-export"
    exported = ExportService(session, store=store).export_items([item], target)
    assert exported.exported == 1, f"导出失败：{exported.summary()}"
    assert (target / source.name).read_bytes() == payload, "导出的字节与原文件不一致"

    archive = service.create("整份档", "")
    session.commit()
    assert archive.total_size == 0, f"内容已入库时新增占用应为 0：{archive.total_size}"
    assert archive.logical_size == len(payload), f"逻辑大小不对：{archive.logical_size}"
    assert all(entry.checksum for entry in service.entries(archive)), "存档条目应记住内容校验和"

    before = store.usage()
    duplicate = case.root / "whole-copy.txt"
    duplicate.write_bytes(payload)
    store.put_file(duplicate, name=duplicate.name, mime="text/plain")
    session.commit()
    assert store.usage() == before, "同一份内容重复入库不该多占空间"

    entry = service.archives.entry_by_item(archive, item.id)
    assert entry is not None, "存档里找不到刚导入的大文件条目"
    ItemRepository(session).soft_delete([item])
    session.commit()
    assert service.entry_state(entry) == "removed", "删除后条目状态应为 removed"
    restored = service.restore_entry(entry)
    session.commit()
    assert restored is not None, "回档应重建数据项"
    path = Path(restored.library.path) / restored.file_path
    assert path.read_bytes() == payload, "回档文件字节与原内容不一致"


@check("archive_permanent_delete", "services")
def archive_permanent_delete(case: Case) -> None:
    """彻底删除后仍能从存档还原：同名不同内容的其它数据项不算「与当前数据一致」。"""
    from app.services import ArchiveService, ImportService, ItemService
    from app.services.content_store import ContentStore

    session = case.session
    store = ContentStore(session)
    service = ArchiveService(session, store=store)
    importer = ImportService(session, store=store)

    folder = case.root / "permanent"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "note.txt"
    source.write_text("存档前的正文。\n" * 200, encoding="utf-8")
    filler = folder / "filler.txt"
    filler.write_text("占位内容，避免数据项 id 被复用。\n" * 50, encoding="utf-8")
    result = importer.import_files([source, filler])
    session.commit()
    assert result.added_count == 2, f"导入失败：{result.summary()}"
    item = next(entry for entry in result.added if entry.name == "note.txt")
    archive = service.create("彻底删除档", "")
    session.commit()
    entry = service.archives.entry_by_item(archive, item.id)
    assert entry is not None, "存档里找不到条目"
    ItemService(session, store=store).purge([item])
    session.commit()
    assert store.content_available(entry.checksum), "彻底删除不该连存档内容一起删掉"

    source.write_text("同名但内容完全不同的新正文。\n" * 200, encoding="utf-8")
    importer.import_files([source])
    session.commit()
    assert service.entry_state(entry) == "removed", "同名不同内容的项不该被当成同一条数据"
    report = service.preview_restore(archive, "restore", None)
    assert report.changes and not report.missing, f"应当可以回档：{report.summary()}"
    restored = service.restore_entry(entry)
    session.commit()
    assert restored is not None, "彻底删除后应能从存档还原"
    assert restored.checksum == entry.checksum, "还原的应是存档里的内容"


@check("archive_codec_choice", "services")
def archive_codec_choice(case: Case) -> None:
    """整份压缩：文本落首选编码、随机数据回退原样、已压缩格式不尝试压缩。"""
    import os

    from sqlalchemy import select

    from app.db.models import Content
    from app.services import ImportService
    from app.services.content_store import (
        CODEC_RAW,
        ContentStore,
        is_compressible,
        preferred_codec,
    )

    mb = 1 << 20
    session = case.session
    store = ContentStore(session)
    importer = ImportService(session, store=store)

    assert is_compressible("notes.txt"), "文本应视为可压缩"
    assert not is_compressible("clip.mp4"), "视频扩展名不该尝试压缩"

    text = ("可压缩的正文，重复重复重复。\n" * 60000).encode("utf-8")
    text_path = case.root / "notes.txt"
    text_path.write_bytes(text)
    result = importer.import_files([text_path])
    session.commit()
    assert result.added_count == 1, f"导入文本失败：{result.summary()}"
    row = session.scalar(select(Content).where(Content.checksum == result.added[0].checksum))
    assert row is not None and row.codec == preferred_codec(), f"文本应用首选编码：{row.codec}"
    assert row.stored_size < row.size, "压缩后应明显更小"
    assert store.usage()["total_bytes"] < len(text), f"整份压缩应小于原文件：{store.usage()}"
    assert store.read_content(row.checksum) == text, "读回内容与原文件不一致"

    payload = os.urandom(2 * mb)
    random_path = case.root / "random.bin"
    random_path.write_bytes(payload)
    before = store.total_size()
    result = importer.import_files([random_path])
    session.commit()
    row = session.scalar(select(Content).where(Content.checksum == result.added[0].checksum))
    assert row is not None and row.codec == CODEC_RAW, f"压不动的数据应原样存：{row.codec}"
    assert row.stored_size == row.size == len(payload), "原样存的落盘大小应等于原始大小"
    assert store.total_size() - before == len(payload), f"占用增长应恰为内容大小：{store.total_size() - before}"

    clip_path = case.root / "redundant.mp4"
    clip_bytes = text + b"\n# a whitelisted extension still gets stored as-is\n"
    clip_path.write_bytes(clip_bytes)
    before = store.total_size()
    result = importer.import_files([clip_path])
    session.commit()
    row = session.scalar(select(Content).where(Content.checksum == result.added[0].checksum))
    assert row is not None and row.codec == CODEC_RAW, "已压缩格式即使内容冗余也该原样存"
    assert store.total_size() - before == len(clip_bytes), (
        f"白名单内容占用应恰为原大小：{store.total_size() - before}"
    )


@check("archive_compression_policy", "services")
def archive_compression_policy(case: Case) -> None:
    """压缩方案：各编码都能往返、文本用更高级别、压不划算一律退回原样。"""
    import os

    from app.services.content_store import (
        CODEC_DEFLATE,
        CODEC_LZMA,
        CODEC_RAW,
        CODEC_ZSTD,
        compress_bytes,
        decode_stored,
        encode_stored,
        is_textual,
        policy_for,
        preferred_codec,
        zstd_available,
    )

    sample = ("方案自检正文，重复重复重复重复。\n" * 2000).encode("utf-8")
    assert is_textual("notes.txt", "text/plain"), "文本应被认作文本"
    assert not is_textual("photo.jpg", "image/jpeg"), "图片不该算文本"
    assert policy_for("clip.mp4", "video/mp4") == CODEC_RAW, "已压缩扩展名应原样存"
    assert policy_for("notes.txt", "text/plain") == preferred_codec(), "文本应用首选编码"

    codecs = [CODEC_DEFLATE, CODEC_LZMA]
    if zstd_available():
        codecs.append(CODEC_ZSTD)
    for codec in codecs:
        packed = compress_bytes(sample, codec=codec, textual=True)
        assert len(packed) < len(sample), f"{codec} 应能压小文本"
        assert decode_stored(codec, packed) == sample, f"{codec} 往返不一致"
    assert decode_stored(CODEC_RAW, sample) == sample, "原样编码应直接读回"

    stored, codec = encode_stored(sample, name="notes.txt", mime="text/plain")
    assert codec == preferred_codec() and len(stored) < len(sample), "文本应压得更小"
    assert decode_stored(codec, stored) == sample, "编码往返不一致"

    noise = os.urandom(64 * 1024)
    stored, codec = encode_stored(noise, name="random.bin", mime="application/octet-stream")
    assert codec == CODEC_RAW and stored == noise, "压不动的数据应原样返回"

    stored, codec = encode_stored(sample, name="clip.mp4", mime="video/mp4")
    assert codec == CODEC_RAW and stored == sample, "已压缩格式不该被压缩"


@check("library_single", "services")
def library_single(case: Case) -> None:
    """库文件夹：只保留一个默认库、额外库被吸收、布局目录齐全、目录名被清洗。"""
    from pathlib import Path

    from app.core import paths
    from app.core.config import library_root
    from app.repositories import LibraryRepository
    from app.services import LibraryService, sanitize_dir_name

    session = case.session
    service = LibraryService(session)
    default = service.ensure_default()
    session.commit()
    assert default is not None and default.is_default, "默认库不存在"
    assert str(default.path) == str(library_root()), f"默认库路径不对：{default.path}"

    libraries = LibraryRepository(session)
    assert libraries.create("临时库", str(case.root / "extra-library")) is not None, "创建库失败"
    session.commit()
    assert len(libraries.all()) == 2, "额外库应先被登记"

    service.ensure_default()
    session.commit()
    remaining = libraries.all()
    assert len(remaining) == 1, f"额外库应被吸收：{[lib.name for lib in remaining]}"
    assert remaining[0].is_default, "剩下的应是默认库"

    service.ensure_layout(remaining[0])
    global_dir = Path(remaining[0].path) / paths.GLOBAL_DIR_NAME
    for name in (paths.LIBRARY_STORE_DIRNAME, paths.LIBRARY_COVER_DIRNAME, paths.LIBRARY_BACKUP_DIRNAME):
        assert (global_dir / name).is_dir(), f"缺少布局目录：{global_dir / name}"

    cleaned = sanitize_dir_name('非法/名字:*?"<>|')
    assert cleaned and not any(ch in cleaned for ch in '/\\:*?"<>|'), f"目录名没清洗干净：{cleaned!r}"


@check("category_shared_conflicts", "services")
def category_shared_conflicts(case: Case) -> None:
    """还原重建分类不再撞同级唯一约束：共享同名分类复用，他人占用则为本用户改名。"""
    from app.repositories import CategoryRepository, ItemFilter, ItemRepository
    from app.services import ArchiveService, ImportService, ItemService, TaxonomyService, UserService

    session = case.session
    categories = CategoryRepository(session)
    taxonomy = TaxonomyService(session)
    users = UserService(session)
    owner = users.current()
    assert owner is not None, "缺少默认用户"
    root = categories.by_name("学习资料")
    assert root is not None, "默认分类「学习资料」缺失"
    archives = ArchiveService(session)

    shared = taxonomy.create_category("共享分类", parent_id=root.id, user_id=None)
    assert shared is not None, "创建共享分类失败"
    item = ImportService(session).import_text("共享笔记", "共享内容", category_id=shared.id)
    session.commit()
    assert item is not None, "导入文本失败"
    archive = archives.create(note="共享分类快照")
    ItemService(session).purge([item])
    session.commit()
    first = archives.restore_all(archive)
    session.commit()
    assert first["restored"] == 1, f"共享同名分类下还原失败：{first}"
    restored = [row for row in ItemRepository(session).query(ItemFilter()) if row.name == "共享笔记"]
    assert len(restored) == 1, "还原后应有一条数据项"
    assert restored[0].category_id == shared.id, "应复用共享的同名分类"

    other = users.create("自检他人", "pw")
    assert other is not None, "创建用户失败"
    mine = taxonomy.create_category("独占分类", parent_id=root.id, user_id=owner.id)
    assert mine is not None, "创建本用户分类失败"
    second = ImportService(session).import_text("独占笔记", "独占内容", category_id=mine.id)
    session.commit()
    assert second is not None, "导入文本失败"
    archive2 = archives.create(note="独占分类快照")
    ItemService(session).purge([second])
    taxonomy.delete_category(mine)
    session.commit()
    theirs = taxonomy.create_category("独占分类", parent_id=root.id, user_id=other.id)
    assert theirs is not None, "他人占用同名分类失败"
    session.commit()
    second_result = archives.restore_all(archive2)
    session.commit()
    assert second_result["restored"] == 1, f"同名冲突下还原失败：{second_result}"
    back = [row for row in ItemRepository(session).query(ItemFilter()) if row.name == "独占笔记"]
    assert len(back) == 1, "还原后应有一条数据项"
    path = categories.path_of(back[0].category) if back[0].category else ""
    assert path.endswith("独占分类-1"), f"应改用空闲名字：{path}"
    assert back[0].category_id != theirs.id, "不应把数据放进他人的分类"


@check("import_tree_skips_meta", "services")
def import_tree_skips_meta(case: Case) -> None:
    """按目录树导入时跳过库元数据目录 `.datamanager`，只收用户文件并保留层级。"""
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ImportService

    session = case.session
    forest = case.root / "森林"
    (forest / "子目录").mkdir(parents=True)
    (forest / ".datamanager").mkdir()
    (forest / "a.txt").write_text("甲", encoding="utf-8")
    (forest / "子目录" / "b.txt").write_text("乙", encoding="utf-8")
    (forest / ".datamanager" / "meta.json").write_text("{}", encoding="utf-8")

    result = ImportService(session).import_tree(forest)
    session.commit()
    assert not result.failed, f"导入目录树失败：{result.failed}"
    names = sorted(row.name for row in ItemRepository(session).query(ItemFilter()))
    assert "meta.json" not in names, f"元数据文件被导入：{names}"
    assert {"a.txt", "b.txt"} <= set(names), f"用户文件未全部导入：{names}"


def _restore_fixture(case: Case, *, hidden: bool = False):
    """回档检查共用的场景：一个分类 + 一个文本项 + 一份基线存档。"""
    from app.repositories import CategoryRepository
    from app.services import (
        ArchiveService,
        ImportService,
        LibraryService,
        TaxonomyService,
        UserService,
    )

    session = case.session
    root = CategoryRepository(session).by_name("学习资料")
    assert root is not None, "默认分类「学习资料」缺失"
    owner = UserService(session).current()
    folder = TaxonomyService(session).create_category("回档分类", parent_id=root.id, user_id=owner.id)
    assert folder is not None, "创建分类失败"
    item = ImportService(session).import_text(
        "回档笔记", "存档里的内容", category_id=folder.id, tags=["重要"]
    )
    assert item is not None, "导入文本失败"
    if hidden:
        LibraryService(session).set_item_hidden(item, True)
    session.commit()
    service = ArchiveService(session)
    archive = service.create(note="回档基线")
    session.commit()
    assert len(service.entries(archive)) == 1, "基线存档条目数不对"
    return session, service, owner, folder, item, archive


@check("archive_restore_modes", "services")
def archive_restore_modes(case: Case) -> None:
    """恢复式保留现有项再复原一份；覆盖式原地替换，并把多出来的数据收进回收站。"""
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ImportService, ItemService

    session, service, owner, folder, item, archive = _restore_fixture(case)
    entry = service.entries(archive)[0]

    ItemService(session).update(item, content="被改成的新内容")
    extra = ImportService(session).import_text("存档外的数据", "不在存档里", category_id=folder.id)
    session.commit()
    assert extra is not None, "导入存档外数据失败"
    assert service.entry_state(entry) == "changed", f"改内容后应为 changed：{service.entry_state(entry)}"

    preview = service.preview_restore(archive)
    assert not preview.is_empty, "有变更时预览不应为空"
    assert (preview.restored, preview.replaced, preview.soft_deleted) == (1, 0, 0), (
        f"恢复式预览数字不对：{preview.summary()}"
    )
    assert [change.kind for change in preview.changes] == ["新增"], f"变更类型不对：{preview.grouped()}"
    assert item.content == "被改成的新内容", "预览不应写数据"

    report = service.restore(archive, "restore")
    session.commit()
    assert (report.restored, report.replaced, report.soft_deleted) == (1, 0, 0), (
        f"恢复式结果不对：{report.summary()}"
    )
    notes = [row for row in ItemRepository(session).query(ItemFilter()) if row.name == "回档笔记"]
    assert len(notes) == 2, f"恢复式应保留原项并新增复原项：{len(notes)}"
    assert sorted(row.content for row in notes) == ["存档里的内容", "被改成的新内容"], "复原的内容不对"
    assert any(
        row.name == "存档外的数据" for row in ItemRepository(session).query(ItemFilter())
    ), "恢复式不应动存档外的数据"

    report = service.restore(archive, "mirror")
    session.commit()
    assert (report.replaced, report.soft_deleted) == (1, 2), f"覆盖式结果不对：{report.summary()}"
    session.refresh(item)
    assert item.content == "存档里的内容" and not item.is_deleted, f"覆盖式应原地替换：{item.content!r}"
    assert (
        len([row for row in ItemRepository(session).query(ItemFilter()) if row.name == "回档笔记"]) == 1
    ), "覆盖式不应留下第二条同名数据"
    trashed = ItemRepository(session).query(ItemFilter(include_deleted=True))
    assert all(row.is_deleted for row in trashed if row.name != "回档笔记"), "多出来的数据应进回收站"


@check("archive_mirror_claims", "services")
def archive_mirror_claims(case: Case) -> None:
    """覆盖式回档（镜像）的认领口径：库里已有内容相同的一条就复用，既不重复复制也不再把它丢进回收站。"""
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ImportService, ItemService

    session, service, owner, folder, item, archive = _restore_fixture(case)
    entry = service.entries(archive)[0]

    ItemService(session).purge([item])
    session.commit()
    assert service.entry_state(entry) == "removed", "彻底删除后条目应为 removed"

    first = service.restore(archive, "mirror")
    session.commit()
    assert (first.restored, first.soft_deleted) == (1, 0), (
        f"彻底删除后覆盖式应复原且不收走别的东西：{first.summary()}"
    )

    again = service.preview_restore(archive, "mirror")
    assert again.is_empty and again.total == 0, f"复原后覆盖式应无变更：{again.summary()}"
    assert not again.changes, "没有变更时不该有变更清单"

    report = service.restore(archive, "mirror")
    session.commit()
    assert report.is_empty and report.soft_deleted == 0, f"覆盖式不该反复回档：{report.summary()}"
    rows = ItemRepository(session).query(ItemFilter(include_deleted=True))
    assert len(rows) == 1 and not rows[0].is_deleted, (
        f"不该复制出重复项或把已复原的数据丢进回收站：{[row.name for row in rows]}"
    )

    extra = ImportService(session).import_text("存档外的数据", "不在存档里", category_id=folder.id)
    session.commit()
    assert extra is not None, "导入存档外数据失败"
    preview = service.preview_restore(archive, "mirror")
    assert [change.kind for change in preview.changes] == ["进回收站"], f"变更类型不对：{preview.grouped()}"
    assert preview.total == len(preview.changes) == 1, "变更计数应与清单行数一致"
    change = preview.changes[0]
    assert change.name == "存档外的数据" and change.category and change.user, (
        f"清单该写清动的是谁的数据：{change}"
    )


@check("archive_hidden_state", "services")
def archive_hidden_state(case: Case) -> None:
    """隐藏态特例：先对齐隐藏位与回收站，再考虑新建（修缺陷 ①：归档里也有、回收站里也有的数据被复制第二份）。"""
    from pathlib import Path

    from app.core import paths
    from app.repositories import ItemFilter, ItemRepository
    from app.services import LibraryService

    session, service, owner, folder, item, archive = _restore_fixture(case, hidden=True)
    entry = service.entries(archive)[0]
    assert entry.is_hidden, "存档条目应记下隐藏位"
    assert service.entry_state(entry) == "same", f"刚建完存档应为 same：{service.entry_state(entry)}"

    libraries = LibraryService(session)
    libraries.set_item_hidden(item, False)
    session.commit()
    assert service.entry_state(entry) == "changed", "只改隐藏位也应算 changed"
    preview = service.preview_restore(archive)
    assert [change.kind for change in preview.changes] == ["仅改隐藏位"], f"变更类型不对：{preview.grouped()}"

    report = service.restore(archive, "restore")
    session.commit()
    assert (report.hidden_fixed, report.restored) == (1, 0), f"隐藏位应原地对齐：{report.summary()}"
    assert item.is_hidden, "隐藏位没对齐"
    assert Path(libraries.abs_path(item)).parent.name == paths.HIDDEN_DIR_NAME, "文件应搬回隐藏目录"
    assert (
        len(ItemRepository(session).query(ItemFilter(include_hidden=True))) == 1
    ), "对齐隐藏位不应新建数据项"

    ItemRepository(session).soft_delete([item])
    session.commit()
    assert service.entry_state(entry) == "removed", "进回收站后应为 removed"
    preview = service.preview_restore(archive)
    assert [change.kind for change in preview.changes] == ["撤销删除"], f"变更类型不对：{preview.grouped()}"

    report = service.restore(archive, "restore")
    session.commit()
    assert (report.undeleted, report.restored) == (1, 0), f"应撤销删除而不是新建：{report.summary()}"
    session.refresh(item)
    assert not item.is_deleted, "撤销删除没生效"
    assert (
        len(ItemRepository(session).query(ItemFilter(include_hidden=True))) == 1
    ), "撤销删除不应新建第二条数据（缺陷 ①）"


@check("archive_selection", "services")
def archive_selection(case: Case) -> None:
    """按条目回档：跨存档只取最新版本并标注冲突；覆盖式不提供条目作用域。"""
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ItemService

    session, service, owner, folder, item, archive = _restore_fixture(case)
    first = service.entries(archive)[0]
    ItemService(session).update(item, content="第二版内容")
    session.commit()
    second_archive = service.create(note="第二版")
    session.commit()
    second = service.entries(second_archive)[0]
    ItemService(session).update(item, content="现在的内容")
    session.commit()

    scope = [first, second]
    preview = service.preview_restore(scope, "restore")
    assert preview.conflicts == 1, f"跨存档重复条目应记一次冲突：{preview.conflicts}"
    assert any(change.kind.startswith("冲突") for change in preview.changes), (
        f"预览应标注冲突：{preview.grouped()}"
    )
    assert preview.source == "2 个存档 / 2 个条目", f"来源描述不对：{preview.source}"

    report = service.restore(scope, "restore")
    session.commit()
    assert report.restored == 1, f"按条目回档应复原最新的一份：{report.summary()}"
    contents = sorted(
        row.content for row in ItemRepository(session).query(ItemFilter()) if row.name == "回档笔记"
    )
    assert "第二版内容" in contents and "现在的内容" in contents, f"应按最新存档复原：{contents}"

    try:
        service.preview_restore(scope, "mirror")
    except ValueError:
        pass
    else:
        raise AssertionError("覆盖式回档不应接受条目作用域")


@check("archive_preview", "services")
def archive_preview(case: Case) -> None:
    """预览只算不写；没有变更时不产生变更清单；可以「先存档再回档」；快照失败就不回档。"""
    from sqlalchemy import func, select

    from app.core.config import config
    from app.db.models import Archive
    from app.services import ItemService

    session, service, owner, folder, item, archive = _restore_fixture(case)
    assert config.restorePreview.value is True, "Archive/Restore-Preview 默认应开启"
    empty = service.preview_restore(archive)
    assert empty.is_empty and empty.total == 0, f"没有变更时预览应为空：{empty.summary()}"
    assert not empty.changes and empty.restored == 0, "没有变更时不该有变更行"

    def archive_count() -> int:
        return int(session.scalar(select(func.count()).select_from(Archive)) or 0)

    before = archive_count()
    ItemService(session).update(item, content="改过的内容")
    session.commit()
    preview = service.preview_restore(archive)
    assert not preview.is_empty and preview.summary() == "新增 1", f"预览不对：{preview.summary()}"
    change = preview.changes[0]
    assert change.name == "回档笔记", f"变更行名字不对：{change.name}"
    assert "回档分类" in change.category, f"变更行分类不对：{change.category}"
    assert item.content == "改过的内容", "预览不应写数据"
    assert archive_count() == before, "预览不应建存档"

    report = service.restore(archive, "restore", snapshot_before=True)
    session.commit()
    assert report.snapshot is not None, "「先存档再回档」应留下快照"
    assert report.snapshot.name.startswith("回档前快照"), f"快照名不对：{report.snapshot.name}"
    assert report.snapshot.note == f"回档自「{archive.name}」前的自动存档", (
        f"快照说明不对：{report.snapshot.note}"
    )
    assert archive_count() == before + 1, "快照数量不对"

    original = service.snapshot_before_restore

    def fail(_source: str):
        raise RuntimeError("自检：快照故意失败")

    service.snapshot_before_restore = fail  # type: ignore[method-assign]
    try:
        current = archive_count()
        try:
            service.restore(archive, "restore", snapshot_before=True)
        except RuntimeError as exc:
            assert "快照故意失败" in str(exc), f"异常不对：{exc}"
        else:
            raise AssertionError("建快照失败时应中止回档")
        assert archive_count() == current, "快照失败后不应留下存档"
    finally:
        service.snapshot_before_restore = original  # type: ignore[method-assign]


@check("archive_gc", "services")
def archive_gc(case: Case) -> None:
    """可达性回收：无索引文件被扫掉、被引用的内容保留、自动清理由开关决定。"""
    from app.core.config import config
    from app.services import ArchiveService, ImportService
    from app.services.content_store import ContentStore

    session = case.session
    store = ContentStore(session)
    service = ArchiveService(session, store=store)
    importer = ImportService(session, store=store)

    keeper = importer.import_text("保留笔记", "这段文字不该被回收")
    session.commit()
    assert keeper is not None, "导入文本失败"
    archive = service.create("回收基线", "")
    session.commit()
    assert archive is not None, "建存档失败"
    keep_checksum = keeper.checksum
    assert store.content_available(keep_checksum), "内容应已入库"

    stray = store.root / "ab" / "cd" / "deadbeefdeadbeef"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"orphan")
    part = store.root / "ab" / "cd" / "half-upload.part"
    part.write_bytes(b"half")
    usage = store.usage()
    assert usage["orphans"] >= 1 and usage["part"] >= 1, f"应能看到无索引文件：{usage}"

    swept = store.sweep()
    session.commit()
    assert swept["orphans"] >= 1 and swept["part"] >= 1, f"扫描应清掉残留：{swept}"
    assert not stray.exists() and not part.exists(), "残留文件应被删除"
    assert store.content_available(keep_checksum), "被引用的内容不该被扫掉"
    assert store.usage()["orphans"] == 0, "扫描后不该还有无索引文件"

    config.set(config.autoCleanup, False)
    idle = service.auto_cleanup()
    assert idle["removed"] == 0 and idle["freed"] == 0, f"关闭自动清理时不该动仓库：{idle}"

    config.set(config.autoCleanup, True)
    service.auto_cleanup()
    session.commit()
    assert store.content_available(keep_checksum), "自动清理不该删被引用的内容"
    assert store.verify("deep").ok, f"清理后仓库应完好：{store.verify('deep').problems}"


@check("archive_empty_dirs", "services")
def archive_empty_dirs(case: Case) -> None:
    """删内容时顺手收掉空掉的哈希目录；清理也会兜底扫一遍遗留空目录。"""
    from app.services.content_store import ContentStore

    store = ContentStore(case.session, root=case.root / "store")
    checksum, rel_path, _size = store.put_bytes(b"empty-dir-probe", name="probe.bin")
    case.session.commit()
    folder = (store.root / rel_path).parent
    assert folder.is_dir(), "内容应落进哈希目录"

    assert store.remove(rel_path), "删除松散整块应成功"
    assert not folder.exists(), "删除内容后空掉的哈希目录应一起收掉"
    assert not folder.parent.exists(), "两级哈希目录都应清掉"

    leftovers = store.root / "ff" / "ee"
    leftovers.mkdir(parents=True, exist_ok=True)
    result = store.cleanup()
    assert result["empty_dirs"] >= 1, f"清理应报告收掉的空目录：{result}"
    assert not leftovers.exists(), "清理应扫掉遗留的空目录"


@check("archive_recompress", "services")
def archive_recompress(case: Case) -> None:
    """编码升级：旧编码内容按当前方案重写，身份不变、字节仍可读、占用下降。"""
    import zlib

    from sqlalchemy import select

    from app.db.models import Content
    from app.services.content_store import CODEC_DEFLATE, ContentStore, preferred_codec

    session = case.session
    store = ContentStore(session)
    text = ("编码升级的自检正文，重复重复重复重复。\n" * 4000).encode("utf-8")
    checksum, rel_path, _size = store.put_bytes(text, name="legacy.txt", mime="text/plain")
    session.commit()
    assert store.needs_recode() == 0, "刚入库的内容不该需要升级"

    packed = zlib.compress(text, 6)
    store.path_of(rel_path).write_bytes(packed)
    row = session.scalar(select(Content).where(Content.checksum == checksum))
    assert row is not None, "内容行没找到"
    row.codec = CODEC_DEFLATE
    row.stored_size = len(packed)
    session.commit()
    assert store.read_content(checksum) == text, "伪装出来的旧内容本身应能读回"
    assert store.verify("deep").ok, f"旧编码内容应能通过校验：{store.verify('deep').problems}"
    was = store.usage()["total_bytes"]
    assert store.needs_recode() == 1, "旧编码内容应被识别出来"

    stats = store.recompress()
    session.commit()
    assert stats["recoded"] == 1 and stats["freed"] > 0, f"应重写一份内容：{stats}"
    assert store.needs_recode() == 0, "升级后不该还需要整理"
    session.refresh(row)
    assert row.codec == preferred_codec(), f"应换成首选编码：{row.codec}"
    assert row.stored_size < len(packed), "首选编码应更小"
    assert store.total_size() < was, "升级后占用应下降"
    assert store.read_content(checksum) == text, "升级后内容读回不一致"
    assert store.verify("deep").ok, f"升级后仓库应完好：{store.verify('deep').problems}"


@check("archive_verify", "services")
def archive_verify(case: Case) -> None:
    """校验：缺失 / 大小不符 / 内容损坏分别记账，quick 只看大小、deep 才解压复核。"""
    from app.services import ImportService
    from app.services.content_store import ContentStore

    session = case.session
    store = ContentStore(session)
    payload = ("深度校验的自检正文，重复重复重复重复。\n" * 3000).encode("utf-8")
    item = ImportService(session, store=store).import_text("校验笔记", payload.decode("utf-8"))
    session.commit()
    assert item is not None and store.content_available(item.checksum), "内容应已入库"
    rel_path = store.rel_path_for(item.checksum)

    report = store.verify("quick")
    assert report.ok and report.contents == 1 and report.files == 1, f"干净仓库不该有问题：{report.summary()}"
    assert store.verify("deep").ok, "深度校验应通过"
    assert store.read_content(item.checksum) == payload, "校验不该改动仓库内容"
    try:
        store.verify("unknown")
    except ValueError as exc:
        assert "校验级别" in str(exc), f"未知级别的报错不对：{exc}"
    else:
        raise AssertionError("未知校验级别应报错")

    path = store.path_of(rel_path)
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
    quick = store.verify("quick")
    assert quick.ok, f"大小没变时 quick 不该报警：{quick.problems}"
    deep = store.verify("deep")
    assert deep.bad_checksum >= 1 and not deep.ok, f"deep 应发现内容损坏：{deep.problems}"

    path.write_bytes(original[:-5])
    truncated = store.verify("quick")
    assert truncated.bad_size == 1, f"截断应报大小不符：{truncated.problems}"
    assert any("大小" in problem for problem in truncated.problems), f"问题文案不对：{truncated.problems}"
    path.write_bytes(original)

    store.remove(rel_path)
    missing = store.verify("quick")
    assert missing.missing == 1 and not missing.ok, f"文件缺失应记账：{missing.problems}"


@check("archive_rebuild", "services")
def archive_rebuild(case: Case) -> None:
    """重新加载存档文件：缺的按存档还原、被改的以现有文件为准、没动的复用；只有默认用户可用。"""
    import os
    from pathlib import Path

    from sqlalchemy import select

    from app.core.config import config
    from app.db.models import DataItem
    from app.services import ArchiveService, ImportService, UserService
    from app.services.archive_service import RebuildBlocked, rebuild_in_progress
    from app.services.blob_store import sha256_of
    from app.services.content_store import ContentStore

    mb = 1 << 20
    session = case.session
    store = ContentStore(session)
    service = ArchiveService(session, store=store)
    importer = ImportService(session, store=store)

    missing_path = case.root / "missing.bin"
    missing_bytes = os.urandom(2 * mb + 17)
    missing_path.write_bytes(missing_bytes)
    changed_path = case.root / "changed.bin"
    changed_path.write_bytes(os.urandom(2 * mb + 33))
    result = importer.import_files([missing_path, changed_path])
    session.commit()
    assert result.added_count == 2, f"导入失败：{result.summary()}"
    note = importer.import_text("重建笔记", "没有改动的内容")
    session.commit()
    assert note is not None, "导入文本失败"
    archive = service.create("重建基线", "")
    session.commit()
    assert archive is not None, "建存档失败"
    entry_ids = {entry.id for entry in service.entries(archive)}
    assert len(entry_ids) == 3, f"条目数不对：{len(entry_ids)}"

    missing_item = session.scalar(select(DataItem).where(DataItem.checksum == sha256_of(missing_path)))
    changed_item = session.scalar(select(DataItem).where(DataItem.checksum == sha256_of(changed_path)))
    assert missing_item is not None and changed_item is not None, "导入的数据项没找到"
    missing_target = Path(missing_item.library.path) / missing_item.file_path
    changed_target = Path(changed_item.library.path) / changed_item.file_path
    missing_target.unlink()
    changed_path.write_bytes(os.urandom(2 * mb + 33))
    changed_target.write_bytes(changed_path.read_bytes())

    plan = service.plan_rebuild()
    counts = (len(plan.reuse_existing), len(plan.restore_needed), len(plan.diverged), len(plan.missing))
    assert counts == (1, 1, 1, 0), f"预检分类不对：{plan.summary()}"

    events: list[tuple[str, str]] = []
    report = service.rebuild_storage(
        on_event=lambda phase, index, total, detail: events.append((phase, detail))
    )
    session.commit()
    assert report.failed == 0 and report.missing == 0, report.summary()
    assert (report.restored_files, report.rebuilt, report.switched) == (1, 3, 3), report.summary()
    assert report.contents_before >= 1, f"重建前应有内容：{report.summary()}"
    assert sorted(phase for phase, _ in events) == ["rebuild", "rebuild", "rebuild", "restore"], events

    assert sha256_of(missing_target) == sha256_of(missing_path), "缺失的文件应按存档内容还原"
    session.refresh(missing_item)
    assert missing_item.checksum == sha256_of(missing_path), "还原后的数据项内容身份应不变"
    entries = service.entries(archive)
    assert {entry.id for entry in entries} == entry_ids, "重建不应增删存档条目"
    assert all(entry.checksum for entry in entries), "重建后每条都应记住内容校验和"
    assert {entry.checksum for entry in entries} == {
        sha256_of(missing_path),
        sha256_of(changed_target),
        note.checksum,
    }, "被改的文件应以现有内容为准更新内容身份"
    deep = service.verify("deep")
    assert deep.ok, f"重建后深度校验应通过：{deep.problems}"
    assert rebuild_in_progress() is False, "重建结束后应复位状态"

    try:
        service.rebuild_storage(cancel=lambda: True)
    except RebuildBlocked as exc:
        assert "已取消" in str(exc), f"取消文案不对：{exc}"
    else:
        raise AssertionError("取消后应中止重建")
    assert rebuild_in_progress() is False, "取消后应复位状态"

    blocked: list[str] = []

    def write_during_rebuild(*_args) -> None:
        if blocked:
            return
        try:
            service.create("重建期间不该建得出来")
        except RebuildBlocked as exc:
            blocked.append(str(exc))

    service.rebuild_storage(on_event=write_during_rebuild)
    session.commit()
    assert blocked and "重新加载存档文件" in blocked[0], f"重建期间写存档应被挡住：{blocked}"

    other = UserService(session).create("重建测试用户", "pw")
    assert other is not None, "创建普通用户失败"
    UserService(session).set_current(other)
    session.commit()
    try:
        service.rebuild_storage()
    except PermissionError as exc:
        assert "只有默认用户" in str(exc), f"权限文案不对：{exc}"
    else:
        raise AssertionError("普通用户不应能重新加载存档文件")
    finally:
        UserService(session).set_current(UserService(session).default())
        session.commit()


@check("archive_trash_scope", "services")
def archive_trash_scope(case: Case) -> None:
    """回收站里的项不进新存档；快照之后删的项回档时要撤销回收；覆盖式仅管理员可用。"""
    from app.services import ArchiveService, ImportService, ItemService, UserService

    session = case.session
    service = ArchiveService(session)
    owner = UserService(session).current()
    trashed = ImportService(session).import_text("已经删掉的笔记", "先删掉再存档")
    assert trashed is not None, "导入文本失败"
    assert ItemService(session).delete([trashed]) == 1, "移入回收站失败"
    session.commit()

    archive = service.create(note="不含回收站")
    session.commit()
    assert service.entries(archive) == [], "回收站里的项不该进新存档"
    empty = service.preview_restore(archive)
    assert empty.is_empty, f"新建存档后立刻预览不该有变更：{empty.summary()}"

    live = ImportService(session).import_text("先存档再删的笔记", "存档里有它")
    assert live is not None, "导入文本失败"
    session.commit()
    archive2 = service.create(note="含它")
    session.commit()
    assert len(service.entries(archive2)) == 1, "新存档应含 1 条"
    assert ItemService(session).delete([live]) == 1, "移入回收站失败"
    session.commit()
    back = service.preview_restore(archive2)
    assert [change.kind for change in back.changes] == ["撤销删除"], (
        f"快照后删除的项应撤销回收：{back.grouped()}"
    )
    report = service.restore(archive2, "restore")
    session.commit()
    assert report.undeleted == 1, f"回档应撤销回收：{report.summary()}"
    session.refresh(live)
    assert not live.is_deleted, "回档后该项应回到正常数据"

    try:
        service.preview_restore(archive2, "mirror", owner.id)
    except PermissionError as exc:
        assert "仅管理员" in str(exc), f"权限文案不对：{exc}"
    else:
        raise AssertionError("普通用户不该能用覆盖式回档")
    admin_view = service.preview_restore(archive2, "mirror")
    assert admin_view.mode == "mirror", "管理员应能用覆盖式回档"


@check("archive_empty_content", "services")
def archive_empty_content(case: Case) -> None:
    """0 字节内容也要落一份内容文件，校验不该把它算成异常。"""
    from app.services import ArchiveService, ImportService

    session = case.session
    note = ImportService(session).import_text("空笔记", "")
    assert note is not None, "导入空文本失败"
    session.commit()
    service = ArchiveService(session)
    archive = service.create(note="含空内容")
    session.commit()
    assert len(service.entries(archive)) == 1, "存档应含空笔记"
    quick = service.verify("quick")
    assert quick.bad_size == 0 and quick.missing == 0, f"空内容不该算异常：{quick.problems}"
    assert quick.ok, f"含空内容也应通过校验：{quick.summary()}"
    deep = service.verify("deep")
    assert deep.ok and deep.bad_checksum == 0, f"深度校验不对：{deep.problems}"


@check("archive_footprints", "services")
def archive_footprints(case: Case) -> None:
    """存档占用：按内容身份统计真实落盘字节，共享内容各存档各算一份、总量只算一份。"""
    from app.services import ArchiveService, ImportService

    session = case.session
    service = ArchiveService(session)
    importer = ImportService(session)

    first = importer.import_text("占用甲", "内容甲" * 200)
    second = importer.import_text("占用乙", "内容乙" * 200)
    assert first is not None and second is not None, "导入失败"
    session.commit()
    expected = len(("内容甲" * 200).encode()) + len(("内容乙" * 200).encode())

    archive_a = service.create("占用基线 A")
    session.commit()
    assert len(service.entries(archive_a)) == 2, "存档 A 应含两个条目"
    usage_a = service.archive_usage()
    share = int(usage_a["stored_size"])
    assert share > 0, f"小内容的实际占用也不该是 0：{usage_a}"
    assert service.footprints([archive_a])[int(archive_a.id)] == share, "单存档占用应与总量一致"
    assert usage_a["archives"] == 1 and usage_a["contents"] == 2, f"计数不对：{usage_a}"
    assert usage_a["logical_size"] == expected, f"逻辑大小应为两条内容之和：{usage_a}"

    archive_b = service.create("占用基线 B")
    session.commit()
    usage_b = service.archive_usage()
    assert usage_b["archives"] == 2, f"应有两条存档：{usage_b}"
    assert usage_b["stored_size"] == share, "同一份内容被两条存档引用时总量不该重复计"
    assert service.footprints([archive_b])[int(archive_b.id)] == share, "两条存档的占用应相同"
    assert usage_b["logical_size"] == 2 * expected, f"逻辑大小应按存档分别累加：{usage_b}"

    importer.import_text("占用丙", "内容丙" * 200)
    session.commit()
    archive_c = service.create("占用基线 C")
    session.commit()
    usage_c = service.archive_usage()
    assert usage_c["contents"] == 3, f"应有三份内容：{usage_c}"
    assert usage_c["stored_size"] > share, f"新增内容应让实际占用上升：{usage_c}"
    assert service.footprints([archive_a, archive_b, archive_c])[int(archive_c.id)] > share, (
        "新存档应比旧存档多出丙的占用"
    )


