# ADR-0006: Strategy + Middleware 双层架构

- **状态**: 已采纳
- **日期**: 2026-07-02

## 背景

Proseproof v0.1.0 采用「阶段方法」架构：每个 Pipeline 阶段对应 BaseProfile 的
一个方法（`split_document`、`proofread_one` 等），定制逻辑通过方法覆写实现。

v0.2.0 需要引入多种新能力：smart-flash 分割、预检标记、相似度校验、两阶段
LLM 验证、文档级全局审读。这些能力在性质上分属两种不同的定制维度：

1. **阶段策略替换**：Split 阶段的新分割模式（heading/smart/deep），替换的是
   整个阶段的实现逻辑。
2. **阶段内横切校验**：PreCheck、Similarity、LLMVerify 等，不改变校对阶段的
   核心流程，只在 LLM 调用前后注入附加检查。

如果将所有能力统一塞入 Profile 配置项（方案 A），`proofread_one` 方法会
越来越臃肿，分支逻辑不可测试。如果全部抽象为 Hooks（方案 B），阶段策略
替换（如新分割模式）无法用 Hook 表达。纯 Validator 链（方案 C）则无法
处理 SKIP_LLM / RECHECK 等需要改变流程控制的需求。

## 决策

采用 **Strategy + Middleware 双层架构**：

**第一层 — Strategy（阶段策略）**：
- 每个 Pipeline 阶段有一个 Strategy 接口（如 `SplitStrategy`、`ProofreadStrategy`），
  用户通过 Profile 选择具体实现。
- 新增一个分割模式 = 实现一个 Strategy，不修改 Pipeline 核心代码。
- 第三方可通过 Python 包提供自定义 Strategy，`pip install` 后注册即可。

**第二层 — Middleware（中间件链）**：
- 仅存在于 Proofread 阶段内部，是 LLM 调用前后的可组合校验链。
- 每个中间件实现 `ProofreadMiddleware` 协议：
  - `name: str`
  - `phase: Literal["pre", "post"]`
  - `process(ctx: ProofreadContext) -> MiddlewareResult`
- MiddlewareResult 使用返回值语义表达控制意图：
  - `CONTINUE`：正常流转
  - `SKIP_LLM`：跳过 LLM 调用，但仍执行后续 post 中间件
  - `RECHECK`：要求重新校对当前片段
  - `ABORT`：中止当前片段处理
- 中间件链的顺序和启用状态在 `config.json` 的 `proofread.middleware_chain` 中
  声明，用户可调整顺序、关闭不需要的中间件。
- 第三方 Middleware 通过 `profile.py` 的 `register_middleware()` 方法注入。

所有中间件通过 `ProofreadContext` 数据载体交换信息，中间件之间不直接通信。

## 后果

**正面**:
- 两个定制维度各得其所：阶段策略替换走 Strategy，LLM 前后的校验注入走 Middleware。
- 中间件链的顺序和组成完全可配置，客制化能力强。
- 第三方扩展只需实现接口，无需 fork 主仓库。
- 返回值语义（MiddlewareAction）让流程控制显式化，避免全局状态标记的混乱。

**负面**:
- 比单一方案多引入一个概念（Middleware），学习曲线略高。
- ProofreadContext 作为共享数据载体，中间件对其字段的读写需要约定（哪些字段
  不可变、哪些字段谁负责写入）。
- Strategy 和 Middleware 的边界需要纪律：当某个能力既涉及阶段策略又涉及校验
  时（如大纲提取），需要明确归属。
