"""自检脚本：逐项验证核心逻辑与界面构建，不依赖人工点击。

    python tools/selftest.py

结果同时打印到控制台并写入项目根目录的 _selftest.txt。
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# Windows 上控制台默认走本地代码页（GitHub runner 上是 cp1252），
# print 中文会直接抛 UnicodeEncodeError，把测试脚本整个搞崩 ——
# 而且崩在 print 里，报告文件都来不及写。
# 统一把标准输出改成 UTF-8，并对无法编码的字符降级替换。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPORT = os.path.join(ROOT, "_selftest.txt")
LINES = []


def _scrub(text: str) -> str:
    """把本机专属路径换成占位符。

    测试报告经常被贴进 issue 或被提交进仓库，不该把「C:\\Users\\真名」
    这类信息带出去。在这里统一处理，以后新增的测试也不用手动留意。
    """
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
    text = _scrub(text)
    try:
        print(text)
    except Exception:
        # 控制台编码不支持中文时（GitHub runner 默认 cp1252），退化成 ASCII 也要打出来。
        # 「打印」这件事绝不能成为测试失败的原因 —— 报告文件本身写的是 UTF-8，不受影响。
        try:
            print(text.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass
    LINES.append(text)


def check(name: str, fn) -> None:
    try:
        detail = fn()
        log("PASS  {}{}".format(name, "  -> " + str(detail) if detail else ""))
    except Exception:
        log("FAIL  {}".format(name))
        log(traceback.format_exc())


import rreminder.config as cfgmod  # noqa: E402
import rreminder.scheduler as sched  # noqa: E402
from rreminder import assets, winapi  # noqa: E402
from rreminder.hotkey import parse_sequence  # noqa: E402

# 整个自检过程一律读写临时目录里的配置，
# 绝不碰用户真实的 config.json（否则测试之间会互相污染，
# 甚至把测试用的 after_rest=shutdown 留在真实配置里）。
SCRATCH_DIR = tempfile.mkdtemp(prefix="rr_selftest_")
cfgmod.data_dir = lambda: SCRATCH_DIR
cfgmod.config_path = lambda: os.path.join(SCRATCH_DIR, "config.json")


class FakeNow(datetime):
    """把调度器里的 datetime.now() 钉死在指定时刻。"""

    current = datetime(2026, 9, 17, 22, 10)

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return cls.current


sched.datetime = FakeNow

RULE = {
    "id": "t1",
    "enabled": True,
    "name": "测试",
    "days": [0, 1, 2, 3, 4, 5, 6],
    "start": "22:00",
    "bedtime": "23:30",
    "end": "00:30",
    "interval_min": 30,
    "ramp": True,
    "jitter": 0.15,
}


def make_cfg(**overrides):
    cfg = cfgmod.load()
    cfg["rules"] = [dict(RULE)]
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


# ---------------------------------------------------------------- 逻辑测试


def t_phase_math():
    rows = []
    for moment, expect_phase, expect_remain in (
        (datetime(2026, 9, 17, 22, 10), "pre", 80),
        (datetime(2026, 9, 17, 23, 0), "ramp", 30),
        (datetime(2026, 9, 17, 23, 45), "overtime", -15),
        (datetime(2026, 9, 18, 0, 10), "overtime", -40),
        (datetime(2026, 9, 17, 12, 0), "", 0),
    ):
        phase = cfgmod.rule_phase(RULE, moment)
        remain = cfgmod.minutes_to_bedtime(RULE, moment)
        assert phase == expect_phase, "{} 阶段应为 {}，实际 {}".format(
            moment, expect_phase, phase
        )
        if expect_phase:
            assert remain == expect_remain, "{} 剩余应为 {}，实际 {}".format(
                moment, expect_remain, remain
            )
        rows.append("{}={}".format(moment.strftime("%H:%M"), phase))
    return " ".join(rows)


def t_levels():
    cfg = make_cfg()
    for key in cfg["reminder"]["channels"]:
        cfg["reminder"]["channels"][key] = True

    now = FakeNow.current
    s = sched.Scheduler(cfg)
    got = {
        "pre": s._compute_level(RULE, "pre", now),
        "ramp": s._compute_level(RULE, "ramp", now),
        "overtime": s._compute_level(RULE, "overtime", now),
    }
    assert got == {"pre": 1, "ramp": 2, "overtime": 3}, got

    cfg2 = make_cfg(reminder={"strength": "gentle"})
    s2 = sched.Scheduler(cfg2)
    gentle = s2._compute_level(RULE, "overtime", now)
    assert gentle == 2, "温和模式最高只能到弹窗，实际 {}".format(gentle)

    cfg3 = make_cfg()
    cfg3["reminder"]["channels"]["overlay"] = False
    s3 = sched.Scheduler(cfg3)
    no_overlay = s3._compute_level(RULE, "overtime", now)
    assert no_overlay == 2, "关掉全屏后最高应为弹窗，实际 {}".format(no_overlay)

    return "pre=1 ramp=2 overtime=3 gentle={} noOverlay={}".format(gentle, no_overlay)


def t_snooze_escalation():
    cfg = make_cfg()
    s = sched.Scheduler(cfg)
    now = FakeNow.current

    before = s._compute_level(RULE, "pre", now)
    s.snooze(5)
    after1 = s._compute_level(RULE, "pre", now)
    s.snooze(5)
    s.snooze(5)
    s.snooze(5)
    many_pre = s._compute_level(RULE, "pre", now)
    many_ramp = s._compute_level(RULE, "ramp", now)
    many_overtime = s._compute_level(RULE, "overtime", now)

    assert before == 1, before
    assert after1 == 2, "被忽略一次应升一级，实际 {}".format(after1)
    assert many_pre == 2, "预热期要有上限，不能提前一小时就全屏，实际 {}".format(many_pre)
    assert many_ramp == 3, "收紧期连续忽略应拉到最强，实际 {}".format(many_ramp)
    assert many_overtime == 3, many_overtime
    assert abs(s._interval_factor - 0.25) < 1e-9, "间隔倍率下限应为 0.25，实际 {}".format(
        s._interval_factor
    )

    s.confirm_rest("test")
    assert s._snoozes == 0 and s._interval_factor == 1.0
    return "预热 1->{} (上限{})，收紧 {}，超时 {}，倍率触底 0.25 后归零".format(
        after1, many_pre, many_ramp, many_overtime
    )


def t_intervals():
    cfg = make_cfg()
    cfg["reminder"]["min_interval_min"] = 5
    s = sched.Scheduler(cfg)

    pre_low, pre_high = None, None
    samples = [s._compute_interval(RULE, "pre", FakeNow.current) for _ in range(40)]
    pre_low, pre_high = min(samples), max(samples)
    assert 25.0 <= pre_low and pre_high <= 35.0, (pre_low, pre_high)

    near = FakeNow.current.replace(hour=23, minute=28)
    near_interval = s._compute_interval(RULE, "ramp", near)
    far = FakeNow.current.replace(hour=22, minute=46)
    far_interval = s._compute_interval(RULE, "ramp", far)
    assert near_interval < far_interval, (near_interval, far_interval)

    overtime = s._compute_interval(RULE, "overtime", datetime(2026, 9, 17, 23, 50))
    assert abs(overtime - 5.0) < 0.001, overtime

    cfg["smart"]["random_jitter"] = False
    s4 = sched.Scheduler(cfg)
    fixed = s4._compute_interval(RULE, "pre", FakeNow.current)
    assert abs(fixed - 30.0) < 0.001, fixed

    return "预热 {:.1f}-{:.1f} 分钟，收紧 {:.1f}→{:.1f}，超时 {:.1f}".format(
        pre_low, pre_high, far_interval, near_interval, overtime
    )


def t_window_end_and_grace():
    now = datetime(2026, 9, 17, 22, 10)
    end = sched.Scheduler._compute_window_end(RULE, now)
    assert end == datetime(2026, 9, 18, 0, 30), end

    late = datetime(2026, 9, 18, 0, 10)
    end2 = sched.Scheduler._compute_window_end(RULE, late)
    assert end2 == datetime(2026, 9, 18, 0, 30), end2
    return "22:10 起算结束于 {}，00:10 起算结束于 {}".format(end, end2)


def t_active_rule_grace():
    cfg = make_cfg(bedtime={"overtime_grace_min": 60})
    s = sched.Scheduler(cfg)

    inside = datetime(2026, 9, 18, 0, 20)
    assert s._active_rule(inside) is not None, "窗口内应生效"

    grace = datetime(2026, 9, 18, 1, 20)
    assert s._active_rule(grace) is not None, "结束后 50 分钟应仍在宽限期内"

    outside = datetime(2026, 9, 18, 2, 30)
    assert s._active_rule(outside) is None, "超出宽限期应放弃"
    return "窗口内生效 / 结束后 50 分钟仍追 / 2 小时半后放弃"


def t_rest_detection():
    cfg = make_cfg()
    s = sched.Scheduler(cfg)
    fired = {}
    s.restConfirmed.connect(lambda reason, info: fired.update({"reason": reason, "info": info}))
    s.confirm_rest("manual")
    assert fired.get("reason") == "manual", fired
    assert s.status().get("rested_today") is True
    history = cfg.get("history") or {}
    assert history.get("last_time"), history
    return "确认休息 -> 记录 {} ，超时 {} 分钟".format(
        history.get("last_time"), history.get("overtime_min")
    )


def t_undo_rest():
    cfg = make_cfg()
    s = sched.Scheduler(cfg)

    s.confirm_rest("manual")
    assert s.status()["rested_today"] is True, "确认休息后应为已休息"
    assert (cfg.get("history") or {}).get("last_time"), "确认休息应当写下历史记录"

    s.undo_rest()
    assert s.status()["rested_today"] is False, "撤销后不应再是已休息"
    assert not (cfg.get("history") or {}).get("last_time"), "撤销后应当清掉历史记录"
    assert s._next_fire is None and s._snoozes == 0, "撤销后应当重新开始计一轮"
    return "确认休息 -> 撤销 -> 状态与历史记录都已还原"


def t_shutdown_roundtrip():
    """真的调用一次系统关机再立刻取消，确认这条路走得通。

    用 30 分钟的延迟，并且同一函数内立刻撤销两次，确保不会真的关机。
    """
    winapi.abort_shutdown()  # 先清场
    scheduled = winapi.schedule_shutdown(1800, "休息提醒 自检")
    cancelled = winapi.abort_shutdown()
    winapi.abort_shutdown()  # 再撤一次兜底
    assert scheduled, "schedule_shutdown 返回失败，关机排定不可用"
    assert cancelled, "取消关机失败"
    return "安排 30 分钟后关机并立即取消成功（当前无待执行关机）"


def t_shutdown_after_rest_flow():
    """after_rest 配置项存在且默认不关机。"""
    cfg = make_cfg()
    conf = cfg.get("after_rest")
    assert isinstance(conf, dict), "缺少 after_rest 配置块"
    assert conf.get("mode") in ("none", "shutdown"), conf
    assert 30 <= int(conf.get("grace_sec", 0)) <= 900, conf
    return "mode={} grace={} 秒".format(conf.get("mode"), conf.get("grace_sec"))


def t_confirm_dialog_renders():
    """验证确认对话框真的能弹出来、尺寸正常、位置在屏幕内。

    托盘「退出」点了没反应，根因就是这类对话框拿不到输入或尺寸异常。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from rreminder import theme as thememod
    from rreminder.ui.settings_win import BaseDialog, ask_confirm

    palette = thememod.palette("dark")
    captured = {}

    def probe() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, BaseDialog) and widget.isVisible():
                captured["visible"] = True
                captured["size"] = (widget.width(), widget.height())
                captured["pos"] = (widget.x(), widget.y())
                widget.reject()
                return
        captured["visible"] = False

    QTimer.singleShot(700, probe)
    result = ask_confirm(None, palette, "自检", "这是一个测试对话框")

    assert captured.get("visible"), "确认对话框没有出现在屏幕上"
    width, height = captured["size"]
    assert width >= 300 and height >= 120, "对话框尺寸异常: {}".format(captured["size"])
    x, y = captured["pos"]
    assert -2000 < x < 20000 and -2000 < y < 20000, "对话框位置异常: {}".format(
        captured["pos"]
    )
    assert result is False, "点关闭后应当返回 False"
    return "对话框正常弹出 {}x{} @ ({}, {})".format(width, height, x, y)


