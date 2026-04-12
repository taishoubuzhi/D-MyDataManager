# 动态显示配置项卡片 - Product Requirement Document

## Overview
- **Summary**: 为设置界面添加动态显示配置项卡片功能，根据配置项的状态智能显示/隐藏相关的子配置项，使界面更加简洁易用
- **Purpose**: 改善设置界面的用户体验，减少不必要的配置项显示，让用户只看到与当前选择相关的配置项
- **Target Users**: 应用程序用户，特别是需要调整日志设置的用户

## Goals
- 添加全局开关控制动态显示功能
- 当 outputFileCard 关闭时，隐藏所有日志文件相关配置项
- 根据 rotateMode 的选择，只显示对应模式的配置项
- 保持向后兼容性，禁用动态显示时显示所有配置项

## Non-Goals (Out of Scope)
- 重构整个设置界面
- 添加新的配置项
- 修改现有配置项的功能

## Background & Context
- 当前设置界面显示所有配置项，即使某些配置项在特定状态下不相关
- 日志配置部分有多个模式（Size/Time/Interval），每个模式只需要特定的配置项
- 当 outputFileCard 关闭时，所有文件相关配置项都应该隐藏

## Functional Requirements
- **FR-1**: 添加全局配置项控制是否启用动态显示
- **FR-2**: 当动态显示启用且 outputFileCard 关闭时，隐藏所有日志文件相关配置项
- **FR-3**: 根据 rotateMode 的选择，只显示对应模式的配置项
- **FR-4**: 当动态显示禁用时，显示所有配置项（保持现有行为）

## Non-Functional Requirements
- **NFR-1**: 配置项的显示/隐藏切换应流畅，无明显延迟
- **NFR-2**: 代码变更应最小化，不破坏现有功能
- **NFR-3**: 保持代码风格与现有代码一致

## Constraints
- **Technical**: 使用 PyQt6 和 qfluentwidgets 框架
- **Business**: 保持向后兼容性
- **Dependencies**: 现有配置系统和设置界面

## Assumptions
- 用户希望界面更简洁，只显示相关配置项
- 现有代码结构可以支持添加此功能
- 配置项的依赖关系是明确的

## Acceptance Criteria

### AC-1: 全局动态显示开关
- **Given**: 用户在设置界面
- **When**: 用户查看个性化设置组
- **Then**: 应该能看到一个新的开关，控制是否启用动态显示配置项
- **Verification**: `programmatic`
- **Notes**: 开关默认可以设置为关闭，保持向后兼容

### AC-2: outputFileCard 控制子配置项显示
- **Given**: 动态显示已启用
- **When**: outputFileCard 关闭
- **Then**: 所有日志文件相关配置项（differentLevelFileCard 之后的所有配置项）应该隐藏
- **Verification**: `programmatic`

### AC-3: outputFileCard 开启时显示子配置项
- **Given**: 动态显示已启用
- **When**: outputFileCard 开启
- **Then**: 所有日志文件相关配置项应该显示
- **Verification**: `programmatic`

### AC-4: rotateMode 控制对应配置项显示
- **Given**: 动态显示已启用，outputFileCard 开启
- **When**: rotateMode 选择为 "Size"
- **Then**: 只显示 rotateSizeCard 和 rotateSizeUnitCard，其他时间/间隔相关配置项隐藏
- **Verification**: `programmatic`

### AC-5: rotateMode Time 模式
- **Given**: 动态显示已启用，outputFileCard 开启
- **When**: rotateMode 选择为 "Time"
- **Then**: 只显示 rotateTimeCard，其他大小/间隔相关配置项隐藏
- **Verification**: `programmatic`

### AC-6: rotateMode Interval 模式
- **Given**: 动态显示已启用，outputFileCard 开启
- **When**: rotateMode 选择为 "Interval"
- **Then**: 只显示 rotateIntervalCard 和 rotateIntervalUnitCard，其他大小/时间相关配置项隐藏
- **Verification**: `programmatic`

### AC-7: rotateMode None 模式
- **Given**: 动态显示已启用，outputFileCard 开启
- **When**: rotateMode 选择为 "None"
- **Then**: 所有 rotate 相关配置项（rotateSizeCard, rotateSizeUnitCard, rotateTimeCard, rotateIntervalCard, rotateIntervalUnitCard）都隐藏
- **Verification**: `programmatic`

### AC-8: 禁用动态显示时显示所有配置项
- **Given**: 动态显示已禁用
- **When**: 用户查看日志配置组
- **Then**: 所有配置项都应该显示，无论其他配置项的状态如何
- **Verification**: `programmatic`

### AC-9: 配置项状态变化实时更新显示
- **Given**: 动态显示已启用
- **When**: 用户修改 outputFileCard 或 rotateMode 的状态
- **Then**: 相关配置项的显示/隐藏状态应该立即更新
- **Verification**: `human-judgment`

## Open Questions
- [ ] 动态显示开关的默认值应该是什么？（建议默认关闭，保持向后兼容）
