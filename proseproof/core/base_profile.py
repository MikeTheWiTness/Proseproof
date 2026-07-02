"""配置方案基类 —— 提供通用的校对管线骨架。

用户可创建 profile.py 继承 BaseProfile，覆盖提示词、工具集、
校对策略等方法。config.json（必选）定义静态配置，
profile.py（可选）提供 Python 级定制逻辑。

加载机制：
  - 框架先读 config.json 组装默认行为
  - 若 profile.py 存在，通过它覆盖/扩展
"""
import re
from pathlib import Path
from proseproof.core.config_loader import load_config
from proseproof.core.defaults import (
    default_generate_knowledge,
    default_collect_paper_dirs,
    default_split_document,
    default_proofread_one,
)
from proseproof.core.manual_split import split_by_manual_markers
from proseproof.core.logging_utils import log
from proseproof.shared.image_utils import copy_md_images


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

    # ---- 子类必须实现的方法 ----

    def build_tools(self):
        """构建工具集。子类必须实现。"""
        raise NotImplementedError

    def get_max_tool_loops(self) -> int:
        """获取 ReAct 工具调用最大循环次数。"""
        raise NotImplementedError

    def get_tool_instructions(self) -> str:
        """获取工具使用说明文本。"""
        raise NotImplementedError

    def get_proofread_prompt(self) -> str:
        """获取校对提示词。"""
        raise NotImplementedError

    def get_segment_prompt(self) -> str:
        """获取段落提取提示词（知识/内容提取场景）。"""
        raise NotImplementedError

    def get_review_prompt(self) -> str:
        """获取批注评审提示词。"""
        raise NotImplementedError

    # ---- 校对主流程（模板方法） ----

    def proofread_one(self, api_url: str, api_key: str, model: str,
                       q_dir: str, q_name: str,
                       is_segment: bool = False,
                       generate_pdf: bool = True,
                       source_mode: str = "文档") -> dict:
        """校对单个片段 —— 模板方法骨架。

        子类可覆盖 _build_pre_hook() 注入前置处理逻辑。
        """
        if is_segment:
            prompt = self.get_segment_prompt()
        elif source_mode == "批注评审":
            prompt = self.get_review_prompt()
        else:
            prompt = self.get_proofread_prompt()

        pre_hook = self._build_pre_hook(api_url, api_key, model, q_dir)

        return default_proofread_one(
            api_url, api_key, model, q_dir, q_name, is_segment,
            prompt, self.tools, self.get_max_tool_loops(), generate_pdf,
            pre_hook=pre_hook,
            react_mode=self.react_mode,
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
        elif split_mode == "heading":
            from proseproof.shared.heading_split import HeadingSplitStrategy
            strategy = HeadingSplitStrategy()
            fragments = strategy.split(md_content, self.config)
        elif split_mode == "smart":
            # Slice #4 实现：大纲驱动 LLM 切分
            raise NotImplementedError(
                "smart 模式将在 Slice #4 中实现。"
                "当前可用：heading、deep、manual、rule、none。"
            )
        elif split_mode == "deep":
            api_url = options.get("api_url", "")
            api_key = options.get("api_key", "")
            model = options.get("model", "")
            from proseproof.shared.smart_split import smart_split
            fragments = smart_split(md_content, api_url, api_key, model,
                                     md_file=md_file)
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

        log(f"[OK] 拆分完成: {len(fragments)} 个片段")
        return True
