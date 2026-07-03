# ADR-0012: 补齐核心模块测试 + 代码现代化

- **状态**: 已采纳
- **日期**: 2026-07-04

## 背景

项目审查发现以下核心模块缺少测试覆盖，构成回归风险：

| 模块 | 行数 | 风险 |
|------|------|------|
| `latex_generator.py` | 1178 | 核心排版逻辑，出错导致 PDF 损坏 |
| `api_client.py` | 652 | 核心通信层，含工具循环、退避重试、熔断器 |
| `parsing.py` | 244 | LLM 输出 → 结构化数据的桥梁 |
| `format_enforcement.py` | 182 | 格式审查 + bash 修正 |

此外存在以下代码质量问题：
- `_save_conversation_log()` 与 `_save_conversation_log_full()` 代码近重复（~60 行）
- `_is_no_issue()` 在 `defaults.py` 和 `format_enforcement.py` 中重复定义
- `pre_check.py` 括号匹配存在两个 bug：`bracket_map` 重复键、栈 pop 不验证配对类型
- `proseproof/templates/proofread_template.tex` 与 `shared/templates/` 下的完全重复
- PreCheck 检测规则硬编码，用户无法自定义
- `output/中间产物/` 有已提交的测试中间产物（违反 `.gitignore`）

## 决策

### 1. LaTeX 测试：L1 单元 + L2 集成

**L1 单元测试**（纯函数输入→输出）：
- `_extract_images()` — 图片路径提取与占位符替换
- `_escape_preserve_math()` — 数学模式保护下的字符转义
- `_process_inline_markers()` — 内联标记 `【N|原文|改为】` → LaTeX 占位符
- `_merge_split_math_blocks()` — 被标记切开的 `\left`/`\right` 配对合并
- `_apply_markers()` — 原文中插入 `\corrmark`
- `_fix_missing_chars()` — 缺失字体的字符处理
- `_format_right_entry()` — 右栏修改条目的格式

**L2 集成测试**（`build_paracol_content()` 端到端验证）：
- 给定 md 原文 + corrections 列表 → 验证 `.tex` 输出包含：
  - `\begin{paracol}{2}`
  - `\corrmark{...}{N}` / `\redcircled{N}`
  - `\correctionbox{...}`
  - `\switchcolumn`
- 验证"无问题"场景产出 `\textbf{✅ 校对无问题}`
- 验证批注评审格式产出正确的右栏结构
- 验证特殊字符（`$`, `%`, `&`, `_`, `{`, `}`）被正确转义
- 验证数学公式（`$...$`, `$$...$$`）内部不被转义破坏

**L3 编译测试**（可选）：实际 `xelatex` 编译，需要 TeX Live 环境，暂不纳入 CI。

### 2. `api_client.py` 测试：纯函数优先

优先测试无外部依赖的纯函数：
- `_classify_error()` — 异常分类（HTTPError → APITimeoutError / APIRateLimitError / APIAuthError）
- `_should_retry()` — 可重试性判断
- `_backoff_delay()` — 指数退避计算（含上限裁剪）
- `_is_empty_or_duplicate()` — 空结果/重复结果检测（含 SymPy JSON 特殊处理）
- `_compress_history()` — 对话历史压缩（移除 tool_calls 对、插入摘要）
- `_extract_usage()` / `_accumulate_usage()` — Token 用量提取与累加

Mock HTTP 层的 `call_api()` 集成测试留到后续（复杂度高，投入产出比低）。

### 3. `parsing.py` 测试

- `parse_proofread_md()` — 内联标记格式解析（`### 标记原文` + `【N|原文|改为】` + `### 修改原因`）
- `_parse_old_format()` — 旧格式兼容解析（`### 修改 N` + `- **原文**:` 列表）
- `_parse_reason_meta()` — 原因文本中的 `[type|severity]` 元数据提取
- "无问题"快速通道验证
- 边界情况：空文本、缺失摘要、标记编号与原因编号不匹配
- 批注评审格式共存（addition: `review_judgments` / `review_supplements`）

### 4. `format_enforcement.py` 测试

- `_enforce_format()` — 格式合规检查（缺少 `### 标记原文`、`### 修改原因`、编号不匹配、格式异常标记）
- `_is_no_issue()` — "无问题"判定（移至 `parsing.py` 后从那里 import）
- `enforce_and_fix()` — bash 修正成功路径（mock `_bash_format_fix`）
- `enforce_and_fix()` — bash 修正失败回退路径

### 5. 代码去重与清理

| 改动 | 说明 |
|------|------|
| `_save_conversation_log` / `_save_conversation_log_full` 合并 | 通过参数 `full: bool` 区分，消除 ~60 行重复 |
| `_is_no_issue()` 移至 `parsing.py` | `defaults.py` 和 `format_enforcement.py` 都从 `parsing.py` import |
| 删除 `proseproof/templates/proofread_template.tex` | 冗余副本，`_get_template_file()` 只查 `shared/` 下的 |
| `git rm --cached` 清理 `output/中间产物/` | 已提交的测试中间产物，违反 `.gitignore` |

### 6. PreCheck bug 修复 + 规则可插拔化

**bug 修复**：
- 删除 `bracket_map` 中重复的 `'"': '"'` 键
- 括号配对验证时增加类型匹配检查（`(` 只能配 `)`，不能配 `]`）

**规则可插拔化**：
将三类检测规则（括号/引号、连续标点、连续重复词）抽取为独立的 checker 函数，`PreCheckMiddleware.__init__` 接受 `checkers: list[Callable]` 参数。默认注册全部规则，用户可通过 `config.json` 的 `proofread.pre_check.rules` 选择性启用。

## 后果

**正面**:
- 核心模块（LaTeX、API、解析、格式）首次获得测试覆盖，降低回归风险
- LaTeX L1+L2 测试覆盖约 80% 的排版逻辑路径
- `api_client.py` 纯函数测试覆盖退避重试、异常分类等易错分支
- 代码消除约 80 行重复，减少维护负担
- PreCheck 规则可插拔化提升客制化能力

**负面**:
- LaTeX 测试需要构造大量 mock 数据（`corrections` 列表、`placeholder_map`），测试代码行数可能接近甚至超过被测代码
- `api_client.py` 的 `call_api()` 工具调用循环仍无测试（mock 复杂度高）
- 规则可插拔化增加了 PreCheck 的初始化复杂度
