# Proseproof

通用 AI 文稿校对与 LaTeX 排版工具 —— 一个可组合的命令行校对管线。

## 核心流程

```
Word/文本 → 拆分 → AI校对 → 格式审查 → 结构化解析 → LaTeX排版 → PDF编译
```

每阶段独立可调用，按需组合：

```bash
proseproof run manuscript.docx -o ./output          # 一键全流程
proseproof convert manuscript.docx -o manuscript.md  # 仅转换
proseproof split manuscript.md -o ./fragments        # 仅拆分
proseproof proofread ./fragments -p academic          # 仅校对
proseproof typeset ./fragments -o output.pdf          # 仅排版
proseproof compile output.tex -o output.pdf           # 仅编译
```

## 特性

- **可组合管线**：5 个阶段独立可调用，灵活编排
- **AI 校对**：接入任意 OpenAI 兼容 API，支持 ReAct 工具循环模式
- **智能拆分**：规则/LLM/手动三种拆分模式
- **结构化输出**：内联标记 + JSON 数据，机器可消费
- **LaTeX 排版**：paracol 双栏对照排版（原文 | 修改意见）
- **配置方案**：JSON 驱动，Python 可扩展

## 安装

```bash
pip install proseproof
```

要求 Python >= 3.10。如需 PDF 编译，需要安装 xelatex（TeX Live）。

## 快速开始

### 1. 设置 API

```bash
export PROSEPROOF_API_URL=https://your-api-endpoint/v1/chat/completions
export PROSEPROOF_API_KEY=your-api-key
export PROSEPROOF_MODEL=your-model-name
```

### 2. 校对文档

```bash
# 一键流程
proseproof run article.docx -o ./output

# 或分步执行
proseproof convert article.docx -o article.md
proseproof split article.md --mode smart -o ./fragments
proseproof proofread ./fragments/article -p generic --react
proseproof typeset ./fragments/article -o ./output/article.pdf
```

### 3. 自定义配置方案

```bash
# 从模板创建
proseproof profile create my-style

# 编辑配置
vim profiles/my-style/config.json
```

## 配置方案

配置方案（Profile）定义校对策略：提示词、工具集、校对规则。内置 `generic` 方案适用于通用文本。

```json
{
  "question_prompt_lines": [
    "你是一位资深校对专家...",
    "## 校对原则",
    "1. **字词校对**...",
    "2. **语句校对**..."
  ]
}
```

高级定制可添加 `profile.py`：

```python
from proseproof.core.base_profile import BaseProfile

class MyProfile(BaseProfile):
    def build_tools(self):
        # 注册自定义工具
        pass
```

## 校对数据结构

每个片段校对后生成 `_校对数据.json`：

```json
{
  "summary": "一般问题",
  "corrections": [
    {
      "num": 1,
      "type": "error",
      "severity": "major",
      "original": "原文文本",
      "correction": "改为文本",
      "reason": "原因说明"
    }
  ]
}
```

- `type`: `error` 纠错型 | `suggestion` 建议型
- `severity`: `critical` | `major` | `minor` | `info`

## 示例扩展

参见 `examples/` 目录了解如何创建自定义配置方案和扩展。

## 从源代码安装

```bash
git clone https://github.com/proseproof/proseproof.git
cd proseproof
pip install -e .
```

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

## 贡献

欢迎贡献！详见 [CONTRIBUTING.md](CONTRIBUTING.md)
