"""Proseproof CLI —— 通用文稿校对工具命令行入口。

每个命令对应管线的一个阶段，单一职责：
  convert    Word/IDML → Markdown
  split      Markdown → fragments
  proofread  校对单个或批量片段
  typeset    校对数据 → LaTeX → PDF
  compile    .tex → PDF
  run        一键完整流水线
  profile    配置方案管理
"""
import os
import sys
from pathlib import Path
import click

from proseproof import __version__


# ── 辅助函数 ──

def _resolve_profile(profile_name: str):
    """解析配置方案目录。

    查找顺序：
    1. 用户项目目录下的 profiles/<name>/
    2. 包内置 profiles/<name>/
    3. 绝对路径
    """
    # 安全检查：拒绝路径穿越
    if '..' in profile_name or os.path.isabs(profile_name):
        # 允许绝对路径（if it exists），但拒绝相对穿越
        if '..' in profile_name:
            raise click.ClickException(f"非法的配置方案名: {profile_name}")

    # 绝对路径
    if os.path.isabs(profile_name) and os.path.isdir(profile_name):
        return profile_name

    # 包内置
    import proseproof.profiles
    builtin = os.path.join(os.path.dirname(proseproof.profiles.__path__[0]),
                            'profiles', profile_name)
    if os.path.isdir(builtin):
        return builtin

    # 当前目录
    cwd = os.path.join(os.getcwd(), 'profiles', profile_name)
    if os.path.isdir(cwd):
        return cwd

    return None


def _strip_images_from_md(md_path: str, mode: str | None,
                           size_threshold: int | None = None):
    """剥离 Markdown 中的图片引用。"""
    import re
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if mode == 'all':
        content = re.sub(r'!\[.*?\]\(.*?\)(\s*\{[^}]*\})?', '', content)
    elif mode == 'small' or size_threshold:
        threshold = size_threshold if size_threshold else 5 * 1024
        img_dir = os.path.dirname(md_path)
        def _filter_by_size(m):
            path = m.group(1)
            full_path = os.path.join(img_dir, path) if not os.path.isabs(path) else path
            if os.path.isfile(full_path) and os.path.getsize(full_path) < threshold:
                return ''
            return m.group(0)
        content = re.sub(r'!\[.*?\]\((.*?)\)(\s*\{[^}]*\})?', _filter_by_size, content)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(content)


def _load_profile(profile_dir: str):
    config_json = os.path.join(profile_dir, 'config.json')

    if not os.path.isfile(config_json):
        raise click.ClickException(f"配置文件不存在: {config_json}")

    if os.path.isfile(profile_py):
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location(
                'user_profile', profile_py)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            raise click.ClickException(
                f"profile.py 加载失败: {e}")
        # 查找 BaseProfile 子类
        from proseproof.core.base_profile import BaseProfile
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (isinstance(obj, type) and
                    issubclass(obj, BaseProfile) and
                    obj is not BaseProfile):
                return obj(profile_dir)
        raise click.ClickException(f"profile.py 中未找到 BaseProfile 子类")

    # 纯 JSON 模式：用默认 BaseProfile
    from proseproof.core.base_profile import BaseProfile
    return BaseProfile(profile_dir)


# ── CLI 主组 ──

@click.group()
@click.version_option(version=__version__, prog_name="proseproof")
def main():
    """Proseproof —— 通用 AI 文稿校对与 LaTeX 排版工具。

    管线阶段：转换 → 拆分 → 校对 → 排版 → 编译
    """
    pass


# ── convert ──

@main.command()
@click.argument('input_file', type=click.Path(exists=True))
@click.option('-o', '--output', default=None,
              help='输出 Markdown 文件路径（默认与输入同目录同名 .md）')
@click.option('--mathjax/--no-mathjax', default=False,
              help='使用 MathJax 公式格式')
@click.option('--strip-images', 'strip_mode', flag_value='all',
              help='剥离所有图片引用')
