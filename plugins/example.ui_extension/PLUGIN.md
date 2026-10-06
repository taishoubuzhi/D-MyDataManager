# 界面扩展示例（example.ui_extension）

演示插件如何在**不改程序源码**的前提下往界面上加东西：概览卡片、工具栏按钮、
右键菜单项、详情行、导入过滤器与设置卡片，并订阅程序广播的事件。

## 目录

| 文件 | 作用 |
| --- | --- |
| `plugin.json` | 清单：id、名称、入口、`data` 声明的数据文件 |
| `plugin.py` | 插件类 `UiExtensionSamplePlugin`，只调用 `app.sdk` |
| `.data/info.json` | 插件自己的数据（设置卡片的说明文字与用到的扩展点清单） |

## 用到的扩展点

| 扩展点 | 值 | 效果 |
| --- | --- | --- |
| `app.ui.home.kpi` | `{"title", "value", "sub", "icon"}` | 概览页多一张卡片，数值由回调实时计算 |
| `app.ui.manage.toolbar` | `{"text", "callback", "icon", "tip"}` | 数据管理页工具栏多一个按钮 |
| `app.ui.manage.item_menu` | `{"text", "callback", "icon"}` | 数据行右键菜单多一项，回调收到该行数据 |
| `app.ui.detail.panel` | `{"title", "lines"}` | 详情弹窗多一段文本，`lines(item)` 返回行列表 |
| `app.ui.import.filter` | `{"name", "accept"}` | `accept(path)` 返回 False 的文件不导入 |
| `app.ui.settings.card` | `{"title", "factory"}` | 设置页多一张卡片，`factory(parent)` 返回控件 |

## 订阅的事件

`item.imported`（导入成功后程序广播，示例用它给概览卡片计数）、`plugin.enabled` /
`plugin.disabled`（启用或禁用插件时广播）、`theme.changed`（切换主题时广播）。

```python
ctx.on(Events.ITEM_IMPORTED, self._on_imported)   # 处理函数用 **payload 收参数
```

## 卸载

删掉整个 `plugins/example.ui_extension/` 目录，或在插件页把它设为「禁用」即可；
插件卸载时程序会自动撤销它登记的全部贡献与事件订阅。
