# AGENTS.md

本文件记录对 Agents 的持久化要求与工作约定。

---

## 工作语言

全程使用简体中文交流。

## 环境要求

- `pip install` 优先使用清华源：
  ```bash
  pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <package>
  ```

## 测试原则

- 禁止为了通过测试而削弱测试效果
- 测试必须能代表真实情况，不能削减需求来让测试通过

## 中间产物保留

- 所有 LLM 返回的原始内容（含 reasoning/thinking）、工具调用请求与返回内容、解析中间结果，**必须写入文件保留**，不得仅在日志中输出。用于后期排查问题时回溯完整调用链。
- 关键中间产物的保存路径：
- 智能分割原始输出 → `_smart_split_raw.md`（`proseproof/shared/smart_split.py:_dump_smart_split_raw`）
- 校对 LLM 原始返回 + 工具调用日志 + 思考内容 → `_校对报告.md`（`proseproof/core/defaults.py:default_proofread_one`）
- 校对结构化解析结果 → `_校对数据.json`（`proseproof/core/parsing.py:save_proofread_json`）
- API 调用日志 → 通过 `proseproof/core/logging_utils.py` 的 `log()` 函数输出到日志面板

## 错误日志

- 所有异常必须记录完整的上下文信息（触发异常的函数名、输入参数摘要、完整 traceback），不得只记录异常消息
- 使用 `log()` 函数（`proseproof/core/logging_utils.py`）输出到 UI 日志面板，同时落盘到日志文件
- 生产环境错误日志必须包含：时间戳、模块名、错误级别、完整堆栈
- API 调用失败时必须记录：请求 URL、模型名、HTTP 状态码、响应体摘要


## 常用工作流

| 技能 | 时机 | 产出 |
|---|---|---|
| `/grill-with-docs` | 设计讨论阶段 | 更新 CONTEXT.md，完成后输出一个 ADR |
| `/to-prd` | grill 结束后，若内容变动大 | 生成 PRD 文档到 issue tracker |
| `/to-issues` | PRD 完成后 | 拆分为可独立领取的 issue |
| `/implement` | 开始编码 | 按 issues 执行实现：预重构 → 构建 → 测试 → review → 提交。 |

## `/grill-with-docs` 时的行为准则

- 只记录，不执行。Grill 阶段收集问题、记录发现，标记为待处理，不要马上改代码
- 等所有问题问完、ADRs 输出后再进入执行阶段
- 发现的 bug/疏忽记录下来，在 `/to-issues` 阶段生成修复任务
