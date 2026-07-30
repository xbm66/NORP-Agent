# Vibe Coding Agent - Tool executor with Docker sandbox
# Copyright (c) 2026 xingluosama

import os
import json
import shutil
import subprocess
import sys
import platform
from datetime import datetime
from pathlib import Path
from typing import Optional


class DockerSandbox:
    """Docker container sandbox for isolated command execution."""

    def __init__(
        self,
        project_root: str,
        image: str = "python:3.11-slim",
        network_mode: str = "none",
        mem_limit: str = "512m"
    ):
        self.project_root = os.path.abspath(project_root)
        self.image = image
        self.network_mode = network_mode
        self.mem_limit = mem_limit
        self._container = None
        self._client = None

    def _get_client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def start(self) -> str:
        client = self._get_client()
        volumes = {self.project_root: {"bind": "/workspace", "mode": "rw"}}
        self._container = client.containers.run(
            self.image,
            command="tail -f /dev/null",
            volumes=volumes,
            network_mode=self.network_mode,
            mem_limit=self.mem_limit,
            detach=True,
            remove=True
        )
        return self._container.id

    def exec(self, cmd: str, timeout: int = 30) -> str:
        if not self._container:
            raise RuntimeError("Sandbox not started")
        exit_code, output = self._container.exec_run(
            cmd,
            workdir="/workspace",
            stdout=True,
            stderr=True
        )
        result = output.decode("utf-8", errors="replace") if output else ""
        if not result.strip():
            result = f"Exit code: {exit_code}"
        return result

    def stop(self):
        if self._container:
            try:
                self._container.stop(timeout=5)
            except Exception:
                pass
            self._container = None

    def is_running(self) -> bool:
        if not self._container:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def __del__(self):
        self.stop()