@click.option('--strip-small-images', 'strip_mode', flag_value='small',
              help='自动过滤 <5KB 的装饰性小图')
@click.option('--strip-images-below', type=int, default=None,
              help='过滤小于指定字节的图片')
def convert(input_file, output, mathjax, strip_mode, strip_images_below):
    """将 Word/IDML 文档转为 Markdown。"""
    from proseproof.core.defaults import default_convert_file_to_md
    from proseproof.core.logging_utils import log, set_log_func
    set_log_func(lambda msg: click.echo(msg))

    if output is None:
        base = os.path.splitext(input_file)[0]
        output = base + '.md'

    img_dir = os.path.splitext(output)[0] + '_images'
    os.makedirs(img_dir, exist_ok=True)

    log(f"转换: {input_file} → {output}")
    result = default_convert_file_to_md(input_file, output, img_dir,
                                         use_mathjax=mathjax)
    if result:
        # 图像清洗（如果指定了 --strip-* 选项）
        if strip_mode or strip_images_below:
            _strip_images_from_md(output, strip_mode, strip_images_below)
        click.echo(f"[OK] {output}")
    else:
        raise click.ClickException("转换失败")


# ── split ──

@main.command()
@click.argument('input_file', type=click.Path(exists=True))
@click.option('-o', '--output-dir', default='./fragments',
              help='输出目录（默认 ./fragments）')
@click.option('--mode', type=click.Choice(['heading', 'smart', 'deep', 'manual', 'rule', 'none']),
              default=None, help='拆分模式（默认从 config 读取，回退为 rule）')
@click.option('-p', '--profile', default='generic',
              help='配置方案名称或路径')
@click.option('--api-url', default=None, help='API 地址（smart/deep 模式需要）')
@click.option('--api-key', default=None, help='API Key（smart/deep 模式需要）')
@click.option('--model', default=None, help='模型名（smart/deep 模式需要）')
@click.option('--split-by-pattern', default=None,
              help='自定义正则切分（逃生舱，覆盖 --mode）')
def split(input_file, output_dir, mode, profile, api_url, api_key, model, split_by_pattern):
    """将 Markdown 拆分为片段。

    支持六种模式：
      heading - 按 Markdown 标题切分（零 LLM 成本）
      smart   - LLM 大纲驱动切分（极低成本，主力模式）
      deep    - LLM 全文切分（高成本兜底）
      manual  - 按 ###### 片段开始/结束 ###### 标记切分
      rule    - 按正则切分（默认）
      none    - 不切分，整份文档作为单一片段
    """
    from proseproof.core.logging_utils import log, set_log_func
    set_log_func(lambda msg: click.echo(msg))

    profile_dir = _resolve_profile(profile)
    if not profile_dir:
        raise click.ClickException(f"配置方案不存在: {profile}")

    app = _load_profile(profile_dir)
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    os.makedirs(output_dir, exist_ok=True)

    # mode 默认值：CLI 未指定时从 config 读取，回退到 rule
    if mode is None:
        mode = app.config.get("split", {}).get("mode", "rule")

    options = {"split_mode": mode}
    if split_by_pattern:
        options["split_mode"] = "pattern"
        options["split_pattern"] = split_by_pattern
    if mode in ('smart', 'deep'):
        options['api_url'] = api_url or os.environ.get('PROSEPROOF_API_URL', '')
        options['api_key'] = api_key or os.environ.get('PROSEPROOF_API_KEY', '')
        options['model'] = model or os.environ.get('PROSEPROOF_MODEL', '')

    result = app.split_document(input_file, output_dir, base_name, options)
    if result:
        click.echo(f"[OK] 拆分完成 → {os.path.join(output_dir, base_name)}")
    else:
        raise click.ClickException("拆分失败")


# ── proofread ──

@main.command()
@click.argument('path', type=click.Path(exists=True))
@click.option('-p', '--profile', default='generic',
              help='配置方案名称或路径')
