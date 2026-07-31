# Vibe Coding Agent - Plugin Manager
# Copyright (c) 2026 xingluosama

import importlib.util
import json
import os
import sys
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from plugin_system.context import PluginContext
from plugin_system.security import (
    PluginSecurity, SecurityIssue, Severity,
    PluginImportBlocker, StrictImportBlocker, ResourceLimiter,
)


# ── Hook names (all 15 hooks across 4 layers) ──────────────────────
HOOK_NAMES = [
    # L1 – Lifecycle
    "on_agent_init",
    "on_agent_shutdown",
    # L2 – Task
    "on_task_start",
    "on_task_done",
    "on_task_error",
    "on_task_stopped",
    "on_task_timeout",
    # L3 – Step
    "before_step",
    "after_step",
    "before_tool_call",
    "after_tool_call",
    "on_user_input_required",
    # L4 – Streaming events
    "on_reasoning",
    "on_content",
    "on_event",
    "on_usage_update",
]

# Hooks whose return value can modify the data flow
_MUTATING_HOOKS = {"before_step", "before_tool_call", "after_tool_call"}

# Max seconds a single hook callback is allowed to run
HOOK_TIMEOUT = 5.0


class PluginInfo:
    """Lightweight metadata for one plugin instance."""

    __slots__ = ("name", "path", "version", "publisher", "description",
                 "enabled", "error", "tools", "module", "_hook_names")

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.version = "0.0.0"
        self.publisher = ""
        self.description = ""
        self.enabled = True
        self.error: Optional[str] = None
        self.tools: List[dict] = []
        self.module: Any = None
        self._hook_names: List[str] = []

    @property
    def hook_names(self) -> List[str]:
        return self._hook_names


