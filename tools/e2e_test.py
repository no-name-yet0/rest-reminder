"""端到端集成测试：用真实时钟跑通「配置 -> 调度器 -> 控制器 -> 提醒窗口」。

与 selftest.py 的区别：selftest 验证各部件能否正确构建，
这里验证它们串起来之后，一条真实提醒能否走完全程。

为避免自动化测试期间全屏遮罩把屏幕卡住，这里只开放到弹窗级别。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# Windows 上控制台默认走本地代码页（GitHub runner 上是 cp1252），
# print 中文会抛 UnicodeEncodeError。统一改成 UTF-8 并对无法编码的字符降级替换。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 原生崩溃（段错误之类）不会走 Python 的异常与 finally，用 faulthandler 把崩溃栈
# 落盘，否则什么都看不到。诊断文件放在系统临时目录 —— 不要写在项目根目录，
# 否则每次跑测试都会留下 _boot.txt / _fault.txt 这类垃圾文件。
# 崩溃后去 %TEMP%\rreminder-e2e\fault.txt 找栈。
import faulthandler  # noqa: E402

DIAG_DIR = os.path.join(tempfile.gettempdir(), "rreminder-e2e")
os.makedirs(DIAG_DIR, exist_ok=True)
with open(os.path.join(DIAG_DIR, "boot.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("module loaded\n")

_FAULT_LOG = open(os.path.join(DIAG_DIR, "fault.txt"), "w", encoding="utf-8")
faulthandler.enable(_FAULT_LOG)

REPORT = os.path.join(ROOT, "_e2e.txt")
LINES = []


def _scrub(text: str) -> str:
    """把本机专属路径换成占位符，报告才能安全地被分享或提交。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for real, placeholder in (
        (here, "<项目目录>"),
        (tempfile.gettempdir(), "%TEMP%"),
        (os.path.expanduser("~"), "~"),
    ):
        if real and len(real) > 3 and real in text:
            text = text.replace(real, placeholder)
    return text