@click.option('--api-url', default=None, help='API 地址')
@click.option('--api-key', default=None, help='API Key')
@click.option('--model', default=None, help='模型名')
@click.option('--react/--no-react', default=False,
              help='启用 ReAct 工具循环模式')
@click.option('--no-pdf', is_flag=True, default=False,
              help='不生成 PDF（仅出校对报告）')
@click.option('--source-mode', type=click.Choice(['文档', '批注评审']),
              default='文档', help='校对来源模式')
def proofread(path, profile, api_url, api_key, model, react, no_pdf, source_mode):
    """校对文稿片段（单片段或批量）。

    PATH 为单个片段目录时校对该片段；
    PATH 为包含多个 frag_*/ 的父目录时批量校对。
    """
    from proseproof.core.logging_utils import log, set_log_func
    set_log_func(lambda msg: click.echo(msg))

    profile_dir = _resolve_profile(profile)
    if not profile_dir:
        raise click.ClickException(f"配置方案不存在: {profile}")

    app = _load_profile(profile_dir)
    if react:
        app.react_mode = True

    api_url = api_url or os.environ.get('PROSEPROOF_API_URL', '')
    api_key = api_key or os.environ.get('PROSEPROOF_API_KEY', '')
    model = model or os.environ.get('PROSEPROOF_MODEL', '')

    # 判断是单片段还是批量
    from proseproof.core.defaults import default_collect_paper_dirs
    frag_dirs = default_collect_paper_dirs(path)

    if not frag_dirs:
        # 单片段模式
        frag_name = os.path.basename(path.rstrip('/\\'))
        generate_pdf = not no_pdf
        result = app.proofread_one(api_url, api_key, model, path, frag_name,
                                     generate_pdf=generate_pdf,
                                     source_mode=source_mode)
        if result.get('success'):
            click.echo(f"[OK] 校对完成 → {path}")
        else:
            raise click.ClickException(
                f"校对失败: {result.get('error', '未知错误')}")
    else:
        # 批量模式
        total = len(frag_dirs)
        success = 0
        for frag_dir in frag_dirs:
            frag_name = os.path.basename(frag_dir.rstrip('/\\'))
            log(f"校对 [{success + 1}/{total}]: {frag_name}")
            result = app.proofread_one(api_url, api_key, model, frag_dir,
                                         frag_name,
                                         generate_pdf=not no_pdf,
                                         source_mode=source_mode)
            if result.get('success'):
                success += 1
        click.echo(f"[OK] 批量校对完成: {success}/{total}")


# ── typeset ──

@main.command()
@click.argument('path', type=click.Path(exists=True))
@click.option('-o', '--output', default=None,
              help='输出文件路径')
@click.option('--no-combine', is_flag=True, default=False,
              help='多片段时不自动汇总，每片段独立 PDF')
