"""TDD: 测试大纲提取器 —— 扫描 Markdown 文档，构建树形 OutlineItem 结构。

覆盖:
  - 多级标题提取（1-4 级，默认 max_depth=4）
  - 编号项识别：1. / (1) / 一、 / ① 等
  - 无序列表标记：- * •
  - 树形层级构建
  - 空文档 / 无标题 / 纯代码块 / 标题在代码块内
  - max_depth 配置
  - _outline.json 落盘格式
"""
import json
import tempfile
import os
from pathlib import Path
import pytest


# ============================================================
# 测试用的真实文档（尽可能接近实际场景）
# ============================================================

SIMPLE_MD = """\
# 第一章

这是一些介绍文字。

## 1.1 背景

背景描述。

### 1.1.1 子背景

更细节的内容。

## 1.2 方法

(1) 步骤一：收集数据
(2) 步骤二：分析数据
"""

MIXED_NUMBERING_MD = """\
# 总纲

## 一、中文编号

内容。

### 1. 阿拉伯编号

子内容。

#### (1) 括号编号

更深的内容。

##### ① 圆圈编号

最深的内容（默认 max_depth=4 应折叠）。
"""

NO_HEADINGS_MD = """\
这是一段纯文本，没有任何标题。

只有段落和空行。

但是有一些编号：
1. 第一点
2. 第二点
3. 第三点
"""

CODE_BLOCK_MD = """\
# 真实标题

```python
# 这是代码注释，不是标题
def foo():
    ## 这个也不是标题
    pass
```

## 真实二级标题

    ## 缩进代码也不是标题
"""

EMPTY_MD = ""

LIST_MARKERS_MD = """\
# 列表测试

- 无序列表项一
- 无序列表项二

* 星号列表

• 圆点列表

## 二级标题
"""

DEEP_NESTING_MD = """\
# L1
## L2
### L3
#### L4
##### L5 (should be folded)
###### L6 (should be folded)
"""


# ============================================================
# 测试大纲提取
# ============================================================

class TestExtractOutline:
    """大纲提取器的核心功能测试。"""

    def test_extract_headings_only(self):
        """提取标题层级。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(SIMPLE_MD)
        # # 第一章
        root = outline[0]
        assert root.level == 1
        assert "第一章" in root.text
        # ## 1.1 背景
        assert len(root.children) == 2
        assert root.children[0].level == 2
        assert "1.1 背景" in root.children[0].text
        # ### 1.1.1 子背景
        assert len(root.children[0].children) == 1
        assert root.children[0].children[0].level == 3
        assert "1.1.1" in root.children[0].children[0].text

    def test_numbered_items_detected(self):
        """编号项 (1) / (2) 被识别。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(SIMPLE_MD)
        # (1) 步骤一 在 ## 1.2 方法 下面
        method_section = None
        for child in outline[0].children:
            if "1.2" in child.text:
                method_section = child
                break
        assert method_section is not None
        # (1) 和 (2) 应作为 numbered item 出现在某个条目中
        numbered_items = [
            item for item in method_section.children
            if item.item_type == "numbered"
        ]
        assert len(numbered_items) >= 2
        assert any("步骤一" in item.text for item in numbered_items)
        assert any("步骤二" in item.text for item in numbered_items)

    def test_chinese_numbered_items(self):
        """中文编号 一、/ 1. 被识别。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(MIXED_NUMBERING_MD)
        root = outline[0]
        # ## 一、中文编号
        l2 = root.children[0]
        assert "一、" in l2.text
        assert "中文编号" in l2.text
        assert l2.level == 2
        # ### 1. 阿拉伯编号  —— 注意，作为 heading，编号保留在 text 中
        l3 = l2.children[0]
        assert "阿拉伯编号" in l3.text
        assert l3.level == 3
        # #### (1) 括号编号
        l4 = l3.children[0]
        assert "括号编号" in l4.text
        assert l4.level == 4

    def test_max_depth_truncation(self):
        """超出 max_depth 的层级折叠到父节点。"""
        from proseproof.shared.outline_extractor import extract_outline

        # 默认 max_depth=4
        outline = extract_outline(MIXED_NUMBERING_MD)
        root = outline[0]
        l2 = root.children[0]
        l3 = l2.children[0]
        l4 = l3.children[0]
        # ##### ① 应被折叠，不应该作为 l4 的独立子节点
        # 验证 l4 没有更多 children（或 ① 被合并）
        assert len(l4.children) == 0

    def test_max_depth_configurable(self):
        """max_depth 可配置。"""
        from proseproof.shared.outline_extractor import extract_outline

        # max_depth=5 应能提取到 ⑤
        outline = extract_outline(DEEP_NESTING_MD, max_depth=5)
        root = outline[0]
        l2 = root.children[0]
        l3 = l2.children[0]
        l4 = l3.children[0]
        l5 = l4.children[0]
        assert l5.level == 5

    def test_empty_document(self):
        """空文档返回空列表。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(EMPTY_MD)
        assert outline == []

    def test_no_headings_document(self):
        """无标题文档——返回仅含编号项的平铺列表。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(NO_HEADINGS_MD)
        # 编号 1. 2. 3. 应该被识别
        numbered = [item for item in outline if item.item_type == "numbered"]
        assert len(numbered) == 3
        assert numbered[0].text == "第一点"
        assert numbered[1].text == "第二点"
        assert numbered[2].text == "第三点"

    def test_code_blocks_ignored(self):
        """代码块内的 # 和 ## 不被识别为标题。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(CODE_BLOCK_MD)

        def _all_headings(items):
            result = []
            for item in items:
                if item.item_type == "heading":
                    result.append(item)
                result.extend(_all_headings(item.children))
            return result

        headings = _all_headings(outline)
        # 应只有 "真实标题" 和 "真实二级标题"
        assert len(headings) == 2
        assert headings[0].text == "真实标题"
        # 真实二级标题 是 真实标题 的子节点
        assert "真实二级标题" in headings[1].text

    def test_list_markers_detected(self):
        """无序列表标记被识别。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(LIST_MARKERS_MD)
        # 应该找到无序列表项
        all_items = outline[0].children
        list_items = [item for item in all_items if item.item_type == "list_marker"]
        assert len(list_items) == 4  # - 两项 + * 一项 + • 一项
        assert "无序列表项一" in list_items[0].text
        assert "无序列表项二" in list_items[1].text
        assert "星号列表" in list_items[2].text
        assert "圆点列表" in list_items[3].text

    def test_line_numbers_tracked(self):
        """line_start 和 line_end 正确记录。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(SIMPLE_MD)
        root = outline[0]
        assert root.line_start >= 0
        assert root.line_end >= root.line_start

    def test_item_index_sequential(self):
        """每个 OutlineItem 有从 1 开始的连续 index。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline(SIMPLE_MD)

        def collect_indices(items):
            indices = []
            for item in items:
                indices.append(item.index)
                indices.extend(collect_indices(item.children))
            return indices

        indices = collect_indices(outline)
        assert indices == sorted(indices)
        assert indices[0] == 1


class TestOutlineItemStructure:
    """OutlineItem 数据结构测试。"""

    def test_heading_type(self):
        from proseproof.shared.outline_extractor import extract_outline
        outline = extract_outline("# 标题")
        assert outline[0].item_type == "heading"

    def test_numbered_type(self):
        from proseproof.shared.outline_extractor import extract_outline
        outline = extract_outline("1. 第一条")
        assert outline[0].item_type == "numbered"

    def test_list_type(self):
        from proseproof.shared.outline_extractor import extract_outline
        outline = extract_outline("- 项目")
        assert outline[0].item_type == "list_marker"

    def test_children_default_empty(self):
        """children 默认为空列表。"""
        from proseproof.shared.outline_extractor import extract_outline
        outline = extract_outline("# 孤立标题")
        assert outline[0].children == []

    def test_text_stripped_of_markers(self):
        """text 字段剥离了 # 标记，但保留编号（编号是标题语义的一部分）。"""
        from proseproof.shared.outline_extractor import extract_outline

        outline = extract_outline("### 1.1 背景介绍")
        assert "###" not in outline[0].text
        assert "1.1" in outline[0].text
        assert "背景介绍" in outline[0].text


