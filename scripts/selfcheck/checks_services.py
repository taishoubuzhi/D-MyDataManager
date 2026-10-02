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
    config.set(config.resourceProtected, True)
    assert privacy.enabled(), "资源保护开关没有生效"
    assert privacy.targets() == [resources_root()], f"资源保护应对准资源根：{privacy.targets()}"
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