class ToolExecutor:

    def __init__(self, project_root: str, sandbox: Optional[DockerSandbox] = None, app_dir: str = ""):
        self.project_root = os.path.abspath(project_root)
        self.sandbox = sandbox
        if app_dir:
            self.history_path = os.path.join(app_dir, ".agent_history.json")
            os.makedirs(app_dir, exist_ok=True)
        else:
            self.history_path = os.path.join(self.project_root, ".agent_history.json")
        self._use_sandbox = sandbox is not None

    def execute(self, tool_name: str, args: dict) -> str:
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
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"Error: unknown tool '{tool_name}'"
        try:
            return handler(args)
        except Exception as e:
            return f"Tool execution failed: {str(e)}"

    def _safe_path(self, path: str) -> str:
        full = os.path.abspath(os.path.join(self.project_root, path))
        if not full.startswith(self.project_root + os.sep) and full != self.project_root:
            raise ValueError(f"Path out of bounds: {path}")
        return full

    def _read_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        start_line = args.get("start_line")
        end_line = args.get("end_line")
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

    def _write_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(args["content"])
        return f"File written: {path}"

    def _replace_in_file(self, args: dict) -> str:
        """替换文件中的指定文本片段。

        old_str 必须在文件中精确匹配唯一一处。
        若匹配 0 处则报错，匹配多处则提示需要更多上下文。
        """
        path = self._safe_path(args["path"])
        old_str = args["old_str"]
        new_str = args["new_str"]

        if not os.path.exists(path):
            return f"Error: file not found: {path}"

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
                    return f"Error: old_str not found in file. Similar lines:\n{hint}\n\nTip: use read_file to verify the exact content."
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

    def _list_dir(self, args: dict) -> str:
        path = self._safe_path(args.get("path", "."))
        if not os.path.isdir(path):
            return f"Not a directory: {path}"
        items = os.listdir(path)
        if not items:
            return "(empty)"
        dirs = [d + "/" for d in items if os.path.isdir(os.path.join(path, d))]
        files = [f for f in items if not os.path.isdir(os.path.join(path, f))]
        return "\n".join(sorted(dirs) + sorted(files))

    def _search_in_files(self, args: dict) -> str:
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

    def _delete_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        if os.path.isdir(path):
            shutil.rmtree(path)
            return f"Directory deleted: {path}"
        elif os.path.isfile(path):
            os.remove(path)
            return f"File deleted: {path}"
        else:
            return f"Path not found: {path}"

    def _exec_cmd(self, args: dict) -> str:
        cmd = args["command"]
        timeout = args.get("timeout", 30)
        dangerous = ["sudo", "rm -rf /", "mkfs", "dd if=", "> /dev/sda", "format c:"]
        for pattern in dangerous:
            if pattern in cmd.lower():
                return f"Blocked dangerous command: matched '{pattern}'"
        if self._use_sandbox:
            return self._exec_in_sandbox(cmd, timeout)
        return self._exec_local(cmd, timeout)

    def _exec_local(self, cmd: str, timeout: int) -> str:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=self.project_root
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            output = f"Command exited with code {result.returncode}"
        return output

    def _exec_in_sandbox(self, cmd: str, timeout: int) -> str:
        return self.sandbox.exec(cmd, timeout=timeout)

    def _open_file(self, args: dict) -> str:
        """用系统默认程序打开文件。"""
        path = self._safe_path(args["path"])
        if not os.path.exists(path):
            return f"Error: file not found: {path}"

        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return f"File opened: {path}"
        except Exception as e:
            return f"Failed to open file: {str(e)}"

    def _web_search(self, args: dict) -> str:
        """使用 DuckDuckGo Instant Answer API 进行联网搜索。"""
        query = args.get("query", "")
        if not query:
            return "Error: search query is required"
        try:
            import requests
            url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
                "t": "vibe_agent"
            }
            resp = requests.get(url, params=params, timeout=15,
                                headers={"User-Agent": "VibeCodingAgent/1.0"})
            resp.raise_for_status()
            data = resp.json()

            results = []
            # Abstract / instant answer
            if data.get("AbstractText"):
                results.append(f"📌 {data['AbstractText']}")
                if data.get("AbstractURL"):
                    results.append(f"   来源: {data['AbstractURL']}")

            # Related topics
            topics = data.get("RelatedTopics", [])
            if topics:
                results.append("")
                count = 0
                for topic in topics:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append(f"🔹 {topic['Text']}")
                        if topic.get("FirstURL"):
                            results.append(f"   {topic['FirstURL']}")
                        count += 1
                        if count >= 5:
                            break

            # Answer
            if data.get("Answer"):
                results.insert(0, f"✅ Answer: {data['Answer']}")

            # Definition
            if data.get("Definition"):
                results.insert(0, f"📖 Definition: {data['Definition']}")
                if data.get("DefinitionSource"):
                    results.insert(1, f"   来源: {data['DefinitionSource']}")

            if not results:
                return f"No results found for: {query}"

            return "\n".join(results)

        except ImportError:
            # Fallback to urllib if requests is not available
            import urllib.request
            import urllib.parse
            try:
                qs = urllib.parse.urlencode({
                    "q": query, "format": "json", "no_html": 1,
                    "skip_disambig": 1, "t": "vibe_agent"
                })
                url = f"https://api.duckduckgo.com/?{qs}"
                req = urllib.request.Request(url, headers={"User-Agent": "VibeCodingAgent/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

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
        except Exception as e:
            return f"Web search failed: {str(e)}"

    def _init_project(self, args: dict) -> str:
        ptype = args["type"]
        name = args["name"]
        proj_path = self._safe_path(name)
        os.makedirs(proj_path, exist_ok=True)
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

    def _install_dependency(self, args: dict) -> str:
        package = args["package"]
        manager = args.get("manager", "pip")
        if manager == "pip":
            cmd = f"pip install {package}"
        elif manager == "npm":
            cmd = f"npm install {package}"
        else:
            return f"Unsupported package manager: {manager}"
        if self._use_sandbox:
            return self._exec_in_sandbox(cmd, timeout=120)
        result = subprocess.run(
            cmd.split() if manager == "pip" else ["npm", "install", package],
            capture_output=True, text=True, timeout=120, cwd=self.project_root,
            shell=(manager == "pip")
        )
        return result.stdout.strip() or result.stderr.strip()

    def _git_commit(self, args: dict) -> str:
        message = args["message"]
        subprocess.run(["git", "add", "-A"], cwd=self.project_root, capture_output=True)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.project_root, capture_output=True, text=True
        )
        return result.stdout.strip() or result.stderr.strip()

    def _task_done(self, args: dict) -> str:
        summary = args["summary"]
        code_path = args.get("code_path", ".")
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


if __name__ == "__main__":
    import tempfile

    def test_no_sandbox():
        print("[test1] local executor")
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            r = ex.execute("write_file", {"path": "hello.py", "content": "print('hello')"})
            assert "File written" in r
            r = ex.execute("read_file", {"path": "hello.py"})
            assert "print('hello')" in r
            r = ex.execute("list_dir", {"path": "."})
            assert "hello.py" in r
            r = ex.execute("delete_file", {"path": "hello.py"})
            assert "File deleted" in r
            print("  pass")

    def test_sandbox():
        print("[test2] docker sandbox")
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = DockerSandbox(tmp)
            try:
                sandbox.start()
            except Exception as e:
                print(f"  skip (docker not available: {e})")
                return
            ex = ToolExecutor(tmp, sandbox=sandbox)
            r = ex.execute("write_file", {"path": "hello.py", "content": "print('hello')"})
            assert "File written" in r
            r = ex.execute("exec_cmd", {"command": "python hello.py"})
            assert "hello" in r
            r = ex.execute("exec_cmd", {"command": "pip --version"})
            assert "pip" in r
            sandbox.stop()
            print("  pass")

    def test_context_manager():
        print("[test3] context manager")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with DockerSandbox(tmp) as sb:
                    assert sb.is_running()
                assert not sb.is_running()
                print("  pass")
            except Exception as e:
                print(f"  skip (docker not available: {e})")

    def test_dangerous_block():
        print("[test4] dangerous command block")
        ex = ToolExecutor(".")
        r = ex.execute("exec_cmd", {"command": "sudo rm -rf /"})
        assert "Blocked" in r
        r = ex.execute("exec_cmd", {"command": "mkfs /dev/sda"})
        assert "Blocked" in r
        print("  pass")

    def test_replace_in_file():
        print("[test5] replace_in_file")
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            ex.execute("write_file", {"path": "test.py", "content": "def foo():\n    return 1\n\ndef bar():\n    return 2\n"})
            # 单次替换
            r = ex.execute("replace_in_file", {"path": "test.py", "old_str": "return 1", "new_str": "return 42"})
            assert "1 replacement" in r
            content = ex.execute("read_file", {"path": "test.py"})
            assert "return 42" in content
            assert "return 1" not in content
            # 多处匹配应报错
            ex.execute("write_file", {"path": "test.py", "content": "x = 1\ny = 1\nz = 2\n"})
            r = ex.execute("replace_in_file", {"path": "test.py", "old_str": "= 1", "new_str": "= 99"})
            assert "matches 2 locations" in r
            # 匹配不到应报错
            r = ex.execute("replace_in_file", {"path": "test.py", "old_str": "nonexistent", "new_str": "x"})
            assert "not found" in r
            print("  pass")

    test_no_sandbox()
    test_dangerous_block()
    test_replace_in_file()
    test_sandbox()
    test_context_manager()
    print("\nall tests done")
