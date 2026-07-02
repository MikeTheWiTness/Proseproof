"""批注评审模式工具 —— 批注提取、评审提示词、结果解析。"""
import re


def is_review_mode(source_mode):
    return source_mode == "批注评审"


def extract_comments_from_md(md_text):
    if not md_text:
        return []

    # XML 风格批注标记：<批注 id=N><原>原文</原><改>建议</改></批注>
    pattern = r'<批注\s+id=(\d+)><原>(.*?)</原><改>(.*?)</改></批注>'
    comments = []
    for match in re.finditer(pattern, md_text):
        cid = int(match.group(1))
        original = match.group(2).strip()
        suggestion = match.group(3).strip()
        text = f'"{original}" 应改为 "{suggestion}"' if original and suggestion else suggestion or original
        start = match.start()
        end = match.end()

        context_before = md_text[max(0, start - 50):start]
        context_after = md_text[end:min(len(md_text), end + 50)]

        comments.append({
            "id": cid,
            "text": text,
            "original": original,
            "suggestion": suggestion,
            "position": start,
            "context_before": context_before,
            "context_after": context_after,
        })

    comments.sort(key=lambda c: c["id"])
    return comments


def build_review_prompt(question_md):
    comments = extract_comments_from_md(question_md)

    if comments:
        prompt = """## 任务：批注评审

你是一位资深校对专家。文档中有 **<批注N>修改建议</批注>** 标记，
这是人工校对者在你之前标注的。每个标记**紧跟在被质疑的原文之后**，
标记内的内容是人工提出的修改建议。

### 批注标记的格式说明

举例说明：如果原文是"我们去上学"，人工发现"学"字有误应改为"班"，
标记后会变成：我们去上学<批注1>班</批注1>

解读：标记紧跟在"学"后面 → 人工认为"学"应改为"班"。

### 你的任务

**1. 逐条评审人工批注的正误并说明理由**
对每条批注，判断人工的修改建议是否正确：
- ✅ 正确：问题确实存在，修改建议完全正确
- ⚠️ 部分正确：问题存在但建议不完全准确（在说明中给出正确改法）
- ❌ 有误：问题不成立或建议完全错误（在说明中解释为什么）

**2. 检查批注的修改建议本身是否有错**
例如批注说"改为昆虫"但根据上下文正确的改法是"改为虫蚁"，
此时评判为"部分正确"，说明中写清楚正确的做法。

**3. 补充发现遗漏错误**
找出人工批注**没有标记到**的错误。已被 <批注N> 标记的位置，
人工已经提过修改建议了，你只需要评判其正误，**不要在补充发现中再报告这些位置**。

### 批注列表
"""
        for c in comments:
            prompt += f"\n- **批注{c['id']}**：{c['text']}"

        prompt += """

### 输出格式

---

## 批注评审结果

### 批注1
- 评判：正确 / 部分正确 / 有误
- 说明：理由（若建议有错，说明正确做法）

### 批注2
- 评判：正确 / 部分正确 / 有误
- 说明：理由

...（逐条，一条不落）

### 补充发现

**仅限人工批注未覆盖的遗漏错误。已在 <批注N> 中标记过的位置不得重新报告。**

如有遗漏：

### 标记原文
[逐字抄写全文（保留已有的 <批注N>...</批注> 标记），
仅在批注未覆盖的位置用 【编号|原文|改为】 标记。编号用 ASCII 数字 1、2、3……]

### 修改原因
1. 原因
2. 原因

如无遗漏，写「暂无补充发现」。
"""

    else:
        prompt = """## 任务：全文校对

你是一位资深校对专家，请对以下文本进行全文校对。

### 你的任务
逐字逐句检查，找出所有问题：错别字、漏字多字、标点错误、地名/人名/书名错误、
文言文断句错误、译文错误、答案选项错误、表述不当等。

### 强制返回格式

如有问题，输出：

### 标记原文
[逐字抄写全文，错误处用 【编号|原文字段|修改后文字】 标记]

### 修改原因
1. 原因
2. 原因

如无问题，只输出「无问题」。
"""

    return prompt


def parse_review_result(result_text):
    if not result_text:
        return {"judgments": [], "supplements": []}

    judgments = []
    supplements = []

    comment_pattern = r'###\s*批注(\d+)\s*\n(.*?)(?=\n###|\n##|$)'
    for match in re.finditer(comment_pattern, result_text, re.DOTALL):
        cid = int(match.group(1))
        body = match.group(2)

        verdict_match = re.search(r'评判[：:]\s*(\S+)', body)
        verdict = verdict_match.group(1).strip() if verdict_match else "未评判"

        reason_match = re.search(r'说明[：:]\s*(.+)', body)
        reason = reason_match.group(1).strip() if reason_match else ""

        judgments.append({
            "id": cid,
            "verdict": verdict,
            "reason": reason,
        })

    supp_pattern = r'###\s*补充发现\s*\n(.*?)(?=\n###|\n##|$)'
    supp_match = re.search(supp_pattern, result_text, re.DOTALL)
    if supp_match:
        supp_text = supp_match.group(1)
        for line in supp_text.splitlines():
            line = line.strip()
            if line.startswith("-") or line.startswith("•"):
                item = line[1:].strip()
                if item and "暂无" not in item:
                    supplements.append(item)

    judgments.sort(key=lambda j: j["id"])

    return {
        "judgments": judgments,
        "supplements": supplements,
    }
