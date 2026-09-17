"""三级提醒窗口：角落轻提醒、中央弹窗、收尾倒计时。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import assets
from .widgets import (
    DELETE_WHEN_STOPPED,
    EASE_IN_CUBIC,
    EASE_OUT_CUBIC,
    FloatCard,
    fmt_duration,
)


class ThinBar(QWidget):
    """细进度条，用于提示自动消失的剩余时间。"""

    def __init__(self, theme: Dict[str, str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.theme = dict(theme)
        self.setFixedHeight(3)
        self._p = 1.0

    def _get_p(self) -> float:
        return self._p

    def _set_p(self, value: float) -> None:
        self._p = max(0.0, min(1.0, float(value)))
        self.update()

    progress = Property(float, _get_p, _set_p)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(self.theme.get("border", "#2A2F3E")))
        width = int(rect.width() * self._p)
        if width > 0:
            painter.fillRect(
                0, 0, width, rect.height(), QColor(self.theme.get("accent", "#6C8CFF"))
            )
        painter.end()

    def run(self, duration_ms: int) -> None:
        anim = QPropertyAnimation(self, b"progress", self)
        anim.setDuration(max(0, int(duration_ms)))
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.start(DELETE_WHEN_STOPPED)


class ToastWindow(FloatCard):
    """右下角轻提醒：滑入 + 淡出，几秒后自动消失。"""

    clicked = Signal()

    CARD_WIDTH = 344

    def __init__(self, theme: Dict[str, str], reduce_motion: bool = False) -> None:
        super().__init__(theme, radius=14)
        self.reduce = reduce_motion
        self._closing = False

        self.card.setFixedWidth(self.CARD_WIDTH)
        self.body.setContentsMargins(16, 14, 16, 12)
        self.body.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(11)

        self.dot = QFrame()
        self.dot.setFixedSize(30, 30)
        top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(3)
        self.title = QLabel("")
        self.title.setObjectName("FloatTitle")
        texts.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("FloatSub")
        self.subtitle.setWordWrap(True)
        texts.addWidget(self.subtitle)
        top.addLayout(texts, 1)
        self.body.addLayout(top)

        self.bar = ThinBar(theme)
        self.body.addWidget(self.bar)

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.dismiss)

    def _round_dot(self, color: str) -> None:
        self.dot.setStyleSheet(
            "background: %s; border-radius: 15px;" % color
        )

    def show_message(
        self,
        title: str,
        subtitle: str,
        color: str,
        lifetime_ms: int = 8500,
        on_click: Optional[Callable[[], None]] = None,
    ) -> None:
        self._closing = False
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self._round_dot(color)
        self._on_click = on_click

        self.adjustSize()
        self.place_bottom_right(margin=8)
        target = self.pos()
        start = QPoint(target.x() + 46, target.y() + 8)
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()

        duration = 0 if self.reduce else 300
        group = QParallelAnimationGroup(self)

        move = QPropertyAnimation(self, b"pos", self)
        move.setDuration(duration)
        move.setStartValue(start)
        move.setEndValue(target)
        move.setEasingCurve(EASE_OUT_CUBIC)
        group.addAnimation(move)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(0 if self.reduce else 240)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(EASE_OUT_CUBIC)
        group.addAnimation(fade)
        group.start(DELETE_WHEN_STOPPED)

        self.raise_()
        self.bar.run(max(1200, lifetime_ms))
        self._auto_timer.start(max(1200, int(lifetime_ms)))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        handler = getattr(self, "_on_click", None)
        if handler is not None:
            handler()
        self.dismiss()
        super().mousePressEvent(event)

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._auto_timer.stop()
        if self.reduce:
            self.hide()
            return
        self.fade_out(210, self.hide)


class PopupWindow(FloatCard):
    """屏幕中央弹窗：带轻微上浮与淡入。"""

    snoozed = Signal(int)
    rested = Signal()
    opened = Signal()
    windDownCancel = Signal()
    windDownDefer = Signal()

    CARD_WIDTH = 420

    def __init__(self, theme: Dict[str, str], reduce_motion: bool = False) -> None:
        super().__init__(theme, radius=18)
        self.reduce = reduce_motion
        self.card.setFixedWidth(self.CARD_WIDTH)

        self.body.setContentsMargins(24, 22, 24, 20)
        self.body.setSpacing(10)

        self.phase_label = QLabel("")
        self.phase_label.setObjectName("FloatSub")
        self.body.addWidget(self.phase_label)

        self.big = QLabel("")
        big_font = QFont(self.big.font())
        big_font.setPointSizeF(26)
        big_font.setWeight(QFont.Weight.Medium)
        self.big.setFont(big_font)
        self.big.setStyleSheet("color: %s;" % theme.get("accent", "#6C8CFF"))
        self.body.addWidget(self.big)

        self.sub = QLabel("")
        self.sub.setObjectName("FloatSub")
        self.sub.setWordWrap(True)
        self.body.addWidget(self.sub)

        self.line = QFrame()
        self.line.setFixedHeight(1)
        self.line.setStyleSheet("background: %s;" % theme.get("border", "#2A2F3E"))
        self.body.addWidget(self.line)

        self.tip = QLabel("")
        self.tip.setObjectName("FloatTiny")
        self.tip.setWordWrap(True)
        self.body.addWidget(self.tip)

        self.snooze_row = QHBoxLayout()
        self.snooze_row.setSpacing(8)
        self.body.addLayout(self.snooze_row)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.rest_btn = QPushButton("我去睡了")
        self.rest_btn.setObjectName("Primary")
        self.rest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rest_btn.clicked.connect(self.rested.emit)
        actions.addWidget(self.rest_btn)
        acts_wrap = QWidget()
        acts_wrap.setLayout(actions)
        self.body.addWidget(acts_wrap)

    def _clear_snooze_row(self) -> None:
        while self.snooze_row.count():
            item = self.snooze_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_content(self, ctx: Dict[str, Any], locked: bool = False) -> None:
        if ctx.get("winddown"):
            self._set_winddown_content(ctx)
            return

        self.rest_btn.setText("我去睡了")
        phase = ctx.get("phase", "pre")
        if phase == "overtime":
            overtime = int(ctx.get("overtime_min", 0))
            self.phase_label.setText("已经超过目标睡觉时间")
            self.big.setText("超时 {} 分钟".format(overtime))
            self.sub.setText("目标 {}，现在该停了。".format(ctx.get("bedtime", "")))
        else:
            self.phase_label.setText("距离目标睡觉时间" if phase == "ramp" else "提醒一下")
            remain = int(ctx.get("remain_min", 0))
            if remain >= 60:
                self.big.setText("还有 {} 小时 {} 分".format(remain // 60, remain % 60))
            else:
                self.big.setText("还有 {} 分钟".format(remain))
            self.sub.setText("目标睡觉时间是 {}。".format(ctx.get("bedtime", "")))

        tips = assets.pick_tips(ctx.get("tips", []), 1)
        self.tip.setText(tips[0] if tips else "")

        self._clear_snooze_row()
        for minutes in ctx.get("snooze_options", [5, 15, 30]):
            btn = QPushButton("再 {} 分钟".format(int(minutes)))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(not locked)
            btn.clicked.connect(lambda _=False, m=int(minutes): self.snoozed.emit(m))
            self.snooze_row.addWidget(btn)
        self.snooze_row.addStretch(1)

    def _set_winddown_content(self, ctx: Dict[str, Any]) -> None:
        """收尾模式：主按钮是「我已经收了」，取消刻意做得不显眼。"""
        step = int(ctx.get("step", 0)) + 1
        total = int(ctx.get("total_steps", 4))
        self.phase_label.setText("收尾模式 · 第 {} / {} 次提醒".format(step, total))
        self.big.setText("已经收了 {}".format(fmt_duration(ctx.get("elapsed_sec", 0))))
        self.sub.setText(
            "节奏：{}\n下一次大约 {} 后再提醒你。".format(
                ctx.get("plan_text", ""), fmt_duration(ctx.get("next_in_sec", 60))
            )
        )
        tips = assets.pick_tips(ctx.get("tips", []), 1)
        self.tip.setText(tips[0] if tips else "")

        # 三个选项，主次分明：
        #   我已经收了（主按钮）→ 结束收尾，记一笔
        #   延后 N 分钟（次按钮）→ 先收起来，过一会儿再来，收尾继续
        #   结束收尾（小字）    → 退出收尾模式，回到早睡提醒
        # 之前只有「我已经收了」+ 小字取消，用户不想现在收就只能退出整个模式。
        self._clear_snooze_row()
        defer = QPushButton("延后 {} 分钟".format(int(ctx.get("defer_min", 5))))
        defer.setCursor(Qt.CursorShape.PointingHandCursor)
        defer.clicked.connect(self.windDownDefer.emit)
        self.snooze_row.addWidget(defer, 0, Qt.AlignmentFlag.AlignLeft)

        cancel = QPushButton("结束收尾")
        cancel.setObjectName("Subtle")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.windDownCancel.emit)
        self.snooze_row.addWidget(cancel, 0, Qt.AlignmentFlag.AlignLeft)
        self.snooze_row.addStretch(1)

        self.rest_btn.setText("我已经收了")

    def appear(self) -> None:
        self.adjustSize()
        self.place_center()
        target = self.pos()
        start = QPoint(target.x(), target.y() + 22)
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        duration = 0 if self.reduce else 360
        group = QParallelAnimationGroup(self)

        move = QPropertyAnimation(self, b"pos", self)
        move.setDuration(duration)
        move.setStartValue(start)
        move.setEndValue(target)
        move.setEasingCurve(
            QEasingCurve.Type.OutBack if not self.reduce else EASE_OUT_CUBIC
        )
        group.addAnimation(move)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(0 if self.reduce else 220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(EASE_OUT_CUBIC)
        group.addAnimation(fade)
        group.start(DELETE_WHEN_STOPPED)

    def pulse(self) -> None:
        if self.reduce:
            return
        self.raise_()
        base = self.pos()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(420)
        anim.setStartValue(base)
        anim.setKeyValueAt(0.25, QPoint(base.x(), base.y() - 7))
        anim.setKeyValueAt(0.75, QPoint(base.x(), base.y() + 3))
        anim.setEndValue(base)
        anim.start(DELETE_WHEN_STOPPED)

    def close_now(self) -> None:
        self.hide()


class WrapupWindow(FloatCard):
    """收尾倒计时：点「再给我 X 分钟」后出现在右下角。"""

    CARD_WIDTH = 236

    def __init__(self, theme: Dict[str, str], reduce_motion: bool = False) -> None:
        super().__init__(theme, radius=14)
        self.reduce = reduce_motion
        self.card.setFixedWidth(self.CARD_WIDTH)
        self.body.setContentsMargins(16, 12, 16, 12)
        self.body.setSpacing(8)

        head = QLabel("收尾倒计时")
        head.setObjectName("FloatTiny")
        self.body.addWidget(head)

        self.time_label = QLabel("--:--")
        font = QFont(self.time_label.font())
        font.setPointSizeF(20)
        font.setWeight(QFont.Weight.Medium)
        self.time_label.setFont(font)
        self.body.addWidget(self.time_label)

        self.bar = ThinBar(theme)
        self.body.addWidget(self.bar)

        self.hint = QLabel("时间到了会继续提醒你")
        self.hint.setObjectName("FloatTiny")
        self.body.addWidget(self.hint)

        self._deadline: Optional[datetime] = None
        self._total = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def start_countdown(self, deadline: datetime, minutes: int) -> None:
        self._deadline = deadline
        self._total = max(1.0, float(minutes) * 60.0)
        self.hint.setText("{} 分钟后继续提醒你".format(int(minutes)))
        self._tick()
        self._timer.start()

        self.adjustSize()
        self.place_bottom_right(margin=8)
        self.move(self.pos())
        self.show()
        self.raise_()
        self.fade_in(200)

    def _tick(self) -> None:
        if self._deadline is None:
            return
        remain = (self._deadline - datetime.now()).total_seconds()
        if remain < 0:
            remain = 0
        mins, secs = divmod(int(remain), 60)
        self.time_label.setText("{:02d}:{:02d}".format(mins, secs))
        self.bar._set_p(remain / self._total if self._total > 0 else 0.0)
        if remain <= 0:
            self._timer.stop()

    def stop(self) -> None:
        self._timer.stop()
        self._deadline = None
        self.hide()


class RestCountdownWindow(FloatCard):
    """右下角的倒计时卡片，共用于两件事：

    - mode="rest"  确认休息后：开了自动关机就显示关机倒计时，否则是可撤销的确认卡片
    - mode="pause" 暂停提醒后：显示暂停到什么时候，并给一个「立即恢复提醒」
    """

    cancelled = Signal()        # 休息确认被撤销
    resumeRequested = Signal()  # 暂停被立刻取消

    CARD_WIDTH = 276
    UNDO_WINDOW_SEC = 90

    def __init__(self, theme: Dict[str, str], reduce_motion: bool = False) -> None:
        super().__init__(theme, radius=14)
        self.reduce = reduce_motion
        self.card.setFixedWidth(self.CARD_WIDTH)
        self.body.setContentsMargins(16, 12, 16, 12)
        self.body.setSpacing(8)

        self.head = QLabel("已记录：今晚休息")
        self.head.setObjectName("FloatTiny")
        self.body.addWidget(self.head)

        self.time_label = QLabel("--:--")
        font = QFont(self.time_label.font())
        font.setPointSizeF(20)
        font.setWeight(QFont.Weight.Medium)
        self.time_label.setFont(font)
        self.body.addWidget(self.time_label)

        self.bar = ThinBar(theme)
        self.body.addWidget(self.bar)

        self.hint = QLabel("")
        self.hint.setObjectName("FloatTiny")
        self.hint.setWordWrap(True)
        self.body.addWidget(self.hint)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("Primary")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.body.addWidget(self.cancel_btn)

        self._deadline: Optional[datetime] = None
        self._total = 1.0
        self._will_shutdown = False
        self._mode = "rest"
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def start(
        self,
        deadline: datetime,
        total_sec: int,
        will_shutdown: bool,
        rest_time: str = "",
        bedtime: str = "",
        mode: str = "rest",
    ) -> None:
        self._deadline = deadline
        self._total = max(1.0, float(total_sec))
        self._will_shutdown = will_shutdown
        self._mode = mode

        if mode == "pause":
            self.head.setText("已暂停提醒")
            self.hint.setText("这段时间不会再打扰你。想现在恢复就点下面的按钮。")
            self.cancel_btn.setText("立即恢复提醒")
        elif will_shutdown:
            self.head.setText("已记录：今晚休息")
            self.hint.setText(
                "距离关机还有一段时间，先把手头文件保存好。\n目标 {}，晚安。".format(bedtime)
            )
            self.cancel_btn.setText("取消关机，继续用电脑")
        else:
            self.head.setText("晚安")
            self.hint.setText("{} 停下了，已经记下。".format(rest_time or ""))
            self.cancel_btn.setText("撤销，继续用电脑")

        self._tick()
        self._timer.start()
        self.adjustSize()
        self.place_bottom_right(margin=8)
        self.show()
        self.raise_()
        self.fade_in(200)

    def _on_cancel(self) -> None:
        mode = self._mode
        self.stop()
        if mode == "pause":
            self.resumeRequested.emit()
        else:
            self.cancelled.emit()

    def stop(self) -> None:
        self._timer.stop()
        self._deadline = None
        self.hide()

    def _tick(self) -> None:
        if self._deadline is None:
            return
        remain = (self._deadline - datetime.now()).total_seconds()
        if remain < 0:
            remain = 0
        mins, secs = divmod(int(remain), 60)
        self.time_label.setText("{:02d}:{:02d}".format(mins, secs))
        self.bar._set_p(remain / self._total if self._total > 0 else 0.0)

        if remain > 0:
            return
        self._timer.stop()
        if self._will_shutdown:
            self.time_label.setText("关机中")
            self.hint.setText("系统正在关机，别忘了保存文件。")
        else:
            self.hide()
