"""TDD: 测试 Strategy 协议 —— SplitStrategy, ProofreadStrategy。"""
import pytest
from typing import Protocol


class TestSplitStrategyProtocol:
    """SplitStrategy 协议的契约测试。"""

    def test_minimal_split_strategy_conforms(self):
        """实现 split(content, config) 方法即满足协议。"""
        from proseproof.core.strategy import SplitStrategy

        class HeadingSplit:
            def split(self, content: str, config: dict) -> list:
                return [{"content": content}]

        strategy = HeadingSplit()
        assert isinstance(strategy, SplitStrategy)

    def test_split_returns_fragments(self):
        """split() 返回片段列表，每个片段是 dict。"""
        from proseproof.core.strategy import SplitStrategy

        class SmartSplit:
            def split(self, content: str, config: dict) -> list:
                return [
                    {"content": "片段1", "fragment_id": "frag_001"},
                    {"content": "片段2", "fragment_id": "frag_002"},
                ]

        strategy = SmartSplit()
        result = strategy.split("全文", {})
        assert len(result) == 2
        assert result[0]["content"] == "片段1"
        assert result[1]["fragment_id"] == "frag_002"

    def test_empty_document_returns_empty_list(self):
        """空文档返回空列表而不崩溃。"""
        from proseproof.core.strategy import SplitStrategy

        class RobustSplit:
            def split(self, content: str, config: dict) -> list:
                if not content.strip():
                    return []
                return [{"content": content}]

        strategy = RobustSplit()
        result = strategy.split("   ", {})
        assert result == []

    def test_config_passed_through(self):
        """config 参数完整传递到策略内部。"""
        from proseproof.core.strategy import SplitStrategy

        call_record = {}

        class ConfigAwareSplit:
            def split(self, content: str, config: dict) -> list:
                call_record["config"] = config
                return []

        strategy = ConfigAwareSplit()
        my_config = {"split": {"mode": "heading", "outline": {"max_depth": 3}}}
        strategy.split("", my_config)
        assert call_record["config"] == my_config


class TestProofreadStrategyProtocol:
    """ProofreadStrategy 协议的契约测试。"""

    def test_minimal_proofread_strategy_conforms(self):
        """实现 proofread(ctx) 方法即满足协议。"""
        from proseproof.core.strategy import ProofreadStrategy
        from proseproof.core.middleware import ProofreadContext, MiddlewareResult

        class DefaultProofread:
            def proofread(self, ctx: ProofreadContext) -> MiddlewareResult:
                ctx.raw_response = "校对完成"
                from proseproof.core.middleware import MiddlewareAction
                return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)

        ctx = ProofreadContext(
            fragment_text="原文",
            fragment_id="frag_001",
            images=[],
            prompt="校对",
            tools=[],
            config={},
        )
        strategy = DefaultProofread()
        result = strategy.proofread(ctx)
        assert result.context.raw_response == "校对完成"
        assert isinstance(strategy, ProofreadStrategy)

    def test_strategy_can_fail(self):
        """策略可以返回失败。"""
        from proseproof.core.strategy import ProofreadStrategy
        from proseproof.core.middleware import ProofreadContext, MiddlewareAction, MiddlewareResult

        class FailingProofread:
            def proofread(self, ctx: ProofreadContext) -> MiddlewareResult:
                return MiddlewareResult(ctx, MiddlewareAction.ABORT, "API 不可用")

        ctx = ProofreadContext(
            fragment_text="x", fragment_id="x", images=[], prompt="x",
            tools=[], config={},
        )
        strategy = FailingProofread()
        result = strategy.proofread(ctx)
        assert result.action == MiddlewareAction.ABORT
