# Tasks

- [x] Task 1: 清理 config.py，移除废弃配置项
  - [x] SubTask 1.1: 移除 `different_level_file`、`file_override`、`log_file` 及各级别文件名配置项
  - [x] SubTask 1.2: 修改 `file_path` 默认值为项目根目录 `logs` 路径

- [x] Task 2: 更新 config.json，移除废弃配置项并修改日志路径
  - [x] SubTask 2.1: 移除 `File-Overwrite`、`File-Log`、`File-Trace`、`File-Debug`、`File-Info`、`File-Success`、`File-Warn`、`File-Error`、`File-Critical` 字段
  - [x] SubTask 2.2: 移除 `File` 分组中的 `Different-Level-Files` 字段
  - [x] SubTask 2.3: 修改 `Log-Path` 为项目根目录 `logs` 路径

- [x] Task 3: 重构 init_log.py，改为单文件输出
  - [x] SubTask 3.1: 移除 `_get_log_file_path()` 函数
  - [x] SubTask 3.2: 修改 `init_log()` 中的文件输出逻辑：以运行时间命名文件，与控制台同步输出所有级别日志

- [x] Task 4: 清理 setting_interface.py，移除废弃 UI 卡片
  - [x] SubTask 4.1: 移除 `differentLevelFileCard`、`fileOverrideCard` 相关代码
  - [x] SubTask 4.2: 更新 `__updateConfigCardVisibility()` 移除对已删除卡片的引用
  - [x] SubTask 4.3: 更新 `__initLayout()` 移除已删除卡片的添加

# Task Dependencies
- Task 1 → Task 3 (init_log 依赖 config 定义)
- Task 2 → Task 3 (init_log 依赖 config.json 配置)
- Task 1 → Task 4 (setting_interface 依赖 config 定义)