def t_rest_window():
    """关机倒计时窗口与撤销按钮。"""
    from PySide6.QtCore import QEventLoop, QTimer

    from rreminder import theme as thememod
    from rreminder.ui.alerts import RestCountdownWindow

    palette = thememod.palette("dark")

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    fired = {"count": 0}
    win = RestCountdownWindow(palette, False)
    win.cancelled.connect(lambda: fired.__setitem__("count", fired["count"] + 1))
    win.start(datetime.now() + timedelta(seconds=8), 8, True, "23:12", "23:30")
    pump(1100)
    assert win.isVisible(), "关机倒计时窗口应当可见"
    shown = win.time_label.text()
    assert ":" in shown, "倒计时文本异常: {}".format(shown)
    win._on_cancel()
    assert fired["count"] == 1, "点取消应当发出 cancelled 信号"
    assert not win.isVisible(), "取消后窗口应当隐藏"

    win2 = RestCountdownWindow(palette, False)
    win2.start(datetime.now() + timedelta(seconds=90), 90, False, "23:12", "23:30")
    pump(600)
    assert win2.isVisible(), "普通撤销窗口应当可见"
    text = win2.cancel_btn.text()
    assert "撤销" in text, "未开自动关机时应提供撤销按钮，实际: {}".format(text)
    win2.stop()

    # 暂停形态：必须给出「立即恢复提醒」，否则暂停就成了回不来的状态
    win3 = RestCountdownWindow(palette, False)
    resumed = {"count": 0}
    win3.resumeRequested.connect(lambda: resumed.__setitem__("count", resumed["count"] + 1))
    win3.start(datetime.now() + timedelta(seconds=120), 120, False, "", "", mode="pause")
    pump(600)
    assert win3.isVisible(), "暂停提示窗应当可见"
    assert "暂停" in win3.head.text(), win3.head.text()
    assert "立即恢复" in win3.cancel_btn.text(), win3.cancel_btn.text()
    win3._on_cancel()
    assert resumed["count"] == 1, "点按钮应当发出 resumeRequested 而不是 cancelled"
    assert not win3.isVisible(), "取消后应当隐藏"

    return "倒计时读数 {}，休息/暂停两种形态与撤销按钮均正常".format(shown)


