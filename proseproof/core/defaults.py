import os, re, base64, shutil
from pathlib import Path
from proseproof.core.parsing import save_proofread_json, _is_no_issue
from proseproof.shared.report_utils import format_tool_calls_summary, format_usage_summary
from proseproof.core.api_client import call_api, MAX_FILE_SIZE
from proseproof.core.logging_utils import log
from proseproof.core.format_enforcement import _enforce_format, enforce_and_fix
from proseproof.core import config_loader
from proseproof.shared.image_utils import copy_md_images


def _strip_search_from_prompt(prompt: str) -> str:
    """移除系统提示词中的联网搜索指令。在前置搜索成功注入原文后调用，
    避免 LLM 拿到前置参考后仍然反复搜索。

    本阶段 LLM 不配备搜索工具，仅剥离「## 可用的联网搜索工具」说明段，
    不再追加任何关于搜索的约束说明（避免引用 LLM 没有的工具造成困惑）。
    """
    # 移除 "## 可用的联网搜索工具" 整段（到下一个 ## 标题前）
    cleaned = re.sub(
        r'\n*## 可用的联网搜索工具\n.*?(?=\n## )',
        '',
        prompt,
        flags=re.DOTALL,
    )
    # 清理多余空行
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.rstrip()


def fix_latex_escapes(md_file):
    """修复 pandoc 的过度转义。

    分三阶段：
    1. 全局反斜杠规约（pandoc 的 \\\\ → \\，必须全局生效）
    2. 保护 $...$ / $$...$$ 数学块，避免内部 LaTeX 命令被破坏
    3. 字面替换仅作用于非数学文本；数学内部仅做安全的还原（下标、上标、分组）
    """
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # ===== Phase 1: 全局反斜杠规约（lines 33-35，安全，数学内外均需） =====
    special_chars = r'[\[\]\(\)\$_<>{}$]'
    content = re.sub(r'\\{2,}(?=' + special_chars + r')', r'\\', content)
    content = re.sub(r'\\{2,}([a-zA-Z]+)', r'\\\1', content)
    content = re.sub(r'\\{2,}([^a-zA-Z0-9])', r'\\\1', content)

    # ===== Phase 2a: 还原数学定界符 \$ → $（必须在保护数学块之前） =====
    # pandoc 把 $...$ 输出为 \$...\$，先还原定界符才能正确识别数学块
    content = content.replace(r'\$', r'$')

    # ===== Phase 2b: 保护数学块（先 $...$ 再 $$...$$，与 comprehensive_clean 一致） =====
    math_blocks = []

    def _save_math(m):
        math_blocks.append(m.group(0))
        return f'\x01MATH{len(math_blocks) - 1}\x01'

    # 先保护 $...$（单行），再保护 $$...$$（多行）
    content = re.sub(r'\$[^$\n]+?\$', _save_math, content)
    content = re.sub(r'\$\$.*?\$\$', _save_math, content, flags=re.DOTALL)

    # ===== Phase 3: 字面替换（仅影响非数学文本） =====
    content = content.replace(r'\_', '_')
    content = content.replace(r'\<', '<')
    content = content.replace(r'\>', '>')
    content = content.replace(r'\{', '{')
    content = content.replace(r'\}', '}')
    content = content.replace(r'\left\(', r'\left(')
    content = content.replace(r'\right\)', r'\right)')
    content = content.replace(r'\left\[', r'\left[')
    content = content.replace(r'\right\]', r'\right]')

    def _fix_escaped_brackets(content):
        def _repl(m):
            inner = m.group(1)
            if re.search(r'[\$\\\^_]', inner):
                return m.group(0)
            return '[' + inner + ']'
        return re.sub(r'\\\[([^\]]*?)\\\]', _repl, content)
    content = _fix_escaped_brackets(content)

    for esc, orig in [(r'\^', '^'), (r'\#', '#'), (r'\~', '~'), (r'\&', '&'),
                       (r'\%', '%'), (r'\*', '*'), (r'\+', '+'), (r'\-', '-'),
                       (r'\=', '='), (r'\|', '|'), (r'\!', '!'), (r"\'", "'")]:
        content = content.replace(esc, orig)

    # ===== Phase 4: 数学块内部的安全还原 =====
    # 只还原数学模式必需的命令（下标、上标、分组），其余 LaTeX 命令保持不动
    for i, block in enumerate(math_blocks):
        block = block.replace(r'\_', '_')   # 下标 a_1
        block = block.replace(r'\^', '^')   # 上标 x^2
        block = block.replace(r'\{', '{')   # 分组 {…}
        block = block.replace(r'\}', '}')   # 分组 {…}
        math_blocks[i] = block

    # ===== Phase 5: 还原数学块 =====
    for i, block in enumerate(math_blocks):
        content = content.replace(f'\x01MATH{i}\x01', block)

    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(content)


