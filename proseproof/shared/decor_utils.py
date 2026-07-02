"""装饰图片清除工具（所有模块通用）。

清除 pandoc 从 Word 转换时产生的无意义装饰图标：
- alt 为 "test" 的小图标（0.19-0.5in，附着在标题行）
- 空 alt + 固定小尺寸的板块标记图标（~1.2×1.5in，如「易错分析」「重点突破」等）
"""

import re

# 装饰图片尺寸阈值（英寸）：宽高均低于此值的视为装饰图标
DECOR_MAX_W = 1.3
DECOR_MAX_H = 1.5

# 匹配空 alt 或 [test] alt 的图片，捕获其宽高尺寸
_DECOR_IMG_RE = re.compile(
    r'!\[(?:test)?\]\([^)]*\)\{width="([\d.]+)in" height="([\d.]+)in"\}'
)


def _is_decor(match: re.Match) -> str:
    """判断匹配到的图片是否为装饰图标。是则返回空串（删除），否则返回原文。"""
    try:
        w = float(match.group(1))
        h = float(match.group(2))
        if w < DECOR_MAX_W and h < DECOR_MAX_H:
            return ""
    except (ValueError, IndexError):
        pass
    return match.group(0)


def strip_decor_images(text: str) -> str:
    """清除文本中的装饰图标，返回清理后的文本。"""
    return _DECOR_IMG_RE.sub(_is_decor, text)


def strip_decor_images_from_file(md_file: str) -> bool:
    """读入文件 → 清除装饰图标 → 写回。返回是否做了修改。"""
    with open(md_file, "r", encoding="utf-8") as f:
        content = f.read()
    new_content = _DECOR_IMG_RE.sub(_is_decor, content)
    if new_content != content:
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    return False