def t_scheduler_undo_signal_path():
    """控制器侧的撤销路径：撤销后应当重新排期而不是就此沉默。"""
    cfg = make_cfg()
    s = sched.Scheduler(cfg)
    cfg["after_rest"] = {"mode": "shutdown", "grace_sec": 120}
    s.confirm_rest("manual")
    s.undo_rest()
    st = s.status()
    assert st["rested_today"] is False, st
    assert st["state"] == "idle", st
    return "撤销后状态回到 idle，仍会在窗口内继续提醒"


def t_winddown_sequence():
    cfg = make_cfg()
    s = sched.Scheduler(cfg)

    intervals = s._winddown_intervals()
    assert intervals == [5.0, 2.5, 2.0, 1.0], intervals

    waits = [s._winddown_interval_for(step) for step in range(5)]
    assert waits == [5.0, 2.5, 2.0, 1.0, 1.0], waits

    levels = [s._winddown_level_for(step) for step in range(5)]
    assert levels == [1, 2, 3, 3, 3], levels

    plan = s._winddown_plan_text()
    assert "5 分钟" in plan and "2 分 30 秒" in plan and "之后每分钟" in plan, plan
    return "间隔 {}，强度 {}".format(waits, levels)


def t_winddown_lifecycle():
    cfg = make_cfg()
    cfg["reminder"]["channels"] = {
        "toast": True,
        "popup": True,
        "overlay": True,
        "sound": False,
    }
    cfg["reminder"]["strength"] = "standard"
    s = sched.Scheduler(cfg)

    fired = []
    s.reminderDue.connect(
        lambda level, ctx: fired.append(
            (level, bool(ctx.get("winddown")), ctx.get("step"))
        )
    )
    changes = []
    s.windDownChanged.connect(lambda active, snap: changes.append(active))

    original_idle = winapi.idle_seconds
    winapi.idle_seconds = lambda: 0.0
    try:
        s.start_winddown()
        assert s.is_winddown(), "start_winddown 之后应当处于收尾模式"
        assert changes == [True], changes
        assert s.status()["phase"] == "winddown", s.status()["phase"]

        for _ in range(5):
            s._next_fire = FakeNow.current
            s._tick_winddown(FakeNow.current)

        steps = [item[2] for item in fired]
        levels = [item[0] for item in fired]
        assert steps == [0, 1, 2, 3, 4], steps
        assert levels == [1, 2, 3, 3, 3], levels
        assert all(item[1] for item in fired), "上下文里必须标记 winddown"

        # 收尾模式的节奏是固定的，不接受拖延
        before = s._next_fire
        s.snooze(5)
        assert s._next_fire == before, "收尾模式下 snooze 不应生效"

        s.cancel_winddown()
        assert not s.is_winddown(), "取消后应当退出收尾模式"
        assert changes == [True, False], changes
        assert s.winddown_status()["active"] is False

        s.start_winddown()
        s.finish_winddown("manual")
        assert not s.is_winddown(), "完成后应当退出收尾模式"
        assert s.status().get("rested_today") is True, "完成后应当记为已休息"
    finally:
        winapi.idle_seconds = original_idle

    return "推进 5 格得到强度 {}，取消与完成都正常".format(levels)