def comprehensive_clean(md_content):
    # Step 0: 保护数学公式中的 | 字符（绝对值、集合、mid 等），避免被表格清理误删
    math_blocks = []
    def _save_math(m):
        math_blocks.append(m.group(0))
        return f'\x00MATH{len(math_blocks)-1}\x00'
    # 先保护 $...$（单行），再保护 $$...$$（多行）
    # 顺序很重要：$ 更细粒度，先匹配可以避免 $$ 误吞相邻的 $...$（如 $$=\!$ 碎片化公式）
    content = re.sub(r'\$[^$\n]+?\$', _save_math, md_content)
    content = re.sub(r'\$\$.*?\$\$', _save_math, content, flags=re.DOTALL)

    lines = content.splitlines()
    cleaned = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if re.match(r'^[\|\+\-=\:\.\s\t]*$', stripped) and len(stripped) > 2:
            i += 1; continue
        line = re.sub(r'\|', '', line)
        if '答案:' in line:
            line = re.sub(r'[-=]+', '', line)
            if i + 1 < len(lines):
                nxt = lines[i+1].strip()
                if re.match(r'^[A-Z\s]+$', nxt):
                    line = line.rstrip() + ' ' + nxt
                    i += 1
        cleaned.append(line)
        i += 1
    text = '\n'.join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = '\n'.join(l.strip() for l in text.split('\n'))

    # Step N: 恢复数学公式
    for j, block in enumerate(math_blocks):
        text = text.replace(f'\x00MATH{j}\x00', block)
    return text.strip()


def fix_floating_images(md_file):
    with open(md_file, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")
    fixed = False

    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^A\.\s*!\[test\]\(([^)]+)\)\s*(\{[^}]*\})?\s*(.*)", line)
        if not m:
            i += 1
            continue

        img_path = m.group(1)
        img_attrs = m.group(2) or ""
        option_text = m.group(3)

        has_img_in_options = False
        for j in range(i, min(i + 10, len(lines))):
            if re.match(r"^[B-D]\.\s*!\[", lines[j]):
                has_img_in_options = True
                break

        if has_img_in_options:
            i += 1
            continue

        img_line = f"![]({img_path}){img_attrs}"
        if option_text:
            lines[i] = f"A.                                  {option_text}"
        else:
            lines[i] = f"A.                                  "
        lines.insert(i, img_line)
        i += 2
        fixed = True

    if fixed:
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return True
    return False


def normalize_option_spacing(md_file):
    import re as _re
    with open(md_file, "r", encoding="utf-8") as f:
        content = f.read()

    new_content = _re.sub(r" {4,}", "  ", content)
    if new_content != content:
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    return False


def clean_md_file(md_file):
    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()
        cleaned = comprehensive_clean(content)
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(cleaned)
        return True
    except Exception as e:
        log(f"   清洗失败: {e}")
        return False


