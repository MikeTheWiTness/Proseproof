"""知识文档智能切割管线。

管线：
  步骤 1（Python）：全文结构编目 → 列出所有结构信号（标题/编号/段头/嵌入片段标记）
  步骤 2（LLM）：基于结构清单 + 原文头尾，决定切分方案 + 类型标注
  步骤 3（Python）：校验 → 按 LLM 方案切分 → 复核

中间产物（全部落盘到 output/中间产物/{文档名}/）：
  - _knowledge_catalog.json       步骤 1 结构编目清单
  - _knowledge_llm_input.txt      步骤 2 LLM 输入（结构清单 + 段头尾）
  - _knowledge_llm_raw.txt        步骤 2 LLM 原始返回
  - _knowledge_llm_parsed.json    步骤 2 解析后的切分方案
  - _knowledge_anchors.json       步骤 3 锚点校验结果
  - _knowledge_tagged.md          步骤 3 插入标签后的全文
  - _knowledge_verify.json        步骤 3 复核结果
"""

import json
import os
import re
import traceback
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proseproof.core.logging_utils import log


# ============================================================================
# 步骤 1：全文结构编目（纯 Python，通用——不针对任何特定文档格式）
# ============================================================================

# 标题行（## 到 ######）
_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)')

# （N）编号的条目（如"（1）《庄子》"浑沌之死""）
_ITEM_NUMBER_RE = re.compile(r'^（(\d+)）(.+)')

# 知识点固定段头
_KNOWLEDGE_SIGNALS = [
    "【寓意】", "【适用角度】", "【事例句运用】", "【标签化引用】",
    "【文段示例】", "【文段展示】", "【详解】", "【参考例文】",
    "【出题意图】", "❎【误用示例】",
]

# 片段相关标记
_EXAM_SIGNALS = [
    "**审题：**", "**立意：**", "【详解】",
]

# 嵌入片段/补充块的引导语
_EXAM_MARKER_RE = re.compile(
    r'^(补充题[一二三四五六七八九十]+|典型例题[一二三四五六七八九十]+|'
    r'即时练|【出题意图】)'
)

# 方法/步骤标记
_METHOD_SIGNAL_RE = re.compile(r'^(第[一二三四五六七八九十\d]+步|方法[一二三四五六七八九十\d]+)')

# 分班/管理标记（不需要校对的内容）
_SKIP_SIGNALS = [
    "分班型：", "目标双一流班", "目标清北班", "复习25暑讲过的",
    "【原版】", "【新增素材版】", "[运用素材大招：]", "[25暑第五讲：]",
    "[素材组合技巧：]", "解答：  【参考示例】", "解答：  【参考答案】",
]

# 万用主题 / 主题变体
_THEME_SIGNAL_RE = re.compile(r'^\*\*主题[一二三四五六七八九十\d]+：')


def _scan_structure(content: str) -> dict:
    """全文结构编目：列出所有可识别的结构信号，不做切割。

    返回一份"文档目录清单"，每条记录包含：
      - line: 行号（0-based）
      - type: 信号类型（heading / item_number / knowledge_signal /
              exam_marker / method_step / theme_variant / skip_signal / unknown）
      - level: 标题层级（仅 heading 类型有效，1-6）
      - text: 该行的 stripped 文本

    Returns:
        {
          "catalog": [ {...}, {...}, ... ],
          "total_lines": int,
          "total_chars": int,
        }
    """
    lines = content.split("\n")
    catalog = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        entry = {"id": f"L{i:04d}", "line": i, "text": stripped}

        # 1. 标题行
        m = _HEADING_RE.match(stripped)
        if m:
            entry["type"] = "heading"
            entry["level"] = len(m.group(1))
            catalog.append(entry)
            continue

        # 2. （N）编号条目
        m = _ITEM_NUMBER_RE.match(stripped)
        if m:
            entry["type"] = "item_number"
            entry["number"] = int(m.group(1))
            entry["title"] = m.group(2).strip()
            catalog.append(entry)
            continue

        # 3. 知识点固定段头
        if any(stripped.startswith(s) for s in _KNOWLEDGE_SIGNALS):
            entry["type"] = "knowledge_signal"
            catalog.append(entry)
            continue

        # 4. 嵌入片段标记
        if _EXAM_MARKER_RE.match(stripped):
            entry["type"] = "exam_marker"
            catalog.append(entry)
            continue

        # 5. 片段信号
        if any(s in stripped for s in _EXAM_SIGNALS):
            entry["type"] = "exam_signal"
            catalog.append(entry)
            continue

        # 6. 方法/步骤标记
        if _METHOD_SIGNAL_RE.match(stripped):
            entry["type"] = "method_step"
            catalog.append(entry)
            continue

        # 7. 主题变体
        if _THEME_SIGNAL_RE.match(stripped):
            entry["type"] = "theme_variant"
            catalog.append(entry)
            continue

        # 8. 管理/跳过类信号
        if any(stripped.startswith(s) for s in _SKIP_SIGNALS):
            entry["type"] = "skip_signal"
            catalog.append(entry)
            continue

        # 9. 未分类（普通正文）
        entry["type"] = "content"
        catalog.append(entry)

    return {
        "catalog": catalog,
        "total_lines": len(lines),
        "total_chars": len(content),
    }


