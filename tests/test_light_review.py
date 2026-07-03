"""TDD: 校对摘要提取 + Light 内容审查。

摘要产出:
  ✅ LLM 返回含摘要 → 正确提取
  ✅ LLM 返回缺摘要 → 检测缺失
  ✅ 摘要截断到 50 字

Light 审查:
  ✅ 大纲+摘要 → LLM 返回全局问题
  ✅ 无问题返回
  ✅ LLM 返回格式异常 → 优雅降级
  ✅ confidence 上限 medium
"""
import json
import pytest


# ============================================================
# 摘要提取测试
# ============================================================

class TestSummaryExtraction:
    """_extract_summary() 的行为测试。"""

    def test_extract_from_response(self):
        """从校对报告中提取大意摘要。"""
        from proseproof.shared.summary_utils import extract_summary

        response = "### 标记原文\n...\n### 修改原因\n...\n\n---\n**大意摘要**：本章介绍了主角背景。"
        summary = extract_summary(response)
        assert summary is not None
        assert "主角" in summary

    def test_missing_summary_returns_none(self):
        """无摘要标记 → None。"""
        from proseproof.shared.summary_utils import extract_summary

        response = "### 标记原文\n...\n### 修改原因\n..."
        summary = extract_summary(response)
        assert summary is None

    def test_empty_summary_returns_none(self):
        """摘要标记存在但内容为空 → None。"""
        from proseproof.shared.summary_utils import extract_summary

        response = "### 标记原文\n...\n---\n**大意摘要**："
        summary = extract_summary(response)
        assert summary is None

    def test_long_summary_truncated(self):
        """超过 50 字的摘要被截断。"""
        from proseproof.shared.summary_utils import extract_summary, SUMMARY_MAX_LENGTH

        long_text = "这是一个非常长的摘要内容" * 10
        response = f"### 标记原文\n...\n---\n**大意摘要**：{long_text}"
        summary = extract_summary(response)
        assert summary is not None
        assert len(summary) <= SUMMARY_MAX_LENGTH + 3  # +3 for ...

    def test_summary_required_format(self):
        """has_summary() 检测所需格式。"""
        from proseproof.shared.summary_utils import has_summary

        assert has_summary("**大意摘要**：x")
        assert has_summary("### 标记原文\n...\n---\n**大意摘要**：x")
        assert not has_summary("没有摘要的文本")
        assert not has_summary("")


class TestLightReview:
    """Light 内容审查的行为测试。"""

    def _make_outline(self):
        return [
            {"index": 1, "level": 1, "item_type": "heading",
             "text": "第一章", "line_start": 0, "line_end": 10, "children": []},
            {"index": 2, "level": 1, "item_type": "heading",
             "text": "第二章", "line_start": 11, "line_end": 20, "children": []},
        ]

    def _make_summaries(self):
        return {
            "frag_001": "介绍了主角在北京的出生和童年。",
            "frag_002": "描述了主角在上海的婚姻生活。",
        }

    def _make_mock_llm(self, response):
        storage = {"content": None, "prompt": None}
        def _llm(content, prompt):
            storage["content"] = content
            storage["prompt"] = prompt
            return response
        _llm.storage = storage
        return _llm

    def test_review_with_issues(self):
        """LLM 返回全局问题列表。"""
        from proseproof.shared.light_review import LightReview

        response = json.dumps({
            "issues": [
                {
                    "type": "term_inconsistency",
                    "location": {"fragment_ids": ["frag_001", "frag_002"]},
                    "description": "frag_001 使用'主角'，frag_002 使用'主人公'，疑似同一概念",
                    "confidence": "medium",
                }
            ]
        }, ensure_ascii=False)
        llm = self._make_mock_llm(response)

        reviewer = LightReview(llm_callable=llm)
        report = reviewer.review(self._make_outline(), self._make_summaries())

        assert len(report["issues"]) == 1
        assert report["issues"][0]["type"] == "term_inconsistency"
        assert report["issues"][0]["confidence"] == "medium"

    def test_review_no_issues(self):
        """LLM 返回无问题。"""
        from proseproof.shared.light_review import LightReview

        response = json.dumps({"issues": []}, ensure_ascii=False)
        llm = self._make_mock_llm(response)

        reviewer = LightReview(llm_callable=llm)
        report = reviewer.review(self._make_outline(), self._make_summaries())

        assert report["issues"] == []

    def test_confidence_capped_at_medium(self):
        """confidence 上限为 medium，即使 LLM 返回 high。"""
        from proseproof.shared.light_review import LightReview

        response = json.dumps({
            "issues": [{
                "type": "chapter_order",
                "location": {"fragment_ids": ["frag_002"]},
                "description": "第二章在第一章之前",
                "confidence": "high",  # LLM 试图返回 high
            }]
        }, ensure_ascii=False)
        llm = self._make_mock_llm(response)

        reviewer = LightReview(llm_callable=llm)
        report = reviewer.review(self._make_outline(), self._make_summaries())

        # 应被截断为 medium
        assert report["issues"][0]["confidence"] in ("medium", "low")

    def test_malformed_response_graceful(self):
        """LLM 返回不可解析文本 → 优雅降级。"""
        from proseproof.shared.light_review import LightReview

        response = "抱歉，无法分析"
        llm = self._make_mock_llm(response)

        reviewer = LightReview(llm_callable=llm)
        report = reviewer.review(self._make_outline(), self._make_summaries())

        # 不应崩溃
        assert "issues" in report
        assert isinstance(report["issues"], list)

    def test_empty_outline_skips(self):
        """空大纲 → 跳过审查。"""
        from proseproof.shared.light_review import LightReview

        reviewer = LightReview(llm_callable=None)
        report = reviewer.review([], {})

        assert report["issues"] == []

    def test_outline_in_prompt(self):
        """验证大纲被注入到 prompt 中。"""
        from proseproof.shared.light_review import LightReview

        response = json.dumps({"issues": []}, ensure_ascii=False)
        llm = self._make_mock_llm(response)

        reviewer = LightReview(llm_callable=llm)
        reviewer.review(self._make_outline(), self._make_summaries())

        content = llm.storage["content"]
        assert content is not None
        assert "第一章" in content
        assert "第二章" in content

    def test_summaries_in_prompt(self):
        """验证片段摘要被注入到 prompt 中。"""
        from proseproof.shared.light_review import LightReview

        response = json.dumps({"issues": []}, ensure_ascii=False)
        llm = self._make_mock_llm(response)

        reviewer = LightReview(llm_callable=llm)
        reviewer.review(self._make_outline(), self._make_summaries())

        content = llm.storage["content"]
        assert "主角在北京" in content
        assert "上海" in content