def default_split_lecture(md_file, output_root, base_name, do_clean, config):
    with open(md_file, 'r', encoding='utf-8') as f:
        md_content = f.read()

    split_mode = "title"
    section_pat = None
    if config:
        split_mode = config_loader.get_lecture_split_mode(config)
        if split_mode == "section":
            section_pat = config_loader.get_section_pattern(config)

    lines = md_content.splitlines()
    questions = []

    if split_mode == "section" and section_pat:
        current_title = "引言"
        current_content = []
        for line in lines:
            stripped = line.strip()
            if section_pat.match(stripped):
                if current_content:
                    questions.append((current_title, '\n'.join(current_content)))
                current_title = stripped
                current_content = [line]
            else:
                current_content.append(line)
        if current_content:
            questions.append((current_title, '\n'.join(current_content)))
    else:
        title_compiled = config_loader.get_compiled_title_patterns(config)
        current_title = None
        current_content = []
        in_question = False
        for line in lines:
            stripped = line.strip()
            is_title = any(p.match(stripped) for p in title_compiled)
            is_section = stripped.startswith('#') and not stripped.startswith('**')
            if is_title:
                if current_title is not None:
                    questions.append((current_title, '\n'.join(current_content)))
                current_title = stripped
                current_content = [line]
                in_question = True
            elif is_section and in_question:
                questions.append((current_title, '\n'.join(current_content)))
                current_title = None; current_content = []; in_question = False
            else:
                if in_question:
                    current_content.append(line)
        if current_title is not None:
            questions.append((current_title, '\n'.join(current_content)))

    if not questions:
        log("   ⚠️ 未识别到任何片段，跳过分割")
        return False

    md_dir = Path(md_file).parent
    src_media = md_dir / f"{base_name}_images" / "media"
    log(f"   🔍 图片源目录: {src_media}")
    if not src_media.exists():
        log(f"   ❌ 图片源目录不存在")

    target_root = Path(output_root) / base_name
    target_root.mkdir(parents=True, exist_ok=True)

    unit_prefix = "板块" if split_mode == "section" else "第"
    unit_suffix = "" if split_mode == "section" else "题"

    total_copied = [0]; total_missing = [0]
    for idx, (title, content) in enumerate(questions, start=1):
        q_dir_name = f"{unit_prefix}{idx}{unit_suffix}"
        q_dir = target_root / q_dir_name
        q_dir.mkdir(exist_ok=True)
        img_dir = q_dir / "images"; img_dir.mkdir(exist_ok=True)
        img_result = copy_md_images(content, [src_media], img_dir)
        total_copied[0] += img_result.copied
        total_missing[0] += img_result.missing
        (q_dir / f"{q_dir_name}.md").write_text(img_result.content, encoding='utf-8')

    log(f"   📂 拆分完成: {len(questions)} 题, 图片 {total_copied[0]} 张")
    return True


def default_generate_knowledge(cleaned_md, output_root, base_name, config):
    with open(cleaned_md, 'r', encoding='utf-8') as f:
        content = f.read()
    lines = content.splitlines()
    compiled = config_loader.get_compiled_title_patterns(config)
    filtered = []
    in_question = False
    for line in lines:
        stripped = line.strip()
        is_title = any(p.match(stripped) for p in compiled)
        is_section = stripped.startswith('#') and not stripped.startswith('**')
        if is_title:
            in_question = True; continue
        elif is_section:
            in_question = False; filtered.append(line)
        else:
            if not in_question:
                filtered.append(line)

    knowledge_text = '\n'.join(filtered)
    md_dir = Path(cleaned_md).parent
    src_media = md_dir / f"{base_name}_images" / "media"
    target_root = Path(output_root) / base_name / "知识"
    target_root.mkdir(parents=True, exist_ok=True)
    img_dest = target_root / "images"; img_dest.mkdir(exist_ok=True)

    img_result = copy_md_images(knowledge_text, [src_media], img_dest)
    (target_root / f"{base_name}_知识.md").write_text(img_result.content, encoding='utf-8')
    log(f"   📘 知识文件已生成")


def fix_pandoc_comment_anomaly(content):
    return content.replace('`<!-- -->`{=html}', '')


