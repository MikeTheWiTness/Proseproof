"""v0.2.0 heading 分割策略 —— 按 Markdown 标题切分文档。

零 LLM 成本，产出大纲，适用于有清晰标题层级的 Markdown 文档。

每个标题（任意层级）成为一个片段的起点，片段内容包含该标题及
其后所有非标题行，直到下一个标题。

设计决策见 ADR-0009 (分割模式矩阵)。
"""
from __future__ import annotations
import re


# 标题匹配模式
_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)


class HeadingSplitStrategy:
    """按 Markdown 标题分割的策略。

    实现 SplitStrategy 协议。
    """

    def split(self, content: str, config: dict) -> list[dict]:
        """按标题边界切分文档为片段列表。

        Args:
            content: Markdown 格式的文档全文。
            config:  Profile 配置字典。

        Returns:
            片段列表，每个 dict 含 content 和 heading 字段。
            无标题时返回整篇文档作为单一片段（等价于 none 模式）。
        """
        if not content or not content.strip():
            return []

        lines = content.split('\n')

        # 找到所有标题行及其位置
        heading_positions: list[tuple[int, int, str]] = []  # (line_idx, level, text)
        for i, line in enumerate(lines):
            m = _HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                heading_positions.append((i, level, line))

        if not heading_positions:
            # 无标题 → 降级为 none 模式
            return [{"content": content, "heading": None}]

        fragments = []

        # 前言（第一个标题之前的内容）
        first_heading_line = heading_positions[0][0]
        preamble = '\n'.join(lines[:first_heading_line]).strip()

        # 按标题切分
        for idx, (line_idx, level, heading_line) in enumerate(heading_positions):
            # 确定当前标题的内容范围
            if idx + 1 < len(heading_positions):
                next_line_idx = heading_positions[idx + 1][0]
                fragment_lines = lines[line_idx:next_line_idx]
            else:
                fragment_lines = lines[line_idx:]

            fragment_content = '\n'.join(fragment_lines).strip()

            # 如果是第一个片段且有前言，合并
            if idx == 0 and preamble:
                fragment_content = preamble + '\n\n' + fragment_content

            if fragment_content:
                fragments.append({
                    "content": fragment_content,
                    "heading": heading_line,
                })

        if not fragments:
            # 所有标题后都没有实质内容
            return [{"content": content, "heading": None}]

        return fragments
