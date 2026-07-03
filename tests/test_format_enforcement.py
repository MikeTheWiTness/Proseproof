"""TDD: format_enforcement 模块测试 —— 格式审查 + bash 修正路径。

覆盖:
  ✅ _enforce_format: 各种格式不合规场景
  ✅ _enforce_format: "无问题"快速通过
  ✅ _enforce_format: 有内联标记但无 ### 标记原文 标题也算合规
  ✅ enforce_and_fix: bash 修正成功/失败两路
"""
import os
import json
from unittest.mock import patch
import pytest
from proseproof.core.format_enforcement import _enforce_format, enforce_and_fix


# ============================================================
# _enforce_format 测试
# ============================================================

# 原因编号使用 digit.period 格式（与 _enforce_format 正则匹配）
VALID_FORMAT = """### 标记原文
这是【1|错误|正确】的原文，还有【2|错字|正确】问题。

### 修改原因
1. 第一个原因
2. 第二个原因
"""

NO_ISSUE_TEXT = "无问题"

INLINE_ONLY = """【1|错误|正确】的文本内容，有内联标记但没标题。

### 修改原因
1. 原因说明
"""

MISSING_BOTH = """这是纯文本，没有内联标记也没有标记原文段落。

这里也没有修改原因段落。
"""

MISSING_REASON = """### 标记原文
这是【1|错误|正确】的原文。

这里没有修改原因段落
"""

NUMBER_MISMATCH = """### 标记原文
这是【1|错误|正确】的原文，还有【2|错字|打字】问题。

### 修改原因
1. 只有第一个原因
"""

EXTRA_REASON = """### 标记原文
这是【1|错误|正确】的原文。

### 修改原因
1. 第一个原因
2. 第二个原因（但标记原文中没有2号）
"""

MALFORMED_MARKER = """### 标记原文
这是【1错误】缺竖线的标记。

### 修改原因
1. 原因
"""


class TestEnforceFormat:
    """_enforce_format() 格式合规检查。"""

    def test_valid_format_passes(self):
        ok, issues = _enforce_format(VALID_FORMAT)
        assert ok is True
        assert issues == ""

    def test_no_issue_passes(self):
        ok, issues = _enforce_format(NO_ISSUE_TEXT)
        assert ok is True

    def test_inline_only_with_reason_passes(self):
        """有内联标记 + ### 修改原因，但缺标题 → 仍合规。"""
        ok, issues = _enforce_format(INLINE_ONLY)
        assert ok is True

    def test_missing_both_fails(self):
        """无内联标记 + 无 ### 标记原文 → 不合规。"""
        ok, issues = _enforce_format(MISSING_BOTH)
        assert ok is False
        assert "缺少" in issues or "标记原文" in issues

    def test_missing_reason_fails(self):
        ok, issues = _enforce_format(MISSING_REASON)
        assert ok is False
        assert "修改原因" in issues

    def test_number_mismatch_detected(self):
        """标记编号 2 在原因中缺失。"""
        ok, issues = _enforce_format(NUMBER_MISMATCH)
        assert ok is False
        assert "2" in issues

    def test_extra_reason_detected(self):
        """原因编号 2 没有对应标记。"""
        ok, issues = _enforce_format(EXTRA_REASON)
        assert ok is False
        assert "2" in issues

    def test_malformed_marker_detected(self):
        """【1错误】缺少 | → 格式异常标记。"""
        ok, issues = _enforce_format(MALFORMED_MARKER)
        assert ok is False
        assert "格式异常" in issues or "|" in issues


# ============================================================
# enforce_and_fix 测试
# ============================================================

class TestEnforceAndFix:
    """enforce_and_fix() 的 bash 修正路径测试。"""

    def test_already_valid_no_fix(self):
        """格式已合规 → 不触发修正。"""
        content, was_fixed, issues = enforce_and_fix(
            file_path="/nonexistent/test.md",
            res=VALID_FORMAT,
            api_url="",
            api_key="",
            model="",
        )
        assert was_fixed is False
        assert issues == ""
        assert content == VALID_FORMAT

    def test_bash_fix_success(self):
        """格式不合规 → bash 修正成功。"""
        with patch('proseproof.core.format_enforcement._bash_format_fix') as mock_fix:
            mock_fix.return_value = VALID_FORMAT
            content, was_fixed, issues = enforce_and_fix(
                file_path="/nonexistent/test.md",
                res=MISSING_REASON,
                api_url="http://fake",
                api_key="fake",
                model="fake",
            )
            assert was_fixed is True
            assert content == VALID_FORMAT

    def test_bash_fix_fails_returns_original(self):
        """bash 返回 None → 返回原始内容。"""
        with patch('proseproof.core.format_enforcement._bash_format_fix') as mock_fix:
            mock_fix.return_value = None
            content, was_fixed, issues = enforce_and_fix(
                file_path="/nonexistent/test.md",
                res=MISSING_REASON,
                api_url="http://fake",
                api_key="fake",
                model="fake",
            )
            assert was_fixed is False
            assert content == MISSING_REASON

    def test_bash_fix_still_invalid(self):
        """bash 返回后格式仍不合规 → 不采用，退回原始。"""
        with patch('proseproof.core.format_enforcement._bash_format_fix') as mock_fix:
            still_bad = MISSING_REASON  # 修正后仍然不合规
            mock_fix.return_value = still_bad
            content, was_fixed, issues = enforce_and_fix(
                file_path="/nonexistent/test.md",
                res=MISSING_REASON,
                api_url="http://fake",
                api_key="fake",
                model="fake",
            )
            assert was_fixed is False
            assert content == MISSING_REASON
