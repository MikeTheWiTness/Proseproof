"""配置方案基类 —— 提供通用的校对管线骨架。

用户可创建 profile.py 继承 BaseProfile，覆盖提示词、工具集、
校对策略等方法。config.json（必选）定义静态配置，
profile.py（可选）提供 Python 级定制逻辑。

加载机制：
  - 框架先读 config.json 组装默认行为
  - 若 profile.py 存在，通过它覆盖/扩展
"""
import re
import os
from pathlib import Path
from proseproof.core.config_loader import load_config
from proseproof.core.defaults import (
    default_generate_knowledge,
    default_collect_paper_dirs,
    default_split_document,
)
from proseproof.core.manual_split import split_by_manual_markers
from proseproof.core.logging_utils import log
from proseproof.core.middleware import MiddlewareAction
from proseproof.core.strategy import ProofreadStrategy
from proseproof.shared.image_utils import copy_md_images


def _split_by_regex(content: str, pattern) -> list:
    """按正则匹配行切分文档。每次匹配到 pattern 的行作为新片段的起点。"""
    lines = content.split('\n')
    fragments = []
    current = []
    for line in lines:
        if pattern.search(line) and current:
            fragments.append({"content": '\n'.join(current).strip()})
            current = [line]
        else:
            current.append(line)
    if current:
        fragments.append({"content": '\n'.join(current).strip()})
    return fragments if fragments else [{"content": content}]


def _save_outline_if_needed(content: str, output_root: str,
                            base_name: str, config: dict):
    """在 split 阶段保存 _outline.json 中间产物。"""
    try:
        from proseproof.shared.outline_extractor import extract_outline, save_outline_json
        outline_cfg = config.get("split", {}).get("outline", {})
        max_depth = outline_cfg.get("max_depth", 4)
        extra_signals = outline_cfg.get("extra_signals", [])
        outline = extract_outline(content, max_depth=max_depth,
                                  extra_patterns=extra_signals)
        if outline:
            target = Path(output_root) / base_name
            save_outline_json(content, target, max_depth=max_depth,
                              extra_patterns=extra_signals)
    except Exception as e:
        log(f"   ⚠️ 大纲保存失败: {e}")


