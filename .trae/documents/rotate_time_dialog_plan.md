
# Rotate Time 时间选择对话框实现计划

## 1. 仓库研究结论

- 项目使用 PyQt6 与 QFluentWidgets 构建 UI
- `setting_interface.py` 包含设置界面，`rotateTimeCard` 是一个 `PushSettingCard`，点击事件绑定到 `__onRotateTimeCard` 函数（目前为空）
- `config.py` 中的 `rotate_time` 是一个使用 `FileTimeSerializer` 序列化的 `datetime.time` 对象，默认值为 `00:00`
- 项目已有必要的导入：`QTime`, `QTimeEdit`, `QDialog`, `QVBoxLayout`, `QHBoxLayout`, `QPushButton`

## 2. 需要修改的文件和模块

- `src/app/view/setting_interface.py` - 实现 `__onRotateTimeCard` 函数

## 3. 修改步骤

### 3.1 创建时间选择对话框类
在 `SettingInterface` 类中实现 `__onRotateTimeCard` 函数，该函数将：
1. 创建一个自定义对话框，包含 `QTimeEdit` 组件
2. 添加确定和取消按钮
3. 读取当前配置的 `rotate_time` 并初始化 `QTimeEdit`
4. 用户点击确定时，将选择的时间更新到配置
5. 更新 `rotateTimeCard` 上显示的时间文本

### 3.2 实现细节
- 使用 `QTimeEdit` 组件进行时间选择
- 对话框使用 `QDialog` 并设置为模态对话框
- 将 `QTime` 与 `datetime.time` 进行互相转换
- 使用 `config.set(config.rotate_time, new_time)` 更新配置

## 4. 依赖关系和考虑因素

- 无额外依赖，项目已包含所需的 PyQt6 组件
- 需要确保与现有的 `FileTimeSerializer` 序列化逻辑兼容
- 对话框应与 QFluentWidgets 风格一致

## 5. 风险处理

- 无重大风险，实现相对简单
- 如果用户取消选择，不做任何修改
- 保持现有配置系统的完整性
