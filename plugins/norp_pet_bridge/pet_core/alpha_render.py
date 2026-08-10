# -*- coding: utf-8 -*-
"""
真 alpha 分层窗口渲染器（Windows）
====================================
解决 Tk `-transparentcolor` 只能二值键控（要么全透明、要么全不透明）的
根本缺陷：优香立绘边缘的大量半透明像素（PNG 抗锯齿过渡）在旧方案里被
强行二值化，导致边缘锯齿、发暗、像素感，且反复调优都无法根治。

本模块用 Windows 分层窗口（WS_EX_LAYERED + UpdateLayeredWindow）实现
逐像素 alpha 合成：半透明边缘像素与桌面真实混合，边缘像素级平滑，
彻底告别锯齿/黑线/紫边。

原理：
  1. CreateDIBSection 创建 32bpp BGRA 位图（自顶向下，与 PIL 行序一致）
  2. 每帧把 RGBA 像素 memmove 进位图缓冲（零分配、~0.5ms）
  3. UpdateLayeredWindow + BLENDFUNCTION(AC_SRC_ALPHA) 做真 alpha 合成
  4. 鼠标交互：分层窗口不带 WS_EX_TRANSPARENT（不穿透），形象区域
     接收鼠标消息后经 WNDPROC 子类化转发给 Tk 主窗口 —— Tk 窗口整个
     背景都是键控透明色（点击穿透），必须由分层窗口当「鼠标接收器」。

仅在 Windows 上可用；其他平台 / 初始化失败时返回 None，由调用方回退。
"""

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000   # 点击不激活、不抢焦点（用户打字时点宠物不失焦）
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_BITFIELDS = 3   # 32bpp 用 BITFIELDS + 显式掩码，否则 GDI 会清空 DIB 的 alpha 通道

# ---- 鼠标消息（转发给 Tk 主窗口）----
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
_MOUSE_MSGS = frozenset((WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP,
                         WM_LBUTTONDBLCLK, WM_RBUTTONDOWN, WM_RBUTTONUP,
                         WM_RBUTTONDBLCLK, WM_MBUTTONDOWN, WM_MBUTTONUP,
                         WM_MBUTTONDBLCLK, WM_MOUSEWHEEL, WM_MOUSEHWHEEL))

GWLP_WNDPROC = -4

