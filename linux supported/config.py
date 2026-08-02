# Vibe Coding Agent - 配置管理
# Copyright (c) 2026 xingluosama

import os
import json
import base64
import sys
import platform
from pathlib import Path
from typing import Any, Dict, Optional

import keyring

# ── 跨平台 API Key 加密 ──────────────────────────────────────────
# Windows: win32crypt (DPAPI)
# Linux/macOS: cryptography.fernet (Fernet 对称加密)
_IS_WINDOWS = platform.system() == "Windows"

if _IS_WINDOWS:
    import win32crypt
else:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        Fernet = None  # 延迟报错，仅在实际加解密时触发

KEYRING_SERVICE = "vibe_agent"
KEYRING_USER = "api_key"


def _get_fernet_key_path(app_dir: str) -> str:
    """获取 Fernet 密钥文件路径（Linux/macOS）。"""
    return os.path.join(app_dir, ".fernet_key")


def _get_or_create_fernet(app_dir: str):
    """获取或创建 Fernet 实例（Linux/macOS）。"""
    if Fernet is None:
        raise RuntimeError(
            "cryptography 库未安装。请运行: pip install cryptography"
        )
    key_path = _get_fernet_key_path(app_dir)
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        os.makedirs(app_dir, exist_ok=True)
        # 设置仅用户可读的权限（Unix）
        with open(key_path, "wb") as f:
            f.write(key)
        if not _IS_WINDOWS:
            os.chmod(key_path, 0o600)
    return Fernet(key)


VALID_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}


class ConfigManager:

    def __init__(self, app_dir: str):
        self.app_dir = app_dir
        self.config_path = os.path.join(app_dir, "config.json")
        self.key_path = os.path.join(app_dir, "base.env")
        Path(app_dir).mkdir(parents=True, exist_ok=True)
        # Linux 默认使用 fernet 加密（无需 DPAPI）
        _default_encryption = "win32crypt" if _IS_WINDOWS else "fernet"
        self.defaults = {
            "language": "zh_CN",
            "model": "deepseek-v4-pro",
            "use_responses_api": True,
            "encryption_method": _default_encryption,
            "api_base": "https://api.deepseek.com",
            "project_root": os.path.join(os.path.expanduser("~"), "vibe_workspace"),
            "queue_max_size": 2000,
            "max_steps": 128,
            "enable_web_search": False,
            "confirm_write_delete": True,
            "temperature": 1.0,
            "think_level": "高",
            "max_tokens": 32767,
            "task_timeout": 0,

            "memory": False,
            "memory_mode": "full",
            "max_rounds": 10,

            # Plugin system
            "plugins_enabled": True,
            "plugin_dirs": [],

            # Plugin security
            "plugin_security_audit": "warn",
            "plugin_security_import_restrict": "off",
            "plugin_security_require_permissions": False,
            "plugin_security_resource_limit": False,

            # 异步架构：沙箱池 & 文件IO队列
            "sandbox_pool_max": 8,
            "sandbox_network_enabled": False,
            "file_io_queue_enabled": True,
            "lifecycle_zombie_scan_seconds": 5,
            "resource_terminal_reserved_pct": 40,
        }

    def load(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in self.defaults.items():
                cfg.setdefault(k, v)
            self._sanitize(cfg)
            return cfg
        return self.defaults.copy()

    def _sanitize(self, cfg: Dict[str, Any]):
        """修复被污染 / 为空的配置项，回退到默认值。"""
        model = cfg.get("model", "")
        if not model or model.strip() == "." or model.strip() == "":
            cfg["model"] = self.defaults["model"]
        elif model not in VALID_MODELS:
            stripped = model.strip()
            if len(stripped) < 2:
                cfg["model"] = self.defaults["model"]
            else:
                cfg["model"] = stripped

        api_base = cfg.get("api_base", "")
        if not api_base or not api_base.strip():
            cfg["api_base"] = self.defaults["api_base"]

        timeout = cfg.get("task_timeout", 0)
        if not isinstance(timeout, (int, float)) or timeout < 0:
            cfg["task_timeout"] = 0

    def save(self, config: Dict[str, Any]):
        self._sanitize(config)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def get_api_key(self) -> Optional[str]:
        cfg = self.load()
        method = cfg.get("encryption_method", "win32crypt")
        # ── 平台兼容：Linux/macOS 上自动回退到 fernet ──
        if method == "win32crypt" and not _IS_WINDOWS:
            method = "fernet"

        if method == "keyring":
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        elif method == "fernet":
            # Linux/macOS: Fernet 对称加密
            if not os.path.exists(self.key_path):
                return None
            try:
                fernet = _get_or_create_fernet(self.app_dir)
                with open(self.key_path, "rb") as f:
                    encrypted = f.read()
                decrypted = fernet.decrypt(encrypted)
                return decrypted.decode("utf-8")
            except Exception:
                return None
        else:
            # Windows: win32crypt (DPAPI)
            if not _IS_WINDOWS:
                # 不应到达此处，但作为防御
                return None
            if not os.path.exists(self.key_path):
                return None
            with open(self.key_path, "rb") as f:
                encrypted = base64.b64decode(f.read())
            decrypted = win32crypt.CryptUnprotectData(
                encrypted, None, None, None, 0
            )
            return decrypted[1].decode("utf-8")

    def set_api_key(self, key: str):
        cfg = self.load()
        method = cfg.get("encryption_method", "win32crypt")
        # ── 平台兼容：Linux/macOS 上自动回退到 fernet ──
        if method == "win32crypt" and not _IS_WINDOWS:
            method = "fernet"
            # 持久化修正：下次读取时无需再次回退
            cfg["encryption_method"] = "fernet"
            self.save(cfg)

        if method == "keyring":
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            if os.path.exists(self.key_path):
                os.remove(self.key_path)
        elif method == "fernet":
            # Linux/macOS: Fernet 对称加密
            fernet = _get_or_create_fernet(self.app_dir)
            encrypted = fernet.encrypt(key.encode("utf-8"))
            # 设置仅用户可读的权限
            with open(self.key_path, "wb") as f:
                f.write(encrypted)
            if not _IS_WINDOWS:
                os.chmod(self.key_path, 0o600)
        else:
            # Windows: win32crypt (DPAPI)
            encrypted = win32crypt.CryptProtectData(
                key.encode("utf-8"), None, None, None, None, 0
            )
            with open(self.key_path, "wb") as f:
                f.write(base64.b64encode(encrypted))

    def is_first_run(self) -> bool:
        """检查是否config.json 不存在"""
        return not os.path.exists(self.config_path)

    def reset_to_defaults(self) -> Dict[str, Any]:
        """将所有配置重置为默认值并保存。"""
        defaults = self.defaults.copy()
        self.save(defaults)
        return defaults

    def validate_api_key(self, api_key: str, base_url: str = "https://api.deepseek.com") -> bool:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
            client.models.list()
            return True
        except Exception:
            return False
