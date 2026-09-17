"""主题调色板与全局样式表。"""

from __future__ import annotations

from typing import Dict

RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14
RADIUS_XL = 20

DARK: Dict[str, str] = {
    "mode": "dark",
    "bg": "#0F1117",
    "bg_alt": "#14171F",
    "surface": "#1A1E28",
    "surface_hover": "#222735",
    "border": "#2A2F3E",
    "border_strong": "#3A4055",
    "text": "#E9EBF2",
    "text_dim": "#99A1B5",
    "text_faint": "#6A7285",
    "accent": "#6C8CFF",
    "accent_hover": "#7E9BFF",
    "accent_pressed": "#5B7AF0",
    "accent_soft": "#232B45",
    "on_accent": "#FFFFFF",
    "danger": "#FF6B6B",
    "warn": "#FFB454",
    "ok": "#4ECB8E",
    "dim": "rgba(6, 8, 14, 0.82)",
    "dim_solid": "#080A10",
}

LIGHT: Dict[str, str] = {
    "mode": "light",
    "bg": "#F4F5F9",
    "bg_alt": "#EBEDF4",
    "surface": "#FFFFFF",
    "surface_hover": "#F6F7FB",
    "border": "#E2E5EE",
    "border_strong": "#C9CEDD",
    "text": "#1A1D26",
    "text_dim": "#5B6273",
    "text_faint": "#8B92A4",
    "accent": "#4A6CF7",
    "accent_hover": "#3D5CE0",
    "accent_pressed": "#3450C9",
    "accent_soft": "#E9EEFE",
    "on_accent": "#FFFFFF",
    "danger": "#E5484D",
    "warn": "#C2710C",
    "ok": "#16A34A",
    "dim": "rgba(16, 20, 30, 0.58)",
    "dim_solid": "#10141E",
}


def palette(name: str) -> Dict[str, str]:
    return LIGHT if name == "light" else DARK


def build_qss(p: Dict[str, str]) -> str:
    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    outline: none;
}}

QWidget {{
    color: {p['text']};
    font-size: 13px;
}}

#Root {{
    background: {p['bg']};
    border: 1px solid {p['border']};
    border-radius: {RADIUS_LG}px;
}}

#TitleBar {{
    background: transparent;
}}

#AppTitle {{ font-size: 13px; font-weight: 500; }}

#WinBtn {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    color: {p['text_dim']};
    font-size: 13px;
    padding: 0px;
}}
#WinBtn:hover {{ background: {p['surface_hover']}; color: {p['text']}; }}
#WinBtnClose:hover {{ background: {p['danger']}; color: #FFFFFF; }}

#Sidebar {{
    background: {p['bg_alt']};
    border-right: 1px solid {p['border']};
}}

#NavItem {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_MD}px;
    color: {p['text_dim']};
    font-size: 13px;
    padding: 9px 12px;
    text-align: left;
}}
#NavItem:hover {{ background: {p['surface_hover']}; color: {p['text']}; }}
#NavItem:checked {{
    background: {p['surface']};
    border: 1px solid {p['border']};
    color: {p['text']};
    font-weight: 500;
}}

#Card {{
    background: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: {RADIUS_LG}px;
}}

#PageTitle {{ font-size: 15px; font-weight: 500; }}
#PageHint {{ color: {p['text_dim']}; font-size: 12px; }}
#CardTitle {{ font-size: 13px; font-weight: 500; }}
#RowTitle {{ font-size: 13px; }}
#RowHint {{ color: {p['text_dim']}; font-size: 12px; }}
#Tiny {{ color: {p['text_faint']}; font-size: 12px; }}

#Sep {{ background: {p['border']}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton {{
    background: {p['surface']};
    border: 1px solid {p['border_strong']};
    border-radius: {RADIUS_MD}px;
    color: {p['text']};
    padding: 8px 16px;
    font-size: 13px;
}}
QPushButton:hover {{ background: {p['surface_hover']}; border-color: {p['accent']}; }}
QPushButton:pressed {{ background: {p['accent_soft']}; }}
QPushButton:disabled {{ color: {p['text_faint']}; border-color: {p['border']}; }}

QPushButton#Primary {{
    background: {p['accent']};
    border: 1px solid {p['accent']};
    color: {p['on_accent']};
    font-weight: 500;
}}
QPushButton#Primary:hover {{ background: {p['accent_hover']}; border-color: {p['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: {p['accent_pressed']}; }}

QPushButton#Danger {{
    background: transparent;
    border: 1px solid {p['border_strong']};
    color: {p['danger']};
}}
QPushButton#Danger:hover {{ background: {p['danger']}; color: #FFFFFF; border-color: {p['danger']}; }}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {p['border_strong']};
    color: {p['text_dim']};
}}
QPushButton#Ghost:hover {{ color: {p['text']}; border-color: {p['accent']}; }}

QPushButton#Chip {{
    background: transparent;
    border: 1px solid {p['border']};
    border-radius: 999px;
    color: {p['text_dim']};
    padding: 5px 12px;
    font-size: 12px;
}}
QPushButton#Chip:checked {{
    background: {p['accent_soft']};
    border-color: {p['accent']};
    color: {p['accent']};
}}
QPushButton#Chip:hover {{ color: {p['text']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{
    background: {p['bg_alt']};
    border: 1px solid {p['border']};
    border-radius: {RADIUS_MD}px;
    padding: 7px 10px;
    color: {p['text']};
    selection-background-color: {p['accent']};
    selection-color: {p['on_accent']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QComboBox:focus {{
    border-color: {p['accent']};
}}
QLineEdit:disabled, QSpinBox:disabled, QTimeEdit:disabled, QComboBox:disabled {{
    color: {p['text_faint']};
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {{
    width: 16px; border: none; background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow {{
    image: none; width: 0px; height: 0px;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {p['text_dim']};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow {{
    image: none; width: 0px; height: 0px;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['text_dim']};
}}
QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover, QTimeEdit::up-arrow:hover {{
    border-bottom-color: {p['accent']};
}}
QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover, QTimeEdit::down-arrow:hover {{
    border-top-color: {p['accent']};
}}

QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p['surface']};
    border: 1px solid {p['border_strong']};
    border-radius: {RADIUS_MD}px;
    padding: 4px;
    selection-background-color: {p['accent_soft']};
    selection-color: {p['text']};
    outline: none;
}}

QCheckBox {{ spacing: 8px; color: {p['text']}; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {p['border_strong']};
    border-radius: 5px;
    background: {p['bg_alt']};
}}
QCheckBox::indicator:hover {{ border-color: {p['accent']}; }}
QCheckBox::indicator:checked {{
    background: {p['accent']};
    border-color: {p['accent']};
}}

QSlider::groove:horizontal {{
    height: 4px; background: {p['border']}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 15px; height: 15px; margin: -6px 0;
    border-radius: 8px; background: {p['accent']};
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hover']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p['border_strong']}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['text_faint']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QMenu {{
    background: {p['surface']};
    border: 1px solid {p['border_strong']};
    border-radius: {RADIUS_MD}px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 16px 7px 12px;
    border-radius: {RADIUS_SM}px;
    color: {p['text']};
    font-size: 13px;
}}
QMenu::item:selected {{ background: {p['accent_soft']}; color: {p['accent']}; }}
QMenu::item:disabled {{ color: {p['text_faint']}; }}
QMenu::separator {{
    height: 1px; background: {p['border']}; margin: 5px 8px;
}}

QToolTip {{
    background: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border_strong']};
    border-radius: {RADIUS_SM}px;
    padding: 5px 8px;
}}

QDialog {{ background: {p['bg']}; }}
"""
