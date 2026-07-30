# Vibe Coding Agent - 程序入口
# Copyright (c) 2026 xingluosama

import os
import sys
from pathlib import Path

import webview

from api import AgentAPI

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.getcwd())
APP_DIR = os.path.join(LOCALAPPDATA, "vibe_agent")
Path(APP_DIR).mkdir(parents=True, exist_ok=True)

FRONTEND_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "front.html")


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

        window.expose(api.send_message)
        window.expose(api.get_next_event)
        window.expose(api.provide_user_input)
        window.expose(api.stop_task)
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

        webview.start()
    except Exception:
        import traceback
        crash_log = os.path.join(APP_DIR, "crash.log")
        with open(crash_log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
