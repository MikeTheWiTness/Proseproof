"""TDD: 测试 Middleware 类型体系 —— ProofreadContext, MiddlewareAction, MiddlewareResult, ProofreadMiddleware 协议。"""
import pytest
from dataclasses import dataclass, field
from typing import Protocol, Literal, runtime_checkable


# ============================================================
# 先写测试，再写实现。以下 import 在 Slice #1 实现后生效。
# ============================================================

class TestProofreadContext:
    """ProofreadContext 数据载体的契约测试。"""

    def test_context_holds_pipeline_fields(self):
        """管道注入字段（不可变，由 Pipeline 传入）。"""
        from proseproof.core.middleware import ProofreadContext

        ctx = ProofreadContext(
            fragment_text="原文内容",
            fragment_id="frag_003",
            images=[],
            prompt="校对提示词",
            tools=[],
            config={},
        )
        assert ctx.fragment_text == "原文内容"
        assert ctx.fragment_id == "frag_003"
        assert ctx.images == []
        assert ctx.prompt == "校对提示词"
        assert ctx.tools == []
        assert ctx.config == {}

    def test_context_holds_llm_output_fields_defaults(self):
        """LLM 产出字段有合理的默认值。"""
        from proseproof.core.middleware import ProofreadContext

        ctx = ProofreadContext(
            fragment_text="x",
            fragment_id="x",
            images=[],
            prompt="x",
            tools=[],
            config={},
        )
        assert ctx.raw_response == ""
        assert ctx.tool_calls_log == []
        assert ctx.reasoning == ""
        assert ctx.usage == {}

    def test_context_holds_middleware_attachments(self):
        """Middleware 附加字段可读写。"""
        from proseproof.core.middleware import ProofreadContext

        ctx = ProofreadContext(
            fragment_text="x",
            fragment_id="x",
            images=[],
            prompt="x",
            tools=[],
            config={},
        )
        # PreCheck 写入
        ctx.pre_check_hints = [{"line": 12, "pattern": "consecutive_punctuation", "text": "。。"}]
        assert len(ctx.pre_check_hints) == 1
        assert ctx.pre_check_hints[0]["pattern"] == "consecutive_punctuation"

        # Similarity 写入
        ctx.similarity_passed = True
        assert ctx.similarity_passed is True

        # LLMVerify 写入
        ctx.verification_result = "confirmed"
        assert ctx.verification_result == "confirmed"

    def test_context_holds_control_signals_defaults(self):
        """控制信号字段有安全的默认值。"""
        from proseproof.core.middleware import ProofreadContext

        ctx = ProofreadContext(
            fragment_text="x",
            fragment_id="x",
            images=[],
            prompt="x",
            tools=[],
            config={},
        )
        assert ctx.skip_llm is False
        assert ctx.reject_result is False

    def test_summary_field_default(self):
        """校对摘要字段默认为空字符串。"""
        from proseproof.core.middleware import ProofreadContext

        ctx = ProofreadContext(
            fragment_text="x",
            fragment_id="x",
            images=[],
            prompt="x",
            tools=[],
            config={},
        )
        assert ctx.summary == ""


class TestMiddlewareAction:
    """MiddlewareAction 枚举的契约测试。"""

    def test_all_actions_defined(self):
        """四种动作全部定义。"""
        from proseproof.core.middleware import MiddlewareAction

        assert MiddlewareAction.CONTINUE.value == "continue"
        assert MiddlewareAction.SKIP_LLM.value == "skip_llm"
        assert MiddlewareAction.RECHECK.value == "recheck"
        assert MiddlewareAction.ABORT.value == "abort"

    def test_action_is_enum(self):
        """MiddlewareAction 是枚举类型。"""
        from proseproof.core.middleware import MiddlewareAction
        from enum import Enum

        assert issubclass(MiddlewareAction, Enum)

    def test_action_from_string(self):
        """支持从字符串构造。"""
        from proseproof.core.middleware import MiddlewareAction

        assert MiddlewareAction("continue") == MiddlewareAction.CONTINUE
        assert MiddlewareAction("skip_llm") == MiddlewareAction.SKIP_LLM
        assert MiddlewareAction("recheck") == MiddlewareAction.RECHECK
        assert MiddlewareAction("abort") == MiddlewareAction.ABORT


