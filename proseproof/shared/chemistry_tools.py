"""
化学独立解题工具 — 可替换接口（对标 ADR-0006 物理独立解题架构）

ChemistryIndependentSolveTool：
  - 单次 API 调用 + 干净上下文
  - 工具签名稳定：(question_without_answer, solve_prompt) -> {answer, reasoning, ...}
  - 在干净上下文中执行：配平方程式 + 化学计量计算 + 综合求解

落盘：_化学求解.md（对标物理 _物理求解.md）
"""
import json
import os
import requests
from typing import Any
from pathlib import Path

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

# ---- 模块级 API 配置（在 default_proofread_one 中注入） ----

_api_config: dict = {}
"""存储 API 调用所需的配置：api_url, api_key, model, output_dir"""


def set_chemistry_api_config(api_url: str, api_key: str, model: str, output_dir: str | None = None):
    """在 ReAct 工具循环开始前注入 API 配置，供 ChemistryIndependentSolveTool 内部使用。"""
    global _api_config
    _api_config = {
        "api_url": api_url,
        "api_key": api_key,
        "model": model,
        "output_dir": output_dir,
    }


# ---- ChemistryIndependentSolveTool ----

class ChemistryIndependentSolveParams(BaseModel):
    question_without_answer: str = Field(
        description="去掉答案和解析的纯问题文本。必须确保上下文中不包含任何答案或解析内容，"
                    "以保证独立求解不受已见答案污染。"
    )
    solve_prompt: str = Field(
        description="求解指令 prompt。由主 agent 生成，包含求解要求、格式规范等。"
    )
    original_answer: str | None = Field(
        default=None,
        description="（可选）原始答案，用于写入 _化学求解.md 的答案比对段落。"
    )


class ChemistryIndependentSolveTool(BaseTool):
    """化学独立解题工具 —— 干净上下文、单次 API 求解，返回独立答案供主 agent 比对。

    工具签名（稳定接口）：
        independent_solve(question_without_answer, solve_prompt) -> {answer, reasoning, ...}

    内部实现可替换：
      - 当前：单次 API 调用
      - 未来可升级为 ReAct 多轮纠错解题循环
    """

    name: str = "independent_solve"
    description: str = (
        "化学独立求解工具：将去答案问题 + 求解 prompt 送入干净上下文（无主对话历史），"
        "发起单次 API 求解，返回独立答案。"
        "用于难题答案校验——主 agent 拿到独立答案后与答案比对，判断一致性。"
        "支持：化学方程式配平验证、化学计量计算（摩尔/质量/产率/纯度）、平衡常数/pH 计算、"
        "热化学计算、电化学计算等化学范围。"
        "仅在判定为难题时调用。"
    )
    args_schema: type[BaseModel] = ChemistryIndependentSolveParams

    def _run(
        self,
        question_without_answer: str,
        solve_prompt: str,
        original_answer: str | None = None,
    ) -> str:
        """执行化学独立解题。

        Args:
            question_without_answer: 去答案纯问题
            solve_prompt: 求解指令
            original_answer: 原始答案（可选，用于落盘比对）

        Returns:
            JSON 字符串：{"answer": str, "reasoning": str, "ok": bool, "error": str|None}
        """
        global _api_config
        api_url = _api_config.get("api_url", "")
        api_key = _api_config.get("api_key", "")
        model = _api_config.get("model", "")
        output_dir = _api_config.get("output_dir")

        if not api_url or not api_key:
            return json.dumps({
                "ok": False,
                "answer": "",
                "reasoning": "",
                "error": "independent_solve 缺少 API 配置（api_url/api_key 未注入）",
            }, ensure_ascii=False)

        chat_url = api_url.rstrip("/")
        if not chat_url.endswith("/chat/completions"):
            chat_url += "/chat/completions"

        # 干净 messages（无主对话历史）
        messages = [
            {"role": "system", "content": solve_prompt},
            {"role": "user", "content": f"请独立求解以下化学问题（不要依赖任何外部答案）：\n\n{question_without_answer}"},
        ]

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "reasoning_effort": "high",
            "max_tokens": 32768,
        }

        try:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            resp = requests.post(chat_url, json=payload, headers=headers, timeout=900)
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            content = choice["message"].get("content", "")
            reasoning = choice["message"].get("reasoning_content", "")

            # 落盘 _化学求解.md
            self._write_chemistry_solution(
                output_dir=output_dir,
                question_without_answer=question_without_answer,
                solve_prompt=solve_prompt,
                answer=content,
                reasoning=reasoning,
                original_answer=original_answer,
            )

            return json.dumps({
                "ok": True,
                "answer": content,
                "reasoning": reasoning,
                "error": None,
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({
                "ok": False,
                "answer": "",
                "reasoning": "",
                "error": str(e),
            }, ensure_ascii=False)

    def _write_chemistry_solution(
        self,
        output_dir: str | None,
        question_without_answer: str,
        solve_prompt: str,
        answer: str,
        reasoning: str,
        original_answer: str | None = None,
    ):
        """落盘 _化学求解.md。

        结构：
        - 独立求解输入（去答案问题 + 求解 prompt）
        - 独立解答案（最终答案 + 求解过程）
        - 答案比对（若提供了 original_answer）
        """
        if not output_dir:
            return

        try:
            os.makedirs(output_dir, exist_ok=True)
            md_path = os.path.join(output_dir, "_化学求解.md")

            lines = [
                "# 化学独立求解过程\n",
                "## 独立解题输入\n",
                "### 去答案问题\n",
                "```\n" + question_without_answer[:5000] + ("\n...[截断]" if len(question_without_answer) > 5000 else "") + "\n```\n",
                "### 解题 prompt\n",
                "```\n" + solve_prompt[:3000] + ("\n...[截断]" if len(solve_prompt) > 3000 else "") + "\n```\n",
                "---\n",
                "## 独立解答案\n",
                "### 最终答案 + 解题过程\n",
                answer[:20000] + ("\n...[截断]" if len(answer) > 20000 else ""),
            ]

            if reasoning:
                lines.append("\n\n### 解题推理过程 (reasoning)\n")
                lines.append("```\n" + reasoning[:5000] + ("\n...[截断]" if len(reasoning) > 5000 else "") + "\n```")

            # 答案比对（若提供了原始答案）
            if original_answer:
                lines.append("\n\n---\n")
                lines.append("## 答案比对\n")
                lines.append("- **答案**：" + original_answer[:500] + "\n")
                lines.append("- **独立解答案**：见上方「最终答案 + 解题过程」段\n")
                lines.append("- **比对结论**：待主 agent 综合评判（第 7 步）\n")

            with open(md_path, "w", encoding="utf-8") as f:
                f.write("".join(lines))

        except Exception:
            pass  # 落盘失败不影响解题主流程

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError
