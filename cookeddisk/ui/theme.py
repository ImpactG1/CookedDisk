"""Windows 11 inspired QSS theme system supporting system light/dark detection."""

from __future__ import annotations

import sys
import winreg
from typing import Literal


def is_windows_dark_mode() -> bool:
    """Detect if Windows 10/11 is currently using Dark Mode."""
    if sys.platform != "win32":
        return True
    try:
        registry_key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(registry_key, "AppsUseLightTheme")
        winreg.CloseKey(registry_key)
        return value == 0
    except Exception:
        return True


def get_stylesheet(mode: Literal["system", "dark", "light"] = "system") -> str:
    """Returns the custom Windows 11 QSS stylesheet for the requested theme."""
    is_dark = is_windows_dark_mode() if mode == "system" else (mode == "dark")

    if is_dark:
        bg_window = "#1E1E1E"
        bg_surface = "#252526"
        bg_surface_alt = "#2D2D2D"
        bg_surface_hover = "#333333"
        bg_input = "#1F1F1F"
        border_subtle = "#383838"
        border_input = "#454545"
        text_primary = "#F1F1F1"
        text_secondary = "#A0A0A0"
        text_muted = "#707070"
        accent_blue = "#2563EB"
        accent_hover = "#1D4ED8"
        accent_border = "#3B82F6"
        table_header_bg = "#222222"
        table_row_alt = "#282828"
        table_row_hover = "#2F2F2F"
        badge_green_bg = "#152E1E"
        badge_green_border = "#255734"
        badge_green_text = "#68D391"
        badge_amber_bg = "#332612"
        badge_amber_border = "#593F16"
        badge_amber_text = "#F6AD55"
        badge_red_bg = "#351919"
        badge_red_border = "#5D2828"
        badge_red_text = "#FC8181"
        progress_bg = "#333333"
    else:
        bg_window = "#F4F4F5"
        bg_surface = "#FFFFFF"
        bg_surface_alt = "#F9FAFB"
        bg_surface_hover = "#F3F4F6"
        bg_input = "#FFFFFF"
        border_subtle = "#E4E4E7"
        border_input = "#D4D4D8"
        text_primary = "#18181B"
        text_secondary = "#52525B"
        text_muted = "#A1A1AA"
        accent_blue = "#0066CC"
        accent_hover = "#0052A3"
        accent_border = "#2563EB"
        table_header_bg = "#F4F4F5"
        table_row_alt = "#FAFAFA"
        table_row_hover = "#F4F4F6"
        badge_green_bg = "#E6F4EA"
        badge_green_border = "#B7E1CD"
        badge_green_text = "#137333"
        badge_amber_bg = "#FEF7E0"
        badge_amber_border = "#FCE8B2"
        badge_amber_text = "#B06000"
        badge_red_bg = "#FCE8E6"
        badge_red_border = "#FAD2CF"
        badge_red_text = "#C5221F"
        progress_bg = "#E4E4E7"

    return f"""
    /* CookedDisk Native Windows 11 Styling */
    * {{
        font-family: "Segoe UI Variable", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
        font-size: 13px;
        color: {text_primary};
        outline: none;
    }}

    QMainWindow, QDialog {{
        background-color: {bg_window};
    }}

    QWidget {{
        background-color: transparent;
    }}

    /* Left Navigation Sidebar */
    #navSidebar {{
        background-color: {bg_surface};
        border-right: 1px solid {border_subtle};
        min-width: 200px;
        max-width: 220px;
    }}

    #navLogoLabel {{
        font-size: 15px;
        font-weight: 600;
        color: {text_primary};
        padding: 16px 12px 12px 16px;
    }}

    #navSubLabel {{
        font-size: 11px;
        color: {text_muted};
        padding: 0px 12px 16px 16px;
    }}

    QPushButton.navButton {{
        text-align: left;
        padding: 8px 12px;
        margin: 2px 8px;
        border-radius: 6px;
        border: none;
        background-color: transparent;
        color: {text_secondary};
        font-size: 13px;
        font-weight: 500;
    }}

    QPushButton.navButton:hover {{
        background-color: {bg_surface_hover};
        color: {text_primary};
    }}

    QPushButton.navButton:checked, QPushButton.navButton.active {{
        background-color: {bg_surface_hover};
        color: {accent_blue};
        font-weight: 600;
        border-left: 3px solid {accent_blue};
    }}

    /* Main Content Area */
    #contentContainer {{
        background-color: {bg_window};
        padding: 16px;
    }}

    /* Cards */
    QFrame.card {{
        background-color: {bg_surface};
        border: 1px solid {border_subtle};
        border-radius: 6px;
        padding: 16px;
    }}

    QFrame.metricCard {{
        background-color: {bg_surface};
        border: 1px solid {border_subtle};
        border-radius: 6px;
        padding: 12px 14px;
    }}

    QLabel.metricTitle {{
        font-size: 12px;
        color: {text_secondary};
        font-weight: 500;
    }}

    QLabel.metricValue {{
        font-size: 20px;
        font-weight: 600;
        color: {text_primary};
        padding-top: 4px;
        font-feature-settings: "tnum" 1;
    }}

    QLabel.metricSub {{
        font-size: 11px;
        color: {text_muted};
        padding-top: 2px;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {bg_surface};
        border: 1px solid {border_input};
        border-radius: 6px;
        padding: 6px 14px;
        font-size: 13px;
        font-weight: 500;
        color: {text_primary};
    }}

    QPushButton:hover {{
        background-color: {bg_surface_hover};
    }}

    QPushButton:pressed {{
        background-color: {border_subtle};
    }}

    QPushButton.primaryButton {{
        background-color: {accent_blue};
        border: 1px solid {accent_border};
        color: #FFFFFF;
        font-weight: 600;
    }}

    QPushButton.primaryButton:hover {{
        background-color: {accent_hover};
    }}

    QPushButton:disabled {{
        opacity: 0.5;
        color: {text_muted};
        background-color: {bg_surface};
        border-color: {border_subtle};
    }}

    /* Form Inputs */
    QLineEdit, QSpinBox, QComboBox {{
        background-color: {bg_input};
        border: 1px solid {border_input};
        border-radius: 6px;
        padding: 6px 10px;
        color: {text_primary};
        selection-background-color: {accent_blue};
    }}

    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
        border: 1px solid {accent_blue};
    }}

    QComboBox::drop-down {{
        border: none;
        padding-right: 8px;
    }}

    /* Tables */
    QTableWidget, QTableView {{
        background-color: {bg_surface};
        border: 1px solid {border_subtle};
        border-radius: 6px;
        gridline-color: {border_subtle};
        selection-background-color: {bg_surface_hover};
        selection-color: {text_primary};
        font-feature-settings: "tnum" 1;
    }}

    QHeaderView::section {{
        background-color: {table_header_bg};
        border: none;
        border-bottom: 1px solid {border_subtle};
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 600;
        color: {text_secondary};
    }}

    QTableWidget::item {{
        padding: 6px 10px;
        border-bottom: 1px solid {border_subtle};
    }}

    /* Progress Bar */
    QProgressBar {{
        background-color: {progress_bg};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: right;
    }}

    QProgressBar::chunk {{
        background-color: {accent_blue};
        border-radius: 3px;
    }}

    /* Status Badges */
    QLabel.badgePass {{
        background-color: {badge_green_bg};
        border: 1px solid {badge_green_border};
        color: {badge_green_text};
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
    }}

    QLabel.badgeWarn {{
        background-color: {badge_amber_bg};
        border: 1px solid {badge_amber_border};
        color: {badge_amber_text};
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
    }}

    QLabel.badgeFail {{
        background-color: {badge_red_bg};
        border: 1px solid {badge_red_border};
        color: {badge_red_text};
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
    }}

    /* Scrollbars */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0px;
    }}

    QScrollBar::handle:vertical {{
        background: {border_input};
        min-height: 24px;
        border-radius: 4px;
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QPlainTextEdit, QTextBrowser {{
        background-color: {bg_input};
        border: 1px solid {border_input};
        border-radius: 6px;
        padding: 8px;
        color: {text_primary};
        font-family: "Consolas", "Cascadia Code", monospace;
        font-size: 12px;
    }}
    """

