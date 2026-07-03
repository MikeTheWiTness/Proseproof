"""TDD: Similarity 中间件 —— 结构骨架快检。

在 LLM 校对后执行，对比原文和 LLM 返回的结构骨架：
  - 段落数
  - 数学块数 ($...$ / $$...$$)
  - 列表项数

覆盖:
  ✅ 结构匹配 → CONTINUE
  ✅ 段落数不匹配 → RECHECK
  ✅ 数学块数不匹配 → RECHECK
  ✅ 列表项不匹配 → RECHECK
  ✅ 空原文/空返回处理
  ✅ 无 raw_response 处理
"""
import pytest
from proseproof.core.middleware import (
    ProofreadContext, MiddlewareAction, MiddlewareResult,
)


def make_ctx(original="", response=""):
    return ProofreadContext(
        fragment_text=original,
        fragment_id="frag_001",
        images=[],
        prompt="校对",
        tools=[],
        config={},
        raw_response=response,
    )


class TestStructureSkeleton:
    """_extract_skeleton() 的结构提取。"""

    def test_length(self):
        from proseproof.shared.similarity import _extract_skeleton

        text = "abc"
        skeleton = _extract_skeleton(text)
        assert skeleton["length"] == 3

    def test_length_stripped(self):
        from proseproof.shared.similarity import _extract_skeleton

        text = "  abc\n  "
        skeleton = _extract_skeleton(text)
        assert skeleton["length"] == 3

    def test_math_blocks_inline(self):
        from proseproof.shared.similarity import _extract_skeleton

        text = "公式 $x^2 + y^2 = 1$ 和 $a = b$"
        skeleton = _extract_skeleton(text)
        assert skeleton["math_blocks"] == 2

    def test_math_blocks_display(self):
        from proseproof.shared.similarity import _extract_skeleton

        text = "$$\n\\frac{1}{2}\n$$\n\n$$\nx = 1\n$$"
        skeleton = _extract_skeleton(text)
        assert skeleton["math_blocks"] == 2

    def test_list_items(self):
        from proseproof.shared.similarity import _extract_skeleton

        text = "- 项目1\n- 项目2\n- 项目3"
        skeleton = _extract_skeleton(text)
        assert skeleton["list_items"] == 3

    def test_numbered_list_items(self):
        from proseproof.shared.similarity import _extract_skeleton

        text = "1. 第一\n2. 第二\n3. 第三"
        skeleton = _extract_skeleton(text)
        assert skeleton["list_items"] == 3

    def test_empty_text(self):
        from proseproof.shared.similarity import _extract_skeleton

        skeleton = _extract_skeleton("")
        assert skeleton["length"] == 0
        assert skeleton["math_blocks"] == 0
        assert skeleton["list_items"] == 0

    def test_code_block_not_counted_as_math(self):
        """代码块中的 $ 符号不算数学块。"""
        from proseproof.shared.similarity import _extract_skeleton

        text = "```\n$not_math$\n```\n\n真正的公式 $x=1$"
        skeleton = _extract_skeleton(text)
        # 只有 $x=1$ 是一个数学块（代码块内不算）
        assert skeleton["math_blocks"] == 1


class TestSimilarityMiddleware:
    """Similarity 中间件的行为测试。"""

    def test_matching_structure_continues(self):
        """结构匹配 → CONTINUE。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        original = "公式 $x=1$ 和列表\n- A\n- B"
        response = "### 标记原文\n公式 $x=1$ 和列表\n- A\n- B\n\n### 修改原因\n无问题"

        mw = SimilarityMiddleware()
        ctx = make_ctx(original=original, response=response)
        result = mw.process(ctx)

        assert result.action == MiddlewareAction.CONTINUE

    def test_length_ratio_too_low_rechecks(self):
        """长度比率过低 → RECHECK。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        original = "这是一段很长的原文内容" * 10  # 长原文
        response = "短返回"  # 极短返回

        mw = SimilarityMiddleware()
        ctx = make_ctx(original=original, response=response)
        result = mw.process(ctx)

        assert result.action == MiddlewareAction.RECHECK

    def test_math_block_count_mismatch_rechecks(self):
        """数学块数不一致 → RECHECK。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        original = "公式 $a=1$ 和 $b=2$"  # 2 math blocks
        response = "### 标记原文\n公式 a=1 和 b=2\n\n### 修改原因\n..."

        mw = SimilarityMiddleware()
        ctx = make_ctx(original=original, response=response)
        result = mw.process(ctx)

        assert result.action == MiddlewareAction.RECHECK

    def test_list_item_count_mismatch_rechecks(self):
        """列表项数不一致 → RECHECK。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        original = "- A\n- B\n- C"  # 3 items
        response = "### 标记原文\n- A\n- B\n\n### 修改原因\n..."

        mw = SimilarityMiddleware()
        ctx = make_ctx(original=original, response=response)
        result = mw.process(ctx)

        assert result.action == MiddlewareAction.RECHECK

    def test_no_raw_response_skips(self):
        """无 raw_response → CONTINUE（跳过检查）。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        mw = SimilarityMiddleware()
        ctx = make_ctx(original="原文", response="")
        result = mw.process(ctx)

        assert result.action == MiddlewareAction.CONTINUE

    def test_empty_original_skips(self):
        """原文为空 → CONTINUE。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        mw = SimilarityMiddleware()
        ctx = make_ctx(original="", response="something")
        result = mw.process(ctx)

        assert result.action == MiddlewareAction.CONTINUE

    def test_result_stores_similarity_flag(self):
        """ctx 中记录了 similarity_passed 标志。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        original = "公式 $x=1$"
        response = "### 标记原文\n公式 $x=1$\n\n### 修改原因\n..."

        mw = SimilarityMiddleware()
        ctx = make_ctx(original=original, response=response)
        result = mw.process(ctx)

        assert result.context.similarity_passed is True

    def test_similarity_phase_is_post(self):
        """Similarity 是 post 阶段中间件。"""
        from proseproof.shared.similarity import SimilarityMiddleware

        mw = SimilarityMiddleware()
        assert mw.phase == "post"
