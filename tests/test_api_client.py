"""TDD: api_client 模块纯函数测试。

覆盖:
  ✅ _classify_error: 异常分类（超时/限流/认证/通用）
  ✅ _should_retry: 可重试性判断
  ✅ _backoff_delay: 指数退避（含上限裁剪）
  ✅ _is_empty_or_duplicate: 空结果/重复检测
  ✅ _compress_history: 对话压缩
  ✅ _extract_usage / _accumulate_usage: Token 用量
"""
import json
import pytest
import requests
from proseproof.core.api_client import (
    _classify_error,
    _should_retry,
    _backoff_delay,
    _is_empty_or_duplicate,
    _compress_history,
    _extract_usage,
    _accumulate_usage,
    APITimeoutError,
    APIRateLimitError,
    APIAuthError,
    ProofreadError,
)


# ============================================================
# _classify_error 测试
# ============================================================

class TestClassifyError:
    """异常分类：原始 Exception → ProofreadError 子类。"""

    def test_timeout_classified(self):
        err = _classify_error(requests.exceptions.Timeout("timeout"))
        assert isinstance(err, APITimeoutError)
        assert err.retryable is True

    def test_connection_error_classified(self):
        err = _classify_error(requests.exceptions.ConnectionError("conn"))
        assert isinstance(err, APITimeoutError)
        assert err.retryable is True

    def test_rate_limit_classified(self):
        resp = requests.Response()
        resp.status_code = 429
        resp._content = b'{"error": "rate limit"}'
        http_err = requests.exceptions.HTTPError("429", response=resp)
        err = _classify_error(http_err)
        assert isinstance(err, APIRateLimitError)
        assert err.retryable is True

    def test_auth_error_401_classified(self):
        resp = requests.Response()
        resp.status_code = 401
        http_err = requests.exceptions.HTTPError("401", response=resp)
        err = _classify_error(http_err)
        assert isinstance(err, APIAuthError)
        assert err.retryable is False

    def test_auth_error_403_classified(self):
        resp = requests.Response()
        resp.status_code = 403
        http_err = requests.exceptions.HTTPError("403", response=resp)
        err = _classify_error(http_err)
        assert isinstance(err, APIAuthError)

    def test_generic_request_error(self):
        err = _classify_error(requests.exceptions.RequestException("generic"))
        assert isinstance(err, ProofreadError)

    def test_unknown_exception(self):
        err = _classify_error(ValueError("unknown"))
        assert isinstance(err, ProofreadError)
        assert "未知错误" in str(err)


# ============================================================
# _should_retry 测试
# ============================================================

class TestShouldRetry:
    """可重试性判断。"""

    def test_retryable_timeout(self):
        err = APITimeoutError("timeout")
        assert _should_retry(err) is True

    def test_retryable_rate_limit(self):
        err = APIRateLimitError("rate")
        assert _should_retry(err) is True

    def test_not_retryable_auth(self):
        err = APIAuthError("auth")
        assert _should_retry(err) is False

    def test_retryable_generic(self):
        err = ProofreadError("generic", retryable=True)
        assert _should_retry(err) is True

    def test_not_retryable_when_false(self):
        err = ProofreadError("custom", retryable=False)
        assert _should_retry(err) is False


# ============================================================
# _backoff_delay 测试
# ============================================================

class TestBackoffDelay:
    """指数退避延迟计算。"""

    def test_retry_0(self):
        delay = _backoff_delay(0, base=2.0)
        assert delay == 2.0

    def test_retry_1(self):
        delay = _backoff_delay(1, base=2.0)
        assert delay == 4.0

    def test_retry_2(self):
        delay = _backoff_delay(2, base=2.0)
        assert delay == 8.0

    def test_max_delay_cap(self):
        """超过 max_delay 时截断到上限。"""
        delay = _backoff_delay(10, base=2.0, max_delay=30.0)
        assert delay == 30.0

    def test_rate_limit_base(self):
        """限流错误使用更大的 base（5.0）。"""
        delay = _backoff_delay(0, base=5.0)
        assert delay == 5.0


# ============================================================
# _is_empty_or_duplicate 测试
# ============================================================

class TestIsEmptyOrDuplicate:
    """空结果/重复结果检测。"""

    def test_none_is_empty(self):
        assert _is_empty_or_duplicate(None, []) is True

    def test_empty_string_is_empty(self):
        assert _is_empty_or_duplicate("", []) is True

    def test_whitespace_is_empty(self):
        assert _is_empty_or_duplicate("   ", []) is True

    def test_empty_marker_detected(self):
        assert _is_empty_or_duplicate("[搜索结果为空] 没有找到", []) is True

    def test_not_found_marker(self):
        assert _is_empty_or_duplicate("[未找到相关内容]", []) is True

    def test_duplicate_detected(self):
        recent = ["result_abc", "another"]
        assert _is_empty_or_duplicate("result_abc", recent) is True

    def test_unique_passes(self):
        recent = ["result_abc"]
        assert _is_empty_or_duplicate("result_xyz", recent) is False

    def test_sympy_json_not_considered_empty(self):
        """SymPy 计算返回的 JSON 不视为空。"""
        assert _is_empty_or_duplicate('{"success": true, "result": "1"}', []) is False

    def test_page_fetch_failure_marker(self):
        assert _is_empty_or_duplicate("[网页抓取失败] 500", []) is True


# ============================================================
# _compress_history 测试
# ============================================================

class TestCompressHistory:
    """对话历史压缩（移除工具调用对）。"""

    def test_removes_tool_messages(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "result"},
            {"role": "assistant", "content": "final answer"},
        ]
        compressed = _compress_history(messages, 1)
        roles = [m["role"] for m in compressed]
        # system + user 保留，有 content 的 assistant 也保留，最后追加摘要 user
        assert "system" in roles
        assert roles.count("user") >= 2  # 原始 user + 摘要 user
        assert "assistant" in roles
        assert "tool" not in roles  # 工具消息被移除
        assert compressed[-1]["role"] == "user"  # 最后是摘要
        assert "工具" in compressed[-1]["content"]

    def test_keeps_system_and_user(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "question"},
        ]
        compressed = _compress_history(messages, 0)
        assert len(compressed) == 3  # system + user + summary
        assert compressed[0]["content"] == "sys"
        assert compressed[1]["content"] == "question"

    def test_summary_contains_tool_count(self):
        messages = [
            {"role": "user", "content": "q"},
        ]
        compressed = _compress_history(messages, 5)
        assert "5 次" in compressed[-1]["content"]


# ============================================================
# _extract_usage / _accumulate_usage 测试
# ============================================================

class TestUsage:
    """Token 用量提取与累加。"""

    def test_extract_normal(self):
        data = {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
        result = _extract_usage(data)
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150

    def test_extract_missing_usage(self):
        result = _extract_usage({})
        assert result["prompt_tokens"] == 0
        assert result["total_tokens"] == 0

    def test_extract_non_dict_usage(self):
        """usage 为非 dict 时返回空 dict。"""
        result = _extract_usage({"usage": None})
        assert result == {}

    def test_accumulate_adds(self):
        total = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        delta = {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75}
        result = _accumulate_usage(total, delta)
        assert result["prompt_tokens"] == 150
        assert result["completion_tokens"] == 75
        assert result["total_tokens"] == 225

    def test_accumulate_from_zero(self):
        total = {}
        delta = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        result = _accumulate_usage(total, delta)
        assert result["prompt_tokens"] == 10
