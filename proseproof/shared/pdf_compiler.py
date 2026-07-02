"""
LaTeX → PDF 编译模块
调用 xelatex 编译 .tex 文件，处理错误和清理辅助文件。

xelatex 查找优先级：
1. XELATEX_PATH 环境变量（显式覆盖）
2. 系统 PATH 上的 xelatex（已安装 TeX Live / MiKTeX）
3. 内嵌便携版（PyInstaller 打包时随 exe 分发）
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

# 模块级缓存：已发现的 xelatex 路径
_XELATEX_PATH = None


def _find_bundled_xelatex(exe_dir: str) -> str | None:
    """在内嵌便携 TeX 发行版中查找 xelatex.exe。

    PyInstaller v5.x: 数据在 exe 同级的 texlive/bin/windows/xelatex.exe
    PyInstaller v6.x: 数据在 exe_dir/_internal/texlive/bin/windows/xelatex.exe
    """
    candidates = [
        os.path.join(exe_dir, "_internal", "texlive", "bin", "windows", "xelatex.exe"),
        os.path.join(exe_dir, "texlive", "bin", "windows", "xelatex.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _find_xelatex() -> str:
    """定位 xelatex.exe，缓存结果。

    优先级：环境变量 > 内嵌便携版 > 系统 PATH
    打包后优先使用内嵌版，避免系统 LaTeX 版本/配置不兼容。
    """
    global _XELATEX_PATH
    if _XELATEX_PATH is not None:
        return _XELATEX_PATH

    # 1. 显式环境变量（用户明确指定）
    env_path = os.environ.get("XELATEX_PATH")
    if env_path and os.path.isfile(env_path):
        _XELATEX_PATH = env_path
        return _XELATEX_PATH

    # 2. 内嵌便携版（打包后优先，确保版本一致）
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        bundled = _find_bundled_xelatex(exe_dir)
        if bundled:
            _XELATEX_PATH = bundled
            return _XELATEX_PATH

    # 3. 系统 PATH（开发时或便携版不可用时）
    system_xelatex = shutil.which("xelatex")
    if system_xelatex:
        _XELATEX_PATH = system_xelatex
        return _XELATEX_PATH

    raise FileNotFoundError(
        "xelatex not found. "
        "Install TeX Live, set XELATEX_PATH, or ensure the portable "
        "distribution is bundled correctly."
    )


def _get_texmf_root(xelatex_path: str) -> str | None:
    """从 xelatex 二进制位置推导 TEXMF 根目录。

    仅在 PyInstaller 打包后 + 检测到内嵌便携版 texmf.cnf 时返回路径。
    系统安装的 TeX Live 无需干预环境变量，返回 None。
    """
    if not getattr(sys, 'frozen', False):
        return None
    bin_dir = os.path.dirname(xelatex_path)          # .../bin/windows
    texlive_dir = os.path.dirname(os.path.dirname(bin_dir))  # .../texlive
    texmf_cnf = os.path.join(texlive_dir, "texmf.cnf")
    if os.path.isfile(texmf_cnf):
        return texlive_dir
    return None


def _copy_fmt_to_tmpdir(texmf_root: str, tmpdir: str) -> None:
    """复制 xelatex 格式文件到临时 TEXMFVAR 目录。

    TEXMFVAR 被设为临时目录（避免写入只读内嵌树），
    但 xelatex.fmt 需要位于 TEXMFVAR 可搜索路径中。
    """
    fmt_src = os.path.join(texmf_root, "texmf-var", "web2c", "xetex", "xelatex.fmt")
    if os.path.isfile(fmt_src):
        fmt_dst_dir = os.path.join(tmpdir, "web2c", "xetex")
        os.makedirs(fmt_dst_dir, exist_ok=True)
        fmt_dst = os.path.join(fmt_dst_dir, "xelatex.fmt")
        if not os.path.isfile(fmt_dst):
            shutil.copy2(fmt_src, fmt_dst)


def _copy_mapfiles_to_tmpdir(texmf_root: str, tmpdir: str) -> str:
    """复制字体映射/编码文件到临时目录（ASCII 路径）。

    xdvipdfmx 和 xetex 字体加载器可能无法处理 CJK 路径。
    将 fonts/map、fonts/enc、字体文件复制到临时目录。
    """
    texmf_dist = os.path.join(texmf_root, "texmf-dist")
    fonts_src = os.path.join(texmf_dist, "fonts")
    fonts_tmp = os.path.join(tmpdir, "fonts")

    # 映射和编码文件（小，必须复制）
    for sub in ["map", "enc", "cmap"]:
        src = os.path.join(fonts_src, sub)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(fonts_tmp, sub)
        if not os.path.isdir(dst):
            shutil.copytree(src, dst)

    # 字体文件本身（避免 CJK 路径干扰 fontspec / xdvipdfmx / FreeType2）
    for sub in ["opentype", "truetype", "type1", "tfm"]:
        src = os.path.join(fonts_src, sub)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(fonts_tmp, sub)
        if not os.path.isdir(dst):
            shutil.copytree(src, dst)

    return fonts_tmp


def _generate_runtime_fonts_conf(fonts_dir: str, tmpdir: str) -> str:
    """在运行时生成 fonts.conf，使用当前部署路径（而非构建时硬编码路径）。

    fonts_dir 应指向字体所在目录（如 tmpdir/fonts），避免 CJK 路径干扰 xdvipdfmx。

    返回 fonts.conf 文件路径。
    """
    fc_cache = os.path.join(tmpdir, "fonts", "cache")
    opentype = os.path.join(fonts_dir, "opentype")
    truetype = os.path.join(fonts_dir, "truetype")
    type1 = os.path.join(fonts_dir, "type1")

    os.makedirs(fc_cache, exist_ok=True)

    conf = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        '<fontconfig>\n'
        f'  <cachedir>{fc_cache.replace(os.sep, "/")}</cachedir>\n'
        f'  <dir>{opentype.replace(os.sep, "/")}</dir>\n'
        f'  <dir>{truetype.replace(os.sep, "/")}</dir>\n'
        f'  <dir>{type1.replace(os.sep, "/")}</dir>\n'
        '</fontconfig>\n'
    )

    conf_path = os.path.join(tmpdir, "fonts.conf")
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(conf)
    return conf_path


def compile_to_pdf(tex_path: str, output_dir: str | None = None,
                   images_map: dict | None = None) -> str:
    """编译 .tex 文件为 PDF。

    在临时目录（ASCII 路径）编译以避免 xelatex 对中文路径的兼容问题，
    然后将 PDF 复制到目标 output_dir。

    Args:
        tex_path: .tex 文件路径
        output_dir: PDF 输出目录，默认为 .tex 同目录
        images_map: {section_title: {filename: source_path}} 图片映射，直接复制到临时目录

    Returns:
        生成的 PDF 文件路径

    Raises:
        FileNotFoundError: tex_path 不存在
        RuntimeError: xelatex 编译失败（含日志摘要）
    """
    if not os.path.isfile(tex_path):
        raise FileNotFoundError(f"TeX file not found: {tex_path}")

    if output_dir is None:
        output_dir = os.path.dirname(tex_path) or "."

    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(tex_path))[0]
    target_pdf = os.path.join(output_dir, f"{base}.pdf")

    # 创建临时目录用于编译（ASCII 路径，避免 xelatex 对中文路径的兼容问题）
    tmpdir = tempfile.mkdtemp(prefix="latex_compile_")
    tex_dir = os.path.dirname(tex_path) or "."
    tmp_tex = os.path.join(tmpdir, f"{base}.tex")

    # 定位 xelatex 并推导 texmf 根目录
    xelatex_path = _find_xelatex()
    texmf_root = _get_texmf_root(xelatex_path)

    # 内嵌便携版：将格式文件 + 字体映射复制到临时目录（ASCII 路径）
    # 避免 CJK 路径导致 xdvipdfmx 失败
    fonts_tmp = None
    if texmf_root:
        _copy_fmt_to_tmpdir(texmf_root, tmpdir)
        fonts_tmp = _copy_mapfiles_to_tmpdir(texmf_root, tmpdir)
        runtime_fonts_conf = _generate_runtime_fonts_conf(fonts_tmp, tmpdir)

    try:
        # 复制 .tex 到临时目录
        shutil.copy2(tex_path, tmp_tex)

        # 从 images_map 直接复制图片到临时目录
        if images_map:
            for sec_title, imgs in images_map.items():
                sec_img_dir = os.path.join(tmpdir, sec_title, "images")
                os.makedirs(sec_img_dir, exist_ok=True)
                for fname, src in imgs.items():
                    shutil.copy2(src, os.path.join(sec_img_dir, fname))

        # 也复制 tex_dir 下的图片目录（兼容旧调用）
        for item in os.listdir(tex_dir):
            src = os.path.join(tex_dir, item)
            if os.path.isdir(src) and item not in (images_map or {}):
                dst = os.path.join(tmpdir, item)
                if not os.path.exists(dst):
                    shutil.copytree(src, dst)

        # 在临时目录中编译
        # 两步法：xelatex -no-pdf 生成 XDV，然后 xdvipdfmx 转 PDF。
        # 一步法（xelatex 内部调用 xdvipdfmx）在 Windows 便携版下会因
        # "系统找不到指定的路径" 失败（xdvipdfmx 子进程搜索 dvipdfmx.cfg 时失败）。
        # 两步法让 xdvipdfmx 作为独立进程运行，env vars 完整传递，可靠得多。
        xdv_path = os.path.join(tmpdir, f"{base}.xdv")
        log_path = os.path.join(tmpdir, f"{base}.log")

        # Step 1: xelatex -no-pdf — 生成 XDV
        cmd1 = [
            xelatex_path, "-no-pdf", "-interaction=nonstopmode",
            f'-output-directory={tmpdir}', tmp_tex,
        ]
        compile_kwargs = {
            'timeout': 120,
            'cwd': tmpdir,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
        }
        if texmf_root:
            env = os.environ.copy()
            texmf_dist = os.path.join(texmf_root, "texmf-dist")
            texmf_var = os.path.join(texmf_root, "texmf-var")

            env["TEXMFDIST"] = texmf_dist
            env["TEXMFVAR"] = tmpdir
            env["TEXMF"] = texmf_var + ";" + tmpdir + ";!!" + texmf_dist
            env["TEXMFCNF"] = texmf_root + ";" + texmf_dist + "/web2c"
            fc_dir = os.path.join(tmpdir, "fonts", "cache")
            os.makedirs(fc_dir, exist_ok=True)
            env["FC_CACHEDIR"] = fc_dir
            env["FONTCONFIG_PATH"] = tmpdir
            env["FONTCONFIG_FILE"] = runtime_fonts_conf

            env["TEXINPUTS"] = ".;" + texmf_dist + "/tex//"
            env["TEXINPUTS.latex"] = ".;" + texmf_dist + "/tex/{latex,generic,xetex,}//"
            env["TEXFORMATS"] = ".;" + tmpdir + "/web2c/{xetex,}//"

            if fonts_tmp:
                env["OPENTYPEFONTS"] = ".;" + fonts_tmp + "/opentype//"
                env["TTFONTS"] = ".;" + fonts_tmp + "/truetype//"
                env["T1FONTS"] = ".;" + fonts_tmp + "/type1//"
                env["TFMFONTS"] = ".;" + fonts_tmp + "/tfm//"
                env["TEXFONTMAPS"] = ".;" + fonts_tmp + "/map//"
                env["ENCFONTS"] = ".;" + fonts_tmp + "/enc//"
                env["TEXINPUTS"] = ".;" + texmf_dist + "/tex//;" + fonts_tmp + "/opentype//;" + fonts_tmp + "/truetype//"

            compile_kwargs['env'] = env
        if os.name == 'nt':
            compile_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            compile_kwargs['startupinfo'] = si

        retcode1 = subprocess.call(cmd1, **compile_kwargs)
        # nonstopmode 下 xelatex 即使只有 minor warnings（如字体警告）
        # 也会返回非零，但 XDV 可能生成成功。以 XDV 是否有效为准。
        xdv_ok = os.path.isfile(xdv_path) and os.path.getsize(xdv_path) > 100
        if not xdv_ok:
            # xelatex 阶段失败——读取 .log 诊断
            log_text = ""
            if os.path.isfile(log_path):
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    log_text = f.read()
            diag = [ln.strip() for ln in log_text.splitlines()
                    if ln.strip().startswith("!") or "fatal" in ln.strip().lower()
                    or "Error" in ln.strip()]
            tail = [ln.strip() for ln in log_text.splitlines()[-10:] if ln.strip()]
            raise RuntimeError(
                f"XeLaTeX stage failed (retcode={retcode1}).\n"
                f"--- DIAGNOSTIC ---\n" + "\n".join(diag[-15:]) + "\n"
                f"--- LOG TAIL ---\n" + "\n".join(tail))

        # Step 2: xdvipdfmx — 将 XDV 转为 PDF
        xdvipdfmx_path = os.path.join(os.path.dirname(xelatex_path), "xdvipdfmx.exe")
        tmp_pdf = os.path.join(tmpdir, f"{base}.pdf")
        cmd2 = [xdvipdfmx_path, "-o", tmp_pdf, xdv_path]
        compile_kwargs2 = {
            'timeout': 120,
            'cwd': tmpdir,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
        }
        if texmf_root:
            env2 = env.copy()
            env2["DVIPDFMXINPUTS"] = ".;" + texmf_dist + "/dvipdfmx//"
            compile_kwargs2['env'] = env2
        if os.name == 'nt':
            compile_kwargs2['creationflags'] = subprocess.CREATE_NO_WINDOW
            compile_kwargs2['startupinfo'] = compile_kwargs.get('startupinfo')

        retcode2 = subprocess.call(cmd2, **compile_kwargs2)

        tmp_pdf = os.path.join(tmpdir, f"{base}.pdf")

        # 检测 xdvipdfmx 阶段失败
        pdf_exists = os.path.isfile(tmp_pdf)
        pdf_size = os.path.getsize(tmp_pdf) if pdf_exists else 0
        is_stub_pdf = pdf_exists and pdf_size < 1024

        # 从 xelatex .log 提取诊断信息（两步共享同一日志文件）
        log_text = ""
        if os.path.isfile(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                log_text = f.read()

        # 使用行首匹配避免子串误判：
        # "I've just inserted will cause me to report a runaway argument"
        # 是 TeX 解释性文本，并非真实的 Runaway argument 错误。
        # 真正的致命错误以 "!" 开头且独占一行或以 "Runaway argument?" 开头。
        has_fatal_error = (
            re.search(r'^Runaway argument\?', log_text, re.MULTILINE) is not None
            or re.search(r'^Emergency stop', log_text, re.MULTILINE) is not None
            or re.search(r'^No pages of output', log_text, re.MULTILINE) is not None
        )

        if retcode2 != 0 or is_stub_pdf or has_fatal_error:
            diagnostic_lines = []
            for ln in log_text.splitlines():
                stripped = ln.strip()
                if not stripped:
                    continue
                if (stripped.startswith("!") or
                    "fatal" in stripped.lower() or
                    "error:" in stripped.lower() or
                    "Error" in stripped):
                    diagnostic_lines.append(stripped)

            tail_lines = [ln.strip() for ln in log_text.splitlines()[-15:] if ln.strip()]
            diagnostic = "\n".join(diagnostic_lines[-15:])
            tail = "\n".join(tail_lines)

            raise RuntimeError(
                f"xdvipdfmx stage failed (xelatex_ret={retcode1}, "
                f"xdvipdfmx_ret={retcode2}, pdf_exists={pdf_exists}, "
                f"pdf_size={pdf_size}).\n"
                f"--- DIAGNOSTIC ---\n{diagnostic}\n"
                f"--- LOG TAIL ---\n{tail}")

        # 复制 PDF 到目标目录
        shutil.copy2(tmp_pdf, target_pdf)

    finally:
        # 清理临时目录
        shutil.rmtree(tmpdir, ignore_errors=True)

    return target_pdf
