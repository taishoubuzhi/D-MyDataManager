# 完善日志输出功能 Spec

## Why
当前日志输出存在两个问题：1) 日志文件输出到 `src/logs` 而非项目根目录 `logs`；2) 按级别分文件输出不符合需求，应改为单一文件（以运行时间命名）+ 日志轮换。

## What Changes
- 修改日志输出目录默认值为项目根目录 `logs`
- **BREAKING** 移除按级别分文件输出功能（`different_level_file` 及各级别文件名配置项）
- 重构 `init_log()`：文件输出改为以运行时间命名单一日志文件，与控制台同步输出
- 清理 `config.py` 和 `config.json` 中不再需要的配置项
- 清理 `setting_interface.py` 中不再需要的 UI 卡片

## Impact
- Affected specs: optimize-config-init
- Affected code: `init_log.py`, `config.py`, `config.json`, `setting_interface.py`

## ADDED Requirements

### Requirement: 日志文件以运行时间命名
系统 SHALL 将日志文件以启动时间命名（格式 `YYYY-MM-DD_HH-MM-SS.log`），所有级别的日志统一输出到该文件。

#### Scenario: 应用启动创建日志文件
- **WHEN** 应用启动并初始化日志
- **THEN** 在日志目录下创建以当前运行时间命名的 `.log` 文件，所有级别日志写入同一文件

### Requirement: 日志输出目录为项目根目录 logs
系统 SHALL 将日志默认输出到项目根目录下的 `logs` 文件夹。

#### Scenario: 默认日志路径
- **WHEN** 使用默认配置启动应用
- **THEN** 日志文件输出到 `{项目根目录}/logs/` 目录

## MODIFIED Requirements

### Requirement: init_log() 文件输出逻辑
将原来的"主日志文件 + 按级别分文件"改为"单一文件以运行时间命名"，与控制台同步输出所有级别日志。

## REMOVED Requirements

### Requirement: 按级别分文件输出
**Reason**: 不符合需求，改为单一文件输出
**Migration**: 移除 `different_level_file` 配置项及各级别文件名配置项（`trace_level_file`、`debug_level_file` 等），移除 `file_override` 配置项（不再需要，每次运行都是新文件）

### Requirement: 主日志文件名配置
**Reason**: 改为自动以运行时间命名
**Migration**: 移除 `log_file` 配置项
