import os, re, json


def _is_no_issue(res: str) -> bool:
    """判断 LLM 返回是否表示「无问题」。
    
    用于格式审查和解析流程的统一判定。
    """
    if not res:
        return False
    stripped = res.strip()
    if stripped == "无问题":
        return True
    if stripped.startswith("无问题") and len(stripped) <= 10:
        return True
    return False


def _circle_to_int(ch: str) -> int | None:
    code = ord(ch)
    if 0x2460 <= code <= 0x2473:
        return code - 0x2460 + 1
    return None


def _parse_marker_num(s: str) -> int:
    n = _circle_to_int(s[0])
    if n is not None:
        return n
    return int(s)


def _parse_reason_meta(reason: str):
    """从原因文本中提取 type 和 severity 元数据。

    支持的格式：
      - [error|critical] 原因...  → type=error, severity=critical
      - [suggestion] 原因...      → type=suggestion, severity=major（默认）
      - [minor] 原因...           → type=error（默认）, severity=minor
      - 普通原因...               → type=error, severity=major

    返回 (type, severity, cleaned_reason_text)
    """
    m = re.match(r'^\[(error|suggestion)\|(critical|major|minor|info)\]\s*',
                  reason)
    if m:
        return m.group(1), m.group(2), reason[m.end():]
    m = re.match(r'^\[(error|suggestion)\]\s*', reason)
    if m:
        return m.group(1), "major", reason[m.end():]
    m = re.match(r'^\[(critical|major|minor|info)\]\s*', reason)
    if m:
        return "error", m.group(1), reason[m.end():]
    return "error", "major", reason


def _parse_inline_format(text: str, summary: str) -> dict | None:
    m = re.search(r'\n###\s*修改原因\s*\n', text)
    if not m:
        return None
    marked_section = text[:m.start()]
    reasons_section = text[m.end():]

    marker_pos = re.search(r'^###\s*标记原文\s*\n?', marked_section, re.MULTILINE)
    if marker_pos:
        marked_section = marked_section[marker_pos.end():]

    # 剥离前置参考段落（## 前置参考 → 权威原文 → 字面差异 → ⚠️ → ---）
    # 无论 ### 标记原文 标题放在什么位置，前置参考都不应进入 marked_text
    # 否则 PDF 左栏会灌入大量与校对无关的搜索中间产物
    marked_section = re.sub(
        r'##\s*前置参考[^\n]*\n.*?\n---\n',
        '',
        marked_section,
        count=1,
        flags=re.DOTALL,
    )
    # 如果前置参考后面没有 ---（异常情况），用更激进的方式清理
    marked_section = re.sub(r'^##\s*前置参考[^\n]*\n', '', marked_section)
    marked_section = re.sub(r'^###\s*(?:权威原文|字面差异)[^\n]*\n', '', marked_section)
    marked_section = re.sub(r'^⚠️[^\n]*\n?', '', marked_section)
    marked_section = marked_section.strip()

    marked_section = re.sub(r'^编号：.+\n?', '', marked_section)
    marked_section = re.sub(r'^内容：\n?', '', marked_section)

    reasons_section = re.split(r'\n###\s', reasons_section)[0]
    reasons = {}
    pattern_circled = r'([①-⑳](?:-([①-⑳]))?)\s*(.+?)(?=\n[①-⑳]|\n\d+[\.\)]|\n\n|\Z)'
    if re.search(r'(?:^|\n)[①-⑳]', reasons_section):
        for rm in re.finditer(pattern_circled, reasons_section, re.DOTALL):
            sn = _circle_to_int(rm.group(1)[0])
            en = _circle_to_int(rm.group(2)) if rm.group(2) else sn
            rt = rm.group(3).strip()
            for n in range(sn, (en or sn) + 1):
                reasons[n] = rt
    else:
        pattern_ascii = r'(\d+)(?:\s*[-–]\s*(\d+))?\s*[\.\)\s]\s*(.+?)(?=\n\d+[\.\)\s]|\n\n|\Z)'
        for rm in re.finditer(pattern_ascii, reasons_section, re.DOTALL):
            sn = int(rm.group(1))
            en = int(rm.group(2)) if rm.group(2) else sn
            rt = rm.group(3).strip()
            if not rt:
                continue
            for n in range(sn, en + 1):
                reasons[n] = rt

    corrections = []
    seen_nums = set()

    def _extract(marker):
        num = _parse_marker_num(marker.group(1))
        orig = marker.group(2)
        corr = marker.group(3).strip() if marker.group(3) else ""
        if num not in seen_nums:
            seen_nums.add(num)
            raw_reason = reasons.get(num, "")
            ctype, severity, clean_reason = _parse_reason_meta(raw_reason)
            corrections.append({
                "num": num,
                "type": ctype,
                "severity": severity,
                "original": orig,
                "correction": corr,
                "reason": clean_reason,
            })
        return ""

    _clean_marked = re.sub(r'【(\d+)\|([^|]*?)\|([^】]*?)】', _extract, marked_section)
    corrections.sort(key=lambda x: x.get("num", 0))

    if not summary and not corrections:
        return None
    return {
        "corrections": corrections,
        "summary": summary or "无问题",
        "marked_text": marked_section.replace('\n', '\\n'),
    }


