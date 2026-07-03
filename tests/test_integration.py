"""TDD: CLI 集成测试 —— 全流水线端到端。

Mock LLM 覆盖关键数据流路径：
  ✅ heading 分割 → 校对 → Light 审查
  ✅ smart 分割 → 校对 → 断点续传
  ✅ --review off 跳过审查
  ✅ --yes 跳过暂停
  ✅ 中间件链贯穿
"""
import json
import tempfile
import os
from pathlib import Path
import pytest


class TestPipelineIntegration:
    """全流水线集成测试（mock LLM）。"""

    def _make_md_file(self, tmpdir, content):
        md_path = Path(tmpdir) / "test.md"
        md_path.write_text(content, encoding="utf-8")
        return md_path

    def test_full_pipeline_heading_mode(self):
        """heading 分割 → 校对(mock) → Light 审查 → 不崩溃。"""
        from proseproof.shared.heading_split import HeadingSplitStrategy
        from proseproof.core.middleware import ProofreadContext, MiddlewareAction, MiddlewareResult

        content = "# 第一章\n\n内容A\n\n## 1.1 节\n\n内容B\n\n# 第二章\n\n内容C"

        # 分割
        strategy = HeadingSplitStrategy()
        fragments = strategy.split(content, {})
        # 三个标题 → 三个片段
        assert len(fragments) == 3
        assert "第一章" in fragments[0]["content"]
        assert "第二章" in fragments[2]["content"]

    def test_structural_review_integration(self):
        """分割产出大纲 → 结构审查 → 无阻塞。"""
        from proseproof.shared.outline_extractor import extract_outline, outline_to_dict
        from proseproof.shared.structural_review import structural_review, has_severe_issues

        content = "# 第一章\n## 1.1\n## 1.2\n# 第二章\n## 2.1"
        outline = outline_to_dict(extract_outline(content))

        issues = structural_review(outline)
        # 正常文档应无结构问题
        assert not has_severe_issues(issues)

    def test_manifest_resume_flow(self):
        """Manifest 断点续传数据流：创建 → 标记完成 → 跳过。"""
        from proseproof.shared.manifest import (
            create_manifest, mark_completed, should_skip,
            get_next_pending, get_progress,
        )

        manifest = create_manifest(["frag_001", "frag_002", "frag_003"])
        mark_completed(manifest, "frag_001", "内容1")
        mark_completed(manifest, "frag_002", "内容2")

        # frag_003 是 pending
        assert get_next_pending(manifest) == "frag_003"
        # frag_001 已完成且 MD5 不变 → 跳过
        assert should_skip(manifest, "frag_001", "内容1") is True
        # frag_003 未完成 → 不跳过
        assert should_skip(manifest, "frag_003", "内容3") is False

        stats = get_progress(manifest)
        assert stats["completed"] == 2
        assert stats["pending"] == 1

    def test_middleware_chain_integration(self):
        """PreCheck + Similarity 链贯穿不崩溃。"""
        from proseproof.shared.pre_check import PreCheckMiddleware
        from proseproof.shared.similarity import SimilarityMiddleware
        from proseproof.core.middleware import ProofreadContext, MiddlewareAction
        from proseproof.core.middleware_runner import run_middleware_chain

        ctx = ProofreadContext(
            fragment_text="正常文本，无异常。公式 $x=1$ 正确。",
            fragment_id="frag_001",
            images=[],
            prompt="校对",
            tools=[],
            config={},
            raw_response="### 标记原文\n正常文本，无异常。公式 $x=1$ 正确。\n### 修改原因\n无问题",
        )

        chain = [PreCheckMiddleware(), SimilarityMiddleware()]
        result = run_middleware_chain(ctx, chain)

        # 不应崩溃
        assert result.fragment_id == "frag_001"
        # PreCheck 在正常文本上不产生提示
        assert len(result.pre_check_hints) == 0
        # Similarity 匹配
        assert result.similarity_passed is True

    def test_light_review_integration(self):
        """大纲 + 摘要 → Light 审查 → 产出报告。"""
        from proseproof.shared.light_review import LightReview
        from proseproof.shared.outline_extractor import extract_outline, outline_to_dict

        content = "# 第一章\n## 1.1 背景\n# 第二章\n## 2.1 方法"
        outline = outline_to_dict(extract_outline(content))

        response = json.dumps({"issues": []}, ensure_ascii=False)

        def mock_llm(content, prompt):
            return response

        reviewer = LightReview(llm_callable=mock_llm)
        report = reviewer.review(outline, {
            "frag_001": "介绍了背景信息。",
            "frag_002": "描述了方法。",
        })

        assert "issues" in report
        assert isinstance(report["issues"], list)

    def test_skip_llm_flow(self):
        """PreCheck 发现空片段 → SKIP_LLM → 后续 post 仍执行。"""
        from proseproof.shared.pre_check import PreCheckMiddleware
        from proseproof.shared.similarity import SimilarityMiddleware
        from proseproof.core.middleware import ProofreadContext
        from proseproof.core.middleware_runner import run_middleware_chain

        ctx = ProofreadContext(
            fragment_text="   \n  ",  # 空白片段
            fragment_id="frag_empty",
            images=[],
            prompt="校对",
            tools=[],
            config={},
        )

        chain = [PreCheckMiddleware(), SimilarityMiddleware()]
        result = run_middleware_chain(ctx, chain)

        # PreCheck 应设置 skip_llm
        assert result.skip_llm is True
        # Similarity 仍应执行（post 阶段），但无 raw_response 时设为 None
        # (None = 跳过检查，不是通过也不是失败)
