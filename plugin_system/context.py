# Vibe Coding Agent - Plugin Context & Logger
# Copyright (c) 2026 xingluosama

import os
from datetime import datetime
from typing import Dict


class SimpleLogger:
    """A minimal file+print logger isolated per plugin."""

    def __init__(self, plugin_name: str, log_dir: str = ""):
        self._name = plugin_name
        self._log_dir = log_dir

    def _emit(self, level: str, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] [{self._name}] {msg}"
        print(line)
        if self._log_dir:
            try:
                log_path = os.path.join(self._log_dir, "plugin.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def info(self, msg: str):   self._emit("INFO", msg)
    def warn(self, msg: str):   self._emit("WARN", msg)
    def error(self, msg: str):  self._emit("ERROR", msg)
    def debug(self, msg: str):  self._emit("DEBUG", msg)


class PluginContext:
    """
    Read-only context passed to every plugin hook.

    Plugins can read environment info and store temporary state in ``storage``
    (a plain dict that lives for the lifetime of the AgentLoop).

    Attributes
    ----------
    project_root : str
        Absolute path of the current workspace / project root.
    app_dir : str
        Application data directory (config, memories, logs).
    config : dict
        Read-only snapshot of the current config.json.
    storage : dict
        Per-plugin key-value store (survives across hooks within one task).
    logger : SimpleLogger
        A simple logger instance pre-configured for the plugin.
    current_step : int
        The current ReAct step (updated by AgentLoop).
    total_usage : dict
        Cumulative token usage (updated by AgentLoop).
    """

    def __init__(self, plugin_name: str, project_root: str,
                 app_dir: str, config: dict):
        self.project_root = project_root
        self.app_dir = app_dir
        self.config = config.copy() if config else {}
        self.storage: Dict = {}
        self.logger = SimpleLogger(plugin_name, app_dir)
        self.current_step = 0
        self.total_usage = {"input_tokens": 0, "output_tokens": 0,
                            "tool_call_tokens": 0}
