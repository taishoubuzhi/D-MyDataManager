---
name: "project-doc-updater"
description: "Updates project documentation and architecture docs when features are added, removed, or modified. Invoke when project functionality changes or user asks to sync docs with code."
---

# Project Doc Updater

When the project undergoes functional changes (addition, deletion, modification), this skill guides the systematic update of two core documents to keep them in sync with the codebase.

## Target Documents

| Document | Path | Purpose |
|----------|------|---------|
| 项目文档 | `docx/项目文档.md` | Design ideas, features, configuration (modular chapters) |
| 架构文档 | `docx/架构文档.md` | Architecture overview, module dependencies, file functions, data flow, extension guide |

## Trigger Conditions

Invoke this skill when ANY of the following occurs:

1. **New module/file added** to the project
2. **Existing module/file removed** from the project
3. **Module functionality significantly changed** (new class, new signal, new config item, interface change)
4. **User explicitly requests** documentation sync

## Update Process

### Step 1: Identify Change Type

Determine the change type from the list below:

| Change Type | Description | Examples |
|-------------|-------------|---------|
| `ADD` | New module, file, class, or feature | New view, new component, new model |
| `REMOVE` | Module, file, class, or feature deleted | Removed component, deprecated feature |
| `MODIFY` | Existing module functionality changed | New signal, new config item, interface refactor |

### Step 2: Analyze Impact Scope

For each change, determine which document sections are affected:

**项目文档.md affected sections:**
- Module chapter corresponding to the changed file
- Configuration system chapter (if config items changed)
- Entry module chapter (if startup flow changed)

**架构文档.md affected sections:**
- Architecture overview diagram (if layer structure changed)
- Directory structure (if files added/removed)
- Module dependency diagram (if import relationships changed)
- File function table (if file responsibilities changed)
- Data flow diagram (if data paths changed)
- Extension guide (if extension patterns changed)

### Step 3: Update 项目文档.md

#### For `ADD` changes:
1. Read the current `docx/项目文档.md`
2. Locate the appropriate chapter for the new module (by module category: common/components/model/view/resource)
3. Add a new `###` subsection following the existing format:
   ```markdown
   ### X.N 新模块名 (new_module.py)

   **设计思路**: [模块的设计目的和思路]

   **功能描述**: [模块提供的功能]

   **关键接口**:

   | 接口 | 说明 |
   |------|------|
   | ClassName | 类描述 |
   | method_name | 方法描述 |
   ```
4. If new config items were added, update the 配置系统 chapter
5. Add an entry to the 变更记录 table at the end

#### For `REMOVE` changes:
1. Read the current `docx/项目文档.md`
2. Locate the chapter for the removed module
3. Mark the section as deprecated or remove it:
   ```markdown
   ### X.N 已移除模块名 (removed_module.py) ~~[已移除]~~

   > **移除说明**: 该模块已于 [日期] 移除，原因：[移除原因]
   ```
4. Update the 配置系统 chapter if config items were removed
5. Add an entry to the 变更记录 table

#### For `MODIFY` changes:
1. Read the current `docx/项目文档.md`
2. Locate the chapter for the modified module
3. Update the relevant content (功能描述, 关键接口 table, etc.)
4. If new config items were added/changed, update the 配置系统 chapter
5. Add an entry to the 变更记录 table

### Step 4: Update 架构文档.md

#### For `ADD` changes:
1. Read the current `docx/架构文档.md`
2. Update the architecture overview mermaid diagram if a new layer or major component was added
3. Add the new file/directory to the 目录结构说明 section
4. Update the module dependency mermaid diagram with new dependency edges
5. Add the new file to the 各文件功能说明 table
6. Update the data flow diagram if new data paths exist
7. Update the 扩展指南 if new extension patterns are needed
8. Add an entry to the 变更记录 table

#### For `REMOVE` changes:
1. Read the current `docx/架构文档.md`
2. Update the architecture overview mermaid diagram to remove the component
3. Remove the file/directory from the 目录结构说明 section
4. Update the module dependency mermaid diagram to remove dependency edges
5. Remove the file from the 各文件功能说明 table
6. Update the data flow diagram if data paths changed
7. Add an entry to the 变更记录 table

#### For `MODIFY` changes:
1. Read the current `docx/架构文档.md`
2. Update the module dependency diagram if import relationships changed
3. Update the 各文件功能说明 table if file responsibilities changed
4. Update the data flow diagram if data paths changed
5. Add an entry to the 变更记录 table

### Step 5: Validate

After updating both documents:
1. Verify all mermaid diagrams are syntactically correct and renderable
2. Verify all cross-references between documents are consistent
3. Verify the 变更记录 tables in both documents have matching entries for this change
4. Verify no orphaned references exist (references to removed modules)

## Module Category Mapping

Use this mapping to determine which chapter a file belongs to:

| File Path Pattern | 项目文档 Chapter | 架构文档 Layer |
|-------------------|-----------------|---------------|
| `src/demo.py` | 入口模块 | 入口层 |
| `src/app/common/config.py` | 公共模块 → 配置管理 | 公共层 |
| `src/app/common/signal_bus.py` | 公共模块 → 信号总线 | 公共层 |
| `src/app/common/icon.py` | 公共模块 → 图标管理 | 公共层 |
| `src/app/common/style_sheet.py` | 公共模块 → 样式管理 | 公共层 |
| `src/app/common/translator.py` | 公共模块 → 翻译管理 | 公共层 |
| `src/app/common/db.py` | 公共模块 → 数据库工具 | 公共层 |
| `src/app/common/init/*.py` | 公共模块 → 初始化模块 | 公共层 |
| `src/app/common/util/*.py` | 公共模块 → 工具模块 | 公共层 |
| `src/app/components/*.py` | 组件模块 | 组件层 |
| `src/app/model/*.py` | 数据模型 | 数据层 |
| `src/app/view/*.py` | 视图模块 | 视图层 |
| `src/app/resource/*` | 资源模块 | 资源层 |
| `src/app/config/*` | 配置系统 | 公共层 |

## Important Notes

- Always read both documents fully before making changes
- Preserve existing content that is not affected by the change
- Mermaid diagrams must use `fill` with light colors and `color:#FFFFFF` for text to ensure dark theme readability
- Keep the modular chapter structure intact — do not merge or split existing chapters
- When in doubt about which sections to update, update conservatively (only clearly affected sections)