class TestOutlineJsonSerialization:
    """_outline.json 落盘测试。"""

    def test_save_and_load_roundtrip(self):
        """大纲序列化后再加载，结构不变。"""
        from proseproof.shared.outline_extractor import extract_outline, outline_to_dict, dict_to_outline

        outline = extract_outline(SIMPLE_MD)
        data = outline_to_dict(outline)
        # 重新加载
        restored = dict_to_outline(data)
        assert len(restored) == len(outline)
        assert restored[0].text == outline[0].text
        assert restored[0].level == outline[0].level

    def test_json_file_format(self):
        """落盘的 JSON 格式正确。"""
        from proseproof.shared.outline_extractor import (
            extract_outline, save_outline_json,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_outline_json(SIMPLE_MD, Path(tmpdir))
            json_path = Path(tmpdir) / "_outline.json"
            assert json_path.exists()

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            assert isinstance(data, list)
            assert len(data) > 0
            item = data[0]
            assert "index" in item
            assert "level" in item
            assert "item_type" in item
            assert "text" in item
            assert "line_start" in item
            assert "line_end" in item
            assert "children" in item


class TestEdgeCases:
    """边界情况测试。"""

    def test_whitespace_only(self):
        """纯空白文档。"""
        from proseproof.shared.outline_extractor import extract_outline
        outline = extract_outline("   \n\n  \n")
        assert outline == []

    def test_only_code_blocks(self):
        """全文都是代码块。"""
        from proseproof.shared.outline_extractor import extract_outline
        content = "```python\n# 注释\nprint('hello')\n```\n```\n## 另一个代码块内的标题\n```"
        outline = extract_outline(content)
        # 所有看起来像标题的内容都在代码块内 → 不应被提取
        headings = [
            item for item in outline
            if item.item_type == "heading"
        ]
        assert len(headings) == 0

    def test_heading_with_special_chars(self):
        """标题含有特殊字符。"""
        from proseproof.shared.outline_extractor import extract_outline
        outline = extract_outline("## 1. $E = mc^2$ — 质能方程")
        assert outline[0].level == 2
        assert "质能方程" in outline[0].text

    def test_duplicate_headings(self):
        """重复标题不应被去重——大纲是树结构，允许相同文本。"""
        from proseproof.shared.outline_extractor import extract_outline
        content = "# 概述\n## 概述\n### 概述"
        outline = extract_outline(content)
        assert len(outline) == 1  # 一个根节点
        assert outline[0].text == "概述"
        assert outline[0].children[0].text == "概述"
        assert outline[0].children[0].children[0].text == "概述"
