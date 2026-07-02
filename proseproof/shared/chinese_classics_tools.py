"""文言文/诗歌校对工具集 —— 文本类型识别、前置搜索、自动 diff。"""
import re
import os
import difflib
import requests
import json

from proseproof.core.logging_utils import log

CLASSICAL_PARTICLES = [
    "之", "乎", "者", "也", "矣", "焉", "哉",
    "其", "而", "于", "以", "为", "所", "耳",
    "乃", "则", "即", "皆", "凡", "诸",
    "何", "孰", "安", "焉", "胡", "奚",
    "不", "弗", "毋", "勿", "未", "非",
    "因", "故", "遂", "乃", "辄", "便",
]

# 批注标记：XML 风格 <批注 id=N><原>原文</原><改>建议</改></批注>
_ANNOTATION_RE = re.compile(r'<批注\s+id=\d+>.*?</批注>', re.DOTALL)

# 下划线/波浪线/强调标记 — 用中文全角括号
_FORMATTING_MARKER_RE = re.compile(r'【(?:波浪线|下划线|加点|/)?】|【/?[波浪线下划线加点]+】')

# Markdown 强调标记和 HTML 标签

# 试题引导语模式
_LEADIN_PATTERNS = [
    # "阅读下面的文言文，完成1-6题" — 允许批注标记插入
    re.compile(r'阅读下面的(?:文言文|古诗|唐诗|宋词|词|诗歌|元曲|散曲|文字|作品|文章|这首词|这首诗)[，。,\.、\s]*完成\d+(?:[—\-～~]\d+)?题[。]?(\[[^\]]*\])?'),
    re.compile(r'阅读下面的(?:文言文|古诗|唐诗|宋词|词|诗歌|元曲|散曲|文字|作品|文章|这首词|这首诗)[，。,\.、\s]*完成下面小?题[。]?'),
    # "二、文言文阅读（本题共4道小题，19分）" 等段落标题
    # 兼容"现代文阅读Ⅰ""文言文阅读Ⅱ"等带罗马数字/数字后缀的标题
    re.compile(r'^[\d一二三四五六七八九十]+[、，\.]\s*(?:文言文|古代诗歌|古诗词|现代文)阅读\s*[ⅠⅡⅢⅣⅤⅥ1-9一二三四五六七八九十]?[、．.]?\s*[（(][^）)]*[）)]'),
    re.compile(r'^[（(]节选自[^）)]*[）)]'),
    # Markdown 粗体标题
    re.compile(r'\*\*[\d一二三四五六七八九十]+、[^*]+\*\*'),
]

