"""共享图片工具 —— 统一的 Markdown 图片复制逻辑。

消除 7 个 subject.py + 3 处 core/defaults.py 中的重复图片处理代码。
"""
import re
import shutil
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ImageCopyResult:
    """图片复制结果。"""
    content: str      # 重写路径后的 Markdown 内容
    copied: int = 0   # 成功复制的图片数
    missing: int = 0  # 未找到的图片数


def copy_md_images(
    md_content: str,
    src_dirs: list[Path],
    target_img_dir: Path,
    *,
    relative_img_path: str = "./images",
) -> ImageCopyResult:
    """将 Markdown 内容中引用的本地图片复制到目标目录，重写路径。

    遍历 `md_content` 中的所有 `![...](...)` 图片引用：
    - HTTP/HTTPS 图片 → 跳过，保留原引用
    - 本地图片 → 在 `src_dirs` 中依次查找，找到后复制到 `target_img_dir`，
      并将路径重写为 `{relative_img_path}/{filename}`

    Args:
        md_content: 包含图片引用的 Markdown 文本
        src_dirs: 图片源目录列表（按优先级搜索）
        target_img_dir: 目标图片目录（自动创建）
        relative_img_path: 重写后的相对路径前缀，默认 "./images"

    Returns:
        ImageCopyResult: 包含重写后的内容、复制数、丢失数
    """
    if not md_content:
        return ImageCopyResult(content=md_content)

    target_img_dir.mkdir(parents=True, exist_ok=True)

    img_pat = re.compile(r'!\[(.*?)\]\((.*?)\)')
    copied = 0
    missing = 0

    def _replace(m: re.Match) -> str:
        nonlocal copied, missing
        alt = m.group(1)
        src = m.group(2).strip()

        # 跳过 HTTP/HTTPS 图片
        if src.lower().startswith(('http://', 'https://')):
            return m.group(0)

        img_name = Path(src).name
        if not img_name:
            missing += 1
            return m.group(0)

        # 在多个源目录中查找
        src_path = None
        for sd in src_dirs:
            try:
                # 候选 1：直接按文件名查找
                candidate = sd / img_name
                if candidate.exists() and candidate.is_file():
                    src_path = candidate
                    break
                # 候选 2：按 Markdown 中的相对路径查找（如 ./images/foo.png）
                if src and not src.startswith('/') and not src.startswith('\\'):
                    candidate = sd / src
                    if candidate.exists() and candidate.is_file():
                        src_path = candidate
                        break
            except (OSError, PermissionError):
                continue

        if src_path is None:
            missing += 1
            return m.group(0)

        # 复制到目标目录（已存在则跳过）
        dest = target_img_dir / img_name
        if not dest.exists():
            try:
                shutil.copy2(src_path, dest)
            except (OSError, shutil.Error):
                missing += 1
                return m.group(0)

        copied += 1
        return f"![{alt}]({relative_img_path}/{img_name})"

    new_content = img_pat.sub(_replace, md_content)
    return ImageCopyResult(content=new_content, copied=copied, missing=missing)
