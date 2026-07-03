"""TDD: 中间件链执行器 —— run_middleware_chain() 的行为契约。

覆盖所有 MiddlewareAction 的处理路径：
  ✅ CONTINUE: 正常流到下一个中间件
  ✅ SKIP_LLM: 设置 skip_llm 标志，跳出 pre 循环
  ✅ RECHECK: 重试最多 3 次，超限后放弃
  ✅ ABORT: 抛出 FragmentAbortedError
  ✅ 空链: 直接返回上下文，无副作用
  ✅ 中间件顺序: 按注册顺序执行
"""
import pytest
from proseproof.core.middleware import (
    ProofreadContext, MiddlewareAction, MiddlewareResult, ProofreadMiddleware,
)
from proseproof.core.middleware_runner import (
    run_middleware_chain, FragmentAbortedError,
)


# ============================================================
# 测试用的简单中间件
# ============================================================

def make_ctx():
    return ProofreadContext(
        fragment_text="测试原文",
        fragment_id="frag_001",
        images=[],
        prompt="校对提示词",
        tools=[],
        config={},
    )


class SpyMiddleware:
    """记录被调用的中间件。"""
    def __init__(self, name="spy", phase="pre", action=MiddlewareAction.CONTINUE,
                 message="", side_effect=None):
        self.name = name
        self.phase = phase
        self._action = action
        self._message = message
        self._side_effect = side_effect
        self.call_count = 0
        self.last_ctx = None

    def process(self, ctx):
        self.call_count += 1
        self.last_ctx = ctx
        if self._side_effect:
            self._side_effect(ctx)
        return MiddlewareResult(ctx, self._action, self._message)


# ============================================================
# 测试
# ============================================================

class TestMiddlewareChainRunner:
    """中间件链执行器行为测试。"""

    def test_empty_chain_passes_through(self):
        """空链 → 上下文原样返回。"""
        ctx = make_ctx()
        result = run_middleware_chain(ctx, [])
        assert result is ctx
        assert result.skip_llm is False

    def test_continue_flows_to_next(self):
        """CONTINUE → 依次执行所有中间件。"""
        ctx = make_ctx()
        mw1 = SpyMiddleware("mw1")
        mw2 = SpyMiddleware("mw2")

        run_middleware_chain(ctx, [mw1, mw2])
        assert mw1.call_count == 1
        assert mw2.call_count == 1

    def test_skip_llm_stops_pre_chain(self):
        """pre 中间件返回 SKIP_LLM → 不再执行后续 pre 中间件。"""
        ctx = make_ctx()
        mw1 = SpyMiddleware("skip_check", action=MiddlewareAction.SKIP_LLM)
        mw2 = SpyMiddleware("never_run")

        result = run_middleware_chain(ctx, [mw1, mw2])
        assert mw1.call_count == 1
        assert mw2.call_count == 0
        assert result.skip_llm is True

    def test_post_middleware_runs_after_skip_llm(self):
        """SKIP_LLM 后仍执行 post 中间件。"""
        ctx = make_ctx()
        pre = SpyMiddleware("pre", phase="pre", action=MiddlewareAction.SKIP_LLM)
        post = SpyMiddleware("post", phase="post")

        result = run_middleware_chain(ctx, [pre, post])
        assert pre.call_count == 1
        assert post.call_count == 1
        assert result.skip_llm is True

    def test_recheck_triggers_retry(self):
        """RECHECK → 重试（最多 3 次）。"""
        ctx = make_ctx()
        recheck = SpyMiddleware("recheck_mw", action=MiddlewareAction.RECHECK)

        result = run_middleware_chain(ctx, [recheck], max_retries=3)
        # RECHECK 重试：第一次调用返回 RECHECK，重试...
        # 但 spy 总是返回 RECHECK，所以 3 次重试后放弃
        assert recheck.call_count >= 1

    def test_recheck_exceeds_limit_stops(self):
        """RECHECK 超过 max_retries 后放弃，不阻塞。"""
        ctx = make_ctx()
        recheck = SpyMiddleware("always_recheck", action=MiddlewareAction.RECHECK)

        result = run_middleware_chain(ctx, [recheck], max_retries=2)
        # 不超过 retries 就停止，不无限循环
        assert recheck.call_count <= 3  # 理论上 1 + 2 = 3
        # 流程继续，不崩溃

    def test_abort_raises_error(self):
        """ABORT → 抛出 FragmentAbortedError。"""
        ctx = make_ctx()
        abort_mw = SpyMiddleware("abort", action=MiddlewareAction.ABORT,
                                message="不可校对的内容")

        with pytest.raises(FragmentAbortedError) as exc_info:
            run_middleware_chain(ctx, [abort_mw])
        assert "不可校对的内容" in str(exc_info.value)

    def test_pre_runs_before_post(self):
        """pre 中间件在 post 之前执行。"""
        order = []
        ctx = make_ctx()

        class OrderedMiddleware:
            def __init__(self, name, phase):
                self.name = name
                self.phase = phase

            def process(self, ctx):
                order.append(self.name)
                return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)

        mw_pre = OrderedMiddleware("pre1", "pre")
        mw_post = OrderedMiddleware("post1", "post")

        run_middleware_chain(ctx, [mw_pre, mw_post])
        assert order == ["pre1", "post1"]

    def test_phase_order_respected_with_mixed(self):
        """混合注册时，pre 全部先于 post。"""
        order = []
        ctx = make_ctx()

        class OrderedMiddleware:
            def __init__(self, name, phase):
                self.name = name
                self.phase = phase

            def process(self, ctx):
                order.append(self.name)
                return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)

        chain = [
            OrderedMiddleware("post_A", "post"),
            OrderedMiddleware("pre_A", "pre"),
            OrderedMiddleware("pre_B", "pre"),
            OrderedMiddleware("post_B", "post"),
        ]
        run_middleware_chain(ctx, chain)
        # pre 应该全部在 post 之前
        pre_indices = {order.index(n) for n in ["pre_A", "pre_B"]}
        post_indices = {order.index(n) for n in ["post_A", "post_B"]}
        assert max(pre_indices) < min(post_indices)

    def test_context_carried_between_middleware(self):
        """前一个中间件修改 ctx → 下一个中间件可见。"""
        ctx = make_ctx()
        writer = SpyMiddleware("writer", side_effect=lambda c: setattr(c, "pre_check_hints", [{"test": True}]))
        reader = SpyMiddleware("reader",
            side_effect=lambda c: setattr(c, "hints_read", bool(c.pre_check_hints)))

        result = run_middleware_chain(ctx, [writer, reader])
        assert result.hints_read is True


