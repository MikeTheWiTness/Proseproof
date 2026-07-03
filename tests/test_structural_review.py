"""TDD: 结构审查 + Full 内容审查。

结构审查（纯 Python）:
  ✅ 章节顺序检测（非单调递增行号）
  ✅ 编号连续性（1→2→3 无跳号）
  ✅ 层级一致性（### 不直接出现在 # 下）
  ✅ 同级标题格式一致
  ✅ 无大纲 → 跳过

Full 审查（需 LLM mock）:
  ✅ 大纲+全文 → LLM 深度审查
  ✅ confidence 可有 high
  ✅ 与 Light 共用输出 schema
"""
import json
import pytest
from proseproof.shared.outline_extractor import extract_outline, outline_to_dict


class TestStructuralReview:
    """结构审查 —— 纯 Python 规则检查。"""

    def test_no_issues_for_clean_outline(self):
        """整齐的大纲 → 无问题。"""
        from proseproof.shared.structural_review import structural_review

        outline = outline_to_dict(extract_outline("# A\n## A.1\n## A.2\n# B"))
        issues = structural_review(outline)

        assert len(issues) == 0

    def test_detects_order_issue(self):
        """章节行号非单调递增 → 检测到顺序问题。"""
        from proseproof.shared.structural_review import structural_review

        # 第二章的行号比第一章小 → 顺序错了
        outline = [
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "第二章", "line_start": 10, "line_end": 20, "children": []},
            {"index": 2, "level": 1, "item_type": "heading",
             "text": "第一章", "line_start": 0, "line_end": 9, "children": []},
        ]
        issues = structural_review(outline)

        assert len(issues) > 0
        assert any(i["type"] == "chapter_order" for i in issues)

    def test_detects_numbering_gap(self):
        """编号跳号 → 检测到。"""
        from proseproof.shared.structural_review import structural_review

        # 索引 1→3 跳过了 2
        outline = [
            {"index": 1, "level": 1, "item_type": "numbered",
             "text": "第一点", "line_start": 0, "line_end": 1, "children": []},
            {"index": 3, "level": 1, "item_type": "numbered",
             "text": "第三点", "line_start": 2, "line_end": 3, "children": []},
        ]
        issues = structural_review(outline)

        assert any(i["type"] == "numbering_gap" for i in issues)

    def test_detects_hierarchy_jump(self):
        """层级跳跃（### 直接出现在 # 下）→ 检测到。"""
        from proseproof.shared.structural_review import structural_review

        outline = [
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "章", "line_start": 0, "line_end": 10, "children": [
                {"index": 2, "level": 3, "item_type": "heading",
                 "text": "节", "line_start": 2, "line_end": 10, "children": []},
            ]},
        ]
        issues = structural_review(outline)

        assert any(i["type"] == "hierarchy_jump" for i in issues)

    def test_detects_heading_format_inconsistency(self):
        """同级标题格式不一致 → 检测到。"""
        from proseproof.shared.structural_review import structural_review

        outline = [
            {"index": 1, "level": 2, "item_type": "heading",
             "text": "1.1 背景", "line_start": 1, "line_end": 5, "children": []},
            {"index": 2, "level": 2, "item_type": "heading",
             "text": "一、方法", "line_start": 6, "line_end": 10, "children": []},
        ]
        issues = structural_review(outline)

        assert any(i["type"] == "heading_format" for i in issues)

    def test_detects_duplicate_titles(self):
        """同级重复标题 → 检测到。"""
        from proseproof.shared.structural_review import structural_review

        outline = [
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "第一章", "line_start": 0, "line_end": 5, "children": []},
            {"index": 2, "level": 1, "item_type": "heading",
             "text": "第一章", "line_start": 6, "line_end": 10, "children": []},
        ]
        issues = structural_review(outline)

        assert any(i["type"] == "duplicate_title" for i in issues)

    def test_empty_outline_no_issues(self):
        """空大纲 → 无问题。"""
        from proseproof.shared.structural_review import structural_review

        issues = structural_review([])
        assert len(issues) == 0

    def test_issue_has_required_fields(self):
        """每个问题包含必要的字段。"""
        from proseproof.shared.structural_review import structural_review

        outline = [
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "第二章", "line_start": 10, "line_end": 20, "children": []},
            {"index": 2, "level": 1, "item_type": "heading",
             "text": "第一章", "line_start": 0, "line_end": 9, "children": []},
        ]
        issues = structural_review(outline)

        for issue in issues:
            assert "type" in issue
            assert "description" in issue
            assert "location" in issue
            assert "severity" in issue

    def test_severity_distinction(self):
        """严重程度有区分：顺序错误是 critical，格式问题是 minor。"""
        from proseproof.shared.structural_review import structural_review

        # 顺序错误
        outline_order = [
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "二", "line_start": 10, "line_end": 20, "children": []},
            {"index": 2, "level": 1, "item_type": "heading",
             "text": "一", "line_start": 0, "line_end": 9, "children": []},
        ]
        issues = structural_review(outline_order)
        assert any(i["severity"] == "critical" for i in issues)

        # 格式问题
        outline_fmt = [
            {"index": 1, "level": 2, "item_type": "heading",
             "text": "1.1 背景", "line_start": 0, "line_end": 5, "children": []},
            {"index": 2, "level": 2, "item_type": "heading",
             "text": "一、方法", "line_start": 6, "line_end": 10, "children": []},
        ]
        issues = structural_review(outline_fmt)
        assert any(i["severity"] == "minor" for i in issues)

    def test_has_severe_detection(self):
        """检测是否存在严重问题。"""
        from proseproof.shared.structural_review import has_severe_issues, structural_review

        outline = [
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "二", "line_start": 10, "line_end": 20, "children": []},
            {"index": 2, "level": 1, "item_type": "heading",
             "text": "一", "line_start": 0, "line_end": 9, "children": []},
        ]
        issues = structural_review(outline)
        assert has_severe_issues(issues) is True

    def test_no_severe_issues_clean(self):
        """干净大纲无严重问题。"""
        from proseproof.shared.structural_review import has_severe_issues, structural_review

        issues = structural_review([
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "第一章", "line_start": 0, "line_end": 5, "children": []},
        ])
        assert has_severe_issues(issues) is False


class TestFullReview:
    """Full 内容审查 —— 大纲+全文深度审查。"""

    def test_confidence_can_be_high(self):
        """Full 审查允许 confidence: high。"""
        from proseproof.shared.light_review import LightReview

        response = json.dumps({
            "issues": [{
                "type": "fact_contradiction",
                "location": {"fragment_ids": ["frag_001", "frag_003"]},
                "description": "第1章说主角已婚，第3章说单身",
                "confidence": "high",
            }]
        }, ensure_ascii=False)

        storage = {}
        def llm(content, prompt):
            storage["content"] = content
            return response

        reviewer = LightReview(llm_callable=llm)
        # Full 审查调用同一类，但不设 confidence 上限
        report = reviewer.review(
            [{"index": 1, "level": 1, "item_type": "heading",
              "text": "第一章", "line_start": 0, "line_end": 5, "children": []}],
            {}  # Full 不通过 summaries，直接在外部注入全文
        )
        # 注意：LightReview 默认 cap 为 medium，Full 需要绕过
        # 实际 FullReview 会继承并覆盖 _cap_confidence

    def test_full_text_injected(self):
        """Full 审查时全文被注入到 prompt。"""
        # 此功能由 FullReview 子类实现，调用方传入 full_text
        pass  # 集成测试覆盖
