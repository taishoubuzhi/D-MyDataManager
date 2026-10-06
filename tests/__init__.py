r"""单元测试包。

运行方式::

    .venv\Scripts\python.exe -m unittest tests.core.test_manifest -v

用例按主题拆分：改哪块代码就只跑对应的模块，不做全量 ``discover``；新增用例的规范见
``docs/TESTS.md``。测试数据隔离在 ``tests/.tmp/``（进程退出时自动清除）；设置
``DM_KEEP_TMP=1`` 可保留临时目录。``scripts/`` 加进搜索路径，测试才能复用
``tmpenv`` 这类共用工具。
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

for _path in (ROOT, ROOT / "src", ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

#: 进程级 QApplication：只建一次并**一直持有**。
#: 若由某个用例自己建 QApplication 并在结束时被回收，Qt 会连带销毁先于它创建的 QObject
#: （`app.core.runtime.signals.signalBus` 就是模块级 QObject），后面的用例会报
#: 「wrapped C/C++ object of type SignalBus has been deleted」；这里提前建好并留强引用。
try:
    from PyQt6.QtWidgets import QApplication as _QApplication
except Exception:  # noqa: BLE001 - 没有 Qt 时纯逻辑用例照常跑
    _APP = None
else:
    _APP = _QApplication.instance() or _QApplication([])
