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
proseproof convert paper.docx --strip-small-images     # 自动过滤装饰图
proseproof split manuscript.md -o ./fragments        # 仅拆分
proseproof split manuscript.md --mode smart --split-by-pattern "^Chapter" # 自定义正则
proseproof proofread ./fragments -p academic          # 仅校对
proseproof typeset ./fragments -o output.pdf          # 仅排版
proseproof compile output.tex -o output.pdf           # 仅编译
```

## 特性

- **可组合管线**：5 个阶段独立可调用，灵活编排
- **AI 校对**：接入任意 OpenAI 兼容 API，支持 ReAct 工具循环模式
- **智能拆分**：heading/smart/deep/manual/rule/none/pattern 七种模式
- **中间件链**：PreCheck（异常预检）+ Similarity（结构校验），可扩展
- **结构化输出**：内联标记 + JSON 数据，机器可消费
- **断点续传**：`--resume` 跳过已完成片段，Manifest 状态追踪
- **内容审查**：Light（大纲+摘要）/ Full（大纲+全文）两级
- **LaTeX 排版**：paracol 双栏对照排版（原文 | 修改意见）

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

# 断点续传 + 自动审查
proseproof run article.md --resume --review light

# 自定义分割边界
proseproof run article.md --split-by-pattern "^第[一二三四五六七八九十]+章"

# 转换时清洗装饰图片
proseproof convert article.docx -o article.md --strip-small-images
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
  ],
  "split": {
    "mode": "smart",
    "outline": { "max_depth": 4, "extra_signals": [] }
  },
  "proofread": {
    "middleware_chain": [
      {"name": "pre_check", "enabled": true},
      {"name": "similarity", "enabled": true}
    ]
  },
  "review": { "content": {"mode": "light"} }
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
