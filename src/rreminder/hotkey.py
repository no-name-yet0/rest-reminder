"""全局快捷键（RegisterHotKey）。

注册失败不影响任何主功能 —— 按钮和托盘菜单都能完成同样的事。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional, Tuple

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312
HOTKEY_ID = 0xB0B0

_NAMED_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "return": 0x0D,
    "enter": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
}


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


def parse_sequence(text: str) -> Optional[Tuple[int, int]]:
    """把 'Ctrl+Alt+R' 这类字符串解析成 (修饰键掩码, 虚拟键码)。"""
    if not text:
        return None
    mods = 0
    vk: Optional[int] = None
    for raw in str(text).replace(" ", "").split("+"):
        token = raw.strip().lower()
        if not token:
            continue
        if token in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif token == "alt":
            mods |= MOD_ALT
        elif token == "shift":
            mods |= MOD_SHIFT
        elif token in ("win", "meta", "super", "cmd"):
            mods |= MOD_WIN
        elif len(token) == 1 and token.isalpha():
            vk = ord(token.upper())
        elif len(token) == 1 and token.isdigit():
            vk = ord(token)
        elif token.startswith("f") and token[1:].isdigit():
            n = int(token[1:])
            if 1 <= n <= 24:
                vk = 0x70 + n - 1
        elif token in _NAMED_KEYS:
            vk = _NAMED_KEYS[token]
    if vk is None or mods == 0:
        return None
    return mods, vk


class HotkeyManager(QObject, QAbstractNativeEventFilter):
    activated = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._registered = False
        self._app = None

    def register(self, app, sequence: str) -> bool:
        self.unregister(app)
        if not sys.platform.startswith("win"):
            return False
        parsed = parse_sequence(sequence)
        if parsed is None:
            return False
        mods, vk = parsed
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if not user32.RegisterHotKey(None, HOTKEY_ID, mods, vk):
                return False
        except Exception:
            return False
        try:
            app.installNativeEventFilter(self)
        except Exception:
            pass
        self._app = app
        self._registered = True
        return True

    def unregister(self, app=None) -> None:
        if not self._registered:
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.UnregisterHotKey(None, HOTKEY_ID)
        except Exception:
            pass
        target = app if app is not None else self._app
        if target is not None:
            try:
                target.removeNativeEventFilter(self)
            except Exception:
                pass
        self._registered = False
        self._app = None

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if not self._registered:
            return False, 0
        try:
            raw = event_type if isinstance(event_type, (bytes, bytearray)) else bytes(event_type)
        except Exception:
            raw = b""
        if b"windows_generic_MSG" not in raw:
            return False, 0
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
            if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                self.activated.emit()
                return True, 0
        except Exception:
            return False, 0
        return False, 0
