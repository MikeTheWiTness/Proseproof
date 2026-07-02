"""格式审查二级制：程序初筛 + LLM bash 直接修改文件。

流程：
1. _enforce_format() 程序检查格式
2. 若格式不合规，将原始输出写入 _校对报告.md
3. _bash_format_fix() 让 LLM 直接用 bash 编辑该文件
4. Python 端重读文件并重新验证
"""
import os
import re
from proseproof.core.logging_utils import log


def _is_no_issue(res: str) -> bool:
    if not res:
        return False
    stripped = res.strip()
    if stripped == "无问题":
        return True
    if stripped.startswith("无问题") and len(stripped) <= 10:
        return True
    return False


def _enforce_format(res: str):
    if _is_no_issue(res):
        return True, ""
    issues = []
    # 注意：标记原文中可能包含内部 ### 标题（如前置参考的"### 权威原文"），
    # 不能以任意 ### 作为结束边界，必须以 ### 修改原因 作为精确边界。
    # 使用 [^\n]* 而非 \s* 允许标题行上有额外描述文字（如 "### 标记原文 段落"），
    # 避免因提示词歧义导致 LLM 在标题后多加文字而匹配失败。
    marker_match = re.search(r'###\s*标记原文[^\n]*\n(.*?)(?=\n###\s*修改原因|\Z)', res, re.DOTALL)
    reason_match = re.search(r'###\s*修改原因[^\n]*\n(.*?)(?=\n###\s*修改|\Z)', res, re.DOTALL)
    # 内联标记检测：只要存在 【N|原文|改为】 标记即视为内联格式
    has_inline_markers = bool(re.search(r'【\d+\|.*\|[^】]*】', res))
    if not marker_match and not has_inline_markers:
        issues.append("缺少 ### 标记原文 段落 且无内联标记")
    if not reason_match:
        issues.append("缺少 ### 修改原因 段落")
    # 确定标记所在的文本区域（优先用 marker_match，其次全文）
    marker_text = marker_match.group(1) if marker_match else (res if has_inline_markers else "")
    if marker_text and reason_match:
        markers = re.findall(r'【(\d+)\|', marker_text)
        marker_nums = set(int(m) for m in markers)
        reason_nums = set()
        for line in reason_match.group(1).split('\n'):
            m = re.match(r'^(\d+)\.\s', line.strip())
            if m:
                reason_nums.add(int(m.group(1)))
        missing = marker_nums - reason_nums
        extra = reason_nums - marker_nums
        if missing:
            issues.append(f"标记编号 {sorted(missing)} 在修改原因中缺少对应条目")
        if extra:
            issues.append(f"修改原因编号 {sorted(extra)} 没有对应的标记")
    if marker_text:
        # 只检测【数字 开头但后面紧跟的不是 | 或数字（防止 \d+ 回溯
        # 把 【13|...】 中的 【1 误判为异常）。【试题答案】等中文括号不受影响。
        malformed = re.findall(r'【\d+(?![|\d])', marker_text)
        if malformed:
            issues.append(f"发现 {len(malformed)} 个格式异常的标记（编号后缺少 |）")
    if issues:
        return False, "; ".join(issues)
    return True, ""


def _bash_format_fix(file_path: str, issues_desc: str,
                     api_url: str, api_key: str, model: str) -> str | None:
    """让 LLM 通过 bash 直接编辑文件来修正格式问题。

    与旧版 _llm_format_fix 的区别：
    - 旧版：LLM 返回修正后文本 → Python 端验证 → 可能因格式不匹配被丢弃
    - 新版：LLM 用 bash 直接修改文件 → Python 端重读验证 → 更可靠

    Args:
        file_path: 待修正的文件路径（_校对报告.md）
        issues_desc: _enforce_format 返回的问题描述
        api_url / api_key / model: API 配置

    Returns:
        修正后的文件内容（str），失败时返回 None
    """
    from proseproof.shared.bash_tool import BashTool, FileReadTool, FileWriteTool
    from proseproof.core.api_client import call_api

    file_dir = os.path.dirname(os.path.abspath(file_path))
    file_name = os.path.basename(file_path)

    system_prompt = """你是一个格式修正助手。用户会告诉你一个文件的格式问题，你需要直接编辑该文件，使其符合规范格式。

## 规范格式要求

文件必须包含两个段落（以 ### 标题分隔）：

1. `### 标记原文` — 完整抄写原文，在错误处用 `【编号|原文|改为】` 标记。编号用阿拉伯数字 1、2、3……
2. `### 修改原因` — 每个编号一条原因，格式为 `编号. 原因说明`。编号必须与标记原文中的编号一一对应。

## 操作方式

1. 先用 **read_file** 工具读取文件当前内容
2. 修正格式后，用 **write_file** 工具写回文件
3. 修改完成后再次 read_file 验证结果

## 重要规则

- **不要改变任何校对结论**，只修正格式结构
- 标题必须是 `### 标记原文` 和 `### 修改原因`（不是 `### 标记原文 段落`）
- 如果原文有总结行（如"一般问题"），保留它，在其后加入 `### 标记原文` 段落
- 如果没有 `### 标记原文` 标题，在正文内容前加上它
- **完成后直接停止，不要继续调用工具！** 只需：read → 修改 → write → read 验证 → 停止。总共不超过 3 轮工具调用。
"""

    user_message = (
        f"文件 `{file_name}` 存在以下格式问题：\n\n"
        f"{issues_desc}\n\n"
        f"文件路径：`{file_path}`\n\n"
        "请用 read_file 读取 → 修正格式 → write_file 写回。"
        "保留所有校对结论，只调整格式结构。"
    )

    read_tool = FileReadTool()
    write_tool = FileWriteTool()
    bash_tool = BashTool(allowed_dir=file_dir)

    try:
        log(f"   🔧 [bash修正] 启动 LLM 直接编辑文件...")
        result = call_api(
            api_url=api_url,
            api_key=api_key,
            model=model,
            md_text=user_message,
            images=[],
            q_title="格式修正",
            system_prompt=system_prompt,
            tools=[read_tool, write_tool, bash_tool],
            max_loops=3,          # 格式修正只需 read→write→read 三轮
            max_tokens=16384,      # 格式修正不需要太长输出
            output_dir=file_dir,
        )
    except Exception as e:
        log(f"   ❌ [bash修正] API 调用异常: {e}")
        return None

    # 从文件重读修正后的内容
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            fixed_content = f.read()
        log(f"   📥 [bash修正] 文件已重读，长度 {len(fixed_content)} 字符")
        return fixed_content
    except Exception as e:
        log(f"   ❌ [bash修正] 读取文件失败: {e}")
        return None


def enforce_and_fix(file_path: str, res: str, api_url: str, api_key: str,
                    model: str) -> tuple[str, bool, str]:
    """格式审查 + bash 修正（新版：LLM 直接编辑文件）。

    Args:
        file_path: _校对报告.md 的路径（原始内容已写入）
        res: 原始 LLM 输出的文本内容
        api_url / api_key / model: API 配置

    Returns:
        (final_content, was_fixed, issues_desc)
        - final_content: 最终内容（修正后或原始）
        - was_fixed: 是否成功修正
        - issues_desc: 格式问题描述（无问题时为空字符串）
    """
    ok, issues = _enforce_format(res)
    if ok:
        return res, False, ""

    fixed = _bash_format_fix(file_path, issues, api_url, api_key, model)
    if fixed and _enforce_format(fixed)[0]:
        log("   ✅ bash 格式修正成功")
        return fixed, True, issues

    log("   ⚠️ bash 格式修正失败，使用原始输出")
    return res, False, issues
