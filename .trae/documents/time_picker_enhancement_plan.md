
# TimePickerDialog 组件增强计划

## 1. 仓库研究结论

- 当前 `TimePickerDialog` 组件在 `time_picker_dialog.py` 中实现
- 仅支持 "HH:mm" 格式，功能较单一
- 标题固定为 "Select Time"
- 内部使用 `QTimeEdit` 进行时间选择
- 已有 `timeSelected` 信号和 `get_time()` 方法

## 2. 需要修改的文件和模块

- `src/app/components/time_picker_dialog.py` - 主要修改文件

## 3. 修改步骤

### 3.1 增强构造函数参数
- 添加 `title` 参数，默认值为 "Select Time"，构造时直接设置
- 添加 `time_format` 参数，支持多种格式，构造时直接设置，默认值为 "HH:mm:ss"

### 3.2 支持的时间格式
- "HH:mm:ss" - 时:分:秒
- "HH:mm" - 时:分
- "HH:ss" - 时:秒
- "mm:ss" - 分:秒
- "HH" - 仅时
- "mm" - 仅分
- "ss" - 仅秒

### 3.3 内部存储完整时间
- 组件始终在内部存储完整的时/分/秒（datetime.time 对象）
- 根据 `time_format` 参数决定 UI 显示格式

### 3.4 API 设计
- `get_time()` - 获取当前格式对应的时间（可能是部分时间）
- `get_full_time()` - 获取完整的 datetime.time 对象
- `set_title(title)` - 动态设置标题
- `set_time_format(format)` - 动态设置时间格式
- `timeSelected` 信号发送完整的 datetime.time 对象

## 4. 依赖关系和考虑因素

- 保持向后兼容，现有代码可继续使用（调整 get_time 行为）
- 使用 QTimeEdit 的 setDisplayFormat() 方法处理不同显示格式
- 内部始终使用完整时间，避免数据丢失

## 5. 风险处理

- 验证 QTimeEdit 对各种格式的支持
- 确保不同格式之间切换时数据正确
- 调整 get_time 行为时注意现有调用的兼容性
