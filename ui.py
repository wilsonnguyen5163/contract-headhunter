import os
import sys

# Fallback to offscreen platform in headless CI environments
if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import Qt, QPropertyAnimation, QRectF, QPointF
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QFrame, QGraphicsDropShadowEffect,
    QFileDialog, QLineEdit
)

# Color Palette matching CSS variables
COLOR_PURPLE = "#5D3BA1"
COLOR_DARK_PURPLE = "#4A2E83"
COLOR_WHITE = "#FEFEFD"
COLOR_GREY = "#6B6280"
COLOR_BORDER = "#E4DFF0"

COLOR_FIT_GREEN = "#1B7A43"
COLOR_FIT_GREEN_BG = "#E3F4EB"
COLOR_FIT_YELLOW = "#B8860B"
COLOR_FIT_YELLOW_BG = "#FFF8E6"
COLOR_FIT_RED = "#A31233"
COLOR_FIT_RED_BG = "#FDE8EC"

STYLE_SHEET = f"""
    QWidget#CentralWidget {{
        background-color: {COLOR_PURPLE};
    }}
    .Card {{
        background-color: {COLOR_WHITE};
        border-radius: 24px;
    }}
    .SectionBox {{
        border: 1.5px solid {COLOR_BORDER};
        border-radius: 16px;
        background-color: #FFFFFF;
    }}
    .ResultCard {{
        border: 1.5px solid {COLOR_BORDER};
        border-radius: 16px;
        background-color: #FFFFFF;
    }}
    QLabel.H1 {{
        color: #1A1A1A;
        font-size: 24px;
        font-weight: 800;
    }}
    QLabel.Subtitle {{
        color: {COLOR_GREY};
        font-size: 14px;
    }}
    QLabel.SectionTitle {{
        color: {COLOR_PURPLE};
        font-size: 16px;
        font-weight: 700;
    }}
    QPushButton.BtnPrimary {{
        background-color: {COLOR_PURPLE};
        color: #FFFFFF;
        border-radius: 14px;
        font-weight: 700;
        font-size: 14px;
        padding: 12px 24px;
        border: none;
    }}
    QPushButton.BtnPrimary:hover {{
        background-color: {COLOR_DARK_PURPLE};
    }}
    QPushButton.BtnTransparent {{
        background-color: rgba(93, 59, 161, 0.1);
        color: {COLOR_PURPLE};
        border-radius: 14px;
        font-weight: 700;
        font-size: 14px;
        padding: 10px 20px;
        border: none;
    }}
    QPushButton.BtnTransparent:hover {{
        background-color: rgba(93, 59, 161, 0.2);
    }}
    QPushButton.NavTab {{
        background-color: rgba(255, 255, 255, 0.15);
        color: #FFFFFF;
        border-radius: 16px;
        font-weight: 700;
        font-size: 13px;
        padding: 8px 20px;
        border: none;
    }}
    QPushButton.NavTab:hover, QPushButton.NavTab[active="true"] {{
        background-color: #FFFFFF;
        color: {COLOR_PURPLE};
    }}
    QPushButton#BtnApprove {{
        background-color: {COLOR_FIT_GREEN};
        color: white;
        border-radius: 10px;
        font-weight: 700;
        padding: 8px 16px;
        border: none;
    }}
    QPushButton#BtnReject {{
        background-color: {COLOR_FIT_RED};
        color: white;
        border-radius: 10px;
        font-weight: 700;
        padding: 8px 16px;
        border: none;
    }}
    QPushButton#BtnInfo {{
        background-color: {COLOR_FIT_YELLOW};
        color: white;
        border-radius: 10px;
        font-weight: 700;
        padding: 8px 16px;
        border: none;
    }}
    QLabel.BadgeApprove {{
        background-color: {COLOR_FIT_GREEN_BG};
        color: {COLOR_FIT_GREEN};
        border-radius: 10px;
        font-size: 12px;
        font-weight: 700;
        padding: 4px 10px;
    }}
    QLineEdit {{
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        padding: 8px;
        font-size: 14px;
    }}
"""


