# ADR-0011: 中间件链闭环 + CLI 集成接线

- **状态**: 已采纳
- **日期**: 2026-07-04

## 背景

v0.2.0 通过 Slice #6–#10 实现了中间件链的所有底层组件（`ProofreadContext`、`MiddlewareAction`、`run_middleware_chain`、`PreCheckMiddleware`、`SimilarityMiddleware`）和断点续传基础设施（`Manifest`），通过了 2818 行单元测试。但存在一个关键缺口：

1. **中间件链未接线**：`BaseProfile.proofread_one()` 直接调用 `default_proofread_one()` → `call_api()`，完全绕过了中间件链。
2. **`ProofreadStrategy` 协议无实现类**：与 `SplitStrategy`（已被 `HeadingSplitStrategy`、`SmartSplitStrategy` 实现）不对称。
3. **CLI `run` 命令缺少 v0.2.0 接口**：PRD 声明的 `--resume`、`--review`、`--yes`、`--middleware` 选项全部未实现。
4. **`BaseProfile` 抽象方法无默认实现**：纯 JSON profile 模式下 `get_proofread_prompt()` 等方法调用即崩溃。
5. **`config.json` 缺少 v0.2.0 配置段**：`split.mode`、`proofread.middleware_chain`、`review.content.mode` 等字段均未定义。
6. **`split` CLI 默认 mode 与 PRD 不一致**：PRD 声明 `smart`，实际硬编码 `rule`。

## 决策

### 1. 中间件链接入：`proofread_with_middleware()` 包装函数

采用方案 B：新建 `proofread_with_middleware()` 作为上层包装函数，`default_proofread_one()` 保持纯粹（单一职责：LLM 调用 + 格式审查 + 文件保存）。

```
proofread_with_middleware(ctx, chain, llm_callable)
  ├── 构建 ProofreadContext
  ├── run pre 中间件链  →  ctx.prompt 被 PreCheck 注入
  ├── if not ctx.skip_llm:
  │     call_api()       →  ctx.raw_response 被填充
  ├── run post 中间件链 →  Similarity 校验结构骨架
  └── return ctx
```

`BaseProfile.proofread_one()` 改为调用 `proofread_with_middleware()`，中间件链列表从 config 的 `proofread.middleware_chain` 读取。

### 2. `DefaultProofreadStrategy` 闭合 Strategy 层

实现 `ProofreadStrategy` 协议：

```python
class DefaultProofreadStrategy:
    def proofread(self, ctx: ProofreadContext) -> MiddlewareResult:
        return proofread_with_middleware(ctx, chain, ...)
```

与 `SplitStrategy` → `HeadingSplitStrategy` / `SmartSplitStrategy` 形成对称。

### 3. CLI `run` 命令补全

`proseproof run` 新增以下选项：

| 选项 | 说明 |
|------|------|
| `--resume` | 断点续传，跳过已完成且 MD5 未变的片段 |
| `--review light\|full\|off` | 内容审查层级（默认 light） |
| `--yes` | 结构审查严重问题时自动通过，不暂停 |
| `--middleware pre_check,similarity` | 中间件链（默认全部启用） |

行为：
- 任何片段失败立即终止流水线，Manifest 记录进度
- `--resume` 跳过 `completed` 且 MD5 一致的片段
- `--review light` 执行大纲 + 摘要审查
- `--review full` 执行全文深度审查

### 4. `BaseProfile` 默认实现

将 `build_tools()`、`get_proofread_prompt()`、`get_max_tool_loops()` 等抽象方法填上默认实现，从 `self.config` 读取：

- `get_proofread_prompt()` → `config["question_prompt_lines"]`
- `build_tools()` → `[]`（空工具列表）
- `get_max_tool_loops()` → `0`
- `get_segment_prompt()` / `get_review_prompt()` → 回退到 `get_proofread_prompt()`

### 5. `config.json` 补充 v0.2.0 配置结构

```json
{
  "split": {
    "mode": "smart",
    "outline": { "max_depth": 4 }
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

配置字段从 `base_profile.py` 直接通过 `self.config.get(...)` 读取，`config_loader.py` 不扩展。

### 6. `ctx.invoke` → 直接函数调用

`run` 命令不再使用 Click 的 `ctx.invoke` 串联子命令，改为直接调用 Pipeline 编排函数，提取为可复用的 Python 接口。

### 7. `DeepSplitStrategy` 升级

将 `smart_split.py`（全文 `<problem>` 标签切分）升级为 `DeepSplitStrategy`，实现 `SplitStrategy` 协议，作为不计成本的全自动兜底策略。

### 8. 版本号

Slice #12 交付时，`proseproof/__init__.py` 和 `pyproject.toml` 版本号从 `0.1.0` 升至 `0.2.0`。

## 后果

**正面**:
- v0.2.0 所有设计组件形成闭环，用户首次可通过 CLI 感知完整能力
- Strategy 层对称性闭合：`SplitStrategy` ↔ `ProofreadStrategy`
- 纯 JSON profile 立即可用，无需编写 `profile.py`
- `run` 命令成为真正的"一键流水线"，支持断点续传和自动审查

**负面**:
- `proofread_with_middleware()` 引入一新层抽象，增加调用链长度
- CLI `run` 命令选项数量增多，需要良好的默认值设计
- `config.json` 字段增多，需要向后兼容 v0.1.0 的旧配置格式
