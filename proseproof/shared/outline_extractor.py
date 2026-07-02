"""v0.2.0 大纲提取器 —— 扫描 Markdown 文档，构建树形结构骨架。

产出 OutlineItem 树 + _outline.json 中间产物。三个消费者:
  - Smart 分割：大纲 → LLM 切分决策
  - 结构审查：大纲 → Python 规则检查
  - 内容审查：大纲 + 校对摘要 → LLM 全局审读

设计决策见 ADR-0007 (大纲作为独立中间产物)。
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


# ============================================================
# OutlineItem —— 大纲条目
# ============================================================

@dataclass
class OutlineItem:
    """大纲中的一个节点。

    Attributes:
        index:      全局序号，从 1 开始，按文档出现顺序递增。
        level:      层级深度 (1=章, 2=节, 3=小节, 4=小小节, …)。
        item_type:  条目类型: "heading" | "numbered" | "list_marker"。
        text:       条目的纯文本，已剥离标记符和编号。
        line_start: 在原文档中的起始行号 (0-based)。
        line_end:   在原文档中的结束行号 (0-based)。
        children:   递归子节点列表。
    """
    index: int
    level: int
    item_type: Literal["heading", "numbered", "list_marker"]
    text: str
    line_start: int
    line_end: int
    children: list[OutlineItem] = field(default_factory=list)


# ============================================================
# 编号模式
# ============================================================

# 自动检测的编号模式列表
_DEFAULT_NUMBERED_PATTERNS = [
    # 1. / 1、 / 1．
    re.compile(r'^(\d+)[.、．]\s+'),
    # (1) / （1）
    re.compile(r'^[(（](\d+)[)）]\s*'),
    # 一、/ 二、/ … 十、
    re.compile(r'^([一二三四五六七八九十]+)[、．]\s*'),
    # ① / ② …
    re.compile(r'^([①-⑩])\s*'),
]

# 无序列表标记
_LIST_MARKER_PATTERN = re.compile(r'^[-*•·]\s+')

# 标题模式
_HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$')


def _strip_heading_marker(text: str) -> str:
    """剥离标题行中的 # 标记，保留编号（编号是标题标识的一部分）。"""
    text = re.sub(r'^#{1,6}\s+', '', text)
    return text.strip()


def _strip_number_marker(text: str) -> str:
    """剥离编号行中的编号标记，保留内容文本。"""
    for pat in _DEFAULT_NUMBERED_PATTERNS:
        m = pat.match(text)
        if m:
            return text[m.end():].strip()
    return text.strip()


def _strip_list_marker(text: str) -> str:
    """剥离列表标记。"""
    m = _LIST_MARKER_PATTERN.match(text)
    if m:
        return text[m.end():].strip()
    return text.strip()


# ============================================================
# 提取器核心
# ============================================================

def _is_inside_code_block(line: str, in_fenced: bool, in_indented: bool,
                          prev_blank: bool) -> tuple[bool, bool]:
    """判断当前行是否在代码块内，更新代码块状态。

    Returns:
        (in_fenced, in_indented) 更新后的状态。
    """
    stripped = line.strip()

    # 围栏代码块切换
    if stripped.startswith('```') or stripped.startswith('~~~'):
        return (not in_fenced, False)

    # 缩进代码块：4 空格或 1 tab 开头，且在空行之后
    if prev_blank and not in_fenced and not in_indented:
        if line.startswith('    ') or line.startswith('\t'):
            return (in_fenced, True)

    # 缩进代码块结束：空行
    if in_indented and stripped == '':
        return (in_fenced, False)

    return (in_fenced, in_indented)


