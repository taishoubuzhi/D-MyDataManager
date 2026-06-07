# Tasks

- [x] Task 1: 修复 config.py 导出，添加 log_config 和 db_config 别名
  - [x] SubTask 1.1: 在 config.py 末尾添加 `log_config = config` 和 `db_config = config` 别名导出

- [x] Task 2: 重构 init_db.py，改为延迟初始化
  - [x] SubTask 2.1: 将模块级 `engine` 和 `DBSession` 改为 `None` 初始值
  - [x] SubTask 2.2: 实现 `get_engine()` 函数，延迟创建并缓存 engine
  - [x] SubTask 2.3: 实现 `get_session()` 函数，延迟创建并缓存 DBSession
  - [x] SubTask 2.4: 实现 `init_db()` 函数，显式调用初始化

- [x] Task 3: 修复 init_log.py，统一配置引用
  - [x] SubTask 3.1: 将 `init_log()` 中直接使用 `log_config` 的地方改为使用传入的 `log_config` 参数（已通过 import 获取）

- [x] Task 4: 更新 init/__init__.py 导出
  - [x] SubTask 4.1: 导出 `get_engine`、`get_session`、`init_log`、`init_db`

- [x] Task 5: 修复 db.py，适配新的初始化方式
  - [x] SubTask 5.1: 将 `from .init import DBSession` 改为 `from .init import get_session`
  - [x] SubTask 5.2: 修改 `create_session()` 使用 `get_session()` 获取会话工厂

- [x] Task 6: 修复 demo.py，添加正确的初始化调用
  - [x] SubTask 6.1: 在创建 QApplication 前调用 `init_log()`
  - [x] SubTask 6.2: 在创建主窗口前调用 `init_db()`

# Task Dependencies
- Task 1 → Task 2, Task 3 (init 模块依赖 config 导出)
- Task 2 → Task 4 (导出依赖 init_db 重构)
- Task 3 → Task 4 (导出依赖 init_log 修复)
- Task 4 → Task 5 (db.py 依赖 init 包的新导出)
- Task 4 → Task 6 (demo.py 依赖 init 包的新导出)
