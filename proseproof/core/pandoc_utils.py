import os, subprocess
from proseproof.core.logging_utils import log


def enhance_docx_conversion(docx_path, output_md):
    """增强 Word 文档转换，补充 Pandoc 丢失的格式。

    用 python-docx 提取着重号、波浪线、下划线等特殊格式，
    在 Markdown 中用自定义标记保留。

    Args:
        docx_path: 原始 Word 文档路径
        output_md: Pandoc 转换后的 Markdown 文件路径

    Returns:
        bool: 是否成功增强
    """
    try:
        from proseproof.shared.docx_format_enhancer import inject_format_markers
    except ImportError:
        return False

    try:
        with open(output_md, 'r', encoding='utf-8') as f:
            md_text = f.read()

        enhanced = inject_format_markers(md_text, docx_path)

        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(enhanced)

        return True
    except Exception as e:
        log(f"⚠️ 格式增强失败: {e}")
        return False

PANDOC_PATH = None


def find_pandoc():
    global PANDOC_PATH
    if PANDOC_PATH:
        return PANDOC_PATH
    import sys
    if getattr(sys, 'frozen', False):
        local = os.path.join(os.path.dirname(sys.executable), "pandoc.exe")
        if os.path.exists(local):
            PANDOC_PATH = local
            return PANDOC_PATH
    PANDOC_PATH = "pandoc"
    return PANDOC_PATH



def _gen_clean_md(md_path):
    """从 raw.md 生成 _clean.md（去除所有格式标记和批注，保留正文文字）"""
    from proseproof.shared.docx_format_enhancer import strip_format_markers

    with open(md_path, 'r', encoding='utf-8') as f:
        text = f.read()

    # Step 1: 去掉【着重】【下划线】等格式标记对
    text = strip_format_markers(text)

    # Step 2: 去掉 <批注 id=N>...</批注> 标记
    text = re.sub(r'<批注\s+id=\d+>.*?</批注>', '', text, flags=re.DOTALL)

    # Step 3: bold/italic 保留文字，只去 ** 标记
    import re
    p_bold = re.compile('\\*\\*([^*]+)\\*\\*')
    text = p_bold.sub('\\1', text)
    p_ul = re.compile('__([^_]+)__')
    text = p_ul.sub('\\1', text)

    # 输出到 _clean.md
    base, ext = os.path.splitext(md_path)
    if base.endswith('_raw'):
        clean_base = base[:-4]
    else:
        clean_base = base
    clean_path = clean_base + '_clean' + ext
    with open(clean_path, 'w', encoding='utf-8') as f:
        f.write(text)

def check_pandoc():
    pandoc = find_pandoc()
    try:
        r = subprocess.run([pandoc, "--version"], capture_output=True, text=True,
                           **(dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else {}))
        if r.returncode == 0:
            log(f"✅ Pandoc: {r.stdout.splitlines()[0]}")
            return True
    except FileNotFoundError:
        log("❌ Pandoc 未安装")
    return False


def convert_with_pandoc(input_path, output_md, img_dir, use_mathjax=False):
    pandoc = find_pandoc()
    # -t markdown-smart: 禁用 pandoc 的“智能引号”扩展，
    # 防止中文弯引号 "" 被转换为英文直引号 ""
    cmd = [
        pandoc, "-f", "docx", "-t", "markdown-smart",
        "--extract-media", img_dir, "--wrap", "none",
        "--markdown-headings", "atx",
    ]
    if use_mathjax:
        cmd.insert(3, "--mathjax")
    cmd.extend([input_path, "-o", output_md])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           **(dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else {}))
        return r.returncode == 0
    except Exception as e:
        log(f"   Pandoc 异常: {e}")
        return False