def extract_outline(content: str, max_depth: int = 4,
                    extra_patterns: list[str] | None = None) -> list[OutlineItem]:
    """从 Markdown 内容中提取大纲。

    Args:
        content:         Markdown 格式的文档全文。
        max_depth:       最大提取深度，默认 4。超出深度的标题折叠到父节点。
        extra_patterns:  用户额外指定的编号正则列表（如 ["^§\\d+"]）。

    Returns:
        大纲条目列表（顶层节点）。若无任何结构，返回空列表。
    """
    if not content or not content.strip():
        return []

    # 编译编号匹配模式
    numbered_patterns = list(_DEFAULT_NUMBERED_PATTERNS)
    if extra_patterns:
        for pat_str in extra_patterns:
            try:
                numbered_patterns.append(re.compile(pat_str))
            except re.error:
                pass

    lines = content.split('\n')
    root_items: list[OutlineItem] = []
    # level_stack: 每一层深度对应的当前父节点（index 0 = level 1）
    level_stack: list[OutlineItem] = []
    global_index = 0

    in_fenced = False
    in_indented = False
    prev_blank = True  # 第一行之前视为空行

    for line_no, line in enumerate(lines):
        stripped = line.strip()

        # ---- 更新代码块状态 ----
        in_fenced, in_indented = _is_inside_code_block(
            line, in_fenced, in_indented, prev_blank,
        )
        prev_blank = (stripped == '')

        # 代码块内的行跳过
        if in_fenced or in_indented:
            continue

        # 空行跳过
        if stripped == '':
            continue

        # ---- 尝试匹配标题 ----
        heading_m = _HEADING_PATTERN.match(stripped)
        if heading_m:
            level = len(heading_m.group(1))
            if level <= max_depth:
                global_index += 1
                text = _strip_heading_marker(stripped)
                item = OutlineItem(
                    index=global_index,
                    level=level,
                    item_type="heading",
                    text=text,
                    line_start=line_no,
                    line_end=line_no,
                )
                _insert_into_tree(root_items, level_stack, item, level)
                continue
            else:
                # 超出 max_depth：跳过，但仍影响层级上下文
                # 后续内容继续归属于最后一个有效层级的父节点
                continue

        # ---- 尝试匹配编号项 ----
        numbered_matched = False
        for pat in numbered_patterns:
            if pat.match(stripped):
                numbered_matched = True
                global_index += 1
                text = _strip_number_marker(stripped)
                # 编号项的层级 = 当前有效层级 + 1（或至少 1）
                current_level = len(level_stack) + 1 if level_stack else 1
                if current_level > max_depth:
                    continue
                item = OutlineItem(
                    index=global_index,
                    level=current_level,
                    item_type="numbered",
                    text=text,
                    line_start=line_no,
                    line_end=line_no,
                )
                # 编号项插入到当前最深层父节点下
                if level_stack:
                    parent = level_stack[-1]
                    parent.children.append(item)
                    parent.line_end = max(parent.line_end, line_no)
                else:
                    root_items.append(item)
                break

        if numbered_matched:
            continue

        # ---- 尝试匹配列表标记 ----
        list_m = _LIST_MARKER_PATTERN.match(stripped)
        if list_m:
            global_index += 1
            text = _strip_list_marker(stripped)
            current_level = len(level_stack) + 1 if level_stack else 1
            if current_level <= max_depth:
                item = OutlineItem(
                    index=global_index,
                    level=current_level,
                    item_type="list_marker",
                    text=text,
                    line_start=line_no,
                    line_end=line_no,
                )
                if level_stack:
                    parent = level_stack[-1]
                    parent.children.append(item)
                    parent.line_end = max(parent.line_end, line_no)
                else:
                    root_items.append(item)
            continue

    return root_items


def _insert_into_tree(root_items: list[OutlineItem],
                      level_stack: list[OutlineItem],
                      item: OutlineItem,
                      level: int):
    """将标题条目插入到树形结构中的正确位置。"""
    # 弹出所有层级 >= 当前层级的节点
    while level_stack and level_stack[-1].level >= level:
        level_stack.pop()

    if level_stack:
        parent = level_stack[-1]
        parent.children.append(item)
        parent.line_end = max(parent.line_end, item.line_end)
    else:
        root_items.append(item)

    level_stack.append(item)


# ============================================================
# 序列化 / 反序列化
# ============================================================

def outline_to_dict(items: list[OutlineItem]) -> list[dict]:
    """将大纲条目列表转为可 JSON 序列化的 dict 列表。"""
    result = []
    for item in items:
        d = {
            "index": item.index,
            "level": item.level,
            "item_type": item.item_type,
            "text": item.text,
            "line_start": item.line_start,
            "line_end": item.line_end,
            "children": outline_to_dict(item.children),
        }
        result.append(d)
    return result


def dict_to_outline(data: list[dict]) -> list[OutlineItem]:
    """从 dict 列表恢复 OutlineItem 列表。"""
    result = []
    for d in data:
        item = OutlineItem(
            index=d["index"],
            level=d["level"],
            item_type=d["item_type"],
            text=d["text"],
            line_start=d["line_start"],
            line_end=d["line_end"],
            children=dict_to_outline(d.get("children", [])),
        )
        result.append(item)
    return result


def save_outline_json(content: str, output_dir: Path,
                      max_depth: int = 4,
                      extra_patterns: list[str] | None = None) -> Path:
    """提取大纲并保存到 output_dir/_outline.json。

    Args:
        content:         Markdown 文档原文。
        output_dir:      输出目录。
        max_depth:       最大提取深度。
        extra_patterns:  额外的编号正则。

    Returns:
        保存的 JSON 文件路径。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    outline = extract_outline(content, max_depth=max_depth,
                              extra_patterns=extra_patterns)
    data = outline_to_dict(outline)
    json_path = output_dir / "_outline.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return json_path
