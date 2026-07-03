"""v0.2.0 Light 内容审查 —— 大纲 + 片段摘要 → LLM 全局语义检测。

第二层审查（A-light）。输入为文档大纲和所有片段的校对摘要，
输出全局问题报告。confidence 上限为 medium。

设计决策见 ADR-0010 (内容审查三层体系)。
"""
from __future__ import annotations
import json
import re
from proseproof.core.logging_utils import log


LIGHT_REVIEW_PROMPT = """你是文档审读专家。以下是文档的结构大纲和每个章节的校对摘要。

请进行全局语义检查，发现以下类型的问题：
1. 章节/片段顺序错误
2. 编号跳号或不连续
3. 术语不一致（同一概念多种称呼）
4. 论点遗漏（大纲承诺但正文未展开）
5. 跨章节的事实矛盾

输出 JSON 格式：

```json
{
  "issues": [
    {
      "type": "term_inconsistency",
      "location": {"fragment_ids": ["frag_001", "frag_003"]},
      "description": "frag_001 使用'用户'，frag_003 使用'使用者'，疑似同一概念",
      "confidence": "medium"
    }
  ]
}
```

规则：
- 只报告你确信的问题，不确定的不要报
- confidence 只能取 "low" 或 "medium"
- 无问题时返回 {"issues": []}
- 只输出 JSON，不要加解释文字
"""


def _parse_review_json(text: str) -> dict:
    """从 LLM 返回中提取审查结果 JSON。"""
    if not text:
        return {"issues": []}

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试 { 到 }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    log("   ⚠️ Light 审查：LLM 返回无法解析")
    return {"issues": []}


def _cap_confidence(issues: list[dict], max_conf: str = "medium") -> list[dict]:
    """将 issues 中的 confidence 截断到指定上限。"""
    levels = {"low": 0, "medium": 1, "high": 2}
    cap = levels.get(max_conf, 1)
    for issue in issues:
        if levels.get(issue.get("confidence", "medium"), 1) > cap:
            issue["confidence"] = max_conf
    return issues


FULL_REVIEW_PROMPT = """你是文档审读专家。以下是文档的结构大纲和每个片段的**完整原文**。

请进行全局深度语义检查，发现以下类型的问题：
1. 章节/片段顺序错误
2. 编号跳号或不连续
3. 术语不一致（同一概念多种称呼）
4. 论点遗漏（大纲承诺但正文未展开）
5. 跨章节的事实矛盾
6. 逻辑断层或论证跳跃

输出 JSON 格式：

```json
{
  "issues": [
    {
      "type": "term_inconsistency",
      "location": {"fragment_ids": ["frag_001", "frag_003"]},
      "description": "frag_001 使用'用户'，frag_003 使用'使用者'，疑似同一概念",
      "confidence": "high"
    }
  ]
}
```

规则：
- 仔细对比全文，只报告你确信的问题
- confidence 可取 "low"、"medium" 或 "high"
- 无问题时返回 {"issues": []}
- 只输出 JSON，不要加解释文字
"""


class LightReview:
    """Light 内容审查器 —— 大纲 + 摘要 → LLM 全局语义检测。

    Args:
        llm_callable: LLM 调用函数 (prompt_text, system_prompt) -> str。
    """

    def __init__(self, llm_callable=None):
        self._llm = llm_callable

    def review(self, outline: list[dict],
               summaries: dict[str, str]) -> dict:
        """执行 Light 内容审查。

        Args:
            outline:    大纲条目列表（outline_to_dict 格式）。
            summaries:  片段 ID → 校对摘要的映射。

        Returns:
            {"issues": [...]} 格式的审查报告。
        """
        if not outline:
            log("   ⏭️ Light 审查：无大纲，跳过")
            return {"issues": []}

        if not self._llm:
            log("   ⚠️ Light 审查：未配置 LLM，跳过")
            return {"issues": []}

        # 构建 prompt
        outline_json = json.dumps(outline, ensure_ascii=False, indent=2)
        summaries_json = json.dumps(summaries, ensure_ascii=False, indent=2)

        full_prompt = (
            LIGHT_REVIEW_PROMPT
            + "\n\n## 文档大纲\n\n```json\n"
            + outline_json
            + "\n```\n\n## 章节校对摘要\n\n```json\n"
            + summaries_json
            + "\n```"
        )

        # 调用 LLM
        try:
            raw_response = self._llm(full_prompt, LIGHT_REVIEW_PROMPT)
        except Exception as e:
            log(f"   ⚠️ Light 审查 LLM 调用失败: {e}")
            return {"issues": []}

        # 解析结果
        report = _parse_review_json(raw_response)
        issues = report.get("issues", [])

        # 截断 confidence
        issues = _cap_confidence(issues, "medium")

        if issues:
            log(f"   📋 Light 审查发现 {len(issues)} 个问题")
        else:
            log(f"   ✅ Light 审查未发现问题")

        return {"issues": issues}


class FullReview(LightReview):
    """Full 内容审查器 —— 大纲 + 全文原文 → LLM 深度全局审查。

    与 LightReview 的区别:
      - 传入完整原文（而非校对摘要），使 LLM 可做深度语义分析
      - confidence 上限为 high（Light 为 medium）
      - 使用 FULL_REVIEW_PROMPT（检查项更全面）
    """

    def review(self, outline: list[dict],
               full_texts: dict[str, str]) -> dict:
        """执行 Full 内容审查。

        Args:
            outline:     大纲条目列表（outline_to_dict 格式）。
            full_texts:  片段 ID → 片段全文的映射。

        Returns:
            {"issues": [...]} 格式的审查报告。
        """
        if not outline:
            log("   ⏭️ Full 审查：无大纲，跳过")
            return {"issues": []}

        if not self._llm:
            log("   ⚠️ Full 审查：未配置 LLM，跳过")
            return {"issues": []}

        # 构建 prompt
        outline_json = json.dumps(outline, ensure_ascii=False, indent=2)
        texts_json = json.dumps(full_texts, ensure_ascii=False, indent=2)

        full_prompt = (
            FULL_REVIEW_PROMPT
            + "\n\n## 文档大纲\n\n```json\n"
            + outline_json
            + "\n```\n\n## 章节全文\n\n```json\n"
            + texts_json
            + "\n```"
        )

        try:
            raw_response = self._llm(full_prompt, FULL_REVIEW_PROMPT)
        except Exception as e:
            log(f"   ⚠️ Full 审查 LLM 调用失败: {e}")
            return {"issues": []}

        report = _parse_review_json(raw_response)
        issues = report.get("issues", [])

        # Full 审查 confidence 上限为 high
        issues = _cap_confidence(issues, "high")

        if issues:
            log(f"   📋 Full 审查发现 {len(issues)} 个问题")
        else:
            log(f"   ✅ Full 审查未发现问题")

        return {"issues": issues}
