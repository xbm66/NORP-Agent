# Vibe Coding Agent - pywebview接口层
# Copyright (c) 2026 xingluosama

import os
import json
import base64
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import ConfigManager
from event_queue import EventQueue
from loop import AgentLoop
from plugin_system.manager import PluginManager

import json



def extract_text_from_file(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext in ['.txt', '.py', '.json', '.csv', '.css', '.html', '.md', '.js',
               '.ts', '.tsx', '.jsx', '.yaml', '.yml', '.toml', '.xml',
               '.sh', '.bat', '.ps1', '.ini', '.cfg', '.log', '.sql', '.rs',
               '.go', '.c', '.cpp', '.h', '.java', '.kt', '.swift', '.rb',
               '.php', '.lua', '.r', '.m', '.mm','jbeam']:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    elif ext == '.pdf':
        try:
            import PyPDF2
        except ImportError:
            raise Exception("PyPDF2 not installed")
        text = []
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text.append(page.extract_text() or '')
        return '\n'.join(text)
    elif ext == '.docx':
        try:
            import docx
        except ImportError:
            raise Exception("python-docx not installed")
        d = docx.Document(file_path)
        return '\n'.join([p.text for p in d.paragraphs])
    elif ext == '.xlsx':
        try:
            import openpyxl
        except ImportError:
            raise Exception("openpyxl not installed")
        wb = openpyxl.load_workbook(file_path, data_only=True)
        text = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                text.append('\t'.join([str(cell) if cell is not None else '' for cell in row]))
        return '\n'.join(text)
    else:
        raise ValueError(f"Unsupported file type: {ext}")



MEMORY_DIR_NAME = 'memory'
MEMORY_FILE_NAME = 'memory.json'


class AgentAPI:

    def __init__(self, app_dir: str):
        self.config_manager = ConfigManager(app_dir)
        self.app_dir = app_dir
        self.event_queue: Optional[EventQueue] = None
        self.loop: Optional[AgentLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self.conversation_history: list = []  


        self.current_messages: list = []   
        self.memory_history: list = []     
        self.memory_summary: str = ""      
        self._load_memory()

        # ── Plugin system ──
        cfg = self.config_manager.load()
        plugin_dirs = cfg.get("plugin_dirs", [])
        self.plugin_manager = PluginManager(
            plugin_dirs=plugin_dirs,
            app_dir=app_dir,
            project_root=cfg.get("project_root", ""),
            config=cfg,  # pass full config for security settings
        )
        self.plugin_manager.update_config_snapshot(cfg)
        if cfg.get("plugins_enabled", True):
            self.plugin_manager.discover_and_load()
        else:
            self.plugin_manager.set_plugin_dirs([])

        self._ensure_project_root()

    def _ensure_project_root(self):
        cfg = self.config_manager.load()
        root = cfg.get("project_root", "")
        if root:
            os.makedirs(root, exist_ok=True)


    def _get_memory_file(self) -> str:
        return os.path.join(self.app_dir, MEMORY_DIR_NAME, MEMORY_FILE_NAME)

    def _load_memory(self):
        """从磁盘加载持久化记忆。"""
        memory_file = self._get_memory_file()
        if os.path.exists(memory_file):
            try:
                with open(memory_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.memory_history = data.get('history', [])
                self.memory_summary = data.get('summary', '')
            except Exception:
                self.memory_history = []
                self.memory_summary = ""

    def _save_memory(self):
        """将记忆保存到磁盘。"""
        memory_dir = Path(self.app_dir) / MEMORY_DIR_NAME
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = memory_dir / MEMORY_FILE_NAME
        data = {
            'history': self.memory_history,
            'summary': self.memory_summary,
        }
        with open(memory_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _trim_memory(self):
        """修剪记忆，保留最近 max_rounds 轮（移植自 duo2.py 的 _trim_memory）。"""
        cfg = self.config_manager.load()
        memory_enabled = cfg.get('memory', True)
        if not memory_enabled:
            return
        max_rounds = cfg.get('max_rounds', 10)
        mode = cfg.get('memory_mode', 'full')
        total_rounds = len(self.memory_history) // 2
        if total_rounds <= max_rounds:
            return

        if mode == 'full':
            
            excess = (total_rounds - max_rounds) * 2
            self.memory_history = self.memory_history[excess:]
            self._save_memory()
        else:
            
            keep_rounds = 2
            keep_count = keep_rounds * 2
            if len(self.memory_history) <= keep_count:
                return
            to_summarize = self.memory_history[:-keep_count]
            recent = self.memory_history[-keep_count:]
            text = "\n".join([f"{m['role']}: {str(m.get('content', ''))[:500]}"
                              for m in to_summarize])
            
            summary_text = text[:400] + "..." if len(text) > 400 else text
            self.memory_summary = f"历史摘要：{summary_text}"
            self.memory_history = recent
            self._save_memory()

    def get_initial_messages(self) -> list:
        return self.current_messages.copy()

    def get_memory_content(self) -> str:
        cfg = self.config_manager.load()
        memory_enabled = cfg.get('memory', True)
        if not memory_enabled:
            return ""
        if not self.memory_history:
            return ""

        prefix = "以下为历史对话记忆，请不要回复该内容：\n"
        if cfg.get('memory_mode', 'full') == 'summary' and self.memory_summary:
            return prefix + self.memory_summary + "\n"

        # 取最近 N 条显示（防止单条过长）
        recent = self.memory_history[-20:]
        text_lines = []
        for m in recent:
            role_label = "用户" if m.get('role') == 'user' else "助手"
            content = str(m.get('content', ''))[:300]
            text_lines.append(f"{role_label}: {content}")
        return prefix + "历史对话：\n" + "\n".join(text_lines) + "\n"

    def clear_memory(self) -> bool:
        memory_file = Path(self.app_dir) / MEMORY_DIR_NAME / MEMORY_FILE_NAME
        if memory_file.exists():
            memory_file.unlink()
            self.memory_history = []
            self.memory_summary = ""
            return True
        return False

    def _create_loop(self):
        cfg = self.config_manager.load()
        api_key = self.config_manager.get_api_key()
        if not api_key:
            raise RuntimeError("API key not configured")
        self.event_queue = EventQueue(max_size=cfg.get("queue_max_size", 200))

        model = cfg.get("model", "")
        if not model or not model.strip() or model.strip() in (".", ""):
            model = "deepseek-v4-pro"

        # Update plugin manager config
        self.plugin_manager.update_config_snapshot(cfg)
        self.plugin_manager.update_security_config(cfg)

        # Reload plugins if dirs or enabled state changed
        if cfg.get("plugins_enabled", True):
            current_dirs = set(self.plugin_manager.plugin_dirs)
            new_dirs = set(cfg.get("plugin_dirs", []))
            if current_dirs != new_dirs:
                self.plugin_manager.set_plugin_dirs(cfg.get("plugin_dirs", []))
        else:
            self.plugin_manager.set_plugin_dirs([])

        self.loop = AgentLoop(
            api_key=api_key,
            project_root=cfg.get("project_root", ""),
            event_queue=self.event_queue,
            app_dir=self.app_dir,
            model=model,
            base_url=cfg.get("api_base", "https://api.deepseek.com"),
            max_steps=cfg.get("max_steps", 128),
            enable_web_search=cfg.get("enable_web_search", False),
            confirm_write_delete=cfg.get("confirm_write_delete", True),
            temperature=cfg.get("temperature", 1.0),
            think_level=cfg.get("think_level", "高"),
            max_tokens=cfg.get("max_tokens", 32767),
            task_timeout=cfg.get("task_timeout", 0),
            plugin_manager=self.plugin_manager,
            use_responses_api=cfg.get("use_responses_api", True),
        )

    def send_message(self, text: str) -> str:
        if self.loop and self._loop_thread and self._loop_thread.is_alive():
            return "error:Task already running"
        try:
            self._create_loop()
        except RuntimeError as e:
            return f"error:{str(e)}"

        self.current_messages.append({"role": "user", "content": text})
        
        print("[DEBUG] current_messages 长度:", len(self.current_messages))
        print("[DEBUG] memory_history 长度:", len(self.memory_history))
        
        def _run():
            try:
                current_history = self.current_messages.copy()
                memory_content = self.get_memory_content()
                
                final_reply = self.loop.run(text, history=current_history,
                                            memory_content=memory_content)
                self.conversation_history = self.loop.get_conversation_history()

                is_valid_reply = (
                    final_reply
                    and final_reply not in ("stopped", "timeout", "max_steps")
                    and not final_reply.startswith("__ERROR__")
                )
                if is_valid_reply:
                    self.current_messages.append({"role": "assistant",
                                                  "content": final_reply})
                    self.memory_history.append({"role": "user", "content": text})
                    self.memory_history.append({"role": "assistant",
                                                "content": final_reply})
                    self._trim_memory()
                    self._save_memory()
                elif final_reply in ("stopped", "timeout", "max_steps"):
                    self.current_messages.append({"role": "assistant",
                                                  "content": f"(Task {final_reply})"})
            except Exception:
                err = traceback.format_exc()
                self.event_queue.put(f"E:{err}")
                self.event_queue.signal_finish()

        self._loop_thread = threading.Thread(target=_run, daemon=True)
        self._loop_thread.start()
        return "ok"

    def get_next_event(self) -> Optional[str]:
        if not self.event_queue:
            return None
        return self.event_queue.get()

    def provide_user_input(self, text: str) -> str:
        if not self.loop:
            return "error:No active task"
        self.loop.provide_user_input(text)
        return "ok"

    def stop_task(self) -> str:
        if not self.loop:
            return "error:No active task"
        self.loop.stop()
        return "stopped"

    def get_config(self) -> dict:
        return self.config_manager.load()

    def save_config(self, config: dict) -> str:
        self.config_manager.save(config)
        return "ok"

    def is_first_run(self) -> bool:
        return self.config_manager.is_first_run()

    def reset_config(self) -> dict:
        """重置所有配置为默认值，返回默认配置。"""
        return self.config_manager.reset_to_defaults()

    def set_api_key(self, key: str) -> str:
        cfg = self.config_manager.load()
        base_url = cfg.get("api_base", "https://api.deepseek.com")
        if not key:
            return "error:API key is empty"
        if not self.config_manager.validate_api_key(key, base_url):
            return "error:Invalid API key"
        self.config_manager.set_api_key(key)
        return "ok"

    def validate_api_key(self, key: str, base_url: str) -> str:
        """仅校验 API Key 是否有效，不保存。返回 'ok' 或 'error:...'"""
        if not key or not key.strip():
            return "error:API key is empty"
        if not base_url or not base_url.strip():
            base_url = "https://api.deepseek.com"
        try:
            if self.config_manager.validate_api_key(key.strip(), base_url.strip()):
                return "ok"
            else:
                return "error:Invalid API key or base URL"
        except Exception as e:
            return f"error:{str(e)}"

    def log_frontend_error(self, text: str) -> str:
        try:
            log_path = os.path.join(self.app_dir, "frontend_errors.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {text}\n")
            return "ok"
        except Exception:
            return "error"

    def pick_directory(self) -> str:
        """打开文件夹选择对话框，返回选中路径"""
        import webview
        try:
            result = webview.windows[0].create_file_dialog(
                webview.FileDialog.FOLDER,
                directory=self.config_manager.load().get("project_root", "")
            )
            if result and len(result) > 0:
                return result[0]
            return ""
        except Exception:
            return ""

    def pick_save_file(self) -> str:
        """打开保存文件对话框，返回选中路径"""
        import webview
        try:
            result = webview.windows[0].create_file_dialog(
                webview.FileDialog.SAVE,
                directory=self.config_manager.load().get("project_root", ""),
                file_types=("JSON Files (*.json)", "All Files (*.*)")
            )
            if result and len(result) > 0:
                return result[0]
            return ""
        except Exception:
            return ""

    def pick_open_file(self) -> str:
        """打开文件选择对话框，返回选中路径"""
        import webview
        try:
            result = webview.windows[0].create_file_dialog(
                webview.FileDialog.OPEN,
                directory=self.config_manager.load().get("project_root", ""),
                file_types=("JSON Files (*.json)", "All Files (*.*)")
            )
            if result and len(result) > 0:
                return result[0]
            return ""
        except Exception:
            return ""

    def has_api_key(self) -> bool:
        return self.config_manager.get_api_key() is not None

    def get_balance(self) -> dict:
        cfg = self.config_manager.load()
        base_url = cfg.get("api_base", "https://api.deepseek.com")
        if base_url not in ("https://api.deepseek.com", "https://api.deepseek.com/"):
            return {"error": "Balance query only supports DeepSeek official endpoint"}
        import requests
        url = "https://api.deepseek.com/user/balance"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config_manager.get_api_key() or ''}"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def get_models_with_base(self, base_url: str) -> list:
        key = self.config_manager.get_api_key()
        if not key:
            return {"error": "API key not configured"}
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=base_url)
            models = client.models.list()
            return [{"id": m.id} for m in models.data]
        except Exception as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response:
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get('error', {}).get('message', str(e))
                except:
                    pass
            return {"error": error_msg}


    def upload_files(self, files_data: list) -> list:
        """接收前端 base64 编码的文件列表，解码保存到临时目录并提取文本。
        返回 [{"name":..., "size":..., "type":..., "content":...}, ...]。
        """
        result = []
        for f in files_data:
            try:
                raw = base64.b64decode(f["data"])
                temp_dir = Path(self.app_dir) / "temp"
                temp_dir.mkdir(exist_ok=True)
                temp_path = temp_dir / f["name"]
                with open(temp_path, "wb") as out:
                    out.write(raw)
                text = extract_text_from_file(str(temp_path))
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                result.append({
                    "name": f["name"],
                    "size": f["size"],
                    "type": f.get("type", ""),
                    "content": text
                })
            except Exception as e:
                result.append({
                    "name": f["name"],
                    "error": str(e)
                })
        return result


    def get_last_usage(self) -> dict:
        """返回最近一次 API 调用的 token 用量。"""
        if not self.loop:
            return {}
        return self.loop.get_last_usage()

    def get_total_usage(self) -> dict:
        """返回当前任务的累计 token 用量。"""
        if not self.loop:
            return {}
        return self.loop.get_total_usage()

    # Plugin Management API

    def get_plugins(self) -> list:
        """返回所有已发现插件的元数据列表。"""
        return self.plugin_manager.get_all_plugins()

    def get_plugin_dirs(self) -> list:
        """返回当前配置的插件目录列表。"""
        return self.plugin_manager.plugin_dirs

    def add_plugin_dir(self, path: str) -> str:
        """添加一个插件目录并重新扫描。"""
        cfg = self.config_manager.load()
        dirs = cfg.get("plugin_dirs", [])
        if path not in dirs:
            dirs.append(path)
            cfg["plugin_dirs"] = dirs
            self.config_manager.save(cfg)
            self.plugin_manager.set_plugin_dirs(dirs)
        return "ok"

    def remove_plugin_dir(self, path: str) -> str:
        """移除一个插件目录并重新扫描。"""
        cfg = self.config_manager.load()
        dirs = cfg.get("plugin_dirs", [])
        if path in dirs:
            dirs.remove(path)
            cfg["plugin_dirs"] = dirs
            self.config_manager.save(cfg)
            self.plugin_manager.set_plugin_dirs(dirs)
        return "ok"

    def reload_plugins(self) -> str:
        """重新扫描所有插件目录。"""
        cfg = self.config_manager.load()
        dirs = cfg.get("plugin_dirs", [])
        self.plugin_manager.set_plugin_dirs(dirs)
        return "ok"

    def pick_plugin_dir(self) -> str:
        """打开文件夹选择对话框，返回选中路径（用于选择插件目录）。"""
        import webview
        try:
            result = webview.windows[0].create_file_dialog(
                webview.FileDialog.FOLDER,
                directory=self.config_manager.load().get("project_root", "")
            )
            if result and len(result) > 0:
                return result[0]
            return ""
        except Exception:
            return ""

    # Plugin Security API 

    def get_plugin_audit_results(self) -> dict:
        """返回所有插件的安全审计结果。"""
        return self.plugin_manager.get_audit_results()

    def get_plugin_security_config(self) -> dict:
        """返回当前插件安全配置。"""
        cfg = self.config_manager.load()
        return {
            "audit": cfg.get("plugin_security_audit", "warn"),
            "import_restrict": cfg.get("plugin_security_import_restrict", "off"),
            "require_permissions": cfg.get("plugin_security_require_permissions", False),
            "resource_limit": cfg.get("plugin_security_resource_limit", False),
        }

    def set_plugin_security_config(self, audit: str = "warn",
                                   import_restrict: str = "off",
                                   require_permissions: bool = False,
                                   resource_limit: bool = False) -> str:
        """更新插件安全配置并保存，触发插件重新加载。"""
        cfg = self.config_manager.load()
        cfg["plugin_security_audit"] = audit
        cfg["plugin_security_import_restrict"] = import_restrict
        cfg["plugin_security_require_permissions"] = require_permissions
        cfg["plugin_security_resource_limit"] = resource_limit
        self.config_manager.save(cfg)

        # Update the plugin manager's security module and reload
        self.plugin_manager.update_security_config(cfg)
        self.plugin_manager.set_plugin_dirs(cfg.get("plugin_dirs", []))
        return "ok"
