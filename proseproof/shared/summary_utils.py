"""v0.2.0 校对摘要工具 —— 从 Proofread 报告中提取和检测摘要。

摘要格式要求 LLM 在校对报告末尾输出:
  ---
  **大意摘要**：≤50 字的片段内容概括

缺失时通过格式校验 + bash 工具补写（不重新生成整份报告）。
"""
from __future__ import annotations
import re


SUMMARY_MAX_LENGTH = 50
_SUMMARY_MARKER = "**大意摘要**"


def extract_summary(response: str) -> str | None:
    """从 LLM 校对报告中提取摘要文本。

    支持两种格式:
      ---\n**大意摘要**：xxx
      或单独一行 **大意摘要**：xxx

    Returns:
        摘要文本，或 None（无摘要/摘要为空）。
    """
    if not response:
        return None

    # 查找 **大意摘要** 标记
    idx = response.rfind(_SUMMARY_MARKER)
    if idx == -1:
        return None

    # 从标记后提取文本
    after_marker = response[idx + len(_SUMMARY_MARKER):]
    # 去除冒号
    after_marker = after_marker.lstrip('：:').strip()

    if not after_marker:
        return None

    # 截断到摘要最大长度
    if len(after_marker) > SUMMARY_MAX_LENGTH:
        after_marker = after_marker[:SUMMARY_MAX_LENGTH] + "..."

    return after_marker


def has_summary(response: str) -> bool:
    """检测校对报告中是否包含摘要字段。"""
    return _SUMMARY_MARKER in (response or "")
