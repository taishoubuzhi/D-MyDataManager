# 修复日志启动检查和路径问题

## 问题分析

### 问题1：启动时仅 Count 模式执行检查
当前 `init_log.py` 第64-66行只在 `rotate_mode == "Count"` 时调用 `_cleanup_old_logs()`。用户要求所有非 None 轮转模式都应在启动时执行检查。

### 问题2：自动创建 src/logs 目录
`config.py` 第92行默认路径计算有误：
```python
os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```
- `__file__` = `.../D-MyDataManager/src/app/common/config.py`
- 3层 dirname = `.../D-MyDataManager/src` ← 错误，差一层
- 应为4层 dirname = `.../D-MyDataManager` ← 项目根目录

虽然 config.json 中配置了正确的绝对路径，但默认值错误会导致：当 config.json 加载异常或 FolderValidator 校验失败时，回退到错误的 `src/logs` 路径。

## 修改方案

### 文件1: `src/app/common/config.py` (第91-93行)
- 将 `file_path` 默认值从3层 `os.path.dirname()` 改为4层，正确指向项目根目录的 `logs`

### 文件2: `src/app/common/init/init_log.py` (第64-66行)
- 将启动时清理检查从 `rotate_mode == "Count"` 条件改为 `rotate_mode != "None"`
- Time 模式也使用 `rotate_count` 作为最大文件数限制进行清理

## 验证步骤
1. 删除 `src/logs` 目录（如存在）
2. 启动应用，确认不再创建 `src/logs`
3. 确认日志输出到 `D-MyDataManager/logs/`
4. 设置 Rotate-Mode 为 Count 或 Time，在 logs 目录放入超过 rotate_count 数量的文件，重启确认旧文件被清理