# WNDPROC 回调类型（LRESULT 在 64 位系统上是 64 位，必须用 c_ssize_t）
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPV4HEADER(ctypes.Structure):
    """BITMAPV4HEADER：32bpp DIB 必须用它 + BI_BITFIELDS + 显式掩码，
    否则 GDI 会把 DIB 的 alpha 通道清零，UpdateLayeredWindow 的
    真 alpha 合成失效（窗口显示为全透明）。"""
    _fields_ = [
        ("bV4Size", wintypes.DWORD),
        ("bV4Width", wintypes.LONG),
        ("bV4Height", wintypes.LONG),
        ("bV4Planes", wintypes.WORD),
        ("bV4BitCount", wintypes.WORD),
        ("bV4V4Compression", wintypes.DWORD),
        ("bV4SizeImage", wintypes.DWORD),
        ("bV4XPelsPerMeter", wintypes.LONG),
        ("bV4YPelsPerMeter", wintypes.LONG),
        ("bV4ClrUsed", wintypes.DWORD),
        ("bV4ClrImportant", wintypes.DWORD),
        ("bV4RedMask", wintypes.DWORD),
        ("bV4GreenMask", wintypes.DWORD),
        ("bV4BlueMask", wintypes.DWORD),
        ("bV4AlphaMask", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


class LayeredImage:
    """真 alpha 分层窗口：把 RGBA 图像逐像素合成到桌面。

    鼠标交互：窗口不带 WS_EX_TRANSPARENT，形象区域会接收鼠标消息；
    调用 forward_events_to(tk_hwnd) 后，所有鼠标消息经 WNDPROC 子类化
    原样转发给 Tk 主窗口（两窗口位置/尺寸完全重合，客户区坐标一致），
    拖动/单击/双击/右键菜单全部由 Tk 正常处理。
    """

    def __init__(self, x, y, w, h):
        self.w, self.h = w, h
        self._x, self._y = x, y
        self._shown = False
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "STATIC", None, WS_POPUP, x, y, w, h, 0, 0, 0, None,
        )
        if not self.hwnd:
            raise OSError("CreateWindowExW failed: %d" % kernel32.GetLastError())
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        # 鼠标转发状态
        self._tk_hwnd = None
        self._bridge = None       # 事件桥回调（由 attach_bridge 设置）
        self._wndproc_cb = None   # 必须持有引用，否则回调被 GC 后窗口过程崩溃
        self._old_wndproc = None
        # 32bpp BGRA DIB（负高度 = 自顶向下，与 PIL 行序一致）
        bmi = BITMAPV4HEADER()
        bmi.bV4Size = ctypes.sizeof(BITMAPV4HEADER)
        bmi.bV4Width = w
        bmi.bV4Height = -h
        bmi.bV4Planes = 1
        bmi.bV4BitCount = 32
        bmi.bV4V4Compression = BI_BITFIELDS
        bmi.bV4SizeImage = w * h * 4
        bmi.bV4RedMask = 0x00FF0000
        bmi.bV4GreenMask = 0x0000FF00
        bmi.bV4BlueMask = 0x000000FF
        bmi.bV4AlphaMask = 0xFF000000
        self._pbits = ctypes.POINTER(ctypes.c_ubyte)()
        hdc = user32.GetDC(0)
        self._hbm = gdi32.CreateDIBSection(
            hdc, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(self._pbits), None, 0)
        user32.ReleaseDC(0, hdc)
        if not self._hbm:
            user32.DestroyWindow(self.hwnd)
            raise OSError("CreateDIBSection failed: %d" % kernel32.GetLastError())
        self._nbytes = w * h * 4
        self._buf = ctypes.cast(
            self._pbits, ctypes.POINTER(ctypes.c_ubyte * self._nbytes))

    # ------------------------------------------------------------------
    # 鼠标事件转发（关键：Tk 窗口全键控透明 → 点击穿透 → 由本窗口接收）
    # ------------------------------------------------------------------
    def attach_bridge(self, bridge):
        """设置事件桥回调 bridge(msg, wparam, lparam)，并子类化窗口过程。

        回调在窗口过程（Tk 主线程消息分发）里被调用，只允许做轻量操作
        （如入队），严禁直接调用 Tk API，否则会造成 Tk 重入卡死。
        """
        self._bridge = bridge
        if self._wndproc_cb is not None:
            return
        self._wndproc_cb = WNDPROC(self._mouse_forward)
        try:
            setproc = user32.SetWindowLongPtrW
        except AttributeError:
            setproc = user32.SetWindowLongW
        setproc.restype = wintypes.LPARAM
        setproc.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPARAM]
        self._old_wndproc = setproc(
            self.hwnd, GWLP_WNDPROC,
            ctypes.cast(self._wndproc_cb, ctypes.c_void_p).value)

    def _mouse_forward(self, hwnd, msg, wparam, lparam):
        """子类化窗口过程：鼠标消息交给事件桥（Tk event_generate），其余走默认处理。

        关键：STATIC 类窗口对 WM_NCHITTEST 默认返回 HTTRANSPARENT
        （静态控件天生点击穿透），必须强制返回 HTCLIENT，鼠标消息
        才会送达本窗口，否则永远点不到宠物。
        """
        try:
            if msg == 0x0084:   # WM_NCHITTEST
                return 1        # HTCLIENT：接收鼠标消息
            if self._bridge is not None and msg in _MOUSE_MSGS:
                self._bridge(msg, wparam, lparam)
                return 0
        except Exception:
            pass
        try:
            if self._old_wndproc:
                user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                                   wintypes.UINT, wintypes.WPARAM,
                                                   wintypes.LPARAM]
                user32.CallWindowProcW.restype = ctypes.c_ssize_t
                return user32.CallWindowProcW(self._old_wndproc, hwnd, msg,
                                              wparam, lparam)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def blit(self, img_rgba, x=None, y=None):
        """把与窗口同尺寸的 RGBA PIL 图像以真 alpha 方式显示。

        x/y 可选：分层窗口的屏幕坐标（UpdateLayeredWindow 的 pptDst）。
        注意：分层窗口用 UpdateLayeredWindow 更新后 SetWindowPos 无法
        移动它，位置必须在这里指定；坐标变化时自动跟随。
        """
        data = img_rgba.tobytes("raw", "BGRA")
        ctypes.memmove(self._pbits, data, self._nbytes)
        hdc = user32.GetDC(self.hwnd)
        hdc_mem = gdi32.CreateCompatibleDC(hdc)
        old = gdi32.SelectObject(hdc_mem, self._hbm)
        size = SIZE(self.w, self.h)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        pt_dst = None
        if x is not None and y is not None:
            # 无条件按传入坐标定位：move() 也会改写 _x/_y（仅记录），
            # 若用「坐标变化才更新」判断会被 move 干扰，导致分层窗口
            # 位置永不刷新（表现为拖拽时形象原地不动）。
            pt_dst = POINT(int(x), int(y))
            self._x, self._y = int(x), int(y)
        ok = user32.UpdateLayeredWindow(self.hwnd, hdc,
                                        ctypes.byref(pt_dst) if pt_dst else None,
                                        ctypes.byref(size),
                                        hdc_mem, ctypes.byref(pt_src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(self.hwnd, hdc)
        if not ok:
            raise OSError("UpdateLayeredWindow failed: %d" % kernel32.GetLastError())
        if not self._shown:
            self._shown = True
            user32.ShowWindow(self.hwnd, 8)   # SW_SHOWNA：显示但不抢焦点

    def move(self, x, y):
        """兼容接口：分层窗口位置需经 blit(x,y) 更新，这里仅记录坐标。"""
        self._x, self._y = int(x), int(y)

    def close(self):
        # 恢复原窗口过程，再销毁窗口
        if self.hwnd and self._old_wndproc:
            try:
                setproc = getattr(user32, "SetWindowLongPtrW",
                                  user32.SetWindowLongW)
                setproc.restype = wintypes.LPARAM
                setproc.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPARAM]
                setproc(self.hwnd, GWLP_WNDPROC, self._old_wndproc)
            except Exception:
                pass
            self._old_wndproc = None
        self._wndproc_cb = None
        if self._hbm:
            gdi32.DeleteObject(self._hbm)
            self._hbm = None
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None


def create_layered(x, y, w, h):
    """安全创建分层窗口；失败返回 None（调用方回退到 Tk 绘制）。"""
    try:
        return LayeredImage(x, y, w, h)
    except Exception:
        return None
