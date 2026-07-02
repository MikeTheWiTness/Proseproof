"""PlanUpdateTool — LLM ���主管理校对计划的工具。

参考 Claude Code TodoWriteTool (src/tools/TodoWriteTool/) 的模式：
- LLM 通过工具调用管理计划状态（非系统解析自由文本）
- 状态：pending → in_progress → completed
- 恰好 1 项 in_progress；全部 completed 时追加自查 nudge
"""
from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool


class PlanItem(BaseModel):
    """校对计划中的一个步骤。"""
    content: str = Field(
        description="祈使句形式：'通读全文，识别文本类型'"
    )
    status: Literal["pending", "in_progress", "completed"] = Field(
        default="pending",
        description="步骤状态：pending(待开始) / in_progress(进行中) / completed(已完成)"
    )
    activeForm: str = Field(
        description="进行时形式：'正在通读全文…'"
    )


class PlanUpdateParams(BaseModel):
    """PlanUpdateTool 的输入参数。"""
    todos: list[PlanItem] = Field(
        description="更新后的完整计划列表。恰好 1 项为 in_progress"
    )


class PlanUpdateTool(BaseTool):
    """让 LLM 通过工具调用来管理校对计划。

    用法：
    - 首轮校对：LLM 调用 plan_update 声明计划步骤，首项为 in_progress
    - 每完成一步：调用 plan_update 标记该项 completed，下一项 in_progress
    - 全部完成：工具返回值中追加自查 nudge（若 nudge_template 非 None）

    nudge_template:
    - None：跳过 nudge（物理场景，自检靠 prompt）
    - 非空字符串：全部 completed 时使用该模板生成 nudge
    - 默认值（未传参）：使用原有 hardcoded 文本校对 nudge（向后兼容文本校对等）
    """

    name: str = "plan_update"
    description: str = (
        "更新校对计划的状态。开始新步骤前标记上一项为 completed，"
        "新项为 in_progress。恰好 1 项 in_progress。"
        "全部 completed 时系统会提示自查。"
    )
    args_schema: type[BaseModel] = PlanUpdateParams

    nudge_template: str | None = None
    """自定义自查 nudge 模板。
    - None（默认）→ 使用原有 hardcoded 文本校对 nudge
    - ""（空串）→ 跳过 nudge（物理场景用）
    - 非空 → 直接作为 nudge 文本
    """

    _DEFAULT_NUDGE: str = (
        "\n\n⚡ 所有校对步骤已完成。在输出最终结果前，请自检：\n"
        "1. 标记原文中的标记数量是否等于修改原因中的条目数量？\n"
        "2. ### 标记原文 和 ### 修改原因 两个段落是否都存在？\n"
        "3. 是否有遗漏的错误类型（如只改了错别字漏了标点）？"
    )

    def _run(self, todos: list[dict]) -> dict:
        """执行计划更新，返回状态摘要和可选的自查提示。

        Args:
            todos: 更新后的计划列表，每项含 content/status/activeForm

        Returns:
            dict: {"ok": bool, "summary": str, "nudge": str}
        """
        items = []
        for item in todos:
            status = item.get("status", "pending")
            content = item.get("content", "")
            items.append({"content": content, "status": status})

        # 检查 in_progress 数量
        in_progress_count = sum(1 for i in items if i["status"] == "in_progress")
        if in_progress_count > 1:
            return {
                "ok": False,
                "summary": (
                    f"错误：当前有 {in_progress_count} 项为 in_progress，"
                    "应该恰好 1 项。请修正后再提交。"
                ),
                "nudge": "",
            }

        # 生成状态摘要
        total = len(items)
        completed = sum(1 for i in items if i["status"] == "completed")
        pending = sum(1 for i in items if i["status"] == "pending")
        in_progress_items = [i["content"] for i in items if i["status"] == "in_progress"]

        summary_lines = [
            f"计划已更新：共 {total} 项 — {completed} 已完成，{pending} 待开始",
        ]
        if in_progress_items:
            summary_lines.append(f"当前进行中：{in_progress_items[0]}")

        # 全部 completed → 追加自查 nudge
        all_done = completed == total
        nudge = ""
        if all_done and total >= 3:
            if self.nudge_template is None:
                # 默认行为：使用 hardcoded 文本校对 nudge（文本校对等）
                nudge = self._DEFAULT_NUDGE
            elif self.nudge_template:
                # 自定义 nudge 模板（模块专用）
                nudge = self.nudge_template
            # else: nudge_template == "" → 跳过 nudge（物理场景：自检靠 prompt）

        return {
            "ok": True,
            "summary": "\n".join(summary_lines),
            "nudge": nudge,
        }

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError
