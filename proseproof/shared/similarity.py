"""v0.2.0 Similarity 中间件 —— LLM 校对后的结构骨架快检。

抽取原文和 LLM 返回中的结构骨架（段落数、数学块数、列表项数），
不匹配时触发 RECHECK。零 LLM 成本。

设计决策见 ADR-0006 (Strategy + Middleware 双层架构)。
"""
from __future__ import annotations
import re
from proseproof.core.middleware import (
    ProofreadContext, MiddlewareAction, MiddlewareResult, ProofreadMiddleware,
)
from proseproof.core.logging_utils import log


# 数学块（$...$ 单行 + $$...$$ 多行）
_INLINE_MATH_RE = re.compile(r'\$[^$\n]+?\$')
_DISPLAY_MATH_RE = re.compile(r'\$\$.*?\$\$', re.DOTALL)

# 列表项
_LIST_RE = re.compile(r'^[\s]*[-*•·]\s+', re.MULTILINE)
_NUMBERED_LIST_RE = re.compile(r'^[\s]*(\d+)[.、．]\s+', re.MULTILINE)

# 代码块（数学块计数时排除）
_CODE_FENCE_RE = re.compile(r'```.*?```', re.DOTALL)


def _extract_skeleton(text: str) -> dict:
    """从文本中提取结构骨架。

    Returns:
        {"length": int, "math_blocks": int, "list_items": int}
    """
    if not text or not text.strip():
        return {"length": 0, "math_blocks": 0, "list_items": 0}

    # 1. 文本总长度（用于比率比较）
    text_length = len(text.strip())

    # 2. 数学块数（排除代码块内的 $ 符号）
    clean = _CODE_FENCE_RE.sub('', text)
    inline_count = len(_INLINE_MATH_RE.findall(clean))
    display_count = len(_DISPLAY_MATH_RE.findall(clean))
    math_count = inline_count + display_count

    # 3. 列表项数
    list_count = len(_LIST_RE.findall(text)) + len(_NUMBERED_LIST_RE.findall(text))

    return {
        "length": text_length,
        "math_blocks": math_count,
        "list_items": list_count,
    }


class SimilarityMiddleware:
    """LLM 校对后执行的结构骨架快检中间件。

    对比原文和 LLM 返回中的结构骨架，任一项不匹配 → RECHECK。
    """

    name = "similarity"
    phase = "post"

    def process(self, ctx: ProofreadContext) -> MiddlewareResult:
        # 无原文或返回 → 跳过
        if not ctx.fragment_text or not ctx.raw_response:
            log(f"   ⏭️ [similarity] 跳过（缺少原文或 LLM 返回）")
            ctx.similarity_passed = None
            return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)

        original_skeleton = _extract_skeleton(ctx.fragment_text)
        response_skeleton = _extract_skeleton(ctx.raw_response)

        # 比对
        mismatches = []
        # 长度比率：LLM 返回长度不应低于原文的 30%
        if original_skeleton["length"] > 0:
            ratio = response_skeleton["length"] / original_skeleton["length"]
            if ratio < 0.3:
                mismatches.append(
                    f"length_ratio: {ratio:.1%}（低于30%阈值）"
                )
        # 数学块和列表项：精确匹配
        for key in ("math_blocks", "list_items"):
            if original_skeleton[key] != response_skeleton[key]:
                mismatches.append(
                    f"{key}: 原文{original_skeleton[key]} vs 返回{response_skeleton[key]}"
                )

        if mismatches:
            ctx.similarity_passed = False
            ctx.reject_result = True  # 触发 proofread_with_middleware 的 RECHECK 循环
            detail = "; ".join(mismatches)
            log(f"   ⚠️ [similarity] 结构不匹配: {detail}")
            return MiddlewareResult(
                ctx, MiddlewareAction.RECHECK,
                f"结构不匹配: {detail}",
            )
        else:
            ctx.similarity_passed = True
            log(f"   ✅ [similarity] 结构一致")
            return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)