# ============================================================================
# 步骤 2：LLM 切分方案决策
# ============================================================================

_LLM_SPLIT_PROMPT = """你是文档结构分析专家。系统已为一份知识文档做了全文结构编目。
你需要基于编目清单，决定这份文档应该如何切分为独立的校对单元。

## 输入

系统会提供：
1. 结构编目清单（每行一条记录：行号、信号类型、文本）
2. 部分段落的头尾上下文（用于确认边界）

## 切分原则

一个「校对单元」应该是内容上自封闭、LLM 可以在一次校对中完整处理的块。

- 每个 **（N）素材条目**（item_number 行）及其全部附属内容（寓意、角度、例句、文段示例）组成一个独立的校对单元
- 嵌入在素材条目中的补充题/例题**保留在原单元内**，不走独立校对
- 独立的**纯例题模块**（exam_marker 行，前后无素材讲解上下文）单独切出，走独立校对
- 文档开头的引导语/方法讲解（无 item_number 父级的内容）作为独立单元
- **skip_signal** 标记的内容可以不校对（分班标签、复习提示等管理信息）

## 输出格式

严格 JSON。只输出 unit 数组，每个 unit 只有 id 和 type：
{"units": [{"id":"L0016","type":"knowledge"}, ...]}

## 不该切的嵌入题（保留在素材单元内）

以下补充题嵌入在素材条目中，**不要**作为独立 unit：
- L0058 补充题一 → 嵌入在 L0042 汉阴丈人素材内
- L0094 补充题二 → 嵌入在 L0080 樗树无用之用素材内
- L0623 补充题四 → 嵌入在 L0587 萨特存在主义素材内

## 该切的独立练习题

以下标记是独立练习题块，应切为 exam：
- ### 练1 / ### 练2 / ### 练3 / ### 练4
- 补充题五（L0715，后无素材直接跟练习题）
- 补充题七（文档末尾独立作文题）

## 通用规则

- ### / #### 是容器层，不作为独立 unit，只切其下的 （N）素材条目
- 文档开头引导内容合并到 L0016 之前的同个知识单元
- 不确定类型选 knowledge
"""


