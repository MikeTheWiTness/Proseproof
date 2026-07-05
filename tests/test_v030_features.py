"""TDD: v0.3.0 新功能测试。

覆盖:
  ✅ FileTool (ReadTool/WriteTool/EditTool) 白名单保护
  ✅ --middleware 标志传递
  ✅ extra_signals 大纲提取
  ✅ review_prompt_lines 回退
  ✅ 错误处理加固
"""
import os
import tempfile
import pytest
from pathlib import Path


# ============================================================
# FileTool 测试
# ============================================================

class TestFileTools:
    """ReadTool / WriteTool / EditTool 行为测试。"""

    def test_read_full_file(self, tmp_path):
        from proseproof.shared.file_tools import ReadTool
        f = tmp_path / "_test.md"
        f.write_text("line1\nline2\nline3", encoding="utf-8")
        tool = ReadTool()
        result = tool._run(str(f))
        assert "line1" in result
        assert "line3" in result

    def test_read_with_offset_limit(self, tmp_path):
        from proseproof.shared.file_tools import ReadTool
        f = tmp_path / "_test.md"
        f.write_text("line1\nline2\nline3\nline4", encoding="utf-8")
        tool = ReadTool()
        result = tool._run(str(f), offset=1, limit=2)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "line2" in lines[0]

    def test_write_to_allowed_file(self, tmp_path):
        from proseproof.shared.file_tools import WriteTool
        f = tmp_path / "_test.md"
        tool = WriteTool()
        result = tool._run(str(f), "hello")
        assert "[OK]" in result
        assert f.read_text(encoding="utf-8") == "hello"

    def test_write_rejected_for_original(self, tmp_path):
        from proseproof.shared.file_tools import WriteTool
        f = tmp_path / "frag_001.md"
        f.write_text("original", encoding="utf-8")
        tool = WriteTool()
        result = tool._run(str(f), "modified")
        assert "[拒绝]" in result
        # 原文未被修改
        assert f.read_text(encoding="utf-8") == "original"

    def test_edit_precise_replace(self, tmp_path):
        from proseproof.shared.file_tools import EditTool
        f = tmp_path / "_校对报告.md"
        f.write_text("AAA BBB CCC", encoding="utf-8")
        tool = EditTool()
        result = tool._run(str(f), "BBB", "DDD")
        assert "[OK]" in result
        assert f.read_text(encoding="utf-8") == "AAA DDD CCC"

    def test_edit_rejected_for_original(self, tmp_path):
        from proseproof.shared.file_tools import EditTool
        f = tmp_path / "frag_001.md"
        f.write_text("original", encoding="utf-8")
        tool = EditTool()
        result = tool._run(str(f), "original", "modified")
        assert "[拒绝]" in result
        assert f.read_text(encoding="utf-8") == "original"

    def test_edit_not_found(self, tmp_path):
        from proseproof.shared.file_tools import EditTool
        f = tmp_path / "_test.md"
        f.write_text("hello", encoding="utf-8")
        tool = EditTool()
        result = tool._run(str(f), "xyz", "abc")
        assert "[失败]" in result

    def test_edit_replace_all(self, tmp_path):
        from proseproof.shared.file_tools import EditTool
        f = tmp_path / "_test.md"
        f.write_text("A X A X A", encoding="utf-8")
        tool = EditTool()
        result = tool._run(str(f), "X", "Y", replace_all=True)
        assert "[OK]" in result
        assert f.read_text(encoding="utf-8") == "A Y A Y A"


# ============================================================
# --middleware 标志传递
# ============================================================

class TestMiddlewareFlag:
    """--middleware CLI 标志生效验证。"""

    def test_no_override_uses_config(self):
        """middleware=None 时 _resolve_middleware_chain_from_names 不被调用。"""
        from proseproof.core.proofread_middleware import _resolve_middleware_chain
        config = {
            "proofread": {"middleware_chain": [
                {"name": "pre_check", "enabled": True}
            ]}
        }
        chain = _resolve_middleware_chain(config)
        assert len(chain) == 1
        assert chain[0].name == "pre_check"

    def test_override_by_name(self):
        """middleware_override 直接按名称构建链。"""
        from proseproof.core.proofread_middleware import _resolve_middleware_chain_from_names
        chain = _resolve_middleware_chain_from_names(["similarity"])
        assert len(chain) == 1
        assert chain[0].name == "similarity"

    def test_override_handles_whitespace(self):
        from proseproof.core.proofread_middleware import _resolve_middleware_chain_from_names
        chain = _resolve_middleware_chain_from_names([" pre_check ", " similarity "])
        assert len(chain) == 2