def fix_tilde_in_math(content):
    def repl(m):
        return m.group(0).replace(r'\~', r'\sim')
    content = re.sub(r'\$\$.*?\$\$', repl, content, flags=re.DOTALL)
    content = re.sub(r'\$[^$]+\$', repl, content)
    return content


def fix_tilde_in_text(content):
    return content.replace(r'\~', '~')


def convert_italics_to_math(content):
    math_blocks = []
    def save(m):
        math_blocks.append(m.group(0))
        return f'<<<MATHBLOCK{len(math_blocks)-1}>>>'
    content = re.sub(r'\$\$.*?\$\$', save, content, flags=re.DOTALL)
    content = re.sub(r'\$[^$]*\$', save, content)
    def italic_repl(m):
        inner = m.group(1)
        inner = re.sub(r'~(.+?)~', r'_{\1}', inner)
        return f'${inner}$'
    content = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', italic_repl, content)
    for i, block in enumerate(math_blocks):
        content = content.replace(f'<<<MATHBLOCK{i}>>>', block)
    return content


def convert_display_to_inline(content):
    def repl(m):
        formula = m.group(1)
        if '\n' in formula: return m.group(0)
        return f'${formula}$'
    return re.sub(r'\$\$(.+?)\$\$', repl, content, flags=re.DOTALL)


def post_process_md_zw(md_path):
    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        log(f"   ❌ 后处理读取失败: {e}")
        return
    original = content
    content = fix_pandoc_comment_anomaly(content)
    content = fix_tilde_in_math(content)
    content = fix_tilde_in_text(content)
    content = convert_italics_to_math(content)
    content = convert_display_to_inline(content)
    if content != original:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(content)
        log("   ✅ 后处理完成")


def find_answer_section(lines):
    ref_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('**') and '参考答案' in stripped:
            ref_idx = i; break
        if '参考答案' in stripped and ('《' in stripped or not stripped.startswith('**')):
            ref_idx = i; break
    if ref_idx is None:
        return None, []
    return ref_idx, lines[ref_idx:]


def detect_answer_mode(lines):
    qs = re.compile(r'^(\d+)．')
    _, ans_lines = find_answer_section(lines)
    search = lines[:lines.index(ans_lines[0])] if ans_lines else lines
    blocks = []
    i = 0
    while i < len(search):
        line = search[i].strip()
        if qs.match(line) and not line.startswith('**'):
            start = i; j = i + 1
            while j < len(search):
                nxt = search[j].strip()
                if qs.match(nxt) and not nxt.startswith('**'): break
                j += 1
            blocks.append(search[start:j]); i = j
        else:
            i += 1
    if not blocks: return "end"
    inline_count = sum(1 for blk in blocks if any('【答案】' in l for l in blk))
    return "inline" if inline_count > len(blocks) / 2 else "end"


def parse_end_answers(answer_lines):
    if not answer_lines: return {}
    qa = re.compile(r'^(\d+)[.．]\s*(.*)')
    start = 0
    while start < len(answer_lines) and not qa.match(answer_lines[start].strip()):
        start += 1
    if start >= len(answer_lines): return {}
    result = {}
    i = start
    while i < len(answer_lines):
        m = qa.match(answer_lines[i].strip())
        if not m: i += 1; continue
        qnum = int(m.group(1))
        ans = m.group(2).strip()
        i += 1
        exp_lines = []
        while i < len(answer_lines):
            if qa.match(answer_lines[i].strip()): break
            exp_lines.append(answer_lines[i]); i += 1
        if not any('【答案】' in l for l in exp_lines):
            exp_lines.insert(0, f'【答案】{ans}')
        result[qnum] = {'answer': ans, 'explanation': exp_lines}
    return result


