"""The look of the Windows app (Theme.cs): black and dark purple, serif headings, monospace labels, bevelled purple
buttons, and cyan meaning "it worked". Windows' Verdana / Times New Roman / Courier New are rarely installed on
Linux, so each face falls back to the closest common family (DejaVu, Liberation, then the desktop's own)."""
from PySide6.QtGui import QColor, QFont, QFontDatabase


def C(rgb):
    return QColor((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)


BG, PANEL, PANEL2, LINE = "#000000", "#0b0613", "#150a24", "#3b1f5f"
TEXT, MUTED, PURPLE, PURPLE2 = "#e6ddf3", "#a898c2", "#b98aff", "#7c3fd0"
BTN, BTN_EDGE, CYAN, ERR, AMBER = "#4b1f86", "#a77ae6", "#58e6ea", "#ff7092", "#ffcf70"

SANS = ["Verdana", "DejaVu Sans", "Noto Sans", "Liberation Sans", "Cantarell", "sans-serif"]
SERIF = ["Times New Roman", "Liberation Serif", "DejaVu Serif", "Noto Serif", "serif"]
MONO = ["Courier New", "Liberation Mono", "DejaVu Sans Mono", "Noto Sans Mono", "monospace"]


def font(families, points, bold=False):
    available = set(QFontDatabase.families())
    pick = [f for f in families if f in available] or families[-1:]
    f = QFont()
    f.setFamilies(pick + [x for x in families if x not in pick])
    f.setPointSizeF(points)
    f.setBold(bold)
    return f


def body():
    return font(SANS, 9.75)


def title():
    return font(SERIF, 20, True)


def brand():
    return font(SERIF, 22, True)


def mono(points=10, bold=False):
    return font(MONO, points, bold)


STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; }}
QWidget#head, QWidget#foot {{ background: {PANEL}; }}
QWidget#nav {{ background: {PANEL2}; }}
QLabel {{ background: transparent; }}
QPushButton {{ background: {BTN}; color: white; border: 2px solid {BTN_EDGE}; padding: 4px 10px; }}
QPushButton:hover {{ background: {PURPLE2}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:disabled {{ background: {PANEL2}; color: {MUTED}; border-color: {LINE}; }}
QPushButton[secondary="true"] {{ background: {PANEL2}; border-color: {LINE}; }}
QPushButton[secondary="true"]:hover {{ background: {PURPLE2}; }}
QPlainTextEdit#log {{ background: black; color: {CYAN}; border: 1px solid {LINE}; }}
QTreeWidget {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; }}
QHeaderView::section {{ background: {PANEL2}; color: {MUTED}; border: 0; }}
QComboBox {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; padding: 3px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {TEXT}; selection-background-color: {BTN}; }}
QProgressBar {{ background: {PANEL}; border: 1px solid {LINE}; text-align: center; color: {TEXT}; }}
QProgressBar::chunk {{ background: {PURPLE2}; }}
QTextBrowser {{ background: {BG}; border: 1px solid {LINE}; }}
QLineEdit {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; padding: 3px; }}
QMessageBox, QDialog {{ background: {PANEL}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {LINE}; min-height: 20px; }}
"""
