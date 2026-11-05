"""Setup check screen verifying OBS Studio settings and providing plain-language fixes."""

from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cookeddisk.obs_client import ObsClient, SetupCheckItem
from cookeddisk.ui.icons import get_icon


class SetupCheckCard(QFrame):
    def __init__(self, item: SetupCheckItem, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("class", "card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # Header row: Title + Status Badge
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        title_lbl = QLabel(item.title)
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")
        top_row.addWidget(title_lbl)

        top_row.addStretch()

        badge_lbl = QLabel(item.status)
        if item.status == "PASS":
            badge_lbl.setProperty("class", "badgePass")
        elif item.status == "WARN":
            badge_lbl.setProperty("class", "badgeWarn")
        else:
            badge_lbl.setProperty("class", "badgeFail")
        top_row.addWidget(badge_lbl)

        layout.addLayout(top_row)

        # Value row
        val_lbl = QLabel(f"Detected: {item.current_value}")
        val_lbl.setStyleSheet("font-size: 12px; color: #888888; font-feature-settings: 'tnum' 1;")
        layout.addWidget(val_lbl)

        # Fix instructions if not passing
        if item.status != "PASS":
            fix_box = QFrame()
            fix_box.setStyleSheet(
                "background-color: rgba(255, 255, 255, 0.03); border: 1px solid #383838; border-radius: 4px; padding: 6px 10px;"
            )
            fix_layout = QVBoxLayout(fix_box)
            fix_layout.setContentsMargins(6, 6, 6, 6)
            fix_layout.setSpacing(2)

            fix_title = QLabel("Fix instruction:")
            fix_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #F08282;" if item.status == "FAIL" else "font-size: 11px; font-weight: 600; color: #E5B660;")
            fix_layout.addWidget(fix_title)

            fix_text = QLabel(item.fix_instruction)
            fix_text.setWordWrap(True)
            fix_text.setStyleSheet("font-size: 12px; color: #CCCCCC;")
            fix_layout.addWidget(fix_text)

            layout.addWidget(fix_box)


class SetupCheckView(QWidget):
    def __init__(self, obs_client: ObsClient, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.obs_client = obs_client

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("OBS Studio Setup Verification")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        title_box.addWidget(title)

        sub = QLabel("Validates OBS settings for zero-buffer streaming and crash safety.")
        sub.setStyleSheet("font-size: 12px; color: #888888;")
        title_box.addWidget(sub)

        header.addLayout(title_box)
        header.addStretch()

        self.btn_auto_config = QPushButton("Auto-Configure OBS")
        self.btn_auto_config.setProperty("class", "primaryButton")
        self.btn_auto_config.setIcon(get_icon("check", "#FFFFFF", 16))
        self.btn_auto_config.clicked.connect(self.auto_configure_obs)
        header.addWidget(self.btn_auto_config)

        self.btn_check_now = QPushButton("Run Check Now")
        self.btn_check_now.setIcon(get_icon("refresh", "#888888", 16))
        self.btn_check_now.clicked.connect(self.run_check)
        header.addWidget(self.btn_check_now)

        main_layout.addLayout(header)

        # Scroll Area for diagnostic cards
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()

        self.scroll_area.setWidget(self.cards_container)
        main_layout.addWidget(self.scroll_area)

    def run_check(self) -> None:
        # Clear existing cards
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        items = self.obs_client.run_setup_check()
        for it in items:
            card = SetupCheckCard(it)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    def auto_configure_obs(self) -> None:
        success, actions = self.obs_client.auto_configure_obs()
        if success:
            actions_text = "\n• " + "\n• ".join(actions)
            QMessageBox.information(
                self,
                "OBS Auto-Configured",
                f"Successfully applied optimal settings to OBS Studio:\n{actions_text}\n\nRunning diagnostic check now...",
            )
            self.run_check()
        else:
            err_msg = "\n• " + "\n• ".join(actions)
            QMessageBox.warning(
                self,
                "Auto-Configure Failed",
                f"Could not automatically configure OBS Studio:\n{err_msg}",
            )

