#!/usr/bin/env bash
# =============================================================================
# NORP Agent - Linux 系统安装脚本
# Copyright (c) 2026 xingluosama
#
# 用法:
#   sudo ./install.sh                    → 安装到 /opt/norp-agent
#   ./install.sh --user                  → 安装到 ~/.local
#   ./install.sh --uninstall             → 卸载
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist/norp-agent"
APP_NAME="norp-agent"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
log_info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
log_err()  { echo -e "${RED}[ERROR]${NC} $*"; }

SYSTEM_MODE=true
UNINSTALL=false

# 解析参数
for arg in "$@"; do
    case "$arg" in
        --user)    SYSTEM_MODE=false ;;
        --uninstall) UNINSTALL=true ;;
    esac
done

# ────────────────────────────────────────────────────────────────────────────
# 卸载
# ────────────────────────────────────────────────────────────────────────────
do_uninstall() {
    log_info "卸载 NORP Agent..."

    if $SYSTEM_MODE; then
        if [[ -d "/opt/$APP_NAME" ]]; then
            rm -rf "/opt/$APP_NAME"
            log_ok "已删除 /opt/$APP_NAME"
        fi
        rm -f "/usr/local/bin/$APP_NAME"
        rm -f "/usr/local/share/applications/$APP_NAME.desktop"
        rm -f "/usr/local/share/icons/hicolor/scalable/apps/$APP_NAME.svg"
        log_ok "已删除系统级快捷方式"
    else
        rm -rf "$HOME/.local/share/$APP_NAME"
        rm -f "$HOME/.local/bin/$APP_NAME"
        rm -f "$HOME/.local/share/applications/$APP_NAME.desktop"
        rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_NAME.svg"
        log_ok "已删除用户级安装"
    fi

    # 更新桌面数据库
    update-desktop-database ~/.local/share/applications/ 2>/dev/null || true

    log_ok "卸载完成"
    exit 0
}

# ────────────────────────────────────────────────────────────────────────────
# 安装
# ────────────────────────────────────────────────────────────────────────────
do_install() {
    if [[ ! -d "$DIST_DIR" ]]; then
        log_err "未找到打包产物: $DIST_DIR"
        log_info "请先运行: cd build && ./build.sh"
        exit 1
    fi

    if $SYSTEM_MODE; then
        # 系统级安装
        log_info "系统级安装 → /opt/$APP_NAME"

        if [[ $EUID -ne 0 ]]; then
            log_err "系统级安装需要 root 权限，请使用 sudo"
            exit 1
        fi

        # 复制应用
        rm -rf "/opt/$APP_NAME"
        cp -r "$DIST_DIR" "/opt/$APP_NAME"
        chmod -R 755 "/opt/$APP_NAME"

        # 创建可执行链接
        cat > "/usr/local/bin/$APP_NAME" << BINEOF
#!/bin/bash
exec /opt/$APP_NAME/$APP_NAME "\$@"
BINEOF
        chmod +x "/usr/local/bin/$APP_NAME"

        # 安装 .desktop
        mkdir -p /usr/local/share/applications
        cp "$SCRIPT_DIR/$APP_NAME.desktop" "/usr/local/share/applications/"

        # 安装图标
        mkdir -p /usr/local/share/icons/hicolor/scalable/apps
        cp "$SCRIPT_DIR/$APP_NAME.svg" "/usr/local/share/icons/hicolor/scalable/apps/"

        log_ok "系统级安装完成"
        log_info "启动命令: $APP_NAME"

    else
        # 用户级安装
        log_info "用户级安装 → ~/.local"

        local APP_HOME="$HOME/.local/share/$APP_NAME"
        local BIN_DIR="$HOME/.local/bin"
        local DESKTOP_DIR="$HOME/.local/share/applications"
        local ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

        mkdir -p "$APP_HOME" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

        cp -r "$DIST_DIR/"* "$APP_HOME/"
        chmod -R 755 "$APP_HOME"

        # 确保 ~/.local/bin 在 PATH 中
        if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
            log_warn "~/.local/bin 不在 PATH 中，添加到 ~/.bashrc"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
        fi

        cat > "$BIN_DIR/$APP_NAME" << BINEOF
#!/bin/bash
exec $APP_HOME/$APP_NAME "\$@"
BINEOF
        chmod +x "$BIN_DIR/$APP_NAME"

        cp "$SCRIPT_DIR/$APP_NAME.desktop" "$DESKTOP_DIR/"
        sed -i "s|Exec=$APP_NAME|Exec=$BIN_DIR/$APP_NAME|" "$DESKTOP_DIR/$APP_NAME.desktop"

        cp "$SCRIPT_DIR/$APP_NAME.svg" "$ICON_DIR/"

        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

        log_ok "用户级安装完成"
        log_info "启动命令: $APP_NAME"
        log_info "（如果找不到命令，请执行: source ~/.bashrc）"
    fi

    # 更新图标缓存
    if command -v gtk-update-icon-cache &>/dev/null; then
        gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true
    fi
}

# ────────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────────
if $UNINSTALL; then
    do_uninstall
else
    do_install
fi
