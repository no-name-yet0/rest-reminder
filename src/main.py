"""休息提醒 —— 程序入口。

绿色免安装：不写注册表（开机自启走启动文件夹快捷方式，默认关闭），
不需要管理员权限，不联网。
"""

from __future__ import annotations

import os
import sys


def _ensure_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def main(argv=None) -> int:
    _ensure_path()

    # 先装日志与异常钩子 —— 之后任何崩溃都会留下线索，
    # 而不是让进程无声消失（用户只会看到「双击了没反应」）。
    from rreminder import log as logmod

    logmod.install()
    logmod.startup_banner()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("休息提醒")
    app.setOrganizationName("RestReminder")
    app.setApplicationDisplayName("休息提醒")
    app.setQuitOnLastWindowClosed(False)

    from rreminder.app import Application

    controller = Application(app)
    if not controller.start():
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
