# ADR-0015: outline_extractor 支持 `extra_signals` 用户自定义信号

- **状态**: 已采纳
- **日期**: 2026-07-04

## 背景

v0.2.0 的 `outline_extractor` 提取三种结构化信号：标题（`#` 开头）、编号项（`1.`/`(1)`/`一、`/`①`）、列表标记（`-`/`*`/`•`）。这三种覆盖了通用 Markdown 文档的绝大多数结构。

`knowledge_split.py`（577 行，从未被 import）实现了一个领域专用的"结构编目"步骤，其核心概念是**用户定义额外信号模式**——如教育文档中的 `【寓意】`、`【适用角度】`、`**审题：**` 等。这些信号本身不是标题/编号/列表的任何一种，但对确定分割边界至关重要。

不应为此保留一个独立的 577 行模块和一个独立的分割模式。应将其核心理念吸收进现有的 `outline_extractor`。

## 决策

在 `config.json` 的 `split.outline` 中增加 `extra_signals` 字段：

```json
"split": {
  "mode": "smart",
  "outline": {
    "max_depth": 4,
    "extra_signals": [
      "^【寓意】", "^【详解】", "^\\*\\*审题："
    ]
  }
}
```

### 行为

1. `extract_outline(content, max_depth, extra_patterns)` 已接受 `extra_patterns` 参数（字符串列表）——当前无调用方传入此参数
2. `extract_outline` 将每个 `extra_pattern` 编译为 `re.compile(pat_str)`，追加到 `numbered_patterns` 匹配列表
3. 匹配到的行创建为 `OutlineItem`，`item_type` 为 `"numbered"`（与编号项同级处理）
4. 这些条目参与 `SmartSplitStrategy` 的大纲 → LLM 边界决策流程

### 默认值

`extra_signals` 默认为空列表 `[]`。不配置 = 行为与 v0.2.0 完全一致。

### 与 knowledge_split 的关系

`knowledge_split.py` 的"信号编目"概念已被此方案吸收。原文件在 v0.3.0 中删除（ADR-0014），其提示词和 LLM 步骤逻辑被 `SmartSplitStrategy` 的现有实现完整覆盖。

## 后果

**正面**:
- 不增加新概念、新模式、新模块——只是在现有出口上开一个参数口
- 用户无需理解"knowledge split 是什么"——只需在 config 中列出自定义的边界信号
- 与 `--split-by-pattern` 逃生舱形成互补：后者是全量正则替代，前者是为 smart 模式补充信号

**负面**:
- `extra_signals` 匹配到的行被归类为 `item_type: "numbered"`，语义上不完全准确（不一定是编号项）。可接受——`SmartSplitStrategy` 的核心输入是行号边界，`item_type` 是辅助标签
- 用户需要了解正则语法才能配置，门槛略高于纯文本列表
