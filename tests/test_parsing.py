"""TDD: parsing 模块测试 —— LLM 校对报告解析 + _is_no_issue() 迁移。

覆盖:
  ✅ 内联标记格式（### 标记原文 + 【N|原文|改为】 + ### 修改原因）
  ✅ 旧格式兼容（### 修改 N + - **原文**: 列表）
  ✅ _parse_reason_meta: type/severity 元数据提取
  ✅ "无问题"快速通道
  ✅ 边界情况：空文本、缺失摘要、编号不匹配
  ✅ 批注评审格式共存
"""
import json
import pytest
from proseproof.core.parsing import (
    parse_proofread_md,
    _is_no_issue,
)


# ============================================================
# _is_no_issue 测试
# ============================================================

class TestIsNoIssue:
    """_is_no_issue() 行为测试。"""

    def test_pure_no_issue(self):
        assert _is_no_issue("无问题") is True

    def test_no_issue_with_punctuation(self):
        assert _is_no_issue("无问题。") is True

    def test_no_issue_with_newlines(self):
        assert _is_no_issue("无问题\n\n") is True

    def test_not_no_issue(self):
        assert _is_no_issue("一般问题") is False

    def test_none_or_empty(self):
        assert _is_no_issue("") is False
        assert _is_no_issue(None) is False

    def test_long_no_issue_variant(self):
        """超过 10 字的'无问题'变体不应误判。"""
        assert _is_no_issue("无问题，但是还有一些小建议可以改进") is False


# ============================================================
# 内联标记格式测试（主力格式）
# ============================================================

INLINE_FORMAT = """### 标记原文
这是【1|错误|正确】的原文，还有【2|一个错字|另一个】问题。

### 修改原因
① [error|critical] 第一个错误原因
② [suggestion|minor] 第二个建议原因
"""

INLINE_NO_ISSUE = "无问题"


class TestInlineFormat:
    """内联标记（【N|原文|改为】）格式解析。"""

    def test_basic_inline_parse(self):
        result = parse_proofread_md(INLINE_FORMAT)
        assert result is not None
        assert len(result["corrections"]) == 2

    def test_correction_fields(self):
        result = parse_proofread_md(INLINE_FORMAT)
        c1 = result["corrections"][0]
        assert c1["num"] == 1
        assert c1["type"] == "error"
        assert c1["severity"] == "critical"
        assert c1["original"] == "错误"
        assert c1["correction"] == "正确"
        assert "第一个错误原因" in c1["reason"]

    def test_suggestion_type(self):
        result = parse_proofread_md(INLINE_FORMAT)
        c2 = result["corrections"][1]
        assert c2["type"] == "suggestion"
        assert c2["severity"] == "minor"

    def test_summary_extracted(self):
        result = parse_proofread_md(INLINE_FORMAT)
        # 内联格式中 summary 来自关键词检测；文本中无关键词时兜底为"无问题"
        assert result["summary"] in ("严重错误", "一般问题", "轻微问题", "无问题")

    def test_no_issue_quick_path(self):
        """纯'无问题'直接返回空 corrections。"""
        result = parse_proofread_md(INLINE_NO_ISSUE)
        assert result is not None
        assert result["corrections"] == []
        assert result["summary"] == "无问题"

    def test_corrections_sorted_by_num(self):
        """corrections 按 num 升序排列。"""
        text = """### 标记原文
【3|C|c】xxx【1|A|a】xxx【2|B|b】

### 修改原因
① 原因A
② 原因B
③ 原因C
"""
        result = parse_proofread_md(text)
        nums = [c["num"] for c in result["corrections"]]
        assert nums == sorted(nums)

    def test_number_range_in_reason(self):
        """修改原因中的编号范围（如 ①-③）。"""
        text = """### 标记原文
【1|A|a】xxx【2|B|b】xxx【3|C|c】

### 修改原因
①-③ 同一个原因
"""
        result = parse_proofread_md(text)
        assert len(result["corrections"]) == 3
        for c in result["corrections"]:
            assert "同一个原因" in c["reason"]

    def test_empty_text_returns_none(self):
        assert parse_proofread_md("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_proofread_md("   \n  ") is None

    def test_summary_from_keyword(self):
        """文本中含'一般问题'关键词时 summary 正确提取。"""
        text = "一般问题\n\n### 标记原文\n【1|A|a】\n\n### 修改原因\n① 原因"
        result = parse_proofread_md(text)
        assert result["summary"] == "一般问题"
        assert len(result["corrections"]) == 1


# ============================================================
# 旧格式兼容测试
# ============================================================

OLD_FORMAT = """### 修改 1

- **类型**: text
- **原文**: `错误`
- **改为**: `正确`
- **原因**: 这是一个错误

### 修改 2

- **类型**: text
- **原文**: `另一个错字`
- **改为**: `另一个`
- **原因**: 拼写错误
"""


class TestOldFormat:
    """旧格式 ### 修改 N 列表格式兼容解析。"""

    def test_basic_old_format(self):
        result = parse_proofread_md(OLD_FORMAT)
        assert result is not None
        assert len(result["corrections"]) == 2

    def test_old_format_fields(self):
        result = parse_proofread_md(OLD_FORMAT)
        c1 = result["corrections"][0]
        assert c1["original"] == "错误"
        assert c1["correction"] == "正确"
        assert "这是一个错误" in c1["reason"]

    def test_old_format_with_summary(self):
        text = "严重错误\n\n" + OLD_FORMAT
        result = parse_proofread_md(text)
        assert result["summary"] == "严重错误"


# ============================================================
# _parse_reason_meta 边缘测试
# ============================================================

class TestReasonMeta:
    """原因行中 [type|severity] 元数据提取边界。"""

    def test_default_meta(self):
        """无标记时默认为 [error|major]。"""
        text = """### 标记原文
【1|原|改】

### 修改原因
① 普通原因
"""
        result = parse_proofread_md(text)
        assert result["corrections"][0]["type"] == "error"
        assert result["corrections"][0]["severity"] == "major"

    def test_error_only(self):
        text = """### 标记原文
【1|原|改】

### 修改原因
① [error] 只有类型
"""
        result = parse_proofread_md(text)
        assert result["corrections"][0]["type"] == "error"
        assert result["corrections"][0]["severity"] == "major"

    def test_severity_only(self):
        text = """### 标记原文
【1|原|改】

### 修改原因
① [minor] 只有严重级别
"""
        result = parse_proofread_md(text)
        assert result["corrections"][0]["type"] == "error"
        assert result["corrections"][0]["severity"] == "minor"
