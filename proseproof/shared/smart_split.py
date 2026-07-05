"""v0.1.0 deep 分割策略（legacy）—— 全文 LLM <problem> 标签切分。

升级为 DeepSplitStrategy（实现 SplitStrategy 协议），定位为不计成本的
全自动兜底策略。当 smart 模式（大纲驱动）无法可靠切分时使用。

注意: v0.2.0 新增的 SmartSplitStrategy（smart_split_v2.py）是大纲驱动
的主力模式，本模块保留作为 deep 模式的内部实现。
"""
import re
import os
from pathlib import Path
from proseproof.core.logging_utils import log
from proseproof.core.api_client import call_api


DEEP_SPLIT_PROMPT = """你是专业的文档结构分析专家。请在给定的文档原文中，用 <problem></problem> 标签标记每个完整的片段单元。

规则：
1. **绝对不修改原文任何一个字**，只在片段边界插入标签
2. 每个完整片段单元（一篇文言文+几道小题、一首诗+鉴赏题等 + 该部分的答案解析）用一对 <problem> 标签包裹
3. **答案解析是片段的一部分**：如果某道题后面紧跟着答案、解析、参考答案等内容，必须将它们也包含在同一个 <problem> 标签内
4. **仅跳过**：文档级别的标题、总分说明等全局信息。这些不属于任何一个片段
5. 标签必须单独占一行，不要和正文混在一起
6. 输出完整的带标签文本，不要加其他解释

示例：
```
这是引言，不标记
<problem>
例1 片段内容...
</problem>
中间过渡文字，不标记
<problem>
例2 片段内容...
</problem>
结尾总结，不标记
```"""


SMART_SPLIT_MAX_TOKENS = 16384  # deepseek 等模型输出上限通常为 8K-16K


def parse_problem_tags(text):
    pattern = r"<problem>(.*?)</problem>"
    matches = re.findall(pattern, text, re.DOTALL)
    return [{"content": m.strip()} for m in matches]


def _dump_smart_split_raw(raw_text, md_file, label=""):
    """将 LLM 返回的原始标注文本保存到 output/中间产物/{文档名}/ 目录。"""
    try:
        # 从 md_file 中提取文档名（去掉 _raw 后缀 或 直接用 basename）
        if md_file:
            doc_name = Path(md_file).stem
            # 去掉 _raw 后缀
            if doc_name.endswith("_raw"):
                doc_name = doc_name[:-4]
        else:
            doc_name = "未命名文档"
        base_dir = Path("output") / "中间产物" / doc_name
        base_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{label}" if label else ""
        dump_path = base_dir / f"_smart_split_raw{suffix}.md"
    except Exception:
        dump_path = Path("output") / "中间产物" / "_smart_split_raw.md"
        Path("output").mkdir(parents=True, exist_ok=True)

    dump_path.write_text(raw_text or "(空)", encoding='utf-8')
    log(f"   📄 智能分割原始输出已保存: {dump_path}")


def smart_split_with_callable(md_content, llm_callable, md_file=None):
    for attempt in range(2):
        try:
            result_text = llm_callable(md_content, DEEP_SPLIT_PROMPT)
        except Exception as e:
            log(f"   ⚠️ 智能分割第 {attempt+1} 次调用失败: {e}")
            continue

        _dump_smart_split_raw(result_text, md_file, label=f"attempt{attempt+1}")

        problems = parse_problem_tags(result_text)
        problems = [p for p in problems if p["content"].strip()]
        if problems:
            log(f"   ✅ 智能分割成功，识别到 {len(problems)} 个片段单元")
            return problems

        log(f"   ⚠️ 第 {attempt+1} 次未识别到有效片段标记")

    log(f"   ⚠️ 智能分割失败，降级为单单元")
    return [{"content": md_content}]


def smart_split(md_content, api_url, api_key, model, md_file=None):
    def _llm_call(text, prompt):
        api_result = call_api(
            api_url, api_key, model,
            text, [], "智能分割",
            prompt, tools=[], max_loops=1,
            max_tokens=SMART_SPLIT_MAX_TOKENS,
        )
        return api_result["content"]

    return smart_split_with_callable(md_content, _llm_call, md_file=md_file)


# ============================================================
# DeepSplitStrategy —— SplitStrategy 协议实现（v0.2.0）
# ============================================================

class DeepSplitStrategy:
    """deep 分割策略：全文 LLM <problem> 标签切分。

    实现 SplitStrategy 协议。不计成本的全自动兜底策略——
    当 smart 模式（大纲驱动）无法可靠切分时使用。
    """

    def __init__(self, api_url: str = "", api_key: str = "",
                 model: str = "", md_file: str = None):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.md_file = md_file

    def split(self, content: str, config: dict) -> list[dict]:
        """执行 deep 分割。

        Args:
            content: Markdown 文档全文。
            config:  Profile 配置字典。

        Returns:
            片段列表，每个 dict 含 content 字段。
        """
        def _llm_call(text, prompt):
            api_result = call_api(
                self.api_url, self.api_key, self.model,
                text, [], "deep分割",
                prompt, tools=[], max_loops=1,
                max_tokens=SMART_SPLIT_MAX_TOKENS,
            )
            return api_result["content"]

        return smart_split_with_callable(content, _llm_call, md_file=self.md_file)
