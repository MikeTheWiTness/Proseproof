"""v0.2.0 结构审查 —— 大纲上的纯 Python 规则检查。

零 LLM 成本。检查维度：章节顺序、编号连续性、层级一致性、标题格式。

设计决策见 ADR-0010 (内容审查三层体系)。
"""
from __future__ import annotations
import re


def _is_numbered_format(text: str) -> str | None:
    """判断标题文本使用的编号格式。

    Returns:
        "arabic" (1. / 1.1), "chinese" (一、), "paren" ((1)), 或 None。
    """
    if re.match(r'^\d+(\.\d+)*[.、．]', text):
        return "arabic"
    if re.match(r'^[一二三四五六七八九十]+[、．]', text):
        return "chinese"
    if re.match(r'^[(（]\d+[)）]', text):
        return "paren"
    return None


def structural_review(outline: list[dict]) -> list[dict]:
    """对大纲执行结构规则检查。

    Args:
        outline: outline_to_dict 格式的大纲条目列表。

    Returns:
        问题列表，每个问题含 type/severity/description/location。
    """
    issues = []

    if not outline:
        return issues

    # 1. 章节顺序：检查同级条目的 line_start 是否单调递增
    _check_order(outline, issues)

    # 2. 编号连续性：检查同级 numbered 条目的 index 是否连续
    _check_numbering(outline, issues)

    # 3. 层级跳跃：子节点 level 不能比父节点 > 1
    _check_hierarchy(outline, issues)

    # 4. 同级标题格式一致性
    _check_heading_format(outline, issues)

    # 5. 同级重复标题
    _check_duplicate_titles(outline, issues)

    return issues


def _check_order(items: list[dict], issues: list, parent_path: str = ""):
    """检查同级条目行号单调递增。"""
    for i in range(1, len(items)):
        prev = items[i - 1]
        curr = items[i]
        if prev.get("line_start", 0) > curr.get("line_start", 0):
            issues.append({
                "type": "chapter_order",
                "severity": "critical",
                "description": (
                    f"片段顺序可能错误："
                    f"\"{prev['text']}\" (行{prev['line_start']}) "
                    f"在 \"{curr['text']}\" (行{curr['line_start']}) 之后"
                ),
                "location": {
                    "item_indices": [prev["index"], curr["index"]],
                },
            })

    # 递归检查子节点
    for item in items:
        if item.get("children"):
            _check_order(item["children"], issues,
                        f"{parent_path}/{item['text']}")


def _check_numbering(items: list[dict], issues: list):
    """检查同级编号项的 index 连续性。"""
    numbered = [i for i in items if i.get("item_type") == "numbered"]
    for i in range(1, len(numbered)):
        prev_idx = numbered[i - 1]["index"]
        curr_idx = numbered[i]["index"]
        if curr_idx != prev_idx + 1:
            issues.append({
                "type": "numbering_gap",
                "severity": "major",
                "description": (
                    f"编号不连续："
                    f"#{prev_idx} \"{numbered[i-1]['text']}\" → "
                    f"#{curr_idx} \"{numbered[i]['text']}\""
                ),
                "location": {
                    "item_indices": [prev_idx, curr_idx],
                },
            })

    # 递归
    for item in items:
        if item.get("children"):
            _check_numbering(item["children"], issues)


def _check_hierarchy(items: list[dict], issues: list, parent_level: int = 0):
    """检查层级跳跃：子节点 level 不能跳跃 > 1。"""
    for item in items:
        if parent_level > 0 and item["level"] > parent_level + 1:
            issues.append({
                "type": "hierarchy_jump",
                "severity": "major",
                "description": (
                    f"层级跳跃：\"{item['text']}\" (L{item['level']}) "
                    f"直接在 L{parent_level} 下，缺少 L{parent_level + 1}"
                ),
                "location": {
                    "item_index": item["index"],
                },
            })
        if item.get("children"):
            _check_hierarchy(item["children"], issues, item["level"])


def _check_heading_format(items: list[dict], issues: list):
    """检查同级标题格式一致性。"""
    headings = [i for i in items if i.get("item_type") == "heading"]

    # 按 level 分组
    by_level = {}
    for h in headings:
        by_level.setdefault(h["level"], []).append(h)

    for level, group in by_level.items():
        if len(group) < 2:
            continue
        # 统计编号格式
        formats = [_is_numbered_format(h["text"]) for h in group]
        unique_formats = set(f for f in formats if f is not None)
        if len(unique_formats) > 1:
            issues.append({
                "type": "heading_format",
                "severity": "minor",
                "description": (
                    f"L{level} 同级标题编号格式不一致："
                    f"{', '.join(sorted(unique_formats))}"
                ),
                "location": {
                    "level": level,
                    "item_indices": [h["index"] for h in group],
                },
            })

    # 递归
    for item in items:
        if item.get("children"):
            _check_heading_format(item["children"], issues)


def _check_duplicate_titles(items: list[dict], issues: list):
    """检查同级重复标题。"""
    seen = {}
    for item in items:
        if item.get("item_type") != "heading":
            continue
        key = (item.get("level"), item.get("text"))
        if key in seen:
            issues.append({
                "type": "duplicate_title",
                "severity": "minor",
                "description": (
                    f"L{item['level']} 标题重复："
                    f"\"{item['text']}\" (索引 #{seen[key]} 和 #{item['index']})"
                ),
                "location": {
                    "item_indices": [seen[key], item["index"]],
                },
            })
        else:
            seen[key] = item["index"]

    # 递归
    for item in items:
        if item.get("children"):
            _check_duplicate_titles(item["children"], issues)


def has_severe_issues(issues: list[dict]) -> bool:
    """判断是否存在严重问题（critical 级别）。

    用于 CLI 暂停判断——严重问题时默认暂停等待用户确认。
    """
    return any(i.get("severity") == "critical" for i in issues)