def _parse_old_format(text: str, summary: str) -> dict | None:
    blocks = re.split(r"\n?(?:###+\s*修改\s*\d+)\s*\n", text)
    corrections = []
    for block in blocks[1:]:
        corr = {}
        cur_field = None
        cur_val = []
        for line in block.strip().split("\n"):
            s = line.strip()
            matched = False
            for prefix, field in [("- **类型**:", "type"), ("- **原文**:", "original"),
                                   ("- **改为**:", "correction"), ("- **原因**:", "reason"),
                                   ("- **位置**:", "location")]:
                if s.startswith(prefix):
                    if cur_field and cur_val:
                        v = "\n".join(cur_val)
                        if cur_field in ("original", "correction", "location"):
                            m = re.search(r"``(.+?)``", v) or re.search(r"`([^`]+)`", v)
                            corr[cur_field] = m.group(1) if m else v
                        else:
                            corr[cur_field] = v
                    cur_field = field
                    cur_val = [s.split(":", 1)[1].strip() if ":" in s else ""]
                    matched = True
                    break
            if not matched and cur_field:
                cur_val.append(s)
        if cur_field and cur_val:
            v = "\n".join(cur_val)
            if cur_field in ("original", "correction", "location"):
                m = re.search(r"``(.+?)``", v) or re.search(r"`([^`]+)`", v)
                corr[cur_field] = m.group(1) if m else v
            else:
                corr[cur_field] = v
        if corr.get("original") or corr.get("location"):
            corr.setdefault("type", "text")
            corr.setdefault("correction", "")
            corr.setdefault("reason", "")
            corrections.append(corr)
    if not summary and not corrections:
        return None
    return {"corrections": corrections, "summary": summary or "无问题"}


def parse_proofread_md(text: str):
    if not text or not text.strip():
        return None
    text = text.strip()
    summary = ""
    for kw in ["严重错误", "一般问题", "轻微问题", "无问题"]:
        if kw in text:
            summary = kw
            break

    # "无问题" 快速通道：仅当 LLM 真只输出"无问题"（无标记、无修改原因）时生效。
    # 若有标记混在文中，走正常内联解析流程，避免丢失校正数据。
    has_markers = bool(re.search(r'【\d+\|.*\|[^】]*】', text))
    has_reasons = bool(re.search(r'###\s*修改原因', text))
    if summary == "无问题" and not has_markers and not has_reasons:
        return {"corrections": [], "summary": "无问题", "marked_text": ""}

    if "### 标记原文" in text and re.search(r'【\d+\|.*\|[^】]*】', text):
        result = _parse_inline_format(text, summary)
        if result:
            return result

    # 兜底：即使缺少 ### 标记原文 标题，只要有 【N|原文|改为】 标记 +
    # ### 修改原因 段落，也能提取校对数据。格式修正 LLM 常常忘记加
    # 标记原文标题但实际内容已在文中。
    if re.search(r'【\d+\|.*\|[^】]*】', text) and re.search(r'###\s*修改原因', text):
        result = _parse_inline_format(text, summary)
        if result:
            return result

    return _parse_old_format(text, summary)


def extract_json(text: str):
    return parse_proofread_md(text)


def save_proofread_json(res: str, q_dir: str, tool_calls: list | None = None):
    data = extract_json(res)

    # 始终尝试批注评审格式解析（## 批注评审结果 / ### 批注N / 评判 / 说明 / ### 补充发现）
    # 与普通校对格式（### 标记原文 + 【N|原文|改为】）可以共存于同一 LLM 输出
    has_review_markers = ("批注评审结果" in res or bool(re.search(r'###\s*批注\d+', res)))
    if has_review_markers:
        try:
            from proseproof.shared.review_mode import parse_review_result
            review = parse_review_result(res)
            if review.get("judgments") or review.get("supplements"):
                if data is None:
                    data = {
                        "corrections": [],
                        "summary": review.get("summary_hint", "批注评审完成"),
                        "marked_text": "",
                        "review_judgments": review["judgments"],
                        "review_supplements": review["supplements"],
                    }
                else:
                    # 合并：普通校对解析的 corrections + 批注评审的 review_judgments/supplements
                    data["review_judgments"] = review["judgments"]
                    data["review_supplements"] = review["supplements"]
        except ImportError:
            pass

    if data is None:
        return False
    if tool_calls:
        data["tool_calls"] = tool_calls
    json_path = os.path.join(q_dir, "_校对数据.json")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        from proseproof.core.logging_utils import log
        log(f"   ⚠️ [parsing] 校对数据保存失败: {e}")
        return False
