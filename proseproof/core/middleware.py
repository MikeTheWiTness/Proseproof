"""v0.2.0 Middleware 类型体系 —— Proofread 阶段内部的可组合校验链。

定义:
  - ProofreadContext:  中间件链的数据载体
  - MiddlewareAction:   中间件返回值语义（CONTINUE / SKIP_LLM / RECHECK / ABORT）
  - MiddlewareResult:   中间件 process() 的返回值
  - ProofreadMiddleware: 中间件的协议接口

设计决策见 ADR-0006 (Strategy + Middleware 双层架构)。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Literal


# ============================================================
# ProofreadContext —— 中间件链的统一数据载体
# ============================================================

@dataclass
class ProofreadContext:
    """校对上下文，在 Middleware 链中传递。

    字段分四类：
    ① 管道注入（不可变，由 Pipeline 传入）
    ② LLM 产出（由核心 proofread 逻辑填充）
    ③ Middleware 附加（各中间件写入）
    ④ 控制信号（Middleware 可以设置来影响后续行为）
    """

    # ---- ① 管道注入字段（必填） ----
    fragment_text: str
    fragment_id: str
    images: list
    prompt: str
    tools: list
    config: dict

    # ---- ② LLM 产出字段（由 proofread 核心填充，默认值） ----
    raw_response: str = ""
    tool_calls_log: list = field(default_factory=list)
    reasoning: str = ""
    usage: dict = field(default_factory=dict)

    # ---- ③ Middleware 附加字段（各中间件写入） ----
    pre_check_hints: list = field(default_factory=list)
    similarity_passed: bool | None = None
    verification_result: str | None = None

    # ---- ④ 控制信号 ----
    skip_llm: bool = False
    reject_result: bool = False

    # ---- 校对摘要（Proofread 阶段顺手产出，供内容审查消费） ----
    summary: str = ""


# ============================================================
# MiddlewareAction —— 中间件返回值语义
# ============================================================

class MiddlewareAction(Enum):
    """中间件 process() 的返回值语义，由 Pipeline 统一处理。

    CONTINUE  : 正常流转到下一个中间件
    SKIP_LLM  : 跳过 LLM 调用，但仍执行后续 post 中间件
    RECHECK   : 要求重新校对当前片段（最多 3 次）
    ABORT     : 中止当前片段的处理
    """
    CONTINUE = "continue"
    SKIP_LLM = "skip_llm"
    RECHECK = "recheck"
    ABORT = "abort"


# ============================================================
# MiddlewareResult —— 中间件返回值
# ============================================================

@dataclass
class MiddlewareResult:
    """中间件 process() 的返回值。

    context: 可能被中间件修改过的上下文
    action:  控制信号
    message: 人类可读的原因说明，记录到日志
    """
    context: ProofreadContext
    action: MiddlewareAction
    message: str = ""


# ============================================================
# ProofreadMiddleware —— 中间件协议
# ============================================================

class ProofreadMiddleware(Protocol):
    """Proofread 阶段内部中间件的协议接口。

    每个中间件需要声明:
      - name:  唯一标识名，对应 config.json 中 middleware_chain 的 name 字段
      - phase: "pre"（LLM 之前）或 "post"（LLM 之后）
      - process(): 核心处理逻辑，接收 ProofreadContext，返回 MiddlewareResult
    """
    name: str
    phase: Literal["pre", "post"]

    def process(self, ctx: ProofreadContext) -> MiddlewareResult:
        """处理当前片段。

        Args:
            ctx: 校对上下文，包含片段原文、LLM 产出、中间件附加数据等。

        Returns:
            MiddlewareResult，其中 action 字段控制后续流程。
        """
        ...
