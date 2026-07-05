"""v0.2.0 smart 分割策略 —— 大纲驱动 LLM 切分。

三步流水线:
  1. Python 提取大纲（~500 tokens）
  2. 大纲 → LLM 边界决策（JSON: units with start_line/end_line）
  3. Python 按行号执行切分

失败回退: 按大纲条目边界做规则切分。

设计决策见 ADR-0009 (分割模式矩阵)。
"""
from __future__ import annotations
import json
import re
from proseproof.shared.outline_extractor import extract_outline, outline_to_dict
from proseproof.core.logging_utils import log


# LLM 提示词
SMART_SPLIT_PROMPT = """你是文档结构分析专家。以下是文档的结构大纲（JSON 格式），
每个条目包含 index（序号）、level（层级）、item_type（类型）、text（文本）、
line_start（起始行号）、line_end（结束行号）。

请判断哪些大纲条目应该合并为一个校对单元，输出 JSON 格式的切分方案：

```json
{
  "units": [
    {"start_line": 0, "end_line": 42},
    {"start_line": 43, "end_line": 120}
  ]
}
```

规则：
1. 每个 unit 的 start_line 取第一个条目的 line_start，end_line 取最后一个条目的 line_end
2. 语义上相关的条目（如同一章节的标题+编号项）应合并到同一个 unit
3. 不同主题/章节的条目应分到不同 unit
4. 前言（第一个标题之前的编号项）通常单独成 unit 或合并到第一个标题 unit
5. 只输出 JSON，不要加解释文字
"""


def _parse_llm_json(text: str) -> dict | None:
    """尝试从 LLM 返回文本中提取 JSON。

    处理 LLM 常见的输出模式：
      - 纯 JSON
      - ```json ... ``` 包裹
      - JSON 前后夹带解释文字
    """
    if not text:
        return None

    # 尝试 1: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试 2: 提取 ```json ... ``` 代码块
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试 3: 查找第一个 { 到最后一个 } 之间的内容
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _fallback_split_by_outline(content: str) -> list[dict]:
    """降级策略：按大纲条目边界直接切分。"""
    from proseproof.shared.heading_split import HeadingSplitStrategy
    strategy = HeadingSplitStrategy()
    fragments = strategy.split(content, {})
    if fragments:
        return fragments
    # 如果 heading 切分也失败了，整篇作为一个片段
    return [{"content": content, "heading": None}]


class SmartSplitStrategy:
    """大纲驱动 LLM 切分策略。

    实现 SplitStrategy 协议。

    Args:
        llm_callable: 可选的 LLM 调用函数 (content, prompt) -> str。
                      在测试中用 mock 注入；生产环境中由 BaseProfile 提供。
    """

    def __init__(self, llm_callable=None):
        self._llm = llm_callable

    def split(self, content: str, config: dict) -> list[dict]:
        """通过大纲驱动 LLM 切分文档。

        Args:
            content: Markdown 文档全文。
            config:  Profile 配置。

        Returns:
            片段列表。
        """
        if not content or not content.strip():
            return []

        lines = content.split('\n')
        total_lines = len(lines)

        # Step 1: 提取大纲
        max_depth = config.get("split", {}).get("outline", {}).get("max_depth", 4)
        extra_signals = config.get("split", {}).get("outline", {}).get("extra_signals", [])
        outline = extract_outline(content, max_depth=max_depth, extra_patterns=extra_signals)
        if not outline:
            # 无结构 → 降级
            log("   ⚠️ smart 分割未检测到文档结构，降级为 heading 切分")
            return _fallback_split_by_outline(content)

        outline_data = outline_to_dict(outline)
        outline_json = json.dumps(outline_data, ensure_ascii=False, indent=2)

        # Step 2: 调用 LLM（最多重试 2 次）
        units = None
        raw_response = ""

        for attempt in range(2):
            try:
                if self._llm:
                    raw_response = self._llm(outline_json, SMART_SPLIT_PROMPT)
                else:
                    # 无 LLM callable（测试或未配置）→ 降级
                    log("   ⚠️ 未配置 LLM，降级为大纲规则切分")
                    return _fallback_split_by_outline(content)
            except Exception as e:
                log(f"   ⚠️ smart 分割 LLM 调用失败 (attempt {attempt + 1}): {e}")
                if attempt == 1:
                    log("   🔄 降级为大纲规则切分")
                    return _fallback_split_by_outline(content)
                continue

            parsed = _parse_llm_json(raw_response)
            if parsed and "units" in parsed and parsed["units"]:
                units = parsed["units"]
                break
            else:
                log(f"   ⚠️ smart 分割 JSON 解析失败 (attempt {attempt + 1})")

        if not units:
            log("   🔄 LLM 返回无法解析，降级为大纲规则切分")
            return _fallback_split_by_outline(content)

        # Step 3: 按行号执行切分
        fragments = []
        for unit in units:
            start = max(0, unit.get("start_line", 0))
            end = min(total_lines, unit.get("end_line", total_lines - 1) + 1)

            if start >= total_lines:
                continue

            fragment_lines = lines[start:end]
            fragment_content = '\n'.join(fragment_lines).strip()

            if fragment_content:
                fragments.append({
                    "content": fragment_content,
                    "heading": None,  # smart 模式不强制要求 heading
                })

        if not fragments:
            return _fallback_split_by_outline(content)

        log(f"   ✅ smart 分割完成，{len(fragments)} 个片段")
        return fragments
