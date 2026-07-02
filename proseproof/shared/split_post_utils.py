"""拆分后处理工具（section 模式通用）。

用于在 default_split_lecture 完成后清理输出目录中的无效板块。
"""

import shutil
from pathlib import Path

# 默认的导航/封面板块标题匹配模式
DEFAULT_NAV_PATTERNS = [r"直击课堂", r"本讲导航"]


def remove_navigation_units(output_root: str, base_name: str,
                            patterns: list[str] | None = None) -> int:
    """删除拆分结果中匹配导航/封面模式的板块目录。

    Args:
        output_root: 输出根目录
        base_name: 文档基础名称（子目录名）
        patterns: 正则模式列表，匹配板块标题首行。默认匹配直击课堂/本讲导航。

    Returns:
        删除的板块数量
    """
    import re

    if patterns is None:
        patterns = DEFAULT_NAV_PATTERNS

    nav_re = re.compile("|".join(patterns))
    target_dir = Path(output_root) / base_name
    if not target_dir.exists():
        return 0

    removed = 0
    for sub_dir in sorted(target_dir.iterdir()):
        if not sub_dir.is_dir():
            continue
        md_files = list(sub_dir.glob("*.md"))
        if not md_files:
            continue
        try:
            first_line = md_files[0].read_text(encoding="utf-8").split("\n")[0].strip()
        except Exception:
            continue
        if nav_re.search(first_line):
            shutil.rmtree(sub_dir)
            removed += 1

    return removed
