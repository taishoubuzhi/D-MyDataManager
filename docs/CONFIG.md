# 配置项说明（`.configs/config.json`）

> 配置文件由 `QFluentWidgets` 的 `QConfig` 读写，定义在 `src/app/core/config.py` 的 `Config` 类里，模块级单例 `config`。
> **配置项不进清单机制**（用户决定 D1）：清单管「固定、可被外部替换」的数据，配置项是「用户在界面里改」的偏好，两者边界见 `docs/MANIFEST_PROTOCOL.md` §1。
> 程序启动时 `load_config()` 依次做：迁移旧目录 → 把旧版布尔值的「简化显示」一次性换算成挡位 → 读配置文件 → 放行资源目录 → 迁移旧资源路径 → 应用资源根 → 建目录。

改配置文件后重启程序生效；标「立即生效」的项在设置页改完就该变，标「重启」的项需要重启。

## 1. MainWindow（窗口与外观）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 缩放比例 | `DpiScale` | `Auto` | `Auto`/`100%`/`125%`/`150%`/`200%` | 重启生效 |
| 语言 | `Language` | `zh_CN` | `zh_CN`/`en_US`/`Auto` | 重启生效 |
| 云母材质 | `MicaEnabled` | `true` | 布尔 | Windows 11 的窗口材质 |
| 主题 | `Theme` | `auto` | `light`/`dark`/`auto` | 立即生效 |

## 2. User（当前用户）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 当前用户 id | `Current-Id` | `0` | 0 – 1 000 000 000 | `0` 表示默认用户 |

## 3. Layout（页面布局与显示偏好）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 每页条数 | `Page-Size` | `50` | 10 – 1000 | 列表分页大小 |
| 显示分类面板 | `Show-Category-Panel` | `true` | 布尔 | |
| 显示筛选面板 | `Show-Filter-Panel` | `true` | 布尔 | |
| 展开分类 | `Expand-Categories` | `false` | 布尔 | |
| 展开的筛选项 | `Expanded-Filters` | `[]` | 字符串列表 | 页面打开时恢复上次展开的项 |
| 简化显示 | `Simple-Display` | `default` | `none`/`default`/`full` | 三挡位：不简化 / 只简化不会混淆的图标 / 完全简化。旧版布尔值（`true`/`false`）在启动时一次性换算为 `full`/`none` |
| 提示延迟 | `Tooltip-Delay` | `2000` | 0 – 10 000 毫秒 | `0` 表示立刻弹出 |
| 双击动作 | `Double-Click-Action` | `viewer` | `viewer`/`editor` | 数据管理页双击条目时打开查看器还是编辑器插件 |

## 4. Log（日志）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 级别 | `Level` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | |
| 输出到控制台 | `Output-Console` | `true` | 布尔 | |
| 异步写入 | `Enqueue` | `true` | 布尔 | loguru 的 `enqueue` |
| 记录回溯 | `Backtrace` | `true` | 布尔 | |
| 记录诊断 | `Diagnose` | `true` | 布尔 | |
| 格式化为 JSON | `Format-To-JSON` | `false` | 布尔 | |
| 格式 | `Log-Format` | 见下 | loguru 格式串 | 默认 `{time:YYYY-MM-DD HH:mm:ss.SSS} \| {level: <8} \| {extra[source]} \| {function}:{line} \| {message}` |
| 切分方式 | `Mode` | `daily` | `single`/`session`/`daily`/`size` | 重启生效；单文件 / 每次启动 / 每天 / 按大小 |
| 保留文件数 | `Keep-Files` | `10` | 1 – 200 | `size` 模式用 |
| 保留天数 | `Keep-Days` | `14` | 1 – 3650 | |
| 单文件上限 | `Max-File-Size-MB` | `20` | 1 – 1024 MB | `size` 模式用 |
| 总量上限 | `Max-Total-Size-MB` | `200` | 1 – 10240 MB | |

## 5. Database（数据库）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 连接串 | `URL` | 空 | SQLAlchemy URL | 空表示用 `sqlite:///<资源目录>/data.db` |
| 打印 SQL | `Echo` | `false` | 布尔 | 调试用 |

## 6. Storage（存储与资源）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 资源文件夹 | `Resource-Path` | 空 | 路径 | 空表示用默认的 `.resources/`；存的是**资源文件夹本身**（用户在设置页选的是容器目录，程序会把 `.resources` 接在后面）。改这个要用设置页的「迁移资源文件夹」：目标位置已有 `.resources` 时抛 `ResourceRootExists`（`FileExistsError` 子类）、界面问用户要不要删掉重搬后带 `replace=True` 再来一次 |
| 资源保护 | `Resource-Protected` | `false` | 布尔 | 静态模型：运行期整场放行、退出时锁定（`.resources` 与库文件夹） |
| 隐藏保护 | `Hidden-Protected` | `false` | 布尔 | 隐藏文件夹 + 清理 Windows 的「最近使用的文件」记录 |
| 导出目录 | `Export-Path` | 空 | 路径 | 空表示用默认导出目录 |
| 封面尺寸 | `Cover-Size` | `256` | 64 – 1024 | 生成的封面图边长 |

## 7. Import（导入）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 按时间命名 | `Name-By-Time` | `false` | 布尔 | |
| 重名策略 | `Duplicate-Policy` | `rename` | `skip`/`rename`/`overwrite` | |
| 导入后存档 | `Archive-On-Import` | `true` | 布尔 | |

## 8. Archive（存档与清理）

| 中文名 | 键 | 默认 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| 保留版本数 | `Keep-Versions` | `10` | 1 – 200 | `pruneMode=count` 用 |
| 清理方式 | `Prune-Mode` | `count` | `count`/`size`/`age`/`none` | 按版本数 / 总大小 / 天数 / 不清理 |
| 保留总大小 | `Keep-Size-MB` | `2048` | 64 – 1 048 576 MB | `pruneMode=size` 用 |
| 保留天数 | `Keep-Days` | `30` | 1 – 3650 | `pruneMode=age` 用 |
| 回档前确认 | `Restore-Preview` | `true` | 布尔 | 关闭后确认点只剩执行本身 |
| 自动清理 | `Auto-Cleanup` | `true` | 布尔 | 启动后延迟一次、建档后、删档后清理无引用内容与残留文件 |

## 9. 与清单机制的边界

* 上表全部是**配置项**，由用户在设置页改，存进 `.configs/config.json`，**不**出现在清单登记表里。
* 清单机制（`app.sdk.manifest`）管的是 `src/app/core/runtime/runtime.json`、`src/app/core/plugins/plugins.json` 以及待迁移的 `plugins/*/data/*.json`：它们可以有「变更 / 对照 / 备份 / 重置」，也可以被插件替换，但不对应用户偏好。
* 需要重启才生效的项（`DpiScale`、`Language`、`Mode`）在设置页会显示重启提示。
