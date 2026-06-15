from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QShowEvent
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QTabWidget

from .master_team_dialog import open_master_team_dialog
from .screen_layout import adapt_window_size

from ..tabs.students_tab import StudentsTab
from ..tabs.courses_tab import CoursesTab
from ..tabs.maquette_tab import MaquetteTab
from ..tabs.grades_tab import GradesTab
from ..tabs.results_tab import ResultsTab
from ..tabs.statistics_tab import StatisticsTab
from ..services.terminology import TAB_PV_DELIBERATIONS
from ..tabs.jury_tab import DeliberationsTab


_TAB_SIZES: dict[str, QSize] = {
    "Étudiants": QSize(1100, 720),
    "Maquette": QSize(1200, 800),
    "Cours": QSize(1100, 700),
    "Notes": QSize(1300, 850),
    "Résultats": QSize(1400, 800),
    TAB_PV_DELIBERATIONS: QSize(1280, 820),
    "Statistiques": QSize(1000, 700),
}


class MainWindow(QMainWindow):
    def __init__(self, repo, academic_year: str = "", back_to_welcome=None):
        super().__init__()
        self.repo = repo
        self.academic_year = academic_year
        self.back_to_welcome = back_to_welcome
        title = "MNE Grade Manager V3"
        if academic_year:
            title = f"{title} — {academic_year}"
        self.setWindowTitle(title)
        self._initial_geometry_done = False

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.results_tab = ResultsTab(self.repo)
        self.statistics_tab = StatisticsTab(self.repo, default_academic_year=self.academic_year)
        self.grades_tab = GradesTab(self.repo, refresh_callbacks=[self.results_tab.refresh_table])
        self.students_tab = StudentsTab(
            self.repo,
            refresh_callbacks=[self.grades_tab.refresh],
            default_academic_year=self.academic_year,
        )
        self.courses_tab = CoursesTab(
            self.repo,
            refresh_callbacks=[self.grades_tab.refresh],
            default_academic_year=self.academic_year,
        )
        self.deliberations_tab = DeliberationsTab(self.repo)
        self.maquette_tab = MaquetteTab(
            self.repo,
            refresh_callbacks=[
                self.grades_tab.refresh,
                self.results_tab.refresh_templates,
                self.courses_tab.refresh,
                self.deliberations_tab.refresh_all,
            ],
        )
        self.courses_tab.refresh_callbacks.append(self.maquette_tab.refresh_maquette_courses)

        self.tabs.addTab(self.students_tab, "Étudiants")
        self.tabs.addTab(self.maquette_tab, "Maquette")
        self.tabs.addTab(self.courses_tab, "Cours")
        self.tabs.addTab(self.grades_tab, "Notes")
        self.tabs.addTab(self.results_tab, "Résultats")
        self.tabs.addTab(self.deliberations_tab, TAB_PV_DELIBERATIONS)
        self.tabs.addTab(self.statistics_tab, "Statistiques")

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._build_menu()
        self._refresh_all()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._initial_geometry_done:
            adapt_window_size(
                self,
                preferred=QSize(1280, 820),
                minimum=QSize(800, 500),
                screen_fraction=0.95,
            )
            self._initial_geometry_done = True

    def _on_tab_changed(self, index: int) -> None:
        label = self.tabs.tabText(index)
        size = _TAB_SIZES.get(label)
        if size is not None:
            adapt_window_size(
                self,
                preferred=size,
                minimum=QSize(800, 500),
                screen_fraction=0.95,
                center=False,
            )
        if label == "Étudiants":
            self.students_tab.refresh()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Fichier")
        save_db_action = QAction("Enregistrer la base sous…", self)
        save_db_action.triggered.connect(self._save_database_as)
        file_menu.addAction(save_db_action)

        seed_action = QAction("Créer des données de démo", self)
        seed_action.triggered.connect(self._seed_demo_data)
        file_menu.addAction(seed_action)

        team_action = QAction("Équipe du master…", self)
        team_action.triggered.connect(self._open_master_team)
        file_menu.addAction(team_action)

        file_menu.addSeparator()

        back_action = QAction("Retour à l'accueil", self)
        back_action.setEnabled(self.back_to_welcome is not None)
        back_action.triggered.connect(self._back_to_welcome)
        file_menu.addAction(back_action)

        refresh_action = QAction("Tout actualiser", self)
        refresh_action.triggered.connect(self._refresh_all)
        file_menu.addAction(refresh_action)

        view_menu = self.menuBar().addMenu("Affichage")
        for i in range(self.tabs.count()):
            label = self.tabs.tabText(i)
            act = QAction(label, self)
            act.triggered.connect(lambda _c=False, idx=i: self.tabs.setCurrentIndex(idx))
            view_menu.addAction(act)

    def _back_to_welcome(self) -> None:
        if callable(self.back_to_welcome):
            self.back_to_welcome()

    def _open_master_team(self) -> None:
        if not (self.academic_year or "").strip():
            QMessageBox.warning(
                self,
                "Équipe du master",
                "Ouvrez d’abord un millésime depuis l’écran d’accueil.",
            )
            return
        open_master_team_dialog(self.repo, self.academic_year, parent=self)

    def _seed_demo_data(self) -> None:
        try:
            msg = self.repo.seed_demo_data(academic_year=self.academic_year)
        except Exception as exc:
            QMessageBox.critical(self, "Données de démo", str(exc))
            return
        self._refresh_all()
        QMessageBox.information(self, "Données de démo", msg)

    def _save_database_as(self) -> None:
        default_name = f"mne_grade_manager_{date.today().isoformat()}.sqlite3"
        if self.academic_year:
            safe = self.academic_year.replace("/", "-").replace(" ", "_")
            default_name = f"mne_grade_manager_{safe}_{date.today().isoformat()}.sqlite3"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la base",
            str(Path.home() / "Documents" / default_name),
            "SQLite (*.sqlite3);;Tous les fichiers (*.*)",
        )
        if not path:
            return
        if not path.lower().endswith(".sqlite3"):
            path = path + ".sqlite3"
        try:
            self.repo.db.backup_to(path)
            QMessageBox.information(self, "Base enregistrée", f"Sauvegarde :\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Échec", str(exc))

    def _refresh_all(self) -> None:
        try:
            self.repo.sync_enrollments_for_academic_year(self.academic_year)
        except Exception:
            pass
        self.students_tab.refresh()
        self.maquette_tab.refresh()
        self.courses_tab.refresh()
        self.grades_tab.refresh()
        self.results_tab.refresh_templates()
        self.deliberations_tab.refresh_all()
        self.statistics_tab.refresh()