def _clean_annotations(text):
    """清理文本中的批注标记、格式标记，方便后续正则匹配。

    XML 风格标记 <批注 id=N>...</批注>，简单正则删除即可（无嵌套歧义）。
    """
    # 反复删除批注标记直到无残留（处理可能的嵌套情况）
    prev = None
    while prev != text:
        prev = text
        text = _ANNOTATION_RE.sub('', text)

    # 清理格式标记（【波浪线】等）
    text = _FORMATTING_MARKER_RE.sub('', text)
    # 清理 Markdown 强调标记（保留内部文字）
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    # 清理 HTML 标签（Pandoc 残留）
    text = re.sub(r'<[^>]+>', '', text)
    # 清理连续逗号/空白
    text = re.sub(r'[,，]{2,}', '，', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text

def _strip_leadin(text):
    """去掉试题引导语，返回正文开头的纯文本片段。"""
    # 先清理批注标记，否则会干扰引导语正则匹配
    result = _clean_annotations(text)
    for pat in _LEADIN_PATTERNS:
        result = pat.sub("", result)
    # 再去掉常见的前缀标注
    result = re.sub(r'^[\(（].*?[\)）]', '', result)
    result = re.sub(r'^[\d一二三四五六七八九十]+[、．.．]\s*', '', result)
    return result.strip()

def extract_text_start_via_api(text, api_url, api_key, model, timeout=15):
    """使用 API 从试题文本中提取文言文/诗歌正文的开头 20 字。

    用于生成精准的搜索关键词，避免引导语干扰。
    返回提取到的开头文本，失败返回 None。
    """
    # 清理批注标记，避免干扰 LLM 判断
    clean_text = _clean_annotations(text)
    sample = clean_text[:300]

    system_prompt = (
        "你是一个文本提取器。只输出 JSON，不输出任何其他内容。"
    )

    user_prompt = (
        "从以下试题文本中，提取文言文/古诗/词/曲的正文开头。\n"
        "\n"
        "规则：\n"
        "1. 去掉\"阅读下面的文言文，完成1-4题\"等引导语\n"
        "2. 去掉题号（如\"一、\"\"1.\"）和段落标题\n"
        "3. 去掉作者名和出处标注\n"
        "4. 只提取汉字，去掉标点符号和空格\n"
        "5. 如果文本不包含文言文或古诗词，text 填 \"MODERN\"\n"
        "\n"
        "输出格式（严格 JSON，不要其他文字）：\n"
        '{"text": "韦凑字彦宗京兆万年人永淳初解褐婺州参军事"}\n'
        "\n"
        f"文本：\n{sample}"
    )

    try:
        chat_url = api_url.rstrip("/")
        if not chat_url.endswith("/chat/completions"):
            chat_url += "/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(chat_url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        msg = body["choices"][0].get("message", {})
        raw = msg.get("content", "") or msg.get("reasoning_content", "") or ""
        raw = raw.strip()
        log(f"   🔧 API 原始返回: {raw[:120]}")
        # 解析 JSON
        data = json.loads(raw)
        result = data.get("text", "")
        if result == "MODERN" or not result:
            return None
        # 清理结果：去标点、取前20字
        clean = re.sub(r'[^一-鿿]', '', result)
        if len(clean) > 20:
            clean = clean[:20]
        if len(clean) < 3:
            return None
        log(f"   🎯 API 提取正文开头：{clean}")
        return clean
    except Exception as e:
        log(f"   ⚠️ API 提取正文开头失败：{e}")
        return None

def detect_text_type(text):
    if not text or not text.strip():
        return "modern"

    clean = re.sub(r'\s+', '', text)
    if len(clean) < 5:
        return "modern"

    lines = [re.sub(r'[^\u4e00-\u9fff]', '', l.strip())
             for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if l]

    particle_density = _particle_density(clean)

    if particle_density >= 0.12 and len(clean) < 50:
        return "classical"

    if _is_poetry(lines, clean, particle_density):
        return "poetry"

    if _is_classical(clean, particle_density):
        return "classical"

    return "modern"

def _particle_density(clean_text):
    if not clean_text:
        return 0
    count = 0
    for p in CLASSICAL_PARTICLES:
        count += clean_text.count(p)
    return count / len(clean_text)

def _is_poetry(lines, clean_text, particle_density=0):
    chinese_lines = [l for l in lines if len(l) >= 3]
    if not chinese_lines:
        return False

    if particle_density >= 0.15:
        return False

    lengths = [len(l) for l in chinese_lines[:10]]
    if not lengths:
        return False

    avg_len = sum(lengths) / len(lengths)
    variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
    std_dev = variance ** 0.5

    five_char = sum(1 for l in lengths if l == 5)
    seven_char = sum(1 for l in lengths if l == 7)
    ten_char = sum(1 for l in lengths if l == 10)
    fourteen_char = sum(1 for l in lengths if l == 14)
    total = len(lengths)

    if total >= 2:
        if five_char >= total * 0.6:
            return True
        if seven_char >= total * 0.6:
            return True
        if ten_char >= total * 0.6:
            return True
        if fourteen_char >= total * 0.6:
            return True
        if std_dev <= 1.5 and 4 <= avg_len <= 15:
            return True

    if total == 1:
        l = lengths[0]
        if l in [20, 28, 40, 56]:
            # 额外检查：虚词密度 < 0.06 且无传记人名模式才可能是诗歌
            # 「字彦宗」「字XX」是典型传记开头，不应判为诗歌
            if particle_density < 0.06 and _has_poetry_markers(clean_text):
                if not re.search(r'[一-鿿]{1,4}字[一-鿿]{1,4}', clean_text):
                    return True
        if l >= 8 and l <= 60:
            if _is_clear_poetry_line(clean_text):
                return True

    return False

def _has_poetry_markers(text):
    markers = ["。", "，", "、", "；", "？", "！"]
    count = sum(text.count(m) for m in markers)
    if len(text) > 0 and count / len(text) > 0.05:
        return True
    return False

def _is_clear_poetry_line(text):
    clean = re.sub(r'[^\u4e00-\u9fff]', '', text)
    if len(clean) < 8:
        return False

    segments = re.split(r'[，。；？！、]', text)
    segments = [re.sub(r'[^\u4e00-\u9fff]', '', s) for s in segments]
    segments = [s for s in segments if s]

    if len(segments) < 4:
        return False

    seg_lens = [len(s) for s in segments]
    avg = sum(seg_lens) / len(seg_lens)
    if avg < 4 or avg > 8:
        return False

    variance = sum((l - avg) ** 2 for l in seg_lens) / len(seg_lens)
    std_dev = variance ** 0.5

    if std_dev <= 1.0:
        # 额外检查：文言文散文（而非诗歌）的虚词密度通常 >= 0.03
        # 避免把短篇文言文传记（如韦凑传开头）误判为诗歌
        if _particle_density(text) < 0.06:
            return True
        # 如果虚词密度达到文言文水平，则不是诗歌
        return False

    return False

def _is_classical(clean_text, particle_density=None):
    if len(clean_text) < 10:
        return False

    if particle_density is None:
        density = _particle_density(clean_text)
    else:
        # 可能来自 detect_text_type 的含标点版本，重新计算以确保准确性
        density = _particle_density(clean_text) if particle_density < 0.04 and len(clean_text) > 50 else particle_density

    if density >= 0.08:
        return True

    classical_markers = ["曰", "云", "言", "谓", "对曰", "问曰", "先生",
                         "寡人", "陛下", "大王", "诸侯", "大夫",
                         "之", "乎", "也", "矣", "焉", "哉"]
    marker_count = 0
    for m in classical_markers:
        if m in clean_text:
            marker_count += 1

    # 长文本要求更高的虚词密度（避免现代文引用古文时被误判）
    if len(clean_text) > 500:
        if density >= 0.07 and marker_count >= 5:
            return True
    elif density >= 0.06 and marker_count >= 4:
        return True

    # 回退：出现「字+X」人名模式 + 官职关键词
    if density >= 0.035:
        has_name = re.search(r'[一-鿿]{1,4}字[一-鿿]{1,4}', clean_text)
        has_title = re.search(r'(刺史|司马|长史|司农|法曹|参军事|太府|通事舍人|太守|县令|尚书|侍郎|御史|大理|鸿胪)', clean_text)
        if has_name and (marker_count >= 1 or has_title):
            # 现代文阅读题(含古人名引用)误入回退分支的否决：真正文言文题不会带这些标记词。
            # 仅作用于低密度回退分支，不影响密度≥0.08 等高密度主分支。
            modern_markers = ("现代文阅读", "论述类", "实用类", "文学类",
                              "非连续性文本", "信息类", "阅读下面的文字")
            if any(m in clean_text for m in modern_markers):
                return False
            return True

    return False

def diff_characters(original, given):
    """n-gram 逐段比对：用多尺度 n-gram 标记匹配位置，聚合未匹配区域为差异。

    不依赖 difflib 全局序列对齐——difflib 在两个序列长度差异大或存在
    结构性增删时会导致后续对齐全部错位。n-gram 逐段比对天然不受长度差异
    和中间增删的干扰。

    Args:
        original: 权威原文（纯汉字）
        given: 待校稿（纯汉字）

    Returns:
        {"identical": bool, "differences": [{"position": int, "original": str, "given": str, "type": str}]}
    """
    if not original and not given:
        return {"identical": True, "differences": []}
    if not original or not given:
        return {
            "identical": False,
            "differences": [{"original": original or "(空)", "given": given or "(空)", "position": 0, "type": "replace"}]
        }

    # 步骤1: 用较长 n-gram (7~12字) 在 original 中标记 given 的匹配区域
    MIN_N, MAX_N, STEP = 7, 12, 2
    matched = [False] * len(given)

    # 生成 given 的 n-gram 索引（去重以加速）
    seen = set()
    for n in range(MAX_N, MIN_N - 1, -1):  # 从长到短，长匹配优先
        for i in range(0, len(given) - n + 1, STEP):
            ng = given[i:i + n]
            if ng in seen:
                continue
            seen.add(ng)
            if ng in original:
                # 在 given 中标记所有此 n-gram 的位置
                pos = 0
                while True:
                    idx = given.find(ng, pos)
                    if idx == -1:
                        break
                    for p in range(idx, idx + n):
                        matched[p] = True
                    pos = idx + 1

    # 步骤2: 用较短 n-gram (3~6字) 覆盖剩余未匹配位置
    MIN_N2, MAX_N2, STEP2 = 3, 6, 2
    for n in range(MAX_N2, MIN_N2 - 1, -1):
        for i in range(0, len(given) - n + 1):
            # 只处理包含未匹配位置的窗口
            if all(matched[i + k] for k in range(n)):
                continue
            ng = given[i:i + n]
            if ng in original:
                for k in range(n):
                    matched[i + k] = True

    # 步骤3: 聚合未匹配位置为差异块
    diffs = []
    i = 0
    while i < len(matched):
        if not matched[i]:
            j = i
            while j < len(matched) and not matched[j]:
                j += 1
            unmatched_text = given[i:j]

            # 推算 original 中对应区域：用未匹配段前后的已匹配锚点
            original_snippet = ""
            diff_type = "replace"

            # 找左侧锚点（未匹配段之前最近的一串已匹配字符）
            left_anchor_start = i - 1
            while left_anchor_start >= 0 and matched[left_anchor_start]:
                left_anchor_start -= 1
            left_anchor_start += 1
            left_anchor_len = i - left_anchor_start

            # 找右侧锚点（未匹配段之后最近的一串已匹配字符）
            right_anchor_end = j
            while right_anchor_end < len(matched) and matched[right_anchor_end]:
                right_anchor_end += 1
            right_anchor_len = right_anchor_end - j

            # 尝试用锚点在 original 中定位对应区间
            if left_anchor_len >= 3:
                left_anchor = given[left_anchor_start:left_anchor_start + left_anchor_len]
                left_pos_in_orig = original.find(left_anchor)
                if left_pos_in_orig != -1:
                    # 从左侧锚点末尾推算差异区间在 original 中的位置
                    orig_start = left_pos_in_orig + left_anchor_len
                    if right_anchor_len >= 3:
                        # 双侧锚点：精确定位区间
                        right_anchor = given[j:j + right_anchor_len]
                        right_pos_in_orig = original.find(right_anchor, orig_start)
                        if right_pos_in_orig != -1:
                            orig_end = right_pos_in_orig
                            original_snippet = original[orig_start:orig_end]
                            if len(original_snippet) == 0:
                                diff_type = "delete_from_original"
                            elif len(unmatched_text) > 3:
                                diff_type = "replace" if len(original_snippet) < 20 else "delete_from_original"
                    else:
                        # 仅左侧锚点：估算 original 对应区域
                        orig_end = min(orig_start + len(unmatched_text) + 10, len(original))
                        original_snippet = original[orig_start:orig_end]
                        if len(unmatched_text) > 3 and len(original_snippet) > 20:
                            diff_type = "delete_from_original"

            if not original_snippet and len(unmatched_text) <= 3:
                # 短差异回退：用旧算法推算
                search_start = max(0, i - 20)
                search_end = min(len(given), j + 20)
                for k in range(search_start, search_end - 6):
                    if k >= 0 and k + 6 <= len(given) and all(matched[k + m] for m in range(6)):
                        ref_pos = original.find(given[k:k + 6])
                        if ref_pos != -1:
                            offset = i - k
                            orig_s = ref_pos + offset
                            orig_e = orig_s + len(unmatched_text)
                            if 0 <= orig_s < len(original) and 0 <= orig_e <= len(original):
                                original_snippet = original[orig_s:orig_e]
                            break
                diff_type = "replace"
            elif not original_snippet:
                diff_type = "delete_from_original"

            diffs.append({
                "position": i,
                "original": original_snippet,
                "given": unmatched_text,
                "type": diff_type,
            })
            i = j
        else:
            i += 1

    return {
        "identical": len(diffs) == 0,
        "differences": diffs,
    }


def _chi_pos_to_raw(raw_text, chi_target, default):
    """将仅汉字文本的索引映射回原始（带标点）文本的索引。"""
    chi_pos = 0
    for i, ch in enumerate(raw_text):
        if chi_pos == chi_target:
            return i
        if '一' <= ch <= '鿿':
            chi_pos += 1
    return default


def _build_chi_index_map(raw_text):
    """构建「第 N 个汉字 → 原始文本位置」的映射表。

    用于将 diff_characters 返回的清洗后坐标系的 position 映射回
    带标点和格式标记的原始文本中的可定位位置。

    会先去除 _clean_for_matching 会移除的标签字符（HTML 标签、格式标记、
    批注块等），保证汉字计数与清洗后的文本一致。
    """
    # 预清理：去除 _clean_for_matching 步骤 2/3/6 会移除的标签字符，
    # 避免标签名中的汉字（如「着」「重」「批」「注」）污染汉字计数
    s = _FORMATTING_MARKER_RE.sub('', raw_text)
    s = _ANNOTATION_RE.sub('', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'\1', s)
    s = re.sub(r'__([^_]+)__', r'\1', s)
    s = re.sub(r'<[^>]+>', '', s)
    # 现在 s 中的汉字序列与 _clean_for_matching 输出的前几步一致
    mapping = []  # mapping[clean_idx] = position_in_precleaned_text
    for i, ch in enumerate(s):
        if '一' <= ch <= '鿿':
            mapping.append(i)
    return mapping


def _map_diff_positions_to_raw(diffs, raw_text):
    """将 diff 结果中的 position 从清洗后坐标系映射到 raw_text 坐标系。

    清洗流程 _clean_for_matching 会做两件事：
    1. 移除 HTML 标签/格式标记/批注块中的汉字（如「着重」「批注」）
    2. 去除非汉字字符 + 简转繁（长度不变）

    本函数先对 raw_text 做与步骤 1 相同的标签剥离，再构建汉字→位置映射表，
    保证 diff position 能正确定位到 raw_text（标签剥离后）中的对应汉字。
    映射失败时保留原始 position 并追加 raw_position=-1 标记。
    """
    chi_map = _build_chi_index_map(raw_text)
    for d in diffs:
        pos = d.get("position", 0)
        if pos < len(chi_map):
            d["raw_position"] = chi_map[pos]
        else:
            d["raw_position"] = -1  # 映射失败标记
    return diffs

def _clean_for_matching(text: str) -> str:
    """将文本清洗为仅保留汉字的归一化形式，用于模糊匹配。

    同时将简体中文转换为繁体中文，以匹配识典古籍/搜韵网的繁体原文。
    """
    if not text:
        return ""
    s = re.sub(r'\[([^\]]*)\]\{[^}]*\}', r'\1', text)
    s = _FORMATTING_MARKER_RE.sub('', s)
    s = _ANNOTATION_RE.sub('', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'\1', s)
    s = re.sub(r'__([^_]+)__', r'\1', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'[^一-鿿]', '', s)
    # 简体→繁体转换，以匹配古籍原文的繁体字符
    try:
        import zhconv
        s = zhconv.convert(s, 'zh-hant')
    except ImportError:
        pass
    return s


# 正文段切分用的行级正则（extract_body_segment）
# 引导语行：阅读下面的(文言文|古诗|…)…完成 —— 只匹配文言文/诗歌引导语，
# 不含"文字/作品/文章"（那是现代文引导语）；现代文题由 detect_text_type 在上游拦截，
# 此处再守一道：若被误用于现代文，返回 None 而非切出现代文正文。
_LEADIN_LINE_RE = re.compile(
    r'阅读下面的(?:文言文|古诗|唐诗|宋词|词|诗歌|元曲|散曲|这首词|这首诗)'
    r'[，。,\.、\s]*完成'
)
# 出处行：（节选自《…》 / (节选自…
_SOURCE_LINE_RE = re.compile(r'^\s*[（(]节选自')
# 题干行：N．/N. + 可选转义反斜杠 + 下列/对下列
_STEM_LINE_RE = re.compile(r'^\s*\d{1,2}[．.](?:\\)?\s*(?:下列|对下列)')


def extract_body_segment(md_text):
    """从片段 md 中切出「文言文/诗歌正文段」。

    返回引导语行之后、出处行/第一题干行之前的原始正文（保留批注与格式标记，未清洗），
    由调用方按需清洗。找不到引导语或终点行 → 返回 None，由调用方回退到整道题逻辑。

    设计要点：
    - 逐行定位，正文起点不依赖首字为汉字（兼容 <波浪线> 起头）。
    - 粗体标题行（如 **二、文言文阅读**）不是引导语行，不计为正文起点。
    - 终点优先出处行 `（节选自…）`，兜底题干行 `N．下列…` / `N. 对下列…`。
    """
    if not md_text or not md_text.strip():
        return None

    lines = md_text.splitlines()

    # 1) 定位引导语行（"阅读下面的…完成…"），容忍批注内嵌与粗体标题前缀
    leadin_idx = None
    for i, line in enumerate(lines):
        if line.strip() and _LEADIN_LINE_RE.search(_clean_annotations(line)):
            leadin_idx = i
            break
    if leadin_idx is None:
        return None

    # 2) 正文起点 = 引导语行后首个非空行
    start_idx = None
    for j in range(leadin_idx + 1, len(lines)):
        if lines[j].strip():
            start_idx = j
            break
    if start_idx is None:
        return None

    # 3) 正文终点 = 自起点向下首个「出处行」或「题干行」，取其前
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if not lines[j].strip():
            continue
        cleaned = _clean_annotations(lines[j])
        if _SOURCE_LINE_RE.match(cleaned) or _STEM_LINE_RE.match(cleaned):
            end_idx = j
            break

    body = "\n".join(lines[start_idx:end_idx]).strip()
    return body or None


def _find_best_excerpt_range(n_full: str, n_sentences: list):
    """n-gram 密度匹配：在每个句子内生成 n-gram，在全文找匹配位置，
    滑动窗口找到命中密度最高的区域。比 difflib 多块聚合更鲁棒——
    n-gram 天然过滤了 excerpt 中的非原文内容（如试题文本在古籍中找不到匹配）。

    n_sentences 应为已清洗（纯汉字）的句子片段列表，n-gram 仅在句内生成，
    避免跨句边界的无意义 n-gram（原书上句末+下句首的组合不存在）。

    返回 (start, end, total_hits) 或 None。
    """
    if not n_full or not n_sentences or len(n_full) == 0:
        return None

    total_chars = sum(len(s) for s in n_sentences)
    if total_chars == 0:
        return None

    # 步骤1: 在每个句子内生成 n-gram 集合（5~12 字，步长 3）
    MIN_N, MAX_N, STEP = 5, 12, 3
    ngrams = set()
    for sent in n_sentences:
        s = sent.strip()
        if len(s) < MIN_N:
            continue
        for n in range(MIN_N, MAX_N + 1):
            if len(s) < n:
                continue
            for i in range(0, len(s) - n + 1, STEP):
                ngrams.add(s[i:i + n])

    if len(ngrams) < 3:
        log(f"   ⚠️ excerpt 中仅 {len(ngrams)} 个 n-gram（{total_chars} 字），不足以匹配")
        return None

    # 步骤2: 在 n_full 中标记每个 n-gram 的所有命中位置
    positions = []
    for ng in ngrams:
        start = 0
        while True:
            idx = n_full.find(ng, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + 1

    if not positions:
        log(f"   ⚠️ 所有 n-gram 在全文无命中")
        return None

    positions.sort()
    total_hits = len(positions)
    unique_hits = len(set(positions))
    log(f"   🔬 n-gram 命中: {total_hits} 次 ({unique_hits} 个唯一位置, {len(ngrams)} 个 n-gram)")

    # 步骤3: 滑动窗口 (500字) 找命中密度峰值
    WINDOW = 500
    step = max(1, WINDOW // 5)
    best_cnt = 0
    best_center = positions[0]
    search_end = min(positions[-1] + 1, len(n_full))

    for center in range(positions[0], search_end, step):
        lo = max(0, center - WINDOW // 2)
        hi = min(len(n_full), center + WINDOW // 2)
        cnt = sum(1 for p in positions if lo <= p <= hi)
        if cnt > best_cnt:
            best_cnt = cnt
            best_center = center

    # 步骤4: 从最佳窗口内取第一个 → 最后一个命中的区间
    lo = max(0, best_center - WINDOW // 2)
    hi = min(len(n_full), best_center + WINDOW // 2)
    in_win = [p for p in positions if lo <= p <= hi]
    start_idx = max(0, min(in_win) - 30)
    end_idx = min(len(n_full), max(in_win) + MAX_N + 30)

    # 步骤5: 覆盖率——窗口内唯一命中 n-gram 数 / 总 n-gram 数
    matched_ngrams = set()
    for ng in ngrams:
        if n_full.find(ng, start_idx, min(end_idx + len(ng), len(n_full))) != -1:
            matched_ngrams.add(ng)
    coverage = len(matched_ngrams) / len(ngrams) if ngrams else 0

    log(f"   🎯 密度匹配: 窗口 {WINDOW} 字内 {best_cnt} 次命中, 区间 [{start_idx}, {end_idx}], 覆盖率 {coverage:.1%}")

    # 阈值:
    # - 短文本 (< 30汉字) 放宽到 3 次命中
    # - 长文本至少 10 次命中
    # - 覆盖率阈值 30%（短文本 20%）——之前 5% 太低，不同古文的共享短 n-gram
    #   也能达到 5%，导致搜到错误的书/章节也能通过质检
    min_hits = 3 if total_chars < 30 else 10
    min_coverage = 0.20 if total_chars < 50 else 0.30
    if best_cnt < min_hits:
        log(f"   ⚠️ 最佳窗口仅 {best_cnt} 次命中 (< {min_hits})，不相关")
        return None
    if coverage < min_coverage:
        log(f"   ⚠️ n-gram 覆盖率仅 {coverage:.1%} < {min_coverage:.0%}，搜索文本与待校稿不相关")
        return None

    return (start_idx, end_idx, best_cnt)

def extract_excerpt_from_full(full_text, excerpt_text):
    """n-gram 密度匹配版：将 excerpt 打碎为 n-gram，在全文找匹配最密集的区间。

    先在原始文本上按标点分句，再对每句清洗并生成句内 n-gram——这样 n-gram
    不会跨越句边界，避免生成原书中不存在的人工组合（如上句末+下句首）。

    优势：excerpt 中非原文内容（现代文试题、选项等）的 n-gram 在古籍全文
    中找不到匹配，自动被过滤；只有真实古籍原文的 n-gram 会命中。
    匹配质量不足时返回 None，调用方跳过前置 diff。
    """
    if not full_text or not excerpt_text:
        return None

    n_full = _clean_for_matching(full_text)

    # 在清洗前按标点分句，避免跨句边界的无意义 n-gram
    raw_sentences = re.split(r'[，。；？！、：\n\s]+', excerpt_text)
    n_sentences = []
    for sent in raw_sentences:
        cleaned = _clean_for_matching(sent)
        if len(cleaned) >= 5:  # MIN_N，过短片段不产生有效 n-gram
            n_sentences.append(cleaned)

    if len(n_full) == 0 or not n_sentences:
        return None

    best = _find_best_excerpt_range(n_full, n_sentences)
    if best is None:
        return None

    start_idx, end_idx, best_cnt = best
    total_chars = sum(len(s) for s in n_sentences)
    coverage = best_cnt / total_chars if total_chars > 0 else 0

    log(f"   🔍 n-gram 节选: 区间 [{start_idx}, {end_idx}] ({end_idx - start_idx} 字), 覆盖率 {coverage:.1%}")

    full_start = _chi_pos_to_raw(full_text, start_idx, 0)
    full_end = _chi_pos_to_raw(full_text, end_idx, len(full_text))

    return full_text[full_start:full_end]

def build_reference_section(text_type, original, diffs):
    type_label = {
        "classical": "文言文",
        "poetry": "诗歌",
    }.get(text_type, "文本")

    lines = [f"## 前置参考：{type_label}原文校验", ""]

    if original:
        lines.append("### 权威原文（来自识典古籍/搜韵网）")
        lines.append("")
        lines.append("> " + original.replace("\n", "\n> "))
        lines.append("")
    else:
        lines.append("### 权威原文")
        lines.append("")
        lines.append("> 未能检索到权威原文，请结合上下文判断。")
        lines.append("")
        return "\n".join(lines)

    if diffs and len(diffs) > 0:
        lines.append("### 字面差异（自动比对）")
        lines.append("")
        for i, d in enumerate(diffs, 1):
            dtype = d.get("type", "replace")
            orig = d.get("original", "")
            giv = d.get("given", "")
            # 优先使用映射后的 raw_position（可在原始文档中定位），
            # 回退到清洗后坐标系的 position
            pos = d.get("raw_position", d.get("position", 0))
            if pos == -1:
                pos = d.get("position", 0)
            if dtype == "replace":
                lines.append(f"{i}. 第{pos}位：「{orig}」→「{giv}」（替换）")
            elif dtype == "delete":
                lines.append(f"{i}. 第{pos}位：「{orig}」（原文有，待校稿缺失）")
            elif dtype == "insert":
                lines.append(f"{i}. 第{pos}位：「{giv}」（待校稿多出）")
            else:
                lines.append(f"{i}. 第{pos}位：{orig} → {giv}")
        lines.append("")
        lines.append("⚠️ 以上差异为程序自动比对结果，请结合语境判断是否为真正的错误。")
    else:
        lines.append("### 比对结果")
        lines.append("")
        lines.append("✅ 待校稿与权威原文字面一致。")
        lines.append("")
        lines.append("> **指令**：该段文言文/诗歌的原文已通过识典古籍/搜韵网自动验证，")
        lines.append("> 与权威原文字面完全一致，无需再对正文内容进行逐字校对。")
        lines.append("> 请仅检查：标点符号、注释编号、格式标记是否与原文匹配。")
    lines.append("")

    return "\n".join(lines)

def search_original_text(text_type, sample_text):
    """搜索权威原文。

    策略：识典古籍(文言文, Playwright) → 搜韵网(诗歌) → ddgs/百度搜索 → 提取原文。
    Playwright 不可用时自动回退到搜索引擎方案。

    Args:
        text_type: 'classical' | 'poetry' | 'modern'
        sample_text: 待搜索的关键词（应尽量是正文而非引导语）
    """
    if text_type == "modern":
        return None
    if not sample_text or not sample_text.strip():
        return None

    sample = sample_text.strip()
    sample = _strip_leadin(sample)
    sample = re.sub(r'[#*`\[\]()\s]', '', sample)
    if len(sample) > 20:
        sample = sample[:20]
    if len(sample) < 4:
        return None

    log(f"   🔍 搜索关键词: {sample}")

    try:
        from proseproof.shared.web_tools import WebFetchTool, WebSearchTool
        import urllib.parse

        fetcher = WebFetchTool()
        searcher = WebSearchTool()

        # 第1优先：识典古籍（文言文，Playwright 可用时）
        if text_type == "classical":
            try:
                from proseproof.shared.shidianguji_playwright import is_playwright_available, search_and_extract
                # 只在 Playwright 可用时才尝试识典
                if is_playwright_available():
                    log(f"   📚 尝试识典古籍搜索...")
                    sdg_result = search_and_extract(sample)
                    if sdg_result and len(sdg_result) > 50:
                        log(f"   ✅ 识典古籍找到原文 ({len(sdg_result)} 字)")
                        return sdg_result
                    log(f"   ⚠️ 识典古籍未找到或结果过短")
            except Exception as e:
                log(f"   ⚠️ 识典古籍搜索异常: {e}")

        # 第2优先：搜韵网（仅诗歌）
        if text_type == "poetry":
            url = f"https://sou-yun.cn/QueryPoem.aspx?q={urllib.parse.quote(sample)}"
            result = fetcher._run(url)
            if result and not result.startswith("[") and "搜索结果为空" not in result:
                log(f"   ✅ 搜韵网找到结果")
                return _extract_first_poem(result)
            log(f"   ⚠️ 搜韵网未找到，尝试百度搜索...")

        # 第3优先：DuckDuckGo/Baidu 搜索 + 抓取
        search_query = f"{sample} 原文"
        log(f"   🌐 搜索: {search_query[:40]}...")
        # 先尝试 ddgs（返回直接 URL），失败回退百度
        search_result = None
        for backend in ["ddgs", "baidu"]:
            try:
                search_result = searcher._run(search_query, backend=backend)
                if search_result and not search_result.startswith("[E"):
                    break
            except Exception:
                continue

        if search_result:
            try:
                items = json.loads(search_result)
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    url = item.get("url", "")
                    if not url:
                        continue
                    # 跳过百度文库（403 反爬）
                    if "wenku.baidu.com" in url:
                        continue

                    log(f"   📄 尝试抓取: {item.get('title', '')[:50]}")
                    page = fetcher._run(url)
                    if page and len(page) > 200 and not page.startswith("["):
                        # 提取页面中的文言文/诗歌部分
                        if text_type == "poetry":
                            extracted = _extract_first_poem(page)
                        else:
                            extracted = _extract_first_classical(page)
                        if extracted and len(extracted) > 30:
                            log(f"   ✅ 搜索→抓取成功 ({len(extracted)} 字)")
                            return extracted
                        else:
                            log(f"   ⚠️ 页面未提取到足够文本（{len(extracted) if extracted else 0} 字）")
            except (json.JSONDecodeError, Exception) as e:
                log(f"   ⚠️ 搜索结果解析失败: {e}")

        log(f"   ⚠️ 搜索未找到可用原文")

    except Exception as e:
        log(f"   ⚠️ 前置搜索异常: {e}")

    return None

def _extract_first_poem(text):
    if not text:
        return None
    lines = text.strip().splitlines()
    poem_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("【") or stripped.startswith("##") or stripped.startswith("#"):
            continue
        if stripped:
            poem_lines.append(stripped)
        if len(poem_lines) >= 20:
            break
    if poem_lines:
        return "\n".join(poem_lines)
    return None

def _extract_first_classical(text):
    """从网页文本中提取文言文正文。跳过导航、页眉等噪音。"""
    if not text:
        return None
    lines = text.strip().splitlines()
    result_lines = []
    started = False

    for line in lines:
        stripped = line.strip()
        # 跳过明显非正文的行
        if not stripped:
            continue
        if stripped.startswith("【") or stripped.startswith("##"):
            continue
        if len(stripped) < 6:
            continue
        # 跳过纯导航/链接行（含大量空格或特殊字符少的中文）
        chinese = re.findall(r'[一-鿿]', stripped)
        if len(chinese) < 4:
            continue

        # 检测"正文开始"信号：需要同时满足文言虚词密度和连续中文长度，
        # 避免将现代文网页（古诗文网知识讲解、百度百科等）误判为文言原文。
        density = _particle_density(''.join(chinese)) if chinese else 0
        if not started:
            # 严苛模式：两个条件必须同时满足，防止单条件误触发
            # - 密度 >= 0.05（古诗文网讲解页的现代文密度通常在 0.01-0.03）
            # - 连续中文 >= 15 字
            if density >= 0.05 and len(chinese) >= 15:
                started = True
            else:
                continue

        result_lines.append(stripped)
        if len(result_lines) >= 40:
            break

    if len(result_lines) >= 3:
        return "\n".join(result_lines)
    return None

def preprocess_for_proofread(md_text, api_url=None, api_key=None, model=None, q_dir=None):
    """前置处理：检测文本类型 → 搜索权威原文 → diff → 注入参考资料。

    Args:
        md_text: 待校对的 Markdown 文本（含格式标记）
        api_url/api_key/model: 可选的 API 配置，用于精准提取搜索关键词

    搜索关键词取前 10 个汉字。识典古籍 Playwright 优先，失败回退 ddgs。
    """
    if not md_text or not md_text.strip():
        return md_text

    text_type = detect_text_type(md_text)

    if text_type == "modern":
        return md_text

    log(f"   📖 检测到文本类型: {'文言文' if text_type == 'classical' else '诗歌'}，启动前置搜索...")

    # 步骤A：切出正文段（引导语后、出处/题干行前），用于节选与 diff。
    # 失败时回退到整道题匹配（零回归，但会引入题干/选项/批注 diff 噪音）。
    from proseproof.shared.docx_format_enhancer import strip_format_markers
    body_segment = extract_body_segment(md_text)
    if body_segment is not None:
        log(f"   ✂️ 已切出正文段 ({len(body_segment)} 字)，用于节选与 diff")
    else:
        log(f"   ⚠️ 未能切出正文段，回退整道题匹配（可能引入 diff 噪音）")

    # 步骤0：确定用于匹配的干净文本
    # 优先用正文段清洗版（不含题干/选项/批注答案）；切分失败时回退 _clean.md / 现场清洗
    match_text = None
    if body_segment is not None:
        m = _clean_annotations(body_segment)
        m = strip_format_markers(m)
        match_text = m
        log(f"   📄 正文段清洗版用于节选匹配 ({len(match_text)} 字)")
    if not match_text and q_dir:
        q_name = os.path.basename(q_dir)
        clean_path = os.path.join(q_dir, f"{q_name}_clean.md")
        if os.path.exists(clean_path):
            try:
                with open(clean_path, 'r', encoding='utf-8') as f:
                    match_text = f.read()
                log(f"   📄 已读取 _clean.md 用于匹配 ({len(match_text)} 字)")
            except Exception:
                match_text = None
    if not match_text:
        # 回退：对原始 md_text 做与 _clean.md 相同的清洗
        clean = _clean_annotations(md_text)
        clean = strip_format_markers(clean)
        clean = re.sub(r'<批注\s+id=\d+>.*?</批注>', '', clean, flags=re.DOTALL)
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)
        clean = re.sub(r'__([^_]+)__', r'\1', clean)
        match_text = clean
        log(f"   📄 使用现场清洗文本用于匹配 ({len(match_text)} 字)")

    # 步骤1：生成搜索关键词（正则去除引导语后取前 10 汉字）
    search_key = None
    clean = _clean_annotations(md_text)
    clean = strip_format_markers(clean)
    clean = re.sub(r'[#*`\[\]()]', '', clean)
    clean = re.sub(r'\s+', '', clean)
    clean = re.sub(r'^第\d+题[：:.,，。、\s]*', '', clean)
    search_key = _strip_leadin(clean)
    if len(search_key) > 20:
        search_key = search_key[:20]
    log(f"   📝 正则提取关键词（前20字）: {search_key}")

    # 步骤2：去权威来源搜索原文
    original = search_original_text(text_type, search_key)

    if original is None:
        log(f"   ⚠️ 未找到权威原文，跳过前置 diff")
        return md_text

    # 步骤3：从全文截取节选范围（使用清理后的 match_text，而非原始 md_text）
    original_excerpt = extract_excerpt_from_full(original, match_text)
    if original_excerpt:
        log(f"   ✂️ 从全文({len(original)}字)中截取节选范围({len(original_excerpt)}字)")
        original = original_excerpt
    else:
        # 节选匹配失败：搜索到的文本与待校稿可能不相关（如搜到了错误的书），
        # 不应继续用全文做 diff，否则会产生大量无意义差异条目干扰 LLM 判断
        log(f"   ⚠️ 节选匹配失败，搜索到的文本与待校稿无法对齐，跳过前置 diff")
        return md_text

    # 步骤4：字面 diff。正文段切分成功时两侧统一用 _clean_for_matching（只留纯汉字，
    # 不比标点——古文原本无句读，标点校对由 LLM 在正文上独立做）；
    # 切分失败时保留原 re.sub 兜底逻辑（零回归）。
    if body_segment is not None:
        clean_given = _clean_for_matching(body_segment)
        clean_orig = _clean_for_matching(original)
    else:
        clean_given = re.sub(r'[#*`\[\]()\s]', '', md_text)
        clean_orig = re.sub(r'[#*`\[\]()\s]', '', original)

    diff_result = diff_characters(clean_orig, clean_given)

    # 将 diff position 从清洗后坐标系映射回原始 body_segment 的可定位位置
    # （清洗去掉了标点/格式标记，导致 position 在原始文档中找不到对应字）
    if body_segment is not None and diff_result.get("differences"):
        diff_result["differences"] = _map_diff_positions_to_raw(
            diff_result["differences"], body_segment
        )

    reference = build_reference_section(text_type, original, diff_result["differences"])

    if diff_result["identical"]:
        log(f"   ✅ 前置校验完成：原文一致，无需 LLM 额外搜索")
    else:
        log(f"   ⚡ 发现 {len(diff_result['differences'])} 处字面差异，已注入 prompt 供 LLM 判断")

    return reference + "\n---\n\n" + md_text
