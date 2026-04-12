# 动态显示配置项卡片 - The Implementation Plan (Decomposed and Prioritized Task List)

## [ ] Task 1: 在 config.py 中添加动态显示配置项
- **Priority**: P0
- **Depends On**: None
- **Description**: 
  - 在 Config 类中添加一个新的 BoolConfigItem，用于控制是否启用动态显示
  - 配置项名称建议为：dynamic_config_display
  - 默认值设为 False（保持向后兼容）
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: 检查 config.py 中是否添加了 dynamic_config_display 配置项
  - `programmatic` TR-1.2: 检查配置项默认值是否为 False
- **Notes**: 配置项应该放在合适的位置，比如 MainWindow 或 Personalization 组

## [ ] Task 2: 在 setting_interface.py 中添加动态显示开关卡片
- **Priority**: P0
- **Depends On**: Task 1
- **Description**: 
  - 在 personalGroup 中添加一个 SwitchSettingCard
  - 绑定到 config.dynamic_config_display
  - 添加合适的图标和描述文本
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-2.1: 检查是否在 personalGroup 中添加了新的开关卡片
  - `programmatic` TR-2.2: 检查卡片是否正确绑定到 config.dynamic_config_display
  - `human-judgement` TR-2.3: 检查卡片的图标和描述文本是否合适
- **Notes**: 建议放在 themeColorCard 附近

## [ ] Task 3: 实现配置项动态显示的核心逻辑
- **Priority**: P0
- **Depends On**: Task 2
- **Description**: 
  - 创建一个私有方法，根据当前配置状态更新所有相关配置项的可见性
  - 方法名称建议为：__updateConfigCardVisibility
  - 该方法应该根据 dynamic_config_display、output_file、rotate_mode 的值来决定哪些卡片显示/隐藏
- **Acceptance Criteria Addressed**: AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8
- **Test Requirements**:
  - `programmatic` TR-3.1: 检查是否创建了 __updateConfigCardVisibility 方法
  - `programmatic` TR-3.2: 检查方法中是否处理了 outputFileCard 的显示/隐藏逻辑
  - `programmatic` TR-3.3: 检查方法中是否处理了 rotateMode 不同模式的显示/隐藏逻辑
- **Notes**: 该方法应该在初始化时调用一次，以及在相关配置变化时调用

## [ ] Task 4: 连接信号槽，实现配置变化时的实时更新
- **Priority**: P0
- **Depends On**: Task 3
- **Description**: 
  - 在 __connectSignalToSlot 方法中连接相关信号
  - outputFileCard 的 checkedChanged 信号
  - rotateModeCard 的 valueChanged 或当前值变化信号
  - dynamic_config_display 的变化信号（如果有的话）
  - 当这些信号触发时，调用 __updateConfigCardVisibility 方法
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `programmatic` TR-4.1: 检查是否在 __connectSignalToSlot 中连接了 outputFileCard 的信号
  - `programmatic` TR-4.2: 检查是否在 __connectSignalToSlot 中连接了 rotateModeCard 的信号
  - `human-judgement` TR-4.3: 测试修改配置时，相关卡片的可见性是否立即更新
- **Notes**: 需要查看 qfluentwidgets 的 ComboBoxSettingCard 如何发送值变化信号

## [ ] Task 5: 初始化时调用可见性更新方法
- **Priority**: P0
- **Depends On**: Task 3
- **Description**: 
  - 在 __initWidget 或 __initLayout 方法的末尾调用 __updateConfigCardVisibility
  - 确保界面初始化时配置项的可见性是正确的
- **Acceptance Criteria Addressed**: AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8
- **Test Requirements**:
  - `programmatic` TR-5.1: 检查初始化时是否调用了 __updateConfigCardVisibility
  - `human-judgement` TR-5.2: 测试启动应用时，配置项的可见性是否正确
- **Notes**: 应该在所有卡片都添加到布局后再调用

## [ ] Task 6: 测试和验证所有功能
- **Priority**: P1
- **Depends On**: Task 4, Task 5
- **Description**: 
  - 测试所有接受标准的场景
  - 验证动态显示开关的功能
  - 验证 outputFileCard 的控制功能
  - 验证各种 rotateMode 模式的显示逻辑
  - 验证禁用动态显示时的行为
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9
- **Test Requirements**:
  - `human-judgement` TR-6.1: 完整测试所有场景，确保功能正常
  - `human-judgement` TR-6.2: 验证向后兼容性（动态显示关闭时的行为）
- **Notes**: 测试时要覆盖所有可能的配置组合