class PluginManager:
    """
    Discovers, loads, and dispatches external plugins.

    Plugins can be placed in any directory listed in ``plugin_dirs``
    (config.json → plugin_dirs).  Two layout styles are supported::

        plugins/
          my_tool.py              # single-file plugin
          fancy/
            manifest.json         # optional metadata
            plugin.py             # entry point

    Every plugin **must** declare its metadata via module-level constants::

        PLUGIN_NAME = "My Plugin"
        PLUGIN_PUBLISHER = "Author Name"
        PLUGIN_VERSION = "1.0.0"       # optional, default "0.0.0"
        PLUGIN_DESCRIPTION = "..."     # optional, default ""

    Every plugin *may* expose:

    * ``TOOLS`` – a list of OpenAI function-schema dicts
    * ``execute(tool_name, args, context) -> str`` – tool handler
    * Any of the 15 hook functions listed in ``HOOK_NAMES``

    Hooks that can mutate data (``before_step``, ``before_tool_call``,
    ``after_tool_call``) may return a modified value; otherwise their
    return value is ignored.
    """

    def __init__(self, plugin_dirs: List[str], app_dir: str,
                 project_root: str, config: Optional[dict] = None):
        # Normalise plugin directories
        self._plugin_dirs: List[str] = []
        for d in (plugin_dirs or []):
            resolved = os.path.normpath(
                d if os.path.isabs(d) else os.path.join(app_dir, d))
            self._plugin_dirs.append(resolved)

        self.app_dir = app_dir
        self.project_root = project_root

        # Plugin registry
        self._plugins: Dict[str, PluginInfo] = {}
        # tool_name → (plugin_name, execute_fn)
        self._tool_registry: Dict[str, Tuple[str, Optional[Callable]]] = {}
        # hook_name → [(plugin_name, fn), ...]
        self._hooks: Dict[str, List[Tuple[str, Callable]]] = {
            h: [] for h in HOOK_NAMES
        }

        # One context per plugin name, lazily created
        self._contexts: Dict[str, PluginContext] = {}
        self._config_snapshot: dict = {}

        self._lock = threading.Lock()

        # ── Security ──
        self.security = PluginSecurity(config or {})
        self._strict_blocker: Optional[StrictImportBlocker] = None
        self._audit_results: Dict[str, List[dict]] = {}  # plugin_name → issues

    # ── Properties ──────────────────────────────────────────────────

    @property
    def plugin_dirs(self) -> List[str]:
        return list(self._plugin_dirs)

    # ── Public API ──────────────────────────────────────────────────

    def set_plugin_dirs(self, dirs: List[str]):
        """Replace the plugin-directory list and reload everything."""
        self._plugin_dirs = [
            os.path.normpath(
                d if os.path.isabs(d) else os.path.join(self.app_dir, d))
            for d in (dirs or [])
        ]
        self.discover_and_load()

    def discover_and_load(self):
        """Scan all ``plugin_dirs`` and load every valid plugin."""
        # Reset state
        with self._lock:
            self._plugins.clear()
            self._tool_registry.clear()
            for h in HOOK_NAMES:
                self._hooks[h].clear()
            self._contexts.clear()
            self._audit_results.clear()

        # ── Setup import blockers before loading ──
        self._setup_import_blockers()

        try:
            for d in self._plugin_dirs:
                if not os.path.isdir(d):
                    continue
                try:
                    entries = sorted(os.listdir(d))
                except OSError:
                    continue

                for entry in entries:
                    full = os.path.join(d, entry)

                    # ── single .py file ──
                    if entry.endswith(".py") and os.path.isfile(full):
                        if entry == "__init__.py":
                            continue  # skip package init files
                        self._load_from_file(entry[:-3], full)

                    # ── package with manifest.json ──
                    elif os.path.isdir(full):
                        manifest_path = os.path.join(full, "manifest.json")
                        if os.path.isfile(manifest_path):
                            try:
                                with open(manifest_path, "r", encoding="utf-8") as fh:
                                    manifest = json.load(fh)
                            except Exception:
                                continue
                            name = manifest.get("name", entry)
                            entry_file = manifest.get("entry", "plugin.py")
                            entry_path = os.path.join(full, entry_file)
                            if os.path.isfile(entry_path):
                                self._load_from_file(name, entry_path,
                                                     manifest=manifest)
        finally:
            # Always tear down blockers
            self._teardown_import_blockers()

    # ── Import blocker management ─────────────────────────────────

    def _setup_import_blockers(self):
        """Register import blockers based on current security config."""
        # If strict mode, use StrictImportBlocker
        if self.security.import_restriction == "strict":
            self._strict_blocker = StrictImportBlocker("vibe_plugin_")
            self._strict_blocker.register()
        else:
            self.security.enable_import_blocker()

    def _teardown_import_blockers(self):
        """Unregister all import blockers."""
        self.security.disable_import_blocker()
        if self._strict_blocker:
            self._strict_blocker.unregister()
            self._strict_blocker = None

    def update_security_config(self, config: dict):
        """Update security settings and re-create the security module."""
        self.security = PluginSecurity(config or {})

    def get_audit_results(self) -> Dict[str, List[dict]]:
        """Return security audit results for all plugins (keyed by name)."""
        with self._lock:
            return dict(self._audit_results)

    def get_tools(self) -> List[dict]:
        """Return the merged tool definitions from all enabled plugins."""
        tools: List[dict] = []
        with self._lock:
            for info in self._plugins.values():
                if info.enabled and info.tools:
                    tools.extend(info.tools)
        return tools

    def get_all_plugins(self) -> List[dict]:
        """Return metadata for every discovered plugin (for the front-end)."""
        result: List[dict] = []
        with self._lock:
            for info in self._plugins.values():
                entry = {
                    "name": info.name,
                    "version": info.version,
                    "publisher": info.publisher,
                    "description": info.description,
                    "enabled": info.enabled,
                    "error": info.error,
                    "path": info.path,
                    "tool_count": len(info.tools) if info.tools else 0,
                    "hook_count": len(info.hook_names),
                    "hook_names": info.hook_names,
                }
                # Attach audit results if available
                audit = self._audit_results.get(info.name)
                if audit:
                    entry["audit_issues"] = audit
                    entry["audit_critical"] = sum(
                        1 for i in audit if i.get("severity") == "critical")
                    entry["audit_warning"] = sum(
                        1 for i in audit if i.get("severity") == "warning")
                    entry["audit_info"] = sum(
                        1 for i in audit if i.get("severity") == "info")
                result.append(entry)
        return result

    def execute(self, tool_name: str, args: dict) -> str:
        """Dispatch a tool call to the plugin that registered it."""
        with self._lock:
            entry = self._tool_registry.get(tool_name)

        if entry is None:
            return f"Error: unknown plugin tool '{tool_name}'"

        plugin_name, execute_fn = entry
        if not callable(execute_fn):
            return f"Error: plugin '{plugin_name}' has no execute() function"

        ctx = self._get_context(plugin_name)
        ctx.current_step = getattr(self, "_step", 0)
        try:
            return execute_fn(tool_name, args, ctx)
        except Exception:
            return f"Plugin execution failed:\n{traceback.format_exc()}"

    def update_config_snapshot(self, config: dict):
        """Refresh the read-only config snapshot shared with plugins."""
        self._config_snapshot = config.copy() if config else {}

    def set_step(self, step: int):
        self._step = step

    # ── Hook dispatchers (one per hook) ─────────────────────────────

    def fire_agent_init(self):
        self._broadcast("on_agent_init", lambda ctx: ctx)

    def fire_agent_shutdown(self):
        self._broadcast("on_agent_shutdown", lambda ctx: ctx)

    def fire_task_start(self, task_text: str):
        self._broadcast("on_task_start", lambda ctx: (task_text, ctx))

    def fire_task_done(self, summary: str, final_reply: str):
        self._broadcast("on_task_done", lambda ctx: (summary, final_reply, ctx))

    def fire_task_error(self, error_msg: str):
        self._broadcast("on_task_error", lambda ctx: (error_msg, ctx))

    def fire_task_stopped(self):
        self._broadcast("on_task_stopped", lambda ctx: ctx)

    def fire_task_timeout(self, elapsed: float):
        self._broadcast("on_task_timeout", lambda ctx: (elapsed, ctx))

    def fire_before_step(self, step: int, messages: list) -> list:
        """Return (possibly modified) messages list."""
        self.set_step(step)
        result = self._broadcast_mutating(
            "before_step", lambda ctx: (step, messages, ctx))
        if result is not None and isinstance(result, list):
            return result
        return messages

    def fire_after_step(self, step: int, reasoning: str, content: str,
                        tool_calls: list):
        self.set_step(step)
        self._broadcast("after_step",
                         lambda ctx: (step, reasoning, content, tool_calls, ctx))

    def fire_before_tool_call(self, tool_name: str, args: dict) -> Optional[dict]:
        """
        Called right before a tool executes.

        Returns
        -------
        dict or None
            * ``dict`` – (possibly modified) arguments → proceed.
            * ``None`` – the call is **blocked** (only when a listener
              explicitly returns a non-dict sentinel; no listeners = passed through).
        """
        # Short-circuit: no listeners → don't block
        listeners = self._hooks.get("before_tool_call", [])
        if not listeners:
            return args

        result = self._broadcast_mutating(
            "before_tool_call", lambda ctx: (tool_name, args, ctx))
        if result is None:
            return args  # all listeners returned None → pass through
        if isinstance(result, dict):
            return result
        return args  # unrecognised return value → pass through

    def fire_after_tool_call(self, tool_name: str, args: dict,
                             result: str) -> str:
        """Return (possibly modified) tool result string."""
        hook_result = self._broadcast_mutating(
            "after_tool_call", lambda ctx: (tool_name, args, result, ctx))
        if hook_result is not None and isinstance(hook_result, str):
            return hook_result
        return result

    def fire_user_input_required(self, question: str):
        self._broadcast("on_user_input_required",
                         lambda ctx: (question, ctx))

    def fire_reasoning(self, token: str):
        self._broadcast("on_reasoning", lambda ctx: (token, ctx))

    def fire_content(self, token: str):
        self._broadcast("on_content", lambda ctx: (token, ctx))

    def fire_event(self, event_type: str, data: str):
        self._broadcast("on_event", lambda ctx: (event_type, data, ctx))

    def fire_usage_update(self, usage: dict):
        self._broadcast("on_usage_update", lambda ctx: (usage, ctx))

    # ── Internal helpers ────────────────────────────────────────────

    def _load_from_file(self, name: str, path: str, *,
                        manifest: dict = None):
        """Import a plugin module and register its tools + hooks."""
        info = PluginInfo(name, path)

        if manifest:
            info.version = manifest.get("version", info.version)
            info.publisher = manifest.get("publisher", manifest.get("author", info.publisher))
            info.description = manifest.get("description", info.description)
            if "enabled" in manifest:
                info.enabled = bool(manifest["enabled"])

        if not info.enabled:
            with self._lock:
                self._plugins[name] = info
            return

        # ── Security audit (before loading) ──
        audit_issues, audit_allowed = self.security.audit_file(path)
        self._audit_results[name] = [i.to_dict() for i in audit_issues]

        if not audit_allowed:
            criticals = [i for i in audit_issues if i.severity == Severity.CRITICAL]
            error_lines = "\n".join(
                f"  L{i.line}: [{i.category}] {i.message}"
                for i in criticals[:5]
            )
            info.error = (
                f"Security audit blocked ({len(criticals)} critical issue(s)):\n"
                f"{error_lines}"
            )
            info.enabled = False
            with self._lock:
                self._plugins[name] = info
            return

        # If only warnings, log them but proceed
        if audit_issues and self.security.audit_level == "warn":
            warnings = [i for i in audit_issues if i.severity == Severity.WARNING]
            if warnings:
                print(f"[PluginSecurity] {name}: {len(warnings)} warning(s) "
                      f"({len(audit_issues)} total).  "
                      f"Set audit=block to reject such plugins.")

        # ── Permission check ──
        if manifest and not self.security.check_permissions(manifest, audit_issues):
            info.error = "Missing required permissions (see audit log)"
            info.enabled = False
            with self._lock:
                self._plugins[name] = info
            return

        try:
            spec = importlib.util.spec_from_file_location(
                f"vibe_plugin_{name}", path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load module spec for {path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module

            # ── Resource limits (if enabled) ──
            limiter = None
            if self.security.resource_limit:
                limiter = ResourceLimiter(max_memory_mb=512, max_cpu_seconds=30)
                limiter.enable()

            try:
                spec.loader.exec_module(module)
            finally:
                if limiter:
                    limiter.disable()

            info.module = module

            # ── Read plugin header metadata ──
            plugin_name_from_header = getattr(module, "PLUGIN_NAME", None)
            if plugin_name_from_header and isinstance(plugin_name_from_header, str) and plugin_name_from_header.strip():
                info.name = plugin_name_from_header.strip()
                # Re-check if this plugin name is already registered
                # (the caller passed filename-based name; update registry key if needed)

            plugin_publisher = getattr(module, "PLUGIN_PUBLISHER", None)
            if plugin_publisher and isinstance(plugin_publisher, str):
                info.publisher = plugin_publisher.strip()

            plugin_version = getattr(module, "PLUGIN_VERSION", None)
            if plugin_version and isinstance(plugin_version, str) and not manifest:
                # manifest version takes priority; only use header if no manifest
                info.version = plugin_version.strip()

            plugin_desc = getattr(module, "PLUGIN_DESCRIPTION", None)
            if plugin_desc and isinstance(plugin_desc, str) and not manifest:
                info.description = plugin_desc.strip()

            # ── tools ──
            tools = getattr(module, "TOOLS", None)
            if isinstance(tools, list):
                info.tools = tools
            else:
                info.tools = []

            execute_fn = getattr(module, "execute", None)

            # Use the resolved name for registration
            resolved_name = info.name

            with self._lock:
                for tool in info.tools:
                    func = tool.get("function", {})
                    tname = func.get("name", "")
                    if tname:
                        # Guard against duplicate tool names
                        if tname in self._tool_registry:
                            existing = self._tool_registry[tname][0]
                            raise RuntimeError(
                                f"Tool '{tname}' already registered by "
                                f"plugin '{existing}'")
                        self._tool_registry[tname] = (resolved_name, execute_fn)

                # ── hooks ──
                for hook_name in HOOK_NAMES:
                    fn = getattr(module, hook_name, None)
                    if callable(fn):
                        self._hooks[hook_name].append((resolved_name, fn))
                        info._hook_names.append(hook_name)

        except ImportError as exc:
            # Import blocked by security – surface clearly
            info.error = f"Import blocked: {exc}"
            info.enabled = False
            traceback.print_exc()
        except Exception as exc:
            info.error = str(exc)
            info.enabled = False
            traceback.print_exc()

        # Skip files that don't define any plugin interface
        if not info.tools and not info.hook_names and not callable(getattr(info.module, 'execute', None)):
            return  # not a plugin — silently skip

        with self._lock:
            self._plugins[info.name] = info

    def _get_context(self, plugin_name: str) -> PluginContext:
        """Return (or lazily create) the PluginContext for *plugin_name*."""
        if plugin_name not in self._contexts:
            self._contexts[plugin_name] = PluginContext(
                plugin_name=plugin_name,
                project_root=self.project_root,
                app_dir=self.app_dir,
                config=self._config_snapshot,
            )
        ctx = self._contexts[plugin_name]
        # Always refresh the config snapshot
        ctx.config = self._config_snapshot.copy() if self._config_snapshot else {}
        return ctx

    def _broadcast(self, hook_name: str, build_args: Callable):
        """Call every listener for *hook_name* (fire-and-forget)."""
        listeners: List[Tuple[str, Callable]] = []
        with self._lock:
            listeners = list(self._hooks.get(hook_name, []))

        for plugin_name, fn in listeners:
            ctx = self._get_context(plugin_name)
            try:
                args = build_args(ctx)
                if isinstance(args, tuple):
                    self._call_with_timeout(fn, *args)
                else:
                    self._call_with_timeout(fn, args)
            except Exception:
                pass  # hook errors never crash the agent

    def _broadcast_mutating(self, hook_name: str, build_args: Callable):
        """
        Like _broadcast, but the **first** non-None return value wins.
        Used for hooks that can modify the data flow.
        """
        listeners: List[Tuple[str, Callable]] = []
        with self._lock:
            listeners = list(self._hooks.get(hook_name, []))

        for plugin_name, fn in listeners:
            ctx = self._get_context(plugin_name)
            try:
                args = build_args(ctx)
                if isinstance(args, tuple):
                    result = self._call_with_timeout(fn, *args)
                else:
                    result = self._call_with_timeout(fn, args)
                if result is not None:
                    return result
            except Exception:
                continue
        return None

    @staticmethod
    def _call_with_timeout(fn: Callable, *args, **kwargs):
        """Call *fn* in a daemon thread with a hard timeout."""
        result_holder = [None]
        error_holder: List[Optional[Exception]] = [None]
        done = threading.Event()

        def _target():
            try:
                result_holder[0] = fn(*args, **kwargs)
            except Exception as exc:
                error_holder[0] = exc
            done.set()

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        done.wait(timeout=HOOK_TIMEOUT)

        if not done.is_set():
            # Timeout – do NOT join; just abandon the thread
            return None

        if error_holder[0] is not None:
            raise error_holder[0]

        return result_holder[0]