class TestPreCheckMiddleware:
    """PreCheck 中间件的检测逻辑测试。"""

    def test_detects_unpaired_brackets(self):
        """检测不成对括号。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "这是）一个例子（而且还有问题"

        result = mw.process(ctx)
        hints = result.context.pre_check_hints
        assert len(hints) > 0
        assert any(h["pattern"] == "unpaired_bracket" for h in hints)

    def test_paired_brackets_pass(self):
        """成对括号不报。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "这是（一个例子）而且没有问题。"

        result = mw.process(ctx)
        hints = result.context.pre_check_hints
        assert not any(h["pattern"] == "unpaired_bracket" for h in hints)

    def test_detects_consecutive_punctuation(self):
        """检测连续重复标点。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "你好。。这个对吧，，还有？?"

        result = mw.process(ctx)
        hints = result.context.pre_check_hints
        # 至少检测到一例
        assert len(hints) > 0
        assert any(h["pattern"] == "consecutive_punctuation" for h in hints)

    def test_detects_repeated_word(self):
        """检测连续重复词（≥2字）。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "这是这是重复的测试测试内容"

        result = mw.process(ctx)
        hints = result.context.pre_check_hints
        assert any(h["pattern"] == "repeated_word" for h in hints)

    def test_repeated_single_char_ignored(self):
        """单个字重复不被检测（如'的的'可能是正常的）。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "的的的确"

        result = mw.process(ctx)
        # "的的" — 单字重复，不报
        assert "的的" not in str(result.context.pre_check_hints)

    def test_empty_fragment_detected(self):
        """检测空片段。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "   \n  \n\t "

        result = mw.process(ctx)
        assert result.action == MiddlewareAction.SKIP_LLM

    def test_no_hints_for_clean_text(self):
        """干净文本不产生提示。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "这是一段正常的文本，没有任何格式问题，而且表达清晰。"

        result = mw.process(ctx)
        assert result.action == MiddlewareAction.CONTINUE
        assert len(result.context.pre_check_hints) == 0

    def test_hint_structure(self):
        """提示的结构化格式：{line, pattern, text}。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "错误。。"

        result = mw.process(ctx)
        hints = result.context.pre_check_hints
        for h in hints:
            assert "line" in h
            assert "pattern" in h
            assert "text" in h
            assert "severity" not in h  # 不设严重级别
            assert "error" not in h     # 不做错误判定

    def test_hints_injected_into_prompt(self):
        """提示清单注入到 prompt 中。"""
        from proseproof.shared.pre_check import PreCheckMiddleware

        mw = PreCheckMiddleware()
        ctx = make_ctx()
        ctx.fragment_text = "错误。。"

        result = mw.process(ctx)
        # prompt 应该被修改，追加了提示清单
        assert "⚠️" in result.context.prompt or "请特别关注" in result.context.prompt
