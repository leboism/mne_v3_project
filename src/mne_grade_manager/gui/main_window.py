from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QKeySequence, QShowEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow, QMessageBox, QTabWidget, QVBoxLayout

from ..services.database_transfer import (
    ARCHIVE_FILTER,
    SQLITE_FILTER,
    default_export_basename,
    export_data_package,
    import_data_package,
)

from ..tabs.students_tab import StudentsTab
from ..tabs.courses_tab import CoursesTab
from ..tabs.maquette_tab import MaquetteTab
from ..tabs.grades_tab import GradesTab
from ..tabs.results_tab import ResultsTab
from ..tabs.statistics_tab import StatisticsTab
from ..tabs.timetable_tab import TimetableTab
from ..services.terminology import TAB_PV_DELIBERATIONS
from ..tabs.jury_tab import DeliberationsTab


_MAIN_WINDOW_PREFERRED = QSize(1360, 860)


_TOOL_WINDOW_PREFERRED = QSize(1180, 760)
_STATISTICS_WINDOW_PREFERRED = QSize(1040, 820)


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

        self.results_tab = ResultsTab(self.repo, default_academic_year=self.academic_year)
        self.statistics_tab = StatisticsTab(self.repo, default_academic_year=self.academic_year)
        self.grades_tab = GradesTab(
            self.repo,
            refresh_callbacks=[self.results_tab.refresh_table],
            default_academic_year=self.academic_year,
        )
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
        self.deliberations_tab = DeliberationsTab(self.repo, default_academic_year=self.academic_year)
        self.maquette_tab = MaquetteTab(
            self.repo,
            refresh_callbacks=[
                self.grades_tab.refresh,
                self.results_tab.refresh_templates,
                self.courses_tab.refresh,
                self.deliberations_tab.refresh_all,
            ],
            default_academic_year=self.academic_year,
        )
        self.timetable_tab = TimetableTab(
            self.repo,
            default_academic_year=self.academic_year,
        )
        self.courses_tab.refresh_callbacks.append(self.maquette_tab.refresh_maquette_courses)

        self._maquette_dialog: QDialog | None = None
        self._courses_dialog: QDialog | None = None
        self._statistics_dialog: QDialog | None = None

        self.tabs.addTab(self.students_tab, "Étudiants")
        self.tabs.addTab(self.timetable_tab, "Emploi du temps")
        self.tabs.addTab(self.grades_tab, "Notes")
        self.tabs.addTab(self.results_tab, "Résultats")
        self.tabs.addTab(self.deliberations_tab, TAB_PV_DELIBERATIONS)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._build_menu()
        self._refresh_all()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._initial_geometry_done:
            from .screen_layout import adapt_window_size

            adapt_window_size(
                self,
                preferred=_MAIN_WINDOW_PREFERRED,
                minimum=QSize(640, 420),
                screen_fraction=0.92,
            )
            self._initial_geometry_done = True

    def _on_tab_changed(self, index: int) -> None:
        label = self.tabs.tabText(index)
        if label == "Étudiants":
            self.students_tab.refresh()

    def _open_tool_window(
        self,
        attr: str,
        widget,
        title: str,
        refresh=None,
        *,
        preferred: QSize | None = None,
    ) -> None:
        dlg = getattr(self, attr, None)
        if dlg is None:
            dlg = QDialog(self)
            dlg.setWindowTitle(title)
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(6, 6, 6, 6)
            lay.addWidget(widget)
            setattr(self, attr, dlg)
        if callable(refresh):
            refresh()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        if not getattr(dlg, "_geometry_done", False):
            from .screen_layout import adapt_window_size

            adapt_window_size(
                dlg,
                preferred=preferred or _TOOL_WINDOW_PREFERRED,
                minimum=QSize(640, 420),
                screen_fraction=0.9,
            )
            dlg._geometry_done = True

    def _open_maquette_window(self) -> None:
        self._open_tool_window(
            "_maquette_dialog",
            self.maquette_tab,
            "Maquette",
            refresh=self.maquette_tab.refresh,
        )

    def _open_courses_window(self) -> None:
        self._open_tool_window(
            "_courses_dialog",
            self.courses_tab,
            "Cours",
            refresh=self.courses_tab.refresh,
        )

    def _open_statistics_window(self) -> None:
        self._open_tool_window(
            "_statistics_dialog",
            self.statistics_tab,
            "Statistiques",
            refresh=self.statistics_tab.refresh,
            preferred=_STATISTICS_WINDOW_PREFERRED,
        )

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Fichier")

        export_action = QAction("Exporter les données (transfert)…", self)
        export_action.setToolTip(
            "Archive .zip : base SQLite, photos et documents — pour un autre ordinateur ou l'exécutable Windows."
        )
        export_action.triggered.connect(self._export_data_package)
        file_menu.addAction(export_action)

        import_action = QAction("Importer des données…", self)
        import_action.triggered.connect(self._import_data_package)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        save_db_action = QAction("Enregistrer la base SQLite seule…", self)
        save_db_action.setToolTip("Copie uniquement la base (.sqlite3), sans photos ni PDF.")
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
        refresh_action.setShortcut(QKeySequence("Ctrl+R"))
        refresh_action.setStatusTip("Recharge les listes et tableaux depuis la base (⌘R / Ctrl+R).")
        refresh_action.triggered.connect(self._refresh_all)
        file_menu.addAction(refresh_action)
        self.addAction(refresh_action)

        file_menu.addSeparator()

        quit_action = QAction("Quitter", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setStatusTip("Fermer l'application (⌘Q / Ctrl+Q).")
        quit_action.triggered.connect(self._quit_application)
        file_menu.addAction(quit_action)
        self.addAction(quit_action)

        view_menu = self.menuBar().addMenu("Affichage")
        for i in range(self.tabs.count()):
            label = self.tabs.tabText(i)
            act = QAction(label, self)
            act.triggered.connect(lambda _c=False, idx=i: self.tabs.setCurrentIndex(idx))
            view_menu.addAction(act)
        view_menu.addSeparator()
        maquette_action = QAction("Maquette…", self)
        maquette_action.triggered.connect(self._open_maquette_window)
        view_menu.addAction(maquette_action)
        courses_action = QAction("Cours…", self)
        courses_action.triggered.connect(self._open_courses_window)
        view_menu.addAction(courses_action)
        statistics_action = QAction("Statistiques…", self)
        statistics_action.triggered.connect(self._open_statistics_window)
        view_menu.addAction(statistics_action)

    def _back_to_welcome(self) -> None:
        if callable(self.back_to_welcome):
            self.back_to_welcome()

    def _quit_application(self) -> None:
        QApplication.instance().quit()

    def _open_master_team(self) -> None:
        if not (self.academic_year or "").strip():
            QMessageBox.warning(
                self,
                "Équipe du master",
                "Ouvrez d’abord un millésime depuis l’écran d’accueil "
                "(bouton « Ouvrir » après avoir choisi l’année).",
            )
            return
        try:
            from .master_team_dialog import open_master_team_dialog

            open_master_team_dialog(self.repo, self.academic_year, parent=self)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Équipe du master",
                f"Impossible d’ouvrir la fenêtre :\n\n{exc}",
            )

    def _seed_demo_data(self) -> None:
        try:
            msg = self.repo.seed_demo_data(academic_year=self.academic_year)
        except Exception as exc:
            QMessageBox.critical(self, "Données de démo", str(exc))
            return
        self._refresh_all()
        stu_idx = self.tabs.indexOf(self.students_tab)
        if stu_idx >= 0:
            self.tabs.setCurrentIndex(stu_idx)
        self._refresh_all()
        QApplication.processEvents()
        QMessageBox.information(self, "Données de démo", msg)

    def _export_data_package(self) -> None:
        default_name = default_export_basename(self.academic_year) + ".zip"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter les données",
            str(Path.home() / "Documents" / default_name),
            f"{ARCHIVE_FILTER};;Tous les fichiers (*.*)",
        )
        if not path:
            return
        try:
            summary = export_data_package(path, self.repo.db, academic_year=self.academic_year)
            QMessageBox.information(
                self,
                "Export terminé",
                f"Archive créée et vérifiée :\n{summary.path}\n\n"
                f"Taille archive : {summary.zip_bytes // 1024} Ko\n"
                f"Base SQLite : {summary.sqlite_bytes // 1024} Ko\n"
                f"Fichiers dans l'archive : {summary.archive_files}\n"
                f"Pièces jointes (photos, PDF…) : {summary.attachment_files}\n\n"
                "Contrôles OK : intégrité zip, manifeste, base SQLite, pièces jointes.\n\n"
                "Copiez ce fichier sur l'autre ordinateur, puis Fichier → Importer des données…",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))

    def _import_data_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer des données",
            str(Path.home() / "Documents"),
            f"{ARCHIVE_FILTER};;{SQLITE_FILTER};;Tous les fichiers (*.*)",
        )
        if not path:
            return
        reply = QMessageBox.warning(
            self,
            "Importer des données",
            "Remplacer toutes les données locales de ce programme par celles du fichier choisi ?\n\n"
            "• Base SQLite (étudiants, notes, maquettes…)\n"
            "• Photos et documents joints\n\n"
            "Les données actuelles seront écrasées. Exportez d'abord si vous souhaitez une sauvegarde.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            summary = import_data_package(path, self.repo.db)
            self._after_data_import()
            QMessageBox.information(
                self,
                "Import terminé",
                f"Données restaurées depuis :\n{summary.path}\n\n"
                f"Fichiers joints : {summary.attachment_files}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Import", str(exc))

    def _after_data_import(self) -> None:
        try:
            for tpl in self.repo.list_templates():
                try:
                    self.repo.consolidate_template_jury_rosters(int(tpl["id"]))
                except Exception:
                    pass
            self.repo.cleanup_empty_orphan_jury_rosters()
            self.repo.repair_missing_s1_jury_sessions()
            for tpl in self.repo.list_templates():
                try:
                    self.repo.repair_jury_decision_session_links(int(tpl["id"]))
                except Exception:
                    pass
        except Exception:
            pass
        self._refresh_all()

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
        self.results_tab.refresh_table()
        self.deliberations_tab.refresh_all()
        self.statistics_tab.refresh()
        self.timetable_tab.refresh()