def log(text: str = "") -> None:
    """逐行落盘，这样即使进程原生崩溃也能看到最后走到哪一步。"""
    text = _scrub(text)
    try:
        print(text)
    except Exception:
        # 控制台编码不支持中文时（GitHub runner 默认 cp1252）也不能崩 ——
        # 落盘走的是 UTF-8，报告本身不受影响。
        try:
            print(text.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass
    LINES.append(text)
    try:
        mode = "w" if len(LINES) == 1 else "a"
        with open(REPORT, mode, encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:
        pass


def main() -> int:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    import rreminder.app as app_module
    import rreminder.config as cfgmod

    # 全程读写临时目录里的配置，绝不碰用户真实的 config.json；
    # 单实例名也换成测试专用的 —— 否则用户开着程序时，测试会因为
    # 抢不到单实例锁直接退出，看起来像"测试失败"。
    scratch = tempfile.mkdtemp(prefix="rr_e2e_")
    cfgmod.data_dir = lambda: scratch
    cfgmod.config_path = lambda: os.path.join(scratch, "config.json")
    app_module.SERVER_NAME = "RestReminder.SingleInstance.E2E"

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    log("端到端测试  运行时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log("=" * 60)

    now = datetime.now()
    stamp = lambda delta: (now + delta).strftime("%H:%M")  # noqa: E731

    cfg = cfgmod.load()
    cfg["theme"] = "dark"
    cfg["first_run_done"] = True
    cfg["rules"] = [
        {
            "id": "e2e",
            "enabled": True,
            "name": "端到端测试",
            "days": [0, 1, 2, 3, 4, 5, 6],
            # 把窗口设成"现在正好处于超时阶段"
            "start": stamp(timedelta(minutes=-120)),
            "bedtime": stamp(timedelta(minutes=-30)),
            "end": stamp(timedelta(hours=3)),
            "interval_min": 1,
            "ramp": True,
            "jitter": 0.0,
        }
    ]
    cfg["reminder"]["channels"] = {
        "toast": True,
        "popup": True,
        "overlay": False,  # 测试期间不占用全屏
        "sound": False,    # 不发出声音干扰
    }
    cfg["reminder"]["strength"] = "standard"
    cfg["reminder"]["min_interval_min"] = 1
    cfg["reminder"]["pre_notice"] = False
    cfg["reminder"]["escalate_after_sec"] = 600
    cfg["smart"]["idle_pause"] = False
    cfg["smart"]["fullscreen_defer"] = False
    cfg["bedtime"]["confirm_idle_min"] = 60  # 测试期间不要判定"已离开"
    cfg["wrapup"]["enabled"] = True
    # 关键安全设置：测试期间绝不排定真实关机，否则中途失败会把机器关掉
    cfg["after_rest"] = {"mode": "none", "grace_sec": 120}
    cfgmod.save(cfg)

    controller = None
    fired = []
    rested = []
    snoozed = []

    try:
        from rreminder.app import Application

        controller = Application(app)
        controller.scheduler.reminderDue.connect(
            lambda level, ctx: fired.append((level, ctx.get("phase"), ctx.get("overtime_min")))
        )
        controller.scheduler.restConfirmed.connect(
            lambda reason, info: rested.append((reason, info))
        )
        controller.scheduler.snoozed.connect(
            lambda minutes, when: snoozed.append((minutes, when))
        )

        if not controller.start():
            log("FAIL  单实例锁：应当由本进程取得")
            return 1
        log("PASS  应用启动，单实例锁取得")

        status = controller.scheduler.status()
        log(
            "PASS  窗口识别  -> 生效={} 阶段={} 距目标={} 分钟".format(
                status["in_window"], status["phase"], status["remain_min"]
            )
        )
        assert status["in_window"], "测试窗口应当处于生效状态"
        assert status["phase"] == "overtime", status["phase"]

        def pump(ms: int) -> None:
            loop = QEventLoop()
            QTimer.singleShot(ms, loop.quit)
            loop.exec()

        # 第一次 tick 会先建立会话，此时会把 _next_fire 置空，必须先让它跑完
        pump(1400)
        assert controller.scheduler._session_rule_id == "e2e", "会话应当已建立"

        # 现在把下次触发时间设成"现在"，下一次 tick 就应当立刻命中
        controller.scheduler._next_fire = datetime.now()
        pump(1800)

        assert fired, "3.2 秒内应当至少触发一次提醒"
        level, phase, overtime = fired[0]
        log(
            "PASS  提醒实际触发  -> 等级={} 阶段={} 超时={} 分钟".format(
                level, phase, overtime
            )
        )
        assert level == 2, "关闭全屏后最高等级应为弹窗(2)，实际 {}".format(level)

        assert controller.popup is not None, "弹窗对象未被创建"
        visible = controller.popup.isVisible()
        log("PASS  弹窗已呈现  -> 可见={}".format(visible))
        assert visible, "弹窗应当处于可见状态"

        # 验证"再给我 5 分钟"这条路径
        controller.snooze(5)
        assert snoozed, "snooze 应当发出信号"
        assert controller.scheduler.status()["next_fire"] is not None
        log("PASS  稍后提醒  -> 记录 {} 分钟，下次触发时间已排定".format(snoozed[0][0]))

        assert controller.wrapup is not None, "收尾倒计时窗口未被创建"
        log("PASS  收尾倒计时  -> 窗口已创建，可见={}".format(controller.wrapup.isVisible()))

        # 验证确认休息这条路径
        controller.rest_now()
        assert rested, "confirm_rest 应当发出信号"
        assert rested[0][0] == "manual", rested[0]
        log(
            "PASS  确认休息  -> 原因={} 时间={} 超时={} 分钟".format(
                rested[0][0], rested[0][1].get("time"), rested[0][1].get("overtime_min")
            )
        )
        assert controller.scheduler.status()["rested_today"] is True
        log("PASS  今日状态  -> rested_today = True")

        # 点「我去睡了」之后必须弹出可撤销的窗口，否则就是个摆设
        assert controller.rest_window is not None, "确认休息后应当弹出撤销窗口"
        pump(700)
        assert controller.rest_window.isVisible(), "撤销窗口应当可见"
        log(
            "PASS  撤销窗口  -> 已弹出，按钮文案「{}」".format(
                controller.rest_window.cancel_btn.text()
            )
        )

        # 撤销后必须真的回到继续提醒的状态
        controller.undo_rest()
        status_after_undo = controller.scheduler.status()
        assert status_after_undo["rested_today"] is False, "撤销后不应仍是已休息"
        assert status_after_undo["state"] == "idle", status_after_undo
        log("PASS  撤销休息  -> rested_today 回到 False，提醒继续")
        assert not controller.rest_window.isVisible(), "撤销后撤销窗口应当关闭"
        log("PASS  撤销窗口关闭  -> 可见=False")

        # 收尾模式：手动触发后按固定节奏催，取消能回退
        wind_fired = []
        controller.scheduler.reminderDue.connect(
            lambda level, ctx: wind_fired.append((level, bool(ctx.get("winddown"))))
        )
        controller.start_winddown()
        assert controller.scheduler.is_winddown(), "应当进入收尾模式"
        plan = (controller.scheduler.status().get("winddown") or {}).get("plan_text", "")
        log("PASS  收尾模式启动  -> 节奏 {}".format(plan))
        assert plan, "收尾模式应当给出节奏说明"

        controller.scheduler._next_fire = datetime.now()
        pump(1600)
        assert wind_fired, "收尾模式应当触发提醒"
        assert wind_fired[-1][1] is True, "收尾提醒的上下文必须标记 winddown"
        step = (controller.scheduler.status().get("winddown") or {}).get("step")
        log("PASS  收尾提醒触发  -> 等级={} 已推进 {} 格".format(wind_fired[-1][0], step))

        # 「延后提醒」的完整信号链：窗口按钮 → 控制器 → 调度器
        controller.defer_winddown()
        wd = controller.scheduler.winddown_status()
        assert controller.scheduler.is_winddown(), "延后不应退出收尾模式"
        assert wd.get("defers") == 1, wd
        assert not controller.overlay.visible, "延后后遮罩应当收起"
        assert controller.popup is None or not controller.popup.isVisible(), "延后后弹窗应当收起"
        log(
            "PASS  延后提醒  -> 收尾继续，档位不变，推迟约 {} 分钟".format(
                int(wd.get("next_in_sec", 0)) // 60
            )
        )

        controller.cancel_winddown()
        assert not controller.scheduler.is_winddown(), "取消后应当退出收尾模式"
        assert controller.scheduler.status()["phase"] != "winddown", "阶段应当还原"
        log("PASS  收尾模式取消  -> 已回到正常节奏")

        # 验证暂停/恢复：暂停后必须能立刻恢复
        controller.pause(60)
        assert controller.scheduler.status()["paused"] is True
        pump(700)
        assert controller.pause_ui is not None, "暂停后应当弹出暂停提示窗"
        assert controller.pause_ui.isVisible(), "暂停提示窗应当可见"
        assert "暂停" in controller.pause_ui.head.text(), controller.pause_ui.head.text()
        btn_text = controller.pause_ui.cancel_btn.text()
        assert "立即恢复" in btn_text, "暂停窗应当提供立即恢复，实际: {}".format(btn_text)
        log("PASS  暂停提示窗  -> 按钮文案「{}」".format(btn_text))

        # 点窗上的「立即恢复」应当真的恢复
        controller.pause_ui.cancel_btn.click()
        assert controller.scheduler.status()["paused"] is False, "点立即恢复后应当恢复"
        assert not controller.pause_ui.isVisible(), "恢复后暂停窗应当关闭"
        log("PASS  立即恢复  -> 暂停状态已取消，窗口已关闭")

        # 从设置/托盘走恢复路径也要能关掉暂停窗
        controller.pause(60)
        assert controller.pause_ui.isVisible(), "再次暂停应当重新弹出"
        controller.resume()
        assert controller.scheduler.status()["paused"] is False
        assert not controller.pause_ui.isVisible(), "恢复后暂停窗应当关闭"
        log("PASS  暂停与恢复  -> 两条恢复路径都正常")

        # 验证重复启动会被单实例挡住
        from PySide6.QtNetwork import QLocalSocket

        probe = QLocalSocket()
        probe.connectToServer(app_module.SERVER_NAME)
        connected = probe.waitForConnected(500)
        probe.disconnectFromServer()
        log("PASS  单实例通道  -> 第二个实例可连上并唤出已有窗口 = {}".format(connected))
        assert connected, "单实例命名管道应当可连接"

    except Exception as exc:  # noqa: BLE001
        import traceback

        log("FAIL  {}".format(exc))
        log(traceback.format_exc())
        return 1
    finally:
        if controller is not None:
            try:
                controller._hide_all_alerts()
                controller.scheduler.stop()
                controller.overlay.hide()
                controller.tray.hide()
            except Exception:
                pass
            try:
                controller.settings_window and controller.settings_window.close()
            except Exception:
                pass
        # 配置全部写在临时目录里，这里不需要还原任何东西
        app.quit()
        # 无论成败都要留下报告，否则失败时看不到原因
        try:
            with open(REPORT, "w", encoding="utf-8") as fh:
                fh.write("\n".join(LINES))
        except Exception:
            pass

    log("")
    log("端到端链路全部通过")
    return 0


if __name__ == "__main__":
    import traceback

    code = 1
    try:
        code = main()
    except BaseException:  # noqa: BLE001
        LINES.append("")
        LINES.append("main() 之外抛出未捕获异常：")
        LINES.append(traceback.format_exc())
        code = 1
    finally:
        # 不管发生什么，报告一定要落盘，否则失败了什么都看不到
        try:
            with open(REPORT, "w", encoding="utf-8") as fh:
                fh.write("\n".join(LINES))
        except Exception:
            pass
    raise SystemExit(code)
