# ADR-0014: 死代码分类处理策略

- **状态**: 已采纳
- **日期**: 2026-07-04

## 背景

审查发现约 3,200 行代码从未被 import 或调用，分布在 15 个文件/模块中。

这些死代码的成因分三类：
1. **v0.1.0 遗留**：被 v0.2.0 新架构替代但未删除（`default_proofread_one`、`smart_split()` 函数）
2. **未接线的完整模块**：代码完整但从未接入管线（`knowledge_split.py`、`docx_comments.py`、`sympy_tools/` 等）
3. **GUI 时代残留**：为未实现的 GUI 编写的 helper（`get_supported_file_types`）

一刀切删除会丢掉有开发投入的模块，全保留会积累维护负担。

## 决策

采用三级分类策略：

### 1. 确定删除（无保留价值）

| 代码 | 理由 |
|------|------|
| `default_proofread_one()` | 被 `proofread_with_middleware` 完全替代，保留造成"哪条是主路径"的混乱 |
| `smart_split()` 便利函数 | 被 `DeepSplitStrategy` 类替代 |
| `get_supported_file_types/extensions` | GUI 时代残留，无消费者 |
| `free_proofread.py` | 临时原型，功能已被 `proofread` 命令覆盖 |

删除原则：对象已被新实现完整替代，且新实现是唯一的执行路径。

### 2. 标记 planned v0.3.0+（完整模块，将来接入）

| 代码 | 行数 | 接入方向 |
|------|:---:|------|
| `chinese_classics_tools.py` | 1,064 | 文言文校对工具链 |
| `sympy_tools/` | ~500 | 数学公式校对工具 |
| `docx_comments.py` | 347 | Word 批注校对 |
| `review_latex.py` | 181 | 批注评审 PDF 排版 |
| `shidianguji_playwright.py` | 184 | 随 chinese_classics_tools |
| `idml_extractor.py` | ~50 | IDML 格式支持 |

保留原则：代码完整且有明确的业务场景，接入工作量可控。

### 3. 理念吸收后删除（核心概念保留，原模块移除）

| 代码 | 处理方式 |
|------|----------|
| `knowledge_split.py`（577行） | "用户自定义分割信号"概念吸收进 `outline_extractor`（F34 `extra_signals`），原文件删除 |
| `decor_utils.py`（47行） | "转换时过滤图片"概念升级为 `--strip-images` CLI 选项（F32），原文件删除 |
| `split_post_utils.py`（51行） | "拆分后跳过板块"概念升级为 `skip_sections` 配置（F33），原文件删除 |

保留原则：核心思想有价值但实现太窄（硬编码垂直领域规则），需要通用化后重新接入。

## 后果

**正面**:
- 代码库减重约 700 行立即删除的遗留代码
- ~2,200 行标记为 planned 的模块有明确的 v0.3.0+ 开发路线
- ~250 行硬编码逻辑被通用配置替代

**负面**:
- planned 模块目前仍是死代码，在真正接入前不产生价值
- `knowledge_split.py` 删除后，如果 `extra_signals` 无法覆盖其全部场景，需要重新实现