def t_winddown_defer():
    """「延后提醒」：只推迟下一次，档位 / 强度 / 节奏都不变。

    与早睡 snooze 的关键区别：那个会缩短间隔并升级强度（惩罚拖延），
    这个不该惩罚 —— 用户已经说要收尾了，只是手头还差一点。
    """
    cfg = make_cfg()
    cfg["winddown"] = {
        "intervals_min": [5, 2.5, 2, 1],
        "level_mode": "ramp",
        "defer_min": 7,
    }
    s = sched.Scheduler(cfg)

    original_idle = winapi.idle_seconds
    winapi.idle_seconds = lambda: 0.0
    try:
        s.start_winddown()
        for _ in range(2):
            s._next_fire = FakeNow.current
            s._tick_winddown(FakeNow.current)
        assert s._winddown_step == 2, s._winddown_step

        level_before = s._winddown_level_for(s._winddown_step - 1)
        s._alerting = True

        started = FakeNow.current
        s.defer_winddown()

        assert s._winddown_step == 2, "延后不该改变档位: {}".format(s._winddown_step)
        assert s._alerting is False, "延后后应退出告警状态"
        assert s.winddown_status()["defers"] == 1, s.winddown_status()
        assert s._interval_factor == 1.0, "延后不该像 snooze 那样压缩间隔"
        assert s._winddown_level_for(s._winddown_step - 1) == level_before, "强度曲线不该变"
        wait = (s._next_fire - started).total_seconds() / 60.0
        assert 6.9 <= wait <= 7.1, "下次触发应推迟约 7 分钟，实际 {:.2f}".format(wait)
    finally:
        winapi.idle_seconds = original_idle
    return "档位保持 {}，下次推迟 {:.1f} 分钟（设定 7），强度不变".format(
        s._winddown_step, wait
    )


def t_winddown_strength_independent():
    """收尾模式的强度不该被早睡的「强制程度」压住。

    这是实际反馈过的问题：早睡设成「温和」（最高只到弹窗）之后，
    收尾模式想升到全屏也被连带压住了 —— 两个功能应当各自独立。
    """
    cfg = make_cfg()
    cfg["reminder"]["channels"] = {
        "toast": True,
        "popup": True,
        "overlay": True,
        "sound": False,
    }
    cfg["reminder"]["strength"] = "gentle"  # 早睡上限 = 弹窗
    cfg["winddown"] = {"intervals_min": [1], "level_mode": "always_full"}
    s = sched.Scheduler(cfg)

    assert s._resolve_level(3, s._capabilities()) == 2, "早睡应被温和档限制在弹窗"
    assert s._resolve_level(3, s._capabilities(ignore_strength=True)) == 3, (
        "收尾模式应能升到全屏"
    )

    fired = []
    s.reminderDue.connect(lambda level, ctx: fired.append(level))
    original_idle = winapi.idle_seconds
    winapi.idle_seconds = lambda: 0.0
    try:
        s.start_winddown()
        s._next_fire = FakeNow.current
        s._tick_winddown(FakeNow.current)
    finally:
        winapi.idle_seconds = original_idle

    assert fired and fired[0] == 3, "收尾模式实测等级应为 3（全屏），实际 {}".format(fired)
    return "早睡上限=弹窗(2)，收尾模式实测={}（全屏）".format(fired[0])


def t_winddown_idle_finishes():
    """收尾模式里人离开了电脑，应当自动算作已收。"""
    cfg = make_cfg()
    s = sched.Scheduler(cfg)
    s.reload()
    s.start_winddown()

    original_idle = winapi.idle_seconds
    winapi.idle_seconds = lambda: 9999.0
    try:
        s._tick_winddown(FakeNow.current)
    finally:
        winapi.idle_seconds = original_idle

    assert not s.is_winddown(), "离开电脑后应当自动结束收尾模式"
    assert s.status().get("rested_today") is True
    return "检测到离开电脑 -> 自动结束并记为已休息"


def t_settings_winddown_and_shutdown_ui():
    from rreminder import theme as thememod
    from rreminder.ui.settings_win import SettingsWindow

    cfg = make_cfg()
    win = SettingsWindow(cfg, thememod.palette("dark"), False)

    # 「我要收了」按钮
    assert hasattr(win, "btn_winddown"), "设置窗口应当有「我要收了」按钮"
    assert win.btn_winddown.text() == "我要收了", win.btn_winddown.text()
    seen = []
    win.windDownRequested.connect(lambda: seen.append(True))
    win.btn_winddown.click()
    assert seen == [True], "点击应当发出 windDownRequested"

    # 自动关机切换必须找得到（用户反馈找不到）
    assert hasattr(win, "seg_after"), "设置窗口应当有「自动关机」切换"
    assert win.seg_after.items == ["只记录", "自动关机"], win.seg_after.items
    win.seg_after.setCurrentIndex(1, emit=True)
    assert cfg["after_rest"]["mode"] == "shutdown", cfg["after_rest"]
    win.seg_after.setCurrentIndex(0, emit=True)
    assert cfg["after_rest"]["mode"] == "none", cfg["after_rest"]

    # 收尾模式在状态栏要走独立分支
    win.refresh_status(
        {"winddown": {"active": True, "elapsed_sec": 200, "next_in_sec": 90}}
    )
    assert "收尾模式" in win.status_label.text(), win.status_label.text()

    # 「收尾模式」独立页面：时间间隔可编辑
    assert win.stack.count() == 5, win.stack.count()
    assert win._nav_buttons[3].text() == "收尾模式", win._nav_buttons[3].text()
    assert len(win.wind_spins) == 4, len(win.wind_spins)
    assert [round(s.value(), 2) for s in win.wind_spins] == [5.0, 2.5, 2.0, 1.0], [
        s.value() for s in win.wind_spins
    ]

    win.wind_spins[0].setValue(7.0)
    assert cfg["winddown"]["intervals_min"][0] == 7.0, cfg["winddown"]["intervals_min"]
    win.wind_spins[0].setValue(5.0)

    win._change_wind_steps(1)
    assert len(cfg["winddown"]["intervals_min"]) == 5, cfg["winddown"]["intervals_min"]
    assert len(win.wind_spins) == 5, len(win.wind_spins)
    win._change_wind_steps(-1)
    assert len(cfg["winddown"]["intervals_min"]) == 4, cfg["winddown"]["intervals_min"]

    # 强度渐进曲线可切换
    win.seg_level_mode.setCurrentIndex(2, emit=True)
    assert cfg["winddown"]["level_mode"] == "always_full", cfg["winddown"]
    win.seg_level_mode.setCurrentIndex(0, emit=True)
    assert cfg["winddown"]["level_mode"] == "ramp", cfg["winddown"]
    assert win.seg_level_mode.sizeHint().width() >= 180, win.seg_level_mode.sizeHint()

    win.close()
    win.deleteLater()
    return "收尾页面 4 档间隔、加减档、强度曲线、自动关机切换全部正常"


