"""校对报告格式化工具 —— 日志摘要 + Token 用量统计。

从 defaults.py 和 proofread_middleware.py 中提取的共享函数。
"""
from __future__ import annotations


def format_tool_calls_summary(tool_calls: list) -> str:
    """生成工具调用摘要，追加到校对报告末尾。"""
    if not tool_calls:
        return ""
    lines = [
        "\n\n---\n",
        "\n## 📋 工具调用日志\n\n",
        f"共调用 {len(tool_calls)} 次\n\n",
    ]
    for i, tc in enumerate(tool_calls, 1):
        tool = tc.get("tool", "?")
        args = tc.get("args", {})
        result = tc.get("result", "")
        arg_summary = args.get("query", "") or args.get("url", "") or str(args)[:80]
        result_preview = result[:500].replace("\n", " ").strip()
        lines.append(f"**{i}. {tool}** — `{arg_summary[:100]}`\n\n")
        lines.append(f"> {result_preview}\n\n")
    return "".join(lines)


def format_usage_summary(usage: dict) -> str:
    """格式化 token 用量统计。"""
    if not usage:
        return ""
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", 0)
    if total == 0:
        return ""
    lines = ["\n\n---\n", "## 📊 Token 用量统计\n\n"]
    lines.append("| 类型 | Token 数 |\n|------|----------|\n")
    lines.append(f"| 提示词 (prompt) | {prompt:,} |\n")
    lines.append(f"| 生成 (completion) | {completion:,} |\n")
    lines.append(f"| **总计** | **{total:,}** |\n")
    return "".join(lines)
