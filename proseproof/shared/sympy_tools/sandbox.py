"""SymPy 代码执行沙箱。

- 开发模式：在隔离子进程中执行（sys.executable -c，带 CREATE_NO_WINDOW）
- 打包后（PyInstaller）：sys.executable 指向 GUI exe，不支持 -c 参数，
  回退为进程内执行（安全性由 check_dangerous 保证）。
"""
import io
import json
import os
import subprocess
import sys
import time

from .safety import check_dangerous


def _is_frozen() -> bool:
    """检测当前是否为 PyInstaller 打包后的运行环境。"""
    return getattr(sys, 'frozen', False)


def _run_in_subprocess(code: str, timeout: int) -> dict:
    """在隔离子进程中执行 SymPy 代码（开发模式）。"""
    start = time.monotonic()
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        restricted_code = (
            "import builtins, sys\n"
            "_allowed = {k:v for k,v in builtins.__dict__.items() "
            "if k not in ('exec','eval','compile','open','input','breakpoint','memoryview')}\n"
            "_allowed['__build_class__'] = __build_class__\n"
            "exec(sys.stdin.read(), {'__builtins__': _allowed, '__name__': '__main__'})\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", restricted_code],
            input=code, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            **(dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else {}),
        )
        elapsed = int((time.monotonic() - start) * 1000)

        if proc.returncode != 0:
            return {
                "success": False, "result": None,
                "error": proc.stderr.strip() or "Unknown subprocess error",
                "code": code, "elapsed_ms": elapsed,
            }

        result = json.loads(proc.stdout.strip())
        return {
            "success": True, "result": result,
            "error": None, "code": code, "elapsed_ms": elapsed,
        }

    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "success": False, "result": None,
            "error": f"Execution timed out ({timeout}s)",
            "code": code, "elapsed_ms": elapsed,
        }
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "success": False, "result": None,
            "error": str(e), "code": code, "elapsed_ms": elapsed,
        }


def _run_inprocess(code: str) -> dict:
    """在进程内执行 SymPy 代码（打包后回退方案）。

    安全性由 check_dangerous() 保证：已禁用 exec/eval/compile/open/import 等危险操作，
    且 SymPy 表达式由 LLM tool call 生成，非用户直接输入。
    """
    start = time.monotonic()
    try:
        namespace: dict = {}
        import builtins
        _allowed = {
            k: v for k, v in builtins.__dict__.items()
            if k not in ('exec', 'eval', 'compile', 'open', 'input', 'breakpoint', 'memoryview')
        }
        _allowed['__build_class__'] = __build_class__
        namespace['__builtins__'] = _allowed
        namespace['__name__'] = '__main__'

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            exec(code, namespace)
            stdout_output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        elapsed = int((time.monotonic() - start) * 1000)

        if stdout_output.strip():
            try:
                result = json.loads(stdout_output.strip())
                return {
                    "success": True, "result": result,
                    "error": None, "code": code, "elapsed_ms": elapsed,
                }
            except json.JSONDecodeError:
                pass

        if 'result' in namespace:
            return {
                "success": True, "result": namespace['result'],
                "error": None, "code": code, "elapsed_ms": elapsed,
            }

        return {
            "success": False, "result": None,
            "error": "No output produced",
            "code": code, "elapsed_ms": elapsed,
        }

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "success": False, "result": None,
            "error": str(e), "code": code, "elapsed_ms": elapsed,
        }


def execute_code(code: str, timeout: int = 30) -> dict:
    """在隔离环境中执行 SymPy 代码，返回结构化结果。

    开发模式：子进程隔离（stdin 传码，避免命令行限制和文件句柄竞争）。
    打包模式：进程内执行（GUI exe 不支持 -c 子进程调用）。
    """
    danger = check_dangerous(code)
    if danger:
        return {
            "success": False, "result": None,
            "error": danger, "code": code, "elapsed_ms": 0,
        }

    if _is_frozen():
        return _run_inprocess(code)
    return _run_in_subprocess(code, timeout)
