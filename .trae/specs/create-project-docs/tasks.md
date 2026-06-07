# Tasks

- [x] Task 1: 创建项目文档 `docx/项目文档.md`
  - [x] SubTask 1.1: 编写文档头部（项目概述、版本信息、技术栈）
  - [x] SubTask 1.2: 编写入口模块章节（demo.py 启动流程）
  - [x] SubTask 1.3: 编写公共模块章节（config、signal_bus、icon、style_sheet、translator、db、init、util）
  - [x] SubTask 1.4: 编写组件模块章节（link_card、sample_card、time_picker_dialog）
  - [x] SubTask 1.5: 编写数据模型章节（Base、Data、DataBase、Tag、User）
  - [x] SubTask 1.6: 编写视图模块章节（main_window、home_interface、setting_interface）
  - [x] SubTask 1.7: 编写资源模块章节（图片、样式、国际化）
  - [x] SubTask 1.8: 编写配置系统章节（config.json 配置项说明）

- [x] Task 2: 创建架构文档 `docx/架构文档.md`
  - [x] SubTask 2.1: 编写架构总览（整体架构图 mermaid、分层说明）
  - [x] SubTask 2.2: 编写目录结构说明（完整目录树及各目录职责）
  - [x] SubTask 2.3: 编写模块依赖关系（模块间依赖图 mermaid）
  - [x] SubTask 2.4: 编写各文件功能说明（每个源文件的职责和关键接口）
  - [x] SubTask 2.5: 编写数据流说明（数据从入口到各模块的流转路径）
  - [x] SubTask 2.6: 编写扩展指南（新增模块/视图/组件的步骤说明）

- [x] Task 3: 创建文档更新 SKILL 文件
  - [x] SubTask 3.1: 使用 skill-creator 创建 SKILL，定义文档更新规则和流程
  - [x] SubTask 3.2: SKILL 中包含项目文档更新指引（新增/删除/修改模块章节的规则）
  - [x] SubTask 3.3: SKILL 中包含架构文档更新指引（新增/删除/修改架构信息的规则）

# Task Dependencies
- [Task 2] depends on [Task 1] (架构文档需参考项目文档的模块划分)
- [Task 3] depends on [Task 1, Task 2] (SKILL 需基于两份文档的结构来定义更新规则)
