"""批注评审模式 LaTeX 生成器 —— 逐条展开式排版。

排版结构：
1. 原文完整展示（带批注位置标记，圈号①②③...）
2. 批注评审区：按编号逐条展开
   - 原批注内容
   - 评判结果（✅正确 / ❌有误 / ⚠️部分正确）
   - 评判理由
3. 补充发现区
"""
import json
import os
import re
import sys


_CIRCLED_NUMBERS = [
    "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
    "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
]

_LATEX_SPECIAL = [
    ("\\", r"\textbackslash "),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde "),
    ("^", r"\textasciicircum "),
    ("#", r"\#"),
]


def _escape_text(text: str) -> str:
    for char, replacement in _LATEX_SPECIAL:
        text = text.replace(char, replacement)
    return text


def _replace_markers_with_circled_numbers(md_text: str) -> str:
    def _replace(match):
        cid = int(match.group(1))
        if 1 <= cid <= len(_CIRCLED_NUMBERS):
            return _CIRCLED_NUMBERS[cid - 1]
        return f"[{cid}]"

    pattern = r'<批注\s+id=(\d+)>.*?</批注>'
    return re.sub(pattern, _replace, md_text)


def _md_to_latex_simple(md_text: str) -> str:
    text = _escape_text(md_text)
    text = _replace_markers_with_circled_numbers(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', text)
    text = re.sub(r'\*(.+?)\*', r'\\textit{\1}', text)
    text = re.sub(r'^#\s+(.+)$', r'\\section*{\1}', text, flags=re.MULTILINE)
    text = re.sub(r'^##\s+(.+)$', r'\\subsection*{\1}', text, flags=re.MULTILINE)
    text = re.sub(r'^###\s+(.+)$', r'\\subsubsection*{\1}', text, flags=re.MULTILINE)
    text = text.replace("\n", "\\\\\n")
    return text


def _verdict_color(verdict: str) -> str:
    if "正确" in verdict and "部分" not in verdict:
        return "green"
    if "有误" in verdict or "错误" in verdict:
        return "red"
    if "部分" in verdict:
        return "orange"
    return "black"


def _verdict_icon(verdict: str) -> str:
    if "正确" in verdict and "部分" not in verdict:
        return r"\ding{51}"
    if "有误" in verdict or "错误" in verdict:
        return r"\ding{55}"
    if "部分" in verdict:
        return r"\ding{111}"
    return ""


def generate_review_latex(md_path, json_path, output_tex_path, title="批注评审报告"):
    with open(md_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    with open(json_path, 'r', encoding='utf-8') as f:
        review_data = json.load(f)

    judgments = review_data.get("judgments", [])
    supplements = review_data.get("supplements", [])

    judgments.sort(key=lambda j: j.get("id", 0))

    original_latex = _md_to_latex_simple(md_content)

    judgment_blocks = []
    for j in judgments:
        cid = j.get("id", 0)
        verdict = j.get("verdict", "未评判")
        reason = j.get("reason", "")
        comment_text = j.get("comment_text", "")

        circled = _CIRCLED_NUMBERS[cid - 1] if 1 <= cid <= len(_CIRCLED_NUMBERS) else f"[{cid}]"
        color = _verdict_color(verdict)

        block = (
            f"\\subsubsection*{{{circled} 批注{cid}}}\n"
            f"\\textbf{{原批注：}}{_escape_text(comment_text)}\\\\\n"
            f"\\textbf{{评判：}}\\textcolor{{{color}}}{{\\textbf{{{_escape_text(verdict)}}}}}\\\\\n"
        )
        if reason:
            block += f"\\textbf{{理由：}}{_escape_text(reason)}\\\\\n"
        block += "\\vspace{0.5em}\n"
        judgment_blocks.append(block)

    supplement_block = ""
    if supplements:
        items = "\n".join(f"\\item {_escape_text(s)}" for s in supplements)
        supplement_block = (
            f"\\section*{{补充发现}}\n"
            f"\\begin{{itemize}}\n"
            f"{items}\n"
            f"\\end{{itemize}}\n"
        )

    template = _get_review_template()
    tex = template.format(
        title=_escape_text(title),
        original_content=original_latex,
        judgment_sections="\n".join(judgment_blocks),
        supplement_section=supplement_block,
    )

    with open(output_tex_path, 'w', encoding='utf-8') as f:
        f.write(tex)

    return output_tex_path


_REVIEW_TEMPLATE = r"""
\documentclass[12pt,a4paper]{{article}}
\usepackage[UTF8]{{ctex}}
\usepackage{{geometry}}
\usepackage{{xcolor}}
\usepackage{{pifont}}
\usepackage{{enumitem}}
\usepackage{{hyperref}}

\geometry{{left=2.5cm,right=2.5cm,top=2.5cm,bottom=2.5cm}}

\title{{{title}}}
\date{{}}

\begin{{document}}

\maketitle

\section*{{原文}}

{original_content}

\vspace{{1em}}
\hrule
\vspace{{1em}}

\section*{{批注评审}}

{judgment_sections}

{supplement_section}

\end{{document}}
"""


def _get_review_template():
    return _REVIEW_TEMPLATE
