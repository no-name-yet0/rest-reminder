"""全屏遮罩：多显示器同时覆盖，强制用户做出选择。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
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
    EASE_IN_OUT_CUBIC,
    EASE_OUT_CUBIC,
    fmt_duration,
)

PHASE_HEADLINE = {
    "pre": "差不多该收尾了",
    "ramp": "该准备睡了",
    "overtime": "已经超过睡觉时间",
}


class OverlayWindow(QWidget):
    """覆盖单块屏幕的遮罩。只有承载卡片的那块屏幕显示内容。"""

    snoozed = Signal(int)
    rested = Signal()
    windDownCancel = Signal()
    windDownDefer = Signal()

    def __init__(
        self,
        screen,
        theme: Dict[str, str],
        reduce_motion: bool = False,
        with_card: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(None)
        self.theme = dict(theme)
        self._reduce = reduce_motion
        self._with_card = with_card
        self._dim = 0.0
        self._alpha_target = 0.84 if theme.get("mode") == "dark" else 0.66
        self._base_color = QColor(theme.get("dim_solid", "#080A10"))

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self.setScreen(screen)
        self.setGeometry(screen.geometry())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("OverlayCard")
        self.card.setFixedWidth(452)
        self.card.setStyleSheet(self._card_qss())
        self._build_card()

        if with_card:
            layout.addStretch(1)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.card, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addStretch(1)
        else:
            layout.addStretch(1)
            hint = QLabel("另一块屏幕上也有提醒")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet(
                "color: rgba(255,255,255,0.45); font-size: 13px; background: transparent;"
            )
            layout.addWidget(hint)
            layout.addStretch(1)

        self.card.setVisible(with_card)

    # ------------------------------------------------------- 样式与内容

    def _card_qss(self) -> str:
        t = self.theme
        return """
        QFrame#OverlayCard {{
            background: {bg};
            border: 1px solid {border};
            border-radius: 20px;
        }}
        QFrame#OverlayCard QLabel {{ color: {text}; background: transparent; }}
        QLabel#OvHead {{ font-size: 20px; font-weight: 500; color: {text}; }}
        QLabel#OvBig {{ font-size: 40px; font-weight: 500; color: {accent}; }}
        QLabel#OvSub {{ font-size: 13px; color: {text_dim}; }}
        QLabel#OvTip {{ font-size: 13px; color: {text_faint}; }}
        QPushButton {{
            background: transparent;
            border: 1px solid {border_strong};
            border-radius: 11px;
            color: {text};
            padding: 10px 16px;
            font-size: 13px;
        }}
        QPushButton:hover {{ border-color: {accent}; color: {accent}; }}
        QPushButton#Primary {{
            background: {accent};
            border: 1px solid {accent};
            color: {on_accent};
            font-size: 14px;
            font-weight: 500;
            padding: 11px 26px;
        }}
        QPushButton#Primary:hover {{
            background: {accent_hover};
            border-color: {accent_hover};
            color: {on_accent};
        }}
        QPushButton#Locked {{
            border-color: {warn};
            color: {warn};
        }}
        QPushButton#Subtle {{
            background: transparent;
            border: none;
            color: rgba(255, 255, 255, 0.42);
            font-size: 12px;
            padding: 4px 6px;
        }}
        QPushButton#Subtle:hover {{ color: rgba(255, 255, 255, 0.72); }}
        """.format(
            bg=self.theme.get("surface", "#1A1E28"),
            border=self.theme.get("border", "#2A2F3E"),
            text=self.theme.get("text", "#E9EBF2"),
            text_dim=self.theme.get("text_dim", "#99A1B5"),
            text_faint=self.theme.get("text_faint", "#6A7285"),
            border_strong=self.theme.get("border_strong", "#3A4055"),
            accent=self.theme.get("accent", "#6C8CFF"),
            accent_hover=self.theme.get("accent_hover", "#7E9BFF"),
            on_accent=self.theme.get("on_accent", "#FFFFFF"),
            warn=self.theme.get("warn", "#FFB454"),
        )

    def _build_card(self) -> None:
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(32, 28, 32, 26)
        lay.setSpacing(12)

        self.head = QLabel("")
        self.head.setObjectName("OvHead")
        lay.addWidget(self.head)

        self.big = QLabel("")
        self.big.setObjectName("OvBig")
        lay.addWidget(self.big)

        self.sub = QLabel("")
        self.sub.setObjectName("OvSub")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: %s;" % self.theme.get("border", "#2A2F3E"))
        lay.addWidget(line)

        self.tip = QLabel("")
        self.tip.setObjectName("OvTip")
        self.tip.setWordWrap(True)
        lay.addWidget(self.tip)

        lay.addSpacing(4)

        self.snooze_row = QHBoxLayout()
        self.snooze_row.setSpacing(9)
        lay.addLayout(self.snooze_row)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(10)
        self.lock_btn = QPushButton("需要密码才能跳过")
        self.lock_btn.setObjectName("Locked")
        self.lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_btn.setVisible(False)
        self.actions.addWidget(self.lock_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.actions.addStretch(1)
        self.rest_btn = QPushButton("我去睡了")
        self.rest_btn.setObjectName("Primary")
        self.rest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rest_btn.clicked.connect(self.rested.emit)
        self.actions.addWidget(self.rest_btn, 0)
        lay.addLayout(self.actions)
        lay.addSpacing(6)

        self.note = QLabel("")
        self.note.setObjectName("OvTip")
        self.note.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.note)

    def update_content(self, ctx: Dict) -> None:
        if not self._with_card:
            return

        if ctx.get("winddown"):
            self._set_winddown_content(ctx)
            return

        self.rest_btn.setText("我去睡了")
        phase = ctx.get("phase", "pre")
        self.head.setText(PHASE_HEADLINE.get(phase, "该睡了"))

        if phase == "overtime":
            overtime = int(ctx.get("overtime_min", 0))
            self.big.setText("+{} 分钟".format(overtime))
            self.sub.setText(
                "目标睡觉时间 {}，现在已经超时".format(ctx.get("bedtime", ""))
            )
        else:
            remain = int(ctx.get("remain_min", 0))
            if remain >= 60:
                self.big.setText("{}:{}".format(remain // 60, "%02d" % (remain % 60)))
            else:
                self.big.setText("{} 分钟".format(remain))
            self.sub.setText("距离目标睡觉时间 {}".format(ctx.get("bedtime", "")))

        tips = assets.pick_tips(ctx.get("tips", []), 1)
        self.tip.setText(tips[0] if tips else "")

        self._clear_snooze_row()
        for minutes in ctx.get("snooze_options", [5, 15, 30]):
            btn = QPushButton("再 {} 分钟".format(int(minutes)))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, m=int(minutes): self.snoozed.emit(m))
            self.snooze_row.addWidget(btn)
        self.snooze_row.addStretch(1)

        self._apply_lock(ctx.get("locked", False))

    def _clear_snooze_row(self) -> None:
        while self.snooze_row.count():
            item = self.snooze_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_winddown_content(self, ctx: Dict) -> None:
        """收尾模式：主按钮是「我已经收了」，取消刻意做得不显眼。"""
        step = int(ctx.get("step", 0)) + 1
        total = int(ctx.get("total_steps", 4))
        self.head.setText("收尾模式 · 第 {} / {} 次提醒".format(step, total))
        self.big.setText("已经收了 {}".format(fmt_duration(ctx.get("elapsed_sec", 0))))
        self.sub.setText(
            "节奏：{}\n下一次大约 {} 后再提醒你。".format(
                ctx.get("plan_text", ""), fmt_duration(ctx.get("next_in_sec", 60))
            )
        )
        tips = assets.pick_tips(ctx.get("tips", []), 1)
        self.tip.setText(tips[0] if tips else "")

        # 与中央弹窗保持一致：三个选项，主次分明
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
        self._apply_lock(False)

    def _apply_lock(self, locked: bool) -> None:
        self.lock_btn.setVisible(bool(locked))
        for i in range(self.snooze_row.count()):
            widget = self.snooze_row.itemAt(i).widget()
            if widget is not None:
                widget.setEnabled(not locked)
        if locked:
            self.note.setText("硬核模式已开启：跳过或暂停都需要密码。")
        else:
            self.note.setText("")

    def set_locked(self, locked: bool) -> None:
        self._apply_lock(locked)

    # ------------------------------------------------------- 动画

    def _get_dim(self) -> float:
        return self._dim

    def _set_dim(self, value: float) -> None:
        self._dim = max(0.0, min(1.0, float(value)))
        self.update()

    dim = Property(float, _get_dim, _set_dim)

    def animate_in(self, delay_ms: int = 0) -> None:
        duration = 0 if self._reduce else 620
        anim = QPropertyAnimation(self, b"dim", self)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(EASE_IN_OUT_CUBIC)
        anim.start(DELETE_WHEN_STOPPED)

        if not self._with_card:
            return
        self.card.setWindowOpacity(0.0)
        self.card.setVisible(True)

        def _card_in() -> None:
            fade = QPropertyAnimation(self.card, b"windowOpacity", self)
            fade.setDuration(0 if self._reduce else 340)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(EASE_OUT_CUBIC)
            fade.start(DELETE_WHEN_STOPPED)

        QTimer.singleShot(0 if self._reduce else max(0, delay_ms), _card_in)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        color = QColor(self._base_color)
        color.setAlphaF(self._alpha_target * self._dim)
        painter.fillRect(self.rect(), color)
        painter.end()

    # ------------------------------------------------------- 输入拦截

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # 吞掉所有按键，避免用户靠 Alt+F4 / Esc 关掉遮罩
        event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()


class OverlayController:
    """按屏幕数量创建遮罩，卡片放在鼠标所在的那块屏幕上。"""

    def __init__(self, theme: Dict[str, str], reduce_motion: bool = False) -> None:
        self.theme = dict(theme)
        self._reduce = reduce_motion
        self._windows: List[OverlayWindow] = []
        self._snooze_cb = None
        self._rest_cb = None
        self._winddown_cancel_cb = None
        self._winddown_defer_cb = None
        self._ctx: Dict[str, Any] = {}
        self._visible = False

    def bind(
        self,
        on_snooze,
        on_rest,
        on_winddown_cancel=None,
        on_winddown_defer=None,
    ) -> None:
        self._snooze_cb = on_snooze
        self._rest_cb = on_rest
        self._winddown_cancel_cb = on_winddown_cancel
        self._winddown_defer_cb = on_winddown_defer

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self, ctx: Dict) -> None:
        self._ctx = dict(ctx)
        self._teardown()

        cursor_screen = QGuiApplication.screenAt(QCursor.pos())
        if cursor_screen is None:
            cursor_screen = QGuiApplication.primaryScreen()

        for screen in QGuiApplication.screens():
            with_card = screen is cursor_screen
            win = OverlayWindow(
                screen, self.theme, self._reduce, with_card=with_card
            )
            if self._snooze_cb is not None:
                win.snoozed.connect(self._snooze_cb)
            if self._rest_cb is not None:
                win.rested.connect(self._rest_cb)
            if self._winddown_cancel_cb is not None:
                win.windDownCancel.connect(self._winddown_cancel_cb)
            if self._winddown_defer_cb is not None:
                win.windDownDefer.connect(self._winddown_defer_cb)
            win.update_content(self._ctx)
            win.show()
            win.animate_in(delay_ms=140 if with_card else 0)
            if with_card:
                win.raise_()
                win.activateWindow()
                win.setFocus()
            self._windows.append(win)

        self._visible = True

    def update(self, ctx: Dict) -> None:
        self._ctx = dict(ctx)
        for win in self._windows:
            win.update_content(self._ctx)

    def set_locked(self, locked: bool) -> None:
        for win in self._windows:
            win.set_locked(locked)

    def pulse(self) -> None:
        if not self._reduce:
            for win in self._windows:
                win.raise_()

    def hide(self) -> None:
        self._teardown()
        self._visible = False

    def _teardown(self) -> None:
        for win in self._windows:
            try:
                win.hide()
                win.deleteLater()
            except Exception:
                pass
        self._windows = []