def default_split_document(md_file, output_root, base_name, config):
    with open(md_file, 'r', encoding='utf-8') as f:
        md_content = f.read()
    lines = md_content.splitlines()
    qs = config_loader.get_exam_question_pattern(config)
    answer_mode = detect_answer_mode(lines)
    log(f"   📋 答案模式: {'随题' if answer_mode == 'inline' else '末尾'}")
    ans_start, ans_lines = find_answer_section(lines)
    main_lines = lines[:ans_start] if ans_start is not None else lines

    blocks = []
    i = 0
    while i < len(main_lines):
        line = main_lines[i].strip()
        if qs.match(line) and not line.startswith('**'):
            start = i; j = i + 1
            while j < len(main_lines):
                nxt = main_lines[j].strip()
                if qs.match(nxt) and not nxt.startswith('**'): break
                j += 1
            blocks.append(main_lines[start:j]); i = j
        else:
            i += 1

    if not blocks:
        log("   ⚠️ 未识别到任何片段"); return False

    end_answers = parse_end_answers(ans_lines) if answer_mode == "end" else None
    md_dir = Path(md_file).parent
    src_media = md_dir / f"{base_name}_images" / "media"
    log(f"   🔍 图片源目录: {src_media}")
    target_root = Path(output_root) / base_name
    target_root.mkdir(parents=True, exist_ok=True)

    total_copied = [0]; total_missing = [0]

    def is_title(l):
        return bool(re.match(r'^\*\*.*\*\*$', l.strip()))

    for idx, block in enumerate(blocks, start=1):
        if answer_mode == "inline":
            start_ans = start_exp = None
            for k, ln in enumerate(block):
                if ln.strip() == '【答案】': start_ans = k
                if ln.strip() == '【详解】': start_exp = k
            if start_ans is not None:
                stem = block[:start_ans]
                ans = block[start_ans:start_exp] if start_exp is not None else block[start_ans:]
                exp = block[start_exp:] if start_exp is not None else []
            else:
                stem = block; ans = []; exp = []
            stem = [l for l in stem if not is_title(l)]
            final_lines = stem + ans + exp
        else:
            stem = block
            stem = [l for l in stem if not is_title(l)]
            if end_answers and idx in end_answers:
                final_lines = stem + end_answers[idx]['explanation']
            else:
                final_lines = stem

        content_str = '\n'.join(final_lines)
        frag_name = f"frag_{idx:03d}"
        q_dir = target_root / frag_name; q_dir.mkdir(exist_ok=True)
        img_dir = q_dir / "images"; img_dir.mkdir(exist_ok=True)

        img_result = copy_md_images(content_str, [src_media], img_dir)
        total_copied[0] += img_result.copied
        total_missing[0] += img_result.missing
        (q_dir / f"{frag_name}.md").write_text(img_result.content, encoding='utf-8')

    log(f"   📂 拆分完成: {len(blocks)} 个片段, 图片 {total_copied[0]} 张")
    return True


