# 内置弹窗工具库（builtin.lib.dialog）

提供「在程序本体之外弹出独立窗口」的能力与运行期扩展接口，查看器插件用它显示自己的页面。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：libraries 声明 plugin.py；provides 声明扩展接口 dialog |
| plugin.py | 插件类 DialogPlugin：setup() 里把 DialogApi 登记成 dialog 接口 |
| dialog_host.py | 弹窗外壳 PopupWindow 与扩展接口 DialogApi 的实现（由 plugin.py 转发出去） |

## 贡献

- 扩展接口 provides: dialog —— 其他插件用 ctx.require("dialog") 取到 DialogApi。
- 库模块固定是 plugin.py（libraries 里的 dialog -> plugin.py，转发 dialog_host.py 的实现）：
  - 常量 EXTENSION_NAME = "dialog"
  - DialogApi.open_page(title, content_factory, meta="", buttons=(), width=980, height=700) -> PopupWindow
    （content_factory(parent) 返回内容控件；buttons 是 [(按钮文字, 回调)]）
  - DialogApi.windows() -> tuple[PopupWindow, ...]；DialogApi.close_all() -> int
  - PopupWindow：标题栏 + 内容区，Esc 或标题栏关闭按钮关闭，WA_DeleteOnClose（关闭即销毁）；
    content_factory 抛异常时退化成一行「页面无法显示：…」，不会影响主界面。

## 依赖

无。

## 说明

弹窗没有父控件，因此与主窗口相互独立；程序关闭时会调用 DialogApi.close_all() 收尾。
内置 7 个查看器插件的 data/viewer.json 里都写 host: "dialog"，基类据此 require 本插件：
禁用本插件后，查看器会提示缺少弹窗工具库。
