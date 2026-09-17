"""系统托盘常驻与右键菜单。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import assets


class TrayController(QObject):
    showSettings = Signal()
    restNow = Signal()
    windDown = Signal()
    pauseFor = Signal(int)
    pauseWindow = Signal()
    resume = Signal()
    clearTraces = Signal()
    quitApp = Signal()

    def __init__(self, theme: Dict[str, str], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.theme = dict(theme)
        self._paused = False

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(assets.app_icon(self.theme))
        self.tray.setToolTip("休息提醒 · 正在运行")
        self.tray.activated.connect(self._on_activated)
        self._build_menu()
        self.tray.show()

    def _build_menu(self) -> None:
        menu = QMenu()

        self.act_status = QAction("正在运行", menu)
        self.act_status.setEnabled(False)
        menu.addAction(self.act_status)
        menu.addSeparator()

        act_settings = QAction("打开设置", menu)
        act_settings.triggered.connect(self.showSettings.emit)
        menu.addAction(act_settings)

        act_wind = QAction("我要收了（收尾模式）", menu)
        act_wind.triggered.connect(self.windDown.emit)
        menu.addAction(act_wind)

        act_rest = QAction("我这就去睡", menu)
        act_rest.triggered.connect(self.restNow.emit)
        menu.addAction(act_rest)
        menu.addSeparator()

        self.act_pause60 = QAction("暂停 1 小时", menu)
        self.act_pause60.triggered.connect(lambda: self.pauseFor.emit(60))
        menu.addAction(self.act_pause60)

        self.act_pause_window = QAction("暂停今晚", menu)
        self.act_pause_window.triggered.connect(self.pauseWindow.emit)
        menu.addAction(self.act_pause_window)

        self.act_resume = QAction("恢复提醒", menu)
        self.act_resume.triggered.connect(self.resume.emit)
        self.act_resume.setVisible(False)
        menu.addAction(self.act_resume)
        menu.addSeparator()

        act_clear = QAction("一键清除所有痕迹", menu)
        act_clear.triggered.connect(self.clearTraces.emit)
        menu.addAction(act_clear)

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quitApp.emit)
        menu.addAction(act_quit)

        self.menu = menu
        self.tray.setContextMenu(menu)

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self.showSettings.emit()

    def apply_theme(self, theme: Dict[str, str]) -> None:
        self.theme = dict(theme)
        self.tray.setIcon(assets.app_icon(self.theme, self._paused))

    def refresh(self, status: Dict[str, Any]) -> None:
        paused = bool(status.get("paused"))
        self._paused = paused

        if paused:
            until = status.get("paused_until")
            line = "已暂停"
            if until is not None:
                line = "已暂停至 {}".format(until.strftime("%H:%M"))
        elif status.get("rested_today"):
            line = "今晚已确认休息"
        elif not status.get("in_window"):
            line = "不在提醒时段"
        else:
            remain = int(status.get("remain_min", 0))
            if remain < 0:
                line = "已超时 {} 分钟".format(-remain)
            elif remain >= 60:
                line = "距睡觉 {} 小时 {} 分".format(remain // 60, remain % 60)
            else:
                line = "距睡觉 {} 分钟".format(remain)

        self.act_status.setText(line)
        self.act_resume.setVisible(paused)
        self.act_pause60.setVisible(not paused)
        self.act_pause_window.setVisible(not paused)
        self.tray.setToolTip("休息提醒 · {}".format(line))
        self.tray.setIcon(assets.app_icon(self.theme, paused))

    def notify(self, title: str, message: str) -> None:
        try:
            self.tray.showMessage(title, message, assets.app_icon(self.theme), 4000)
        except Exception:
            pass

    def hide(self) -> None:
        try:
            self.tray.hide()
        except Exception:
            pass
