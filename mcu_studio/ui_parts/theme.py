"""Application color themes and Qt stylesheets (no Qt import needed)."""


DARK_STYLE = """
    QWidget { background-color: #1c1c1e; color: #f2f2f7; font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; font-size: 13px; }
    QDialog, QMainWindow { background-color: #1c1c1e; }
    QLabel { background-color: transparent; }
    QLabel#appTitle { color: #f3f3f3; font-size: 22px; font-weight: 700; letter-spacing: 1px; }
    QLabel#appSubtitle { color: #8b949e; font-size: 12px; }
    QLabel#sectionTitle { color: #e5e5ea; font-size: 12px; font-weight: 700; padding: 8px 0 4px 8px; border-left: 3px solid #0a84ff; }
    QLabel#fieldCaption { color: #9da5b4; font-size: 12px; font-weight: 700; }
    QLabel#fieldHint { color: #ffb340; font-size: 12px; padding-top: 2px; }
    QLabel#toolbarSeparator { color: #3c3c3c; padding: 0 2px; }
    QFrame#topBar { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 11px; }
    QWidget#plotToolbar { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 9px; }
    QLabel#plotTitle { color: #d1d1d6; font-size: 13px; font-weight: 700; }
    QFrame#heroPanel { background-color: #1f1f1f; border: 1px solid #2d2d30; border-radius: 6px; }
    QFrame#statusPanel { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 10px; }
    QFrame#sliderControlPanel { background-color: #202225; border: 1px solid #3a3a3c; border-radius: 10px; }
    QLabel#sliderControlTitle { color: #f2f2f7; font-size: 13px; font-weight: 700; }
    QLineEdit, QComboBox, QTextEdit { min-height: 30px; background-color: #2c2c2e; border: 1px solid #48484a; padding: 4px 10px; color: #f2f2f7; border-radius: 8px; selection-background-color: #0a84ff; font-size: 13px; }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #0a84ff; background-color: #323234; }
    QComboBox::drop-down { border: 0; width: 24px; }
    QComboBox QAbstractItemView { background-color: #2c2c2e; color: #f2f2f7; border: 1px solid #48484a; selection-background-color: #0a84ff; outline: 0; }
    QCheckBox, QRadioButton { spacing: 7px; color: #d4d4d4; font-size: 13px; }
    QRadioButton:checked { color: #64b5ff; font-weight: 700; }
    QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
    QRadioButton::indicator { border: 1px solid #8e8e93; border-radius: 8px; background-color: #1c1c1e; }
    QRadioButton::indicator:checked { border: 4px solid #0a84ff; background-color: #f2f2f7; }
    QPushButton { min-height: 30px; background-color: #2c2c2e; border: 1px solid #48484a; padding: 4px 14px; color: #f2f2f7; border-radius: 8px; font-size: 13px; font-weight: 600; }
    QPushButton:hover { background-color: #3a3a3c; border-color: #636366; }
    QPushButton:pressed { background-color: #48484a; border-color: #0a84ff; }
    QPushButton:disabled { background-color: #1d1d1d; color: #626262; border-color: #2a2a2a; }
    QPushButton#primaryButton { background-color: #0e639c; border-color: #0e639c; color: #ffffff; font-weight: 700; }
    QPushButton#primaryButton:hover { background-color: #1177bb; }
    QPushButton#profileAddButton { min-height: 30px; padding: 0; color: #64b5ff; font-size: 20px; font-weight: 500; }
    QPushButton#profileDeleteButton { min-height: 30px; padding: 0; color: #ff6961; font-size: 19px; font-weight: 500; }
    QPushButton#backConfigButton { background-color: #252a32; border: 1px solid #465264; color: #d8e8f8; border-radius: 9px; padding: 0px; }
    QPushButton#backConfigButton:hover { background-color: #2e4058; border-color: #5b8fc4; color: #ffffff; }
    QPushButton#backConfigButton:pressed { background-color: #20344c; }
    QPushButton#warningButton { background-color: #ff9f0a; border-color: #ff9f0a; color: #1c1c1e; }
    QPushButton#warningButton:hover { background-color: #ffb340; }
    QPushButton#dangerButton { background-color: #ff453a; border-color: #ff453a; color: #ffffff; }
    QPushButton#dangerButton:hover { background-color: #ff6961; border-color: #ff6961; }
    QFrame#actionStrip { background-color: transparent; border: 0; }
    QPushButton[commandButton="true"] { background-color: #2c2c2e; color: #d1d1d6; border-color: #48484a; }
    QPushButton[commandButton="true"]:hover { background-color: #3a3a3c; color: #ffffff; border-color: #636366; }
    QPushButton[commandRole="reset"]:hover { color: #ff6961; border-color: #ff6961; }
    QPushButton[plotTool="true"] { min-height: 28px; padding: 3px 14px; background-color: #242426; color: #d1d1d6; border-color: #3a3a3c; }
    QPushButton[plotTool="true"]:hover { background-color: #323234; color: #ffffff; border-color: #545458; }
    QPushButton[plotTool="true"]:checked { background-color: #094771; color: #ffffff; border-color: #007acc; }
    QPushButton[viewSegment="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[viewSegment="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[viewSegment="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[segmentPosition="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QLineEdit:disabled, QComboBox:disabled { background-color: #1b1b1b; color: #666666; border-color: #292929; }
    QTreeWidget { background-color: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 10px; alternate-background-color: #202022; outline: 0; }
    QTreeWidget::item { height: 30px; border-bottom: 1px solid #242424; }
    QTreeWidget::item:hover { background-color: #2c2c2e; }
    QTreeWidget::item:selected { background-color: #094771; color: #ffffff; }
    QHeaderView::section { background-color: #242426; color: #d1d1d6; padding: 8px; border: 0; border-right: 1px solid #3a3a3c; border-bottom: 1px solid #3a3a3c; font-size: 13px; font-weight: 700; }
    QSplitter::handle { background-color: #2d2d30; }
    QTreeWidget QLineEdit { background-color: #252526 !important; border: 1px solid #007acc !important; color: #ffffff !important; font-family: "Consolas" !important; font-size: 12px !important; font-weight: bold !important; padding: 1px 4px; }
    QTabWidget::pane { border: 1px solid #3a3a3c; background-color: #1c1c1e; border-radius: 10px; top: -1px; }
    QTabBar::tab { background-color: #2c2c2e; color: #aeaeb2; border: 1px solid #3a3a3c; padding: 7px 16px; border-radius: 8px; font-weight: 600; margin: 2px; }
    QTabBar::tab:hover { background-color: #3a3a3c; color: #f2f2f7; }
    QTabBar::tab:selected { background-color: #3a3a3c; color: #64b5ff; border-color: #545458; }
    QScrollBar:vertical { background: #181818; width: 12px; margin: 0; }
    QScrollBar::handle:vertical { background: #424242; min-height: 28px; border-radius: 5px; margin: 2px; }
    QScrollBar::handle:vertical:hover { background: #5a5a5a; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QToolTip { background-color: #252526; color: #f0f0f0; border: 1px solid #5a5a5a; padding: 5px; }
"""

