"""v0.2.0 Middleware 链执行器 —— Proofread 阶段的中间件调度引擎。

设计决策见 ADR-0006 (Strategy + Middleware 双层架构)。
"""
from __future__ import annotations
from proseproof.core.middleware import (
    ProofreadContext, MiddlewareAction, MiddlewareResult, ProofreadMiddleware,
)
from proseproof.core.logging_utils import log


class FragmentAbortedError(Exception):
    """中间件要求中止当前片段处理。"""
    pass


def run_middleware_chain(
    ctx: ProofreadContext,
    chain: list[ProofreadMiddleware],
    max_retries: int = 3,
) -> ProofreadContext:
    """按顺序执行 Middleware 链。

    执行逻辑:
      1. 分离 pre 和 post 中间件，先执行所有 pre，再执行所有 post。
      2. pre 阶段: SKIP_LLM → 跳出循环（不再执行后续 pre）。
      3. post 阶段: 始终执行（即使 SKIP_LLM）。
      4. RECHECK → 重试最多 max_retries 次。
      5. ABORT → 抛出 FragmentAbortedError。
      6. CONTINUE → 继续下一个。

    Args:
        ctx:         校对上下文。
        chain:       按注册顺序排列的中间件列表。
        max_retries: RECHECK 最大重试次数。

    Returns:
        处理后的 ProofreadContext。

    Raises:
        FragmentAbortedError: 中间件返回 ABORT 时抛出。
    """
    if not chain:
        return ctx

    # 分离 pre 和 post，保持各自组内的原始顺序
    pre_chain = [mw for mw in chain if mw.phase == "pre"]
    post_chain = [mw for mw in chain if mw.phase == "post"]

    # ---- pre 阶段 ----
    retries = 0
    i = 0
    while i < len(pre_chain):
        mw = pre_chain[i]
        result = mw.process(ctx)
        ctx = result.context

        if result.action == MiddlewareAction.ABORT:
            raise FragmentAbortedError(
                f"[{mw.name}] 中止: {result.message or '无详细原因'}"
            )
        elif result.action == MiddlewareAction.SKIP_LLM:
            ctx.skip_llm = True
            log(f"   ⏭️ [{mw.name}] 跳过 LLM: {result.message}")
            break  # 跳出 pre 循环，执行 post
        elif result.action == MiddlewareAction.RECHECK:
            if retries < max_retries:
                retries += 1
                log(f"   🔄 [{mw.name}] 要求重试 ({retries}/{max_retries}): {result.message}")
                continue  # 不递增 i，重试同一个中间件
            else:
                log(f"   ⚠️ [{mw.name}] 重试次数耗尽，继续处理")
                i += 1
        else:  # CONTINUE
            i += 1

    # ---- post 阶段 ----
    for mw in post_chain:
        result = mw.process(ctx)
        ctx = result.context

        if result.action == MiddlewareAction.ABORT:
            raise FragmentAbortedError(
                f"[{mw.name}] 中止: {result.message or '无详细原因'}"
            )
        # post 阶段不支持 RECHECK（LLM 已经调用过了）
        # 其他 action 直接忽略，继续下一个

    return ctx
