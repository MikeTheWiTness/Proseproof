"""BashTool + FileReadTool + FileWriteTool —— 让 LLM 直接操作文件。

用于格式修正等场景：LLM 不再返回修正后的文本（可能格式再出错），
而是直接编辑目标文件，编辑后由 Python 端重读验证。
"""
import subprocess
import os
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool


# ─── BashTool ───────────────────────────────────────────────

class BashParams(BaseModel):
    command: str = Field(
        description="要执行的 bash 命令。支持管道、重定向、python -c 等。"
    )


class BashTool(BaseTool):
    """让 LLM 执行 bash 命令来直接操作文件。

    用途：格式修正时，LLM 用 cat/type 读取文件内容，用 sed / python -c 等
    直接修改文件，Python 端在工具返回后重读文件进行验证。

    安全约束：通过 allowed_dir 限制可操作的文件目录。
    """

    name: str = "bash"
    description: str = (
        "执行 bash 命令来读取或编辑文件。常用命令：\n"
        "- python -c \"...\" — 用 Python 脚本读取/编辑文件（最推荐，跨平台）\n"
        "- sed -i 's/旧/新/g' <文件路径> — 替换文本\n"
        "注意：Windows 不支持 cat；用 python 读取文件。"
        "每个 python -c 命令末尾必须有 print()，否则看不到输出！"
    )
    args_schema: type[BaseModel] = BashParams

    allowed_dir: str | None = None

    def _run(self, command: str) -> str:
        cwd = self.allowed_dir if self.allowed_dir else os.getcwd()
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
                cwd=cwd,
            )
            out = result.stdout
            err = result.stderr
            parts = []
            if out:
                parts.append(f"STDOUT:\n{out.rstrip()}")
            if err:
                parts.append(f"STDERR:\n{err.rstrip()}")
            if not parts:
                parts.append("(无输出，命令执行成功)")
            return "\n".join(parts)
        except subprocess.TimeoutExpired:
            return "错误：命令执行超时（30秒）"
        except Exception as e:
            return f"错误：{e}"

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError


# ─── FileReadTool ────────────────────────────────────────────

class FileReadParams(BaseModel):
    path: str = Field(description="要读取的文件路径（绝对路径）")


class FileReadTool(BaseTool):
    """读取文件内容。比 bash cat/python -c 更简单可靠。"""

    name: str = "read_file"
    description: str = "读取指定文件的全部内容。返回文件文本。优先用这个工具而不是 bash 来读取文件。"
    args_schema: type[BaseModel] = FileReadParams

    def _run(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return f"文件内容 ({len(content)} 字符):\n\n{content}"
        except Exception as e:
            return f"读取文件失败：{e}"

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError


# ─── FileWriteTool ───────────────────────────────────────────

class FileWriteParams(BaseModel):
    path: str = Field(description="要写入的文件路径（绝对路径）")
    content: str = Field(description="要写入文件的完整内容")


class FileWriteTool(BaseTool):
    """覆盖写入文件。比 bash sed/python -c 更简单可靠。"""

    name: str = "write_file"
    description: str = (
        "将完整内容写入指定文件（覆盖原内容）。"
        "优先用这个工具而不是 bash 来写入/修改文件。"
    )
    args_schema: type[BaseModel] = FileWriteParams

    def _run(self, path: str, content: str) -> str:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"文件已成功写入 ({len(content)} 字符): {path}"
        except Exception as e:
            return f"写入文件失败：{e}"

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError
