# Proseproof Profile

"""通用文本校对配置方案。

这是一个纯 JSON 驱动的配置方案（无 Python 定制逻辑）。
如需定制工具集或钩子函数，可添加 profile.py 继承 BaseProfile。
"""

# 如需定制，在此添加：
#
# from proseproof.core.base_profile import BaseProfile
#
# class MyProfile(BaseProfile):
#     def build_tools(self):
#         # 注册自定义工具
#         pass
#
#     def get_tool_instructions(self):
#         # 自定义工具说明
#         pass
