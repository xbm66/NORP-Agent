# Vibe Coding Agent - 异步工具执行器 (Async Tool Executor)
# 集成：沙箱池、文件IO队列、路径映射、权限级联、生命周期、资源隔离
# Copyright (c) 2026 xingluosama

import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from sandbox_pool import SandboxPool, Sandbox, get_sandbox_pool
from file_io_queue import FileIOQueue, FileOp, get_file_io_queue
from path_mapper import PathMapper, PluginPathMapper
from permission_cascade import (
    Permission, PermissionCascade, PermissionSet,
    get_permission_cascade, SYSTEM_PERMISSIONS,
)
from lifecycle_manager import LifecycleManager, get_lifecycle_manager
from resource_isolator import ResourceIsolator, ResourceLimits, get_resource_isolator


class AsyncToolExecutor:
    """异步工具执行器。

    集成所有新架构模块：
    - SandboxPool: 多沙箱池，异步获取/释放
    - FileIOQueue: 文件并发访问检测和排队
    - PathMapper: 路径映射（宿主↔沙箱）
    - PermissionCascade: 权限级联检查
    - LifecycleManager: 进程组生命周期
    - ResourceIsolator: 资源隔离
    """

    def __init__(
        self,
        project_root: str,
        app_dir: str = "",
        task_id: str = "",
        sandbox_pool: Optional[SandboxPool] = None,
        file_io_queue: Optional[FileIOQueue] = None,
        path_mapper: Optional[PathMapper] = None,
        permission_cascade: Optional[PermissionCascade] = None,
        lifecycle_manager: Optional[LifecycleManager] = None,
        resource_isolator: Optional[ResourceIsolator] = None,
    ):
        self.project_root = os.path.abspath(project_root)
        self.app_dir = app_dir
        self.task_id = task_id or f"task_{id(self)}"

        # 注入依赖（默认使用全局单例）
        self.sandbox_pool = sandbox_pool or get_sandbox_pool()
        self.file_io_queue = file_io_queue or get_file_io_queue()
        self.path_mapper = path_mapper or PathMapper()
        self.permission_cascade = permission_cascade or get_permission_cascade()
        self.lifecycle_manager = lifecycle_manager or get_lifecycle_manager()
        self.resource_isolator = resource_isolator or get_resource_isolator()

        # 当前占用的沙箱
        self._sandbox: Optional[Sandbox] = None

        # 历史记录路径
        if app_dir:
            self.history_path = os.path.join(app_dir, ".agent_history.json")
            os.makedirs(app_dir, exist_ok=True)
        else:
            self.history_path = os.path.join(self.project_root, ".agent_history.json")

        # 工具日志路径
        if app_dir:
            self.tool_log_path = os.path.join(app_dir, "tool_calls.jsonl")
        else:
            self.tool_log_path = ""

    # ── 沙箱管理 ──

    async def acquire_sandbox(self) -> Sandbox:
        """获取沙箱（异步）。"""
        if self._sandbox and self._sandbox.in_use:
            return self._sandbox

        # 配置路径映射
        extra_paths = {}
        if self.app_dir:
            extra_paths[self.app_dir] = "/sandbox_app"

        self._sandbox = await self.sandbox_pool.acquire(
            task_id=self.task_id,
            workspace_root=self.project_root,
            extra_paths=extra_paths,
        )

        # ★ 将沙箱的路径映射同步到本地 PathMapper（供无沙箱回退路径使用）
        for host_path, sandbox_path in self._sandbox.path_map.items():
            self.path_mapper.add_mapping(host_path, sandbox_path)

        return self._sandbox

    async def release_sandbox(self):
        """释放沙箱。"""
        if self._sandbox:
            await self.sandbox_pool.release(self._sandbox)
            self._sandbox = None

    # ── 工具执行入口 ──

    async def execute(self, tool_name: str, args: dict) -> str:
        """异步执行工具。"""
        # 权限检查
        self._check_tool_permission(tool_name, args)

        # 资源检查
        if not self.resource_isolator.check_any(self.task_id):
            return "Error: resource quota exhausted for this task"

        handlers = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "replace_in_file": self._replace_in_file,
            "list_dir": self._list_dir,
            "search_in_files": self._search_in_files,
            "delete_file": self._delete_file,
            "exec_cmd": self._exec_cmd,
            "init_project": self._init_project,
            "install_dependency": self._install_dependency,
            "git_commit": self._git_commit,
            "task_done": self._task_done,
            "web_search": self._web_search,
            "open_file": self._open_file,
            "read_clipboard": self._read_clipboard,
            "write_clipboard": self._write_clipboard,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return f"Error: unknown tool '{tool_name}'"

        try:
            return await handler(args)
        except PermissionError as e:
            return f"Permission denied: {str(e)}"
        except Exception as e:
            return f"Tool execution failed: {str(e)}"

    def _check_tool_permission(self, tool_name: str, args: dict):
        """检查工具执行权限。"""
        perm_map = {
            "read_file": Permission.FILE_READ,
            "write_file": Permission.FILE_WRITE,
            "replace_in_file": Permission.FILE_WRITE,
            "list_dir": Permission.FILE_LIST,
            "search_in_files": Permission.FILE_READ,
            "delete_file": Permission.FILE_DELETE,
            "exec_cmd": Permission.PROCESS_SHELL,
            "init_project": Permission.FILE_WRITE,
            "install_dependency": Permission.PROCESS_SHELL,
            "git_commit": Permission.PROCESS_SHELL,
            "open_file": Permission.PROCESS_EXEC,
            "web_search": Permission.NETWORK_OUT,
            "read_clipboard": Permission.PROCESS_EXEC,
            "write_clipboard": Permission.PROCESS_EXEC,
        }

        perm = perm_map.get(tool_name)
        if perm:
            path = args.get("path", "")
            self.permission_cascade.check_or_raise(perm, path)

    # ── 路径安全 ──

    def _safe_path(self, path: str) -> str:
        """验证并规范化路径，确保在工作区范围内。"""
        full = os.path.abspath(os.path.join(self.project_root, path))
        if not full.startswith(self.project_root + os.sep) and full != self.project_root:
            raise ValueError(f"Path out of bounds: {path}")
        return full

    def _map_to_sandbox(self, host_path: str) -> str:
        """将宿主路径映射为沙箱路径。"""
        if self._sandbox:
            return self._sandbox.map_path(host_path)
        return self.path_mapper.to_sandbox(host_path)

    # ═══════════════════════════════════════════════════════════════
    #  文件操作（带 FileIOQueue 并发检测）
    # ═══════════════════════════════════════════════════════════════

    async def _read_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        # 文件 I/O 队列：获取读权限
        await self.file_io_queue.acquire(self.task_id, path, FileOp.READ)
        try:
            with open(path, "r", encoding="utf-8") as f:
                if start_line is None and end_line is None:
                    return f.read()
                lines = f.readlines()
                total = len(lines)
                if start_line is None:
                    start_line = 1
                if end_line is None:
                    end_line = total
                start_line = max(1, start_line)
                end_line = min(total, end_line)
                if start_line > end_line:
                    return f"Error: start_line ({start_line}) > end_line ({end_line})"
                result = "".join(lines[start_line - 1:end_line])
                header = f"[Lines {start_line}-{end_line} of {total}]\n"
                return header + result
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.READ)

    async def _write_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])

        # 文件 I/O 队列：获取写权限（如有冲突则排队）
        await self.file_io_queue.acquire(self.task_id, path, FileOp.WRITE)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(args["content"])
            return f"File written: {path}"
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.WRITE)

    async def _replace_in_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        old_str = args["old_str"]
        new_str = args["new_str"]

        if not os.path.exists(path):
            return f"Error: file not found: {path}"

        # 文件 I/O 队列：写权限
        await self.file_io_queue.acquire(self.task_id, path, FileOp.WRITE)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            if old_str == "":
                return "Error: old_str cannot be empty"

            count = content.count(old_str)
            if count == 0:
                old_first_line = old_str.split('\n')[0].strip()
                if old_first_line:
                    suggestions = []
                    for lineno, line in enumerate(content.split('\n'), 1):
                        if old_first_line[:20] in line:
                            suggestions.append(f"  Line {lineno}: {line.strip()[:80]}")
                    if suggestions:
                        hint = "\n".join(suggestions[:5])
                        return (f"Error: old_str not found in file. Similar lines:\n{hint}\n\n"
                                f"Tip: use read_file to verify the exact content.")
                return f"Error: old_str not found in file. The text must match exactly (including whitespace). Use read_file to verify."

            if count > 1:
                return (
                    f"Error: old_str matches {count} locations in the file. "
                    f"Please include more surrounding context to make it unique. "
                    f"Use read_file to see the file and select a larger unique snippet."
                )

            new_content = content.replace(old_str, new_str, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return f"File modified: {path} (1 replacement)"
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.WRITE)

    async def _delete_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])

        # 文件 I/O 队列：删除权限（视为写操作）
        await self.file_io_queue.acquire(self.task_id, path, FileOp.DELETE)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                return f"Directory deleted: {path}"
            elif os.path.isfile(path):
                os.remove(path)
                return f"File deleted: {path}"
            else:
                return f"Path not found: {path}"
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.DELETE)

    async def _list_dir(self, args: dict) -> str:
        path = self._safe_path(args.get("path", "."))
        if not os.path.isdir(path):
            return f"Not a directory: {path}"
        items = os.listdir(path)
        if not items:
            return "(empty)"
        dirs = [d + "/" for d in items if os.path.isdir(os.path.join(path, d))]
        files = [f for f in items if not os.path.isdir(os.path.join(path, f))]
        return "\n".join(sorted(dirs) + sorted(files))

    async def _search_in_files(self, args: dict) -> str:
        pattern = args["pattern"]
        root = self._safe_path(args.get("path", "."))
        matches = []
        if os.path.isfile(root):
            targets = [root]
        else:
            targets = []
            for dirpath, _, filenames in os.walk(root):
                if any(part.startswith(".") or part in ("node_modules", "__pycache__", ".git")
                       for part in Path(dirpath).relative_to(root).parts):
                    continue
                for fn in filenames:
                    if not fn.startswith("."):
                        targets.append(os.path.join(dirpath, fn))
        for filepath in targets:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if pattern in line:
                            rel = os.path.relpath(filepath, self.project_root)
                            matches.append(f"{rel}:{lineno}: {line.strip()[:120]}")
            except Exception:
                continue
            if len(matches) >= 50:
                matches.append("... (truncated, max 50 results)")
                break
        return "\n".join(matches) if matches else "No matches found."

    # ═══════════════════════════════════════════════════════════════
    #  命令执行（沙箱池 + 生命周期绑定）
    # ═══════════════════════════════════════════════════════════════

    async def _exec_cmd(self, args: dict) -> str:
        cmd = args["command"]
        timeout = args.get("timeout", 30)

        # 安全检查
        dangerous = ["sudo", "rm -rf /", "mkfs", "dd if=", "> /dev/sda", "format c:"]
        for pattern in dangerous:
            if pattern in cmd.lower():
                return f"Blocked dangerous command: matched '{pattern}'"

        # 资源隔离检查
        if self.resource_isolator.throttle_plugins():
            # 终端资源紧张，非终端命令延后
            pass

        # 获取沙箱
        try:
            sandbox = await self.acquire_sandbox()
        except Exception as e:
            # 沙箱不可用，回退到本地执行
            return await self._exec_local_async(cmd, timeout)

        # 在沙箱中执行
        try:
            # 路径映射：将命令中的宿主路径替换为沙箱路径
            mapped_cmd = self._map_command_paths(cmd)
            cwd = self._map_to_sandbox(self.project_root)

            result = await self.sandbox_pool.exec_in_sandbox(
                sandbox, mapped_cmd, timeout=timeout, cwd=cwd,
            )

            # 路径反向映射：将输出中的沙箱路径还原为宿主路径
            result = self._unmap_result_paths(result)
            return result
        except Exception as e:
            # 沙箱执行失败，回退本地
            return await self._exec_local_async(cmd, timeout)

    async def _exec_local_async(self, cmd: str, timeout: int) -> str:
        """本地异步执行命令（带进程组管理）。"""
        creationflags = 0
        if platform.system() == "Windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            creationflags |= 0x08000000

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
                creationflags=creationflags if platform.system() == "Windows" else 0,
                start_new_session=True,
            )

            # 注册到生命周期管理器
            self.lifecycle_manager.register_process(self.task_id, proc.pid)

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            output = ((stdout.decode("utf-8", errors="replace") if stdout else "") +
                      (stderr.decode("utf-8", errors="replace") if stderr else ""))
            if not output.strip():
                output = f"Command exited with code {proc.returncode}"
            return output.strip()

        except asyncio.TimeoutError:
            # 超时：生命周期管理器会杀进程组
            self.lifecycle_manager.stop_task(self.task_id, reason="cmd_timeout")
            return f"Command timed out after {timeout}s and process group was terminated"

    def _map_command_paths(self, cmd: str) -> str:
        """将命令中的宿主路径替换为沙箱路径。"""
        if not self._sandbox:
            return cmd

        result = cmd
        # 替换工作区路径
        if self.project_root in result:
            sandbox_root = self._sandbox.map_path(self.project_root)
            result = result.replace(self.project_root, sandbox_root)

        # 替换 app_dir
        if self.app_dir and self.app_dir in result:
            sandbox_app = self._sandbox.map_path(self.app_dir)
            result = result.replace(self.app_dir, sandbox_app)

        return result

    def _unmap_result_paths(self, text: str) -> str:
        """将输出中的沙箱路径还原为宿主路径。"""
        if not self._sandbox:
            return text

        result = text
        for sandbox_path, host_path in self._sandbox.reverse_path_map.items():
            if sandbox_path in result:
                result = result.replace(sandbox_path, host_path)
        return result

    # ═══════════════════════════════════════════════════════════════
    #  其他工具（异步化）
    # ═══════════════════════════════════════════════════════════════

    async def _open_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        if not os.path.exists(path):
            return f"Error: file not found: {path}"

        def _open():
            system = platform.system()
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])

        await asyncio.get_running_loop().run_in_executor(None, _open)
        return f"File opened: {path}"

    async def _read_clipboard(self, args: dict) -> str:
        """读取系统剪贴板文本。"""
        def _read():
            system = platform.system()
            if system == "Windows":
                try:
                    result = subprocess.run(
                        ["powershell", "-Command", "Get-Clipboard"],
                        capture_output=True, text=True, timeout=10,
                        creationflags=0x08000000 if platform.system() == "Windows" else 0,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr.strip())
                    return result.stdout
                except FileNotFoundError:
                    # 回退：使用 clip 命令 + 临时文件
                    import tempfile
                    tmp = os.path.join(tempfile.gettempdir(), "_vibe_paste.txt")
                    subprocess.run(
                        ["powershell", "-Command", f"Get-Clipboard > '{tmp}'"],
                        capture_output=True, timeout=10,
                        creationflags=0x08000000,
                    )
                    try:
                        with open(tmp, "r", encoding="utf-8", errors="replace") as f:
                            return f.read()
                    finally:
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
            elif system == "Darwin":
                result = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=10
                )
                return result.stdout
            else:
                # Linux: 尝试 wl-paste (Wayland) 或 xclip (X11)
                for cmd in [["wl-paste"], ["xclip", "-selection", "clipboard", "-o"]]:
                    try:
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=10
                        )
                        if result.returncode == 0:
                            return result.stdout
                    except FileNotFoundError:
                        continue
                return "Error: No clipboard tool found. Install xclip (X11) or wl-clipboard (Wayland)."

        try:
            text = await asyncio.get_running_loop().run_in_executor(None, _read)
            if not text:
                return "(clipboard is empty)"
            return text
        except Exception as e:
            return f"Failed to read clipboard: {str(e)}"

    async def _write_clipboard(self, args: dict) -> str:
        """将文本写入系统剪贴板。"""
        text = args["text"]

        def _write():
            system = platform.system()
            if system == "Windows":
                # 使用 PowerShell Set-Clipboard，避免特殊字符问题
                proc = subprocess.run(
                    ["powershell", "-Command", "Set-Clipboard", "-Value", "$input"],
                    input=text, capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip())
            elif system == "Darwin":
                proc = subprocess.run(
                    ["pbcopy"], input=text, capture_output=True, text=True, timeout=10
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip())
            else:
                # Linux: 尝试 wl-copy (Wayland) 或 xclip (X11)
                for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"]]:
                    try:
                        proc = subprocess.run(
                            cmd, input=text, capture_output=True, text=True, timeout=10
                        )
                        if proc.returncode == 0:
                            return
                    except FileNotFoundError:
                        continue
                raise RuntimeError(
                    "No clipboard tool found. Install xclip (X11) or wl-clipboard (Wayland)."
                )

        try:
            await asyncio.get_running_loop().run_in_executor(None, _write)
            preview = text[:80] + "..." if len(text) > 80 else text
            return f"Text copied to clipboard ({len(text)} chars): {preview}"
        except Exception as e:
            return f"Failed to write clipboard: {str(e)}"

    async def _web_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "Error: search query is required"

        def _search():
            try:
                import requests
                url = "https://api.duckduckgo.com/"
                params = {
                    "q": query, "format": "json", "no_html": 1,
                    "skip_disambig": 1, "t": "vibe_agent"
                }
                resp = requests.get(url, params=params, timeout=15,
                                    headers={"User-Agent": "VibeCodingAgent/1.0"})
                resp.raise_for_status()
                data = resp.json()

                results = []
                if data.get("AbstractText"):
                    results.append(f"📌 {data['AbstractText']}")
                topics = data.get("RelatedTopics", [])
                for topic in topics[:5]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append(f"🔹 {topic['Text']}")
                if data.get("Answer"):
                    results.insert(0, f"✅ Answer: {data['Answer']}")
                if not results:
                    return f"No results found for: {query}"
                return "\n".join(results)
            except Exception as e:
                return f"Web search failed: {str(e)}"

        return await asyncio.get_running_loop().run_in_executor(None, _search)

    async def _init_project(self, args: dict) -> str:
        ptype = args["type"]
        name = args["name"]
        proj_path = self._safe_path(name)
        os.makedirs(proj_path, exist_ok=True)

        def _init():
            if ptype == "python":
                os.makedirs(os.path.join(proj_path, name), exist_ok=True)
                with open(os.path.join(proj_path, "requirements.txt"), "w") as f:
                    f.write("")
                with open(os.path.join(proj_path, name, "__init__.py"), "w") as f:
                    f.write("")
                with open(os.path.join(proj_path, name, "main.py"), "w") as f:
                    f.write(f"# {name}\n\ndef main():\n    pass\n\nif __name__ == '__main__':\n    main()\n")
            elif ptype == "web":
                os.makedirs(os.path.join(proj_path, "css"), exist_ok=True)
                os.makedirs(os.path.join(proj_path, "js"), exist_ok=True)
                with open(os.path.join(proj_path, "index.html"), "w") as f:
                    f.write(f"<!DOCTYPE html>\n<html>\n<head><title>{name}</title></head>\n<body>\n</body>\n</html>\n")
                with open(os.path.join(proj_path, "css", "style.css"), "w") as f:
                    f.write("/* styles */\n")
                with open(os.path.join(proj_path, "js", "main.js"), "w") as f:
                    f.write("// scripts\n")
            else:
                with open(os.path.join(proj_path, "README.md"), "w") as f:
                    f.write(f"# {name}\n")
            return f"Project '{name}' (type: {ptype}) created at {proj_path}"

        return await asyncio.get_running_loop().run_in_executor(None, _init)

    async def _install_dependency(self, args: dict) -> str:
        package = args["package"]
        manager = args.get("manager", "pip")

        # ★ 安全修复：使用列表参数 + shell=False，防止命令注入
        if manager == "pip":
            cmd_list = [sys.executable, "-m", "pip", "install", package]
        elif manager == "npm":
            cmd_list = ["npm", "install", package]
        else:
            return f"Unsupported package manager: {manager}"

        # 直接在本地异步执行，绕过 _exec_cmd 的 shell 封装
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
            )
            self.lifecycle_manager.register_process(self.task_id, proc.pid)

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
            output = ((stdout.decode("utf-8", errors="replace") if stdout else "") +
                      (stderr.decode("utf-8", errors="replace") if stderr else ""))
            return output.strip() or f"Exit code: {proc.returncode}"
        except asyncio.TimeoutError:
            self.lifecycle_manager.stop_task(self.task_id, reason="install_timeout")
            return f"Package install timed out after 120s"
        except Exception as e:
            return f"Package install failed: {str(e)}"

    async def _git_commit(self, args: dict) -> str:
        message = args["message"]

        async def _commit():
            proc1 = await asyncio.create_subprocess_exec(
                "git", "add", "-A",
                cwd=self.project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc1.communicate()

            proc2 = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", message,
                cwd=self.project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc2.communicate()
            output = (stdout.decode("utf-8", errors="replace") if stdout else "") + \
                     (stderr.decode("utf-8", errors="replace") if stderr else "")
            return output.strip()

        try:
            return await _commit()
        except Exception as e:
            return f"Git commit failed: {str(e)}"

    async def _task_done(self, args: dict) -> str:
        summary = args["summary"]
        code_path = args.get("code_path", ".")

        def _record():
            record = {
                "task": summary,
                "path": code_path,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            history = []
            if os.path.exists(self.history_path):
                try:
                    with open(self.history_path, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception:
                    history = []
            history.append(record)
            if len(history) > 20:
                history = history[-20:]
            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
            return f"Task recorded: {summary}"

        return await asyncio.get_running_loop().run_in_executor(None, _record)

    # ── 日志 ──

    def log_tool_call(self, step: int, tool_name: str, args: dict, result: str):
        """记录工具调用（JSONL 格式）。"""
        if not self.tool_log_path:
            return
        try:
            result_summary = result[:500] + "..." if len(result) > 500 else result
            record = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "step": step,
                "tool": tool_name,
                "args": args,
                "result_length": len(result),
                "result_summary": result_summary,
                "file_io_stats": self.file_io_queue.get_stats(),
            }
            with open(self.tool_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 清理 ──

    async def cleanup(self):
        """清理资源。"""
        await self.release_sandbox()
