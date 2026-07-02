# 贡献指南

欢迎为 Proseproof 贡献代码、文档或配置方案。

## 开发环境

```bash
git clone https://github.com/proseproof/proseproof.git
cd proseproof
pip install -e ".[dev]"
```

## 项目结构

```
proseproof/
├── core/          # 核心流水线：API调用、解析、格式审查、拆分
├── shared/        # 共享工具：LaTeX生成、PDF编译、图片处理
├── profiles/      # 内置配置方案
│   └── generic/   # 通用文本校对（默认）
├── templates/     # LaTeX 模板
├── cli.py         # CLI 入口（Click）
└── __init__.py
```

## 添加自定义配置方案

### 纯 JSON 方案（入门）

1. 创建方案目录：

```bash
proseproof profile create my-domain
```

2. 编辑 `profiles/my-domain/config.json`：
   - `question_prompt_lines`: 校对提示词
   - `exam_split.question_pattern`: 拆分正则

3. 使用：

```bash
proseproof proofread ./fragments -p my-domain
```

### Python 扩展方案（高级）

在方案目录添加 `profile.py`：

```python
from proseproof.core.base_profile import BaseProfile
from langchain_core.tools import BaseTool

class MyProfile(BaseProfile):
    name = "my-domain"

    def build_tools(self):
        """注册自定义工具。"""
        tools = []
        # 基础工具
        from proseproof.shared.plan_tools import PlanUpdateTool
        tools.append(PlanUpdateTool())

        # 自定义工具
        tools.append(MyCustomTool())
        return tools

    def get_tool_instructions(self):
        return "你可以使用以下工具..."

    def get_max_tool_loops(self):
        return 20

    def get_proofread_prompt(self):
        return "你是一位专业领域的校对专家..."
```

## 管线扩展

每个阶段接受标准输入、产出标准输出，可按需插入自定义处理：

```python
from proseproof.core.defaults import default_proofread_one

def my_custom_proofread(api_url, api_key, model, q_dir, q_name, **kwargs):
    # 前置处理
    # ...
    result = default_proofread_one(api_url, api_key, model, q_dir, q_name, ...)
    # 后置处理
    # ...
    return result
```

## 运行测试

```bash
pytest tests/ -v
```

## 提交 PR

1. Fork 仓库
2. 创建特性分支
3. 添加测试
4. 确保现有测试通过
5. 提交 PR
