"""v0.2.0 Strategy 协议 —— 每个 Pipeline 阶段的可替换实现。

定义:
  - SplitStrategy:      分割阶段的策略接口
  - ProofreadStrategy:  校对阶段的策略接口

设计决策见 ADR-0006 (Strategy + Middleware 双层架构)。
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from proseproof.core.middleware import ProofreadContext, MiddlewareResult


# ============================================================
# SplitStrategy —— 分割阶段的策略协议
# ============================================================

@runtime_checkable
class SplitStrategy(Protocol):
    """分割阶段的策略接口。

    实现类接收文档内容和配置，返回片段列表。
    每个片段是一个 dict，至少包含 "content" 字段。
    """

    def split(self, content: str, config: dict) -> list[dict]:
        """将文档全文切分为若干片段。

        Args:
            content: Markdown 格式的文档全文。
            config:  Profile 配置的完整字典（含 split / proofread / review sections）。

        Returns:
            片段列表，每个元素为 dict，需含 "content" 字段。
            如无法切分（如空文档），返回空列表。
        """
        ...


# ============================================================
# ProofreadStrategy —— 校对阶段的策略协议
# ============================================================

@runtime_checkable
class ProofreadStrategy(Protocol):
    """校对阶段的策略接口。

    实现类接收 ProofreadContext，执行 LLM 校对或自定义校对逻辑，
    返回 MiddlewareResult 表示校对结果和控制意图。
    """

    def proofread(self, ctx: ProofreadContext) -> MiddlewareResult:
        """对单个片段执行校对。

        Args:
            ctx: 校对上下文，含片段原文、提示词、工具集等。

        Returns:
            MiddlewareResult，其 context 中的 raw_response 等字段
            应被填充为 LLM 校对的实际产出。
        """
        ...
