import re
import os
import zipfile
import tempfile
import shutil

from proseproof.core.logging_utils import log
from proseproof.core.pandoc_utils import convert_with_pandoc, check_pandoc


def normalize_text(s):
    if not s:
        return ""
    result = []
    for ch in s:
        if ch.isspace():
            continue
        if ch == '\u201c' or ch == '\u201d':
            result.append('"')
        elif ch == '\u2018' or ch == '\u2019':
            result.append("'")
        else:
            result.append(ch)
    return "".join(result)


def _build_norm_map(text):
    norm_chars = []
    orig_indices = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        if ch == '\u201c' or ch == '\u201d':
            norm_chars.append('"')
            orig_indices.append(i)
        elif ch == '\u2018' or ch == '\u2019':
            norm_chars.append("'")
            orig_indices.append(i)
        else:
            norm_chars.append(ch)
            orig_indices.append(i)
    return "".join(norm_chars), orig_indices


def _fuzzy_insert(md_text, search, anchor_text, comment_content, comment_num):
    """模糊匹配锚点（归一化空白/引号后），在屏蔽视图 search 里查找，
    插入到 md_text 对应位置，同步把新标记屏蔽进 search。

    search 与 md_text 等长、位置对齐，已插入标记处为 \\x00（_build_norm_map
    原样保留 \\x00，不吞为空白，故屏蔽区不会匹配到真实锚点文字）。
    返回 (new_md, new_search, ok)。
    """
    if not anchor_text or not md_text:
        return md_text, search, False

    norm_md, md_map = _build_norm_map(search)
    norm_anchor, _ = _build_norm_map(anchor_text)

    if not norm_anchor or len(norm_anchor) > len(norm_md):
        return md_text, search, False

    pos = norm_md.find(norm_anchor)
    if pos < 0:
        return md_text, search, False

    end_norm_pos = pos + len(norm_anchor) - 1
    if end_norm_pos >= len(md_map):
        return md_text, search, False

    orig_end_pos = md_map[end_norm_pos] + 1
    marker = f'<批注 id={comment_num}><原>{anchor_text}</原><改>{comment_content}</改></批注>'
    new_md = md_text[:orig_end_pos] + marker + md_text[orig_end_pos:]
    new_search = search[:orig_end_pos] + ('\x00' * len(marker)) + search[orig_end_pos:]
    return new_md, new_search, True


def fuzzy_insert_comment(md_text, anchor_text, comment_content, comment_num):
    """模糊插入批注（向后兼容入口）。不屏蔽已插入标记，行为与历史版本一致。

    供外部/测试直接调用；insert_comments_into_md 内部走 _fuzzy_insert 的屏蔽路径。
    """
    new_md, _, ok = _fuzzy_insert(md_text, md_text, anchor_text, comment_content, comment_num)
    return new_md, ok


def parse_comments_xml(comments_xml_str):
    comments = {}
    pattern = r'<w:comment\s+[^>]*w:id="(\d+)"[^>]*>(.*?)</w:comment>'
    for match in re.finditer(pattern, comments_xml_str, re.DOTALL):
        comment_id = match.group(1)
        comment_body = match.group(2)
        texts = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', comment_body)
        comments[comment_id] = "".join(texts)
    return comments


def extract_comment_anchors(doc_xml_str):
    anchors = []
    start_pattern = re.compile(r'<w:commentRangeStart\s+w:id="(\d+)"\s*/>')
    end_pattern = re.compile(r'<w:commentRangeEnd\s+w:id="(\d+)"\s*/>')
    text_pattern = re.compile(r'<w:t[^>]*>([^<]*)</w:t>')

    starts = [(m.start(), m.group(1)) for m in start_pattern.finditer(doc_xml_str)]
    ends = {m.group(1): m.start() for m in end_pattern.finditer(doc_xml_str)}

    for pos, cid in starts:
        end_pos = ends.get(cid)
        if end_pos is None:
            continue
        segment = doc_xml_str[pos:end_pos]
        texts = text_pattern.findall(segment)
        anchor_text = "".join(texts)
        if anchor_text:
            anchors.append({"id": cid, "text": anchor_text, "pos": pos})

    return anchors


