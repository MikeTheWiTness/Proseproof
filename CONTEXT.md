# CONTEXT.md — Proseproof 领域词汇表

本文档定义 Proseproof 项目中的核心领域概念。所有讨论、代码注释和文档均应使用
本文档定义的术语。

---

## 流水线 (Pipeline)

将输入文档转化为校对后 PDF 的完整处理链。每个阶段通过中间文件传递数据，
可独立调用或组合执行。

## 阶段 (Stage)

流水线的五个标准阶段：

- **Convert**（转换）：将 DOCX、IDML 等格式转为 Markdown
- **Split**（分割）：将 Markdown 文档切分为若干片段
- **Proofread**（校对）：对每个片段执行 AI 校对
- **Typeset**（排版）：将校对结果生成 LaTeX 源码
- **Compile**（编译）：将 LaTeX 编译为 PDF

## 策略 (Strategy)

某个阶段的可替换实现。用户通过 Profile 选择每个阶段使用哪种策略（如
Split 阶段可选 `heading`、`smart`、`deep` 等）。第三方可通过 Python 接口
提供自定义策略。

## 中间件 (Middleware)

仅存在于 Proofread 阶段内部的、可组合的校验链。中间件在 LLM 调用前后执行，
不改变校对的核心流程，仅注入附加逻辑（如预检标记、相似度校验、结果验证）。

每个中间件实现 `ProofreadMiddleware` 协议，声明 `name` 和 `phase`（`pre` 或 `post`）。

## 中间件动作 (MiddlewareAction)

中间件 `process()` 方法的返回值语义，由 Pipeline 统一处理：

- **CONTINUE**：正常流转到下一个中间件
- **SKIP_LLM**：跳过 LLM 调用（但仍执行后续 post 中间件）
- **RECHECK**：要求重新校对当前片段
- **ABORT**：中止当前片段的处理

## 校对上下文 (ProofreadContext)

中间件链的数据载体。包含管道注入字段（片段原文、提示词、工具集）、LLM 产出
字段（原始返回、工具调用日志、Token 统计）和各中间件附加字段（预检结果、
相似度报告、验证结论）。

## 大纲 (Outline)

文档的结构骨架，由 Python 扫描文档提取，存为 `_outline.json` 中间产物。
大纲被三个消费者复用：Smart 分割、结构审查、内容审查。

## 大纲条目 (OutlineItem)

大纲中的单个节点。包含层级深度、条目类型（标题/编号项/列表标记）、纯文本
内容和在原文档中的行号范围。条目之间通过 `children` 字段形成树形层级。

## 片段 (Fragment)

Split 阶段将文档切分后的独立校对单元。每个片段为一个目录，内含原文、图片、
校对报告和结构化数据。片段命名格式为 `frag_NNN`。

## 分割模式 (Split Mode)

Split 阶段支持的策略：

- **heading**：按 Markdown 标题切分，零 LLM 成本
- **smart**：大纲驱动切分，极低 LLM 成本（主力模式）
- **deep**：全文 LLM 切分，高成本兜底
- **manual**：按用户放置的标记切分
- **none**：全文作为单一片段
- **自定义正则**：通过 `--split-by-pattern` 指定，逃生舱

## 预检 (PreCheck)

LLM 校对前执行的中间件。扫描片段原文中的异常模式（括号不成对、连续重复标点、
连续重复词等），生成"提示列表"注入校对提示词。**仅标记位置，不判定对错，
不设严重级别。**

## 相似度校验 (Similarity)

LLM 校对后执行的中间件。对比原文和校对报告的结构骨架（段落数、数学块数、
列表项数），如不匹配则触发 RECHECK。零 LLM 成本。

## 校对摘要 (Proofread Summary)

LLM 在校对报告末尾附带的一句（≤50 字）片段内容摘要。由 Proofread 阶段
顺手产出，为内容审查（A-light）提供语义信息。

## 结构审查 (Structural Review)

内容审查的第一层。在大纲上执行纯 Python 规则检查（章节顺序、编号连续性、
层级一致性），零 LLM 成本。发现严重问题时默认暂停等待用户确认。

## 内容审查 (Content Review)

Proofread 阶段完成后执行的文档级全局审读。分三层：

- **Structural**（结构审查）：大纲级别，零成本
- **Light**（A-light）：大纲 + 每片段校对摘要，中成本
- **Full**（A-full）：大纲 + 全文原文 + 校对报告，高成本

产出的问题统一携带 `confidence` 字段：Light 上限 `medium`，Full 可有 `high`。

## 状态清单 (Manifest)

存储于 `output/{doc}/.proofread_manifest.json` 的文件。记录每个片段的校对
状态（`pending`/`in_progress`/`completed`）、MD5 哈希和时间戳。供
`--resume` 命令跳过已完成的片段。

## 配置方案 (Profile)

一组 JSON + 可选 Python 文件的组合，定义特定文档类型的校对行为。包含
提示词模板、中间件链配置、分割策略选择、审查层级等。

## 默认校对策略 (DefaultProofreadStrategy)

`ProofreadStrategy` 协议的参考实现。内部执行完整的中间件链流程：
pre 中间件（PreCheck）→ LLM 调用 → post 中间件（Similarity），
以 `ProofreadContext` 为载体，以 `MiddlewareResult` 为返回值。
闭合了 Strategy 层的对称性（`SplitStrategy` ↔ `ProofreadStrategy`）。

## 中层校对包装 (proofread_with_middleware)

位于 `default_proofread_one()` 之上的中间件链包装函数。负责构建
`ProofreadContext`、执行中间件链、调用 LLM（仅当 `skip_llm` 为 False），
最终返回填充好的上下文。`BaseProfile.proofread_one()` 通过此函数接入
中间件链，而非直接调用 `default_proofread_one()`。

## Deep 分割策略 (DeepSplitStrategy)

升级自 v0.1.0 的 `smart_split.py`（全文 `<problem>` 标签切分），实现
`SplitStrategy` 协议。定位为不计成本的全自动兜底策略：当 `smart` 模式
无法可靠切分时使用。旧的 `smart_split.py` 保留但标注为 legacy。

## 逃生舱 (Escape Hatch)

不暴露在一线 CLI 帮助文本中、需要用户主动指定才能使用的功能入口（如
`--split-by-pattern`）。用于覆盖工具自动化无法处理的边缘场景，由用户
对结果负责。

## 模块职责拆分 (Module Split)

v0.3.0 计划将 `defaults.py`（795 行）按职责拆分为四个模块：
- `text_cleaning.py` — 文本清洗函数
- `convert.py` — 文档格式转换
- `split_utils.py` — 试卷/讲义规则拆分
- `proofread_utils.py` — 校对主流程 + `proofread_with_middleware()`

当前阶段 `defaults.py` 保持不变，待独立 ADR（ADR-0013）驱动迁移。
