# Copyright (c) 2026 xingluosama121, MIT Licensed
"""子进程沙箱：跨平台命令执行沙箱（无第三方依赖）。

P1 提供基础隔离（独立进程 + 超时 + 输出捕获）。
P3 将迁移现有应用的沙箱池（复用/并发上限）、资源限制与网络策略。

编码说明：按 utf-8 → gbk → latin-1 顺序解码子进程输出，
避免 Windows 中文代码页输出乱码（与现有应用 robust_decode 一致）。
"""

from __future__ import annotations

import subprocess
from typing import Any, Callable, Dict, Optional

from norpagent.protocols.sandbox import SandboxResult


def _robust_decode(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class SubprocessSandbox:
    """单个子进程沙箱实例（每次 run_shell 启动新进程）。

    P4 起支持 ``run_python``：PTC 代码在独立子进程中执行，
    工具调用经长度前缀协议回传给宿主（见 isolated_python）。
    """

    def run_shell(
        self,
        command: str,
        timeout: float = 60.0,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,  # 跨平台：Windows 走 cmd，POSIX 走 /bin/sh
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
                env=env,
            )
            return SandboxResult(
                stdout=_robust_decode(proc.stdout).strip(),
                stderr=_robust_decode(proc.stderr).strip(),
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                stdout=_robust_decode(exc.stdout or b"").strip(),
                stderr=_robust_decode(exc.stderr or b"").strip() or f"命令超时({timeout}s)",
                exit_code=-1,
                timed_out=True,
            )
        except OSError as exc:
            return SandboxResult(stderr=str(exc), exit_code=-1)

    def run_python(
        self,
        code: str,
        tool_dispatch: Callable[[str, Dict[str, Any]], str],
        timeout: float = 60.0,
    ) -> SandboxResult:
        """PTC 代码隔离执行：独立子进程 + 工具 RPC 回传。"""
        from norpagent.builtin.sandboxes.isolated_python import (
            IsolatedPythonRunner,
        )

        return IsolatedPythonRunner().execute(code, tool_dispatch, timeout)

    def close(self) -> None:
        """P1 无驻留资源，直接返回。"""
        return None


class SubprocessSandboxProvider:
    """子进程沙箱提供者：按需新建实例。"""

    kind = "subprocess"

    def create(self) -> SubprocessSandbox:
        return SubprocessSandbox()
