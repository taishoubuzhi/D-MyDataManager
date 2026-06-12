# Data模型增强 & 脚本目录整理计划

## 概述

4项改动：Data模型新增属性、size动态单位显示、初始化脚本重命名、测试脚本迁移。

---

## 当前状态分析

### Data模型现状
- 字段：id, name, type, keywords, tag, size(Integer, 无默认值), is_hidden, content, user_id, database_id
- 缺少时间戳字段和封面路径
- size直接显示原始数字，无单位转换

### 脚本现状
- `src/app/common/init/init_data.py`：初始化逻辑，被 `init_db.py` 通过相对导入调用
- `tests/test_log_config.py`：日志配置测试脚本，独立运行
- `scripts/` 目录已存在但为空
- `tests/` 目录仅有 `test_log_config.py` 一个文件

### UI组件现状
- `DataCard`：48x48 FluentIcon类型图标 + 文本信息
- `DataListCard`：24x24 FluentIcon类型图标 + 紧凑文本
- 两者均无封面图逻辑，size显示为原始数字

---

## 改动详情

### 改动1：Data模型新增属性

**文件**: `src/app/model/Data.py`

新增3个字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `import_time` | `DateTime` | `func.now()` | 导入时间，记录创建时自动填充 |
| `update_time` | `DateTime` | `func.now()` | 修改时间，ORM更新时自动刷新（onupdate=func.now()） |
| `cover_path` | `String(256)` | `None` | 封面路径，默认为NULL，为空时使用按类型区分的默认封面 |

同时为 `size` 字段添加默认值 `0`。

需要新增的导入：`DateTime`, `func` from sqlalchemy。

**默认封面逻辑**：每种DataType对应一个默认封面（在UI层实现，非模型层）。

### 改动2：size动态单位显示

**文件**: `src/app/model/Data.py`

新增模块级函数 `format_size(size_bytes)`：

```python
def format_size(size_bytes):
    """将字节数格式化为可读的大小字符串"""
    if size_bytes is None or size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    size = float(size_bytes)
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"
```

**影响文件**（3处size显示需更新）：

1. `src/app/components/data_tab_page.py` - 表格视图"大小"列
   - `_COLUMNS` 中 size 的 getter：`lambda d: format_size(d.size or 0)`

2. `src/app/components/data_card.py` - DataCard卡片模式
   - 第185-186行：`size_str = format_size(item.size or 0)` + `f"大小: {size_str}"`

3. `src/app/components/data_card.py` - DataListCard条目模式
   - 第260行：`size_str = format_size(item.size or 0)` + 显示

### 改动3：初始化脚本重命名

**策略**：将 `init_data.py` 重命名为 `init_data_script.py`，标注其为脚本文件，保持在原目录。

**操作**:
1. 重命名 `src/app/common/init/init_data.py` → `src/app/common/init/init_data_script.py`
2. 更新 `src/app/common/init/init_db.py` 中的导入：
   - `from .init_data import init_data, db_exists` → `from .init_data_script import init_data, db_exists`

**不修改**: `init_data_script.py` 内部代码逻辑不变，仅文件名变更。

### 改动4：测试脚本迁移

**操作**:
1. 将 `tests/test_log_config.py` 移动到 `scripts/test_log_config.py`
2. 脚本内的路径计算 `os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', ...)` 无需修改（`tests/` 和 `scripts/` 同级，相对路径一致）
3. 删除空的 `tests/` 目录

---

## 封面显示逻辑（UI层）

**文件**: `src/app/components/data_card.py`

### DataCard 卡片模式
- 当前：48x48 `IconWidget` 显示类型图标
- 改后：
  - 若 `cover_path` 非None且文件存在 → 显示封面图（`QLabel` + `QPixmap`，保持48x48或适当放大）
  - 若 `cover_path` 为None → 显示按类型区分的默认封面（类型图标 + 类型对应背景色的圆角矩形）

### DataListCard 条目模式
- 保持24x24图标不变（空间太小不适合封面图）

### 默认封面配色方案

| DataType | 背景色(亮/暗) | 图标 |
|----------|--------------|------|
| IMAGE | #E8F5E9 / #1B3A1D | PHOTO |
| VIDEO | #E3F2FD / #1A2940 | VIDEO |
| AUDIO | #FFF3E0 / #3E2723 | MUSIC |
| DOC/DOCX | #F3E5F5 / #2A1B3D | DOCUMENT |
| EXCEL | #E8F5E9 / #1B3A1D | DOCUMENT |
| PPT | #FBE9E7 / #3E1B1B | DOCUMENT |
| TEXT | #F5F5F5 / #2C2C2C | EDIT |
| UNKNOWN | #ECEFF1 / #263238 | HELP |

实现方式：创建 `_CoverWidget(QWidget)` 组件，根据类型绘制圆角背景 + 居中图标。

---

## 假设与决策

1. **import_time/update_time 使用服务器时间**：`func.now()` 在SQLite中为 `CURRENT_TIMESTAMP`，无需Python端计算
2. **size默认值为0**：代表0字节，即"数据最小单位"
3. **cover_path默认为None**：数据库中存储为NULL，UI层判断None时使用默认封面
4. **数据库迁移**：新增字段后，已有数据库需通过设置页"强制初始化数据库"重建，或手动ALTER TABLE
5. **scripts/不作为Python包**：无需 `__init__.py`，脚本独立运行
6. **DataListCard不显示封面**：紧凑布局空间不足，仅DataCard显示封面

---

## 验证步骤

1. 启动应用，确认 `init_data_script.py` 重命名后导入正常（无 ImportError）
2. 运行 `python scripts/test_log_config.py` 验证测试脚本迁移后正常
3. 启动应用，验证DataCard中size显示为动态单位（如"1.5 KB"）
4. 启动应用，验证DataCard中默认封面按类型显示不同背景色
5. 设置页强制初始化数据库后，验证新字段（import_time, update_time, cover_path）存在
6. 验证表格视图中size列显示动态单位
