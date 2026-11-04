"""Left navigation sidebar with outline icons and active state indication."""

from __future__ import annotations

from pathlib import Path
from typing import List
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.ui.icons import get_icon


class NavBar(QFrame):
    page_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("navSidebar")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 16)
        self.layout.setSpacing(4)

        # Header Title with Logo
        header_hlayout = QHBoxLayout()
        header_hlayout.setSpacing(10)
        header_hlayout.setContentsMargins(16, 16, 16, 12)

        # Find logo.png
        logo_candidates = [
            Path(__file__).resolve().parent.parent.parent / "resources" / "logo.png",
            Path(__file__).resolve().parent.parent.parent / "logo.png",
            Path.cwd() / "resources" / "logo.png",
            Path.cwd() / "logo.png",
        ]
        for candidate in logo_candidates:
            if candidate.is_file():
                img_lbl = QLabel()
                pix = QPixmap(str(candidate)).scaled(
                    30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                img_lbl.setPixmap(pix)
                header_hlayout.addWidget(img_lbl)
                break

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        self.logo_label = QLabel("CookedDisk")
        self.logo_label.setObjectName("navLogoLabel")
        self.logo_label.setStyleSheet("font-size: 15px; font-weight: 600; padding: 0;")
        title_box.addWidget(self.logo_label)

        self.sub_label = QLabel("OBS Drive Companion")
        self.sub_label.setObjectName("navSubLabel")
        self.sub_label.setStyleSheet("font-size: 11px; color: #888888; padding: 0;")
        title_box.addWidget(self.sub_label)

        header_hlayout.addLayout(title_box)
        header_hlayout.addStretch()

        self.layout.addLayout(header_hlayout)

        # Nav Buttons
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self.nav_items: List[tuple[str, str, int]] = [
            ("Dashboard", "dashboard", 0),
            ("Sessions", "sessions", 1),
            ("Setup check", "setup", 2),
            ("Settings", "settings", 3),
            ("Logs", "logs", 4),
        ]

        self.buttons: List[QPushButton] = []
        for text, icon_name, idx in self.nav_items:
            btn = QPushButton(text)
            btn.setProperty("class", "navButton")
            btn.setCheckable(True)
            btn.setIcon(get_icon(icon_name, "#999999", 18))
            self.btn_group.addButton(btn, idx)
            self.layout.addWidget(btn)
            self.buttons.append(btn)

        self.btn_group.idClicked.connect(self.page_changed.emit)

        # Spacer
        self.layout.addStretch()

        # Google Account status footer
        self.account_frame = QFrame()
        self.account_frame.setStyleSheet(
            "border-top: 1px solid #333333; margin: 0 8px; padding-top: 10px;"
        )
        acc_layout = QVBoxLayout(self.account_frame)
        acc_layout.setContentsMargins(8, 8, 8, 0)
        acc_layout.setSpacing(2)

        self.acc_title = QLabel("Google Account")
        self.acc_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #888888;")
        acc_layout.addWidget(self.acc_title)

        self.acc_email_label = QLabel("Not connected")
        self.acc_email_label.setStyleSheet("font-size: 12px; color: #BBBBBB;")
        self.acc_email_label.setWordWrap(True)
        acc_layout.addWidget(self.acc_email_label)

        self.layout.addWidget(self.account_frame)

        # Default select first
        if self.buttons:
            self.buttons[0].setChecked(True)

    def set_active_page(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    def set_connected_user(self, email: str | None) -> None:
        if email:
            self.acc_email_label.setText(email)
            self.acc_email_label.setStyleSheet("font-size: 12px; color: #4ADE80; font-weight: 500;")
        else:
            self.acc_email_label.setText("Not connected")
            self.acc_email_label.setStyleSheet("font-size: 12px; color: #F87171;")

