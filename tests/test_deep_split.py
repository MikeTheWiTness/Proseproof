"""TDD: deep 分割策略 —— 全文 LLM <problem> 标签切分。

原 v0.1.0 的 smart 模式，重命名为 deep。高成本兜底方案。

Mock LLM 边界全覆盖：
  ✅ 正常 <problem> 标签
  ✅ 无标签 → 降级为单片段
  ✅ 标签不成对
  ✅ 空返回
  ✅ LLM 异常重试
  ✅ 嵌套标签
"""
import json
import pytest


# ============================================================
# 测试文档
# ============================================================

DEEP_DOC = """\
这是引言部分。

**例1** 这是第一道题的内容。
包含多个段落。

**例2** 这是第二道题的内容。
也有多个段落。

这是总结部分。
"""


# ============================================================
# Mock LLM
# ============================================================

def make_deep_mock(response: str, raise_on: int = 0):
    """构建 deep 分割的 mock LLM."""
    counter = [0]
    calls = []

    def _llm(content: str, prompt: str) -> str:
        counter[0] += 1
        calls.append({"content": content, "prompt": prompt})
        if raise_on and counter[0] <= raise_on:
            raise ConnectionError(f"模拟故障 #{counter[0]}")
        return response

    _llm.counter = counter
    _llm.calls = calls
    return _llm


# ============================================================
# 测试
# ============================================================

class TestDeepSplitNormal:
    """正常流程。"""

    def test_problem_tags_split(self):
        """LLM 返回 <problem> 标签 → 正确切分。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = """\
<problem>
**例1** 这是第一道题的内容。
包含多个段落。
</problem>
<problem>
**例2** 这是第二道题的内容。
也有多个段落。
</problem>"""
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable(DEEP_DOC, llm)
        assert len(fragments) == 2
        assert "例1" in fragments[0]["content"]
        assert "例2" in fragments[1]["content"]

    def test_no_tags_fallback(self):
        """LLM 未返回 <problem> 标签 → 降级为单一片段。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = "这是一段没有标签的返回文本。"
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable(DEEP_DOC, llm)
        assert len(fragments) == 1
        assert fragments[0]["content"] == DEEP_DOC

    def test_single_tag_wraps_all(self):
        """LLM 用一个 <problem> 包裹全部内容。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = f"<problem>\n{DEEP_DOC}\n</problem>"
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable(DEEP_DOC, llm)
        assert len(fragments) == 1
        assert "例1" in fragments[0]["content"]

    def test_intermediate_artifact_saved(self):
        """验证 _dump_smart_split_raw 被调用（不崩溃）。"""
        from proseproof.shared.smart_split import (
            smart_split_with_callable, _dump_smart_split_raw,
        )

        response = "<problem>test</problem>"
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable("test doc", llm, md_file="test.md")
        assert len(fragments) == 1

    def test_retry_on_first_failure(self):
        """第一次 LLM 调用失败 → 重试成功。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = "<problem>test</problem>"
        llm = make_deep_mock(response, raise_on=1)

        fragments = smart_split_with_callable("test doc", llm)
        assert len(fragments) == 1
        assert llm.counter[0] == 2  # 调用了两次

    def test_empty_tags_stripped(self):
        """空 <problem></problem> 被过滤。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = """\
<problem>有效内容</problem>
<problem>   </problem>
<problem>第二段</problem>"""
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable("test doc", llm)
        assert len(fragments) == 2


class TestDeepSplitEdgeCases:
    """边界情况。"""

    def test_unclosed_tag(self):
        """LLM 返回未闭合的 <problem> 标签。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = "<problem>开始但没结束"
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable("test", llm)
        # 不应崩溃
        assert len(fragments) == 1
        assert fragments[0]["content"] == "test"

    def test_nested_tags(self):
        """意外嵌套的 <problem> 标签。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = "<problem>外层<problem>内层</problem></problem>"
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable("test", llm)
        # 不应崩溃
        assert len(fragments) >= 1

    def test_empty_response(self):
        """LLM 返回空字符串。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        llm = make_deep_mock("")

        fragments = smart_split_with_callable(DEEP_DOC, llm)
        assert len(fragments) == 1
        assert fragments[0]["content"] == DEEP_DOC

    def test_only_whitespace_in_tags(self):
        """标签内只有空白字符。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = "<problem>\n  \n\t\n</problem>"
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable("test", llm)
        assert len(fragments) == 1
        # 降级为单一片段
        assert fragments[0]["content"] == "test"

    def test_tags_with_newlines(self):
        """标签前后有多余换行。"""
        from proseproof.shared.smart_split import smart_split_with_callable

        response = """
<problem>
内容A
</problem>

<problem>
内容B
</problem>
"""
        llm = make_deep_mock(response)

        fragments = smart_split_with_callable("test doc", llm)
        assert len(fragments) == 2
        assert "内容A" in fragments[0]["content"]
        assert "内容B" in fragments[1]["content"]

    def test_raw_dump_not_crashing(self):
        """_dump_smart_split_raw 在各种路径下不崩溃。"""
        from proseproof.shared.smart_split import _dump_smart_split_raw

        # None md_file
        _dump_smart_split_raw("测试内容", None)
        # 空内容
        _dump_smart_split_raw("", "test.md")
        # None raw_text
        _dump_smart_split_raw(None, "test.md")
