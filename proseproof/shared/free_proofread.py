"""自由校对模式工具 —— 临时文件生成、输出目录管理。"""
import os
import shutil
import datetime
from pathlib import Path

from proseproof.core.logging_utils import log


def _timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def create_free_proofread_md(text, images=None, output_dir=None):
    if output_dir is None:
        output_dir = os.getcwd()

    os.makedirs(output_dir, exist_ok=True)

    ts = _timestamp()
    base_name = f"自由校对_{ts}"
    md_path = os.path.join(output_dir, f"{base_name}.md")
    img_dir = os.path.join(output_dir, f"{base_name}_images", "media")
    os.makedirs(img_dir, exist_ok=True)

    lines = []
    if text:
        lines.append(text.strip())
        lines.append("")

    if images:
        for idx, img_path in enumerate(images, 1):
            if not img_path or not os.path.exists(img_path):
                continue
            img_name = os.path.basename(img_path)
            dest_path = os.path.join(img_dir, img_name)
            try:
                shutil.copy2(img_path, dest_path)
                rel_path = f"./{base_name}_images/media/{img_name}"
                lines.append(f"![图片{idx}]({rel_path})")
                lines.append("")
            except Exception as e:
                log(f"⚠️ 复制图片失败: {img_name}, {e}")

    content = "\n".join(lines)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(content)

    log(f"✅ 自由校对临时文件已生成: {md_path}")
    return md_path


def get_free_proofread_output_dir(base_dir):
    ts = _timestamp()
    dir_name = f"自由校对_{ts}"
    output_dir = os.path.join(base_dir, dir_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def is_free_proofread_mode(source_mode):
    return source_mode == "自由校对"