class BaseProfile:
    """配置方案基类。

    子类可自由覆盖以下方法：
      - build_tools()          → 注册自定义工具集
      - get_tool_instructions() → 工具使用说明注入 prompt
      - get_max_tool_loops()   → ReAct 工具循环上限
      - get_proofread_prompt() → 校对提示词
      - get_segment_prompt()   → 段落提取提示词
      - get_review_prompt()    → 批注评审提示词
      - split_document()       → 文档拆分逻辑
      - proofread_one()        → 校对主流程
      - register_middleware()  → 注册自定义中间件（v0.2.0）
      - _build_pre_hook()      → 校对前置钩子（如原文检索）
    """

    # ---- 子类可覆盖的类属性 ----
    name: str = "generic"
    version: str = "1.0"

    _show_segment_option: bool = True
    _clean_bold_replacement: str = "\x01"

    def __init__(self, profile_dir: str):
        self.profile_dir = profile_dir
        self.config = load_config(profile_dir)
        self._react_mode = False
        self.tools = self.build_tools()

    # ---- react_mode 属性 ----

    @property
    def react_mode(self) -> bool:
        """是否启用 ReAct 模式（工具循环）。"""
        return self._react_mode

    @react_mode.setter
    def react_mode(self, value: bool):
        self._react_mode = value
        self.tools = self.build_tools()

    # ---- 子类可覆盖的方法（均有默认实现，从 config 读取） ----

    def build_tools(self):
        """构建工具集。默认无工具，子类可覆盖注册自定义工具。"""
        return []

    def get_max_tool_loops(self) -> int:
        """获取 ReAct 工具调用最大循环次数。默认 0（无工具循环）。"""
        return 0

    def get_tool_instructions(self) -> str:
        """获取工具使用说明文本。默认空字符串。"""
        return ""

    def get_proofread_prompt(self) -> str:
        """获取校对提示词。默认从 config.json 的 question_prompt_lines 读取。"""
        from proseproof.core.config_loader import get_question_prompt
        return get_question_prompt(self.config)

    def get_segment_prompt(self) -> str:
        """获取段落提取提示词。默认回退到校对提示词。"""
        return self.get_proofread_prompt()

    def get_review_prompt(self) -> str:
        """获取批注评审提示词。优先从 config.json 的 review_prompt_lines 读取，
        无则回退到 question_prompt_lines。"""
        review_lines = self.config.get("review_prompt_lines")
        if review_lines:
            if isinstance(review_lines, list):
                return "\n".join(review_lines)
            return review_lines
        return self.get_proofread_prompt()

    # ---- 校对主流程（模板方法） ----

    def proofread_one(self, api_url: str, api_key: str, model: str,
                       q_dir: str, q_name: str,
                       is_segment: bool = False,
                       generate_pdf: bool = True,
                       source_mode: str = "文档",
                       middleware: str | None = None,
                       strategy=None) -> dict:
        """校对单个片段 —— 通过中间件链驱动。

        Args:
            middleware: 可选，逗号分隔的中间件名列表，覆盖 config.json。
                        None 时从 config 读取默认值。
            strategy:   可选，ProofreadStrategy 实例。提供时使用该策略替代
                        默认的 proofread_with_middleware 路径。
                        子类可通过覆盖 get_proofread_strategy() 注入自定义策略。
        """
        if is_segment:
            prompt = self.get_segment_prompt()
        elif source_mode == "批注评审":
            prompt = self.get_review_prompt()
        else:
            prompt = self.get_proofread_prompt()

        pre_hook = self._build_pre_hook(api_url, api_key, model, q_dir)

        # 读取片段原文
        target_md = os.path.join(q_dir, f"{q_name}.md")
        md_content = ""
        if os.path.exists(target_md):
            with open(target_md, 'r', encoding='utf-8') as fm:
                md_content = fm.read()
        if not md_content:
            return {"success": False, "result": "", "error": "未找到 md 文件"}

        # 前置处理 hook
        if pre_hook:
            try:
                md_content = pre_hook(md_content)
            except Exception as e:
                log(f"   ⚠️ 前置处理异常：{e}")

        # 构建 ProofreadContext
        from proseproof.core.middleware import ProofreadContext
        ctx = ProofreadContext(
            fragment_text=md_content,
            fragment_id=q_name,
            images=[],
            prompt=prompt,
            tools=self.tools,
            config=self.config,
        )

        # 选择校对路径：注入的策略 > 默认中间件链
        if strategy is not None:
            log(f"   🔀 [proofread_one] 使用注入策略: {type(strategy).__name__}")
            result = strategy.proofread(ctx)
        else:
            from proseproof.core.proofread_middleware import proofread_with_middleware
            result = proofread_with_middleware(
                ctx=ctx,
                api_url=api_url, api_key=api_key, model=model,
                output_dir=q_dir,
                generate_pdf=generate_pdf,
                react_mode=self.react_mode,
                middleware_override=middleware,
            )

        # 转换为向后兼容的 dict 格式
        if result.action == MiddlewareAction.ABORT:
            return {"success": False, "result": "", "error": result.message,
                    "tool_calls": ctx.tool_calls_log}
        if "API调用失败" in ctx.raw_response:
            return {"success": False, "result": "", "error": ctx.raw_response,
                    "tool_calls": ctx.tool_calls_log}
        return {"success": True, "result": ctx.raw_response,
                "tool_calls": ctx.tool_calls_log, "error": None}

    def get_proofread_strategy(self, api_url: str = "", api_key: str = "",
                                model: str = "", react_mode: bool = False):
        """获取当前的校对策略实例（实现 ProofreadStrategy 协议）。

        v0.2.0 默认返回 DefaultProofreadStrategy。
        子类可覆盖此方法以提供自定义策略。
        """
        from proseproof.core.proofread_middleware import DefaultProofreadStrategy
        return DefaultProofreadStrategy(
            api_url=api_url, api_key=api_key, model=model,
            react_mode=react_mode,
        )

    def _build_pre_hook(self, api_url: str, api_key: str, model: str,
                         q_dir: str):
        """构建校对前置钩子。默认返回 None，子类可覆盖。"""
        return None

    # ---- 中间件注册（v0.2.0） ----

    def register_middleware(self) -> dict:
        """注册自定义中间件。

        返回一个 dict，key 为中间件名（对应 config.json 中
        middleware_chain 的 name 字段），value 为实现 ProofreadMiddleware
        协议的实例。

        v0.2.0 内置中间件（pre_check、similarity）由框架自动注册，
        此方法仅用于第三方扩展。

        子类覆盖示例:
            from my_package import CustomMiddleware
            return {**super().register_middleware(), "custom": CustomMiddleware()}
        """
        return {}

    # ---- 零差异方法 ----

    def generate_knowledge(self, md_file: str, output_root: str,
                            base_name: str):
        """知识提取 —— 委托给默认实现。"""
        return default_generate_knowledge(md_file, output_root, base_name,
                                           self.config)

    def collect_dirs(self, base_path: str) -> list:
        """收集片段目录列表。"""
        return default_collect_paper_dirs(base_path)

    # ---- 文档拆分 ----

    def split_document(self, md_file: str, output_root: str,
                        base_name: str, options: dict = None) -> bool:
        """文档拆分为片段 —— 支持 heading/smart/deep/manual/rule/none 模式。

        - heading: 按 Markdown 标题拆分（零成本）
        - smart:   LLM 大纲驱动拆分（极低成本，Slice #4 实现）
        - deep:    LLM 全文拆分（高成本兜底）
        - manual:  按 `###### 片段开始/结束 ######` 标记拆分
        - rule:    按正则拆分
        - none:    整个文档作为一个片段
        """
        if options is None:
            options = {}
        split_mode = options.get("split_mode", "rule")

        # rule 模式：无需读全文，直接委托
        if split_mode == "rule":
            return default_split_document(md_file, output_root, base_name,
                                           self.config)

        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        if split_mode == "none":
            fragments = [{"content": md_content}]
        elif split_mode == "manual":
            fragments = split_by_manual_markers(md_content)
        elif split_mode == "pattern":
            split_pattern = options.get("split_pattern", "")
            if not split_pattern:
                log("[WARN] pattern 模式未提供 split_pattern，回退到 rule")
                return default_split_document(md_file, output_root, base_name,
                                               self.config)
            try:
                compiled = re.compile(split_pattern)
                fragments = _split_by_regex(md_content, compiled)
            except re.error as e:
                log(f"[ERROR] 正则无效: {e}")
                return False
        elif split_mode == "heading":
            from proseproof.shared.heading_split import HeadingSplitStrategy
            strategy = HeadingSplitStrategy()
            fragments = strategy.split(md_content, self.config)
            _save_outline_if_needed(md_content, output_root, base_name, self.config)
        elif split_mode == "smart":
            api_url = options.get("api_url", "")
            api_key = options.get("api_key", "")
            model = options.get("model", "")
            from proseproof.shared.smart_split_v2 import SmartSplitStrategy

            def _llm_call(text, prompt):
                from proseproof.core.api_client import call_api
                result = call_api(
                    api_url, api_key, model,
                    text, [], "smart分割",
                    prompt, tools=[], max_loops=1,
                )
                return result["content"]

            strategy = SmartSplitStrategy(llm_callable=_llm_call)
            fragments = strategy.split(md_content, self.config)
            _save_outline_if_needed(md_content, output_root, base_name, self.config)
        elif split_mode == "deep":
            from proseproof.shared.smart_split import DeepSplitStrategy
            api_url = options.get("api_url", "")
            api_key = options.get("api_key", "")
            model = options.get("model", "")
            strategy = DeepSplitStrategy(
                api_url=api_url, api_key=api_key, model=model,
                md_file=md_file,
            )
            fragments = strategy.split(md_content, self.config)
        else:
            log(f"[WARN] 未知拆分模式: {split_mode}，回退到 rule 模式")
            return default_split_document(md_file, output_root, base_name,
                                           self.config)

        return self._write_fragments_to_dirs(md_file, output_root, base_name,
                                              fragments)

    def _write_fragments_to_dirs(self, md_file: str, output_root: str,
                                   base_name: str,
                                   fragments: list) -> bool:
        """将拆分后的片段写入目录，含图片复制。"""
        if not fragments:
            log("[WARN] 没有片段可写入")
            return False

        md_dir = Path(md_file).parent
        src_media = md_dir / f"{base_name}_images" / "media"
        target_root = Path(output_root) / base_name
        target_root.mkdir(parents=True, exist_ok=True)

        for idx, frag in enumerate(fragments, start=1):
            content = frag.get("content", "")
            frag_dir_name = f"frag_{idx:03d}"
            frag_dir = target_root / frag_dir_name
            frag_dir.mkdir(exist_ok=True)
            img_dir = frag_dir / "images"
            img_dir.mkdir(exist_ok=True)

            img_result = copy_md_images(content, [src_media, md_dir], img_dir)
            new_content = img_result.content

            (frag_dir / f"{frag_dir_name}.md").write_text(
                new_content, encoding='utf-8')

            # 生成纯文本版
            try:
                from proseproof.shared.docx_format_enhancer import \
                    strip_format_markers
                clean = strip_format_markers(new_content)
                clean = re.sub(
                    r'<批注\s+id=\d+>.*?</批注>', '', clean,
                    flags=re.DOTALL)
                repl = self._clean_bold_replacement
                clean = re.sub(r'\*\*([^*]+)\*\*', repl, clean)
                clean = re.sub(r'__([^_]+)__', repl, clean)
                (frag_dir / f"{frag_dir_name}_clean.md").write_text(
                    clean, encoding='utf-8')
            except Exception:
                pass

        # skip_sections: 按配置丢弃匹配的板块
        skip_patterns = self.config.get("lecture_split", {}).get("skip_sections", [])
        if skip_patterns and target_root.exists():
            import shutil
            for sub_dir in sorted(target_root.iterdir()):
                if not sub_dir.is_dir():
                    continue
                md_files = list(sub_dir.glob("*.md"))
                if not md_files:
                    continue
                try:
                    first_line = md_files[0].read_text(encoding="utf-8").split("\n")[0].strip()
                except Exception:
                    continue
                for pat in skip_patterns:
                    if re.search(pat, first_line):
                        shutil.rmtree(sub_dir)
                        log(f"   🗑️ 跳过板块: {sub_dir.name}（匹配 {pat}）")
                        break

        log(f"[OK] 拆分完成: {len(fragments)} 个片段")
        return True
