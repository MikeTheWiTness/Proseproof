"""TDD: heading 分割策略 —— 按 Markdown 标题切分文档。

覆盖:
  - 按标题边界切分为独立片段
  - 多级标题正确处理
  - 无标题文档降级为 none 模式
  - 前言内容归属第一个片段
  - 大纲产出正确
  - 空文档 / 纯文本 / 标题后无内容
"""
import json
import tempfile
import os
from pathlib import Path
import pytest


# ============================================================
# 测试文档
# ============================================================

HEADING_DOC = """\
# 第一章

这是第一章的内容。
包含多个段落。

## 1.1 第一节

第一节的具体内容。

## 1.2 第二节

第二节的具体内容。
也是多段落。

# 第二章

第二章开始。
"""

NO_HEADING_DOC = """\
这是一篇没有标题的纯文本文档。

只有段落，没有任何 Markdown 标题标记。
"""

SINGLE_HEADING_DOC = """\
# 唯一的标题

这是唯一的章节。
"""

PREAMBLE_DOC = """\
这是前言内容，在第一个标题之前。

可能包含一些总述性的文字。

# 第一章

正式内容开始。
"""

EMPTY_DOC = ""

HEADING_WITH_IMAGES_DOC = """\
# 第一章

这里有张图片：![示例](images/example.png)

## 1.1 第一节

更多内容。
"""

DEEP_HEADING_DOC = """\
# L1 内容
L1 body.
## L2 内容
L2 body.
### L3 内容
L3 body.
#### L4 内容
L4 body.
"""


# ============================================================
# 测试
# ============================================================

class TestHeadingSplitStrategy:
    """heading 分割策略的核心测试。"""

    def test_split_by_headings(self):
        """按标题正确切分。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(HEADING_DOC, {})

        # 四个标题 → 四个片段（# 第一章, ## 1.1, ## 1.2, # 第二章）
        assert len(fragments) == 4

        assert "第一章" in fragments[0]["content"]
        assert "1.1" in fragments[1]["content"]
        assert "1.2" in fragments[2]["content"]
        assert "第二章" in fragments[3]["content"]

    def test_fragment_has_required_fields(self):
        """每个片段包含 content 和 fragment_id。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(HEADING_DOC, {})

        for frag in fragments:
            assert "content" in frag
            assert "heading" in frag
            assert isinstance(frag["content"], str)
            assert len(frag["content"]) > 0

    def test_no_headings_fallback_to_none(self):
        """无标题文档降级为 none 模式（整篇作为一个片段）。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(NO_HEADING_DOC, {})

        assert len(fragments) == 1
        assert fragments[0]["content"] == NO_HEADING_DOC

    def test_single_heading_one_fragment(self):
        """只有一个标题 → 一个片段。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(SINGLE_HEADING_DOC, {})

        assert len(fragments) == 1
        assert "唯一的标题" in fragments[0]["content"]

    def test_preamble_merged_into_first_fragment(self):
        """前言内容合并到第一个标题片段。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(PREAMBLE_DOC, {})

        assert len(fragments) == 1
        assert "这是前言内容" in fragments[0]["content"]
        assert "正式内容开始" in fragments[0]["content"]

    def test_empty_document(self):
        """空文档返回空列表。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(EMPTY_DOC, {})
        assert fragments == []

    def test_fragments_are_consecutive(self):
        """片段内容不应该重叠，且拼接后等于原文（忽略多余空行）。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(HEADING_DOC, {})

        combined = "\n".join(f["content"] for f in fragments)
        # 每个原始行至少在 combined 中出现一次
        for line in HEADING_DOC.split("\n"):
            if line.strip():
                assert line.strip() in combined, f"行 '{line}' 在切分后丢失"

    def test_deep_headings_produce_tree(self):
        """多级标题产出正确数量的片段。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(DEEP_HEADING_DOC, {})

        # # L1 → 包含所有内容的一个片段（因为我们按最高级标题切分）
        # 或者按每个 # 标题都切分
        assert len(fragments) >= 1
        assert "L1" in fragments[0]["content"]

    def test_config_passed_through(self):
        """config 参数传递到策略内部。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        config = {"split": {"mode": "heading", "outline": {"max_depth": 3}}}
        fragments = strategy.split(HEADING_DOC, config)
        assert len(fragments) == 4  # max_depth 不影响切分粒度

    def test_outline_produced(self):
        """strateg.split() 产出的 outline 正确。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(HEADING_DOC, {})

        # fragments 中的 heading 字段记录了每个片段的标题
        assert fragments[0]["heading"] is not None
        assert "第一章" in fragments[0]["heading"]


class TestHeadingSplitIntegration:
    """与 BaseProfile 集成时的行为测试。"""

    def test_strategy_conforms_to_protocol(self):
        """HeadingSplitStrategy 实现 SplitStrategy 协议。"""
        from proseproof.core.strategy import SplitStrategy
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        assert isinstance(strategy, SplitStrategy)

    def test_fragments_suitable_for_write(self):
        """产出的片段可以直接传给 _write_fragments_to_dirs。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split(HEADING_DOC, {})

        # _write_fragments_to_dirs 期望每个 frag 有 "content"
        for i, frag in enumerate(fragments):
            assert "content" in frag, f"fragment {i} missing content"
            assert isinstance(frag["content"], str)


class TestHeadingSplitEdgeCases:
    """边界情况。"""

    def test_only_hashes_no_text(self):
        """只有 # 没有实质标题文本。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split("#\n\n内容", {})
        assert len(fragments) == 1

    def test_heading_without_body(self):
        """标题后没有内容。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split("# 孤立标题\n\n## 有内容的标题\n\n正文", {})
        assert len(fragments) >= 1

    def test_consecutive_headings(self):
        """连续的标题（无正文分隔）。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy

        strategy = HeadingSplitStrategy()
        fragments = strategy.split("# A\n## B\n## C\n\n正文", {})
        # B 和 C 可能被合并或分开
        assert len(fragments) >= 1
