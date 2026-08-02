# =============================================================================
# NORP Agent - Linux AppImage 构建环境 (Docker)
# Copyright (c) 2026 xingluosama
#
# 用法:
#   docker build -t norp-agent-builder -f build/Dockerfile .
#   docker run --rm -v "$(pwd)/dist:/app/dist" norp-agent-builder
#
# 或在 Windows PowerShell:
#   docker build -t norp-agent-builder -f build/Dockerfile .
#   docker run --rm -v "${PWD}/dist:/app/dist" norp-agent-builder
# =============================================================================

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV VERSION=1.0.0

# ── 系统依赖：GTK3 + WebKit2GTK（pywebview 运行时） + 打包工具 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-3.0 \
    gir1.2-webkit2-4.0 \
    gir1.2-javascriptcoregtk-4.0 \
    gir1.2-soup-2.4 \
    gir1.2-atk-1.0 \
    gir1.2-gdkpixbuf-2.0 \
    gir1.2-pango-1.0 \
    wget \
    file \
    fuse \
    && rm -rf /var/lib/apt/lists/*

# ── Python 依赖 ──
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pyinstaller

# ── 预下载 appimagetool ──
RUN mkdir -p /app/linux_build && \
    wget -q -O /app/linux_build/appimagetool-x86_64.AppImage \
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" && \
    chmod +x /app/build/appimagetool-x86_64.AppImage

# ── 复制源码 ──
COPY . /app/

# ── 构建入口 ──
ENTRYPOINT ["bash", "/app/linux_build/build.sh"]
CMD ["appimage"]
