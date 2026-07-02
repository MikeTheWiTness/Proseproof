# 自定义配置方案示例

本目录演示如何创建自定义的 Proseproof 配置方案。

## 文件说明

- `config.json` — 静态配置（必选）
- `agent_prompt.json` — ReAct 工具循环提示词（可选）
- `profile.py` — Python 扩展逻辑（可选）

## 快速创建

```bash
# 从 generic 模板创建新方案
proseproof profile create my-style

# 编辑
vim profiles/my-style/config.json

# 使用
proseproof proofread ./fragments -p my-style
```

## 纯 JSON 方案示例

`config.json`：

```json
{
  "question_prompt_lines": [
    "你是一位专业校对专家...",
    "校对原则...",
    "输出格式..."
  ],
  "exam_split": {
    "question_pattern": "^#+\\s+"
  }
}
```

## Python 扩展示例

参考本目录下的 `profile.py` 了解如何添加自定义工具和钩子。
