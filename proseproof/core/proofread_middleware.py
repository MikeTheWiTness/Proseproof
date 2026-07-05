"""v0.2.0 中间件链包装 + DefaultProofreadStrategy。

本模块提供:
  - proofread_with_middleware(): 中间件链驱动的校对包装函数
  - DefaultProofreadStrategy: 实现 ProofreadStrategy 协议

设计决策见 ADR-0011（中间件链闭环）。
"""
from __future__ import annotations
import os
import base64
from pathlib import Path
from proseproof.core.middleware import (
    ProofreadContext, MiddlewareAction, MiddlewareResult, ProofreadMiddleware,
)
from proseproof.core.middleware_runner import run_middleware_chain, FragmentAbortedError
from proseproof.core.api_client import call_api, MAX_FILE_SIZE
from proseproof.core.logging_utils import log
from proseproof.core.parsing import save_proofread_json, _is_no_issue


def _resolve_middleware_chain(config: dict) -> list:
    """根据 config 的 middleware_chain 字段构建中间件实例列表。

    从 proofread.middleware_chain 读取已启用（enabled: true）的中间件名称，
    匹配内置中间件（pre_check、similarity）并实例化。

    第三方中间件可通过 profile.register_middleware() 注册。
    """
    chain_config = config.get("proofread", {}).get("middleware_chain", [])
    if not chain_config:
        return []

    builtin_registry = {
        "pre_check": lambda: _import_builtin("pre_check", "PreCheckMiddleware"),
        "similarity": lambda: _import_builtin("similarity", "SimilarityMiddleware"),
    }

    chain = []
    for item in chain_config:
        name = item.get("name", "")
        enabled = item.get("enabled", True)
        if not enabled:
            continue
        if name in builtin_registry:
            try:
                chain.append(builtin_registry[name]())
            except Exception as e:
                log(f"   ⚠️ 中间件 [{name}] 加载失败: {e}")
        else:
            log(f"   ⚠️ 未知内置中间件: {name}（已跳过）")
    return chain


def _resolve_middleware_chain_from_names(names: list) -> list:
    """根据名称列表构建中间件实例（用于 CLI --middleware override）。"""
    return _resolve_middleware_chain(
        {"proofread": {"middleware_chain": [
            {"name": n.strip(), "enabled": True} for n in names if n.strip()
        ]}}
    )


def _import_builtin(module_name: str, class_name: str):
    """动态加载内置中间件。"""
    if module_name == "pre_check":
        from proseproof.shared.pre_check import PreCheckMiddleware
        return PreCheckMiddleware()
    if module_name == "similarity":
        from proseproof.shared.similarity import SimilarityMiddleware
        return SimilarityMiddleware()
    raise ImportError(f"未知内置中间件模块: {module_name}")


from proseproof.shared.report_utils import format_tool_calls_summary, format_usage_summary


