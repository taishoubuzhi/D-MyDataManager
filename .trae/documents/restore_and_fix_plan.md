
# 文件状态恢复与修复计划

## 1. 当前文件状态分析

### 正常的文件（无需修复）
- ✅ `src/app/components/time_picker_dialog.py` - 已存在且是增强版本
- ✅ `src/app/resource/qss/dark/time_picker_dialog.qss` - 已存在
- ✅ `src/app/resource/qss/light/time_picker_dialog.qss` - 已存在

### 需要修复的文件
1. ❌ `src/app/components/__init__.py` - 缺少 TimePickerDialog 导出
2. ❌ `src/app/common/style_sheet.py` - 缺少 TIME_PICKER_DIALOG 枚举
3. ❌ `src/app/view/setting_interface.py` - 回到原始状态，需要重新实现
4. ❌ `src/app/resource/resource.qrc` - 缺少 time_picker_dialog.qss 引用

## 2. 需要修改的文件和模块

1. `src/app/components/__init__.py`
2. `src/app/common/style_sheet.py`
3. `src/app/view/setting_interface.py`
4. `src/app/resource/resource.qrc`

## 3. 修复步骤

### 3.1 修复 components/__init__.py
- 添加 `TimePickerDialog` 的导出

### 3.2 修复 common/style_sheet.py
- 添加 `TIME_PICKER_DIALOG` 枚举项

### 3.3 修复 view/setting_interface.py
- 导入 `TimePickerDialog` 组件
- 修复 `rotateTimeCard` 的显示格式（使用 strftime）
- 实现 `__onRotateTimeCardClicked` 函数
- 修复信号连接
- 清理不需要的导入

### 3.4 修复 resource.qrc
- 添加 dark 和 light 主题下的 time_picker_dialog.qss 引用

## 4. 依赖关系和考虑因素

- 保持与之前任务的实现一致
- 确保向后兼容
- 代码风格与现有代码保持一致

## 5. 风险处理

- 逐文件检查修改内容
- 验证每个修改的正确性
