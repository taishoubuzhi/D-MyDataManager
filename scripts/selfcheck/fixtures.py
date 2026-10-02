"""自检夹具：只通过公开服务造数据，返回不含 ORM 对象的句柄。"""

from __future__ import annotations

from dataclasses import dataclass

from .harness import ROOT

SAMPLE_IMAGE = ROOT / "src" / "app" / "resource" / "images" / "logo.png"


@dataclass(frozen=True)
class Fixture:
    """一组代表性数据的句柄（id / 名字，可直接跨会话使用）。"""

    category_root: int
    category_child: int
    tag: str
    text_item: int
    file_item: int | None
    user_id: int
    user_name: str


def build(case) -> Fixture:
    """造两级分类 + 标签 + 文本项 + 图片项 + 第二个普通用户。"""
    from app.repositories import CategoryRepository
    from app.services import ImportService, TaxonomyService, UserService

    session = case.session
    taxonomy = TaxonomyService(session)
    root = CategoryRepository(session).by_name("学习资料")
    assert root is not None, "默认分类「学习资料」缺失"
    child = taxonomy.create_category("Python", parent_id=root.id)
    assert child is not None, "创建子分类失败"
    assert taxonomy.create_tag("自检标签", description="自检用") is not None, "创建标签失败"
    importer = ImportService(session)
    text_item = importer.import_text(
        "自检笔记", "第一行\n第二行", category_id=child.id, keywords=["自检"], tags=["自检标签"]
    )
    assert text_item is not None, "导入文本项失败"
    file_item = None
    if SAMPLE_IMAGE.exists():
        result = importer.import_files([SAMPLE_IMAGE])
        file_item = result.added[0].id if result.added else None
    guest = UserService(session).create("自检用户")
    assert guest is not None, "创建用户失败"
    session.commit()
    return Fixture(
        category_root=root.id,
        category_child=child.id,
        tag="自检标签",
        text_item=text_item.id,
        file_item=file_item,
        user_id=guest.id,
        user_name=guest.name,
    )
