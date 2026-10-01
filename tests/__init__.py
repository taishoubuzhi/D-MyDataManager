r"""单元测试包。

运行方式::

    .venv\Scripts\python.exe -m unittest tests.test_manage -v

用例按主题拆分：改哪块代码就只跑对应的模块，不做全量 ``discover``；新增用例的范式见
``tests/README.md``。测试数据隔离在 ``tests/_tmp/``；设置 ``DM_KEEP_TMP=1`` 可保留临时目录。
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
