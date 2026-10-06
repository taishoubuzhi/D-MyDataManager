"""向真实运行环境注入示例数据，方便直接上手体验（注入的数据不会被删除）。

用法：
    .venv\\Scripts\\python.exe scripts\\seed_demo.py

脚本先清空运行期数据（数据库 / 库文件夹 / 内容仓库 / 封面），再用 tests/dataset.py 生成一份
多样化示例数据（图片、视频、音频、文档、表格、演示、压缩包、代码与各类文本）并导入，
随后额外注入标签、隐藏项、回收站项、多个用户与一个存档快照。重复执行结果一致；
在「设置 → 维护 → 恢复初始化」或 scripts/dev_reset.py 中可以清空。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.core.runtime import paths # noqa: E402
from app.db import database  # noqa: E402
from app.db.models import DataType, guess_type  # noqa: E402
from app.repositories import ItemFilter, ItemRepository  # noqa: E402
from app.services import (  # noqa: E402
    ArchiveService,
    ImportService,
    ItemService,
    LibraryService,
    TaxonomyService,
    UserService,
    overview,
)
from app.services.maintenance import DEMO_DIR_NAME, reset_runtime_data  # noqa: E402
from tests.dataset import build_corpus  # noqa: E402

CATEGORY_BY_TYPE = {
    DataType.IMAGE: "图片素材",
    DataType.VIDEO: "影音资料",
    DataType.AUDIO: "影音资料",
    DataType.TEXT: "学习资料",
    DataType.DOCUMENT: "工作文档",
    DataType.SPREADSHEET: "工作文档",
    DataType.PRESENTATION: "工作文档",
    DataType.ARCHIVE: "工作文档",
    DataType.CODE: "学习资料",
    DataType.OTHER: "工作文档",
}

TAG_BY_TYPE = {
    DataType.IMAGE: ["收藏", "图片素材", "公共素材"],
    DataType.VIDEO: ["收藏", "年度归档", "影音"],
    DataType.AUDIO: ["待整理", "影音"],
    DataType.TEXT: ["学习", "待整理"],
    DataType.DOCUMENT: ["年度归档", "工作"],
    DataType.SPREADSHEET: ["参考", "工作", "年度归档"],
    DataType.PRESENTATION: ["工作", "待校对"],
    DataType.ARCHIVE: ["年度归档", "待整理"],
    DataType.CODE: ["参考", "学习"],
    DataType.OTHER: ["待整理"],
}

KEYWORD_BY_TYPE = {
    DataType.IMAGE: ["图片", "素材"],
    DataType.VIDEO: ["视频", "影音"],
    DataType.AUDIO: ["音频", "影音"],
    DataType.TEXT: ["文本", "笔记"],
    DataType.DOCUMENT: ["文档", "办公"],
    DataType.SPREADSHEET: ["表格", "数据"],
    DataType.PRESENTATION: ["演示", "汇报"],
    DataType.ARCHIVE: ["压缩包", "备份"],
    DataType.CODE: ["源码", "脚本"],
    DataType.OTHER: ["其他", "未分类"],
}


def _keywords_for(path) -> list[str]:
    """每条数据至少两个关键词：扩展名 + 类型关键词。"""
    data_type = guess_type(path.name)
    words = [path.suffix.lstrip(".").lower() or "无扩展名"]
    words.extend(KEYWORD_BY_TYPE.get(data_type, ["未分类"]))
    return list(dict.fromkeys(words))

EXTRA_CATEGORIES = (
    ("学习资料", ("编程笔记", "读书摘录")),
    ("工作文档", ("季度报表",)),
)

GLOBAL_TAGS = ("年度归档", "公共素材")
EXTRA_TAGS = ("参考", "待校对")

NOTES = (
    ("机器学习笔记", "梯度下降与反向传播的推导过程，含公式与手写推导。", ["算法", "数学"], ["重要"]),
    ("出行清单", "护照、充电器、相机、备用电池、离线地图。", ["生活"], ["待整理"]),
    ("SQL 速查", "SELECT / JOIN / GROUP BY / 窗口函数 的常用写法。", ["数据库"], []),
)

# 额外用户：(用户名, 账户说明, 笔记标题, 笔记正文)
EXTRA_USERS = (
    ("演示用户", "演示账户：少量数据，用于验证多用户之间的数据隔离。", "演示用户的私密笔记", "切换用户后只应看到本账户的数据，默认用户的数据不可见。"),
    ("家人共享", "家人共用账户：照片与视频集中在这里。", "家庭照片清单", "2026 年家庭出游的照片与视频，按地点与时间归档。"),
    ("归档账号", "历史归档账户：只保留索引与说明。", "归档说明", "本账户存放历史归档数据，原始文件已转移到外部硬盘。"),
)

ARCHIVE_NAME = "示例快照"
PER_USER_CATEGORY = ("图片素材", "影音资料", "学习资料")


def _categories(taxonomy: TaxonomyService, user_id: int) -> dict[str, object]:
    """取默认分类，并按需补几个子分类，返回「分类名 -> 分类对象」。"""
    nodes: dict[str, object] = {
        node.category.name: node.category for node in taxonomy.tree(user_id=user_id)
    }
    for parent_name, children in EXTRA_CATEGORIES:
        parent = nodes.get(parent_name)
        if parent is None:
            continue
        for name in children:
            created = taxonomy.create_category(name, parent_id=parent.id, user_id=user_id)
            if created is not None:
                nodes[name] = created
    return nodes


def _id_of(nodes: dict[str, object], name: str) -> int | None:
    category = nodes.get(name)
    return None if category is None else category.id


def inject(session) -> dict:
    """把示例数据写入当前数据库，返回注入统计。"""
    taxonomy = TaxonomyService(session)
    library = LibraryService(session).ensure_default()
    users = UserService(session)
    owner = users.current()
    importer = ImportService(session, library=library)
    corpus = build_corpus(paths.DATA_DIR / DEMO_DIR_NAME)
    nodes = _categories(taxonomy, owner.id)

    added = 0
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    def try_import(call: Callable[[], object], label: str):
        nonlocal added
        try:
            item = call()
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响整体注入
            failed.append((label, str(exc)))
            return None
        if item is None:
            skipped.append(label)
        else:
            added += 1
        return item

    # 默认用户：全局标签（所有用户可见）+ 个人标签 + 语料文件
    for tag_name in GLOBAL_TAGS:
        taxonomy.create_tag(tag_name, user_id=owner.id, is_global=True)
    for tag_name in EXTRA_TAGS:
        taxonomy.create_tag(tag_name, user_id=owner.id)

    for path in corpus.files:
        data_type = guess_type(path.name)
        try_import(
            lambda path=path, data_type=data_type: importer.import_file(
                path,
                category_id=_id_of(nodes, CATEGORY_BY_TYPE.get(data_type, "工作文档")),
                user_id=owner.id,
                keywords=_keywords_for(path),
                tags=list(TAG_BY_TYPE.get(data_type, ["待整理"])),
            ),
            path.name,
        )

    nested = importer.import_directory(
        corpus.root / "子目录A",
        category_id=_id_of(nodes, "编程笔记"),
        user_id=owner.id,
        keywords=["子目录", "批量导入"],
        tags=["待整理", "学习"],
    )
    added += nested.added_count
    skipped.extend(nested.skipped)
    failed.extend(nested.failed)

    for name, content, keywords, tags in NOTES:
        try_import(
            lambda name=name, content=content, keywords=keywords, tags=tags: importer.import_text(
                name,
                content,
                category_id=_id_of(nodes, "学习资料"),
                user_id=owner.id,
                keywords=list(keywords),
                tags=list(tags),
            ),
            name,
        )

    # 隐藏项与回收站项
    items = ItemService(session)
    rows = ItemRepository(session).query(ItemFilter(sort_by="created_at", user_ids={owner.id}))
    hidden = [row for row in rows if guess_type(row.name) in (DataType.IMAGE, DataType.VIDEO)][:2]
    if hidden:
        items.set_hidden(hidden, True)
    trashed = [row for row in rows if guess_type(row.name) is DataType.OTHER][:2]
    if trashed:
        items.delete(trashed)

    # 其他用户：各自拥有独立分类 / 标签 / 数据
    for index, (name, description, title, body) in enumerate(EXTRA_USERS):
        user = users.create(name)
        if user is None:
            continue
        tag_name = EXTRA_TAGS[index % len(EXTRA_TAGS)]
        taxonomy.create_tag(tag_name, description=description, user_id=user.id)
        user_nodes = _categories(taxonomy, user.id)
        user_importer = ImportService(session, library=library)
        try_import(
            lambda user=user, title=title, body=body, tag_name=tag_name, user_nodes=user_nodes: user_importer.import_text(
                title,
                body,
                category_id=_id_of(user_nodes, "学习资料"),
                user_id=user.id,
                keywords=[name, "私密笔记"],
                tags=[tag_name, "个人", GLOBAL_TAGS[0]],
            ),
            title,
        )
        for data_type in (DataType.IMAGE, DataType.CODE):
            candidates = corpus.of_type(data_type)
            if index >= len(candidates):
                continue
            path = candidates[index]
            try_import(
                lambda path=path, user=user, name=name, tag_name=tag_name, data_type=data_type, user_nodes=user_nodes: user_importer.import_file(
                    path,
                    category_id=_id_of(user_nodes, CATEGORY_BY_TYPE.get(data_type, "工作文档")),
                    user_id=user.id,
                    keywords=[name, data_type.value],
                    tags=[tag_name, "个人", GLOBAL_TAGS[0]],
                ),
                path.name,
            )
    users.set_current(owner)

    archive = ArchiveService(session).create(
        name=ARCHIVE_NAME, note="注入示例数据后创建，可在存档页对比与还原"
    )
    session.commit()

    return {
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "archive": archive.name,
        "stats": overview(session, user_id=owner.id),
        "users": len(EXTRA_USERS) + 1,
    }


def main() -> int:
    print("第 1 步：清空运行期数据（数据库 / 库文件夹 / 内容仓库 / 封面）…")
    reset_runtime_data()
    demo_dir = paths.DATA_DIR / DEMO_DIR_NAME
    print(f"第 2 步：生成示例文件到 {demo_dir} …")
    session = database.new_session()
    try:
        report = inject(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    stats = report["stats"]
    print(f"\n注入完成：新增 {report['added']} 条数据项")
    print(f"  跳过 {len(report['skipped'])} 条，失败 {len(report['failed'])} 条")
    for name, message in report["failed"]:
        print(f"    - {name}: {message}")
    print(
        f"  默认用户现有 {stats['total']} 条（隐藏 {stats['hidden']}，回收站 {stats['trashed']}，"
        f"重复组 {stats['duplicate_groups']}）"
    )
    print(
        f"  分类 {stats['categories']} 个，标签 {stats['tags']} 个，用户 {report['users']} 个，"
        f"内容 {stats['total_size']} 字节"
    )
    print(f"  存档快照：{report['archive']}")
    print("\n启动应用查看：.venv\\Scripts\\python.exe src\\main.py")
    print("清空示例数据：应用内「设置 → 维护 → 恢复初始化」，或 .venv\\Scripts\\python.exe scripts\\dev_reset.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
