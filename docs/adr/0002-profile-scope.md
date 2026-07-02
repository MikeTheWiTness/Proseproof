# ADR-0002: Profile 配置方案边界

- **状态**: 已采纳（v0.2.0 修订）
- **日期**: 2026-07-02
- **修订日期**: 2026-07-02

## 背景

系统需要一个"配置方案"（Profile）机制，让用户可以为不同类型的文档定制校对行为。
随着 v0.2.0 引入分割策略选择、中间件链配置和文档级审查层级，Profile 的边界需要
重新审视。

v0.1.0 的决策是 Profile 只绑定校对阶段。但 v0.2.0 的分割模式（heading/smart/deep）、
中间件链（PreCheck/Similarity）和审查层级（Light/Full）这些配置项，在语义上都属于
「这份文档应该怎么校对」——用户不应该在两个地方分别配置"怎么切"和"怎么校"。

## 决策

**Profile 绑定三个阶段：Split、Proofread、Review**。Convert 和 Typeset/Compile
保持独立（它们是格式转换和渲染工具，与校对策略无关）。

`config.json` 的顶层结构：

```json
{
  "split": {
    "mode": "smart",
    "outline": {
      "max_depth": 4,
      "numbered_patterns": ["^\\d+[.、．]", "^[(（]\\d+[)）]"],
      "numbered_hints": []
    }
  },
  "proofread": {
    "strategy": "default",
    "middleware_chain": [
      {"name": "pre_check", "enabled": true},
      {"name": "similarity", "enabled": true}
    ]
  },
  "review": {
    "structural": {"enabled": true},
    "content": {"mode": "light"}
  }
}
```

**三个阶段各自的 Profile 职责**：

| 阶段 | Profile 负责 | 不属于 Profile 的 |
|------|-------------|-------------------|
| Split | 分割模式选择、大纲提取配置、编号模式 | 文件格式转换（那是 Convert 的事） |
| Proofread | 提示词、工具集、中间件链 | 具体 LLM 模型选择（那是 CLI flag） |
| Review | 审查层级、结构审查开关 | 不涉及 |

**扩展方式不变**：
- `config.json`（必选）：JSON 声明式配置，入门门槛低。
- `profile.py`（可选）：Python 钩子，通过 `register_middleware()` 等方法注入
  自定义中间件或策略。
- 加载优先级：先加载 `config.json`，若存在 `profile.py` 则其 Python 类覆盖/扩展
  JSON 配置。

## 后果

**正面**:
- 用户在一个文件中完成「这份文档怎么校对」的全部配置。
- Split 模式选择（heading/smart/deep）与后续的审查阶段（需要大纲）自然关联——
  同一份配置保证一致性。
- 中间件链的顺序和组成完全声明式，用户无需写代码即可调整。
- 配置 schema 分层清晰：`split.` / `proofread.` / `review.` 各管各的，互不侵入。

**负面**:
- `config.json` 的字段数量增加，需要提供清晰的 schema 文档和默认值。
- 与 v0.1.0 的 Pure-JSON Profile 不兼容——旧 Profile 缺少 `split.` 和 `review.`
  字段，需要迁移指南。
- 如果未来某个阶段需要大量新配置项，可能导致 `config.json` 膨胀。届时可考虑
  拆分为 `split.json` + `proofread.json` + `review.json`，但当前规模不需要。