def t_winddown_level_modes():
    """三种强度曲线的实际档位序列。"""
    expected_map = {
        "ramp": [1, 2, 3, 3, 3],
        "from_popup": [2, 3, 3, 3, 3],
        "always_full": [3, 3, 3, 3, 3],
    }
    got_map = {}
    for mode, expected in expected_map.items():
        cfg = make_cfg(winddown={"intervals_min": [5, 2.5, 2, 1], "level_mode": mode})
        s = sched.Scheduler(cfg)
        got = [s._winddown_level_for(step) for step in range(5)]
        assert got == expected, "{}: 期望 {} 实际 {}".format(mode, expected, got)
        got_map[mode] = got
    return " / ".join("{}={}".format(k, v) for k, v in got_map.items())


def t_winddown_custom_intervals():
    """自定义档位数与间隔之后，节奏和计划文案要跟着变。"""
    cfg = make_cfg(winddown={"intervals_min": [8, 4, 1.5], "level_mode": "ramp"})
    s = sched.Scheduler(cfg)

    assert s._winddown_intervals() == [8.0, 4.0, 1.5], s._winddown_intervals()
    assert s._winddown_interval_for(2) == 1.5
    assert s._winddown_interval_for(3) == 1.5, "走完最后一档应当保持最短档"
    assert s._winddown_interval_for(9) == 1.5

    plan = s._winddown_plan_text()
    assert "8 分钟" in plan and "4 分钟" in plan and "1 分 30 秒" in plan, plan

    # 空列表 / 脏数据要能回退到默认
    bad = make_cfg(winddown={"intervals_min": [], "level_mode": "ramp"})
    assert sched.Scheduler(bad)._winddown_intervals() == [5.0, 2.5, 2.0, 1.0]
    dirty = make_cfg(winddown={"intervals_min": ["x", 3, -1], "level_mode": "ramp"})
    assert sched.Scheduler(dirty)._winddown_intervals() == [3.0]
    return "自定义 3 档 -> {}；脏数据可回退".format(plan)


def t_pause_resume():
    cfg = make_cfg()
    s = sched.Scheduler(cfg)
    s.pause(60)
    assert s.status()["paused"] is True
    s.resume()
    assert s.status()["paused"] is False
    return "暂停 60 分钟后可恢复"


def t_defer():
    cfg = make_cfg()
    s = sched.Scheduler(cfg)
    s.defer(10)
    assert s.status()["next_fire"] is not None
    assert s._alerting is False
    return "全屏避让可把提醒推后"


def t_config_roundtrip():
    original = cfgmod.config_path
    tmp = os.path.join(tempfile.mkdtemp(prefix="rr_cfg_"), "config.json")
    try:
        cfgmod.config_path = lambda: tmp
        cfg = cfgmod.load()
        cfg["reminder"]["snooze_options"] = [3, 9, 27]
        assert cfgmod.save(cfg)
        again = cfgmod.load()
        assert again["reminder"]["snooze_options"] == [3, 9, 27], again["reminder"]
        assert again["rules"][0]["bedtime"] == "23:30"

        cfgmod.set_password(again, "abc123")
        assert cfgmod.verify_password(again, "abc123")
        assert not cfgmod.verify_password(again, "wrong")
    finally:
        cfgmod.config_path = original
        try:
            os.remove(tmp)
        except Exception:
            pass
    return "保存/读取/密码校验均正常"


def t_password_salt():
    cfg = make_cfg()
    cfgmod.set_password(cfg, "nightowl")
    h1 = cfg["general"]["password_hash"]
    cfgmod.set_password(cfg, "nightowl")
    h2 = cfg["general"]["password_hash"]
    assert h1 != h2, "同一密码两次设置应因盐值不同而不同"
    assert cfgmod.verify_password(cfg, "nightowl")
    cfgmod.set_password(cfg, "")
    assert cfgmod.verify_password(cfg, "anything"), "清除密码后应放行"
    return "盐值随机，清除后放行"


# ---------------------------------------------------------------- 系统能力


def t_idle():
    value = winapi.idle_seconds()
    assert value >= 0
    return "当前空闲 {:.1f} 秒".format(value)


def t_fullscreen_probe():
    value = winapi.foreground_is_fullscreen()
    assert isinstance(value, bool)
    return "前台是否全屏 = {}".format(value)


def t_system_prefs():
    return "系统动画={} 浅色主题={}".format(
        winapi.animations_enabled(), winapi.system_uses_light_theme()
    )


def t_hotkey_parse():
    assert parse_sequence("Ctrl+Alt+R") == (0x0002 | 0x0001, ord("R"))
    assert parse_sequence("Win+Shift+F5") == (0x0008 | 0x0004, 0x74)
    assert parse_sequence("R") is None
    assert parse_sequence("") is None
    return "Ctrl+Alt+R / Win+Shift+F5 解析正常，无修饰键被拒绝"


