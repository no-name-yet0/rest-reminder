"""把设置窗口每一页渲染成 PNG，用来目视检查布局。

只检查「控件存在」是不够的 —— 自绘控件如果没有有效 sizeHint，
会被布局压成 0 宽：存在、能点、但看不见。必须看实际渲染宽度和截图。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rreminder import config as cfgmod  # noqa: E402
from rreminder import theme as thememod  # noqa: E402
from rreminder.ui.settings_win import SettingsWindow  # noqa: E402

SHOT_DIR = os.path.join(ROOT, "_shots")
REPORT = os.path.join(ROOT, "_shots.txt")


def main() -> int:
    os.makedirs(SHOT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    cfg = cfgmod.load()
    win = SettingsWindow(cfg, thememod.palette("dark"), False)

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    win.show()
    pump(1000)

    lines = []
    problems = 0

    for widget, label in (
        (win.seg_theme, "主题分段控件"),
        (win.seg_after, "自动关机分段控件"),
        (win.seg_strength, "强制程度分段控件"),
        (win.seg_level_mode, "强度渐进分段控件"),
        (win.btn_winddown, "「我要收了」按钮"),
        (win.btn_rest, "「我这就去睡」按钮"),
    ):
        hint = widget.sizeHint()
        actual = (widget.width(), widget.height())
        visible = widget.isVisible()
        ok = actual[0] > 60 and actual[1] > 10
        if not ok:
            problems += 1
        lines.append(
            "{}{:<16} sizeHint={} 实际={} 可见={}".format(
                "OK   " if ok else "BAD  ", label, (hint.width(), hint.height()), actual, visible
            )
        )

    lines.append("")
    for index in range(win.stack.count()):
        win.switch_page(index)
        pump(450)
        name = win._nav_buttons[index].text()
        path = os.path.join(SHOT_DIR, "{}-{}.png".format(index, name))
        win.grab().save(path)
        lines.append("已截图 {}".format(os.path.basename(path)))

        # 再抓一次完整的滚动内容，否则看不到折叠在下面的部分
        scroll = win.stack.widget(index)
        inner = scroll.widget() if hasattr(scroll, "widget") else None
        if inner is not None:
            inner.adjustSize()
            pump(150)
            full_path = os.path.join(SHOT_DIR, "{}-{}-整页.png".format(index, name))
            inner.grab().save(full_path)
            lines.append("已截图 {}".format(os.path.basename(full_path)))

    full = os.path.join(SHOT_DIR, "full.png")
    win.grab().save(full)
    lines.append("已截图 full.png")

    # ---- 交互后再检查一次：自绘控件被点击后绝不能改变自身位置 ----
    # 曾踩过的坑：Segmented 的动画属性与 QWidget.pos 重名，点一下就 move 到 (1,0)。
    lines.append("")
    lines.append("[点击后布局检查]")
    general_index = win.stack.count() - 1
    win.switch_page(general_index)
    pump(400)
    for widget, label in (
        (win.seg_after, "自动关机分段控件"),
        (win.seg_theme, "主题分段控件"),
    ):
        before = widget.pos()
        index = widget.currentIndex()
        target = 0 if index == 1 else 1
        QTest.mouseClick(
            widget,
            Qt.MouseButton.LeftButton,
            pos=QPoint(int(widget.width() * (0.75 if target else 0.25)), widget.height() // 2),
        )
        pump(420)
        after = widget.pos()
        moved = after != before
        if moved:
            problems += 1
        lines.append(
            "{}{} 点击后 index {}->{} 位置 ({},{}) -> ({},{}) {}".format(
                "BAD  " if moved else "OK   ",
                label,
                index,
                widget.currentIndex(),
                before.x(),
                before.y(),
                after.x(),
                after.y(),
                "被移动了！" if moved else "未移动",
            )
        )
        widget.setCurrentIndex(index)
        pump(300)

    clicked = os.path.join(SHOT_DIR, "通用-点击后.png")
    win.grab().save(clicked)
    lines.append("已截图 {}".format(os.path.basename(clicked)))

    lines.append("")
    lines.append("问题数: {}".format(problems))

    win.close()

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