@click.option('--title', default='校对报告', help='PDF 标题')
def typeset(path, output, no_combine, title):
    """将校对数据排版为 LaTeX 并编译 PDF。

    PATH 为单个片段目录或包含多个片段的父目录。
    多片段默认自动汇总为一份 PDF。
    """
    from proseproof.core.logging_utils import log, set_log_func
    set_log_func(lambda msg: click.echo(msg))

    from proseproof.core.defaults import default_collect_paper_dirs
    frag_dirs = default_collect_paper_dirs(path)

    if not frag_dirs:
        # 单片段
        from proseproof.shared.latex_generator import generate_tex
        data_json = os.path.join(path, '_校对数据.json')
        md_file = None
        for f in os.listdir(path):
            if f.endswith('.md') and not f.startswith('_'):
                md_file = os.path.join(path, f)
                break
        if not os.path.isfile(data_json) or not md_file:
            raise click.ClickException("未找到 _校对数据.json 或源 .md 文件")

        if output is None:
            output = os.path.join(path, 'output.tex')
        generate_tex(data_json, md_file, output)
        click.echo(f"[OK] {output}")

        # 尝试编译 PDF
        from proseproof.shared.pdf_compiler import compile_to_pdf
        pdf_path = output.replace('.tex', '.pdf')
        compile_to_pdf(output, os.path.dirname(output))
        if os.path.isfile(pdf_path):
            click.echo(f"[OK] {pdf_path}")
    else:
        # 多片段汇总
        from proseproof.shared.latex_generator import generate_combined_pdf
        if output is None:
            output = os.path.join(path, 'output.pdf')
        if no_combine:
            for frag_dir in frag_dirs:
                data_json = os.path.join(frag_dir, '_校对数据.json')
                md_file = None
                for f in os.listdir(frag_dir):
                    if f.endswith('.md') and not f.startswith('_'):
                        md_file = os.path.join(frag_dir, f)
                        break
                if os.path.isfile(data_json) and md_file:
                    from proseproof.shared.latex_generator import generate_tex
                    out_tex = os.path.join(frag_dir, 'output.tex')
                    generate_tex(data_json, md_file, out_tex, title=title)
                    from proseproof.shared.pdf_compiler import compile_to_pdf
                    compile_to_pdf(out_tex, frag_dir)
                    click.echo(f"[OK] {os.path.join(frag_dir, 'output.pdf')}")
        else:
            generate_combined_pdf(frag_dirs, output)
            click.echo(f"[OK] {output}")


# ── compile ──

@main.command()
@click.argument('tex_file', type=click.Path(exists=True))
@click.option('-o', '--output', default=None,
              help='输出 PDF 路径（默认与 .tex 同目录同名）')
def compile(tex_file, output):
    """编译 .tex 文件为 PDF（需要 xelatex）。"""
    from proseproof.core.logging_utils import log, set_log_func
    set_log_func(lambda msg: click.echo(msg))

    from proseproof.shared.pdf_compiler import compile_to_pdf
    output_dir = os.path.dirname(tex_file) or '.'
    result = compile_to_pdf(tex_file, output_dir)
    if result and os.path.isfile(result):
        click.echo(f"[OK] {result}")
    else:
        raise click.ClickException("编译失败，请检查 xelatex 是否已安装")


# ── run ──

@main.command()
@click.argument('input_file', type=click.Path(exists=True))
@click.option('-o', '--output-dir', default='./output',
              help='输出目录（默认 ./output）')
@click.option('-p', '--profile', default='generic', help='配置方案')
@click.option('--api-url', default=None, help='API 地址')
@click.option('--api-key', default=None, help='API Key')
@click.option('--model', default=None, help='模型名')
@click.option('--react/--no-react', default=False, help='ReAct 模式')
@click.option('--split-mode', type=click.Choice(['heading', 'smart', 'deep', 'manual', 'rule', 'none']),
              default=None, help='拆分模式（默认从 config 读取）')
@click.option('--middleware', default=None,
              help='中间件链（逗号分隔，默认 pre_check,similarity）')
@click.option('--review', type=click.Choice(['light', 'full', 'off']),
              default=None, help='内容审查层级（默认 light）')
@click.option('--resume', is_flag=True, default=False, help='断点续传')
@click.option('--yes', is_flag=True, default=False, help='自动跳过确认')
@click.option('--no-pdf', is_flag=True, default=False, help='不生成 PDF')
@click.option('--split-by-pattern', default=None,
              help='自定义正则切分（逃生舱，覆盖 --split-mode）')
