# 优化重构配置加载与初始化架构 Spec

## Why
当前项目的配置加载和系统初始化流程存在严重断裂：`init_db.py` 和 `init_log.py` 引用了 `config.py` 中不存在的 `db_config`/`log_config`，导致整个初始化链无法工作；`demo.py` 未调用任何初始化函数；日志系统从未被初始化。

## What Changes
- 修复 `config.py` 的导出，使 init 模块能正确获取配置
- 修复 `init_db.py`，移除模块级副作用，改为延迟初始化
- 修复 `init_log.py`，统一配置引用方式
- 修复 `demo.py`，添加正确的初始化调用顺序（config → log → db）
- 修复 `db.py`，适配新的初始化方式

## Impact
- Affected code: `config.py`, `init_db.py`, `init_log.py`, `init/__init__.py`, `demo.py`, `db.py`
- Affected capabilities: 日志系统、数据库连接、应用启动流程

## ADDED Requirements

### Requirement: 配置导出修正
系统 SHALL 在 `config.py` 中导出 `log_config` 和 `db_config`，作为 `config` 对象的别名，使 init 模块能正确引用配置。

#### Scenario: init 模块导入配置
- **WHEN** `init_db.py` 执行 `from ..config import db_config`
- **THEN** `db_config` 应为 `config` 对象的引用，可正常调用 `.get()` 方法

### Requirement: 延迟初始化数据库
系统 SHALL 将数据库引擎和会话的创建改为延迟初始化模式，不再在模块导入时执行。

#### Scenario: 首次访问数据库引擎
- **WHEN** 调用 `get_engine()` 获取数据库引擎
- **THEN** 若引擎未初始化，自动创建并缓存；若已初始化，直接返回

#### Scenario: 首次访问数据库会话
- **WHEN** 调用 `get_session()` 获取数据库会话工厂
- **THEN** 若会话工厂未初始化，自动创建并缓存；若已初始化，直接返回

### Requirement: 日志初始化函数可调用
系统 SHALL 在应用启动时调用 `init_log()` 函数，确保日志系统正常工作。

#### Scenario: 应用启动时初始化日志
- **WHEN** `demo.py` 启动应用
- **THEN** 在创建主窗口前，应调用 `init_log()` 完成日志系统初始化

### Requirement: 正确的初始化顺序
系统 SHALL 在 `demo.py` 中按 config → log → db 的顺序执行初始化。

#### Scenario: 应用启动流程
- **WHEN** 用户运行 `demo.py`
- **THEN** 执行顺序为：加载配置 → 初始化日志 → 创建应用 → 创建主窗口

### Requirement: db.py 适配新初始化方式
系统 SHALL 修改 `db.py` 中的 `create_session()` 函数，使用延迟初始化方式获取 `DBSession`。

#### Scenario: 创建数据库会话
- **WHEN** 调用 `create_session()`
- **THEN** 通过 `get_session()` 获取会话工厂并创建会话

## MODIFIED Requirements

### Requirement: init/__init__.py 导出
将 `init/__init__.py` 的导出从直接导出 `engine`、`DBSession` 改为导出 `get_engine`、`get_session`、`init_log`、`init_db`。

## REMOVED Requirements
无
