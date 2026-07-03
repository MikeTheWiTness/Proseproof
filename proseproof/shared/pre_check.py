"""v0.2.0 PreCheck 中间件 —— LLM 前的异常模式标记。

扫描片段原文中的异常模式，生成结构化提示清单注入校对 prompt。
仅标记位置和模式类型，不做错误判定，不设严重级别。

设计决策见 ADR-0008 (PreCheck 仅标记不判定)。
"""
from __future__ import annotations
import re
from proseproof.core.middleware import (
    ProofreadContext, MiddlewareAction, MiddlewareResult, ProofreadMiddleware,
)
from proseproof.core.logging_utils import log


# ============================================================
# 检测规则
# ============================================================

# 连续重复标点（2 次以上）
_CONSECUTIVE_PUNCT_RE = re.compile(r'([。，、！？,\.!?])\1{1,}')

# 连续重复词（≥2 个汉字）
_REPEATED_WORD_RE = re.compile(r'([\u4e00-\u9fff]{2,})\1')


def _check_unpaired_brackets(text: str) -> list[dict]:
    """检测括号/引号不成对。

    使用栈检测括号的嵌套正确性：左括号入栈，右括号匹配栈顶。
    中文括号（（）「」『』）和半角括号（()[]{}）分别处理。
    """
    hints = []
    lines = text.split('\n')

    # 所有括号对的映射
    bracket_map = {
        '（': '）', '「': '」', '『': '』',
        '(': ')', '[': ']', '{': '}',
        '"': '"', '"': '"',
    }
    # 成对引号的特殊处理：不嵌套，计数取奇偶
    quote_pairs = {'"', '"'}

    for left, right in bracket_map.items():
        if left in quote_pairs:
            # 引号：检查总数是否为偶数
            count = text.count(left)
            if count % 2 != 0:
                for i, line in enumerate(lines):
                    if left in line:
                        hints.append({
                            "line": i,
                            "pattern": "unpaired_bracket",
                            "text": line.strip()[:40],
                        })
                        break
        else:
            # 普通括号：栈检测
            stack = []
            for i, line in enumerate(lines):
                for ch in line:
                    if ch == left:
                        stack.append((i, ch))
                    elif ch == right:
                        if not stack:
                            hints.append({
                                "line": i,
                                "pattern": "unpaired_bracket",
                                "text": line.strip()[:40],
                            })
                        else:
                            stack.pop()

            # 栈中仍有未闭合的左括号
            for line_no, ch in stack:
                hints.append({
                    "line": line_no,
                    "pattern": "unpaired_bracket",
                    "text": lines[line_no].strip()[:40],
                })

    return hints


def _check_consecutive_punctuation(text: str) -> list[dict]:
    """检测连续重复标点（如。。，，）。"""
    hints = []
    lines = text.split('\n')
    for i, line in enumerate(lines):
        for m in _CONSECUTIVE_PUNCT_RE.finditer(line):
            hints.append({
                "line": i,
                "pattern": "consecutive_punctuation",
                "text": m.group(),
            })
    return hints


def _check_repeated_words(text: str) -> list[dict]:
    """检测连续重复词（≥2 汉字）。"""
    hints = []
    lines = text.split('\n')
    for i, line in enumerate(lines):
        for m in _REPEATED_WORD_RE.finditer(line):
            hints.append({
                "line": i,
                "pattern": "repeated_word",
                "text": m.group(),
            })
    return hints


def _is_empty(text: str) -> bool:
    """判断是否为空白片段。"""
    return not text or not text.strip()


# ============================================================
# PreCheckMiddleware
# ============================================================

_HINT_PROMPT_TEMPLATE = """\n\n⚠️ 请特别关注以下位置（可能存在异常模式，请自行判断是否为错误）：

{hint_text}
"""


class PreCheckMiddleware:
    """LLM 校对前执行的异常模式标记中间件。

    仅标记位置和模式，不做错误判定。
    """

    name = "pre_check"
    phase = "pre"

    def process(self, ctx: ProofreadContext) -> MiddlewareResult:
        text = ctx.fragment_text

        # 空片段 → 跳过 LLM
        if _is_empty(text):
            log(f"   ⏭️ [pre_check] 空片段，跳过校对")
            ctx.skip_llm = True
            return MiddlewareResult(ctx, MiddlewareAction.SKIP_LLM, "空片段")

        # 收集所有提示
        all_hints = []
        all_hints.extend(_check_unpaired_brackets(text))
        all_hints.extend(_check_consecutive_punctuation(text))
        all_hints.extend(_check_repeated_words(text))

        ctx.pre_check_hints = all_hints

        if all_hints:
            # 注入提示到 prompt
            hint_lines = []
            for h in all_hints:
                hint_lines.append(
                    f"- 第 {h['line'] + 1} 行: [{h['pattern']}] \"{h['text']}\""
                )
            hint_text = '\n'.join(hint_lines)
            ctx.prompt = ctx.prompt + _HINT_PROMPT_TEMPLATE.format(hint_text=hint_text)

            log(f"   🔍 [pre_check] 发现 {len(all_hints)} 个异常模式")
            return MiddlewareResult(
                ctx, MiddlewareAction.CONTINUE,
                f"标记了 {len(all_hints)} 个异常模式",
            )
        else:
            log(f"   ✅ [pre_check] 未发现异常模式")
            return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)
