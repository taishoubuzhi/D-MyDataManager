"""自检套件（REWRITE.md §7）：按分层跑公开契约检查。

分层：
- `data`：库结构与内容仓库（业务表、FTS5 虚表、同步触发器、blob 布局、配置落盘）
- `services`：服务层行为（导入 / 导出 / 存档 / 用户 / 统计）
- `pages`：页面结构（offscreen 建页面，只断言结构与权限装配）
- `flows`：端到端流程（导入 → 筛选 → 移动 → 存档 → 还原）

每个检查都跑在自己的临时目录与数据库上，只调用公开契约，不读写真实的
`.resources/` 与 `config/`。末行统一输出 `RESULT failures=N`。
"""

from .cli import main
from .harness import LAYERS, REGISTRY, Case, Check, check, ensure_app, load, run, select

__all__ = [
    "Case",
    "Check",
    "LAYERS",
    "REGISTRY",
    "check",
    "ensure_app",
    "load",
    "main",
    "run",
    "select",
]
