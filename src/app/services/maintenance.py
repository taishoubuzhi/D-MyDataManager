"""维护操作：把运行期数据与设置恢复为首次运行的初始化状态。

界面「设置 → 维护 → 恢复初始化」与脚本（scripts/dev_reset.py）共用这里的实现，
避免多处各写一份删除逻辑。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from qfluentwidgets import ConfigItem, qconfig

from ..core import paths
from ..core.config import config, library_root, release_resource_root
from ..db import database
from ..db.seed import seed

# 示例脚本生成的原始文件目录（随初始化一并清理）
DEMO_DIR_NAME = "demo"


def _removable_dirs() -> list[Path]:
    return [
        library_root(),
        paths.DEFAULT_LIBRARY_DIR,
        paths.LEGACY_STORE_DIR,
        paths.LEGACY_COVER_DIR,
        paths.DATA_DIR / DEMO_DIR_NAME,
    ]


def _remove_dir(directory: Path) -> None:
    """删除目录及其内容，但拒绝作用在项目根与磁盘根上。"""
    if not directory.exists():
        return
    target = directory.resolve()
    if target == paths.ROOT.resolve() or target.parent == target:
        return
    shutil.rmtree(target, ignore_errors=True)


def config_items() -> list[ConfigItem]:
    """收集 Config 与 QConfig 上定义的全部配置项（直接读类字典，避免描述符求值）。"""
    items: dict[str, ConfigItem] = {}
    for klass in reversed(type(config).__mro__):
        for name, value in vars(klass).items():
            if isinstance(value, ConfigItem):
                items[name] = value
    return list(items.values())


def reset_config() -> None:
    """把所有设置项恢复为默认值并写回 .configs/config.json。"""
    for item in config_items():
        qconfig.set(item, item.defaultValue)


def reset_runtime_data() -> None:
    """清空数据库、库文件夹、内容仓库、封面与示例文件，重建空库并写入默认数据。"""
    database.dispose_engine()
    # 配置可能刚被恢复默认值：资源文件夹跟着配置走
    paths.apply_resource_root(paths.resource_root(config.resourcePath.value))
    release_resource_root()  # 锁着的话删不掉、也建不了
    for directory in dict.fromkeys(_removable_dirs()):
        _remove_dir(directory)
    paths.ensure_dirs()
    database.init_db(force=True)
    session = database.new_session()
    try:
        seed(session)
        session.commit()
    finally:
        session.close()
    # 静态保护：运行期整场放行，重建后不需要立刻锁上（退出时才锁）


def reset_to_defaults() -> None:
    """恢复初始化状态：设置回默认值 + 清空运行期数据。此操作不可撤销。"""
    reset_config()
    reset_runtime_data()
