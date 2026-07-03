# ADR-0013: `defaults.py` 按职责拆分

- **状态**: 已采纳
- **日期**: 2026-07-04

## 背景

`proseproof/core/defaults.py` 是项目中最大的源文件（795 行），承担了四种不同职责：

1. **文本清洗** — `fix_latex_escapes`、`comprehensive_clean`、`fix_floating_images`、`normalize_option_spacing`、`fix_pandoc_comment_anomaly`、`fix_tilde_in_math`、`fix_tilde_in_text`、`convert_italics_to_math`、`convert_display_to_inline`、`post_process_md_zw`（~200 行）
2. **文档转换** — `default_convert_file_to_md`、`get_supported_file_types`、`get_supported_extensions`（~80 行）
3. **文档拆分** — `default_split_document`、`default_split_lecture`、`default_generate_knowledge`、`find_answer_section`、`detect_answer_mode`、`parse_end_answers`（~200 行）
4. **校对主流程** — `default_proofread_one`、`_strip_search_from_prompt`、`_format_tool_calls_summary`、`_format_usage_summary`、`default_collect_paper_dirs`（~350 行）

随着 v0.2.0 引入 `proofread_with_middleware()`（ADR-0011），`defaults.py` 的校对部分将进一步膨胀。不拆分将导致：
- 单文件难以导航和审查
- 不相关的改动产生虚假冲突
- 测试文件无法与实现模块一一对应

## 决策

按职责拆分为四个独立模块：

```
proseproof/core/
├── defaults.py          →  删除（重组到以下四个模块）
├── text_cleaning.py     ←  文本清洗函数（~200 行）
├── convert.py           ←  转换函数（~80 行）
├── split_utils.py       ←  拆分函数（~200 行）
└── proofread_utils.py   ←  校对主流程（~350 行）
```

### 1. `text_cleaning.py`

```python
# 搬迁的函数
fix_latex_escapes(md_file)
comprehensive_clean(md_content)
clean_md_file(md_file)
fix_floating_images(md_file)
normalize_option_spacing(md_file)
fix_pandoc_comment_anomaly(content)
fix_tilde_in_math(content)
fix_tilde_in_text(content)
convert_italics_to_math(content)
convert_display_to_inline(content)
post_process_md_zw(md_path)
```

职责：Markdown/LaTeX 文本清洗，不涉及 API 调用或文件 I/O 编排。

### 2. `convert.py`

```python
default_convert_file_to_md(file_path, output_md, img_dir, use_mathjax)
get_supported_file_types()
get_supported_extensions()
```

职责：Pandoc 转换编排 + 类型注册。

### 3. `split_utils.py`

```python
default_split_document(md_file, output_root, base_name, config)
default_split_lecture(md_file, output_root, base_name, do_clean, config)
default_generate_knowledge(cleaned_md, output_root, base_name, config)
find_answer_section(lines)
detect_answer_mode(lines)
parse_end_answers(answer_lines)
```

职责：试卷/讲义拆分的规则逻辑，与 v0.2.0 的 Strategy 分割模式并列存在。

### 4. `proofread_utils.py`

```python
default_proofread_one(api_url, api_key, model, q_dir, q_name, ...)
proofread_with_middleware(ctx, chain, llm_callable)  # 新增（ADR-0011）
_strip_search_from_prompt(prompt)
_format_tool_calls_summary(tool_calls)
_format_usage_summary(usage)
default_collect_paper_dirs(base_path)
```

职责：LLM 校对执行的通用实现，供 `BaseProfile` 和 `DefaultProofreadStrategy` 消费。

### 迁移策略

**阶段 1**：创建新模块，搬迁函数，`defaults.py` 改为 re-export（`from .text_cleaning import *` 等）。

**阶段 2**：更新所有直接 import 的调用方，指向新模块。

**阶段 3**：删除 `defaults.py` 中的 re-export，移除文件。

每个阶段独立提交，确保每一步都可编译可测试。

此拆分在 Slice #12（CLI 集成）之后执行，由独立 ADR 驱动。

## 后果

**正面**:
- 每个模块行数 ≤ 350 行，单一职责清晰
- 测试文件与实现模块一一对应（`test_text_cleaning.py`、`test_proofread_utils.py` 等）
- 新增校对能力（`proofread_with_middleware`）有明确的归属模块
- 新人可以按模块理解代码，不需要一次消化 795 行

**负面**:
- 拆分过程涉及大量 import 路径变更，短期改动范围大
- 调用方需要从 `from proseproof.core.defaults import X` 改为 `from proseproof.core.proofread_utils import X`
- `base_profile.py`、`cli.py` 等高频模块需要更新 import，增加合并冲突风险
- 需要在 CHANGELOG 或迁移指南中记录路径变更
