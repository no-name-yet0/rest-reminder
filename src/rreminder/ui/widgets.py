"""界面公共组件：卡片、动画开关、分段选择器、浮层基类。"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    Property,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import RADIUS_LG, RADIUS_MD

EASE_OUT_CUBIC = QEasingCurve.Type.OutCubic
EASE_IN_CUBIC = QEasingCurve.Type.InCubic
EASE_OUT_BACK = QEasingCurve.Type.OutBack
EASE_IN_OUT_CUBIC = QEasingCurve.Type.InOutCubic
DELETE_WHEN_STOPPED = QAbstractAnimation.DeletionPolicy.DeleteWhenStopped


def scale_duration(ms: int, reduce_motion: bool) -> int:
    """用户或系统开启「减少动画」时把时长压到 0。"""
    return 0 if reduce_motion else max(0, int(ms))


def animate_property(
    target: QWidget,
    name: bytes,
    start,
    end,
    duration: int,
    curve: QEasingCurve.Type = EASE_OUT_CUBIC,
    on_finished: Optional[Callable[[], None]] = None,
) -> QPropertyAnimation:
    anim = QPropertyAnimation(target, name, target)
    anim.setDuration(max(0, int(duration)))
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(curve)
    if on_finished is not None:
        anim.finished.connect(on_finished)
    anim.start(DELETE_WHEN_STOPPED)
    return anim


def screen_for_cursor():
    screen = QGuiApplication.screenAt(QCursor.pos())
    return screen or QGuiApplication.primaryScreen()


# ------------------------------------------------------------------ 基础控件


class Card(QFrame):
    """带标题的卡片容器。内容加进 card.body。"""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        self.title_label: Optional[QLabel] = None
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("CardTitle")
            outer.addWidget(self.title_label)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body)


class Separator(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sep")
        self.setFixedHeight(1)


class Row(QWidget):
    """一行设置项：左边标题与说明，右边放控件。"""

    def __init__(
        self,
        title: str,
        hint: str = "",
        right: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("RowTitle")
        left.addWidget(self.title_label)

        self.hint_label: Optional[QLabel] = None
        if hint:
            self.hint_label = QLabel(hint)
            self.hint_label.setObjectName("RowHint")
            self.hint_label.setWordWrap(True)
            left.addWidget(self.hint_label)
        lay.addLayout(left, 1)

        if right is not None:
            lay.addWidget(right, 0, Qt.AlignVCenter)

    def set_hint(self, text: str) -> None:
        if self.hint_label is None:
            self.hint_label = QLabel(text)
            self.hint_label.setObjectName("RowHint")
            self.hint_label.setWordWrap(True)
            self.layout().itemAt(0).layout().addWidget(self.hint_label)  # type: ignore[union-attr]
        self.hint_label.setText(text)


class ToggleSwitch(QWidget):
    """自绘动画开关，观感比系统 QCheckBox 好很多。"""

    toggled = Signal(bool)

    def __init__(
        self,
        checked: bool = False,
        theme: Optional[Dict[str, str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme or {}
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = bool(checked)
        self._knob = 1.0 if checked else 0.0
        self._look_enabled = True

        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(190)
        self._anim.setEasingCurve(EASE_OUT_CUBIC)

    def _get_knob(self) -> float:
        return self._knob

    def _set_knob(self, value: float) -> None:
        self._knob = float(value)
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, value: bool) -> None:  # noqa: N802
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if value else 0.0)
        self._anim.start()

    def set_theme(self, theme: Dict[str, str]) -> None:
        self.theme = theme
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        radius = h / 2.0

        off = QColor(self.theme.get("border_strong", "#C9CEDD"))
        on = QColor(self.theme.get("accent", "#4A6CF7"))
        t = max(0.0, min(1.0, self._knob))
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)

        knob_d = h - 5.0
        knob_x = 2.5 + (w - knob_d - 5.0) * t
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(knob_x, 2.5, knob_d, knob_d))
        painter.end()


class Segmented(QWidget):
    """分段选择器，选中块会平滑滑动。"""

    changed = Signal(int)

    def __init__(
        self,
        items: List[str],
        theme: Optional[Dict[str, str]] = None,
        current: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme or {}
        self.items = list(items)
        self.setFixedHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._index = max(0, min(current, len(self.items) - 1)) if self.items else 0
        self._slide = float(self._index)

        # 属性名绝对不能叫 pos！QWidget 自带 pos（QPoint，控件在父容器中的位置），
        # 同名会让动画去 move 控件本身而不是滑动选中块 ——
        # 表现就是点一下控件跳到左边、且选中块毫无反应。
        self._anim = QPropertyAnimation(self, b"slide", self)
        self._anim.setDuration(230)
        self._anim.setEasingCurve(EASE_OUT_CUBIC)

    def _segment_width(self) -> int:
        return 88 if all(len(item) <= 3 for item in self.items) else 104

    def sizeHint(self) -> QSize:  # noqa: N802
        """必须给出尺寸。

        QWidget 默认的 sizeHint 是无效值：放进 Row 这种「左边占 stretch」的
        横向布局里，这个控件会被压成 0 宽 —— 存在、能点，但完全看不见。
        """
        return QSize(self._segment_width() * max(1, len(self.items)), 36)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(56 * max(1, len(self.items)), 36)

    def _get_slide(self) -> float:
        return self._slide

    def _set_slide(self, value: float) -> None:
        self._slide = float(value)
        self.update()

    slide = Property(float, _get_slide, _set_slide)

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def setCurrentIndex(self, index: int, emit: bool = False) -> None:  # noqa: N802
        if not self.items:
            return
        index = max(0, min(int(index), len(self.items) - 1))
        if index == self._index:
            return
        self._index = index
        self._anim.stop()
        self._anim.setStartValue(self._slide)
        self._anim.setEndValue(float(index))
        self._anim.start()
        if emit:
            self.changed.emit(index)

    def set_theme(self, theme: Dict[str, str]) -> None:
        self.theme = theme
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.items:
            return
        seg_w = self.width() / float(len(self.items))
        index = int(event.position().x() // seg_w)
        index = max(0, min(index, len(self.items) - 1))
        if index != self._index:
            self.setCurrentIndex(index, emit=True)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme.get("bg_alt", "#EBEDF4")))
        painter.drawRoundedRect(QRectF(0, 0, w, h), float(RADIUS_MD), float(RADIUS_MD))

        seg_w = w / float(len(self.items))
        pill_x = self._slide * seg_w + 3.0
        pill_w = max(0.0, seg_w - 6.0)
        painter.setBrush(QColor(self.theme.get("accent", "#4A6CF7")))
        painter.drawRoundedRect(
            QRectF(pill_x, 3.0, pill_w, h - 6.0),
            float(RADIUS_MD) - 3.0,
            float(RADIUS_MD) - 3.0,
        )

        font = QFont(self.font())
        font.setPointSizeF(9.5)
        for i, text in enumerate(self.items):
            selected = abs(i - self._slide) < 0.5
            font.setWeight(QFont.Weight.Medium if selected else QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(
                QColor(
                    self.theme.get("on_accent", "#FFFFFF")
                    if selected
                    else self.theme.get("text_dim", "#5B6273")
                )
            )
            painter.drawText(QRectF(i * seg_w, 0, seg_w, h), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


# ------------------------------------------------------------------ 浮层基类


class FloatCard(QWidget):
    """无边框、透明底、带柔和投影的浮层窗口基类。内容加进 self.body。"""

    SHADOW_MARGIN = 26

    def __init__(
        self,
        theme: Dict[str, str],
        radius: int = RADIUS_LG,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(None)
        self.theme = dict(theme)
        self._radius = radius
        self._margin = self.SHADOW_MARGIN

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._margin, self._margin, self._margin, self._margin)
        outer.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("FloatCard")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(34)
        shadow.setColor(QColor(0, 0, 0, 130))
        shadow.setOffset(0, 7)
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)

        self.apply_theme(self.theme)

    def apply_theme(self, theme: Dict[str, str]) -> None:
        self.theme = dict(theme)
        self.card.setStyleSheet(
            """
            QFrame#FloatCard {{
                background: {bg};
                border: 1px solid {border};
                border-radius: {radius}px;
            }}
            QLabel#FloatTitle {{ font-size: 15px; font-weight: 500; color: {text}; }}
            QLabel#FloatSub {{ font-size: 12px; color: {text_dim}; }}
            QLabel#FloatTiny {{ font-size: 12px; color: {text_faint}; }}
            QFrame#FloatCard QLabel {{ color: {text}; }}
            QPushButton {{
                background: transparent;
                border: 1px solid {border_strong};
                border-radius: 10px;
                color: {text};
                padding: 7px 14px;
                font-size: 13px;
            }}
            QPushButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton#Primary {{
                background: {accent};
                border: 1px solid {accent};
                color: {on_accent};
                font-weight: 500;
            }}
            QPushButton#Primary:hover {{
                background: {accent_hover};
                border-color: {accent_hover};
                color: {on_accent};
            }}
            QPushButton#Subtle {{
                background: transparent;
                border: none;
                color: {text_faint};
                font-size: 12px;
                padding: 4px 6px;
            }}
            QPushButton#Subtle:hover {{ color: {text_dim}; }}
            """.format(
                bg=theme.get("surface", "#1A1E28"),
                border=theme.get("border", "#2A2F3E"),
                radius=self._radius,
                text=theme.get("text", "#E9EBF2"),
                text_dim=theme.get("text_dim", "#99A1B5"),
                text_faint=theme.get("text_faint", "#6A7285"),
                border_strong=theme.get("border_strong", "#3A4055"),
                accent=theme.get("accent", "#6C8CFF"),
                accent_hover=theme.get("accent_hover", "#7E9BFF"),
                on_accent=theme.get("on_accent", "#FFFFFF"),
            )
        )

    def place_center(self, offset: Tuple[int, int] = (0, 0)) -> None:
        screen = screen_for_cursor()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.adjustSize()
        x = area.center().x() - self.width() // 2 + offset[0]
        y = area.center().y() - self.height() // 2 + offset[1]
        self.move(max(area.left(), x), max(area.top(), y))

    def place_bottom_right(self, margin: int = 10) -> None:
        screen = screen_for_cursor()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.adjustSize()
        x = area.right() - self.width() - margin + 1
        y = area.bottom() - self.height() - margin + 1
        self.move(max(area.left(), x), max(area.top(), y))
        self._anchor = (x, y)

    def fade_in(self, duration: int = 260, start: float = 0.0) -> None:
        self.setWindowOpacity(start)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(max(0, duration))
        anim.setStartValue(start)
        anim.setEndValue(1.0)
        anim.setEasingCurve(EASE_OUT_CUBIC)
        anim.start(DELETE_WHEN_STOPPED)

    def fade_out(
        self, duration: int = 200, on_done: Optional[Callable[[], None]] = None
    ) -> QPropertyAnimation:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(max(0, duration))
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(EASE_IN_CUBIC)
        if on_done is not None:
            anim.finished.connect(on_done)
        anim.start(DELETE_WHEN_STOPPED)
        return anim


def fmt_duration(seconds: float) -> str:
    """把秒数写成「3 分 12 秒」这种好读的形式。"""
    total = max(0, int(round(float(seconds))))
    minutes, secs = divmod(total, 60)
    if minutes and secs:
        return "{} 分 {} 秒".format(minutes, secs)
    if minutes:
        return "{} 分钟".format(minutes)
    return "{} 秒".format(secs)