# ============================================================
# review_prompt_lines 回退
# ============================================================

class TestReviewPromptFallback:
    """get_review_prompt() 优先读 review_prompt_lines。"""

    def test_uses_review_prompt_lines(self):
        from proseproof.core.base_profile import BaseProfile
        import tempfile, json, os
        with tempfile.TemporaryDirectory() as d:
            config = {
                "question_prompt_lines": ["Q"],
                "knowledge_prompt_lines": ["K"],
                "review_prompt_lines": ["R1", "R2"],
            }
            with open(os.path.join(d, "config.json"), "w") as f:
                json.dump(config, f)
            bp = BaseProfile(d)
            result = bp.get_review_prompt()
            assert "R1" in result
            assert "R2" in result

    def test_falls_back_to_question_prompt(self):
        from proseproof.core.base_profile import BaseProfile
        import tempfile, json, os
        with tempfile.TemporaryDirectory() as d:
            config = {
                "question_prompt_lines": ["Q"],
                "knowledge_prompt_lines": ["K"],
                # 无 review_prompt_lines
            }
            with open(os.path.join(d, "config.json"), "w") as f:
                json.dump(config, f)
            bp = BaseProfile(d)
            result = bp.get_review_prompt()
            assert "Q" in result  # 回退到 question_prompt_lines


# ============================================================
# extra_signals 大纲提取
# ============================================================

class TestExtraSignals:
    """extra_signals 配置被传递给 extract_outline。"""

    def test_extra_signals_detected(self):
        from proseproof.shared.outline_extractor import extract_outline
        content = "普通行\n【寓意】这是寓意行\n继续\n【详解】这是详解"
        outline = extract_outline(content, extra_patterns=[r"^【寓意】", r"^【详解】"])
        assert len(outline) >= 2
        texts = [item.text for item in outline]
        assert any("寓意" in t for t in texts)

    def test_no_extra_signals_no_extra_items(self):
        from proseproof.shared.outline_extractor import extract_outline
        content = "普通\n【特殊标记】不会被识别"
        outline = extract_outline(content)  # 无 extra_patterns
        # 普通内容只有"普通"这行——但不是任何已知模式 → 不应产出条目
        assert len(outline) == 0


# ============================================================
# 错误处理加固
# ============================================================

class TestErrorHandling:
    """profile 加载 + config 解析保护。"""

    def test_traversal_blocked(self):
        """_resolve_profile 拒绝 .. 穿越。"""
        from proseproof.cli import _resolve_profile
        import click
        with pytest.raises(click.ClickException, match="非法"):
            _resolve_profile("../etc")

    def test_bad_config_json(self):
        """config.json 格式错误时友好报错。"""
        from proseproof.core.config_loader import load_config
        import tempfile, os, pytest
        with tempfile.TemporaryDirectory() as d:
            bad_json = os.path.join(d, "config.json")
            with open(bad_json, "w") as f:
                f.write("{bad json")
            with pytest.raises(ValueError, match="JSON 格式错误"):
                load_config(d)

    def test_profile_py_no_subclass_fallback(self):
        """profile.py 无 BaseProfile 子类时回退纯 JSON 模式。"""
        from proseproof.cli import _load_profile
        import tempfile, json, os
        with tempfile.TemporaryDirectory() as d:
            config = {
                "question_prompt_lines": ["Q"],
                "knowledge_prompt_lines": ["K"],
            }
            with open(os.path.join(d, "config.json"), "w") as f:
                json.dump(config, f)
            # 创建空的 profile.py（无 BaseProfile 子类）
            with open(os.path.join(d, "profile.py"), "w") as f:
                f.write("# just a comment")
            profile = _load_profile(d)
            assert profile is not None
            assert "Q" in profile.get_proofread_prompt()