def proofread_with_middleware(
    ctx: ProofreadContext,
    api_url: str, api_key: str, model: str,
    output_dir: str, generate_pdf: bool = True,
    react_mode: bool = False,
    middleware_override: str | None = None,
) -> MiddlewareResult:
    """中间件链驱动的校对包装函数。

    流程:
      1. 构建中间件链
      2. 执行 pre 中间件 → 可能注入提示、跳过 LLM
      3. 调用 LLM（call_api）
      4. 执行 post 中间件 → 结构校验
      5. 格式审查 + 保存文件

    Args:
        ctx:          校对上下文（含 fragment_text / prompt / tools 等）
        api_url:      API 端点
        api_key:      API 密钥
        model:        模型名
        output_dir:   输出目录
        generate_pdf: 是否生成 PDF
        react_mode:   是否 ReAct 模式

    Returns:
        MiddlewareResult，含最终 context 和 action。
    """
    # Step 1: 构建中间件链（CLI override > config）
    if middleware_override:
        chain = _resolve_middleware_chain_from_names(middleware_override.split(","))
    else:
        chain = _resolve_middleware_chain(ctx.config)

    # Step 2: pre 中间件
    try:
        ctx = run_middleware_chain(ctx, chain)
    except FragmentAbortedError as e:
        log(f"   🛑 [proofread] 片段中止: {e}")
        result = MiddlewareResult(ctx, MiddlewareAction.ABORT, str(e))
        return result

    # Step 3: LLM 调用（如果未被 skip）
    if not ctx.skip_llm:
        # 加载图片
        images_b64 = []
        img_dir = os.path.join(output_dir, "images")
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

        # ReAct 模式: 注入 API 配置
        if react_mode:
            try:
                from proseproof.shared.physics_tools import set_physics_api_config
                set_physics_api_config(api_url, api_key, model, output_dir=output_dir)
            except ImportError:
                pass
            try:
                from proseproof.shared.chemistry_tools import set_chemistry_api_config
                set_chemistry_api_config(api_url, api_key, model, output_dir=output_dir)
            except ImportError:
                pass

    # Step 3-4: LLM 调用 + post 验证（支持最多 2 次 RECHECK）
    max_rechecks = 2
    for recheck_round in range(max_rechecks + 1):
        if recheck_round > 0:
            log(f"   🔄 [proofread] post 中间件要求重试 ({recheck_round}/{max_rechecks})")
            ctx.reject_result = False

        # LLM 调用（如果未被 skip）
        if not ctx.skip_llm:
            try:
                result = call_api(
                    api_url, api_key, model,
                    ctx.fragment_text, images_b64,
                    ctx.fragment_id, ctx.prompt,
                    tools=ctx.tools, max_loops=20,
                    output_dir=output_dir,
                )
                ctx.raw_response = result["content"]
                ctx.tool_calls_log = result.get("tool_calls_log", [])
                ctx.reasoning = result.get("reasoning", "")
                ctx.usage = result.get("usage", {})
                log(f"   📥 LLM 最终返回: {ctx.raw_response[:150].replace(chr(10), ' ')}...")
            except Exception as e:
                log(f"   ❌ LLM 调用失败: {e}")
                return MiddlewareResult(
                    ctx, MiddlewareAction.ABORT,
                    f"LLM 调用失败: {e}"
                )

        # post 中间件（即使 skip_llm 也执行）
        try:
            ctx = run_middleware_chain(ctx, chain)
        except FragmentAbortedError as e:
            log(f"   🛑 [proofread] post 阶段中止: {e}")
            break

        # post RECHECK 消费：相似度不匹配时要求重新校对
        if not ctx.reject_result:
            break  # post 通过，退出循环

    # Step 5: 格式审查 + 保存文件
    if ctx.raw_response and "API调用失败" not in ctx.raw_response:
        from proseproof.core.format_enforcement import _enforce_format, enforce_and_fix
        format_ok, format_issues = _enforce_format(ctx.raw_response)
        if not format_ok and generate_pdf:
            md_path = os.path.join(output_dir, "_校对报告.md")
            try:
                os.makedirs(output_dir, exist_ok=True)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(ctx.raw_response)
            except Exception as e:
                log(f"   ⚠️ [proofread] 格式审查阶段 _校对报告.md 写入失败: {e}")
            log(f"   ⚠️ 格式不合规：{format_issues}")
            ctx.raw_response, _, _ = enforce_and_fix(
                md_path, ctx.raw_response, api_url, api_key, model)
        elif not format_ok:
            log(f"   ⚠️ 格式不合规：{format_issues}（跳过修正）")

        # 保存 _校对报告.md（核心中间产物，始终产出）
        md_path = os.path.join(output_dir, "_校对报告.md")
        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("> 完整 API 对话记录请见 `_API对话记录.md`\n\n---\n\n")
                f.write(ctx.raw_response)
                if ctx.tool_calls_log:
                    f.write(format_tool_calls_summary(ctx.tool_calls_log))
                if _is_no_issue(ctx.raw_response) and ctx.reasoning:
                    f.write("\n\n---\n")
                    f.write("## 📋 模型思考过程（仅核查用，不出现在 PDF 中）\n\n")
                    f.write(ctx.reasoning)
                usage_text = format_usage_summary(ctx.usage)
                if usage_text:
                    f.write(usage_text)
        except Exception as e:
            log(f"   ⚠️ [proofread] _校对报告.md 写入失败: {e}")

        # 保存校对数据 JSON（核心中间产物，始终产出）
        try:
            saved = save_proofread_json(ctx.raw_response, output_dir,
                                        tool_calls=ctx.tool_calls_log)
            if saved:
                log(f"   📊 [proofread] _校对数据.json 已保存")
            else:
                log(f"   ⚠️ [proofread] 校对数据解析为空，未生成 JSON")
        except Exception as e:
            log(f"   ⚠️ [proofread] _校对数据.json 保存失败: {e}")

        # 同步存档到 output/中间产物（仅 generate_pdf 模式）
        if generate_pdf:
            try:
                from pathlib import Path as _Path
                import shutil
                q_dir_path = _Path(output_dir)
                doc_name = q_dir_path.parent.name
                q_name_clean = q_dir_path.name
                artifact_dir = _Path("output") / "中间产物" / doc_name / q_name_clean
                artifact_dir.mkdir(parents=True, exist_ok=True)
                artifact_path = artifact_dir / "_校对报告.md"
                with open(artifact_path, "w", encoding="utf-8") as f:
                    f.write("> 完整 API 对话记录请见 `_API对话记录.md`\n\n---\n\n")
                    f.write(ctx.raw_response)
                src_json = os.path.join(output_dir, "_校对数据.json")
                if os.path.exists(src_json):
                    shutil.copy2(src_json, artifact_dir / "_校对数据.json")
                src_api_log = os.path.join(output_dir, "_API对话记录.md")
                if os.path.exists(src_api_log):
                    shutil.copy2(src_api_log, artifact_dir / "_API对话记录.md")
            except Exception as e:
                log(f"   ⚠️ [proofread] 中间产物同步失败: {e}")

    return MiddlewareResult(ctx, MiddlewareAction.CONTINUE)


class DefaultProofreadStrategy:
    """默认校对策略 —— 实现 ProofreadStrategy 协议。

    使用 proofread_with_middleware() 作为核心实现。
    """

    def __init__(self, api_url: str = "", api_key: str = "",
                 model: str = "", react_mode: bool = False):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.react_mode = react_mode

    def proofread(self, ctx: ProofreadContext) -> MiddlewareResult:
        return proofread_with_middleware(
            ctx=ctx,
            api_url=self.api_url,
            api_key=self.api_key,
            model=self.model,
            output_dir=f"./output/{ctx.fragment_id}",
            generate_pdf=True,
            react_mode=self.react_mode,
        )