def run(input_file, output_dir, profile, api_url, api_key, model,
        react, split_mode, middleware, review, resume, yes, no_pdf, split_by_pattern):
    """一键完整流水线：转换 → 拆分 → 校对 → 排版 → 编译。

    支持断点续传 (--resume)、内容审查 (--review)、中间件链配置 (--middleware)。
    """
    from proseproof.core.logging_utils import log, set_log_func
    set_log_func(lambda msg: click.echo(msg))

    api_url = api_url or os.environ.get('PROSEPROOF_API_URL', '')
    api_key = api_key or os.environ.get('PROSEPROOF_API_KEY', '')
    model = model or os.environ.get('PROSEPROOF_MODEL', '')

    # 加载 profile
    profile_dir = _resolve_profile(profile)
    if not profile_dir:
        raise click.ClickException(f"配置方案不存在: {profile}")
    app = _load_profile(profile_dir)
    if react:
        app.react_mode = True

    # 解析默认值
    if split_mode is None:
        split_mode = app.config.get("split", {}).get("mode", "rule")
    if review is None:
        review = app.config.get("review", {}).get("content", {}).get("mode", "light")
    if middleware is None:
        # 从 config 读取已启用的中间件
        mw_cfg = app.config.get("proofread", {}).get("middleware_chain", [])
        middleware = ",".join(m["name"] for m in mw_cfg if m.get("enabled", True))

    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    ext = os.path.splitext(input_file)[1].lower()

    # ── 阶段 1: 转换 ──
    if ext in ('.docx', '.doc', '.idml', '.zip'):
        md_file = os.path.join(output_dir, base_name + '.md')
        img_dir = os.path.join(output_dir, base_name + '_images')
        log(f"[1/5] 转换: {input_file} → {md_file}")
        from proseproof.core.defaults import default_convert_file_to_md
        default_convert_file_to_md(input_file, md_file, img_dir)
    else:
        md_file = input_file
        log(f"[1/5] 跳过转换（已是 Markdown）")

    # ── 阶段 2: 拆分 ──
    frag_root = os.path.join(output_dir, 'fragments')
    log(f"[2/5] 拆分 (mode={split_mode}): {md_file}")
    split_options = {"split_mode": split_mode}
    if split_by_pattern:
        split_options["split_mode"] = "pattern"
        split_options["split_pattern"] = split_by_pattern
    app.split_document(md_file, frag_root, base_name, split_options)
    frag_base = os.path.join(frag_root, base_name)

    # ── 阶段 3: 结构审查 ──
    if app.config.get("review", {}).get("structural", {}).get("enabled", True):
        log(f"[3/5] 结构审查")
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                md_content = f.read()
            from proseproof.shared.outline_extractor import extract_outline, outline_to_dict
            max_depth = app.config.get("split", {}).get("outline", {}).get("max_depth", 4)
            extra_signals = app.config.get("split", {}).get("outline", {}).get("extra_signals", [])
            outline = extract_outline(md_content, max_depth=max_depth, extra_patterns=extra_signals)
            if outline:
                # 落盘 _outline.json 中间产物
                from proseproof.shared.outline_extractor import save_outline_json
                save_outline_json(md_content, Path(frag_base), max_depth=max_depth)
                from proseproof.shared.structural_review import structural_review, has_severe_issues
                issues = structural_review(outline_to_dict(outline))
                if issues:
                    log(f"   ⚠️ 发现 {len(issues)} 个结构问题")
                    if has_severe_issues(issues) and not yes:
                        click.echo("   ⚠️ 存在严重结构问题，建议检查后再继续。")
                        click.echo("   使用 --yes 可跳过此确认。")
                        if not click.confirm("   是否继续？"):
                            raise click.ClickException("用户取消")
        except Exception as e:
            log(f"   ⚠️ 结构审查异常: {e}")

    # ── 阶段 4: 校对（含中间件链） ──
    log(f"[4/5] 校对（中间件: {middleware or '无'}）")
    from proseproof.core.defaults import default_collect_paper_dirs
    frag_dirs = default_collect_paper_dirs(frag_base)

    if not frag_dirs:
        raise click.ClickException(f"未找到已拆分的片段: {frag_base}")

    # 断点续传: 加载 Manifest
    manifest = None
    if resume:
        from proseproof.shared.manifest import load_manifest, create_manifest, mark_completed, mark_failed, should_skip, save_manifest
        manifest_path = os.path.join(frag_base, ".proofread_manifest.json")
        manifest = load_manifest(Path(manifest_path))
        if manifest is None:
            frag_ids = [os.path.basename(d.rstrip('/\\')) for d in frag_dirs]
            manifest = create_manifest(frag_ids)
        log(f"   📋 断点续传: {manifest_path}")

    total = len(frag_dirs)
    success = 0
    for i, frag_dir in enumerate(frag_dirs):
        frag_name = os.path.basename(frag_dir.rstrip('/\\'))

        # 跳过已完成且未变更的片段
        if resume and manifest:
            target_md = os.path.join(frag_dir, f"{frag_name}.md")
            if os.path.exists(target_md):
                with open(target_md, 'r', encoding='utf-8') as fm:
                    current_content = fm.read()
            else:
                current_content = ""
            if should_skip(manifest, frag_name, current_content):
                log(f"   ⏭️ [{i+1}/{total}] 跳过 {frag_name}（已完成）")
                success += 1
                continue

        log(f"   [{i+1}/{total}] 校对: {frag_name}")
        try:
            result = app.proofread_one(
                api_url, api_key, model, frag_dir, frag_name,
                generate_pdf=False, source_mode="文档",
                middleware=middleware)
            if result.get("success"):
                success += 1
                if resume and manifest:
                    mark_completed(manifest, frag_name, current_content)
                    save_manifest(manifest, Path(manifest_path))
            else:
                log(f"   ❌ {frag_name} 校对失败: {result.get('error', '未知')}")
                if resume and manifest:
                    mark_failed(manifest, frag_name, result.get('error', '未知'))
                    save_manifest(manifest, Path(manifest_path))
                # 严格停止：失败即终止
                raise click.ClickException(f"片段 {frag_name} 校对失败，流水线终止")
        except click.ClickException:
            raise
        except Exception as e:
            log(f"   ❌ {frag_name} 异常: {e}")
            if resume and manifest:
                mark_failed(manifest, frag_name, str(e))
                save_manifest(manifest, Path(manifest_path))
            raise click.ClickException(f"片段 {frag_name} 异常: {e}")

    click.echo(f"   ✅ 校对完成: {success}/{total}")

    # ── 阶段 5: 内容审查 ──
    if review != "off":
        log(f"[5/5] 内容审查 (mode={review})")
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                md_content = f.read()
            from proseproof.shared.outline_extractor import extract_outline, outline_to_dict
            extra_signals = app.config.get("split", {}).get("outline", {}).get("extra_signals", [])
            outline = outline_to_dict(extract_outline(md_content, extra_patterns=extra_signals))

            def _llm_review(prompt_text, system_prompt):
                from proseproof.core.api_client import call_api
                result = call_api(
                    api_url, api_key, model,
                    prompt_text, [], "内容审查",
                    system_prompt, tools=[], max_loops=1,
                )
                return result["content"]

            if review == "full":
                # Full 审查：大纲 + 全文原文
                from proseproof.shared.light_review import FullReview
                full_texts = {}
                for frag_dir in frag_dirs:
                    frag_name = os.path.basename(frag_dir.rstrip('/\\'))
                    target_md = os.path.join(frag_dir, f"{frag_name}.md")
                    if os.path.exists(target_md):
                        with open(target_md, 'r', encoding='utf-8') as f:
                            full_texts[frag_name] = f.read()[:4000]  # 截断避免超长
                if full_texts and outline:
                    reviewer = FullReview(llm_callable=_llm_review)
                    report = reviewer.review(outline, full_texts)
                else:
                    log("   ⏭️ 跳过 Full 审查（无全文）")
                    report = {"issues": []}
            else:
                # Light 审查：大纲 + 校对摘要
                from proseproof.shared.light_review import LightReview
                from proseproof.shared.summary_utils import extract_summary
                summaries = {}
                for frag_dir in frag_dirs:
                    frag_name = os.path.basename(frag_dir.rstrip('/\\'))
                    report_path = os.path.join(frag_dir, "_校对报告.md")
                    if os.path.exists(report_path):
                        with open(report_path, 'r', encoding='utf-8') as f:
                            summary = extract_summary(f.read())
                        if summary:
                            summaries[frag_name] = summary
                if summaries and outline:
                    reviewer = LightReview(llm_callable=_llm_review)
                    report = reviewer.review(outline, summaries)
                else:
                    log("   ⏭️ 跳过 Light 审查（无大纲或无摘要）")
                    report = {"issues": []}

            if report.get("issues"):
                log(f"   📋 发现 {len(report['issues'])} 个内容问题")
                report_path = os.path.join(frag_base, "_review_report.json")
                import json
                with open(report_path, 'w', encoding='utf-8') as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)
                log(f"   📄 审查报告: {report_path}")
            else:
                log(f"   ✅ 内容审查未发现问题")
        except Exception as e:
            log(f"   ⚠️ 内容审查异常: {e}")

    # ── 排版 + 编译 ──
    if not no_pdf:
        pdf_path = os.path.join(output_dir, base_name + '.pdf')
        log(f"     排版 + 编译 → {pdf_path}")
        try:
            from proseproof.shared.latex_generator import generate_combined_pdf
            generate_combined_pdf(frag_base, output_dir)
            if os.path.isfile(pdf_path):
                click.echo(f"   ✅ {pdf_path}")
        except Exception as e:
            log(f"   ⚠️ PDF 编译失败: {e}")

    click.echo(f"\n[完成] 产物目录: {output_dir}")