def t_chime():
    path = assets.chime_path(60)
    assert path and os.path.isfile(path)
    size = os.path.getsize(path)
    assert size > 20000, size
    assets.cleanup_generated_files()
    assert not os.path.isfile(path)
    return "生成 {} 字节并已清理".format(size)


def t_autostart_roundtrip():
    tmp = os.path.join(tempfile.mkdtemp(prefix="rr_lnk_"), "test.lnk")
    ok = winapi.enable_autostart(tmp)
    exists = os.path.isfile(tmp)
    if exists:
        os.remove(tmp)
    return "创建快捷方式 = {}，文件存在 = {}（未触碰真实启动文件夹）".format(ok, exists)


def t_startup_path():
    return "{} 存在={}".format(winapi.startup_dir(), os.path.isdir(winapi.startup_dir()))


# ---------------------------------------------------------------- 界面测试


def t_widgets_construct():
    from PySide6.QtWidgets import QApplication

    from rreminder import theme as thememod
    from rreminder.ui.widgets import Card, Row, Segmented, ToggleSwitch

    palette = thememod.palette("dark")
    switch = ToggleSwitch(True, palette)
    switch.setChecked(False)
    assert switch.isChecked() is False
    seg = Segmented(["温和", "标准", "硬核"], palette, 1)
    assert seg.currentIndex() == 1
    seg.setCurrentIndex(2)
    assert seg.currentIndex() == 2

    # 自绘控件必须给出有效 sizeHint。QWidget 默认的 sizeHint 是无效值，
    # 放进 Row 这种「左边占 stretch」的布局里会被压成 0 宽 —— 看不见的控件。
    assert seg.sizeHint().width() >= 180, seg.sizeHint()
    assert seg.minimumSizeHint().width() > 0, seg.minimumSizeHint()

    Card("测试")
    Row("标题", "说明", ToggleSwitch(False, palette))
    return "开关/分段/卡片构建正常，分段 sizeHint={}".format(seg.sizeHint().width())