class AnimatedInfinityLogo(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 20)
        self._angle = 0.0

        self.animation = QPropertyAnimation(self, b"angle", self)
        self.animation.setDuration(3000)
        self.animation.setStartValue(0.0)
        self.animation.setKeyValueAt(0.25, 30.0)
        self.animation.setKeyValueAt(0.50, 0.0)
        self.animation.setKeyValueAt(0.75, -30.0)
        self.animation.setEndValue(0.0)
        self.animation.setLoopCount(-1)
        self.animation.start()

    def get_angle(self):
        return self._angle

    def set_angle(self, angle):
        self._angle = angle
        self.update()

    angle = property(get_angle, set_angle)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._angle)
        painter.translate(-self.width() / 2, -self.height() / 2)

        pen = QPen(QColor("#FFFFFF"), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        path = QPainterPath()
        path.moveTo(18, 10)
        path.cubicTo(22.3, 2, 34, 2, 34, 10)
        path.cubicTo(34, 18, 22.3, 18, 18, 10)
        path.cubicTo(13.7, 2, 2, 2, 2, 10)
        path.cubicTo(2, 18, 13.7, 18, 18, 10)
        painter.drawPath(path)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("do good talent · Desktop Matching Engine")
        self.resize(900, 750)
        self.setObjectName("CentralWidget")
        self.setStyleSheet(STYLE_SHEET)
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QVBoxLayout()
        header_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_container = QHBoxLayout()
        title_container.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_do = QLabel("do g")
        lbl_do.setStyleSheet("font-size: 28px; font-weight: 900; color: #FFFFFF;")
       
        self.logo = AnimatedInfinityLogo()
        lbl_d = QLabel("d")
        lbl_d.setStyleSheet("font-size: 28px; font-weight: 900; color: #FFFFFF;")

        title_container.addWidget(lbl_do)
        title_container.addWidget(self.logo)
        title_container.addWidget(lbl_d)

        lbl_sub = QLabel("a talent company")
        lbl_sub.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.9); font-weight: 400;")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_layout.addLayout(title_container)
        header_layout.addWidget(lbl_sub)
        main_layout.addLayout(header_layout)

        nav_layout = QHBoxLayout()
        nav_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_layout.setSpacing(12)

        self.btn_tab_find = QPushButton("FIND MATCHES")
        self.btn_tab_find.setProperty("class", "NavTab")
        self.btn_tab_find.setProperty("active", "true")
        self.btn_tab_find.clicked.connect(lambda: self.switch_tab(0))

        self.btn_tab_results = QPushButton("RESULTS")
        self.btn_tab_results.setProperty("class", "NavTab")
        self.btn_tab_results.clicked.connect(lambda: self.switch_tab(1))

        self.btn_tab_settings = QPushButton("SETTINGS")
        self.btn_tab_settings.setProperty("class", "NavTab")
        self.btn_tab_settings.clicked.connect(lambda: self.switch_tab(2))

        nav_layout.addWidget(self.btn_tab_find)
        nav_layout.addWidget(self.btn_tab_results)
        nav_layout.addWidget(self.btn_tab_settings)

        main_layout.addLayout(nav_layout)
        main_layout.addSpacing(10)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.create_find_tab())
        self.stack.addWidget(self.create_results_tab())
        self.stack.addWidget(self.create_settings_tab())

        main_layout.addWidget(self.stack)

        self.fallback_bar = QFrame(self)
        self.fallback_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {COLOR_WHITE};
                border: 2px solid {COLOR_PURPLE};
                border-radius: 20px;
            }}
        """)
        fallback_layout = QHBoxLayout(self.fallback_bar)
        fallback_layout.setContentsMargins(20, 10, 20, 10)
        lbl_status = QLabel("Results ready")
        lbl_status.setStyleSheet(f"font-weight: 700; color: {COLOR_PURPLE}; font-size: 14px; border: none;")
        btn_view = QPushButton("See Results")
        btn_view.setProperty("class", "BtnPrimary")
        btn_view.clicked.connect(lambda: self.switch_tab(1))
       
        fallback_layout.addWidget(lbl_status)
        fallback_layout.addWidget(btn_view)
       
        self.fallback_bar.hide()
        main_layout.addWidget(self.fallback_bar, alignment=Qt.AlignmentFlag.AlignCenter)

    def create_card_frame(self):
        card = QFrame()
        card.setProperty("class", "Card")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setColor(QColor(20, 8, 50, 90))
        shadow.setOffsetY(10)
        card.setGraphicsEffect(shadow)
        return card

    def create_find_tab(self):
        card = self.create_card_frame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 32, 32, 32)

        lbl_title = QLabel("Find Talent Matches")
        lbl_title.setProperty("class", "H1")
        lbl_sub = QLabel("Upload contractors and job postings to scan for best fits.")
        lbl_sub.setProperty("class", "Subtitle")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_sub)
        layout.addSpacing(10)

        sec1 = QFrame()
        sec1.setProperty("class", "SectionBox")
        sec1_layout = QVBoxLayout(sec1)
        sec1_title = QLabel("1. Contractors (optional)")
        sec1_title.setProperty("class", "SectionTitle")
        sec1_sub = QLabel("Select your contractor CSV or text file.")
        sec1_sub.setProperty("class", "Subtitle")
        btn_browse1 = QPushButton("Browse File...")
        btn_browse1.setProperty("class", "BtnTransparent")
        btn_browse1.clicked.connect(self.browse_file)

        sec1_layout.addWidget(sec1_title)
        sec1_layout.addWidget(sec1_sub)
        sec1_layout.addWidget(btn_browse1, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(sec1)

        sec2 = QFrame()
        sec2.setProperty("class", "SectionBox")
        sec2_layout = QVBoxLayout(sec2)
        sec2_title = QLabel("2. Jobs")
        sec2_title.setProperty("class", "SectionTitle")
        sec2_sub = QLabel("Select your job descriptions file (.txt, .pdf).")
        sec2_sub.setProperty("class", "Subtitle")
        btn_browse2 = QPushButton("Browse File...")
        btn_browse2.setProperty("class", "BtnTransparent")
        btn_browse2.clicked.connect(self.browse_file)

        sec2_layout.addWidget(sec2_title)
        sec2_layout.addWidget(sec2_sub)
        sec2_layout.addWidget(btn_browse2, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(sec2)

        btn_scan = QPushButton("🔍 FIND MATCHES")
        btn_scan.setProperty("class", "BtnPrimary")
        btn_scan.clicked.connect(self.trigger_scan)
        layout.addSpacing(10)
        layout.addWidget(btn_scan)
        layout.addStretch()

        wrapper = QWidget()
        wrap_layout = QVBoxLayout(wrapper)
        wrap_layout.addWidget(card)
        return wrapper

    def create_results_tab(self):
        card = self.create_card_frame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 32, 32, 32)

        lbl_title = QLabel("Match Results")
        lbl_title.setProperty("class", "H1")
        lbl_sub = QLabel("1 job with clear fits · 0 to check by hand")
        lbl_sub.setProperty("class", "Subtitle")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_sub)
        layout.addSpacing(10)

        res_card = QFrame()
        res_card.setProperty("class", "ResultCard")
        res_layout = QVBoxLayout(res_card)

        res_header = QHBoxLayout()
        res_job_title = QLabel("Content Producer")
        res_job_title.setStyleSheet("font-size: 18px; font-weight: 800;")
        badge = QLabel("Clear Fit")
        badge.setProperty("class", "BadgeApprove")

        res_header.addWidget(res_job_title)
        res_header.addStretch()
        res_header.addWidget(badge)

        lbl_candidate = QLabel("Alexandra Rabbitte Hughes · Social Media Strategy & Community Lead (Score: 80)")
        lbl_candidate.setProperty("class", "Subtitle")

        lbl_desc = QLabel("Matches social media strategy, execution, and community management.")
        lbl_desc.setStyleSheet(f"font-size: 14px; color: {COLOR_GREY}; margin: 8px 0;")

        btn_group = QHBoxLayout()
        btn_approve = QPushButton("✓ Approve")
        btn_approve.setObjectName("BtnApprove")
        btn_reject = QPushButton("✕ Reject")
        btn_reject.setObjectName("BtnReject")
        btn_info = QPushButton("? Needs Info")
        btn_info.setObjectName("BtnInfo")

        btn_group.addWidget(btn_approve)
        btn_group.addWidget(btn_reject)
        btn_group.addWidget(btn_info)
        btn_group.addStretch()

        res_layout.addLayout(res_header)
        res_layout.addWidget(lbl_candidate)
        res_layout.addWidget(lbl_desc)
        res_layout.addLayout(btn_group)

        layout.addWidget(res_card)
        layout.addStretch()

        wrapper = QWidget()
        wrap_layout = QVBoxLayout(wrapper)
        wrap_layout.addWidget(card)
        return wrapper

    def create_settings_tab(self):
        card = self.create_card_frame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 32, 32, 32)

        lbl_title = QLabel("Settings")
        lbl_title.setProperty("class", "H1")
        lbl_sub = QLabel("Manage API Keys and environment settings.")
        lbl_sub.setProperty("class", "Subtitle")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_sub)
        layout.addSpacing(10)

        sec = QFrame()
        sec.setProperty("class", "SectionBox")
        sec_layout = QVBoxLayout(sec)

        sec_title = QLabel("Gemini API Key")
        sec_title.setProperty("class", "SectionTitle")
        sec_sub = QLabel("Paste your Google AI Studio API key below.")
        sec_sub.setProperty("class", "Subtitle")

        input_key = QLineEdit()
        input_key.setEchoMode(QLineEdit.EchoMode.Password)
        input_key.setPlaceholderText("Enter API Key...")

        btn_save = QPushButton("Save Key")
        btn_save.setProperty("class", "BtnPrimary")

        sec_layout.addWidget(sec_title)
        sec_layout.addWidget(sec_sub)
        sec_layout.addWidget(input_key)
        sec_layout.addWidget(btn_save, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(sec)
        layout.addStretch()

        wrapper = QWidget()
        wrap_layout = QVBoxLayout(wrapper)
        wrap_layout.addWidget(card)
        return wrapper

    def switch_tab(self, index):
        self.stack.setCurrentIndex(index)
        tabs = [self.btn_tab_find, self.btn_tab_results, self.btn_tab_settings]
        for i, tab in enumerate(tabs):
            tab.setProperty("active", "true" if i == index else "false")
            tab.style().unpolish(tab)
            tab.style().polish(tab)

    def browse_file(self):
        QFileDialog.getOpenFileName(self, "Select File")

    def trigger_scan(self):
        dialog = ScanCompleteDialog(self)
        if dialog.exec():
            self.switch_tab(1)
            self.fallback_bar.hide()
        else:
            self.fallback_bar.show()


class ScanCompleteDialog(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.result_status = False

        container = QFrame(self)
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {COLOR_WHITE};
                border-radius: 24px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 100))
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Scan Complete!")
        title.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {COLOR_PURPLE};")
        subtitle = QLabel("Matches have been calculated using Gemini AI.")
        subtitle.setStyleSheet(f"color: {COLOR_GREY}; font-size: 14px; margin: 12px 0 24px;")

        btn_layout = QHBoxLayout()
        btn_see = QPushButton("See Results")
        btn_see.setProperty("class", "BtnPrimary")
        btn_see.setStyleSheet(STYLE_SHEET)
        btn_see.clicked.connect(self.accept)

        btn_decline = QPushButton("Decline")
        btn_decline.setProperty("class", "BtnTransparent")
        btn_decline.setStyleSheet(STYLE_SHEET)
        btn_decline.clicked.connect(self.reject)

        btn_layout.addWidget(btn_see)
        btn_layout.addWidget(btn_decline)

        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(btn_layout)

        main_l = QVBoxLayout(self)
        main_l.addWidget(container)
        self.setFixedSize(440, 220)

    def exec(self):
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.show()
        self.loop = Qt.QEventLoop()
        self.loop.exec()
        return self.result_status

    def accept(self):
        self.result_status = True
        self.close()
        self.loop.quit()

    def reject(self):
        self.result_status = False
        self.close()
        self.loop.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