# ── profile ──

@main.group()
def profile():
    """配置方案管理。"""
    pass


@profile.command(name='list')
def profile_list():
    """列出可用的配置方案。"""
    import proseproof.profiles
    builtin_dir = os.path.dirname(proseproof.profiles.__path__[0])
    builtin = os.path.join(builtin_dir, 'profiles')
    if os.path.isdir(builtin):
        for name in sorted(os.listdir(builtin)):
            cfg = os.path.join(builtin, name, 'config.json')
            has_py = os.path.isfile(os.path.join(builtin, name, 'profile.py'))
            marker = ' [py]' if has_py else ''
            if os.path.isdir(os.path.join(builtin, name)):
                click.echo(f"  {name}{marker}")


@profile.command(name='create')
@click.argument('name')
@click.option('--from', 'template_name', default='generic',
              help='从哪个方案模板创建')
def profile_create(name, template_name):
    """从模板创建新的配置方案。"""
    import proseproof.profiles
    import shutil
    builtin_dir = os.path.dirname(proseproof.profiles.__path__[0])
    src = os.path.join(builtin_dir, 'profiles', template_name)
    if not os.path.isdir(src):
        raise click.ClickException(f"模板方案不存在: {template_name}")

    dst = os.path.join(os.getcwd(), 'profiles', name)
    if os.path.exists(dst):
        raise click.ClickException(f"目录已存在: {dst}")

    os.makedirs(dst, exist_ok=True)
    for f in ['config.json', 'agent_prompt.json']:
        sf = os.path.join(src, f)
        if os.path.isfile(sf):
            shutil.copy(sf, os.path.join(dst, f))
    click.echo(f"[OK] 配置方案已创建: {dst}")
    click.echo("  编辑 config.json 调整提示词，或添加 profile.py 进行深度定制。")


@profile.command(name='show')
@click.argument('name')
def profile_show(name):
    """查看配置方案详情。"""
    profile_dir = _resolve_profile(name)
    if not profile_dir:
        raise click.ClickException(f"配置方案不存在: {name}")

    config_json = os.path.join(profile_dir, 'config.json')
    if os.path.isfile(config_json):
        with open(config_json, 'r', encoding='utf-8') as f:
            import json
            cfg = json.load(f)
            click.echo(json.dumps(cfg, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
