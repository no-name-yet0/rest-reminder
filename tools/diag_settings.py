"""诊断：打印设置窗口「通用」页里实际存在的控件。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from rreminder import config as cfgmod  # noqa: E402
from rreminder import theme as thememod  # noqa: E402
from rreminder.ui.settings_win import SettingsWindow  # noqa: E402
from rreminder.ui.widgets import Segmented  # noqa: E402

OUT = os.path.join(ROOT, "_diag_settings.txt")

app = QApplication(sys.argv)
cfg = cfgmod.load()

lines = []
lines.append("after_rest 配置: {}".format(cfg.get("after_rest")))
lines.append("winddown 配置: {}".format(cfg.get("winddown")))
lines.append("")

win = SettingsWindow(cfg, thememod.palette("dark"), False)
lines.append("页面数: {}".format(win.stack.count()))

for index in range(win.stack.count()):
    page = win.stack.widget(index)
    name = win._nav_buttons[index].text()
    lines.append("")
    lines.append("=== 第 {} 页: {} ===".format(index, name))
    for seg in page.findChildren(Segmented):
        lines.append("  [分段控件] items={} current={}".format(seg.items, seg.currentIndex()))
    for btn in page.findChildren(QPushButton):
        if btn.text().strip():
            lines.append("  [按钮] {}".format(btn.text()))
    for label in page.findChildren(QLabel):
        text = label.text().strip()
        if text:
            lines.append("  [文字] {}".format(text.replace("\n", " / ")[:70]))

# 直接测一下切换是否生效
lines.append("")
lines.append("=== 切换测试 ===")
try:
    seg = win.seg_after
    lines.append("seg_after 存在: items={} current={}".format(seg.items, seg.currentIndex()))
    seg.setCurrentIndex(1, emit=True)
    lines.append("切到「自动关机」后 cfg mode = {}".format(cfg["after_rest"]["mode"]))
    seg.setCurrentIndex(0, emit=True)
    lines.append("切回「只记录」后 cfg mode = {}".format(cfg["after_rest"]["mode"]))
except Exception as exc:  # noqa: BLE001
    lines.append("切换失败: {!r}".format(exc))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
