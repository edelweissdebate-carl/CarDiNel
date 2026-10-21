from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.propagation_tab import PropagationTab
from src.gui.comparison_tab import ComparisonTab
from src.gui.research_workflow_tab import ResearchWorkflowTab
from src.gui.dashboard import Dashboard
from src.gui.orbit_3d_tab import Orbit3DTab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle(
            "ORBIT-TRUST"
        )

        self.resize(
            1200,
            760,
        )

        self.tabs = QTabWidget()

        self.tabs.addTab(
            Dashboard(),
            "Dashboard",
        )

        self.tabs.addTab(
            PropagationTab(),
            "Propagation",
        )

        self.tabs.addTab(
            Orbit3DTab(),
            "3D Orbit",
        )

        self.tabs.addTab(
            ResearchWorkflowTab(),
            "Research Workflow",
        )

        self.tabs.addTab(
            ComparisonTab(),
            "Comparison",
        )

        self.tabs.addTab(
            self.create_placeholder(
                "Monte Carlo",
                (
                    "Run repeated simulations "
                    "with uncertain parameters"
                ),
            ),
            "Monte Carlo",
        )

        self.tabs.addTab(
            self.create_placeholder(
                "Results",
                (
                    "Graphs, reliability horizon, "
                    "and exported data"
                ),
            ),
            "Results",
        )

        defense_button = QPushButton("Open Defense Results")
        defense_button.clicked.connect(self.open_defense_results)
        self.tabs.setCornerWidget(defense_button)
        self.defense_window = None

        self.setCentralWidget(
            self.tabs
        )

        self.statusBar().showMessage(
            "ORBIT-TRUST ready"
        )

    def open_defense_results(self):
        from src.gui.defense_window import DefenseWindow
        if self.defense_window is None:
            self.defense_window = DefenseWindow()
        self.defense_window.show()
        self.defense_window.raise_()
        self.defense_window.activateWindow()

    @staticmethod
    def create_placeholder(
        title_text: str,
        description_text: str,
    ) -> QWidget:
        page = QWidget()

        layout = QVBoxLayout(
            page
        )

        title = QLabel(
            title_text
        )

        title.setStyleSheet(
            "font-size: 26px; "
            "font-weight: bold; "
            "margin-bottom: 8px;"
        )

        description = QLabel(
            description_text
        )

        description.setStyleSheet(
            "font-size: 15px;"
        )

        layout.addWidget(
            title
        )

        layout.addWidget(
            description
        )

        layout.addStretch()

        return page