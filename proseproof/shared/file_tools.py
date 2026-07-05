"""专用文件编辑工具链 —— Read / Write / Edit。

替代 bash_tool.py 的粗糙操作。白名单保护原文文件（只允许 _* 中间产物）。
设计决策见 ADR-0017。
"""
from __future__ import annotations
import os
from pathlib import Path
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


# 白名单：只允许操作以 _ 开头的中间产物文件
_ALLOWED_PREFIXES = ("_",)
# 原文文件前缀（显式拒绝）
_DENIED_PREFIXES = ("frag_",)


def _is_allowed(file_path: str) -> bool:
    """检查文件路径是否在白名单内。"""
    basename = os.path.basename(file_path)
    if any(basename.startswith(p) for p in _DENIED_PREFIXES if p != "_"):
        return False
    if any(basename.startswith(p) for p in _ALLOWED_PREFIXES):
        return True
    return False


# ============================================================
# ReadTool
# ============================================================

class ReadInput(BaseModel):
    file_path: str = Field(description="文件路径")
    offset: int = Field(default=0, description="起始行号（0-based）")
    limit: int | None = Field(default=None, description="读取行数，None 表示读全文")


class ReadTool(BaseTool):
    name: str = "read_file"
    description: str = "读取文件内容。offset/limit 控制行范围，不传则读全文。"
    args_schema: type[BaseModel] = ReadInput

    def _run(self, file_path: str, offset: int = 0, limit: int | None = None) -> str:
        path = Path(file_path)
        if not path.exists():
            return f"[错误] 文件不存在: {file_path}"
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"[错误] 读取失败: {e}"
        lines = content.split("\n")
        if limit is not None:
            lines = lines[offset:offset + limit]
        else:
            lines = lines[offset:]
        return "\n".join(lines)

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError


# ============================================================
# WriteTool
# ============================================================

class WriteInput(BaseModel):
    file_path: str = Field(description="文件路径")
    content: str = Field(description="要写入的内容")


class WriteTool(BaseTool):
    name: str = "write_file"
    description: str = "覆写文件内容。仅用于新建中间产物文件或 EditTool 失败后的兜底。"
    args_schema: type[BaseModel] = WriteInput

    def _run(self, file_path: str, content: str) -> str:
        if not _is_allowed(file_path):
            return (f"[拒绝] 不允许修改原文文件: {file_path}。"
                    "仅允许操作 _* 开头的中间产物。")
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            Path(file_path).write_text(content, encoding="utf-8")
            return f"[OK] 已写入 {len(content)} 字符到 {file_path}"
        except Exception as e:
            return f"[错误] 写入失败: {e}"

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError


# ============================================================
# EditTool
# ============================================================

class EditInput(BaseModel):
    file_path: str = Field(description="文件路径")
    old_string: str = Field(description="要替换的原文（必须精确匹配文件内容）")
    new_string: str = Field(description="替换后的新文本")
    replace_all: bool = Field(default=False, description="是否替换所有匹配项")


class EditTool(BaseTool):
    name: str = "edit_file"
    description: str = (
        "精确替换文件中的字符串。old_string 必须与文件内容完全一致（含缩进和换行）。"
        "replace_all=True 替换所有匹配项。"
    )
    args_schema: type[BaseModel] = EditInput

    def _run(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> str:
        if not _is_allowed(file_path):
            return (f"[拒绝] 不允许修改原文文件: {file_path}。"
                    "仅允许操作 _* 开头的中间产物。")

        path = Path(file_path)
        if not path.exists():
            return f"[错误] 文件不存在: {file_path}"
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return f"[错误] 读取失败: {e}"

        count = content.count(old_string)
        if count == 0:
            return (f"[失败] 找不到 old_string。请用 read_file 确认文件内容后重试。"
                    f"\n文件前 200 字符: {content[:200]}")
        if count > 1 and not replace_all:
            return (f"[提示] 找到 {count} 个匹配项。请缩小范围或设置 replace_all=True。"
                    f"\n首次匹配位置: {content.find(old_string)}")

        try:
            new_content = content.replace(old_string, new_string)
            path.write_text(new_content, encoding="utf-8")
            replaced = count if replace_all else 1
            return f"[OK] 已替换 {replaced} 处。"
        except Exception as e:
            return f"[错误] 写入失败: {e}"

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError
