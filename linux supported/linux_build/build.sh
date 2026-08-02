#!/usr/bin/env bash
# =============================================================================
# NORP Agent - Linux 打包脚本（默认产出可直接双击运行的 AppImage）
# Copyright (c) 2026 xingluosama
#
# 用法:
#   ./build.sh                 → 一键构建 AppImage（双击运行）
#   ./build.sh portable        → 便携目录 + .desktop 启动器
#   ./build.sh tarball         → AppImage + .tar.gz 分发包
#   ./build.sh clean           → 清理构建产物
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build"
DIST_DIR="$PROJECT_DIR/dist"
OUTPUT_NAME="norp-agent"
VERSION="${VERSION:-1.0.0}"
APPIMAGE_TOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"

# 如果 appimagetool 已下载，直接使用；否则自动下载
APPIMAGE_DOWNLOAD_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"

cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# ────────────────────────────────────────────────────────────────────────────
# 环境检查
# ────────────────────────────────────────────────────────────────────────────
check_deps() {
    log_info "检查打包依赖..."

    local missing=()

    if ! command -v python3 &>/dev/null; then
        missing+=("python3")
    fi

    if ! python3 -c "import PyInstaller" &>/dev/null 2>&1; then
        missing+=("pyinstaller (pip install pyinstaller)")
    fi

    # GTK/WebKit2 (pywebview Linux 后端)
    if ! python3 -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk" &>/dev/null 2>&1; then
        log_warn "GTK3 Python 绑定不可用（pywebview 需要）"
        log_warn "  Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.0"
        log_warn "  Fedora:        sudo dnf install python3-gobject gtk3 webkit2gtk3"
        log_warn "  Arch:          sudo pacman -S python-gobject gtk3 webkit2gtk"
        log_warn "  打包可继续，但运行时需要这些库"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_err "缺少依赖:"
        for m in "${missing[@]}"; do
            echo "  - $m"
        done
        exit 1
    fi

    log_ok "依赖检查通过"
}

# ────────────────────────────────────────────────────────────────────────────
# 下载 appimagetool（幂等）
# ────────────────────────────────────────────────────────────────────────────
ensure_appimagetool() {
    if [[ -f "$APPIMAGE_TOOL" ]] && [[ -x "$APPIMAGE_TOOL" ]]; then
        log_info "appimagetool 已就绪: $APPIMAGE_TOOL"
        return 0
    fi

    if command -v wget &>/dev/null; then
        log_info "下载 appimagetool..."
        mkdir -p "$BUILD_DIR"
        wget -q --show-progress -O "$APPIMAGE_TOOL" "$APPIMAGE_DOWNLOAD_URL" || {
            log_err "下载失败，请手动下载并放到: $APPIMAGE_TOOL"
            log_err "地址: $APPIMAGE_DOWNLOAD_URL"
            exit 1
        }
        chmod +x "$APPIMAGE_TOOL"
        log_ok "appimagetool 下载完成"
    elif command -v curl &>/dev/null; then
        log_info "下载 appimagetool..."
        mkdir -p "$BUILD_DIR"
        curl -fSL --progress-bar -o "$APPIMAGE_TOOL" "$APPIMAGE_DOWNLOAD_URL" || {
            log_err "下载失败，请手动下载并放到: $APPIMAGE_TOOL"
            log_err "地址: $APPIMAGE_DOWNLOAD_URL"
            exit 1
        }
        chmod +x "$APPIMAGE_TOOL"
        log_ok "appimagetool 下载完成"
    else
        log_err "需要 wget 或 curl 来下载 appimagetool"
        log_err "请手动下载并放到: $APPIMAGE_TOOL"
        log_err "地址: $APPIMAGE_DOWNLOAD_URL"
        exit 1
    fi
}

# ────────────────────────────────────────────────────────────────────────────
# PyInstaller 打包（生成目录）
# ────────────────────────────────────────────────────────────────────────────
build_pyinstaller() {
    log_info "PyInstaller 打包中..."

    mkdir -p "$DIST_DIR"

    pyinstaller \
        --clean \
        --noconfirm \
        --distpath "$DIST_DIR" \
        --workpath "$BUILD_DIR/pyinstaller_work" \
        "$SCRIPT_DIR/agent.spec"

    log_ok "PyInstaller 打包完成 → $DIST_DIR/$OUTPUT_NAME/"
}

# ────────────────────────────────────────────────────────────────────────────
# 在便携目录中嵌入 .desktop 启动器，让文件管理器可双击图标启动
# ────────────────────────────────────────────────────────────────────────────
embed_desktop_launcher() {
    local target_dir="${1:-$DIST_DIR/$OUTPUT_NAME}"

    log_info "嵌入 .desktop 启动器到 $target_dir..."

    # 复制图标
    mkdir -p "$target_dir/share/icons/hicolor/scalable/apps/"
    if [[ -f "$SCRIPT_DIR/$OUTPUT_NAME.svg" ]]; then
        cp "$SCRIPT_DIR/$OUTPUT_NAME.svg" "$target_dir/share/icons/hicolor/scalable/apps/"
        cp "$SCRIPT_DIR/$OUTPUT_NAME.svg" "$target_dir/"
    fi

    # 生成自定位 .desktop 文件（使用相对路径，可在任意位置双击）
    cat > "$target_dir/$OUTPUT_NAME.desktop" << DESKTOPEOF
[Desktop Entry]
Type=Application
Name=NORP Agent
Name[zh_CN]=NORP Agent - 编程助手
Comment=Vibe Coding Agent with multi-provider AI support
Comment[zh_CN]=支持多 AI 提供商的 Vibe Coding 编程助手
Exec=$OUTPUT_NAME
Icon=$OUTPUT_NAME
Path=$target_dir
Terminal=false
Categories=Development;IDE;
Keywords=AI;code;programming;assistant;agent;
StartupWMClass=$OUTPUT_NAME
StartupNotify=true
Version=$VERSION
DESKTOPEOF

    # 同时创建启动脚本（兜底方案：终端用户可直接双击 .sh）
    cat > "$target_dir/start.sh" << 'STARTEOF'
#!/bin/bash
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$HERE/norp-agent" "$@"
STARTEOF
    chmod +x "$target_dir/start.sh"

    log_ok ".desktop 启动器已嵌入"
}

# ────────────────────────────────────────────────────────────────────────────
# 构建 AppImage（默认目标：双击运行）
# ────────────────────────────────────────────────────────────────────────────
build_appimage() {
    local appimage_out="$DIST_DIR/${OUTPUT_NAME}-${VERSION}-x86_64.AppImage"

    # 检查是否已有构建产物
    if [[ -f "$appimage_out" ]]; then
        log_info "AppImage 已存在，跳过构建: $appimage_out"
        log_info "如需重新构建请先执行: $0 clean"
        ls -lh "$appimage_out"
        return 0
    fi

    ensure_appimagetool

    # 先做 PyInstaller 打包
    build_pyinstaller

    log_info "构建 AppImage..."

    local APPDIR="$BUILD_DIR/AppDir"
    rm -rf "$APPDIR"
    mkdir -p "$APPDIR"

    # 复制 PyInstaller 产物
    cp -r "$DIST_DIR/$OUTPUT_NAME/"* "$APPDIR/"

    # ═══════════════════════════════════════════════════════════════════
    # AppRun：入口脚本，支持 --appimage-extract-and-run（无需 FUSE）
    # ═══════════════════════════════════════════════════════════════════
    cat > "$APPDIR/AppRun" << 'APPRUNEOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE:$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/lib:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$HERE/usr/lib/girepository-1.0:${GI_TYPELIB_PATH:-}"

# 将 AppDir 内的库加入环境
if [ -d "$HERE/usr/lib" ]; then
    export LD_LIBRARY_PATH="$HERE/usr/lib:$HERE/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
fi
if [ -d "$HERE/usr/lib/girepository-1.0" ]; then
    export GI_TYPELIB_PATH="$HERE/usr/lib/girepository-1.0:${GI_TYPELIB_PATH:-}"
fi

exec "$HERE/norp-agent" "$@"
APPRUNEOF
    chmod +x "$APPDIR/AppRun"

    # .desktop 文件
    cp "$SCRIPT_DIR/$OUTPUT_NAME.desktop" "$APPDIR/"

    # 图标（多尺寸兼容）
    mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
    if [[ -f "$SCRIPT_DIR/$OUTPUT_NAME.svg" ]]; then
        cp "$SCRIPT_DIR/$OUTPUT_NAME.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
        cp "$SCRIPT_DIR/$OUTPUT_NAME.svg" "$APPDIR/$OUTPUT_NAME.svg"
        cp "$SCRIPT_DIR/$OUTPUT_NAME.svg" "$APPDIR/.DirIcon"
    fi

    # 构建 AppImage
    log_info "打包 AppImage（此过程可能需要几分钟）..."
    ARCH=x86_64 "$APPIMAGE_TOOL" --comp gzip "$APPDIR" "$appimage_out" 2>&1 | while IFS= read -r line; do
        # 过滤掉常见的非关键警告
        [[ "$line" == *"WARNING"* ]] && continue
        [[ "$line" == *"desktop file"*"not found"* ]] && continue
        echo "  $line"
    done

    if [[ -f "$appimage_out" ]]; then
        chmod +x "$appimage_out"
        log_ok "AppImage 构建完成！"
        echo ""
        echo -e "  ${BOLD}${GREEN}✔ ${appimage_out}${NC}"
        ls -lh "$appimage_out"
        echo ""
        echo -e "  ${CYAN}使用方法:${NC}"
        echo -e "    1. 在文件管理器中双击 ${BOLD}$(basename "$appimage_out")${NC}"
        echo -e "    2. 或命令行: ${BOLD}./$(basename "$appimage_out")${NC}"
        echo -e "    3. 如果提示权限问题: ${BOLD}chmod +x $(basename "$appimage_out")${NC}"
        echo -e "    4. 无 FUSE 环境: ${BOLD}./$(basename "$appimage_out") --appimage-extract-and-run${NC}"
        echo ""
    else
        log_err "AppImage 构建失败"
        exit 1
    fi
}

# ────────────────────────────────────────────────────────────────────────────
# 便携目录模式（解压即用，双击 .desktop 或 start.sh 运行）
# ────────────────────────────────────────────────────────────────────────────
build_portable() {
    log_info "构建便携版..."

    build_pyinstaller
    embed_desktop_launcher "$DIST_DIR/$OUTPUT_NAME"

    local portable_dir="$DIST_DIR/${OUTPUT_NAME}-${VERSION}-portable"
    rm -rf "$portable_dir"
    cp -r "$DIST_DIR/$OUTPUT_NAME" "$portable_dir"

    log_ok "便携版构建完成！"
    echo ""
    echo -e "  ${BOLD}${GREEN}✔ ${portable_dir}${NC}"
    echo ""
    echo -e "  ${CYAN}使用方法:${NC}"
    echo -e "    1. 将整个目录复制到任意位置"
    echo -e "    2. 在文件管理器中双击 ${BOLD}norp-agent.desktop${NC} 或 ${BOLD}start.sh${NC}"
    echo -e "    3. 如果桌面环境不支持 .desktop，右键点击 start.sh → 运行"
    echo ""
}

# ────────────────────────────────────────────────────────────────────────────
# tarball 分发包
# ────────────────────────────────────────────────────────────────────────────
build_tarball() {
    build_appimage

    log_info "创建 tar.gz 分发包..."

    local tarball="$DIST_DIR/${OUTPUT_NAME}-${VERSION}-linux-x86_64.tar.gz"

    # 打包：AppImage + 便携目录 + 安装脚本 + README
    tar -czf "$tarball" \
        -C "$DIST_DIR" \
        "$(basename "${OUTPUT_NAME}-${VERSION}-x86_64.AppImage")"

    log_ok "分发包创建完成 → $tarball"
    ls -lh "$tarball"
}

# ────────────────────────────────────────────────────────────────────────────
# 清理
# ────────────────────────────────────────────────────────────────────────────
clean() {
    log_info "清理构建产物..."
    rm -rf "$BUILD_DIR/pyinstaller_work"
    rm -rf "$BUILD_DIR/AppDir"
    rm -rf "$DIST_DIR/$OUTPUT_NAME"
    rm -rf "$DIST_DIR/${OUTPUT_NAME}-"*
    find "$PROJECT_DIR" -name "*.pyc" -delete
    find "$PROJECT_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    # 不删除 appimagetool（保留缓存，避免重复下载）
    log_ok "清理完成"
}

# ────────────────────────────────────────────────────────────────────────────
# 显示帮助
# ────────────────────────────────────────────────────────────────────────────
show_help() {
    echo ""
    echo -e "${BOLD}NORP Agent Linux 打包工具${NC}  v${VERSION}"
    echo ""
    echo "用法: $0 [目标]"
    echo ""
    echo -e "  ${BOLD}appimage${NC}      构建 AppImage（默认）→ ${GREEN}双击即可运行${NC}"
    echo "  portable       构建便携目录 → 解压后双击 .desktop 运行"
    echo "  tarball        构建 AppImage + .tar.gz 分发包"
    echo "  clean          清理所有构建产物"
    echo "  deps           安装 Python 依赖"
    echo ""
    echo "示例:"
    echo "  $0                  # 一键构建 AppImage"
    echo "  $0 portable         # 构建便携目录"
    echo "  VERSION=2.0.0 $0    # 指定版本号构建"
    echo ""
}

# ────────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────────
main() {
    local target="${1:-appimage}"

    case "$target" in
        appimage)
            check_deps
            build_appimage
            ;;
        portable|portable-dir|dir)
            check_deps
            build_portable
            ;;
        tarball|tar.gz|tgz|dist)
            check_deps
            build_tarball
            ;;
        pyinstaller|onedir|dir-only)
            check_deps
            build_pyinstaller
            embed_desktop_launcher "$DIST_DIR/$OUTPUT_NAME"
            log_info "PyInstaller 目录: $DIST_DIR/$OUTPUT_NAME/"
            ;;
        clean)
            clean
            ;;
        deps)
            log_info "安装 Python 依赖..."
            pip install --upgrade pyinstaller
            pip install -r "$PROJECT_DIR/requirements.txt"
            log_ok "完成"
            ;;
        help|-h|--help)
            show_help
            ;;
        *)
            log_err "未知目标: $target"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