LIGHT_STYLE = """
    QWidget { background-color: #f2f2f7; color: #1c1c1e; font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; font-size: 13px; }
    QDialog, QMainWindow { background-color: #f2f2f7; }
    QLabel { background-color: transparent; }
    QLabel#appTitle { color: #172033; font-size: 22px; font-weight: 700; letter-spacing: 1px; }
    QLabel#appSubtitle { color: #64748b; font-size: 12px; }
    QLabel#sectionTitle { color: #3a3a3c; font-size: 12px; font-weight: 700; padding: 8px 0 4px 8px; border-left: 3px solid #007aff; }
    QLabel#fieldCaption { color: #007aff; font-size: 13px; font-weight: 700; }
    QLabel#fieldHint { color: #b45309; font-size: 12px; padding-top: 2px; }
    QLabel#toolbarSeparator { color: #cbd5e1; padding: 0 2px; }
    QFrame#topBar { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 11px; }
    QWidget#plotToolbar { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 9px; }
    QLabel#plotTitle { color: #3a3a3c; font-size: 13px; font-weight: 700; }
    QFrame#heroPanel { background-color: #f8fafc; border: 1px solid #dbe3ec; border-radius: 6px; }
    QFrame#statusPanel { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 10px; }
    QFrame#sliderControlPanel { background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 10px; }
    QLabel#sliderControlTitle { color: #172033; font-size: 13px; font-weight: 700; }
    QLineEdit, QComboBox, QTextEdit { min-height: 30px; background-color: #ffffff; border: 1px solid #c7c7cc; padding: 4px 10px; color: #1c1c1e; border-radius: 8px; font-size: 13px; }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #1976d2; background-color: #ffffff; }
    QComboBox::drop-down { border: 0; width: 26px; }
    QComboBox QAbstractItemView { background-color: #ffffff; color: #102a43; border: 1px solid #bdcad8; selection-background-color: #d9effc; selection-color: #075985; outline: 0; }
    QCheckBox, QRadioButton { spacing: 7px; color: #19324d; font-size: 13px; }
    QRadioButton:checked { color: #087fbd; font-weight: 700; }
    QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
    QRadioButton::indicator { border: 1px solid #7d8ea3; border-radius: 8px; background-color: #ffffff; }
    QRadioButton::indicator:checked { border: 4px solid #0b86c9; background-color: #ffffff; }
    QPushButton { min-height: 30px; background-color: #ffffff; border: 1px solid #c7c7cc; padding: 4px 14px; color: #1c1c1e; border-radius: 8px; font-size: 13px; font-weight: 600; }
    QPushButton:hover { background-color: #eef6ff; border: 1px solid #60a5fa; }
    QPushButton:pressed { background-color: #dbeafe; }
    QPushButton:disabled { background-color: #f1f5f9; color: #94a3b8; border: 1px solid #e2e8f0; }
    QPushButton#primaryButton { background-color: #007aff; border-color: #007aff; color: #ffffff; font-weight: 700; }
    QPushButton#primaryButton:hover { background-color: #2188ff; }
    QPushButton#profileAddButton { min-height: 30px; padding: 0; color: #007aff; font-size: 20px; font-weight: 500; }
    QPushButton#profileDeleteButton { min-height: 30px; padding: 0; color: #d92d20; font-size: 19px; font-weight: 500; }
    QPushButton#backConfigButton { background-color: #f7faff; border: 1px solid #b9cbe0; color: #24557f; border-radius: 9px; padding: 0px; }
    QPushButton#backConfigButton:hover { background-color: #e8f2ff; border-color: #7aaee0; color: #123d63; }
    QPushButton#backConfigButton:pressed { background-color: #dcecff; }
    QPushButton#warningButton { background-color: #ff9500; border-color: #ff9500; color: #ffffff; }
    QPushButton#dangerButton { background-color: #ff3b30; border-color: #ff3b30; color: #ffffff; }
    QFrame#actionStrip { background-color: transparent; border: 0; }
    QPushButton[commandButton="true"] { background-color: #ffffff; color: #3a3a3c; border-color: #c7c7cc; }
    QPushButton[commandButton="true"]:hover { background-color: #f2f2f7; color: #1c1c1e; border-color: #8e8e93; }
    QPushButton[commandRole="reset"]:hover { color: #ff3b30; border-color: #ff3b30; }
    QPushButton[plotTool="true"] { min-height: 28px; padding: 3px 14px; background-color: #ffffff; color: #3a3a3c; border-color: #d1d1d6; }
    QPushButton[plotTool="true"]:hover { background-color: #f2f2f7; color: #1c1c1e; border-color: #8e8e93; }
    QPushButton[plotTool="true"]:checked { background-color: #007aff; color: #ffffff; border-color: #007aff; }
    QPushButton[viewSegment="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[viewSegment="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[viewSegment="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[segmentPosition="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QLineEdit:disabled, QComboBox:disabled { background-color: #f1f5f9; color: #94a3b8; border: 1px solid #e2e8f0; }
    QTreeWidget { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 10px; alternate-background-color: #f8f8fa; outline: 0; }
    QTreeWidget::item { height: 30px; border-bottom: 1px solid #e7edf4; }
    QTreeWidget::item:hover { background-color: #eff6ff; }
    QTreeWidget::item:selected { background-color: #dbeafe; color: #0f3f73; }
    QHeaderView::section { background-color: #eaf0f6; color: #36516d; padding: 8px; border: 1px solid #d2dde8; font-size: 13px; font-weight: 700; }
    QSplitter::handle { background-color: #dbe3ec; }
    QTreeWidget QLineEdit { background-color: #ffffff !important; border: 2px solid #1976d2 !important; color: #111827 !important; font-family: "Consolas" !important; font-size: 12px !important; font-weight: bold !important; padding: 1px 3px; }
    QTabWidget::pane { border: 1px solid #d1d1d6; background-color: #ffffff; border-radius: 10px; top: -1px; }
    QTabBar::tab { background-color: #e5e5ea; color: #636366; border: 1px solid #d1d1d6; padding: 7px 16px; border-radius: 8px; font-weight: 600; margin: 2px; }
    QTabBar::tab:hover { background-color: #dcdce1; color: #1c1c1e; }
    QTabBar::tab:selected { background-color: #ffffff; color: #007aff; border-color: #b8b8bd; }
    QToolTip { background-color: #ffffff; color: #111827; border: 1px solid #94a3b8; padding: 4px; }
"""

