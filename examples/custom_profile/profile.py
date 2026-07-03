"""自定义配置方案示例 —— Python 扩展。

用法：
  proseproof proofread ./fragments -p examples/custom_profile

本文件演示如何通过继承 BaseProfile 来定制校对行为：
  - 覆盖 build_tools() 注册自定义工具
  - 覆盖 get_proofread_prompt() 自定义提示词
  - 覆盖 register_middleware() 注入第三方中间件
"""
from proseproof.core.base_profile import BaseProfile


class CustomProfile(BaseProfile):
    """自定义校对方案的 Python 扩展入口。

    如果没有 Python 定制需求，可以删除本文件，纯 JSON 方案同样可用。
    """

    name = "custom"
    version = "1.0"

    def build_tools(self):
        """注册自定义工具集。

        返回 LangChain BaseTool 列表。基础工具（web_search、web_fetch 等）
        在子类中按需导入。返回空列表表示不使用工具。
        """
        return []

    def get_max_tool_loops(self) -> int:
        """ReAct 工具循环最大次数。0 表示无工具循环。"""
        return 0

    def get_tool_instructions(self) -> str:
        """工具使用说明，注入到 prompt 中。"""
        return ""

    def get_proofread_prompt(self) -> str:
        """自定义校对提示词。

        默认从 config.json 的 question_prompt_lines 读取。
        覆盖此方法可完全自定义提示词生成逻辑。
        """
        return super().get_proofread_prompt()

    def register_middleware(self) -> dict:
        """注册自定义中间件。

        返回 {name: instance} 的字典。内置中间件（pre_check、similarity）
        由框架自动注册，此处仅用于第三方扩展。
        """
        return {}