def insert_comments_into_md(md_text, comments_dict, anchors_list):
    """将批注以 XML 风格标记插入 md 文本，避免方括号嵌套问题。

    标记格式：<批注 id=N><原>原文</原><改>建议</改></批注>

    按锚点在文档中的位置排序，编号跟随文档顺序。

    屏蔽式插入：维护与 result 等长对齐的搜索视图 search，每插入一个标记就在
    search 对应位置填等长 \\x00。后续锚点只在 search 里查找，已插入标记区域
    变成 \\x00 串，与任何真实锚点文字都不匹配——避免短锚点（如单数字 "9"）
    匹配到已插入 <批注 id=N> 标记内部的数字，从而错插进旧标记的开标签。
    """
    if not comments_dict or not anchors_list:
        return md_text

    # 过滤有效锚点，按文档位置升序排列
    valid = []
    for a in anchors_list:
        if a["id"] in comments_dict and a.get("text"):
            valid.append(a)
    valid.sort(key=lambda a: a.get("pos", 999999))

    result = md_text
    search = md_text  # 屏蔽视图：已插入标记处为 \x00，与 result 等长对齐
    inserted = 0

    for anchor in valid:
        cid = anchor["id"]
        anchor_text = anchor["text"]
        comment_content = comments_dict[cid]
        inserted += 1
        marker = f'<批注 id={inserted}><原>{anchor_text}</原><改>{comment_content}</改></批注>'

        # 只在屏蔽视图里找锚点，避免匹配已插入标记内部的数字
        pos = search.find(anchor_text)
        if pos >= 0:
            insert_pos = pos + len(anchor_text)
            result = result[:insert_pos] + marker + result[insert_pos:]
            search = search[:insert_pos] + ('\x00' * len(marker)) + search[insert_pos:]
        else:
            new_result, new_search, ok = _fuzzy_insert(
                result, search, anchor_text, comment_content, inserted)
            if ok:
                result, search = new_result, new_search
            else:
                inserted -= 1

    return result


def extract_comments_to_md(docx_path, output_md_path):
    if not os.path.exists(docx_path):
        log(f"❌ 文件不存在: {docx_path}")
        return False

    if not check_pandoc():
        log("❌ Pandoc 未安装，无法转换 Word 文档")
        return False

    output_dir = os.path.dirname(output_md_path) or "."
    base_name = os.path.splitext(os.path.basename(output_md_path))[0]
    img_dir = os.path.join(output_dir, f"{base_name}_images", "media")
    os.makedirs(img_dir, exist_ok=True)

    ok = convert_with_pandoc(docx_path, output_md_path, img_dir, use_mathjax=False)
    if not ok:
        log("❌ Pandoc 转换失败")
        return False

    try:
        with zipfile.ZipFile(docx_path, 'r') as z:
            names = z.namelist()
            if 'word/comments.xml' not in names:
                log("ℹ️ 文档中没有批注，跳过批注提取")
                return True

            comments_xml = z.read('word/comments.xml').decode('utf-8')
            doc_xml = z.read('word/document.xml').decode('utf-8')
    except Exception as e:
        log(f"❌ 读取 docx 失败: {e}")
        return False

    comments_dict = parse_comments_xml(comments_xml)
    anchors = extract_comment_anchors(doc_xml)

    if not comments_dict:
        log("ℹ️ 未解析到批注内容")
        return True

    try:
        with open(output_md_path, 'r', encoding='utf-8') as f:
            md_text = f.read()
    except Exception as e:
        log(f"❌ 读取 md 失败: {e}")
        return False

    new_md = insert_comments_into_md(md_text, comments_dict, anchors)

    try:
        with open(output_md_path, 'w', encoding='utf-8') as f:
            f.write(new_md)
    except Exception as e:
        log(f"❌ 写入 md 失败: {e}")
        return False

    log(f"✅ 批注提取完成，共插入 {len([a for a in anchors if a['id'] in comments_dict])} 条批注")
    return True


# 批注占位符：pandoc 转换前往 docx 注入，转换后留在 md 里精确定位批注位置。
# CMTEND{N}Z —— 纯 ASCII 字母+数字，无标点（避开 md 特殊字符与 post_process 标点转换），
# 尾部 Z 防止 id 数字与后文数字粘连。
_COMMENT_END_TOKEN_RE = re.compile(r'CMTEND(\d+)Z')


