"""运行时资源：程序图标、托盘图标、提示音，全部代码生成。

这样绿色版不需要携带任何外部素材文件。
"""

from __future__ import annotations

import array
import math
import os
import tempfile
import wave
from typing import Dict, List, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap

_ICON_CACHE: Dict[str, QIcon] = {}
_CHIME_PATH: Optional[str] = None


# ------------------------------------------------------------------ 图标


def _draw_mark(painter: QPainter, size: int, bg: str, fg: str, paused: bool) -> None:
    painter.setRenderHint(QPainter.Antialiasing, True)

    radius = size * 0.23
    back = QPainterPath()
    back.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.fillPath(back, QColor(bg))

    # 月牙 = 大圆挖掉一个上右偏移的圆
    outer = QPainterPath()
    outer.addEllipse(QRectF(size * 0.20, size * 0.24, size * 0.54, size * 0.54))
    inner = QPainterPath()
    inner.addEllipse(QRectF(size * 0.35, size * 0.15, size * 0.54, size * 0.54))
    painter.fillPath(outer.subtracted(inner), QColor(fg))

    # 三颗小星
    star = QColor(fg)
    star.setAlpha(210 if not paused else 120)
    painter.setBrush(star)
    painter.setPen(Qt.NoPen)
    for cx, cy, r in ((0.74, 0.30, 0.045), (0.80, 0.47, 0.030), (0.68, 0.62, 0.024)):
        painter.drawEllipse(
            QRectF(size * (cx - r), size * (cy - r), size * r * 2, size * r * 2)
        )

    if paused:
        bar = QColor(fg)
        painter.setBrush(bar)
        for x in (0.30, 0.42):
            painter.drawRoundedRect(
                QRectF(size * x, size * 0.60, size * 0.075, size * 0.26),
                size * 0.02,
                size * 0.02,
            )


def app_icon(palette: Dict[str, str], paused: bool = False) -> QIcon:
    """生成多尺寸 QIcon（托盘与窗口共用）。"""
    key = "{}|{}".format(palette.get("accent", ""), paused)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    icon = QIcon()
    bg = palette.get("accent", "#6C8CFF")
    fg = "#FFFFFF"
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        _draw_mark(p, size, bg, fg, paused)
        p.end()
        icon.addPixmap(pm)
    _ICON_CACHE[key] = icon
    return icon


def app_icon_path_ico() -> Optional[str]:
    """打包时写入 exe 的 .ico，运行时若不存在则为 None。"""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cand = os.path.join(here, "assets", "icon.ico")
    return cand if os.path.isfile(cand) else None


# ------------------------------------------------------------------ 提示音


def _chime_samples(rate: int = 44100, duration: float = 1.7, volume: int = 55) -> array.array:
    """一声柔和的钟铃：两个泛音 + 指数衰减包络。

    音量直接烘焙进波形 —— winsound 不支持运行时调音量，
    但这样反而少了一个播放器依赖。
    """
    total = int(rate * duration)
    gain = max(0.0, min(1.0, float(volume) / 100.0)) ** 1.4
    buf = array.array("h")
    partials = ((784.0, 0.62, 0.0, 2.9), (1046.5, 0.34, 0.16, 2.6))
    for i in range(total):
        t = i / rate
        sample = 0.0
        for freq, amp, delay, decay in partials:
            if t < delay:
                continue
            tt = t - delay
            env = math.exp(-decay * tt) * (1.0 - math.exp(-70.0 * tt))
            sample += amp * env * math.sin(2.0 * math.pi * freq * tt)
        clamped = max(-1.0, min(1.0, sample))
        buf.append(int(clamped * 32767 * 0.9 * gain))
    return buf


_CHIME_CACHE: Dict[int, str] = {}


def chime_path(volume: int = 55) -> Optional[str]:
    """生成并缓存提示音 wav（放在临时目录，不污染 exe 目录）。"""
    bucket = max(0, min(100, int(round(float(volume) / 10.0) * 10)))
    cached = _CHIME_CACHE.get(bucket)
    if cached and os.path.isfile(cached):
        return cached
    try:
        path = os.path.join(
            tempfile.gettempdir(), "restreminder_chime_{}.wav".format(bucket)
        )
        if not os.path.isfile(path):
            data = _chime_samples(volume=bucket)
            with wave.open(path, "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(44100)
                fh.writeframes(data.tobytes())
        _CHIME_CACHE[bucket] = path
        return path
    except Exception:
        return None


def cleanup_generated_files() -> None:
    """一键清除痕迹时删掉所有生成的临时提示音。"""
    for path in list(_CHIME_CACHE.values()):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass
    _CHIME_CACHE.clear()
    try:
        base = tempfile.gettempdir()
        if os.path.isdir(base):
            for name in os.listdir(base):
                if name.startswith("restreminder_chime_") and name.endswith(".wav"):
                    try:
                        os.remove(os.path.join(base, name))
                    except Exception:
                        pass
    except Exception:
        pass


# ------------------------------------------------------------------ 小贴士


def pick_tips(tips: List[str], count: int = 1) -> List[str]:
    import random

    pool = [t for t in (tips or []) if str(t).strip()]
    if not pool:
        return []
    count = max(1, min(count, len(pool)))
    return random.sample(pool, count)
