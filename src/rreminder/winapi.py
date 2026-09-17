"""Windows 原生能力封装。

全部只调用 user32 / kernel32 的只读查询接口 —— 不需要管理员权限，
也不修改任何系统设置。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # 仅用于非 Windows 环境下导入不报错
    user32 = None
    kernel32 = None

CREATE_NO_WINDOW = 0x08000000
STARTUP_LNK_NAME = "休息提醒.lnk"

# ------------------------------------------------------------------ 结构体


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


# ------------------------------------------------------------------ 空闲检测


def idle_seconds() -> float:
    """距离上次键鼠输入过去了多少秒。用于判断用户是否已经离开。"""
    if not IS_WINDOWS:
        return 0.0
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        tick = int(kernel32.GetTickCount64()) & 0xFFFFFFFF
        delta = (tick - int(info.dwTime)) & 0xFFFFFFFF
        return delta / 1000.0
    except Exception:
        return 0.0


# ------------------------------------------------------------------ 全屏检测


def _own_pid() -> int:
    return os.getpid()


def foreground_is_fullscreen() -> bool:
    """前台窗口是否铺满整块显示器（视频、游戏、演示、全屏会议）。

    用于在"最不该被打断"的时候自动延后提醒。
    """
    if not IS_WINDOWS:
        return False
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        # 排除本程序自己的窗口
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == _own_pid():
            return False

        # 排除桌面与任务栏
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Button"):
            return False

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return False

        hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return False
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return False
        mw = mi.rcMonitor.right - mi.rcMonitor.left
        mh = mi.rcMonitor.bottom - mi.rcMonitor.top

        # 允许 2px 误差
        return w >= mw - 2 and h >= mh - 2
    except Exception:
        return False


# ------------------------------------------------------------------ 系统偏好


def animations_enabled() -> bool:
    """系统是否开启了窗口动画（辅助功能里的"显示动画"）。"""
    if not IS_WINDOWS:
        return True
    try:
        SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled = wintypes.BOOL(True)
        user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
        )
        return bool(enabled.value)
    except Exception:
        return True


def system_uses_light_theme() -> bool:
    """Windows 应用模式是否为浅色。"""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return bool(value)
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


# ------------------------------------------------------------------ 开机自启
# 用启动文件夹快捷方式，不使用注册表：用户在资源管理器输入 shell:startup
# 就能看到并直接删除。默认关闭，是否启用完全由用户决定。


def _run_hidden(cmd: list) -> bool:
    return _run_hidden_rc(cmd) == 0


def _run_hidden_rc(cmd: list) -> int:
    """执行命令并返回退出码。失败返回 -1。"""
    try:
        proc = subprocess.run(
            cmd,
            creationflags=CREATE_NO_WINDOW,
            timeout=25,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return int(proc.returncode)
    except Exception:
        return -1


def startup_dir() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )


def startup_lnk_path() -> str:
    return os.path.join(startup_dir(), STARTUP_LNK_NAME)


def autostart_enabled() -> bool:
    try:
        return os.path.isfile(startup_lnk_path())
    except Exception:
        return False


def _exe_target() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    # 源码运行时，用 pythonw 启动脚本
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    script = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py")
    )
    return pyw if os.path.isfile(pyw) else script


def enable_autostart(target_lnk: Optional[str] = None) -> bool:
    """在启动文件夹创建快捷方式。返回是否成功。

    target_lnk 只给自检脚本用来指定临时路径，正常运行传 None 即可。
    """
    if not IS_WINDOWS:
        return False
    lnk = target_lnk or startup_lnk_path()
    target = _exe_target()
    workdir = os.path.dirname(target)
    q = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731

    if getattr(sys, "frozen", False):
        args = "--tray"
        icon = target + ",0"
    else:
        args = '"%s" --tray' % os.path.abspath(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py")
        )
        icon = target + ",0"

    ps = (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut({lnk});"
        "$s.TargetPath={tgt};$s.Arguments={args};"
        "$s.WorkingDirectory={wd};$s.IconLocation={ico};"
        "$s.Description='休息提醒';$s.Save()"
    ).format(lnk=q(lnk), tgt=q(target), args=q(args), wd=q(workdir), ico=q(icon))

    _run_hidden(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    return os.path.isfile(lnk)


def disable_autostart() -> bool:
    """删除启动文件夹里的快捷方式。"""
    try:
        path = startup_lnk_path()
        if os.path.isfile(path):
            os.remove(path)
        return not os.path.isfile(path)
    except Exception:
        return False


def autostart_location_hint() -> str:
    return "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"


def open_startup_folder() -> None:
    """在资源管理器中打开启动文件夹，方便用户自己确认/删除。"""
    path = startup_dir()
    if os.path.isdir(path):
        try:
            os.startfile(path)  # noqa: S606
        except Exception:
            _run_hidden(["explorer", path])


# ------------------------------------------------------------------ 其他


def hide_from_taskbar_hint() -> None:  # 占位，保持接口稳定
    return None


def system_uptime_seconds() -> Optional[float]:
    """系统已运行秒数，用于识别休眠/唤醒后计时器漂移。"""
    if not IS_WINDOWS:
        return None
    try:
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return int(kernel32.GetTickCount64()) / 1000.0
    except Exception:
        return None


# ------------------------------------------------------------------ 关机
# 用系统自带的 shutdown.exe，普通用户对自己的机器就有这个权限，不需要管理员。
# 计时交给系统（/t 参数），即使本程序被关掉，关机计划依然有效 ——
# 这样「去睡了」才不是摆设。


def schedule_shutdown(seconds: int = 120, comment: str = "") -> bool:
    """安排一次延时关机，返回是否成功排定。"""
    if not IS_WINDOWS:
        return False
    delay = max(15, min(86400, int(seconds)))
    cmd = ["shutdown", "/s", "/t", str(delay)]
    if comment:
        cmd += ["/c", str(comment)[:200]]
    return _run_hidden_rc(cmd) == 0


def abort_shutdown() -> bool:
    """取消已排定的关机。本来就没有待执行的关机也算成功。"""
    if not IS_WINDOWS:
        return False
    # 1116 = ERROR_SHUTDOWN_NOT_IN_PROGRESS
    return _run_hidden_rc(["shutdown", "/a"]) in (0, 1116)