class TestMiddlewareResult:
    """MiddlewareResult 数据类的契约测试。"""

    def test_result_holds_context_action_message(self):
        from proseproof.core.middleware import (
            ProofreadContext, MiddlewareAction, MiddlewareResult,
        )

        ctx = ProofreadContext(
            fragment_text="原文",
            fragment_id="frag_001",
            images=[],
            prompt="校",
            tools=[],
            config={},
        )
        result = MiddlewareResult(
            context=ctx,
            action=MiddlewareAction.CONTINUE,
            message="一切正常",
        )
        assert result.context.fragment_id == "frag_001"
        assert result.action == MiddlewareAction.CONTINUE
        assert result.message == "一切正常"

    def test_result_default_message_is_empty(self):
        """message 默认为空字符串。"""
        from proseproof.core.middleware import (
            ProofreadContext, MiddlewareAction, MiddlewareResult,
        )

        ctx = ProofreadContext(
            fragment_text="x", fragment_id="x", images=[], prompt="x",
            tools=[], config={},
        )
        result = MiddlewareResult(context=ctx, action=MiddlewareAction.SKIP_LLM)
        assert result.message == ""


class TestProofreadMiddlewareProtocol:
    """ProofreadMiddleware 协议的契约测试。"""

    def test_minimal_middleware_conforms(self):
        """实现 name, phase, process 三个成员即满足协议。"""
        from proseproof.core.middleware import (
            ProofreadContext, MiddlewareResult, MiddlewareAction,
        )

        class MinimalMiddleware:
            name = "minimal"
            phase = "pre"

            def process(self, ctx: ProofreadContext) -> MiddlewareResult:
                return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)

        mw = MinimalMiddleware()
        assert mw.name == "minimal"
        assert mw.phase == "pre"

        ctx = ProofreadContext(
            fragment_text="x", fragment_id="x", images=[], prompt="x",
            tools=[], config={},
        )
        result = mw.process(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    def test_pre_phase_middleware(self):
        """pre 阶段中间件在 LLM 调用前执行。"""
        from proseproof.core.middleware import (
            ProofreadContext, MiddlewareResult, MiddlewareAction,
        )

        class PreMiddleware:
            name = "pre_check"
            phase = "pre"

            def process(self, ctx: ProofreadContext) -> MiddlewareResult:
                ctx.skip_llm = True
                return MiddlewareResult(ctx, MiddlewareAction.SKIP_LLM, "空片段跳过")

        ctx = ProofreadContext(
            fragment_text="", fragment_id="x", images=[], prompt="x",
            tools=[], config={},
        )
        mw = PreMiddleware()
        result = mw.process(ctx)
        assert result.action == MiddlewareAction.SKIP_LLM
        assert result.context.skip_llm is True

    def test_post_phase_middleware(self):
        """post 阶段中间件在 LLM 调用后执行。"""
        from proseproof.core.middleware import (
            ProofreadContext, MiddlewareResult, MiddlewareAction,
        )

        class PostMiddleware:
            name = "similarity"
            phase = "post"

            def process(self, ctx: ProofreadContext) -> MiddlewareResult:
                if not ctx.raw_response:
                    return MiddlewareResult(ctx, MiddlewareAction.RECHECK, "无 LLM 返回")
                return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)

        ctx = ProofreadContext(
            fragment_text="原文", fragment_id="x", images=[], prompt="x",
            tools=[], config={},
        )
        mw = PostMiddleware()
        # 无 raw_response → RECHECK
        result = mw.process(ctx)
        assert result.action == MiddlewareAction.RECHECK
        # 有 raw_response → CONTINUE
        ctx.raw_response = "校对完成"
        result = mw.process(ctx)
        assert result.action == MiddlewareAction.CONTINUE

    def test_abort_action_stops_pipeline(self):
        """ABORT 动作应立即停止处理。"""
        from proseproof.core.middleware import (
            ProofreadContext, MiddlewareResult, MiddlewareAction,
        )

        class AbortMiddleware:
            name = "abort_check"
            phase = "pre"

            def process(self, ctx: ProofreadContext) -> MiddlewareResult:
                return MiddlewareResult(ctx, MiddlewareAction.ABORT, "不可校对内容")

        ctx = ProofreadContext(
            fragment_text="纯代码无文本",
            fragment_id="x", images=[], prompt="x",
            tools=[], config={},
        )
        mw = AbortMiddleware()
        result = mw.process(ctx)
        assert result.action == MiddlewareAction.ABORT