def default_proofread_one(api_url, api_key, model, q_dir, q_name, is_segment, prompt, tools, max_loops, generate_pdf, pre_hook=None, react_mode=False):
    target_md = os.path.join(q_dir, f"{q_name}.md")
    md_content = ""
    if os.path.exists(target_md):
        with open(target_md, 'r', encoding='utf-8') as fm:
            md_content = fm.read()
    if not md_content:
        return {"success": False, "result": "", "error": "未找到 md 文件"}

    # 前置处理 hook：前置搜索 + diff
    if pre_hook:
        try:
            md_content = pre_hook(md_content)
        except Exception as e:
            log(f"   ⚠️ 前置处理异常：{e}")

    # 前置搜索成功后，砍掉 prompt 里的联网搜索指令，避免 LLM 重复搜索
    if pre_hook and "## 前置参考" in md_content:
        prompt = _strip_search_from_prompt(prompt)
        if react_mode:
            # ReAct 模式：移除联网工具，仅依靠前置搜索结果
            tools = [t for t in tools if t.name not in ("web_fetch", "web_search")]
            log("   📖 前置参考已注入（已移除联网工具，仅依靠前置搜索结果）")
        else:
            tools = []
            max_loops = 0
            log("   🔒 前置参考已注入，关闭联网搜索")

    images_b64 = []
    img_dir = os.path.join(q_dir, "images")
    if os.path.exists(img_dir):
        for img_file in os.listdir(img_dir):
            if not img_file.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
                continue
            img_path = os.path.join(img_dir, img_file)
            if os.path.getsize(img_path) > MAX_FILE_SIZE:
                continue
            try:
                with open(img_path, "rb") as fi:
                    b64 = base64.b64encode(fi.read()).decode()
                ext = img_file.lower().split('.')[-1]
                mime = ("image/png" if ext == "png"
                        else "image/jpeg" if ext in ("jpg", "jpeg")
                        else "image/gif")
                images_b64.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"}
                })
            except Exception:
                continue

    try:
        # ReAct 模式：注入 API 配置供 IndependentSolveTool 等内部工具使用
        if react_mode:
            try:
                from proseproof.shared.physics_tools import set_physics_api_config
                set_physics_api_config(api_url, api_key, model, output_dir=q_dir)
            except ImportError:
                pass  # 无 physics_tools 模块，忽略
            try:
                from proseproof.shared.chemistry_tools import set_chemistry_api_config
                set_chemistry_api_config(api_url, api_key, model, output_dir=q_dir)
            except ImportError:
                pass  # 无 chemistry_tools 模块，忽略

        result = call_api(api_url, api_key, model, md_content, images_b64,
                          q_name, prompt, tools=tools, max_loops=max_loops,
                          output_dir=q_dir)
        res = result["content"]
        tool_calls = result["tool_calls_log"]
        reasoning = result.get("reasoning", "")
        usage = result.get("usage", {})
        # 记录 LLM 最终返回内容摘要
        log(f"   📥 LLM 最终返回: {res[:150].replace(chr(10), ' ')}...")
        if reasoning:
            log(f"   💭 模型思考: {reasoning[:150].replace(chr(10), ' ')}...")
    except Exception as e:
        return {"success": False, "result": "", "error": str(e), "tool_calls": []}

    if "API调用失败" not in res:
        # ---- 格式审查 + bash 直接编辑文件修正 ----
        format_ok, format_issues = _enforce_format(res)
        if not format_ok and generate_pdf:
            # 先把原始输出写入文件（不含头部元信息），供 LLM 用 bash 直接编辑
            md_path = os.path.join(q_dir, "_校对报告.md")
            try:
                os.makedirs(q_dir, exist_ok=True)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(res)
            except Exception:
                pass
            log(f"   \u26a0\ufe0f 格式不合规：{format_issues}")
            res, was_fixed, _ = enforce_and_fix(md_path, res, api_url, api_key, model)
        elif not format_ok:
            log(f"   \u26a0\ufe0f 格式不合规：{format_issues}（无文件路径，跳过修正）")

        if generate_pdf:
            md_path = os.path.join(q_dir, "_校对报告.md")
            try:
                with open(md_path, "w", encoding="utf-8") as f:
                    # 加注 API 对话记录路径，方便排查
                    f.write(f"> 完整 API 对话记录请见 `_API对话记录.md`\n\n---\n\n")
                    f.write(res)
                    # 追加工具调用摘要，方便排查搜索质量
                    if tool_calls:
                        f.write(format_tool_calls_summary(tool_calls))
                    # "无问题" 时追加模型思考内容，方便后期核查
                    if _is_no_issue(res) and reasoning:
                        f.write("\n\n---\n")
                        f.write("## 📋 模型思考过程（仅核查用，不出现在 PDF 中）\n\n")
                        f.write(reasoning)
                    # 追加 token 用量统计
                    usage_text = format_usage_summary(usage)
                    if usage_text:
                        f.write(usage_text)
            except Exception:
                pass
            save_proofread_json(res, q_dir, tool_calls)

            # 同步存档到 output/中间产物/{文档名}/{片段名}/
            try:
                q_dir_path = Path(q_dir)
                doc_name = q_dir_path.parent.name   # 文档名（如 示例文档）
                q_name_clean = q_dir_path.name       # 片段名（如 第1题）
                artifact_dir = Path("output") / "中间产物" / doc_name / q_name_clean
                artifact_dir.mkdir(parents=True, exist_ok=True)
                artifact_path = artifact_dir / "_校对报告.md"
                with open(artifact_path, "w", encoding="utf-8") as f:
                    f.write(f"> 完整 API 对话记录请见 `_API对话记录.md`\n\n---\n\n")
                    f.write(res)
                    if tool_calls:
                        f.write(format_tool_calls_summary(tool_calls))
                    if _is_no_issue(res) and reasoning:
                        f.write("\n\n---\n")
                        f.write("## 📋 模型思考过程（仅核查用，不出现在 PDF 中）\n\n")
                        f.write(reasoning)
                    usage_text = format_usage_summary(usage)
                    if usage_text:
                        f.write(usage_text)
                # 同步存档结构化数据
                import shutil, json as _json
                src_json = os.path.join(q_dir, "_校对数据.json")
                if os.path.exists(src_json):
                    shutil.copy2(src_json, artifact_dir / "_校对数据.json")
                # 同步存档 API 对话记录
                src_api_log = os.path.join(q_dir, "_API对话记录.md")
                if os.path.exists(src_api_log):
                    shutil.copy2(src_api_log, artifact_dir / "_API对话记录.md")
            except Exception:
                pass
        return {"success": True, "result": res, "tool_calls": tool_calls, "error": None}
    else:
        err_detail = res.replace("**API调用失败：**\n", "").strip()[:200]
        return {"success": False, "result": "", "error": err_detail, "tool_calls": []}


