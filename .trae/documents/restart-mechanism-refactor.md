# 重启机制重构计划：从进程内重启改为 QProcess 外部重启

## 问题现状

### 问题1：第一个窗口无法自动关闭
- `restart()` 中 `_window.close()` → 触发 `closeEvent` → `themeListener.terminate()` 强制终止线程
- `terminate()` 是异步的，线程未完全退出，窗口对象无法正确销毁
- 新窗口创建后旧窗口残留

### 问题2：关闭所有窗口后进程仍在运行
- `SystemThemeListener` 是 QThread，`terminate()` 不保证线程立即停止
- 线程仍在运行 → Qt 事件循环不退出 → 进程不退出
- `QApplication.quitOnLastWindowClosed` 默认 True，但线程阻止了"最后一个窗口关闭"的判定

### 根因
- 进程内重启方案依赖正确销毁旧窗口，但 Qt 的窗口销毁和线程终止都是异步的
- 在同一个进程内"先关旧窗口、再建新窗口"存在时序竞争

## 解决方案

**撤销进程内重启，改用 QProcess 启动新进程后退出当前进程。**

### 核心思路
1. 点击"重启应用"按钮 → 启动一个新的 Python 进程（QProcess.startDetached）
2. 新进程启动后 → 当前进程优雅退出（QApplication.quit()）
3. 不再尝试在进程内销毁/重建窗口

### 优势
- 新进程是干净的，没有旧窗口/线程残留问题
- 旧进程退出后操作系统会回收所有资源（线程、内存等）
- 不需要处理复杂的窗口销毁时序

## 具体修改

### 文件1：`src/demo.py`

**修改内容**：移除 `restart()` 函数的进程内重启逻辑，改为 QProcess 外部重启

```python
import os
import sys

from PyQt6.QtCore import Qt, QTranslator
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentTranslator

from app.common.config import config, load_config
from app.common.init import init_log, init_db

from app.view.main_window import MainWindow

from loguru import logger

init_log()
logger.info("Application starting...")
init_db()

if config.get(config.dpi_scale) != "Auto":
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = str(config.get(config.dpi_scale))

app = QApplication(sys.argv)
app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)

_translator = None
_appTranslator = None
_window = None


def _setup_translators():
    global _translator, _appTranslator
    if _translator:
        app.removeTranslator(_translator)
    if _appTranslator:
        app.removeTranslator(_appTranslator)
    locale = config.get(config.language).value
    _translator = FluentTranslator(locale)
    _appTranslator = QTranslator()
    _appTranslator.load(locale, "app", ".", ":/app/i18n")
    app.installTranslator(_translator)
    app.installTranslator(_appTranslator)


def restart():
    """ Restart application by launching a new process and exiting the current one """
    from PyQt6.QtCore import QProcess
    logger.info("Restarting application via new process...")

    # 启动新进程
    program = sys.executable
    args = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    QProcess.startDetached(program, args)

    # 退出当前进程
    QApplication.quit()


_setup_translators()
_window = MainWindow()
_window.show()

app.exec()
```

**关键点**：
- `QProcess.startDetached()` 启动独立的新进程，不依赖父进程
- `QApplication.quit()` 退出当前进程的事件循环
- 不再需要 `_window` 全局变量的复杂管理

### 文件2：`src/app/view/main_window.py`

**修改内容**：修复 `closeEvent` 中线程停止方式

```python
def closeEvent(self, e):
    logger.info("Application closed")
    # 优雅停止主题监听线程
    self.themeListener.quit()
    self.themeListener.wait(3000)  # 最多等待3秒
    super().closeEvent(e)
```

**关键点**：
- `quit()` 替代 `terminate()`：优雅请求线程退出
- `wait(3000)` 等待线程真正退出，超时后继续
- 移除 `deleteLater()`：`quit()+wait()` 后线程自然清理，不需要手动延迟删除

### 文件3：`src/app/view/setting_interface.py`

**修改内容**：`__restartApp()` 方法简化

```python
def __restartApp(self):
    """ Restart application by launching a new process """
    import demo
    demo.restart()
```

**关键点**：
- 不再需要 `QTimer.singleShot(0, ...)` 延迟执行
- 因为 `demo.restart()` 会启动新进程后立即 `QApplication.quit()`，不需要担心访问已销毁对象
- 直接调用即可

## 验证步骤

1. 在 PyCharm 中运行程序，修改一个需要重启的配置项（如日志级别）
2. 点击"重启应用"按钮 → 旧进程应退出，新进程应启动并显示新窗口
3. 重复操作 → 新窗口也能正常重启
4. 点击窗口右上角关闭按钮 → 进程应完全退出（PyCharm 中不再显示运行中）
5. 还原配置按钮功能不受影响（不需要重启，只重置配置值）

## 假设与决策

- **假设**：`QProcess.startDetached()` 在 PyCharm 调试环境下能正常工作
- **决策**：使用 `QProcess.startDetached()` 而非 `subprocess.Popen`，因为前者是 Qt 原生方式，跨平台兼容性更好
- **决策**：`closeEvent` 中使用 `quit()+wait()` 替代 `terminate()`，确保线程优雅退出
- **决策**：不在 `restart()` 中做任何旧窗口清理，直接启动新进程并退出，让操作系统回收资源
