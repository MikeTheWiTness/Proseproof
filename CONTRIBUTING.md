# 贡献指南

欢迎为 Proseproof 贡献代码、文档或配置方案。

## 开发环境

```bash
git clone https://github.com/MikeTheWiTness/Proseproof.git
cd proseproof
pip install -e ".[dev]"
```

推荐使用清华源加速：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"
```

## 项目结构

```
proseproof/
├── core/                    # 核心管线
│   ├── cli.py               # CLI 入口（Click）
│   ├── strategy.py           # Strategy 协议（SplitStrategy / ProofreadStrategy）
│   ├── middleware.py          # Middleware 协议（ProofreadContext / MiddlewareAction）
│   ├── middleware_runner.py   # 中间件链执行器
│   ├── proofread_middleware.py # proofread_with_middleware() + DefaultProofreadStrategy
│   ├── base_profile.py       # BaseProfile 基类
│   ├── config_loader.py      # config.json 加载
│   ├── api_client.py         # LLM API 客户端
│   ├── defaults.py           # 转换/拆分/校对默认实现
│   ├── parsing.py            # 校对报告解析 + JSON 落盘
│   ├── format_enforcement.py # 格式审查 + bash 修正
│   ├── manual_split.py       # 手动标记分割
│   └── logging_utils.py      # 统一日志（线程安全）
├── shared/                   # 共享模块
│   ├── heading_split.py      # 按标题切分（HeadingSplitStrategy）
│   ├── smart_split_v2.py     # 大纲驱动切分（SmartSplitStrategy）
│   ├── smart_split.py        # Deep 切分策略 + 原始输出落盘
│   ├── outline_extractor.py  # 大纲提取器
│   ├── pre_check.py           # PreCheck 中间件
│   ├── similarity.py          # Similarity 中间件
│   ├── structural_review.py   # 结构审查（纯 Python）
│   ├── light_review.py        # Light / Full 内容审查
│   ├── summary_utils.py       # 校对摘要提取
│   ├── manifest.py            # 断点续传状态清单
│   ├── latex_generator.py     # LaTeX .tex 生成
│   ├── pdf_compiler.py        # PDF 编译（xelatex）
│   ├── image_utils.py         # 图片复制
│   └── ...（其他工具模块）
├── profiles/                 # 内置配置方案
│   └── generic/              # 通用文本校对（默认）
│       ├── config.json       # 静态配置（必选）
│       ├── agent_prompt.json # ReAct 工具循环提示词（可选）
│       └── profile.py        # Python 扩展入口（可选）
├── templates/ -> shared/templates/  # LaTeX 模板
└── __init__.py               # 版本号
```

## 添加自定义配置方案

### 纯 JSON 方案（入门，推荐）

大多数场景只需要调整提示词和中间件链，不需要写代码。

```bash
# 从 generic 模板创建
proseproof profile create my-style

# 编辑提示词和配置
vim profiles/my-style/config.json
```

`config.json` 的关键配置段：

```json
{
  "question_prompt_lines": [...],
  "split": { "mode": "smart", "outline": { "max_depth": 4 } },
  "proofread": {
    "middleware_chain": [
      {"name": "pre_check", "enabled": true},
      {"name": "similarity", "enabled": true}
    ]
  },
  "review": { "content": {"mode": "light"} }
}
```

参考 `examples/custom_profile/` 了解完整示例。

### Python 扩展方案（高级）

当需要注册自定义工具或中间件时，在方案目录添加 `profile.py`：

```python
from proseproof.core.base_profile import BaseProfile

class MyProfile(BaseProfile):
    name = "my-style"

    def build_tools(self):
        """注册自定义工具集。"""
        return []  # 默认无工具

    def get_proofread_prompt(self) -> str:
        """自定义提示词生成逻辑（默认从 config.json 读取）。"""
        return super().get_proofread_prompt()

    def register_middleware(self) -> dict:
        """注册第三方中间件（内置的 pre_check/similarity 由框架自动注册）。"""
        return {}
```

`BaseProfile` 的所有方法都有默认实现（从 `config.json` 读取），只需覆盖需要定制的部分。

## 管线扩展

Proseproof 有两个扩展维度：

### 阶段策略替换（Strategy）

实现 `SplitStrategy` 或 `ProofreadStrategy` 协议，替换整个阶段的实现：

```python
from proseproof.core.strategy import SplitStrategy

class MySplitStrategy:
    def split(self, content: str, config: dict) -> list[dict]:
        # 自定义分割逻辑
        return [{"content": content}]
```

### 阶段内校验注入（Middleware）

实现 `ProofreadMiddleware` 协议，在 LLM 调用前后注入校验逻辑：

```python
from proseproof.core.middleware import (
    ProofreadContext, ProofreadMiddleware, MiddlewareAction, MiddlewareResult,
)

class MyMiddleware:
    name = "my_check"
    phase = "pre"  # "pre" or "post"

    def process(self, ctx: ProofreadContext) -> MiddlewareResult:
        # 检查片段原文，可选地设置 ctx.skip_llm 或返回 RECHECK/ABORT
        return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)
```

通过 `register_middleware()` 注册后，在 `config.json` 的 `middleware_chain` 中引用名称即可启用。

## 运行测试

```bash
# 全部测试（265 个）
pytest tests/ -v

# 单个模块测试
pytest tests/test_parsing.py -v
pytest tests/test_latex_generator.py -v
```

测试原则：
- 先写测试，再写实现（TDD）
- 测试覆盖真实执行路径，不 mock 掉胶水层
- 禁止为了通过测试而削弱测试效果

## 提交 PR

1. Fork 仓库
2. 创建特性分支
3. 添加测试
4. 确保 `pytest tests/ -v` 全部通过
5. 提交 PR

## 架构决策

重大设计变更需先写 ADR（Architecture Decision Record）。
参见 `docs/adr/` 下的现有决策记录（0014 份）。