def _build_llm_input(catalog: list[dict], content: str) -> str:
    """基于结构编目清单 + 原文段头尾，构建 LLM 输入。"""
    lines = content.split("\n")
    parts = []

    # 编目清单摘要
    parts.append("## 结构编目清单\n")
    type_counts = {}
    for entry in catalog:
        t = entry["type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    parts.append(f"信号统计: {json.dumps(type_counts, ensure_ascii=False)}\n")
    parts.append("---\n")
    for entry in catalog:
        level_str = str(entry.get('level', '')) if entry.get('level') else ''
        eid = entry.get('id', f'L{entry["line"]:04d}')
        # 只发送关键边界类型的条目（非 content），大幅缩减输入
        if entry['type'] in ('heading', 'item_number', 'exam_marker'):
            parts.append(
                f"{eid}  [{entry['type']:18s}]  "
                f"{level_str:1s}  {entry['text'][:100]}"
            )

    # 段落上下文：给 LLM 确认边界用的原文内容
    parts.append("\n\n## 段落上下文（关键边界行的原文内容，用于确认切分）\n")
    boundary_indices = sorted(set(
        e["line"] for e in catalog
        if e["type"] in ("heading", "item_number", "exam_marker")
    ))
    for idx in boundary_indices:
        eid = f'L{idx:04d}'
        start = max(0, idx - 1)
        end = min(len(lines), idx + 5)
        snippet = "\n".join(f"L{i}: {lines[i]}" for i in range(start, end))
        parts.append(f"--- boundary {eid} ---\n{snippet}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _dump_intermediate(filename: str, content: str, doc_name: str = "") -> None:
    """保存中间产物到 output/中间产物/{doc_name}/ 目录。"""
    try:
        if doc_name:
            base = Path("output") / "中间产物" / doc_name
        else:
            base = Path("output") / "中间产物" / "knowledge_split"
        base.mkdir(parents=True, exist_ok=True)
        (base / filename).write_text(content, encoding="utf-8")
        log(f"   📄 中间产物已保存: {base / filename}")
    except Exception as e:
        log(f"   ⚠️ 保存中间产物失败: {e}")


def _infer_block_type(snippet: str) -> str:
    """从块内容的结构信号推断类型。"""
    problem_signals = ["【详解】", "**审题：**", "**立意：**", "【参考例文】"]
    knowledge_signals = ["【寓意】", "【适用角度】", "【事例句运用】", "【标签化引用】"]

    p_score = sum(1 for s in problem_signals if s in snippet)
    k_score = sum(1 for s in knowledge_signals if s in snippet)

    if p_score > k_score and p_score >= 2:
        return "problem_strip"
    return "knowledge"


# ---------------------------------------------------------------------------
# 步骤 3：切分执行 + 标签插入 + 复核
# ---------------------------------------------------------------------------

def _execute_split(content: str, catalog: list[dict],
                   units: list[dict], doc_name: str = "") -> list[dict]:
    """根据 LLM 返回的 units 执行切分。

    每个 unit 的 id 对应 catalog 中一条记录的行号，
    切分边界在相邻 unit 的起始行之间。

    Returns:
        [{ "content": "...", "type": "knowledge" | "problem_strip" }, ...]
    """
    lines = content.split("\n")
    total_lines = len(lines)

    # 构建 catalog id → line 映射
    id_to_line = {}
    for entry in catalog:
        eid = entry.get("id", f'L{entry["line"]:04d}')
        id_to_line[eid] = entry["line"]

    # 获取每个 unit 的起始行和类型，按行号排序
    unit_boundaries = []
    for u in units:
        uid = u.get("id", "")
        line_no = id_to_line.get(uid)
        if line_no is not None:
            unit_boundaries.append({
                "line": line_no,
                "type": u.get("type", "knowledge"),
                "id": uid,
            })

    if not unit_boundaries:
        # LLM 没返回有效 unit → 全文作为一个单元
        return [{"content": content, "type": _infer_block_type(content)}]

    unit_boundaries.sort(key=lambda u: u["line"])

    # 切分：每个 unit 从它的起始行到下一个 unit 的起始行之前
    results = []
    for i, ub in enumerate(unit_boundaries):
        start_line = ub["line"]
        end_line = unit_boundaries[i + 1]["line"] if i + 1 < len(unit_boundaries) else total_lines
        # 不包含下一个 unit 的起始行
        slice_lines = lines[start_line:end_line]
        slice_content = "\n".join(slice_lines).strip()
        if slice_content:
            results.append({
                "content": slice_content,
                "type": ub["type"],
            })

    # 如果第一个 unit 不是从第 0 行开始，把前面的引导内容也加入
    if unit_boundaries and unit_boundaries[0]["line"] > 0:
        preamble_lines = lines[:unit_boundaries[0]["line"]]
        preamble = "\n".join(preamble_lines).strip()
        if preamble:
            # 前面导语并入第一个单元
            results[0]["content"] = preamble + "\n" + results[0]["content"]

    # ---- 尾部标题修剪：将 unit 末尾的容器标题/分割线剥离，归入下一个 unit 头部 ----
    # 收集 LLM 未作为 unit 边界的 heading 行号（即被跳过的容器标题）
    boundary_line_set = set(ub["line"] for ub in unit_boundaries)
    skipped_headings = [
        e for e in catalog
        if e["type"] == "heading" and e["line"] not in boundary_line_set
    ]
    skipped_heading_texts = set(e["text"] for e in skipped_headings)

    _HEADING_LINE_RE = re.compile(r'^(#{1,6})\s+')
    # 加粗节标题（如 **1．希腊神话经典** / **主题一：xxx**）
    _BOLD_SECTION_RE = re.compile(r'^\*\*[\d一二三四五六七八九十]+[．、].+\*\*$')

    def _is_tail_header(line_stripped: str) -> bool:
        """判断一行是否为应剥离的尾部标题/分割线。"""
        if not line_stripped:
            return True  # 空行跟随标题一起剥离
        if _HEADING_LINE_RE.match(line_stripped):
            return True
        if line_stripped in skipped_heading_texts:
            return True
        if _BOLD_SECTION_RE.match(line_stripped):
            return True
        return False

    for i in range(len(results) - 1):
        unit_lines = results[i]["content"].split("\n")
        tail_trim = []
        # 从尾部向上找，剥离连续的标题/空行
        while unit_lines:
            last = unit_lines[-1].strip()
            if _is_tail_header(last):
                tail_trim.append(unit_lines.pop())
            else:
                break
        # 如果剥离后尾部全是空行，也剥掉
        while unit_lines and not unit_lines[-1].strip():
            tail_trim.append(unit_lines.pop())

        if tail_trim:
            tail_trim.reverse()
            tail_text = "\n".join(tail_trim).rstrip()
            results[i]["content"] = "\n".join(unit_lines).rstrip()
            if tail_text:
                results[i + 1]["content"] = tail_text + "\n" + results[i + 1]["content"]

    _dump_intermediate("_knowledge_anchors.json",
                       json.dumps({"units": units, "boundaries": unit_boundaries,
                                   "result_count": len(results)},
                                  ensure_ascii=False, indent=2),
                       doc_name)

    return results


def _rule_fallback_split(content: str, catalog: list[dict]) -> list[dict]:
    """规则降级切分（无 LLM 时使用）。

    以 item_number 为主要边界切分，无 item_number 则以 heading 为边界。
    """
    lines = content.split("\n")
    total_lines = len(lines)

    # 优先用 item_number 边界
    boundaries = sorted(set(
        e["line"] for e in catalog
        if e["type"] == "item_number"
    ))

    # 无 item_number → 用 heading
    if not boundaries:
        boundaries = sorted(set(
            e["line"] for e in catalog
            if e["type"] == "heading"
        ))

    # 仍无边界 → 全文单单元
    if not boundaries:
        return [{"content": content, "type": _infer_block_type(content)}]

    results = []
    for i, start_line in enumerate(boundaries):
        end_line = boundaries[i + 1] if i + 1 < len(boundaries) else total_lines
        slice_lines = lines[start_line:end_line]
        slice_content = "\n".join(slice_lines).strip()
        if slice_content:
            results.append({
                "content": slice_content,
                "type": _infer_block_type(slice_content),
            })

    # 第一个边界前的内容并入第一个单元
    if boundaries[0] > 0:
        preamble = "\n".join(lines[:boundaries[0]]).strip()
        if preamble and results:
            results[0]["content"] = preamble + "\n" + results[0]["content"]

    return results


# ---------------------------------------------------------------------------
# 完整管线
# ---------------------------------------------------------------------------

def knowledge_split(content: str, llm_callable=None,
                    doc_name: str = "") -> list[dict]:
    """知识文档智能切割完整管线。

    步骤：
      1. 程序粗拆：全文结构编目（列出所有结构信号）
      2. LLM 决策：发送 catalog + 段头尾 → LLM 决定切分方案
      3. 程序执行：按 LLM 方案切分 + 校验

    Args:
        content: 原始 Markdown 文本
        llm_callable: LLM 调用函数，签名为 (user_text, system_prompt) -> str
                      为 None 时使用规则降级切分
        doc_name: 文档名（用于中间产物路径）

    Returns:
        [{ "content": "...", "type": "knowledge" | "problem_strip" }, ...]
    """
    log("📐 知识切割管线启动...")

    # ---- 步骤 1：程序粗拆 - 结构编目 ----
    log("   📊 步骤 1：结构扫描 + 编目...")
    scan = _scan_structure(content)
    catalog = scan["catalog"]
    log(f"   📊 编目完成: {len(catalog)} 条信号, {scan['total_lines']} 行")

    _dump_intermediate("_knowledge_catalog.json",
                       json.dumps(scan, ensure_ascii=False, indent=2),
                       doc_name)

    # ---- 步骤 2：LLM 决策 - 发送 catalog + 段头尾 ----
    units = []
    if llm_callable:
        log("   🤖 步骤 2：发送编目 + 段头尾给 LLM 决策切分方案...")
        llm_input = _build_llm_input(catalog, content)
        _dump_intermediate("_knowledge_llm_input.txt", llm_input, doc_name)

        try:
            raw = llm_callable(llm_input, _LLM_SPLIT_PROMPT)
            _dump_intermediate("_knowledge_llm_raw.txt", raw, doc_name)

            # 解析 JSON
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                parsed = json.loads(json_match.group(0))
                units = parsed.get("units", [])
                _dump_intermediate("_knowledge_llm_parsed.json",
                                   json.dumps(parsed, ensure_ascii=False, indent=2),
                                   doc_name)
                log(f"   🤖 LLM 返回 {len(units)} 个切分单元")
            else:
                log("   ⚠️ LLM 返回中未找到 JSON，降级为规则切分")
                _dump_intermediate("_knowledge_llm_parse_error.txt",
                                   f"未找到 JSON 块\n\n原始返回:\n{raw}", doc_name)
        except Exception as e:
            log(f"   ⚠️ LLM 调用失败: {e}，降级为规则切分")
            _dump_intermediate("_knowledge_llm_error.txt",
                               f"调用异常: {e}\n\n{traceback.format_exc()}",
                               doc_name)
            units = []
    else:
        log("   📋 步骤 2：无 LLM，使用规则降级切分")

    # ---- 步骤 3：执行切分 + 校验 ----
    log("   🔗 步骤 3：执行切分 + 校验...")

    if units:
        results = _execute_split(content, catalog, units, doc_name)
    else:
        results = _rule_fallback_split(content, catalog)

    # 校验：确保至少有一个单元
    if not results:
        log("   ⚠️ 切分未产生有效单元，降级为单单元")
        results = [{"content": content, "type": _infer_block_type(content)}]

    # 类型统计
    type_counts = {}
    for r in results:
        t = r.get("type", "knowledge")
        type_counts[t] = type_counts.get(t, 0) + 1
    log(f"   ✅ 切割完成: {len(results)} 个单元 ({type_counts})")

    return results


def knowledge_split_smart(md_content: str, api_url: str, api_key: str,
                          model: str, md_file: str = None) -> list[dict]:
    """智能切割的对外接口：含 LLM 调用。

    与 smart_split.py 的 smart_split() 接口一致，方便 subject.py 中替换调用。
    """
    from proseproof.core.api_client import call_api

    doc_name = ""
    if md_file:
        doc_name = Path(md_file).stem
        if doc_name.endswith("_raw"):
            doc_name = doc_name[:-4]

    def _llm_call(user_text: str, system_prompt: str) -> str:
        try:
            result = call_api(
                api_url=api_url,
                api_key=api_key,
                model=model,
                md_text=user_text,
                images=[],
                q_title="知识切割",
                system_prompt=system_prompt,
                tools=[],
                max_loops=1,
                max_tokens=4096,
                output_dir=str(Path("output") / "中间产物" / doc_name) if doc_name else None,
            )
            content = result.get("content", "")
            if result.get("tool_calls_log"):
                _dump_intermediate("_knowledge_llm_tool_calls.json",
                                   json.dumps(result["tool_calls_log"],
                                              ensure_ascii=False, indent=2),
                                   doc_name)
            return content
        except Exception as e:
            log(f"   ❌ LLM 调用异常: {e}")
            raise

    return knowledge_split(md_content, llm_callable=_llm_call, doc_name=doc_name)
