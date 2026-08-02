# Linux 打包指南

打包产物是**可直接双击运行**的 AppImage 或便携目录。

---

## 方式一：Linux 本地打包（推荐）

### 1. 安装系统依赖

| 发行版 | 命令 |
|---|---|
| **Ubuntu / Debian** | `sudo apt install python3 python3-pip python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.0 wget` |
| **Fedora** | `sudo dnf install python3 python3-pip python3-gobject gtk3 webkit2gtk3 wget` |
| **Arch Linux** | `sudo pacman -S python python-pip gtk3 webkit2gtk wget` |

### 2. 安装 Python 依赖

```bash
pip install pyinstaller
pip install -r requirements.txt
```

### 3. 一键打包

```bash
cd linux_build
chmod +x build.sh

# 默认：构建 AppImage（双击运行）✨
./build.sh

# 或：便携目录（解压后双击 .desktop / start.sh）
./build.sh portable

# 或：分发包（AppImage + tar.gz）
./build.sh tarball
```

---

## 方式二：Docker 构建（Windows / macOS 也可用）

无需安装 Linux 系统依赖，只要有 Docker 即可：

```bash
# 1. 构建 Docker 镜像
docker build -t norp-agent-builder -f linux_build/Dockerfile .

# 2. 打包（产物输出到 ./dist）
# Linux / macOS:
docker run --rm -v "$(pwd)/dist:/app/dist" norp-agent-builder

# Windows PowerShell:
docker run --rm -v "${PWD}/dist:/app/dist" norp-agent-builder

# 指定版本号:
docker run --rm -v "$(pwd)/dist:/app/dist" -e VERSION=2.0.0 norp-agent-builder
```

---

## 产物说明

| 产物 | 说明 |
|---|---|
| `dist/norp-agent-1.0.0-x86_64.AppImage` | **双击即可运行**（推荐分发） |
| `dist/norp-agent-1.0.0-portable/` | 便携目录，解压后双击 `.desktop` 或 `start.sh` |

---

## 用户使用方式

### AppImage（推荐）

```bash
# 1. 添加执行权限（仅首次）
chmod +x norp-agent-*.AppImage

# 2. 双击运行（文件管理器）
#    或在终端运行:
./norp-agent-*.AppImage

# 3. 如果系统没有 FUSE（如某些 CI 环境）:
./norp-agent-*.AppImage --appimage-extract-and-run
```

### 便携目录

1. 解压 `norp-agent-*-portable/` 到任意位置
2. 在文件管理器中双击 `norp-agent.desktop` 或 `start.sh`
3. 如果没有桌面环境，终端运行 `./start.sh`

---

## 注意事项

| 事项 | 说明 |
|---|---|
| **GTK 运行时** | 最终用户仍需安装 GTK3 + WebKit2GTK（AppImage 不包含系统库） |
| **剪贴板** | 建议安装 `xclip`（X11）或 `wl-clipboard`（Wayland） |
| **Docker** | Docker 沙箱功能需要 Docker Engine |
| **FUSE** | 大多数桌面发行版已内置，服务器版可能需要 `sudo apt install fuse` |

---

## 目录结构

```
dist/
├── norp-agent-1.0.0-x86_64.AppImage   # ← 双击运行！
├── norp-agent-1.0.0-portable/         # ← 便携目录
│   ├── norp-agent                       # 可执行文件
│   ├── norp-agent.desktop               # 桌面启动器
│   ├── norp-agent.svg                   # 应用图标
│   ├── start.sh                         # 启动脚本
│   ├── front.html
│   ├── plugin_system/
│   └── official_plugins/
└── norp-agent-1.0.0-linux-x86_64.tar.gz
```
