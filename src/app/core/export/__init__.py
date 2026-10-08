"""导出引擎：命名模板、分包、打包三件套，被 `app.services.export_service` 与
SDK 接口 `export.open` 复用。

* `template` —— 命名模板：`{creator}-自定义文本-{time}`，编号式变量支持起点与间隔
  （`{number,3,2}`、`{alpha,b,2}`），时间式变量支持自带格式（`{date,%Y%m%d}`）；
* `planner`  —— 分包：一个包 / 按最顶层分类分别打包；
* `packer`   —— 打包：先写 `.part` 再原子改名，绝不留下半成品。

三层都只依赖标准库，不碰界面、不碰数据库，方便单测与在插件侧的复用。
"""

from __future__ import annotations

from .packer import PackEntry, PackError, PART_SUFFIX, ZipPack, write_zip
from .planner import (
    DEFAULT_SUFFIX,
    NamedPackage,
    PLAN_MODES,
    PackagePlan,
    PlannedItem,
    PlannedPackage,
    UNASSIGNED_LABEL,
    mode_label,
    name_packages,
    plan_packages,
)
from .template import (
    DEFAULT_TEMPLATE,
    RenderContext,
    RenderedName,
    TemplateError,
    Token,
    VARIABLE_MAP,
    VARIABLES,
    Variable,
    alpha_name,
    alpha_ordinal,
    apply_numbering,
    from_roman,
    numbered_tokens,
    ordinal,
    parse_start,
    preview,
    render,
    safe_filename,
    to_roman,
    token_names,
    unique_name,
    unknown_names,
)

__all__ = [
    "DEFAULT_SUFFIX",
    "DEFAULT_TEMPLATE",
    "NamedPackage",
    "PART_SUFFIX",
    "PLAN_MODES",
    "PackEntry",
    "PackError",
    "PackagePlan",
    "PlannedItem",
    "PlannedPackage",
    "RenderContext",
    "RenderedName",
    "TemplateError",
    "Token",
    "UNASSIGNED_LABEL",
    "VARIABLE_MAP",
    "VARIABLES",
    "Variable",
    "ZipPack",
    "alpha_name",
    "alpha_ordinal",
    "apply_numbering",
    "from_roman",
    "mode_label",
    "name_packages",
    "numbered_tokens",
    "ordinal",
    "parse_start",
    "plan_packages",
    "preview",
    "render",
    "safe_filename",
    "to_roman",
    "token_names",
    "unique_name",
    "unknown_names",
    "write_zip",
]