def get_supported_file_types():
    """返回支持的文件类型列表，用于文件选择对话框。"""
    return [
        ("支持的文件", "*.docx;*.doc;*.idml;*.zip"),
        ("Word 文档", "*.docx;*.doc"),
        ("InDesign IDML", "*.idml"),
        ("ZIP 压缩包", "*.zip"),
        ("所有文件", "*.*"),
    ]


def get_supported_extensions():
    """返回支持的文件扩展名列表（小写）。"""
    return {".docx", ".doc", ".idml"}


def default_convert_file_to_md(file_path, output_md, img_dir, use_mathjax=False):
    """默认的文件转 Markdown 方法（仅支持 Word 文档）。
    
    Args:
        file_path: 输入文件路径
        output_md: 输出 Markdown 文件路径
        img_dir: 图片输出目录
        use_mathjax: 是否使用 MathJax
    
    Returns:
        dict: 包含 success 和 needs_post_process 等信息
    """
    from proseproof.core.pandoc_utils import convert_with_pandoc, check_pandoc, enhance_docx_conversion

    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".docx", ".doc"):
        if not check_pandoc():
            log("❌ Pandoc 未安装，无法转换 Word 文档")
            return {"success": False, "needs_post_process": True}
        ok = convert_with_pandoc(file_path, output_md, img_dir, use_mathjax=use_mathjax)
        if ok:
            enhance_docx_conversion(file_path, output_md)
        return {"success": ok, "needs_post_process": True}
    
    log(f"❌ 不支持的文件格式: {ext}")
    return {"success": False, "needs_post_process": False}


def default_collect_paper_dirs(base_path):
    result = []
    base = Path(base_path)
    if not base.exists(): return result
    sub_items = [x for x in base.iterdir() if x.is_dir()]
    sub_names = [x.name for x in sub_items]
    def _is_frag_dir(name):
        return name.startswith('frag_')
    has_frag_dir = any(_is_frag_dir(n) for n in sub_names)
    if has_frag_dir:
        result.append(str(base))
    else:
        for d in sub_items:
            inner = [x.name for x in d.iterdir() if x.is_dir()]
            if any(_is_frag_dir(n) for n in inner):
                result.append(str(d))
    return result
