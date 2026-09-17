"""设置窗口、首次运行引导与通用对话框。"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QTime,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config as cfgmod
from .. import winapi
from ..theme import RADIUS_LG, RADIUS_MD
from .widgets import (
    DELETE_WHEN_STOPPED,
    EASE_OUT_CUBIC,
    Card,
    Row,
    Segmented,
    Separator,
    ToggleSwitch,
    fmt_duration,
)

DAYS = ["一", "二", "三", "四", "五", "六", "日"]
THEME_ITEMS = ["深色", "浅色", "跟随系统"]
THEME_KEYS = ["dark", "light", "system"]
STRENGTH_ITEMS = ["温和", "标准", "硬核"]
STRENGTH_KEYS = ["gentle", "standard", "hardcore"]
STRENGTH_HINTS = {
    "gentle": "最多只弹窗，永远不会占用全屏。适合电脑上还挂着别的事。",
    "standard": "逐级升级：轻通知 → 弹窗 → 全屏遮罩，一上来不会打断你。",
    "hardcore": "可以升到全屏遮罩，且跳过或暂停都需要密码，防止自己拖延。",
}
LEVEL_MODE_ITEMS = ["从轻到重", "从弹窗开始", "直接全屏"]
LEVEL_MODE_KEYS = ["ramp", "from_popup", "always_full"]
LEVEL_MODE_HINTS = {
    "ramp": "第 1 次角落轻提醒，第 2 次中央弹窗，第 3 次起全屏遮罩。由轻到重，比较温和。",
    "from_popup": "跳过轻提醒，从中央弹窗起步，第 2 次就到全屏。适合已经决定要收工的时候。",
    "always_full": "每一次都是全屏遮罩，从第一次就拉满。",
}
WIND_MIN_STEPS = 1
WIND_MAX_STEPS = 6
SHADOW_MARGIN = 16


# ------------------------------------------------------------------ 通用部件


class Panel(QFrame):
    """圆角面板，作为窗口的可见背景。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")


