"""应用控制器：把调度器、托盘、提醒窗口和设置界面串起来。"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import assets
from . import config as cfgmod
from . import log
from . import theme as thememod
from . import winapi
from .hotkey import HotkeyManager
from .scheduler import (
    LEVEL_OVERLAY,
    LEVEL_POPUP,
    LEVEL_TOAST,
    PHASE_OVERTIME,
    REST_MANUAL,
    Scheduler,
)
from .ui.alerts import (
    PopupWindow,
    RestCountdownWindow,
    ToastWindow,
    WrapupWindow,
)
from .ui.overlay import OverlayController
from .ui.settings_win import SettingsWindow, ask_confirm
from .ui.tray import TrayController

SERVER_NAME = "RestReminder.SingleInstance.v1"

ACCENT_OK = "#4ECB8E"
ACCENT_WARN = "#FFB454"
ACCENT_ALERT = "#FF6B6B"


class Application(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.app = app
        self.cfg: Dict[str, Any] = cfgmod.load()

        self.reduce_motion = self._compute_reduce_motion()
        self.theme_name = self._resolve_theme_name()
        self.palette = thememod.palette(self.theme_name)

        self.scheduler = Scheduler(self.cfg, self)
        self.tray = TrayController(self.palette, self)
        self.overlay = OverlayController(self.palette, self.reduce_motion)
        self.hotkeys = HotkeyManager(self)

        self.settings_window: Optional[SettingsWindow] = None
        self.toast: Optional[ToastWindow] = None
        self.popup: Optional[PopupWindow] = None
        self.wrapup: Optional[WrapupWindow] = None
        self.rest_window: Optional[RestCountdownWindow] = None
        self.pause_ui: Optional[RestCountdownWindow] = None

        self._current_level = 0
        self._last_status: Dict[str, Any] = {}
        self._was_first_run = False
        self._shutdown_pending = False
        self._server: Optional[QLocalServer] = None

        self._wire()
        self.app.setStyleSheet(thememod.build_qss(self.palette))

    # ------------------------------------------------------------ 启动

    def start(self) -> bool:
        """返回 False 表示已有实例在运行，本进程应当直接退出。"""
        if not self._claim_single_instance():
            log.write("已有实例在运行，本进程正常退出（不是崩溃）")
            return False
        log.write("单实例锁已取得，继续初始化")

        # 用户在启动文件夹里删掉快捷方式后，同步把配置里的开关关掉
        if self.cfg["general"].get("autostart") and not winapi.autostart_enabled():
            self.cfg["general"]["autostart"] = False
            cfgmod.save(self.cfg)

        self._was_first_run = not self.cfg.get("first_run_done")
        if self._was_first_run:
            self.cfg["first_run_done"] = True
            cfgmod.save(self.cfg)

        self.tray.refresh(self.scheduler.status())
        self._sync_hotkey()
        self.scheduler.start()

        if self._was_first_run:
            QTimer.singleShot(200, self.open_settings)
        else:
            QTimer.singleShot(900, self._startup_message)

        log.write(
            "启动完成 | 主题={} | 首次运行={} | 开机自启={}".format(
                self.cfg.get("theme"),
                self._was_first_run,
                bool(self.cfg["general"].get("autostart")),
            )
        )
        return True

    def _startup_message(self) -> None:
        feedback = self.scheduler.launch_feedback()
        if feedback:
            self._toast(feedback, "今晚目标 {}".format(self._bedtime_text()), ACCENT_OK, 9000)
        else:
            self._toast(
                "休息提醒已在后台运行",
                "目标 {}，到点会提醒你收尾".format(self._bedtime_text()),
                ACCENT_OK,
                6500,
            )

    def _bedtime_text(self) -> str:
        rules = self.cfg.get("rules") or []
        return str(rules[0].get("bedtime", "23:30")) if rules else "23:30"

    def _claim_single_instance(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(350):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(350)
            socket.disconnectFromServer()
            log.write("检测到已有实例，唤起它的设置窗口，本进程退出")
            return False

        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_ipc)
        if not self._server.listen(SERVER_NAME):
            log.write("单实例监听失败: {}".format(self._server.errorString()))
        return True

    def _on_ipc(self) -> None:
        if self._server is None:
            return
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(conn.readAll)
        conn.disconnected.connect(conn.deleteLater)
        self.open_settings()

    # ------------------------------------------------------------ 信号接线

    @staticmethod
    def _defer(fn: Callable[[], None]) -> None:
        """把动作推到下一轮事件循环再执行。

        托盘菜单项被点击时，菜单还握着鼠标/键盘抓取。此时直接 exec 一个模态
        对话框，对话框拿不到输入，表现出来就是"点了没反应"。先让菜单关掉。
        """
        QTimer.singleShot(0, fn)

    def _wire(self) -> None:
        self.scheduler.reminderDue.connect(self._on_reminder)
        self.scheduler.preNoticeDue.connect(self._on_pre_notice)
        self.scheduler.snoozed.connect(self._on_snoozed)
        self.scheduler.restConfirmed.connect(self._on_rest)
        self.scheduler.statusChanged.connect(self._on_status)

        self.tray.showSettings.connect(lambda: self._defer(self.open_settings))
        self.tray.windDown.connect(lambda: self._defer(self.start_winddown))
        self.tray.restNow.connect(lambda: self._defer(self.rest_now))
        self.tray.pauseFor.connect(
            lambda minutes: self._defer(lambda: self.pause(minutes))
        )
        self.tray.pauseWindow.connect(lambda: self._defer(self.pause_window))
        self.tray.resume.connect(self.resume)
        self.tray.clearTraces.connect(lambda: self._defer(self.clear_traces))
        self.tray.quitApp.connect(lambda: self._defer(self.quit_app))

        self.overlay.bind(
            self.snooze, self.rest_now, self.cancel_winddown, self.defer_winddown
        )
        self.hotkeys.activated.connect(self.toggle_pause)

    # ------------------------------------------------------------ 主题

    def _resolve_theme_name(self) -> str:
        name = self.cfg.get("theme", "dark")
        if name == "system":
            name = "light" if winapi.system_uses_light_theme() else "dark"
        return name if name in ("dark", "light") else "dark"

    def _compute_reduce_motion(self) -> bool:
        if self.cfg["general"].get("reduce_motion"):
            return True
        return not winapi.animations_enabled()

    def _refresh_theme(self) -> None:
        self.reduce_motion = self._compute_reduce_motion()

        was_visible = self.overlay.visible
        saved_ctx = dict(getattr(self.overlay, "_ctx", {}) or {})
        self.overlay.hide()

        self.theme_name = self._resolve_theme_name()
        self.palette = thememod.palette(self.theme_name)

        self.app.setStyleSheet(thememod.build_qss(self.palette))
        self.tray.apply_theme(self.palette)

        self.overlay = OverlayController(self.palette, self.reduce_motion)
        self.overlay.bind(
            self.snooze, self.rest_now, self.cancel_winddown, self.defer_winddown
        )
        if was_visible and saved_ctx:
            saved_ctx["locked"] = self._hardcore_locked()
            self.overlay.show(saved_ctx)

        for window in (
            self.toast,
            self.popup,
            self.wrapup,
            self.rest_window,
            self.pause_ui,
        ):
            if window is not None:
                window.reduce = self.reduce_motion
                window.apply_theme(self.palette)

        if self.settings_window is not None:
            self.settings_window.reduce_motion = self.reduce_motion
            self.settings_window.apply_theme(self.palette)
            self.settings_window.refresh_status(self._last_status)

    # ------------------------------------------------------------ 设置窗口

    def open_settings(self) -> None:
        if self.settings_window is None:
            self.settings_window = SettingsWindow(
                self.cfg, self.palette, self.reduce_motion
            )
            self.settings_window.configChanged.connect(self._on_config_changed)
            self.settings_window.themeChanged.connect(self._on_theme_changed)
            self.settings_window.restNow.connect(self.rest_now)
            self.settings_window.windDownRequested.connect(self.start_winddown)
            self.settings_window.pauseRequested.connect(self.pause)
            self.settings_window.pauseWindowRequested.connect(self.pause_window)
            self.settings_window.resumeRequested.connect(self.resume)
            self.settings_window.clearTracesRequested.connect(self.clear_traces)
            self.settings_window.quitRequested.connect(self.quit_app)
        self.settings_window.refresh_from_config()
        self.settings_window.refresh_status(self.scheduler.status())
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _on_config_changed(self) -> None:
        cfgmod.save(self.cfg)
        self.scheduler.reload()
        self._sync_hotkey()
        if self.settings_window is not None:
            self.settings_window.refresh_status(self._last_status)

    def _on_theme_changed(self) -> None:
        self._refresh_theme()

    def _sync_hotkey(self) -> None:
        general = self.cfg["general"]
        if general.get("hotkey_enabled", True):
            if not self.hotkeys.register(self.app, general.get("hotkey", "Ctrl+Alt+R")):
                self.hotkeys.unregister(self.app)
        else:
            self.hotkeys.unregister(self.app)

    # ------------------------------------------------------------ 提醒呈现

    def _toast_lifetime_ms(self) -> int:
        """轻提醒停留时间：短于升级间隔，避免和下一级撞在一起。"""
        escalate = int(self.cfg["reminder"].get("escalate_after_sec", 45))
        return max(5000, min(12000, escalate * 1000 // 3))

    def _toast(
        self,
        title: str,
        subtitle: str,
        color: str,
        lifetime: Optional[int] = None,
        on_click: Optional[Callable[[], None]] = None,
    ) -> None:
        if self.toast is None:
            self.toast = ToastWindow(self.palette, self.reduce_motion)
        self.toast.reduce = self.reduce_motion
        self.toast.show_message(
            title, subtitle, color, lifetime or self._toast_lifetime_ms(), on_click
        )

    def _on_pre_notice(self, minutes: int, ctx: Dict[str, Any]) -> None:
        self._toast(
            "还有 {} 分钟就该睡了".format(int(minutes)),
            "目标 {}，可以开始收尾了".format(ctx.get("bedtime", "")),
            ACCENT_WARN,
            8000,
            self.open_settings,
        )

    def _on_reminder(self, level: int, ctx: Dict[str, Any]) -> None:
        winddown = bool(ctx.get("winddown"))

        # 全屏应用避让：看视频、开会、打游戏时先推后；
        # 但超时阶段和收尾模式不再客气 —— 那是用户自己说要收的
        if (
            level < LEVEL_OVERLAY
            and not winddown
            and ctx.get("phase") != PHASE_OVERTIME
            and self.cfg["smart"].get("fullscreen_defer", True)
            and winapi.foreground_is_fullscreen()
        ):
            self.scheduler.defer(int(self.cfg["smart"].get("fullscreen_defer_min", 10)))
            return

        locked = self._hardcore_locked()

        if level == LEVEL_TOAST:
            if not self.cfg["reminder"]["channels"].get("toast", True):
                level = LEVEL_POPUP
            else:
                self._hide_popup()
                self.overlay.hide()
                phase = ctx.get("phase")
                if winddown:
                    title = "收尾模式"
                elif phase == PHASE_OVERTIME:
                    title = "该睡了"
                else:
                    title = "该收尾了"
                self._toast(
                    title,
                    self._short_line(ctx),
                    ACCENT_WARN if phase != PHASE_OVERTIME else ACCENT_ALERT,
                    None,
                    self.open_settings,
                )
                self._current_level = LEVEL_TOAST
                self._play_sound()
                return

        if level == LEVEL_POPUP:
            if self.toast is not None:
                self.toast.dismiss()
            self.overlay.hide()
            if self.popup is None:
                self.popup = PopupWindow(self.palette, self.reduce_motion)
                self.popup.snoozed.connect(self.snooze)
                self.popup.rested.connect(self.rest_now)
                self.popup.windDownCancel.connect(self.cancel_winddown)
                self.popup.windDownDefer.connect(self.defer_winddown)
            self.popup.reduce = self.reduce_motion
            self.popup.set_content(ctx, locked)
            if self._current_level == LEVEL_POPUP and self.popup.isVisible():
                self.popup.pulse()
            else:
                self.popup.appear()
            self._current_level = LEVEL_POPUP
            self._play_sound()
            return

        if self.toast is not None:
            self.toast.dismiss()
        self._hide_popup()

        payload = dict(ctx)
        payload["locked"] = locked
        if self.overlay.visible and self._current_level == LEVEL_OVERLAY:
            self.overlay.update(payload)
            self.overlay.pulse()
        else:
            self.overlay.show(payload)
        self._current_level = LEVEL_OVERLAY
        self._play_sound()

    def _short_line(self, ctx: Dict[str, Any]) -> str:
        if ctx.get("winddown"):
            return "节奏：{}".format(ctx.get("plan_text", ""))
        remain = int(ctx.get("remain_min", 0))
        if remain < 0:
            return "已经超时 {} 分钟，目标 {}".format(-remain, ctx.get("bedtime", ""))
        if remain >= 60:
            return "距目标睡觉时间还有 {} 小时 {} 分".format(remain // 60, remain % 60)
        return "距目标睡觉时间还有 {} 分钟".format(remain)

    def _play_sound(self) -> None:
        """用 winsound 播放，不依赖 QtMultimedia，体积更小也更稳。"""
        if not self.cfg["reminder"]["channels"].get("sound", True):
            return
        volume = int(self.cfg["reminder"].get("sound_volume", 55))
        path = assets.chime_path(volume)
        if not path:
            return
        try:
            import winsound

            winsound.PlaySound(
                path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            # 播放失败不影响提醒本身
            pass

    def _hardcore_locked(self) -> bool:
        return bool(
            self.cfg["reminder"].get("strength") == "hardcore"
            and self.cfg["general"].get("password_hash")
        )

    def _hide_popup(self) -> None:
        if self.popup is not None:
            self.popup.close_now()

    def _hide_all_alerts(self) -> None:
        if self.toast is not None:
            self.toast.dismiss()
        self._hide_popup()
        self.overlay.hide()
        if self.wrapup is not None:
            self.wrapup.stop()
        if self.rest_window is not None:
            self.rest_window.stop()
        if self.pause_ui is not None:
            self.pause_ui.stop()
        self._current_level = 0

    # ------------------------------------------------------------ 收尾模式

    def start_winddown(self) -> None:
        """「我要收了」：进入收尾模式，按固定节奏催到停为止。"""
        self._hide_all_alerts()
        self.scheduler.start_winddown()
        wd = self.scheduler.status().get("winddown") or {}
        self._toast(
            "收尾模式已开始",
            "节奏：{}".format(wd.get("plan_text", "")),
            ACCENT_WARN,
            9000,
            self.open_settings,
        )

    def cancel_winddown(self) -> None:
        """「结束收尾」：退出收尾模式，回到正常提醒节奏。"""
        if not self.scheduler.is_winddown():
            return
        self._hide_all_alerts()
        self.scheduler.cancel_winddown()
        self._toast(
            "已退出收尾模式",
            "提醒回到正常节奏，但目标 {} 还在".format(self._bedtime_text()),
            ACCENT_OK,
            6000,
        )

    def defer_winddown(self) -> None:
        """「延后提醒」：收起提醒，过一会儿再来 —— 收尾模式继续跑。

        与「结束收尾」的区别：那个会退出整个收尾模式，这个只是这次先收起来。
        与早睡 snooze 的区别：不缩短间隔、不升级强度，用户只是手头还差一点。
        """
        if not self.scheduler.is_winddown():
            return
        self._hide_all_alerts()
        self.scheduler.defer_winddown()
        minutes = int((self.cfg.get("winddown") or {}).get("defer_min", 5) or 5)
        self._toast(
            "好，{} 分钟后再提醒".format(minutes),
            "收尾模式继续，节奏不变",
            ACCENT_WARN,
            6000,
        )

    def finish_winddown(self) -> None:
        """「我已经收了」：结束收尾模式，按正常流程记一笔。"""
        self._hide_all_alerts()
        self.scheduler.finish_winddown(REST_MANUAL)

    # ------------------------------------------------------------ 用户操作

    def snooze(self, minutes: int) -> None:
        if self._hardcore_locked() and not self._ask_password(
            "跳过需要密码", "硬核模式下，跳过提醒需要先输入密码。"
        ):
            return
        self._hide_all_alerts()
        self.scheduler.snooze(int(minutes))

    def rest_now(self) -> None:
        self._hide_all_alerts()
        self.scheduler.confirm_rest(REST_MANUAL)

    def undo_rest(self) -> None:
        """撤销「今晚已休息」，连带取消已经排定的关机。"""
        if self._shutdown_pending:
            winapi.abort_shutdown()
            self._shutdown_pending = False
        # 撤销窗口自己点取消时已经关过了，这里再关一次是幂等的；
        # 但从别处（托盘、设置窗口）触发撤销时，必须由这里负责关掉它。
        if self.rest_window is not None:
            self.rest_window.stop()
        self.scheduler.undo_rest()
        self._toast(
            "已撤销，继续提醒",
            "目标 {} 还在，别忘了".format(self._bedtime_text()),
            ACCENT_WARN,
            6500,
        )

    def pause(self, minutes: int = 60) -> None:
        if self._hardcore_locked() and not self._ask_password(
            "暂停需要密码", "硬核模式下，暂停提醒需要先输入密码。"
        ):
            return
        self._hide_all_alerts()
        self.scheduler.pause(minutes=int(minutes))
        self._show_pause_window()

    def pause_window(self) -> None:
        if self._hardcore_locked() and not self._ask_password(
            "暂停需要密码", "硬核模式下，暂停提醒需要先输入密码。"
        ):
            return
        self._hide_all_alerts()
        self.scheduler.pause_until_end_of_window()
        self._show_pause_window()

    def resume(self) -> None:
        """立刻取消暂停，恢复提醒。"""
        if self.pause_ui is not None:
            self.pause_ui.stop()
        self.scheduler.resume()

    def _show_pause_window(self) -> None:
        """暂停之后马上给一个「立即恢复」的入口。

        不然暂停很容易变成"忘了怎么回来"，托盘和设置里虽然有恢复按钮，
        但都不是你刚点完暂停时会去看的地方。
        """
        status = self.scheduler.status()
        until = status.get("paused_until")
        if until is None:
            return
        total = max(1, int((until - datetime.now()).total_seconds()))
        if self.pause_ui is None:
            self.pause_ui = RestCountdownWindow(self.palette, self.reduce_motion)
            self.pause_ui.resumeRequested.connect(self.resume)
        self.pause_ui.reduce = self.reduce_motion
        self.pause_ui.apply_theme(self.palette)
        self.pause_ui.start(until, total, False, "", "", mode="pause")

    def toggle_pause(self) -> None:
        if self.scheduler.status().get("paused"):
            self.resume()
            self._toast(
                "已恢复提醒", "目标 {} 别忘了".format(self._bedtime_text()), ACCENT_OK, 5000
            )
        else:
            self.pause(60)

    def _ask_password(self, title: str, hint: str) -> bool:
        from .ui.settings_win import ask_password

        value = ask_password(self.settings_window, self.palette, title, hint)
        if value is None:
            return False
        if cfgmod.verify_password(self.cfg, value):
            return True
        ask_confirm(self.settings_window, self.palette, "密码不对", "请再试一次。")
        return False

    def clear_traces(self) -> None:
        ok = ask_confirm(
            self.settings_window,
            self.palette,
            "清除所有痕迹",
            "即将做三件事：取消开机自启、删除配置文件、删除生成的临时提示音。"
            "程序本身不会被删除，你需要自己删掉这个 exe。确定继续吗？",
        )
        if not ok:
            return
        winapi.disable_autostart()
        assets.cleanup_generated_files()
        cfgmod.remove_config_file()
        fallback = os.path.join(os.environ.get("APPDATA", ""), cfgmod.APP_DIR_NAME)
        if os.path.isdir(fallback) and not cfgmod.is_portable():
            shutil.rmtree(fallback, ignore_errors=True)
        # 日志也要清掉，否则「清除所有痕迹」名不副实。
        # 放在最后：这之后不应再写任何日志。
        log.clear()
        self._shutdown()

    def quit_app(self) -> None:
        if self.cfg["general"].get("exit_confirm", True):
            try:
                ok = ask_confirm(
                    self.settings_window,
                    self.palette,
                    "退出",
                    "退出后今晚就不会再提醒你了。确定要退出吗？",
                )
            except Exception:
                # 弹窗本身出问题也绝不能让用户退不掉程序
                ok = True
            if not ok:
                return
        self._hide_all_alerts()
        cfgmod.save(self.cfg)
        self._shutdown()

    def _shutdown(self) -> None:
        # 用户主动退出程序，说明还没打算睡，先把已经排定的关机撤掉
        if self._shutdown_pending:
            winapi.abort_shutdown()
            self._shutdown_pending = False
        self._hide_all_alerts()
        self.scheduler.stop()
        self.hotkeys.unregister(self.app)
        if self._server is not None:
            self._server.close()
        self.tray.hide()
        self.app.quit()

    # ------------------------------------------------------------ 状态同步

    def _on_snoozed(self, minutes: int, next_fire) -> None:
        if not self.cfg["wrapup"].get("enabled", True):
            return
        if self.wrapup is None:
            self.wrapup = WrapupWindow(self.palette, self.reduce_motion)
        self.wrapup.reduce = self.reduce_motion
        self.wrapup.apply_theme(self.palette)
        self.wrapup.start_countdown(next_fire, int(minutes))

    def _on_rest(self, reason: str, info: Dict[str, Any]) -> None:
        self._hide_all_alerts()
        if reason != REST_MANUAL:
            # 靠空闲自动判定出来的休息，人已经不在电脑前，别弹东西
            self.tray.refresh(self.scheduler.status())
            return
        self._show_rest_window(info)
        self.tray.refresh(self.scheduler.status())

    def _show_rest_window(self, info: Dict[str, Any]) -> None:
        conf = self.cfg.get("after_rest") or {}
        will_shutdown = str(conf.get("mode", "none")) == "shutdown"
        grace = max(30, int(conf.get("grace_sec", 120)))

        if will_shutdown:
            ok = winapi.schedule_shutdown(grace, "休息提醒：该睡了")
            self._shutdown_pending = bool(ok)
            if not ok:
                # 排定失败就老实退化成普通确认，不要假装成功
                will_shutdown = False
        else:
            self._shutdown_pending = False

        total = grace if will_shutdown else RestCountdownWindow.UNDO_WINDOW_SEC
        if self.rest_window is None:
            self.rest_window = RestCountdownWindow(self.palette, self.reduce_motion)
            self.rest_window.cancelled.connect(self.undo_rest)
        self.rest_window.reduce = self.reduce_motion
        self.rest_window.apply_theme(self.palette)
        self.rest_window.start(
            datetime.now() + timedelta(seconds=total),
            total,
            will_shutdown,
            info.get("time", ""),
            self._bedtime_text(),
        )

    def _on_status(self, status: Dict[str, Any]) -> None:
        self._last_status = status
        self.tray.refresh(status)
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.refresh_status(status)
