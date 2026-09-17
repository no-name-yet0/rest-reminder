"""轻量运行日志。

为什么需要它：
这是一个绿色程序 —— 用户手上只有一个 exe，没有控制台输出。
万一出现「双击了但托盘没图标」「启动后又自己没了」这种情况，
没有日志就只能靠猜。日志写在配置目录（和 config.json 放一起），
超过阈值自动轮转，不会无限长大。

原则：日志是配角。任何写入失败都必须静默忽略，绝不能影响主流程。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from typing import Optional

MAX_BYTES = 512 * 1024
LOG_NAME = "restreminder.log"

_path_cache: Optional[str] = None


def log_path() -> str:
    from . import config as cfgmod

    return os.path.join(cfgmod.data_dir(), LOG_NAME)


def _rotate(path: str) -> None:
    """超过阈值就轮转一次，最多保留一个历史文件。"""
    try:
        if os.path.isfile(path) and os.path.getsize(path) > MAX_BYTES:
            backup = path + ".1"
            if os.path.isfile(backup):
                os.remove(backup)
            os.rename(path, backup)
    except Exception:
        pass


def write(message: str) -> None:
    global _path_cache
    try:
        path = _path_cache or log_path()
        _path_cache = path
        _rotate(path)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("[{}] {}\n".format(stamp, message))
    except Exception:
        pass


def format_exception(exc_type, exc_value, exc_tb) -> str:
    try:
        return "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip()
    except Exception:
        return "{!r}".format(exc_value)


def write_exception(prefix: str = "未捕获异常") -> None:
    """把当前正在处理的异常写进日志。"""
    write("{}：\n{}".format(prefix, format_exception(*sys.exc_info())))


def install() -> None:
    """接管未捕获异常。否则异常只会让进程无声消失，什么线索都不留。"""

    def hook(exc_type, exc_value, exc_tb):
        write("未捕获异常：\n" + format_exception(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook

    def thread_hook(args) -> None:
        name = getattr(getattr(args, "thread", None), "name", "?")
        write(
            "线程 {} 内未捕获异常：\n{}".format(
                name, format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            )
        )

    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_hook


def startup_banner() -> None:
    from . import APP_VERSION

    write("-" * 50)
    write("启动 | 版本 {} | Python {}".format(APP_VERSION, sys.version.split()[0]))
    try:
        from . import config as cfgmod

        write("配置目录 {}".format(cfgmod.data_dir()))
    except Exception as exc:
        write("读取配置目录失败 {!r}".format(exc))


def clear() -> bool:
    """「一键清除所有痕迹」时顺带删掉日志。"""
    cleared = False
    try:
        for path in (log_path(), log_path() + ".1"):
            if os.path.isfile(path):
                os.remove(path)
                cleared = True
    except Exception:
        pass
    return cleared
