"""
Word 文档格式增强模块
提取 Pandoc 转换时会丢失的特殊格式（着重号、下划线、删除线等），
在 Markdown 中用自定义标记保留，供校对流程使用。
"""
import os
import re
from proseproof.core.logging_utils import log


def extract_special_formats(docx_path):
    """提取 Word 文档中的特殊格式，返回格式位置列表。

    格式类型：
    - emphasis_dot: 着重号（文字下方的点）
    - underline: 下划线
    - underline_wavy: 波浪线
    - strike: 删除线
    - double_strike: 双删除线
    - subscript: 下标
    - superscript: 上标

    Args:
        docx_path: Word 文档路径

    Returns:
        list: 格式信息列表，每项为 {
            'text': 文本内容,
            'type': 格式类型,
            'paragraph_index': 段落索引,
            'start_pos': 在段落中的起始位置
        }
    """
    Document = None
    import importlib.util
    try:
        from docx import Document
    except (ImportError, AttributeError):
        try:
            import docx as _dx
            Document = _dx.Document
        except (ImportError, AttributeError):
            pass
    if Document is None:
        # 诊断：找出实际加载的 docx 模块位置
        try:
            spec = importlib.util.find_spec("docx")
            loc = spec.origin if spec and spec.origin else "not found"
        except Exception:
            loc = "unknown"
        log(f"\u26a0\ufe0f python-docx \u672a\u5b89\u88c5\u6216\u7248\u672c\u4e0d\u517c\u5bb9 (docx path: {loc})\uff0c\u65e0\u6cd5\u63d0\u53d6\u7279\u6b8a\u683c\u5f0f")
        return []

    formats = []
    try:
        doc = Document(docx_path)
    except Exception as e:
        log(f"⚠️ 读取 Word 文档失败: {e}")
        return []

    for para_idx, para in enumerate(doc.paragraphs):
        pos = 0
        for run in para.runs:
            text = run.text
            if not text:
                pos += len(text)
                continue

            fmt_type = None

            # 着重号（文字下方的点）- 通过 XML 访问
            rPr = run._element.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rPr')
            if rPr is not None:
                emph = rPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}emph')
                if emph is not None:
                    val = emph.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val')
                    if val and val.lower() != 'none':
                        fmt_type = "emphasis_dot"

            # 下划线 - 通过 XML 准确判断类型
            if rPr is not None:
                u = rPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}u')
                if u is not None:
                    val = u.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val')
                    if val and val.lower() != 'none':
                        if 'wave' in val.lower():
                            fmt_type = "underline_wavy"
                        else:
                            fmt_type = "underline"

            # 删除线
            try:
                if run.font.strike:
                    fmt_type = "strike"
            except Exception:
                pass

            # 双删除线
            try:
                if run.font.double_strike:
                    fmt_type = "double_strike"
            except Exception:
                pass

            # 下标
            try:
                if run.font.subscript:
                    fmt_type = "subscript"
            except Exception:
                pass

            # 上标
            try:
                if run.font.superscript:
                    fmt_type = "superscript"
            except Exception:
                pass

            if fmt_type:
                formats.append({
                    "text": text,
                    "type": fmt_type,
                    "paragraph_index": para_idx,
                    "start_pos": pos,
                })

            pos += len(text)

    return formats


_FMT_MARKERS = {
    "emphasis_dot": ("<着重>", "</着重>"),
    "underline": ("<下划线>", "</下划线>"),
    "underline_wavy": ("<波浪线>", "</波浪线>"),
    "strike": ("<删除线>", "</删除线>"),
    "double_strike": ("<双删除线>", "</双删除线>"),
    "subscript": ("<下标>", "</下标>"),
    "superscript": ("<上标>", "</上标>"),
}


def inject_format_markers(md_text, docx_path):
    """将特殊格式标记注入到 Markdown 文本中。

    通过段落文本匹配，找到对应位置插入自定义标记。

    Args:
        md_text: Pandoc 转换后的 Markdown 文本
        docx_path: 原始 Word 文档路径

    Returns:
        str: 注入格式标记后的 Markdown 文本
    """
    formats = extract_special_formats(docx_path)
    if not formats:
        return md_text

    # 按格式类型分组
    by_type = {}
    for fmt in formats:
        t = fmt["type"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(fmt)

    result = md_text

    for fmt_type, fmt_list in by_type.items():
        if fmt_type not in _FMT_MARKERS:
            continue
        open_marker, close_marker = _FMT_MARKERS[fmt_type]

        # 对每个格式项，尝试在 Markdown 中找到对应文本并包裹
        for fmt in fmt_list:
            text = fmt["text"].strip()
            if not text or len(text) < 1:
                continue

            # 跳过太短或太常见的文本，避免误匹配
            if len(text) < 2 and not re.search(r'[\u4e00-\u9fff]', text):
                continue

            # 在结果中查找第一个未被标记的匹配项
            pattern = re.escape(text)
            matches = list(re.finditer(pattern, result))

            for m in matches:
                start = m.start()
                end = m.end()

                before = result[:start]
                after = result[end:]

                # 检查是否已经在标记内部
                # 简单检查：前面最近的标记是打开还是关闭
                # 更准确的做法是栈匹配，这里用简化版
                # 检查是否被 【xxx】 和 【/xxx】 包裹
                last_open = before.rfind(open_marker)
                last_close = before.rfind(close_marker)
                if last_open > last_close:
                    # 已在标记内，跳过
                    continue

                # 检查是否在其他格式标记内
                in_other = False
                for other_type, (other_open, other_close) in _FMT_MARKERS.items():
                    if other_type == fmt_type:
                        continue
                    o = before.rfind(other_open)
                    c = before.rfind(other_close)
                    if o > c:
                        in_other = True
                        break
                if in_other:
                    # 在其他标记内也跳过（避免嵌套）
                    continue

                # 包裹文本
                result = before + open_marker + text + close_marker + after
                break

    log(f"📝 已注入 {len(formats)} 个格式标记")
    return result


def strip_format_markers(text):
    """移除所有格式标记，返回纯文本。"""
    for open_marker, close_marker in _FMT_MARKERS.values():
        text = text.replace(open_marker, "").replace(close_marker, "")
    return text


def get_format_marker_list():
    """返回所有格式标记列表，用于校对时的识别。"""
    return _FMT_MARKERS.copy()
