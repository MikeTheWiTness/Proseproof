# ADR-0017: 专用文件编辑工具链 —— 替代 Bash 格式修正

- **状态**: 已采纳
- **日期**: 2026-07-04

## 背景

v0.2.0 的校对流程中，LLM 对文件的唯一操作路径是 `_bash_format_fix()`（`format_enforcement.py:68`）——当格式不合规时，让 LLM 通过 `BashTool` / `FileReadTool` / `FileWriteTool` 直接编辑 `_校对报告.md`。这套工具存在以下问题：

1. **粒度太粗**：`FileWriteTool` 是全量覆写，LLM 必须重写整个文件来修一个格式问题，容易引入新错误
2. **效率低**：Bash 操作对 LLM 不友好——需要先 `read` → 思考 → `write` → `read` 验证，3 轮工具调用做一件事
3. **无精确编辑能力**：缺 `replace_text` 这类精确编辑工具，LLM 无法"改这一行"，只能"重写整个文件"
4. **范围窄**：三个工具仅用于格式修正路径，未进入校对主流程

本 Agent 的工作模式证明了"精确文件编辑工具"的价值——通过 `Edit` 工具做字符串精确替换，通过 `Read` 工具做片段读取，通过 `Write` 工具做新建文件。

## 决策

### 1. 新增三种工具

| 工具 | 功能 | 签名 |
|------|------|------|
| `ReadTool` | 读取文件指定行范围或全文 | `read(file_path, offset=0, limit=None) -> str` |
| `WriteTool` | 写入/覆写文件 | `write(file_path, content) -> bool` |
| `EditTool` | 精确字符串替换 | `edit(file_path, old_string, new_string, replace_all=False) -> int` |

三者的关系：
- `ReadTool` 是唯读入口，LLM 用它了解文件当前内容
- `EditTool` 是主力修改工具，LLM 用它做精确行级/块级替换
- `WriteTool` 仅用于创建全新文件或在 `EditTool` 多次失败后的兜底覆写

### 2. 安全约束

**禁止修改原文文件**。三个工具通过白名单机制强制此约束：

- 默认白名单只包含 `_校对报告.md`、`_校对数据.json`、`_outline.json`、`_review_report.json`——即所有以 `_` 开头的中间产物文件
- 原文文件（`frag_NNN.md`、`frag_NNN_clean.md`）**显式拒绝**，任何写入/编辑操作返回错误
- 用户可通过 CLI 参数 `--allow-edit-pattern` 扩展白名单

### 3. 部署位置

两处使用：

**A) 格式修正路径（替代 `_bash_format_fix`）**

```
_enforce_format 不合规
  → ReadTool 读取 _校对报告.md
  → EditTool 修正格式结构
  → ReadTool 验证
```

三轮变两轮（读 → 改 → 验证，不需要 write），且 `EditTool` 只改需要改的部分。

**B) ReAct 工具循环（`--react` 模式）**

将三个工具注入 ReAct 工具列表，LLM 在循环中直接操作中间产物文件。

```python
def build_tools(self):
    from proseproof.shared.file_tools import ReadTool, WriteTool, EditTool
    return [ReadTool(), WriteTool(), EditTool(self.profile_dir)]
```

### 4. 迁移路径

| 阶段 | 操作 |
|------|------|
| 1 | 新建 `shared/file_tools.py`，实现 `ReadTool` / `WriteTool` / `EditTool`（基于 langchain `BaseTool`） |
| 2 | 重写 `_bash_format_fix` 为新实现（用 `EditTool` 替代 bash 路径），旧实现保留为 fallback |
| 3 | 将三个工具注册到 `generic` profile 的 ReAct 工具列表 |
| 4 | 旧 `bash_tool.py` 中的 `BashTool` / `FileReadTool` / `FileWriteTool` 标记为 deprecated |

## 后果

**正面**:
- 格式修正从"3 轮 LLM 调用"减少到"单次多工具调用"，效率提升显著
- `EditTool` 的精确替换语义比 bash 的全文覆写更可靠，降低 LLM 修改越界风险
- 原文文件保护从"约定"变为"代码强制"，消除误操作可能
- 与现有 ReAct 工具循环完美对接

**负面**:
- `EditTool` 需要 `old_string` 精确匹配文件内容才能生效——LLM 必须准确引用要替换的原文。如果 LLM 的引文与文件实际内容有细微差异（空格、换行），操作会失败
- 三个新工具的维护负担。但这是核心流程工具，值得投入
- 旧 `bash_tool.py` 的调用方需要迁移