def t_segmented_click():
    """真实鼠标点击分段控件：选中块要滑动，且控件自身绝不能被挪走。

    踩过的坑：滑动值属性曾命名为 pos，与 QWidget.pos（QPoint，控件在父容器中
    的位置）重名。动画于是操作到控件真实位置，点一下就 move 到 (1,0) ——
    表现是「控件跳到左边」且「选中块毫无反应」。

    「控件存在性」检查抓不到这类 bug，必须真的点一下并核对控件位置。
    """
    from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from rreminder import theme as thememod
    from rreminder.ui.widgets import Segmented

    QApplication.instance() or QApplication([])
    palette = thememod.palette("dark")
    seg = Segmented(["只记录", "自动关机"], palette, 0)
    seg.resize(seg.sizeHint())
    seg.show()

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(200)
    origin = seg.pos()
    hits = []
    seg.changed.connect(lambda i: hits.append(i))

    # 点右侧那一半（「自动关机」）
    QTest.mouseClick(
        seg,
        Qt.MouseButton.LeftButton,
        pos=QPoint(int(seg.width() * 0.75), seg.height() // 2),
    )
    pump(400)

    assert hits == [1], "点击右侧应发出 changed(1)，实际 {}".format(hits)
    assert seg.currentIndex() == 1, seg.currentIndex()
    assert abs(seg.slide - 1.0) < 0.02, "选中块未滑到位: {}".format(seg.slide)
    assert seg.pos() == origin, "控件自身被移动了: {} -> {}".format(origin, seg.pos())

    # 再点回左侧，确认双向都正常
    QTest.mouseClick(
        seg,
        Qt.MouseButton.LeftButton,
        pos=QPoint(int(seg.width() * 0.25), seg.height() // 2),
    )
    pump(400)
    assert seg.currentIndex() == 0, seg.currentIndex()
    assert abs(seg.slide - 0.0) < 0.02, seg.slide
    assert seg.pos() == origin, seg.pos()

    seg.close()
    seg.deleteLater()
    return "点右/左 -> index 1/0，滑动到位，控件位置恒为 ({}, {})".format(
        origin.x(), origin.y()
    )


def t_no_qt_property_conflict():
    """静态扫描：自绘动画的属性名不能与 Qt 内置属性重名。

    QWidget 自带 pos / size / geometry 等属性。用同名 Property 覆盖它们，
    动画就会去操作控件本身而不是我们的值 —— 且不会报任何错。
    新增自绘控件时这一步能提前拦住同类问题。
    """
    from PySide6.QtWidgets import QWidget

    from rreminder.ui import alerts as alerts_mod
    from rreminder.ui import overlay as overlay_mod
    from rreminder.ui import widgets as widgets_mod

    builtin = set()
    meta = QWidget.staticMetaObject
    while meta is not None:
        for index in range(meta.propertyCount()):
            builtin.add(meta.property(index).name())
        meta = meta.superClass()

    candidates = [
        widgets_mod.Segmented,
        widgets_mod.ToggleSwitch,
        alerts_mod.ThinBar,
        overlay_mod.OverlayWindow,
    ]

    found = []
    conflicts = []
    for cls in candidates:
        for name, value in vars(cls).items():
            if type(value).__name__ != "Property":
                continue
            found.append("{}.{}".format(cls.__name__, name))
            if name in builtin:
                conflicts.append("{}.{}".format(cls.__name__, name))

    assert found, "未扫描到任何自定义动画属性，检查逻辑已失效"
    assert not conflicts, "与 Qt 内置属性重名: {}".format(", ".join(conflicts))
    return "{} 个动画属性均无重名: {}".format(len(found), ", ".join(found))


def t_title_close_button_renders():
    """标题栏关闭按钮必须真的画出图标，不能是空白或缺字形方框。

    踩过的坑：按钮原本用 "✕"（U+2715）字符，很多字体没有这个字形，
    渲染出来是一个空心方块。这里直接数渲染结果里的像素 ——
    只要有图标，必然存在与背景不同的像素。
    """
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from rreminder import theme as thememod
    from rreminder.ui.settings_win import TitleIconButton

    QApplication.instance() or QApplication([])
    palette = thememod.palette("dark")
    btn = TitleIconButton("close", palette)
    assert btn.text() == "", "关闭按钮不应依赖任何字符: {!r}".format(btn.text())
    btn.show()

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pump(200)

    def ink(hover: bool) -> int:
        """把按钮直接渲染进内存位图，数出与背景不同的像素。

        用 `QWidget.render()` 而不是 `grab()`：render 直接调 paintEvent，
        不依赖窗口系统是否真的显示了窗口、有没有派发鼠标事件。
        离屏平台（CI 无人值守环境）下也稳定。
        """
        btn._hover = hover
        image = QImage(btn.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        btn.render(image)
        base = image.pixelColor(1, 1).rgb()
        count = 0
        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).rgb() != base:
                    count += 1
        return count

    normal = ink(False)
    assert normal > 12, "关闭图标几乎没画出像素（多半是字体缺字形）: {}".format(normal)

    hovered = ink(True)
    assert hovered > normal, "悬停时应有整块高亮背景: {} -> {}".format(normal, hovered)

    btn.close()
    btn.deleteLater()
    return "图标像素 {} 个，悬停高亮后 {} 个，全程不依赖字体".format(normal, hovered)


def t_run_log():
    """运行日志：能写、能记异常、能轮转、能被「清除痕迹」删掉。

    为什么需要它：绿色程序没有控制台，出问题时用户手上只有 exe。
    「双击了没反应」如果没有日志就只能靠猜，所以这块必须有测试兜住。
    """
    import shutil

    import rreminder.log as logmod

    tmp = tempfile.mkdtemp(prefix="rrtest-log-")
    old_data_dir = cfgmod.data_dir
    old_cache = logmod._path_cache
    cfgmod.data_dir = lambda: tmp  # type: ignore[assignment]
    logmod._path_cache = None
    path = os.path.join(tmp, "restreminder.log")
    try:
        assert not os.path.isfile(path), "起始状态不该有日志"
        logmod.write("测试一行")
        assert os.path.isfile(path), "日志文件应被创建"
        text = open(path, "r", encoding="utf-8").read()
        assert "测试一行" in text, text
        assert "[20" in text, "应当带时间戳: {}".format(text)

        try:
            raise ValueError("boom")
        except ValueError:
            logmod.write_exception("测试异常")
        text = open(path, "r", encoding="utf-8").read()
        assert "boom" in text and "ValueError" in text, "异常堆栈应被记录"

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x" * (logmod.MAX_BYTES + 10))
        logmod.write("轮转之后")
        assert os.path.isfile(path + ".1"), "超限后应轮转出历史文件"
        assert "轮转之后" in open(path, "r", encoding="utf-8").read()

        assert logmod.clear() is True, "清除应返回 True"
        assert not os.path.isfile(path), "清除后主日志应消失"
        assert not os.path.isfile(path + ".1"), "清除后轮转文件也应消失"
    finally:
        cfgmod.data_dir = old_data_dir  # type: ignore[assignment]
        logmod._path_cache = old_cache
        shutil.rmtree(tmp, ignore_errors=True)
    return "写入 / 异常记录 / 轮转 / 清除 全部正常"


def t_light_theme():
    from rreminder import theme as thememod

    qss = thememod.build_qss(thememod.palette("light"))
    assert "#F4F5F9" in qss
    qss_dark = thememod.build_qss(thememod.palette("dark"))
    assert "#0F1117" in qss_dark
    return "深浅两套样式表生成正常"


def t_settings_window():
    from PySide6.QtCore import QEventLoop, QTimer

    from rreminder import theme as thememod
    from rreminder.ui.settings_win import SettingsWindow

    cfg = make_cfg()
    win = SettingsWindow(cfg, thememod.palette("dark"), False)
    assert win.stack.count() == 5, win.stack.count()
    for index in range(5):
        win.switch_page(index)
    win.apply_theme(thememod.palette("light"))
    win.apply_theme(thememod.palette("dark"))
    win.refresh_status(
        {
            "paused": False,
            "in_window": True,
            "remain_min": 75,
            "rule_name": "早睡提醒",
            "rested_today": False,
        }
    )

    # 真正显示出来再量尺寸：只检查「控件存在」是不够的
    win.show()
    loop = QEventLoop()
    QTimer.singleShot(800, loop.quit)
    loop.exec()

    widths = []
    for name, widget in (
        ("主题", win.seg_theme),
        ("自动关机", win.seg_after),
        ("强制程度", win.seg_strength),
        ("强度渐进", win.seg_level_mode),
    ):
        hint = widget.sizeHint().width()
        actual = widget.width()
        assert hint >= 150, "{} 的 sizeHint 异常: {}".format(name, hint)
        assert actual >= 150, "{} 实际宽度只有 {}，被布局压扁了".format(name, actual)
        widths.append("{}={}px".format(name, actual))

    win.close()
    win.deleteLater()
    return "5 个页面正常，分段控件宽度 " + "、".join(widths)


def t_alert_windows():
    from PySide6.QtCore import QEventLoop, QTimer

    from rreminder import theme as thememod
    from rreminder.ui.alerts import PopupWindow, ToastWindow, WrapupWindow

    palette = thememod.palette("dark")
    ctx = {
        "phase": "overtime",
        "level": 3,
        "bedtime": "23:30",
        "remain_min": -18,
        "overtime_min": 18,
        "snooze_options": [5, 15, 30],
        "tips": ["站起来走两步，让腰背放松一下"],
    }

    toast = ToastWindow(palette, False)
    toast.show_message("测试：角落轻提醒", "这是右下角轻提醒的样子", "#FFB454", 1800)

    popup = PopupWindow(palette, False)
    popup.set_content(ctx, False)
    popup.appear()
    popup.pulse()

    wrap = WrapupWindow(palette, False)
    wrap.start_countdown(datetime.now() + timedelta(minutes=10), 10)

    loop = QEventLoop()
    QTimer.singleShot(1800, loop.quit)
    loop.exec()

    toast.dismiss()
    popup.close_now()
    wrap.stop()

    loop2 = QEventLoop()
    QTimer.singleShot(400, loop2.quit)
    loop2.exec()
    return "轻提醒 / 弹窗 / 收尾倒计时均已实际渲染"


def t_overlay_construct():
    from PySide6.QtGui import QGuiApplication

    from rreminder import theme as thememod
    from rreminder.ui.overlay import OverlayController, OverlayWindow

    palette = thememod.palette("dark")
    screen = QGuiApplication.primaryScreen()
    win = OverlayWindow(screen, palette, False, with_card=True)
    win.update_content(
        {
            "phase": "overtime",
            "bedtime": "23:30",
            "remain_min": -18,
            "overtime_min": 18,
            "snooze_options": [5, 15, 30],
            "tips": ["把手机放到房间另一头"],
            "locked": False,
        }
    )
    win.update_content(
        {
            "phase": "ramp",
            "bedtime": "23:30",
            "remain_min": 24,
            "overtime_min": 0,
            "snooze_options": [5, 15, 30],
            "tips": [],
            "locked": True,
        }
    )
    counts = QGuiApplication.screens()
    win.deleteLater()

    controller = OverlayController(palette, False)
    controller.bind(lambda m: None, lambda: None)
    controller.hide()
    return "遮罩内容构建正常，检测到 {} 块屏幕".format(len(counts))


def t_tray_construct():
    from rreminder import theme as thememod
    from rreminder.ui.tray import TrayController

    tray = TrayController(thememod.palette("dark"))
    tray.refresh(
        {
            "paused": False,
            "in_window": True,
            "remain_min": -5,
            "rested_today": False,
            "paused_until": None,
        }
    )
    tray.refresh(
        {
            "paused": True,
            "in_window": False,
            "remain_min": 0,
            "rested_today": False,
            "paused_until": datetime.now() + timedelta(hours=1),
        }
    )
    available = tray.tray.isSystemTrayAvailable()
    tray.hide()
    tray.deleteLater()
    return "托盘菜单与状态刷新正常，系统托盘可用 = {}".format(available)


def t_icon_render():
    from rreminder import theme as thememod

    for mode in ("dark", "light"):
        icon = assets.app_icon(thememod.palette(mode))
        assert not icon.isNull()
        sizes = icon.availableSizes()
        assert sizes, "图标没有像素图"
    paused = assets.app_icon(thememod.palette("dark"), True)
    assert not paused.isNull()
    return "深浅主题 + 暂停态图标均生成成功"


def t_ico_file():
    path = assets.app_icon_path_ico()
    assert path and os.path.isfile(path), path
    return "{} ({} 字节)".format(path, os.path.getsize(path))


# ---------------------------------------------------------------- 主流程


def main() -> int:
    log("休息提醒 自检报告")
    log("运行时间: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log("=" * 60)
    log("")

    log("[核心逻辑]")
    check("三阶段判定与剩余时间", t_phase_math)
    check("强度等级计算", t_levels)
    check("忽略后升级与归零", t_snooze_escalation)
    check("间隔压缩与随机浮动", t_intervals)
    check("窗口结束时间计算", t_window_end_and_grace)
    check("窗口结束后宽限期", t_active_rule_grace)
    check("休息判定与历史记录", t_rest_detection)
    check("暂停与恢复", t_pause_resume)
    check("全屏避让推后", t_defer)
    check("配置读写往返", t_config_roundtrip)
    check("密码盐值与校验", t_password_salt)
    log("")

    log("[系统能力]")
    check("空闲时间读取", t_idle)
    check("前台全屏检测", t_fullscreen_probe)
    check("系统偏好读取", t_system_prefs)
    check("快捷键解析", t_hotkey_parse)
    check("提示音生成与清理", t_chime)
    check("自启快捷方式创建", t_autostart_roundtrip)
    check("启动文件夹定位", t_startup_path)
    log("")

    log("[界面构建与渲染]")
    check("基础组件", t_widgets_construct)
    check("分段控件真实点击", t_segmented_click)
    check("动画属性无重名", t_no_qt_property_conflict)
    check("关闭按钮图标渲染", t_title_close_button_renders)
    check("运行日志", t_run_log)
    check("图标生成", t_icon_render)
    check("exe 图标文件", t_ico_file)
    check("主题样式表", t_light_theme)
    check("设置窗口五个页面", t_settings_window)
    check("托盘与菜单", t_tray_construct)
    check("全屏遮罩构建", t_overlay_construct)
    check("提醒窗口实际渲染", t_alert_windows)
    log("")

    log("[本次修复验证]")
    check("撤销「已休息」", t_undo_rest)
    check("撤销后重新排期", t_scheduler_undo_signal_path)
    check("关机功能真实可用", t_shutdown_roundtrip)
    check("after_rest 配置完整", t_shutdown_after_rest_flow)
    check("确认对话框可弹出", t_confirm_dialog_renders)
    check("关机倒计时与撤销窗口", t_rest_window)
    check("收尾模式节奏表", t_winddown_sequence)
    check("收尾模式完整流程", t_winddown_lifecycle)
    check("收尾模式延后提醒", t_winddown_defer)
    check("收尾强度独立于早睡", t_winddown_strength_independent)
    check("收尾模式遇离开自动结束", t_winddown_idle_finishes)
    check("收尾按钮与关机切换", t_settings_winddown_and_shutdown_ui)
    check("三种强度曲线", t_winddown_level_modes)
    check("自定义档位与脏数据", t_winddown_custom_intervals)
    log("")

    passed = sum(1 for line in LINES if line.startswith("PASS"))
    failed = sum(1 for line in LINES if line.startswith("FAIL"))
    log("=" * 60)
    log("通过 {} 项，失败 {} 项".format(passed, failed))

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES))
    return 1 if failed else 0


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    _app = QApplication(sys.argv)
    raise SystemExit(main())
