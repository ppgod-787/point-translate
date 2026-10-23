"""Windows 平台细节：DPI 感知、屏幕坐标、截图、鼠标穿透。

单独抽出来是因为这些坑不处理就会有玄学 bug：
- 不声明 DPI 感知 → 125% 缩放下鼠标坐标和实际像素对不上，取词整体偏移。
- 多显示器 → 副屏可能是负坐标，截图范围要用虚拟桌面而不是主屏。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

# 虚拟桌面（所有显示器合起来）的边界
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

SRCCOPY = 0x00CC0020
BI_RGB = 0

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def enable_dpi_awareness() -> None:
    """必须在创建任何窗口之前调用一次。"""
    try:
        if user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return
    except Exception:
        pass
    try:
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def foreground_title() -> str:
    """当前前台窗口标题，用于日志和调试。"""
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """返回 (left, top, width, height)，覆盖所有显示器。"""
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return left, top, width, height


def clamp_to_virtual(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    """把一个矩形裁进虚拟桌面范围，避免截到屏幕外面。"""
    vl, vt, vw, vh = virtual_screen_rect()
    left = max(x, vl)
    top = max(y, vt)
    right = min(x + w, vl + vw)
    bottom = min(y + h, vt + vh)
    return left, top, max(right - left, 1), max(bottom - top, 1)


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


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# 64 位下 GDI 句柄是 8 字节指针。不声明 restype，ctypes 默认按 32 位 int 处理，
# 句柄值一旦超过 2^31 就会 OverflowError 直接崩掉。
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
]

user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

# 窗口扩展样式：GetWindowLongW 在 64 位下必须明确用 LONG_PTR 版本，
# 否则句柄/样式值可能被截断
try:
    _get_window_long = user32.GetWindowLongPtrW
    _set_window_long = user32.SetWindowLongPtrW
except AttributeError:  # pragma: no cover
    _get_window_long = user32.GetWindowLongW
    _set_window_long = user32.SetWindowLongW

_get_window_long.restype = ctypes.c_ssize_t
_get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
_set_window_long.restype = ctypes.c_ssize_t
_set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]


def grab_region_raw(x: int, y: int, w: int, h: int) -> tuple[bytes, int, int] | None:
    """截取屏幕矩形，返回 (BGRA 裸像素, 宽, 高)。失败返回 None。

    用 GDI BitBlt 而不是 PIL.ImageGrab：快得多，而且能正确
    配合 DPI 感知拿到真实屏幕像素。
    """
    x, y, w, h = clamp_to_virtual(x, y, w, h)

    screen_dc = user32.GetDC(0)
    if not screen_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    if not mem_dc or not bitmap:
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, screen_dc)
        return None

    try:
        gdi32.SelectObject(mem_dc, bitmap)
        if not gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, SRCCOPY):
            return None

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h  # 负数 = 自上而下，否则图上下颠倒
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        buffer = ctypes.create_string_buffer(w * 4 * h)
        if not gdi32.GetDIBits(
            mem_dc, bitmap, 0, h, ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(info), 0
        ):
            return None
        return buffer.raw, w, h
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, screen_dc)


def raw_to_software_bitmap(raw: bytes, w: int, h: int):
    """把 BGRA 裸像素包成 WinRT SoftwareBitmap，可直接喂给 OCR 引擎。

    走这条路就不用先编码成 PNG 再解码，省一次无谓的往返。
    """
    try:
        from winsdk.windows.graphics.imaging import (
            BitmapAlphaMode,
            BitmapPixelFormat,
            SoftwareBitmap,
        )
        from winsdk.windows.security.cryptography import CryptographicBuffer
    except ImportError:
        return None

    try:
        buf = CryptographicBuffer.create_from_byte_array(bytes(raw))
        return SoftwareBitmap.create_copy_from_buffer(
            buf, BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.IGNORE
        )
    except Exception:
        return None


def make_click_through(hwnd: int) -> None:
    """让窗口对鼠标完全透明：看得见、点不到。

    这一步很关键 —— 否则鼠标移向气泡时会先指到气泡本身，
    取词对象从"原文"变成"气泡"，导致结果闪烁或死循环。
    """
    ex = _get_window_long(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    _set_window_long(hwnd, GWL_EXSTYLE, ex)


if __name__ == "__main__":
    enable_dpi_awareness()
    print("虚拟桌面:", virtual_screen_rect())
    print("鼠标位置:", cursor_pos())
    print("前台窗口:", foreground_title())
    got = grab_region_raw(0, 0, 200, 60)
    if got:
        print(f"截图成功: {got[1]}x{got[2]}, {len(got[0])} 字节")
        print("SoftwareBitmap:", raw_to_software_bitmap(*got) is not None)
    else:
        print("截图失败")
