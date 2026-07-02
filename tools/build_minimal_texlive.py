#!/usr/bin/env python3
"""
Build a minimal portable TeX Live distribution for bundling with PyInstaller.

Uses xelatex -recorder to capture the exact set of files needed to
compile a representative document, then copies only those files into
a portable directory structure.

Usage:
    python tools/build_minimal_texlive.py [--texlive ROOT] [--output DIR]

Output structure:
    bundled_texlive/
        texmf.cnf
        bin/windows/      # engine binaries + DLLs
        texmf-dist/       # minimal TeX packages + fonts
        texmf-var/        # format file
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile


def parse_args():
    p = argparse.ArgumentParser(description="Build minimal portable TeX Live")
    p.add_argument("--texlive", default=r"C:/Program Files/texlive/2026",
                   help="Path to installed TeX Live root")
    p.add_argument("--output", default="bundled_texlive",
                   help="Output directory for the portable distribution")
    return p.parse_args()


# ---- Test .tex content exercising ALL required packages ----

TEST_TEX = r"""
\documentclass[12pt,a4paper]{article}
\usepackage{xeCJK}

% Fonts — use filenames so kpathsea can find them without fontconfig
\setCJKmainfont{FandolSong-Regular.otf}[
  BoldFont=FandolSong-Bold.otf,
  ItalicFont=FandolKai-Regular.otf]
\setCJKsansfont{FandolHei-Regular.otf}[
  BoldFont=FandolHei-Bold.otf]
\setCJKmonofont{FandolKai-Regular.otf}
\setmainfont{texgyretermes-regular.otf}[
  BoldFont=texgyretermes-bold.otf,
  ItalicFont=texgyretermes-italic.otf,
  BoldItalicFont=texgyretermes-bolditalic.otf]
\setsansfont{texgyretermes-regular.otf}
\setmonofont{DejaVuSans.ttf}
\newfontfamily{\fallbacksymbols}{DejaVuSans.ttf}[Scale=MatchUppercase]

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage[version=4]{mhchem}
\usepackage{graphicx}
\usepackage{paracol}
\usepackage{xcolor}
\usepackage{fancyhdr}
\usepackage{geometry}
\usepackage{tikz}
\usepackage[normalem]{ulem}        % \sout, \uwave, \uline, \dout
\usepackage{xeCJKfntef}      % \CJKunderdot

