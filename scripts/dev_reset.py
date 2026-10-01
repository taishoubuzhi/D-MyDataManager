"""把数据库与运行期目录恢复到首次运行的干净状态（等价于界面里的「恢复初始化」）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.core import paths  # noqa: E402
from app.services.maintenance import reset_runtime_data  # noqa: E402


def main() -> int:
    """清空库文件夹 / 备份仓库 / 封面并重建空库。"""
    reset_runtime_data()
    print("db:", paths.DB_FILE.exists(), "library:", paths.DEFAULT_LIBRARY_DIR.exists())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
