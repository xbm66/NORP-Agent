# Vibe Coding Agent - 程序入口 (异步架构)
# Copyright (c) 2026 xingluosama

import os
import sys
import asyncio
from pathlib import Path

import webview

from api import AgentAPI
from lifecycle_manager import get_lifecycle_manager
from sandbox_pool import get_sandbox_pool

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.getcwd())
APP_DIR = os.path.join(LOCALAPPDATA, "vibe_agent")
Path(APP_DIR).mkdir(parents=True, exist_ok=True)

# PyInstaller 兼容：打包后资源在 sys._MEIPASS 中
if getattr(sys, 'frozen', False):
    _BASE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FRONTEND_PATH = os.path.join(_BASE_DIR, "front.html")


def load_frontend_html():
    if os.path.exists(FRONTEND_PATH):
        with open(FRONTEND_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Error: front.html not found</h1>"


def main():
    try:
        api = AgentAPI(APP_DIR)
        html = load_frontend_html()

        window = webview.create_window(
            title="NORP Vibe Coding Agent",
            html=html,
            width=1200,
            height=800,
            resizable=True,
            min_size=(800, 500)
        )

        # ── Session management (multi-tab) ──
        window.expose(api.create_session)
        window.expose(api.close_session)
        window.expose(api.get_sessions)
        window.expose(api.set_session_title)
        window.expose(api.set_session_workspace)

        # ── Core task methods ──
        window.expose(api.send_message)
        window.expose(api.get_next_event)
        window.expose(api.provide_user_input)
        window.expose(api.stop_task)

        # ── Config ──
        window.expose(api.get_config)
        window.expose(api.save_config)
        window.expose(api.is_first_run)
        window.expose(api.reset_config)
        window.expose(api.set_api_key)
        window.expose(api.validate_api_key)
        window.expose(api.log_frontend_error)
        window.expose(api.pick_directory)
        window.expose(api.pick_save_file)
        window.expose(api.pick_open_file)
        window.expose(api.has_api_key)
        window.expose(api.get_balance)
        window.expose(api.get_models_with_base)
        window.expose(api.get_last_usage)
        window.expose(api.get_total_usage)
        window.expose(api.upload_files)
        window.expose(api.get_initial_messages)
        window.expose(api.get_memory_content)
        window.expose(api.clear_memory)

        # ── Plugin management ──
        window.expose(api.get_plugins)
        window.expose(api.get_plugin_dirs)
        window.expose(api.add_plugin_dir)
        window.expose(api.remove_plugin_dir)
        window.expose(api.reload_plugins)
        window.expose(api.pick_plugin_dir)

        # ── Plugin security ──
        window.expose(api.get_plugin_audit_results)
        window.expose(api.get_plugin_security_config)
        window.expose(api.set_plugin_security_config)

        # ── Async architecture: lifecycle & sandbox stats ──
        window.expose(api.get_sandbox_pool_stats)
        window.expose(api.get_file_io_stats)
        window.expose(api.get_lifecycle_stats)
        window.expose(api.get_resource_stats)

        # 启动僵尸扫描器（在单独线程中运行）
        import threading
        _scanner_started = False

        def _start_zombie_scanner():
            nonlocal _scanner_started
            if _scanner_started:
                return
            _scanner_started = True
            lm = get_lifecycle_manager()

            def _run_scanner():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(lm.start_zombie_scanner(interval=5.0))

            t = threading.Thread(target=_run_scanner, daemon=True)
            t.start()

        _start_zombie_scanner()

        webview.start()

        # 程序退出时清理
        lm = get_lifecycle_manager()
        lm.shutdown()
        pool = get_sandbox_pool()
        # 在线程中运行异步销毁
        def _cleanup():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(pool.destroy_all())

        cleanup_thread = threading.Thread(target=_cleanup, daemon=True)
        cleanup_thread.start()
        cleanup_thread.join(timeout=5)
    except Exception:
        import traceback
        crash_log = os.path.join(APP_DIR, "crash.log")
        with open(crash_log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