\newcommand{\redcircled}[1]{%
  \tikz[baseline=(char.base)]{%
    \node[shape=circle,draw=red,inner sep=0.3pt,text=red,line width=0.4pt,
          font=\fontsize{7pt}{7pt}\selectfont] (char) {#1};%
  }%
}
\newcommand{\corrmark}[2]{%
  \textcolor{red}{#1}\textsuperscript{\textcolor{red}{\redcircled{#2}}}%
}

\geometry{a4paper,left=1.5cm,right=1.5cm,top=2cm,bottom=2cm,columnsep=1cm}

\newlength{\colwidthinner}
\newcommand{\calccolwidth}{%
  \setlength{\colwidthinner}{\dimexpr\linewidth-2\fboxsep-2\fboxrule\relax}%
}
\newcommand{\correctionbox}[1]{%
  \calccolwidth
  \colorbox{blue!8}{\parbox{\colwidthinner}{#1}}%
}
\newcommand{\lt}{{<}}
\newcommand{\gt}{{>}}

% 双删除线命令（基于 ulem）
\makeatletter
\newcommand{\dout}{%
  \bgroup
  \markoverwith{%
    \rule[-0.8ex]{0.1pt}{2.5ex}%
    \hskip-0.1pt
    \rule[0.2ex]{0.1pt}{2.5ex}%
  }%
  \ULon
}
\makeatother

\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{Test}
\fancyhead[R]{\leftmark}
\fancyfoot[C]{\thepage}

\begin{document}

\title{Test}
\maketitle

\section{Chinese Text}
这是一段中文测试文本，用于验证中文字体是否正确嵌入。
高中物理题目：一个质量为$m$的物体从高度$h$处自由下落。

\section{Math}
Inline: $\alpha + \beta = \gamma$, $E=mc^2$

Display:
\[
x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}
\]
\[
v = v_0 + at, \quad x = v_0t + \frac{1}{2}at^2
\]

\section{Chemistry}
\ce{2H2 + O2 -> 2H2O}
\ce{CH4 + 2O2 -> CO2 + 2H2O}
\ce{Ca^2+ + CO3^2- -> CaCO3 v}

\section{Paracol + Corrections}
\begin{paracol}{2}
Left column: original text with \corrmark{error}{1} marker.

More content in left column.
\switchcolumn
\correctionbox{Right column: correction suggestion for the error marked above.}
\switchcolumn*

\bigskip
Text with no correction.
\switchcolumn
\correctionbox{Another correction box with \textbf{bold} and math $x^2$.}
\switchcolumn*
\end{paracol}

\section{Fallback Symbols}
{\fallbacksymbols \char"2605\char"2606\char"2460\char"2461\char"2462}

\section{Format Markers (ulem + xeCJKfntef)}
\uline{下划线文本} \sout{删除线} \uwave{波浪线}

\dout{双删除线}

\CJKunderdot{着重号文本}

\end{document}
"""


def find_fls_files(search_dir):
    """Find all .fls files in a directory tree."""
    result = []
    for root, dirs, files in os.walk(search_dir):
        for f in files:
            if f.endswith(".fls"):
                result.append(os.path.join(root, f))
    return result


def parse_fls(fls_path):
    """Parse a .fls file, return set of absolute paths from INPUT lines."""
    paths = set()
    with open(fls_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("INPUT "):
                p = line.split(" ", 1)[1].strip()
                if p:
                    paths.add(os.path.normpath(p))
    return paths


def map_to_texmf(paths, texlive_root):
    """Map absolute paths to {subtree: {relative_path: absolute_source}}.

    Returns dict like {'texmf-dist': {'tex/latex/base/article.cls': 'C:/...'}, ...}
    """
    result = {}
    subtrees = ["texmf-dist", "texmf-var", "texmf-sys-var", "texmf-local"]
    texlive_root = os.path.normcase(os.path.normpath(texlive_root))

    for p in paths:
        p_norm = os.path.normpath(p)
        if not os.path.isfile(p_norm):
            continue
        p_case = os.path.normcase(p_norm)
        for subtree in subtrees:
            prefix = os.path.normcase(os.path.join(texlive_root, subtree))
            if p_case.startswith(prefix + os.sep) or p_case == prefix:
                rel = os.path.relpath(p_norm, os.path.join(texlive_root, subtree))
                result.setdefault(subtree, {})[rel] = p_norm
                break

    return result


def copy_engine_binaries(texlive_root, output_dir):
    """Copy xelatex engine binaries and DLL dependencies."""
    bin_src = os.path.join(texlive_root, "bin", "windows")
    bin_dst = os.path.join(output_dir, "bin", "windows")
    os.makedirs(bin_dst, exist_ok=True)

    # Engine binaries (minimum set)
    files = [
        "xelatex.exe",
        "xetex.exe",
        "xetex.dll",
        "xdvipdfmx.exe",
        "dvipdfmx.dll",
        "icudt78.dll",
        "kpathsealibw64.dll",
        # MSVC runtime (needed by the above DLLs)
        "msvcp140.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "ucrtbase.dll",
    ]

    for f in files:
        src = os.path.join(bin_src, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(bin_dst, f))
            print(f"  bin/ {f}")
        else:
            print(f"  WARNING: {f} not found at {src}")

    # Copy CRT API set shims if present (may be needed on older Windows)
    for f in os.listdir(bin_src):
        if f.startswith("api-ms-win-crt-") and f.endswith(".dll"):
            src = os.path.join(bin_src, f)
            shutil.copy2(src, os.path.join(bin_dst, f))
            print(f"  bin/ {f}")


def copy_texmf_files(file_map, output_dir):
    """Copy texmf tree files preserving directory structure."""
    for subtree, files in file_map.items():
        for rel, src in files.items():
            dst = os.path.join(output_dir, subtree, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.isfile(dst):
                shutil.copy2(src, dst)


def copy_fonts(texlive_root, output_dir):
    """Explicitly copy font files needed by the template.

    The .fls recorder only captures files loaded through kpathsea's TeX
    input mechanism — font files loaded by xetex's fontloader or xdvipdfmx
    are NOT tracked. We copy these explicitly.
    """
    texmf_dist = os.path.join(texlive_root, "texmf-dist")
    fonts_src = os.path.join(texmf_dist, "fonts")
    fonts_dst = os.path.join(output_dir, "texmf-dist", "fonts")
    os.makedirs(fonts_dst, exist_ok=True)

    # Fonts needed by our template:
    # 1. Fandol (CJK: Song, Hei, Kai) — SIL OFL
    # 2. TeX Gyre Termes (Latin serif) — GUST Font License
    # 3. DejaVu Sans (symbol fallback) — Bitstream Vera + public domain
    # 4. Latin Modern (math + Latin fallback) — GUST Font License

    font_dirs = [
        ("opentype/public/fandol", [
            "FandolSong-Regular.otf", "FandolSong-Bold.otf",
            "FandolHei-Regular.otf", "FandolHei-Bold.otf",
            "FandolKai-Regular.otf",
        ]),
        ("opentype/public/tex-gyre", [
            "texgyretermes-regular.otf", "texgyretermes-bold.otf",
            "texgyretermes-italic.otf", "texgyretermes-bolditalic.otf",
        ]),
        ("truetype/public/dejavu", [
            "DejaVuSans.ttf",
        ]),
        ("opentype/public/lm", [
            "lmroman12-regular.otf", "lmroman12-bold.otf",
            "lmroman12-italic.otf", "lmroman12-bolditalic.otf",
            "lmroman10-regular.otf", "lmroman10-bold.otf",
            "lmroman10-italic.otf", "lmroman10-bolditalic.otf",
            "lmsans12-regular.otf", "lmsans10-regular.otf",
            "lmmath-regular.otf",
        ]),
    ]

    for rel_dir, files in font_dirs:
        src_dir = os.path.join(fonts_src, rel_dir)
        dst_dir = os.path.join(fonts_dst, rel_dir)
        os.makedirs(dst_dir, exist_ok=True)
        for fname in files:
            src = os.path.join(src_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dst_dir, fname))
                print(f"  fonts/ {rel_dir}/{fname}")

    # Also copy font mapping files (tex-text.tec etc.) for xetex font loader.
    # These are in fonts/misc/xetex/fontmapping/base/ and are not captured
    # by .fls recorder (font loader doesn't route through kpathsea).
    misc_src = os.path.join(fonts_src, "misc", "xetex", "fontmapping", "base")
    if os.path.isdir(misc_src):
        misc_dst = os.path.join(fonts_dst, "misc", "xetex", "fontmapping", "base")
        os.makedirs(misc_dst, exist_ok=True)
        for fname in os.listdir(misc_src):
            src = os.path.join(misc_src, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(misc_dst, fname))
        print(f"  fonts/misc/ {len(os.listdir(misc_src))} files")


def copy_type1_math_fonts(texlive_root, output_dir):
    """Copy Type1 math font .pfb files that might not be captured by .fls."""
    texmf_dist = os.path.join(texlive_root, "texmf-dist")
    fonts_src = os.path.join(texmf_dist, "fonts")

    # CM/AMS Type1 fonts for math (needed if Latin Modern Math isn't loaded)
    type1_dirs = [
        "type1/public/amsfonts/cm",
        "type1/public/amsfonts/cmextra",
        "type1/public/amsfonts/symbols",
        "type1/public/cm",
    ]

    for rel_dir in type1_dirs:
        src_dir = os.path.join(fonts_src, rel_dir)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(output_dir, "texmf-dist", "fonts", rel_dir)
        os.makedirs(dst_dir, exist_ok=True)
        count = 0
        for fname in os.listdir(src_dir):
            if fname.endswith(".pfb") or fname.endswith(".pfa"):
                src = os.path.join(src_dir, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(dst_dir, fname))
                    count += 1
        if count:
            print(f"  type1/ {rel_dir}: {count} files")


def copy_dvipdfmx_config(texlive_root, output_dir):
    """Copy dvipdfmx.cfg config and font map files for xdvipdfmx.

    dvipdfmx.cfg is NOT captured by .fls recorder because it's read by
    xdvipdfmx (a separate process spawned by xelatex), not by xetex itself.
    Without it, xdvipdfmx can't find font map files and fails to embed fonts.
    """
    texmf_dist = os.path.join(texlive_root, "texmf-dist")

    # dvipdfmx.cfg
    cfg_src = os.path.join(texmf_dist, "dvipdfmx", "dvipdfmx.cfg")
    if os.path.isfile(cfg_src):
        cfg_dst = os.path.join(output_dir, "texmf-dist", "dvipdfmx")
        os.makedirs(cfg_dst, exist_ok=True)
        shutil.copy2(cfg_src, os.path.join(cfg_dst, "dvipdfmx.cfg"))
        print(f"  Copied dvipdfmx/dvipdfmx.cfg")

    # pdftex.map (generated by updmap, in texmf-var)
    for map_name in ["pdftex.map", "kanjix.map"]:
        map_src = os.path.join(texlive_root, "texmf-var", "fonts", "map",
                               "pdftex", "updmap", map_name)
        if not os.path.isfile(map_src):
            # Fallback: try texmf-dist
            map_src = os.path.join(texmf_dist, "fonts", "map", "dvipdfmx", map_name)
        if os.path.isfile(map_src):
            map_dst = os.path.join(output_dir, "texmf-dist", "fonts", "map",
                                   "dvipdfmx")
            os.makedirs(map_dst, exist_ok=True)
            shutil.copy2(map_src, os.path.join(map_dst, map_name))
            print(f"  Copied fonts/map/dvipdfmx/{map_name}")
        else:
            print(f"  WARNING: {map_name} not found — "
                  "xdvipdfmx will warn but should still work for OpenType fonts")


def copy_web2c_config(texlive_root, output_dir):
    """Copy the system's texmf-dist/web2c/texmf.cnf for engine memory settings.

    The pre-built xelatex.fmt requires matching memory/string/pool values.
    Our minimal texmf.cnf in the root only handles paths; the full engine
    config is in texmf-dist/web2c/texmf.cnf.
    """
    src = os.path.join(texlive_root, "texmf-dist", "web2c", "texmf.cnf")
    if os.path.isfile(src):
        dst_dir = os.path.join(output_dir, "texmf-dist", "web2c")
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(dst_dir, "texmf.cnf"))
        print(f"  Copied texmf-dist/web2c/texmf.cnf")


def write_fonts_conf(output_dir):
    """Generate fonts.conf for xetex's fontconfig integration.

    Without this, fontconfig fails with 'Cannot load default config file'
    and xetex can't find any fonts (even through kpathsea filename lookup).
    """
    fc_cache = os.path.join(output_dir, "texmf-var", "fonts", "cache")
    opentype = os.path.join(output_dir, "texmf-dist", "fonts", "opentype")
    truetype = os.path.join(output_dir, "texmf-dist", "fonts", "truetype")
    type1 = os.path.join(output_dir, "texmf-dist", "fonts", "type1")

    os.makedirs(fc_cache, exist_ok=True)

    # Use forward slashes (fontconfig on Windows accepts them)
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

    conf_path = os.path.join(output_dir, "fonts.conf")
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(conf)
    print("  Generated fonts.conf")


def write_texmf_cnf(output_dir):
    """Generate minimal texmf.cnf for portable distribution."""
    cnf = r"""% Minimal texmf.cnf for portable xelatex distribution
% Engine memory settings are in texmf-dist/web2c/texmf.cnf (copied from system)
% This file only handles path setup.

TEXMFDIST = $SELFAUTOGRANDPARENT/texmf-dist
TEXMFVAR = $SELFAUTOGRANDPARENT/texmf-var
TEXMFSYSVAR = $SELFAUTOGRANDPARENT/texmf-var
TEXMFCONFIG = $TEXMFVAR

TEXMF = {$TEXMFVAR,$TEXMFSYSVAR,!!$TEXMFDIST}

% Output format for xelatex
TEXINPUTS.latex = .;$TEXMF/tex/{latex,generic,xetex,}//;$TEXMF/tex/{platex,uplatex,}//

% Font search paths
OPENTYPEFONTS = .;$TEXMF/fonts/opentype//
TTFONTS = .;$TEXMF/fonts/truetype//
T1FONTS = .;$TEXMF/fonts/type1//
AFMFONTS = .;$TEXMF/fonts/afm//
TFMFONTS = .;$TEXMF/fonts/tfm//
ENCFONTS = .;$TEXMF/fonts/enc//
TEXFONTMAPS = .;$TEXMF/fonts/map/{dvipdfmx,dvips,}//

% XeTeX font search: include bundled fonts + system fonts
OSFONTDIR = $TEXMFDIST/fonts/{opentype,truetype}//;$SystemRoot/fonts//;$LOCALAPPDATA/Microsoft/Windows/Fonts//

% Allow all file operations
openout_any = a
openin_any = a

% Security: no shell escape
shell_escape = 0

% Format file location
TEXFORMATS = .;$TEXMFVAR/web2c/{xetex,}//;$TEXMFDIST/web2c/{xetex,}//

% Fontconfig cache directory
FC_CACHEDIR = $TEXMFVAR/fonts/cache
"""
    cnf_path = os.path.join(output_dir, "texmf.cnf")
    with open(cnf_path, "w", encoding="utf-8") as f:
        f.write(cnf)
    print(f"  Generated texmf.cnf")


def main():
    args = parse_args()
    texlive_root = os.path.normpath(args.texlive)
    output_dir = os.path.abspath(args.output)

    if not os.path.isdir(texlive_root):
        sys.exit(f"TeX Live root not found: {texlive_root}")

    bin_dir = os.path.join(texlive_root, "bin", "windows")
    xelatex_exe = os.path.join(bin_dir, "xelatex.exe")
    if not os.path.isfile(xelatex_exe):
        sys.exit(f"xelatex.exe not found at {xelatex_exe}")

    print(f"TeX Live root: {texlive_root}")
    print(f"Output: {output_dir}")

    # Clean output directory
    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Compile test .tex with -recorder to capture dependencies
    print("\n[1/6] Compiling test documents with -recorder...")
    tmpdir = tempfile.mkdtemp(prefix="build_texlive_")

    # Test document 1: Full ctexart with all packages (12pt)
    tex_path1 = os.path.join(tmpdir, "test_full.tex")
    with open(tex_path1, "w", encoding="utf-8") as f:
        f.write(TEST_TEX)

    # Test document 2: Minimal article (10pt) to capture base files like size10.clo
    TEST_TEX_BASE = r"""
\documentclass{article}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{tikz}
\begin{document}
Hello $x=y$. \textcolor{red}{Red text}.
\tikz{\node{test};}
\end{document}
"""
    tex_path2 = os.path.join(tmpdir, "test_base.tex")
    with open(tex_path2, "w", encoding="utf-8") as f:
        f.write(TEST_TEX_BASE)

    all_fls_paths = set()
    for label, tex_path in [("full", tex_path1), ("base", tex_path2)]:
        cmd = [
            xelatex_exe, "-recorder", "-interaction=nonstopmode",
            f"-output-directory={tmpdir}", tex_path,
        ]
        retcode = subprocess.call(cmd, timeout=120, cwd=tmpdir,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        print(f"  {label}: ret={retcode}")

    # Check for .fls files
    fls_files = find_fls_files(tmpdir)
    if not fls_files:
        sys.exit("No .fls files found after compilation. Recorder may have failed.")
    print(f"  Found {len(fls_files)} .fls file(s)")

    # Step 2: Parse .fls and map to texmf subtrees
    print("\n[2/6] Parsing recorder output...")
    all_paths = set()
    for fls in fls_files:
        all_paths |= parse_fls(fls)

    print(f"  {len(all_paths)} unique input files referenced")

    file_map = map_to_texmf(all_paths, texlive_root)
    total_files = sum(len(v) for v in file_map.values())
    print(f"  {total_files} files in texmf tree")
    for subtree, files in file_map.items():
        print(f"    {subtree}: {len(files)} files")

    # Step 3: Copy engine binaries
    print("\n[3/6] Copying engine binaries...")
    copy_engine_binaries(texlive_root, output_dir)

    # Step 4: Copy texmf files
    print(f"\n[4/7] Copying {total_files} texmf files...")
    copy_texmf_files(file_map, output_dir)
    print(f"  Done.")

    # Step 4b: Copy system web2c texmf.cnf for engine memory settings
    print("\n[5/7] Copying web2c config...")
    copy_web2c_config(texlive_root, output_dir)

    # Step 5: Copy font files (not captured by .fls recorder)
    print("\n[6/8] Copying font files...")
    copy_fonts(texlive_root, output_dir)
    copy_type1_math_fonts(texlive_root, output_dir)

    # Step 6: Copy dvipdfmx config (not captured by .fls — separate process)
    print("\n[7/8] Copying dvipdfmx config...")
    copy_dvipdfmx_config(texlive_root, output_dir)

    # Step 7: Generate texmf.cnf
    print("\n[7/8] Generating texmf.cnf...")
    write_texmf_cnf(output_dir)

    # Step 8: Generate fonts.conf for fontconfig
    print("\n[8/8] Generating fonts.conf...")
    write_fonts_conf(output_dir)

    # Summary
    total_size = 0
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))

    print(f"\n=== Done ===")
    print(f"Output: {output_dir}")
    print(f"Total size: {total_size / (1024*1024):.1f} MB")
    print(f"Total files: {sum(1 for _, _, files in os.walk(output_dir) for _ in files)}")

    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