class TitleIconButton(QPushButton):
    """标题栏图标按钮：图标用 QPainter 画出来，不依赖字体字形。

    为什么不用字符（比如 "✕" U+2715）：这类符号在很多字体里没有对应字形，
    会被渲染成豆腐块（空方框）—— 之前截图里右上角那个空框就是这么来的。
    自绘既彻底摆脱字体差异，又能自由控制线宽、圆头与悬停配色。
    """

    def __init__(
        self,
        kind: str,
        theme: Optional[Dict[str, str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind  # "close" | "min"
        self.theme = dict(theme or {})
        self._hover = False
        self.setFixedSize(30, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setText("")  # 不依赖任何字符

    def set_theme(self, theme: Dict[str, str]) -> None:
        self.theme = dict(theme)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())

        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            if self._kind == "close":
                painter.setBrush(QColor(self.theme.get("danger", "#E5484D")))
            else:
                painter.setBrush(QColor(self.theme.get("surface_hover", "#ECEFF7")))
            painter.drawRoundedRect(rect, 8.0, 8.0)

        if self._kind == "close" and self._hover:
            color = QColor("#FFFFFF")
        else:
            color = QColor(self.theme.get("text_dim", "#5B6273"))

        pen = QPen(color)
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx = rect.center().x()
        cy = rect.center().y()
        arm = 4.5
        if self._kind == "close":
            painter.drawLine(QPointF(cx - arm, cy - arm), QPointF(cx + arm, cy + arm))
            painter.drawLine(QPointF(cx + arm, cy - arm), QPointF(cx - arm, cy + arm))
        else:
            painter.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
        painter.end()


class TitleBar(QWidget):
    """自绘标题栏，可拖动窗口。"""

    def __init__(
        self,
        window: QWidget,
        title: str,
        closable: bool = True,
        theme: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(window)
        self._win = window
        self._drag: Optional[QPoint] = None
        self.setFixedHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 10, 0)
        lay.setSpacing(9)

        self.dot = QFrame()
        self.dot.setFixedSize(14, 14)
        lay.addWidget(self.dot)

        self.label = QLabel(title)
        self.label.setObjectName("AppTitle")
        lay.addWidget(self.label)
        lay.addStretch(1)

        # 按钮一定要建出来，只是按需隐藏 —— 否则 apply_theme 访问时会炸。
        # （之前就是这里让所有对话框在构造阶段就抛异常，表现为"点了没反应"。）
        self.btn_close = TitleIconButton("close", theme)
        self.btn_close.setObjectName("WinBtnClose")
        self.btn_close.clicked.connect(window.close)
        self.btn_close.setVisible(closable)
        lay.addWidget(self.btn_close)

    def set_accent(self, color: str) -> None:
        self.dot.setStyleSheet(
            "background: %s; border-radius: 4px;" % color
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._win.move(event.globalPosition().toPoint() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        event.accept()


class BaseDialog(QDialog):
    """无边框圆角对话框基类。"""

    def __init__(self, theme: Dict[str, str], title: str, parent=None) -> None:
        super().__init__(parent)
        self.theme = dict(theme)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        outer.setSpacing(0)

        self.panel = Panel(self)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 140))
        shadow.setOffset(0, 6)
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)

        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.title_bar = TitleBar(self, title, closable=False, theme=self.theme)
        lay.addWidget(self.title_bar)

        self.content = QVBoxLayout()
        self.content.setContentsMargins(20, 4, 20, 18)
        self.content.setSpacing(12)
        lay.addLayout(self.content)

        self.apply_theme(self.theme)

    def apply_theme(self, theme: Dict[str, str]) -> None:
        self.theme = dict(theme)
        self.setStyleSheet(
            "QDialog { background: transparent; }"
            "QWidget { color: %s; font-size: 13px; }"
            "QLabel { background: transparent; }"
            % theme.get("text", "#E9EBF2")
        )
        self.panel.setStyleSheet(
            "QFrame#Root { background: %s; border: 1px solid %s; border-radius: %dpx; }"
            % (theme.get("bg", "#0F1117"), theme.get("border", "#2A2F3E"), RADIUS_LG)
        )
        self.title_bar.set_accent(theme.get("accent", "#6C8CFF"))
        self.title_bar.btn_close.set_theme(theme)

    def style_inputs(self) -> None:
        """给对话框里的输入框和按钮补上样式。

        注意：这里刻意不用 str.format —— CSS 里全是花括号，只要漏转义一个
        就会 KeyError，整个对话框就构造不出来（之前托盘「退出」没反应就是这个原因）。
        用标记替换，永远不会有转义问题。
        """
        t = self.theme
        template = """
QLineEdit {
    background: __BG_ALT__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
    padding: 9px 12px;
    color: __TEXT__;
    selection-background-color: __ACCENT__;
    selection-color: __ON_ACCENT__;
}
QLineEdit:focus { border-color: __ACCENT__; }
QPushButton {
    background: __SURFACE__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: 10px;
    color: __TEXT__;
    padding: 8px 18px;
}
QPushButton:hover { border-color: __ACCENT__; color: __ACCENT__; }
QPushButton#Primary {
    background: __ACCENT__;
    border-color: __ACCENT__;
    color: __ON_ACCENT__;
    font-weight: 500;
}
QPushButton#Primary:hover {
    background: __ACCENT_HOVER__;
    border-color: __ACCENT_HOVER__;
    color: __ON_ACCENT__;
}
"""
        tokens = {
            "__BG_ALT__": t.get("bg_alt", "#EBEDF4"),
            "__BORDER__": t.get("border", "#E2E5EE"),
            "__BORDER_STRONG__": t.get("border_strong", "#C9CEDD"),
            "__TEXT__": t.get("text", "#1A1D26"),
            "__SURFACE__": t.get("surface", "#FFFFFF"),
            "__ACCENT__": t.get("accent", "#4A6CF7"),
            "__ACCENT_HOVER__": t.get("accent_hover", "#3D5CE0"),
            "__ON_ACCENT__": t.get("on_accent", "#FFFFFF"),
        }
        for key, value in tokens.items():
            template = template.replace(key, str(value))
        self.setStyleSheet(self.styleSheet() + template)


def ask_password(
    parent: Optional[QWidget],
    theme: Dict[str, str],
    title: str,
    hint: str,
    confirm: bool = False,
) -> Optional[str]:
    """弹出密码输入框。取消返回 None。"""
    dlg = BaseDialog(theme, title, parent)
    try:
        dlg.style_inputs()
    except Exception:
        # 样式出问题绝不能连累对话框本身
        pass
    dlg.setFixedWidth(400 + SHADOW_MARGIN * 2)

    hint_label = QLabel(hint)
    hint_label.setWordWrap(True)
    hint_label.setStyleSheet("color: %s;" % theme.get("text_dim", "#99A1B5"))
    dlg.content.addWidget(hint_label)

    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setPlaceholderText("输入密码")
    dlg.content.addWidget(edit)

    edit2: Optional[QLineEdit] = None
    if confirm:
        edit2 = QLineEdit()
        edit2.setEchoMode(QLineEdit.EchoMode.Password)
        edit2.setPlaceholderText("再输一次确认")
        dlg.content.addWidget(edit2)

    error = QLabel("")
    error.setStyleSheet("color: %s;" % theme.get("danger", "#FF6B6B"))
    error.setWordWrap(True)
    dlg.content.addWidget(error)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("取消")
    cancel.clicked.connect(dlg.reject)
    buttons.addWidget(cancel)
    ok = QPushButton("确定")
    ok.setObjectName("Primary")
    buttons.addWidget(ok)
    dlg.content.addLayout(buttons)

    result: Dict[str, Optional[str]] = {"value": None}

    def _submit() -> None:
        value = edit.text()
        if not value:
            error.setText("密码不能为空")
            return
        if confirm and edit2 is not None and edit2.text() != value:
            error.setText("两次输入不一致")
            return
        result["value"] = value
        dlg.accept()

    ok.clicked.connect(_submit)
    edit.returnPressed.connect(_submit)
    if edit2 is not None:
        edit2.returnPressed.connect(_submit)

    dlg.adjustSize()
    dlg.exec()
    return result["value"]


def ask_confirm(
    parent: Optional[QWidget], theme: Dict[str, str], title: str, text: str
) -> bool:
    dlg = BaseDialog(theme, title, parent)
    try:
        dlg.style_inputs()
    except Exception:
        # 样式出问题绝不能连累对话框本身
        pass
    dlg.setFixedWidth(400 + SHADOW_MARGIN * 2)

    label = QLabel(text)
    label.setWordWrap(True)
    dlg.content.addWidget(label)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("取消")
    cancel.clicked.connect(dlg.reject)
    buttons.addWidget(cancel)
    ok = QPushButton("确定")
    ok.setObjectName("Primary")
    ok.clicked.connect(dlg.accept)
    buttons.addWidget(ok)
    dlg.content.addLayout(buttons)

    dlg.adjustSize()
    return dlg.exec() == QDialog.DialogCode.Accepted


# ------------------------------------------------------------------ 设置窗口


class SettingsWindow(QWidget):
    configChanged = Signal()
    themeChanged = Signal()
    restNow = Signal()
    windDownRequested = Signal()
    pauseRequested = Signal(int)
    pauseWindowRequested = Signal()
    resumeRequested = Signal()
    clearTracesRequested = Signal()
    quitRequested = Signal()

    PANEL_W = 852
    PANEL_H = 588

    def __init__(
        self,
        cfg: Dict[str, Any],
        theme: Dict[str, str],
        reduce_motion: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(None)
        self.cfg = cfg
        self.theme = dict(theme)
        self.reduce_motion = reduce_motion
        self._theme_widgets: List[Any] = []
        self._nav_buttons: List[QPushButton] = []

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("休息提醒 · 设置")
        self.setFixedSize(
            self.PANEL_W + SHADOW_MARGIN * 2, self.PANEL_H + SHADOW_MARGIN * 2
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        outer.setSpacing(0)

        self.panel = Panel(self)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(42)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 10)
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)

        root = QVBoxLayout(self.panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self, "休息提醒", theme=theme)
        root.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(148)
        side_lay = QVBoxLayout(self.sidebar)
        side_lay.setContentsMargins(12, 12, 12, 12)
        side_lay.setSpacing(4)
        body.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        self._nav_group = nav_group
        for index, name in enumerate(
            ["睡觉时间", "提醒方式", "智能避让", "收尾模式", "通用"]
        ):
            btn = QPushButton(name)
            btn.setObjectName("NavItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=index: self.switch_page(i))
            nav_group.addButton(btn, index)
            side_lay.addWidget(btn)
            self._nav_buttons.append(btn)
        side_lay.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("RowHint")
        self.status_label.setWordWrap(True)
        side_lay.addWidget(self.status_label)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        body.addLayout(right, 1)

        right.addWidget(self.stack, 1)

        self.action_bar = QFrame()
        act = QHBoxLayout(self.action_bar)
        act.setContentsMargins(18, 10, 18, 14)
        act.setSpacing(8)

        self.btn_pause_window = QPushButton("暂停今晚")
        self.btn_pause_window.setObjectName("Ghost")
        self.btn_pause_window.clicked.connect(self.pauseWindowRequested.emit)
        act.addWidget(self.btn_pause_window)

        self.btn_pause_60 = QPushButton("暂停 1 小时")
        self.btn_pause_60.setObjectName("Ghost")
        self.btn_pause_60.clicked.connect(lambda: self.pauseRequested.emit(60))
        act.addWidget(self.btn_pause_60)

        self.btn_resume = QPushButton("恢复提醒")
        self.btn_resume.setObjectName("Primary")
        self.btn_resume.clicked.connect(self.resumeRequested.emit)
        act.addWidget(self.btn_resume)

        act.addStretch(1)

        self.btn_winddown = QPushButton("我要收了")
        self.btn_winddown.setObjectName("Ghost")
        self.btn_winddown.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_winddown.clicked.connect(self.windDownRequested.emit)
        act.addWidget(self.btn_winddown)

        self.btn_rest = QPushButton("我这就去睡")
        self.btn_rest.setObjectName("Primary")
        self.btn_rest.clicked.connect(self.restNow.emit)
        act.addWidget(self.btn_rest)

        right.addWidget(self.action_bar)

        self._build_pages()
        self._nav_buttons[0].setChecked(True)
        self.apply_theme(self.theme)

    # ------------------------------------------------------------ 主题

    def apply_theme(self, theme: Dict[str, str]) -> None:
        from ..theme import build_qss

        self.theme = dict(theme)
        self.setStyleSheet("QWidget { background: transparent; }")
        self.panel.setStyleSheet(build_qss(self.theme))
        self.title_bar.set_accent(self.theme.get("accent", "#6C8CFF"))
        self.title_bar.btn_close.set_theme(self.theme)
        for widget in self._theme_widgets:
            try:
                widget.set_theme(self.theme)
            except Exception:
                pass
        self._refresh_strength_hint()
        self._refresh_autostart_hint()
        self._refresh_config_hint()

    # ------------------------------------------------------------ 布局工具

    def _page(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll, lay

    def _track(self, widget):
        if isinstance(widget, (ToggleSwitch, Segmented)):
            self._theme_widgets.append(widget)
        return widget

    def _commit(self, theme_changed: bool = False) -> None:
        cfgmod.save(self.cfg)
        self.configChanged.emit()
        if theme_changed:
            self.themeChanged.emit()

    def _rule(self) -> Dict[str, Any]:
        rules = self.cfg.setdefault("rules", [])
        if not rules:
            rules.append(
                {
                    "id": "r1",
                    "enabled": True,
                    "name": "早睡提醒",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                    "start": "22:00",
                    "bedtime": "23:30",
                    "end": "00:30",
                    "interval_min": 30,
                    "ramp": True,
                    "jitter": 0.15,
                }
            )
        return rules[0]

    def _set_rule(self, key: str, value: Any) -> None:
        self._rule()[key] = value
        self._commit()

    def _time_edit(self, value: str) -> QTimeEdit:
        edit = QTimeEdit()
        edit.setDisplayFormat("HH:mm")
        edit.setFixedWidth(96)
        parsed = QTime.fromString(str(value), "HH:mm")
        edit.setTime(parsed if parsed.isValid() else QTime(22, 0))
        return edit

    def _spin(self, value: int, low: int, high: int, suffix: str = "", width: int = 108) -> QSpinBox:
        box = QSpinBox()
        box.setRange(low, high)
        box.setValue(int(value))
        box.setFixedWidth(width)
        if suffix:
            box.setSuffix(suffix)
        return box

    def _toggle(self, checked: bool, on_change: Callable[[bool], None]) -> ToggleSwitch:
        sw = ToggleSwitch(bool(checked), self.theme)
        self._track(sw)

        def _handler(value: bool) -> None:
            on_change(value)
            self._commit()

        sw.toggled.connect(_handler)
        return sw

    # ------------------------------------------------------------ 页面

    def _build_pages(self) -> None:
        self.stack.addWidget(self._page_schedule())
        self.stack.addWidget(self._page_channels())
        self.stack.addWidget(self._page_smart())
        self.stack.addWidget(self._page_winddown())
        self.stack.addWidget(self._page_general())

    def _page_schedule(self):
        scroll, lay = self._page()
        rule = self._rule()

        title = QLabel("睡觉时间")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        hint = QLabel("设定一个时间段和一个目标睡觉时间，剩下的交给它。")
        hint.setObjectName("PageHint")
        lay.addWidget(hint)

        card = Card("提醒时段")
        self.t_start = self._time_edit(rule.get("start", "22:00"))
        self.t_start.timeChanged.connect(
            lambda t: self._set_rule("start", t.toString("HH:mm"))
        )
        card.body.addWidget(Row("开始提醒", "到点后开始低频提醒你留意时间", self.t_start))
        card.body.addWidget(Separator())

        self.t_bed = self._time_edit(rule.get("bedtime", "23:30"))
        self.t_bed.timeChanged.connect(
            lambda t: self._set_rule("bedtime", t.toString("HH:mm"))
        )
        card.body.addWidget(Row("目标睡觉时间", "你希望真正停下来的时间点", self.t_bed))
        card.body.addWidget(Separator())

        self.t_end = self._time_edit(rule.get("end", "00:30"))
        self.t_end.timeChanged.connect(
            lambda t: self._set_rule("end", t.toString("HH:mm"))
        )
        card.body.addWidget(Row("结束时间", "可以跨零点，例如 00:30", self.t_end))
        lay.addWidget(card)

        week = Card("适用星期")
        chips = QWidget()
        chips_lay = QHBoxLayout(chips)
        chips_lay.setContentsMargins(0, 0, 0, 0)
        chips_lay.setSpacing(6)
        self.day_buttons: List[QPushButton] = []
        for index, name in enumerate(DAYS):
            btn = QPushButton(name)
            btn.setObjectName("Chip")
            btn.setCheckable(True)
            btn.setFixedWidth(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setChecked(index in (rule.get("days") or []))
            btn.clicked.connect(lambda _=False, i=index: self._toggle_day(i))
            chips_lay.addWidget(btn)
            self.day_buttons.append(btn)
        chips_lay.addStretch(1)
        week.body.addWidget(Row("提醒哪几天", "", chips))
        lay.addWidget(week)

        rhythm = Card("提醒节奏")
        self.sp_interval = self._spin(rule.get("interval_min", 30), 5, 180, " 分钟")
        self.sp_interval.valueChanged.connect(
            lambda v: self._set_rule("interval_min", int(v))
        )
        rhythm.body.addWidget(
            Row("预热期基础间隔", "刚开始提醒时多久响一次", self.sp_interval)
        )
        rhythm.body.addWidget(Separator())

        self.sw_ramp = self._toggle(
            rule.get("ramp", True), lambda v: self._set_rule("ramp", v)
        )
        rhythm.body.addWidget(
            Row("越接近睡觉越频繁", "从基础间隔逐步压缩到最短间隔", self.sw_ramp)
        )
        rhythm.body.addWidget(Separator())

        self.sw_jitter = self._toggle(
            bool(self.cfg["smart"].get("random_jitter", True)),
            lambda v: self.cfg["smart"].__setitem__("random_jitter", v),
        )
        rhythm.body.addWidget(
            Row("加一点随机浮动", "避免每天都在同一秒钟被打断", self.sw_jitter)
        )
        rhythm.body.addWidget(Separator())

        self.sp_min_interval = self._spin(
            self.cfg["reminder"].get("min_interval_min", 5), 1, 60, " 分钟"
        )
        self.sp_min_interval.valueChanged.connect(
            lambda v: self.cfg["reminder"].__setitem__("min_interval_min", int(v))
        )
        rhythm.body.addWidget(
            Row("超时阶段最短间隔", "已经过了睡觉时间后，最快多久催一次", self.sp_min_interval)
        )
        lay.addWidget(rhythm)
        lay.addStretch(1)
        return scroll

    def _page_channels(self):
        scroll, lay = self._page()

        title = QLabel("提醒方式")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        hint = QLabel("提醒会按强度逐级升级，被忽略的次数越多，升级越快。")
        hint.setObjectName("PageHint")
        lay.addWidget(hint)

        strength = Card("强制程度")
        current = self.cfg["reminder"].get("strength", "standard")
        index = STRENGTH_KEYS.index(current) if current in STRENGTH_KEYS else 1
        self.seg_strength = Segmented(STRENGTH_ITEMS, self.theme, index)
        self._track(self.seg_strength)
        self.seg_strength.changed.connect(self._on_strength)
        strength.body.addWidget(self.seg_strength)

        self.lbl_strength = QLabel("")
        self.lbl_strength.setObjectName("RowHint")
        self.lbl_strength.setWordWrap(True)
        strength.body.addWidget(self.lbl_strength)
        lay.addWidget(strength)

        channels = Card("提醒通道")
        ch = self.cfg["reminder"].setdefault("channels", {})
        specs = [
            ("toast", "角落轻提醒", "3 秒后自动消失，不抢焦点"),
            ("popup", "中央弹窗", "带弹簧动效，需要点一下"),
            ("overlay", "全屏遮罩", "覆盖所有显示器，必须做出选择"),
            ("sound", "提示音", "柔和钟铃，可关"),
        ]
        for key, name, desc in specs:
            if key != specs[0][0]:
                channels.body.addWidget(Separator())
            sw = self._toggle(
                bool(ch.get(key, True)),
                lambda v, k=key: self.cfg["reminder"]["channels"].__setitem__(k, v),
            )
            channels.body.addWidget(Row(name, desc, sw))
        channels.body.addWidget(Separator())

        vol_wrap = QWidget()
        vol_lay = QHBoxLayout(vol_wrap)
        vol_lay.setContentsMargins(0, 0, 0, 0)
        vol_lay.setSpacing(10)
        self.sl_volume = QSlider(Qt.Orientation.Horizontal)
        self.sl_volume.setRange(0, 100)
        self.sl_volume.setValue(int(self.cfg["reminder"].get("sound_volume", 55)))
        self.sl_volume.setFixedWidth(180)
        self.sl_volume.valueChanged.connect(self._on_volume)
        vol_lay.addWidget(self.sl_volume)
        self.lbl_volume = QLabel("{}%".format(self.sl_volume.value()))
        self.lbl_volume.setObjectName("Tiny")
        self.lbl_volume.setFixedWidth(38)
        vol_lay.addWidget(self.lbl_volume)
        channels.body.addWidget(Row("音量", "", vol_wrap))
        lay.addWidget(channels)

        delay = Card("拖延的代价")
        options = self.cfg["reminder"].get("snooze_options", [5, 15, 30])
        opts_wrap = QWidget()
        opts_lay = QHBoxLayout(opts_wrap)
        opts_lay.setContentsMargins(0, 0, 0, 0)
        opts_lay.setSpacing(8)
        self.sp_snoozes: List[QSpinBox] = []
        for pos in range(3):
            value = int(options[pos]) if pos < len(options) else (5, 15, 30)[pos]
            box = self._spin(value, 1, 180, " 分钟", width=96)
            box.valueChanged.connect(self._on_snooze_options)
            opts_lay.addWidget(box)
            self.sp_snoozes.append(box)
        delay.body.addWidget(Row("「再给我 X 分钟」档位", "被忽略后下次间隔还会减半", opts_wrap))
        delay.body.addWidget(Separator())

        self.sp_escalate = self._spin(
            self.cfg["reminder"].get("escalate_after_sec", 45), 15, 600, " 秒"
        )
        self.sp_escalate.valueChanged.connect(
            lambda v: self.cfg["reminder"].__setitem__("escalate_after_sec", int(v))
        )
        delay.body.addWidget(Row("多久没回应就升级", "", self.sp_escalate))
        delay.body.addWidget(Separator())

        self.sw_pre = self._toggle(
            bool(self.cfg["reminder"].get("pre_notice", True)),
            lambda v: self.cfg["reminder"].__setitem__("pre_notice", v),
        )
        self.cfg.setdefault("reminder", {})
        delay.body.addWidget(
            Row("提前预告一下", "快到点时先轻轻提醒一句，降低突兀感", self.sw_pre)
        )
        delay.body.addWidget(Separator())

        self.sp_pre_min = self._spin(
            self.cfg["reminder"].get("pre_notice_min", 5), 1, 30, " 分钟"
        )
        self.sp_pre_min.valueChanged.connect(
            lambda v: self.cfg["reminder"].__setitem__("pre_notice_min", int(v))
        )
        delay.body.addWidget(Row("提前多久", "", self.sp_pre_min))
        lay.addWidget(delay)
        lay.addStretch(1)
        return scroll

    def _page_smart(self):
        scroll, lay = self._page()

        title = QLabel("智能避让")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        hint = QLabel("核心原则：你离开电脑，就是最好的回答。")
        hint.setObjectName("PageHint")
        lay.addWidget(hint)

        smart = self.cfg["smart"]
        avoid = Card("不要在最尴尬的时候弹出")
        self.sw_fullscreen = self._toggle(
            bool(smart.get("fullscreen_defer", True)),
            lambda v: self.cfg["smart"].__setitem__("fullscreen_defer", v),
        )
        avoid.body.addWidget(
            Row("全屏应用时先等等", "看视频、打游戏、开会演示时自动延后", self.sw_fullscreen)
        )
        avoid.body.addWidget(Separator())
        self.sp_defer = self._spin(smart.get("fullscreen_defer_min", 10), 1, 120, " 分钟")
        self.sp_defer.valueChanged.connect(
            lambda v: self.cfg["smart"].__setitem__("fullscreen_defer_min", int(v))
        )
        avoid.body.addWidget(Row("最多延后多久", "", self.sp_defer))
        lay.addWidget(avoid)

        away = Card("你离开就当你睡了")
        self.sw_idle = self._toggle(
            bool(smart.get("idle_pause", True)),
            lambda v: self.cfg["smart"].__setitem__("idle_pause", v),
        )
        away.body.addWidget(Row("空闲时不再提醒", "键鼠没有任何操作时自动安静", self.sw_idle))
        away.body.addWidget(Separator())

        bedtime = self.cfg["bedtime"]
        self.sp_idle_threshold = self._spin(
            smart.get("idle_threshold_min", 10), 1, 60, " 分钟"
        )
        self.sp_idle_threshold.valueChanged.connect(self._on_idle_threshold)
        away.body.addWidget(Row("空闲多久算离开", "", self.sp_idle_threshold))
        away.body.addWidget(Separator())

        self.sp_confirm_idle = self._spin(bedtime.get("confirm_idle_min", 10), 1, 60, " 分钟")
        self.sp_confirm_idle.valueChanged.connect(
            lambda v: self.cfg["bedtime"].__setitem__("confirm_idle_min", int(v))
        )
        away.body.addWidget(
            Row("离开多久算已休息", "超过这个时间就判定你今晚停下了，不再打扰", self.sp_confirm_idle)
        )
        lay.addWidget(away)

        overtime = Card("超时之后")
        self.sp_boost = self._spin(bedtime.get("overtime_boost_min", 15), 0, 120, " 分钟")
        self.sp_boost.valueChanged.connect(
            lambda v: self.cfg["bedtime"].__setitem__("overtime_boost_min", int(v))
        )
        overtime.body.addWidget(
            Row("超过睡觉时间多久拉满", "到点后直接进入最强等级", self.sp_boost)
        )
        overtime.body.addWidget(Separator())

        self.sp_grace = self._spin(bedtime.get("overtime_grace_min", 60), 0, 360, " 分钟")
        self.sp_grace.valueChanged.connect(
            lambda v: self.cfg["bedtime"].__setitem__("overtime_grace_min", int(v))
        )
        overtime.body.addWidget(
            Row("结束后还追多久", "窗口结束后仍在你电脑前，最多再催这么久，0 表示立刻放弃", self.sp_grace)
        )
        overtime.body.addWidget(Separator())

        self.sw_countdown = self._toggle(
            bool(bedtime.get("show_countdown", True)),
            lambda v: self.cfg["bedtime"].__setitem__("show_countdown", v),
        )
        overtime.body.addWidget(Row("显示距睡觉时间的倒计时", "", self.sw_countdown))
        lay.addWidget(overtime)

        extra = Card("附加（不属于早睡场景，按需开启）")
        eye = self.cfg["eye_care"]
        self.sw_eye = self._toggle(
            bool(eye.get("enabled", False)),
            lambda v: self.cfg["eye_care"].__setitem__("enabled", v),
        )
        extra.body.addWidget(Row("护眼 20-20-20", "每 20 分钟提醒看远处 20 秒", self.sw_eye))
        extra.body.addWidget(Separator())

        wrap = self.cfg["wrapup"]
        self.sw_wrapup = self._toggle(
            bool(wrap.get("enabled", True)),
            lambda v: self.cfg["wrapup"].__setitem__("enabled", v),
        )
        extra.body.addWidget(
            Row("收尾倒计时", "点「再给我 X 分钟」后在角落显示剩余时间", self.sw_wrapup)
        )
        extra.body.addWidget(Separator())

        lay.addWidget(extra)
        lay.addStretch(1)
        return scroll

    def _page_winddown(self):
        scroll, lay = self._page()

        title = QLabel("收尾模式")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        hint = QLabel(
            "点「我要收了」之后，按这套节奏催你收工：间隔越来越短、强度越来越高。"
            "它不受「睡觉时间」限制，什么时间点都能手动开。"
        )
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        rhythm = Card("提醒节奏")
        self.lbl_plan = QLabel("")
        self.lbl_plan.setObjectName("RowTitle")
        self.lbl_plan.setWordWrap(True)
        rhythm.body.addWidget(self.lbl_plan)
        rhythm.body.addWidget(Separator())

        self.wind_steps_wrap = QWidget()
        self.wind_steps_lay = QVBoxLayout(self.wind_steps_wrap)
        self.wind_steps_lay.setContentsMargins(0, 0, 0, 0)
        self.wind_steps_lay.setSpacing(6)
        rhythm.body.addWidget(self.wind_steps_wrap)

        buttons = QWidget()
        button_lay = QHBoxLayout(buttons)
        button_lay.setContentsMargins(0, 0, 0, 0)
        button_lay.setSpacing(8)

        self.btn_add_step = QPushButton("加一档")
        self.btn_add_step.setObjectName("Ghost")
        self.btn_add_step.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_step.clicked.connect(lambda: self._change_wind_steps(1))
        button_lay.addWidget(self.btn_add_step)

        self.btn_del_step = QPushButton("减一档")
        self.btn_del_step.setObjectName("Ghost")
        self.btn_del_step.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del_step.clicked.connect(lambda: self._change_wind_steps(-1))
        button_lay.addWidget(self.btn_del_step)
        button_lay.addStretch(1)
        rhythm.body.addWidget(buttons)
        rhythm.body.addWidget(Separator())

        note = QLabel(
            "走完最后一档后，会一直用最短的那一档继续催，"
            "直到你点「我已经收了」或者离开电脑。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        rhythm.body.addWidget(note)
        lay.addWidget(rhythm)

        strength = Card("强度渐进")
        mode = (self.cfg.get("winddown") or {}).get("level_mode", "ramp")
        level_index = LEVEL_MODE_KEYS.index(mode) if mode in LEVEL_MODE_KEYS else 0
        self.seg_level_mode = Segmented(LEVEL_MODE_ITEMS, self.theme, level_index)
        self._track(self.seg_level_mode)
        self.seg_level_mode.changed.connect(self._on_wind_level_mode)
        strength.body.addWidget(self.seg_level_mode)

        self.lbl_level_mode = QLabel("")
        self.lbl_level_mode.setObjectName("RowHint")
        self.lbl_level_mode.setWordWrap(True)
        strength.body.addWidget(self.lbl_level_mode)

        # 这条容易被误解，所以显式说明：
        # 「强制程度」是给自动提醒设的上限，不该管到用户主动开启的收尾
        independent = QLabel(
            "收尾模式的强度不受「提醒方式 → 强制程度」限制 —— 那是给自动提醒设的上限；"
            "收尾是你自己主动开的，理应允许升到最强。"
        )
        independent.setObjectName("Tiny")
        independent.setWordWrap(True)
        strength.body.addWidget(independent)
        lay.addWidget(strength)

        defer = Card("延后提醒")
        self.sp_defer = self._spin(
            int((self.cfg.get("winddown") or {}).get("defer_min", 5) or 5),
            1,
            60,
            " 分钟",
            width=110,
        )
        self.sp_defer.valueChanged.connect(self._on_wind_defer_changed)
        defer.body.addWidget(
            Row(
                "延后时长",
                "点「延后提醒」后隔多久再来。只跳过一次，档位和强度都不变",
                self.sp_defer,
            )
        )
        lay.addWidget(defer)

        entry = Card("怎么进入收尾模式")
        for text in (
            "设置窗口底部：「我要收了」按钮（就是左下角那个）",
            "托盘图标上点右键：「我要收了（收尾模式）」",
            "提醒上三个选项：我已经收了（结束）/ 延后提醒（跳过这次）/ 结束收尾（整个退出）",
        ):
            item = QLabel("· " + text)
            item.setObjectName("RowHint")
            item.setWordWrap(True)
            entry.body.addWidget(item)
        lay.addWidget(entry)

        lay.addStretch(1)

        self._rebuild_wind_steps()
        self._refresh_level_mode_hint()
        return scroll

    # ------------------------------------------------------------ 收尾模式回调

    def _wind_intervals(self) -> List[float]:
        raw = (self.cfg.get("winddown") or {}).get("intervals_min") or [5, 2.5, 2, 1]
        out: List[float] = []
        for item in raw:
            try:
                value = float(item)
            except Exception:
                continue
            if value > 0:
                out.append(round(value, 2))
        return out or [5.0, 2.5, 2.0, 1.0]

    def _rebuild_wind_steps(self) -> None:
        while self.wind_steps_lay.count():
            item = self.wind_steps_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.wind_spins: List[QDoubleSpinBox] = []
        for position, value in enumerate(self._wind_intervals()):
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(10)

            label = QLabel("第 {} 次提醒等多久".format(position + 1))
            label.setObjectName("RowTitle")
            row_lay.addWidget(label, 1)

            spin = QDoubleSpinBox()
            spin.setRange(0.25, 120.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.5)
            spin.setSuffix(" 分钟")
            spin.setFixedWidth(132)
            spin.setValue(float(value))
            spin.valueChanged.connect(self._on_wind_steps_changed)
            row_lay.addWidget(spin, 0)
            self.wind_spins.append(spin)
            self.wind_steps_lay.addWidget(row)

        self._refresh_wind_plan()

    def _on_wind_steps_changed(self, _value: float = 0.0) -> None:
        values = [round(float(spin.value()), 2) for spin in self.wind_spins]
        self.cfg.setdefault("winddown", {})["intervals_min"] = values
        self._refresh_wind_plan()
        self._commit()

    def _change_wind_steps(self, delta: int) -> None:
        values = self._wind_intervals()
        if delta > 0:
            if len(values) >= WIND_MAX_STEPS:
                return
            tail = values[-1] if values else 1.0
            values.append(max(0.25, round(tail / 2, 2)))
        else:
            if len(values) <= WIND_MIN_STEPS:
                return
            values.pop()
        self.cfg.setdefault("winddown", {})["intervals_min"] = values
        self._rebuild_wind_steps()
        self._commit()

    def _refresh_wind_plan(self) -> None:
        if not hasattr(self, "lbl_plan"):
            return
        values = self._wind_intervals()
        parts = [fmt_duration(value * 60) for value in values]
        self.lbl_plan.setText(
            "当前节奏：{} → 之后每 {}".format(
                " → ".join(parts), fmt_duration(values[-1] * 60)
            )
        )
        if hasattr(self, "btn_add_step"):
            self.btn_add_step.setEnabled(len(values) < WIND_MAX_STEPS)
            self.btn_del_step.setEnabled(len(values) > WIND_MIN_STEPS)

    def _on_wind_level_mode(self, index: int) -> None:
        self.cfg.setdefault("winddown", {})["level_mode"] = LEVEL_MODE_KEYS[index]
        self._refresh_level_mode_hint()
        self._commit()

    def _refresh_level_mode_hint(self) -> None:
        if not hasattr(self, "lbl_level_mode"):
            return
        key = (self.cfg.get("winddown") or {}).get("level_mode", "ramp")
        self.lbl_level_mode.setText(LEVEL_MODE_HINTS.get(key, ""))

    def _on_wind_defer_changed(self, value: int) -> None:
        self.cfg.setdefault("winddown", {})["defer_min"] = int(value)
        self._commit()

    def _page_general(self):
        scroll, lay = self._page()

        title = QLabel("通用")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        hint = QLabel("配置与程序放在同一个目录，卸载就是删掉这一个 exe。")
        hint.setObjectName("PageHint")
        lay.addWidget(hint)

        look = Card("外观")
        current = self.cfg.get("theme", "dark")
        index = THEME_KEYS.index(current) if current in THEME_KEYS else 0
        self.seg_theme = Segmented(THEME_ITEMS, self.theme, index)
        self._track(self.seg_theme)
        self.seg_theme.changed.connect(self._on_theme)
        look.body.addWidget(Row("主题", "", self.seg_theme))
        look.body.addWidget(Separator())

        self.sw_motion = self._toggle(
            bool(self.cfg["general"].get("reduce_motion", False)),
            lambda v: self.cfg["general"].__setitem__("reduce_motion", v),
        )
        look.body.addWidget(Row("减少动画", "关掉所有过渡动效，界面更安静", self.sw_motion))
        lay.addWidget(look)

        startup = Card("启动")
        self.sw_autostart = self._toggle(
            winapi.autostart_enabled(), lambda v: self._on_autostart(v)
        )
        startup.body.addWidget(
            Row("开机自动启动", "在启动文件夹放一个快捷方式，删掉它就等于取消", self.sw_autostart)
        )
        self.lbl_autostart = QLabel("")
        self.lbl_autostart.setObjectName("Tiny")
        self.lbl_autostart.setWordWrap(True)
        startup.body.addWidget(self.lbl_autostart)

        open_btn = QPushButton("打开启动文件夹")
        open_btn.setObjectName("Ghost")
        open_btn.clicked.connect(winapi.open_startup_folder)
        wrapper = QWidget()
        wrapper_lay = QHBoxLayout(wrapper)
        wrapper_lay.setContentsMargins(0, 0, 0, 0)
        wrapper_lay.addWidget(open_btn)
        wrapper_lay.addStretch(1)
        startup.body.addWidget(wrapper)

        self.sw_hotkey = self._toggle(
            bool(self.cfg["general"].get("hotkey_enabled", True)),
            lambda v: self.cfg["general"].__setitem__("hotkey_enabled", v),
        )
        startup.body.addWidget(Separator())
        startup.body.addWidget(
            Row("启用全局快捷键", "任何时候按下都能暂停或恢复提醒", self.sw_hotkey)
        )

        self.ed_hotkey = QLineEdit(self.cfg["general"].get("hotkey", "Ctrl+Alt+R"))
        self.ed_hotkey.setFixedWidth(150)
        self.ed_hotkey.editingFinished.connect(self._on_hotkey)
        startup.body.addWidget(Row("快捷键", "格式例如 Ctrl+Alt+R", self.ed_hotkey))
        lay.addWidget(startup)

        rest_card = Card("「我去睡了」之后：自动关机")
        after = self.cfg["after_rest"]
        self.seg_after = Segmented(
            ["只记录", "自动关机"], self.theme, 1 if after.get("mode") == "shutdown" else 0
        )
        self._track(self.seg_after)
        self.seg_after.changed.connect(self._on_after_rest_mode)
        rest_card.body.addWidget(
            Row("自动关机", "让「我去睡了」真的能把电脑停下来", self.seg_after)
        )
        rest_card.body.addWidget(Separator())

        self.sp_grace = self._spin(int(after.get("grace_sec", 120)), 30, 900, " 秒")
        self.sp_grace.valueChanged.connect(self._on_after_rest_grace)
        rest_card.body.addWidget(
            Row("关机宽限时间", "留足时间给你保存文件，期间随时可以取消", self.sp_grace)
        )

        self.lbl_after = QLabel("")
        self.lbl_after.setObjectName("Tiny")
        self.lbl_after.setWordWrap(True)
        rest_card.body.addWidget(self.lbl_after)
        lay.addWidget(rest_card)

        safety = Card("安全")
        pw_wrap = QWidget()
        pw_lay = QHBoxLayout(pw_wrap)
        pw_lay.setContentsMargins(0, 0, 0, 0)
        pw_lay.addWidget(QLabel(""))
        pw_lay.addStretch(1)
        self.btn_password = QPushButton("设置密码")
        self.btn_password.clicked.connect(self._on_password)
        pw_lay.addWidget(self.btn_password)
        safety.body.addWidget(
            Row("硬核模式密码", "开启后跳过或暂停提醒都需要输入密码", pw_wrap)
        )
        self.lbl_password = QLabel("")
        self.lbl_password.setObjectName("Tiny")
        safety.body.addWidget(self.lbl_password)
        lay.addWidget(safety)

        data = Card("数据与卸载")
        self.lbl_config = QLabel("")
        self.lbl_config.setObjectName("Tiny")
        self.lbl_config.setWordWrap(True)
        data.body.addWidget(self.lbl_config)

        btn_row = QWidget()
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(8)
        open_dir = QPushButton("打开所在文件夹")
        open_dir.setObjectName("Ghost")
        open_dir.clicked.connect(self._open_config_dir)
        btn_lay.addWidget(open_dir)
        clear = QPushButton("一键清除所有痕迹")
        clear.setObjectName("Danger")
        clear.clicked.connect(self.clearTracesRequested.emit)
        btn_lay.addWidget(clear)
        btn_lay.addStretch(1)
        data.body.addWidget(btn_row)
        lay.addWidget(data)

        quit_card = Card("")
        qbtn = QPushButton("退出程序")
        qbtn.setObjectName("Ghost")
        qbtn.clicked.connect(self.quitRequested.emit)
        q_wrap = QWidget()
        q_lay = QHBoxLayout(q_wrap)
        q_lay.setContentsMargins(0, 0, 0, 0)
        q_lay.addWidget(qbtn)
        q_lay.addStretch(1)
        quit_card.body.addWidget(q_wrap)
        lay.addWidget(quit_card)

        lay.addStretch(1)
        return scroll

    # ------------------------------------------------------------ 交互回调

    def switch_page(self, index: int) -> None:
        if index == self.stack.currentIndex():
            return
        self.stack.setCurrentIndex(index)
        self._nav_buttons[index].setChecked(True)
        page = self.stack.currentWidget()
        if self.reduce_motion or page is None:
            return
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(EASE_OUT_CUBIC)

        def _cleanup() -> None:
            try:
                page.setGraphicsEffect(None)
            except Exception:
                pass

        anim.finished.connect(_cleanup)
        anim.start(DELETE_WHEN_STOPPED)

    def _toggle_day(self, index: int) -> None:
        days = list(self._rule().get("days") or [])
        if index in days:
            days.remove(index)
        else:
            days.append(index)
        days.sort()
        self._set_rule("days", days)

    def _on_strength(self, index: int) -> None:
        key = STRENGTH_KEYS[index]
        self.cfg["reminder"]["strength"] = key
        self._refresh_strength_hint()
        if key == "hardcore" and not self.cfg["general"].get("password_hash"):
            self._on_password()
        self._commit()

    def _refresh_strength_hint(self) -> None:
        key = self.cfg["reminder"].get("strength", "standard")
        self.lbl_strength.setText(STRENGTH_HINTS.get(key, ""))

    def _on_volume(self, value: int) -> None:
        self.cfg["reminder"]["sound_volume"] = int(value)
        self.lbl_volume.setText("{}%".format(int(value)))
        self._commit()

    def _on_snooze_options(self, _value: int = 0) -> None:
        options = sorted({int(box.value()) for box in self.sp_snoozes})
        self.cfg["reminder"]["snooze_options"] = options
        self._commit()

    def _on_idle_threshold(self, value: int) -> None:
        self.cfg["smart"]["idle_threshold_min"] = int(value)
        if self.sp_confirm_idle is not None:
            self.sp_confirm_idle.setValue(int(value))
        self.cfg["bedtime"]["confirm_idle_min"] = int(value)
        self._commit()

    def _on_theme(self, index: int) -> None:
        self.cfg["theme"] = THEME_KEYS[index]
        self._commit(theme_changed=True)

    def _on_after_rest_mode(self, index: int) -> None:
        self.cfg["after_rest"]["mode"] = "shutdown" if index == 1 else "none"
        self._refresh_after_hint()
        self._commit()

    def _on_after_rest_grace(self, value: int) -> None:
        self.cfg["after_rest"]["grace_sec"] = int(value)
        self._refresh_after_hint()
        self._commit()

    def _refresh_after_hint(self) -> None:
        conf = self.cfg.get("after_rest") or {}
        if conf.get("mode") == "shutdown":
            seconds = max(30, int(conf.get("grace_sec", 120)))
            self.lbl_after.setText(
                "点「我去睡了」后会立刻安排一次系统关机（{} 分 {} 秒后执行）。"
                "这段时间里右下角会显示倒计时，随时可以点「取消关机」撤销。"
                "关机由系统执行，会正常给各个程序保存文件的机会。".format(
                    seconds // 60, seconds % 60
                )
            )
        else:
            self.lbl_after.setText(
                "当前只记录休息，不会对电脑做任何操作。"
                "如果想让它真的把电脑停下来，把上面切成「自动关机」。"
            )

    def _on_autostart(self, enabled: bool) -> None:
        if enabled:
            ok = winapi.enable_autostart()
        else:
            ok = winapi.disable_autostart()
        self.cfg["general"]["autostart"] = bool(enabled and ok)
        if not ok:
            self.sw_autostart.setChecked(winapi.autostart_enabled())
        self._refresh_autostart_hint()
        self._commit()

    def _refresh_autostart_hint(self) -> None:
        if winapi.autostart_enabled():
            self.lbl_autostart.setText(
                "已启用。快捷方式位于 {} ，删掉它即可取消。".format(
                    winapi.autostart_location_hint()
                )
            )
        else:
            self.lbl_autostart.setText("当前未启用，程序不会随系统启动。")

    def _on_hotkey(self) -> None:
        text = self.ed_hotkey.text().strip()
        if text:
            self.cfg["general"]["hotkey"] = text
            self._commit()

    def _on_password(self) -> None:
        has = bool(self.cfg["general"].get("password_hash"))
        if has:
            old = ask_password(
                self, self.theme, "验证密码", "先输入当前密码，才能修改。"
            )
            if old is None:
                return
            if not cfgmod.verify_password(self.cfg, old):
                ask_confirm(self, self.theme, "密码不对", "当前密码不正确，请重试。")
                return
        value = ask_password(
            self,
            self.theme,
            "设置密码" if not has else "修改密码",
            "这个密码只保存在本机配置文件里，不需要联网，也不会要求任何系统权限。留空取消。",
            confirm=True,
        )
        if value is None:
            return
        cfgmod.set_password(self.cfg, value)
        self._refresh_password_hint()
        self._commit()

    def _refresh_password_hint(self) -> None:
        if self.cfg["general"].get("password_hash"):
            self.lbl_password.setText("已设置密码。请记牢，程序无法帮你找回。")
            self.btn_password.setText("修改密码")
        else:
            self.lbl_password.setText("未设置密码，硬核模式下可以随时跳过。")
            self.btn_password.setText("设置密码")

    def _refresh_config_hint(self) -> None:
        mode = "与程序同目录（绿色模式）" if cfgmod.is_portable() else "用户目录（程序目录不可写）"
        self.lbl_config.setText("配置文件：{}\n位置：{}".format(cfgmod.config_path(), mode))
        self._refresh_after_hint()
        self._refresh_wind_plan()
        self._refresh_level_mode_hint()

    def _open_config_dir(self) -> None:
        path = os.path.dirname(cfgmod.config_path())
        try:
            os.startfile(path)  # noqa: S606
        except Exception:
            pass

    # ------------------------------------------------------------ 对外刷新

    def refresh_status(self, status: Dict[str, Any]) -> None:
        paused = bool(status.get("paused"))
        winddown = status.get("winddown") or {}
        wind = bool(winddown.get("active"))

        self.btn_resume.setVisible(paused)
        self.btn_pause_60.setVisible(not paused)
        self.btn_pause_window.setVisible(not paused)
        # 收尾模式进行中就不用再显示入口了
        self.btn_winddown.setVisible(not wind and not paused)

        if wind:
            self.status_label.setText(
                "收尾模式 · 已收尾 {}，下次 {} 后".format(
                    fmt_duration(winddown.get("elapsed_sec", 0)),
                    fmt_duration(winddown.get("next_in_sec", 0)),
                )
            )
            return

        if paused:
            until = status.get("paused_until")
            text = "已暂停"
            if until is not None:
                text = "已暂停至 {}".format(until.strftime("%H:%M"))
        elif status.get("rested_today"):
            text = "今晚已经确认休息，晚安"
        elif not status.get("in_window"):
            text = "当前不在提醒时段"
        else:
            remain = int(status.get("remain_min", 0))
            name = status.get("rule_name", "")
            if remain < 0:
                text = "{} · 已超时 {} 分钟".format(name, -remain)
            elif remain >= 60:
                text = "{} · 距目标 {} 小时 {} 分".format(name, remain // 60, remain % 60)
            else:
                text = "{} · 距目标 {} 分钟".format(name, remain)

        self.status_label.setText(text)

    def refresh_from_config(self) -> None:
        """外部改动配置后同步界面。"""
        self._refresh_password_hint()
        self._refresh_autostart_hint()
        self._refresh_config_hint()
        self._refresh_strength_hint()
        self.sw_autostart.setChecked(winapi.autostart_enabled())

    def focus_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


# 需要放在文件末尾导入，避免循环引用
from PySide6.QtWidgets import QGraphicsOpacityEffect  # noqa: E402