def inject_comment_placeholders(docx_path):
    """在 docx 每个 commentRangeEnd 后注入占位符 run，返回 temp docx 路径。

    pandoc 转换后占位符原样留在 md 里，位置即批注真实位置，供
    replace_comment_placeholders 精确替换——彻底摆脱文本搜索在短/重复
    锚点上的错位问题。只在 commentRangeEnd 后注入（End 紧跟锚点文字）。

    失败（无 document.xml / 无批注范围 / 写 temp 失败）返回 None，调用方回退原文件。
    """
    try:
        with zipfile.ZipFile(docx_path, 'r') as z:
            if 'word/document.xml' not in z.namelist():
                return None
            document_xml = z.read('word/document.xml').decode('utf-8')
    except Exception as e:
        log(f"⚠️ 注入占位符：读取 docx 失败: {e}")
        return None

    end_pattern = re.compile(r'<w:commentRangeEnd\s+w:id="(\d+)"\s*/>')

    def _inject(m):
        return m.group(0) + f'<w:r><w:t xml:space="preserve">CMTEND{m.group(1)}Z</w:t></w:r>'

    new_xml = end_pattern.sub(_inject, document_xml)
    if new_xml == document_xml:
        return None  # 无批注范围

    file_dir = os.path.dirname(docx_path) or '.'
    fd, temp_path = tempfile.mkstemp(suffix='.docx', prefix='_cmt_ph_', dir=file_dir)
    os.close(fd)
    try:
        # 复制全部 part，仅替换 document.xml；用原 ZipInfo 保留各 part 压缩类型
        # （避免 [Content_Types].xml 等 stored part 被错误重压导致 Word/pandoc 报错）
        with zipfile.ZipFile(docx_path, 'r') as zin, \
                zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == 'word/document.xml':
                    data = new_xml.encode('utf-8')
                zout.writestr(item, data)
    except Exception as e:
        log(f"⚠️ 注入占位符：写 temp docx 失败: {e}")
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return None
    return temp_path


def replace_comment_placeholders(md_text, comments_dict, anchors):
    """把 md 里的 CMTEND{N}Z 占位符精确替换为批注标记。

    按 md 中占位符出现顺序（左到右 = docx 顺序，pandoc 保序）重新编号 1..N，
    逐个替换为 <批注 id=N><原>anchor</原><改>content</改></批注>。
    anchor 取自 extract_comment_anchors（docx 提取）；id 不在 anchors 或
    comments_dict 的占位符（空锚点/无 Range 点批注）移除、不插标记，与现状一致。
    """
    if not md_text:
        return md_text
    anchor_by_id = {a["id"]: a["text"] for a in anchors if a.get("text")}
    state = {"n": 0}

    def _repl(m):
        cid = m.group(1)
        if cid not in anchor_by_id or cid not in comments_dict:
            return ''  # 无锚点文本或无内容，移除占位符（跳过）
        state["n"] += 1
        return (f'<批注 id={state["n"]}><原>{anchor_by_id[cid]}</原>'
                f'<改>{comments_dict[cid]}</改></批注>')

    return _COMMENT_END_TOKEN_RE.sub(_repl, md_text)


def insert_comments_from_docx(docx_path, md_text):
    """从 docx 文件提取批注并插入到已有的 md 文本中，返回新的 md 文本。

    若 md 含 CMTEND{N}Z 占位符（pandoc 转换前已注入），走精确替换——批注位置
    由 docx 真实位置决定，不受锚点文本重复影响；否则回退到文本搜索插入。
    """
    if not os.path.exists(docx_path):
        log(f"❌ 文件不存在: {docx_path}")
        return md_text

    try:
        with zipfile.ZipFile(docx_path, 'r') as z:
            names = z.namelist()
            if 'word/comments.xml' not in names:
                log("ℹ️ 文档中没有批注")
                return md_text

            comments_xml = z.read('word/comments.xml').decode('utf-8')
            doc_xml = z.read('word/document.xml').decode('utf-8')
    except Exception as e:
        log(f"❌ 读取 docx 失败: {e}")
        return md_text

    comments_dict = parse_comments_xml(comments_xml)
    anchors = extract_comment_anchors(doc_xml)

    if not comments_dict:
        log("ℹ️ 未解析到批注内容")
        return md_text

    if _COMMENT_END_TOKEN_RE.search(md_text):
        # md 含占位符（pandoc 转换前已注入 temp docx）→ 精确替换，位置来自 docx
        new_md = replace_comment_placeholders(md_text, comments_dict, anchors)
        log(f"✅ 已按占位符精确插入批注（位置来自 docx）")
    else:
        # 回退：文本搜索 + 屏蔽式插入（无占位符，如注入失败或 md 来自他处）
        new_md = insert_comments_into_md(md_text, comments_dict, anchors)
        log(f"✅ 已插入 {len([a for a in anchors if a['id'] in comments_dict])} 条批注")
    return new_md
