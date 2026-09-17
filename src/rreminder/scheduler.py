"""提醒调度引擎：阶段判定、强度升级、休息判定。

与界面完全解耦，只通过 Qt 信号对外通知，方便单独测试。

三个阶段（以 22:00 开始、23:30 目标睡觉为例）：
    pre       预热期  低频轻提醒，不打断
    ramp      收紧期  越接近目标时间间隔越短、强度越高
    overtime  超时追踪 目标时间已过，间隔压到最短、强度拉满，
                      不提供「今天别再提醒」，直到用户真正停下来
"""

from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from . import config as cfgmod
from . import winapi

LEVEL_TOAST = 1
LEVEL_POPUP = 2
LEVEL_OVERLAY = 3

PHASE_PRE = "pre"
PHASE_RAMP = "ramp"
PHASE_OVERTIME = "overtime"

STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_ALERTING = "alerting"
STATE_PAUSED = "paused"
STATE_RESTED = "rested"
STATE_AWAY = "away"

STRENGTH_GENTLE = "gentle"
STRENGTH_STANDARD = "standard"
STRENGTH_HARDCORE = "hardcore"

REST_MANUAL = "manual"
REST_IDLE = "idle"


class Scheduler(QObject):
    """核心调度器。整个程序只有这一个计时源。"""

    reminderDue = Signal(int, dict)      # level, ctx
    preNoticeDue = Signal(int, dict)     # 距目标睡觉时间还剩几分钟
    snoozed = Signal(int, object)        # 分钟数, 下次触发时间
    restConfirmed = Signal(str, dict)    # 原因, 信息
    statusChanged = Signal(dict)
    windDownChanged = Signal(bool, dict)  # 是否处于收尾模式, 状态快照

    def __init__(self, config: Dict[str, Any], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.cfg = config

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self._state = STATE_IDLE
        self._last_mono = time.monotonic()

        self._paused_until: Optional[datetime] = None
        self._next_fire: Optional[datetime] = None

        self._alerting = False
        self._alert_level = 0
        self._alert_since: Optional[datetime] = None

        self._snoozes = 0
        self._interval_factor = 1.0

        self._rested = False
        self._rested_date: Optional[date] = None

        self._pre_notice_done = False
        self._session_rule_id: Optional[str] = None
        self._session_end_dt: Optional[datetime] = None

        # 收尾模式：手动触发，脱离正常时间段规则独立运行
        self._winddown_active = False
        self._winddown_step = 0
        self._winddown_started: Optional[datetime] = None
        self._winddown_cancels = 0
        self._winddown_defers = 0

        self.reload()

    # ------------------------------------------------------------ 生命周期

    def reload(self) -> None:
        """设置变更后调用。"""
        self._idle_limit = max(
            60, int(self.cfg["bedtime"].get("confirm_idle_min", 10)) * 60
        )
        self._esc_sec = max(10, int(self.cfg["reminder"].get("escalate_after_sec", 45)))

    def start(self) -> None:
        self._last_mono = time.monotonic()
        self._timer.start()
        self.statusChanged.emit(self.status())

    def stop(self) -> None:
        self._timer.stop()

    # ------------------------------------------------------------ 主循环

    def _tick(self) -> None:
        now = datetime.now()
        mono = time.monotonic()
        gap = mono - self._last_mono
        self._last_mono = mono

        # 暂停中
        if self._paused_until is not None:
            if now < self._paused_until:
                self.statusChanged.emit(self.status())
                return
            self._paused_until = None
            self._alerting = False
            self._alert_level = 0
            self._next_fire = None

        # 收尾模式独立运行，不受时间段规则约束
        if self._winddown_active:
            self._tick_winddown(now)
            self.statusChanged.emit(self.status())
            return

        rule = self._active_rule(now)
        if rule is None:
            self._close_session()
            self.statusChanged.emit(self.status())
            return

        phase = self._phase_for(rule, now)

        # 休眠唤醒后计时器会跳一大步，重新排期，避免开机就弹提醒
        if gap > 90:
            self._alerting = False
            self._alert_level = 0
            self._next_fire = None

        idle = winapi.idle_seconds()
        if idle >= self._idle_limit:
            self._handle_away(rule, phase, now)
            self.statusChanged.emit(self.status())
            return

        if self._state == STATE_AWAY:
            self._state = STATE_IDLE
            self._next_fire = None

        # 今天已确认休息过，又回到电脑前且仍在窗口内 → 重新开一轮
        if self._rested and self._rested_date == now.date():
            self._rested = False
            self._reset_round()
            self._next_fire = None

        self._maybe_pre_notice(rule, phase, now)

        if self._alerting:
            self._maybe_escalate(rule, phase, now)
        else:
            if self._next_fire is None:
                self._next_fire = now + timedelta(
                    minutes=self._compute_interval(rule, phase, now)
                )
            if now >= self._next_fire:
                self._fire(rule, phase, now)

        self.statusChanged.emit(self.status())

    # ------------------------------------------------------------ 规则与阶段

    def _active_rule(self, now: datetime) -> Optional[Dict[str, Any]]:
        rules = self.cfg.get("rules", []) or []
        for rule in rules:
            if not cfgmod.rule_active_now(rule, now):
                continue
            rid = str(rule.get("id") or rule.get("name") or "r")
            need_begin = (
                self._session_rule_id != rid
                or self._session_end_dt is None
                or now > self._session_end_dt
            )
            if need_begin:
                self._session_rule_id = rid
                self._session_end_dt = self._compute_window_end(rule, now)
                self._reset_round()
                self._pre_notice_done = False
                self._next_fire = None
                if self._rested_date != now.date():
                    self._rested = False
            return rule

        # 窗口结束后的宽限期：仍在超时状态就继续追一段
        grace = int(self.cfg["bedtime"].get("overtime_grace_min", 60))
        if grace > 0 and self._session_end_dt is not None:
            delta = (now - self._session_end_dt).total_seconds()
            if 0 <= delta <= grace * 60:
                for rule in rules:
                    rid = str(rule.get("id") or rule.get("name") or "r")
                    if rid == self._session_rule_id and rule.get("enabled", True):
                        return rule

        if self._session_rule_id is not None:
            self._close_session()
        return None

    @staticmethod
    def _compute_window_end(rule: Dict[str, Any], now: datetime) -> datetime:
        end_min = cfgmod.parse_hhmm(rule.get("end", "23:59"))
        cand = now.replace(
            hour=end_min // 60, minute=end_min % 60, second=0, microsecond=0
        )
        if cand <= now:
            cand += timedelta(days=1)
        return cand

    @staticmethod
    def _phase_for(rule: Dict[str, Any], now: datetime) -> str:
        return cfgmod.rule_phase(rule, now) or PHASE_OVERTIME

    # ------------------------------------------------------------ 间隔与强度

    def _compute_interval(self, rule: Dict[str, Any], phase: str, now: datetime) -> float:
        rem = self.cfg["reminder"]
        low = max(1, int(rem.get("min_interval_min", 5)))
        base = max(low, int(rule.get("interval_min", 30)))

        if phase == PHASE_PRE:
            minutes = float(base)
        elif phase == PHASE_RAMP:
            remain = max(0, cfgmod.minutes_to_bedtime(rule, now))
            lead = max(1, cfgmod.RAMP_LEAD_MIN)
            ratio = max(0.0, min(1.0, remain / lead))
            minutes = low + (base - low) * ratio
        else:
            minutes = float(low)

        minutes *= self._interval_factor
        minutes = max(float(low), minutes)

        if phase != PHASE_OVERTIME and self.cfg["smart"].get("random_jitter", True):
            jitter = float(rule.get("jitter", 0.0) or 0.0)
            if jitter > 0:
                minutes *= 1.0 + random.uniform(-jitter, jitter)

        return max(1.0, minutes)

    def _capabilities(self, ignore_strength: bool = False) -> List[int]:
        """按通道开关与强度上限，算出实际可用的强度等级列表。

        ignore_strength=True 时忽略早睡的「强制程度」设置 —— 收尾模式是用户
        自己点的「我要收了」，理应允许升到最强，不该被早睡那边的「温和/标准」
        连带压住。通道开关（是否允许全屏遮罩）仍然尊重。
        """
        rem = self.cfg["reminder"]
        ch = rem.get("channels", {})
        strength = rem.get("strength", STRENGTH_STANDARD)

        available: List[int] = []
        if ch.get("toast", True):
            available.append(LEVEL_TOAST)
        if ch.get("popup", True):
            available.append(LEVEL_POPUP)
        if ch.get("overlay", True):
            available.append(LEVEL_OVERLAY)
        if not available:
            available = [LEVEL_TOAST]

        if ignore_strength:
            return available

        max_allowed = 2 if strength == STRENGTH_GENTLE else 3
        allowed = [lvl for lvl in available if lvl <= max_allowed]
        return allowed or [min(available)]

    @staticmethod
    def _resolve_level(desired: int, allowed: List[int]) -> int:
        below = [lvl for lvl in allowed if lvl <= desired]
        return max(below) if below else min(allowed)

    def _desired_level(self, phase: str, rule: Dict[str, Any], now: datetime) -> int:
        if phase == PHASE_RAMP:
            desired = LEVEL_POPUP
        elif phase == PHASE_OVERTIME:
            desired = LEVEL_OVERLAY
        else:
            desired = LEVEL_TOAST

        boost = int(self.cfg["bedtime"].get("overtime_boost_min", 15))
        if phase == PHASE_OVERTIME and cfgmod.overtime_minutes(rule, now) >= boost:
            desired = LEVEL_OVERLAY

        # 被忽略的次数会推高强度：忽略 1 次升一级；忽略 3 次以上再升一级
        if self._snoozes >= 1:
            desired += 1
        if self._snoozes >= 3 and phase != PHASE_PRE:
            desired += 1
        return min(3, desired)

    def _compute_level(self, rule: Dict[str, Any], phase: str, now: datetime) -> int:
        return self._resolve_level(
            self._desired_level(phase, rule, now), self._capabilities()
        )

    # ------------------------------------------------------------ 触发与升级

    def _fire(self, rule: Dict[str, Any], phase: str, now: datetime) -> None:
        level = self._compute_level(rule, phase, now)
        self._alerting = True
        self._alert_level = level
        self._alert_since = now
        self._state = STATE_ALERTING
        self.reminderDue.emit(level, self._context(rule, phase, now, level))

    def _maybe_escalate(self, rule: Dict[str, Any], phase: str, now: datetime) -> None:
        if self._alert_since is None:
            self._alert_since = now
            return
        elapsed = (now - self._alert_since).total_seconds()
        if elapsed < self._esc_sec:
            return

        higher = self._resolve_level(min(3, self._alert_level + 1), self._capabilities())
        if higher > self._alert_level:
            self._alert_level = higher
            self._alert_since = now
            self.reminderDue.emit(
                self._alert_level, self._context(rule, phase, now, self._alert_level)
            )
            return

        # 已经在最高等级仍无响应 → 隔一段时间再提醒一次
        if elapsed >= self._esc_sec * 4:
            self._alert_since = now
            self.reminderDue.emit(
                self._alert_level, self._context(rule, phase, now, self._alert_level)
            )

    def _maybe_pre_notice(self, rule: Dict[str, Any], phase: str, now: datetime) -> None:
        rem = self.cfg["reminder"]
        if self._pre_notice_done or not rem.get("pre_notice", True):
            return
        if phase == PHASE_OVERTIME:
            self._pre_notice_done = True
            return
        lead = max(1, int(rem.get("pre_notice_min", 5)))
        remain = cfgmod.minutes_to_bedtime(rule, now)
        if 0 < remain <= lead:
            self._pre_notice_done = True
            self.preNoticeDue.emit(
                remain, self._context(rule, phase, now, LEVEL_TOAST)
            )

    # ------------------------------------------------------------ 用户操作

    def snooze(self, minutes: int) -> None:
        """「再给我 X 分钟」：间隔减半、强度随忽略次数上升。"""
        if self._winddown_active:
            # 收尾模式的节奏是固定的，不接受拖延
            return
        now = datetime.now()
        self._snoozes += 1
        self._interval_factor = max(0.25, self._interval_factor * 0.5)
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        minutes = max(1, int(minutes))
        self._next_fire = now + timedelta(minutes=minutes)
        self._state = STATE_WAITING
        self.snoozed.emit(minutes, self._next_fire)
        self.statusChanged.emit(self.status())

    # ------------------------------------------------------------ 收尾模式

    def _winddown_intervals(self) -> List[float]:
        raw = (self.cfg.get("winddown") or {}).get("intervals_min") or [5, 2.5, 2, 1]
        out: List[float] = []
        for item in raw:
            try:
                value = float(item)
            except Exception:
                continue
            if value > 0:
                out.append(value)
        return out or [5.0, 2.5, 2.0, 1.0]

    @staticmethod
    def _fmt_minutes(value: float) -> str:
        total = int(round(float(value) * 60))
        minutes, secs = divmod(total, 60)
        if minutes and secs:
            return "{} 分 {} 秒".format(minutes, secs)
        if minutes:
            return "{} 分钟".format(minutes)
        return "{} 秒".format(secs)

    def _winddown_plan_text(self) -> str:
        parts = [self._fmt_minutes(v) for v in self._winddown_intervals()]
        return " → ".join(parts) + " → 之后每分钟"

    def _winddown_interval_for(self, step: int) -> float:
        intervals = self._winddown_intervals()
        return intervals[step] if step < len(intervals) else intervals[-1]

    def _winddown_level_for(self, step: int) -> int:
        """收尾模式的强度曲线，由 winddown.level_mode 决定。"""
        mode = (self.cfg.get("winddown") or {}).get("level_mode", "ramp")
        if mode == "always_full":
            return LEVEL_OVERLAY
        if mode == "from_popup":
            return min(LEVEL_OVERLAY, step + LEVEL_POPUP)
        # 默认：第 1 次角落轻提醒，第 2 次中央弹窗，第 3 次起全屏
        return min(LEVEL_OVERLAY, step + 1)

    def is_winddown(self) -> bool:
        return self._winddown_active

    def start_winddown(self) -> None:
        """手动进入收尾模式：按固定节奏催，间隔越来越短、强度递增。"""
        now = datetime.now()
        self._winddown_active = True
        self._winddown_step = 0
        self._winddown_started = now
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        self._snoozes = 0
        self._interval_factor = 1.0
        self._pre_notice_done = True
        self._paused_until = None
        self._next_fire = now + timedelta(minutes=self._winddown_interval_for(0))
        self._state = STATE_WAITING
        self.statusChanged.emit(self.status())
        self.windDownChanged.emit(True, self.winddown_status())

    def cancel_winddown(self) -> None:
        """「再玩一会儿」：退出收尾模式，回到正常节奏。"""
        self._winddown_cancels += 1
        self._winddown_active = False
        self._winddown_step = 0
        self._winddown_started = None
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        self._next_fire = None
        self._reset_round()
        self._state = STATE_IDLE
        self.statusChanged.emit(self.status())
        self.windDownChanged.emit(False, {})

    def defer_winddown(self, minutes: Optional[float] = None) -> None:
        """「延后提醒」：把下一次提醒推迟一会儿，档位与强度都不变。

        与「结束收尾」的区别：收尾模式继续运行，只是这次先收起来。
        与早睡 snooze 的区别：这里不缩短间隔、也不升级强度 ——
        用户已经说了要收尾，只是手头还差一点，不该为此惩罚他。
        """
        if not self._winddown_active:
            return
        if minutes is None:
            minutes = float((self.cfg.get("winddown") or {}).get("defer_min", 5) or 5)
        minutes = max(1.0, float(minutes))

        self._winddown_defers += 1
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        self._state = STATE_WAITING
        self._next_fire = datetime.now() + timedelta(minutes=minutes)
        self.statusChanged.emit(self.status())

    def finish_winddown(self, reason: str = REST_MANUAL) -> None:
        """「我已经收了」：结束收尾模式，并按正常流程记一笔。"""
        self._winddown_active = False
        self._winddown_step = 0
        self._winddown_started = None
        self.windDownChanged.emit(False, {})
        self.confirm_rest(reason)

    def winddown_status(self) -> Dict[str, Any]:
        if not self._winddown_active or self._winddown_started is None:
            return {"active": False}
        now = datetime.now()
        intervals = self._winddown_intervals()
        next_in = 0.0
        if self._next_fire is not None:
            next_in = max(0.0, (self._next_fire - now).total_seconds())
        return {
            "active": True,
            "elapsed_sec": max(0, int((now - self._winddown_started).total_seconds())),
            "step": self._winddown_step,
            "total_steps": len(intervals),
            "next_in_sec": next_in,
            "cancels": self._winddown_cancels,
            "defers": self._winddown_defers,
            "plan": list(intervals),
            "plan_text": self._winddown_plan_text(),
        }

    def _tick_winddown(self, now: datetime) -> None:
        if winapi.idle_seconds() >= self._idle_limit:
            # 人已经离开电脑，就当他已经收了
            self.finish_winddown(REST_IDLE)
            return

        if self._next_fire is None:
            self._next_fire = now + timedelta(
                minutes=self._winddown_interval_for(self._winddown_step)
            )
        if now < self._next_fire:
            return

        self._fire_winddown(now)
        self._winddown_step += 1
        self._next_fire = now + timedelta(
            minutes=self._winddown_interval_for(self._winddown_step)
        )

    def _fire_winddown(self, now: datetime) -> None:
        step = self._winddown_step
        level = self._resolve_level(
            self._winddown_level_for(step),
            self._capabilities(ignore_strength=True),
        )
        self._alerting = True
        self._alert_level = level
        self._alert_since = now
        self._state = STATE_ALERTING
        self.reminderDue.emit(level, self._winddown_context(now, step, level))

    def _winddown_context(self, now: datetime, step: int, level: int) -> Dict[str, Any]:
        rem = self.cfg["reminder"]
        intervals = self._winddown_intervals()
        elapsed = 0
        if self._winddown_started is not None:
            elapsed = max(0, int((now - self._winddown_started).total_seconds()))
        return {
            "phase": "winddown",
            "winddown": True,
            "level": level,
            "step": step,
            "total_steps": len(intervals),
            "elapsed_sec": elapsed,
            "next_in_sec": int(round(self._winddown_interval_for(step + 1) * 60)),
            "cancels": self._winddown_cancels,
            "plan": list(intervals),
            "plan_text": self._winddown_plan_text(),
            "defer_min": int((self.cfg.get("winddown") or {}).get("defer_min", 5) or 5),
            "rule_name": "收尾模式",
            "bedtime": "",
            "remain_min": 0,
            "overtime_min": 0,
            "snoozes": 0,
            "snooze_options": [],
            "show_countdown": True,
            "wrapup": {},
            "strength": rem.get("strength", STRENGTH_STANDARD),
            "tips": list(self.cfg.get("tips", [])),
            "now": now,
        }

    def confirm_rest(self, reason: str = REST_MANUAL) -> None:
        """确认今天不再用电脑了。"""
        # 无论从哪条路进来（托盘、设置、遮罩），都要顺手结束收尾模式
        if self._winddown_active:
            self._winddown_active = False
            self._winddown_step = 0
            self._winddown_started = None
            self.windDownChanged.emit(False, {})

        now = datetime.now()
        rule = self._active_rule(now)
        if rule is not None:
            self._mark_rested(now, rule, reason)
        else:
            self._rested = True
            self._rested_date = now.date()
            self._alerting = False
            self._alert_level = 0
            self._next_fire = None
            self._reset_round()
            self._state = STATE_RESTED
            self.restConfirmed.emit(
                reason, {"time": now.strftime("%H:%M"), "overtime_min": 0, "rule_name": ""}
            )
        self.statusChanged.emit(self.status())

    def undo_rest(self) -> None:
        """撤销「今晚已休息」，重新开始提醒。

        同时把刚才写下的历史记录清掉 —— 否则下次启动会告诉用户
        「昨晚 23:12 就停下了」，而实际上他后来又接着用了。
        """
        now = datetime.now()
        self._rested = False
        self._rested_date = None
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        self._reset_round()
        self._pre_notice_done = False
        self._next_fire = None
        self._state = STATE_IDLE

        history = self.cfg.get("history") or {}
        if history.get("last_date") == now.strftime("%Y-%m-%d"):
            self.cfg["history"] = {}
            cfgmod.save(self.cfg)

        self.statusChanged.emit(self.status())

    def pause(self, minutes: Optional[int] = None, until: Optional[datetime] = None) -> None:
        now = datetime.now()
        if until is not None:
            self._paused_until = until
        elif minutes:
            self._paused_until = now + timedelta(minutes=int(minutes))
        else:
            self._paused_until = now + timedelta(hours=1)
        self._alerting = False
        self._alert_level = 0
        self._next_fire = None
        self._state = STATE_PAUSED
        self.statusChanged.emit(self.status())

    def pause_until_end_of_window(self) -> None:
        """暂停到今天最后一个提醒窗口结束。"""
        now = datetime.now()
        horizon = now + timedelta(hours=12)
        for rule in self.cfg.get("rules", []) or []:
            cand = self._compute_window_end(rule, now)
            if cand < horizon:
                horizon = cand
        self.pause(until=horizon)

    def resume(self) -> None:
        self._paused_until = None
        self._alerting = False
        self._alert_level = 0
        self._reset_round()
        self._next_fire = None
        self._state = STATE_IDLE
        self.statusChanged.emit(self.status())

    def defer(self, minutes: int) -> None:
        """把本次提醒整体推后（检测到全屏应用时使用）。"""
        now = datetime.now()
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        self._next_fire = now + timedelta(minutes=max(1, int(minutes)))
        self._state = STATE_WAITING
        self.statusChanged.emit(self.status())

    # ------------------------------------------------------------ 内部状态

    def _handle_away(self, rule: Dict[str, Any], phase: str, now: datetime) -> None:
        if self._alerting:
            self._alerting = False
            self._alert_level = 0
        self._next_fire = None
        self._state = STATE_AWAY
        if phase == PHASE_OVERTIME and not (
            self._rested and self._rested_date == now.date()
        ):
            self._mark_rested(now, rule, REST_IDLE)

    def _mark_rested(self, now: datetime, rule: Dict[str, Any], reason: str) -> None:
        try:
            overtime = cfgmod.overtime_minutes(rule, now)
        except Exception:
            overtime = 0
        self._rested = True
        self._rested_date = now.date()
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        self._next_fire = None
        self._reset_round()
        self._state = STATE_RESTED
        self._write_history(now, overtime)
        self.restConfirmed.emit(
            reason,
            {
                "time": now.strftime("%H:%M"),
                "overtime_min": int(overtime),
                "rule_name": rule.get("name", ""),
            },
        )

    def _write_history(self, now: datetime, overtime: int) -> None:
        self.cfg["history"] = {
            "last_date": now.strftime("%Y-%m-%d"),
            "last_time": now.strftime("%H:%M"),
            "overtime_min": int(max(0, overtime)),
        }
        cfgmod.save(self.cfg)

    def _close_session(self) -> None:
        self._alerting = False
        self._alert_level = 0
        self._alert_since = None
        self._session_rule_id = None
        self._session_end_dt = None
        self._next_fire = None
        self._pre_notice_done = False
        self._reset_round()
        if self._state != STATE_PAUSED:
            self._state = STATE_IDLE

    def _reset_round(self) -> None:
        self._snoozes = 0
        self._interval_factor = 1.0

    # ------------------------------------------------------------ 对外查询

    def _context(
        self, rule: Dict[str, Any], phase: str, now: datetime, level: int
    ) -> Dict[str, Any]:
        rem = self.cfg["reminder"]
        remain = cfgmod.minutes_to_bedtime(rule, now)
        return {
            "phase": phase,
            "level": level,
            "rule_name": rule.get("name", ""),
            "bedtime": rule.get("bedtime", ""),
            "remain_min": remain,
            "overtime_min": max(0, -remain),
            "snoozes": self._snoozes,
            "snooze_options": list(rem.get("snooze_options", [5, 15, 30])),
            "show_countdown": bool(self.cfg["bedtime"].get("show_countdown", True)),
            "wrapup": dict(self.cfg.get("wrapup", {})),
            "strength": rem.get("strength", STRENGTH_STANDARD),
            "tips": list(self.cfg.get("tips", [])),
            "now": now,
        }

    def status(self) -> Dict[str, Any]:
        now = datetime.now()

        if self._winddown_active:
            return {
                "state": self._state,
                "paused": False,
                "paused_until": None,
                "phase": "winddown",
                "remain_min": 0,
                "overtime_min": 0,
                "rule_name": "收尾模式",
                "bedtime": "",
                "next_fire": self._next_fire,
                "level": self._alert_level if self._alerting else 0,
                "snoozes": 0,
                "rested_today": self._rested and self._rested_date == now.date(),
                "in_window": True,
                "winddown": self.winddown_status(),
            }

        rule: Optional[Dict[str, Any]] = None
        for item in self.cfg.get("rules", []) or []:
            if cfgmod.rule_active_now(item, now):
                rule = item
                break

        if rule is not None:
            phase = cfgmod.rule_phase(rule, now) or PHASE_OVERTIME
            remain = cfgmod.minutes_to_bedtime(rule, now)
        else:
            phase = ""
            remain = 0

        return {
            "state": self._state,
            "paused": self._paused_until is not None,
            "paused_until": self._paused_until,
            "phase": phase,
            "remain_min": remain,
            "overtime_min": max(0, -remain),
            "rule_name": (rule or {}).get("name", ""),
            "bedtime": (rule or {}).get("bedtime", ""),
            "next_fire": self._next_fire,
            "level": self._alert_level if self._alerting else 0,
            "snoozes": self._snoozes,
            "rested_today": self._rested and self._rested_date == now.date(),
            "in_window": rule is not None,
        }

    def launch_feedback(self) -> str:
        """下次启动时给一句昨晚的反馈。"""
        history = self.cfg.get("history") or {}
        raw_date = history.get("last_date") or ""
        raw_time = history.get("last_time") or ""
        if not raw_date or not raw_time:
            return ""
        try:
            when = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except Exception:
            return ""
        days = (date.today() - when).days
        if days == 0:
            prefix = "昨晚"
        elif days == 1:
            prefix = "前天晚上"
        else:
            return ""
        overtime = int(history.get("overtime_min") or 0)
        if overtime <= 0:
            return "{} {} 就停下了，按时睡觉".format(prefix, raw_time)
        return "{} {} 才停，超时 {} 分钟".format(prefix, raw_time, overtime)