POPUP_MENU_STYLE = """
    QMenu { background-color: #ffffff; color: #1f2937; border: 1px solid #c7d1dc; border-radius: 11px; padding: 7px; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
    QMenu::item { padding: 8px 30px 8px 13px; border-radius: 7px; margin: 1px 0px; background-color: transparent; color: #1f2937; }
    QMenu::item:selected { background-color: #dff2fa; color: #066cae; }
    QMenu::item:disabled { color: #9aa5b1; }
    QMenu::separator { height: 1px; background-color: #d8e0e8; margin: 6px 9px; }
"""

DARK_POPUP_MENU_STYLE = """
    QMenu { background-color: #20242b; color: #e6e9ee; border: 1px solid #3d4858; border-radius: 11px; padding: 7px; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
    QMenu::item { padding: 8px 30px 8px 13px; border-radius: 7px; margin: 1px 0px; background-color: transparent; }
    QMenu::item:selected { background-color: #293b54; color: #ffffff; }
    QMenu::item:disabled { color: #6f7b89; }
    QMenu::separator { height: 1px; background-color: #3d4858; margin: 6px 9px; }
"""


def application_theme_stylesheet(theme_mode):
    return LIGHT_STYLE if str(theme_mode).upper() == "LIGHT" else DARK_STYLE
