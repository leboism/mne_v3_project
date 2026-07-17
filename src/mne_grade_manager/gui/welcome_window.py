"""Fenêtre d'accueil : logo du Master et sélection de l'année universitaire."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.database import Database
from ..services.academic_years import (
    current_academic_year_label,
    ensure_custom_academic_year,
    ensure_welcome_year_floor,
    hide_academic_year,
    list_academic_year_choices,
    load_custom_academic_years,
    normalize_academic_year,
    remove_custom_academic_year,
    unhide_academic_year,
)
from ..services.database_transfer import ARCHIVE_FILTER, SQLITE_FILTER, import_data_package
from ..services.repository import Repository
from .screen_layout import adapt_window_size


class WelcomeWindow(QWidget):
    """Fenêtre d'accueil avec logo du Master et choix de l'année."""

    year_selected = Signal(str)

    def __init__(self, *, db: Database | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = db
        self.setWindowTitle("MNE Grade Manager — Accueil")
        self.setMinimumSize(420, 480)
        self._initial_geometry_done = False
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setSpacing(24)
        layout.setContentsMargins(32, 32, 32, 32)

        logo_path = Path(__file__).resolve().parent.parent / "assets" / "logo_master.png"
        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignCenter)
        self.logo_label.setStyleSheet("background: white; padding: 16px; border-radius: 8px;")
        if logo_path.is_file():
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(360, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.logo_label.setPixmap(scaled)
        if self.logo_label.pixmap() is None or self.logo_label.pixmap().isNull():
            self.logo_label.setText("Master NUCLEAR ENERGY")
            self.logo_label.setStyleSheet(
                "background: #f0f0f0; color: #333; font-size: 18px; font-weight: bold; padding: 24px; border-radius: 8px;"
            )

        layout.addWidget(self.logo_label, alignment=Qt.AlignCenter)

        title = QLabel("Choisissez l'année universitaire")
        title.setStyleSheet("font-size: 14px; color: #444;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self.year_combo = QComboBox()
        ensure_welcome_year_floor()
        self._reload_year_combo(select_current=False)
        self.year_combo.setMinimumWidth(200)
        self.year_combo.setStyleSheet(
            "min-height: 28px; padding: 6px 12px; font-size: 13px;"
        )
        layout.addWidget(self.year_combo, alignment=Qt.AlignCenter)

        add_layout = QHBoxLayout()
        add_layout.setSpacing(8)
        self.year_edit = QLineEdit()
        self.year_edit.setPlaceholderText("ex. 2027-2028")
        self.year_edit.setMaximumWidth(140)
        self.year_edit.setStyleSheet("padding: 6px 10px; font-size: 13px;")
        self.add_year_btn = QPushButton("Ajouter une année")
        self.add_year_btn.clicked.connect(self._on_add_year)
        self.delete_year_btn = QPushButton("Retirer le millésime…")
        self.delete_year_btn.setToolTip(
            "Retire le millésime de cette liste. "
            "Si des données existent (étudiants, maquettes…), elles sont supprimées définitivement."
        )
        self.delete_year_btn.clicked.connect(self._on_delete_year)
        add_layout.addStretch()
        add_layout.addWidget(self.year_edit)
        add_layout.addWidget(self.add_year_btn)
        add_layout.addWidget(self.delete_year_btn)
        add_layout.addStretch()
        layout.addLayout(add_layout)

        self.open_btn = QPushButton("Ouvrir")
        self.open_btn.setMinimumWidth(180)
        self.open_btn.setMinimumHeight(40)
        self.open_btn.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 8px 24px;"
        )
        self.open_btn.clicked.connect(self._on_open)
        layout.addWidget(self.open_btn, alignment=Qt.AlignCenter)

        self.import_btn = QPushButton("Importer des données…")
        self.import_btn.setToolTip(
            "Restaurer une archive exportée depuis un autre ordinateur (Fichier → Exporter les données)."
        )
        self.import_btn.clicked.connect(self._import_data)
        layout.addWidget(self.import_btn, alignment=Qt.AlignCenter)

        quit_action = QAction("Quitter", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self._quit_application)
        self.addAction(quit_action)

        layout.addStretch()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._initial_geometry_done:
            adapt_window_size(
                self,
                preferred=(520, 580),
                minimum=(400, 460),
                screen_fraction=0.55,
            )
            self._initial_geometry_done = True

    def _on_add_year(self) -> None:
        raw = self.year_edit.text()
        try:
            year = ensure_custom_academic_year(raw)
        except ValueError as exc:
            QMessageBox.warning(self, "Année invalide", str(exc))
            return
        unhide_academic_year(year)
        existing = [self.year_combo.itemText(i) for i in range(self.year_combo.count())]
        if year in existing:
            self.year_combo.setCurrentText(year)
        else:
            self._reload_year_combo(preferred=year)
        self.year_edit.clear()
        QMessageBox.information(self, "Ajouté", f"L'année {year} a été ajoutée.")

    def _on_delete_year(self) -> None:
        if self._db is None:
            QMessageBox.warning(self, "Retrait", "Base de données indisponible.")
            return
        year = self.year_combo.currentText().strip()
        if not normalize_academic_year(year):
            QMessageBox.warning(self, "Retrait", "Sélectionnez un millésime valide.")
            return

        repo = Repository(self._db)
        summary = repo.summarize_academic_year_deletion(year)
        total = sum(summary.values())
        custom = year in load_custom_academic_years()

        lines = [f"Millésime : {year}", ""]
        if total > 0:
            lines.extend(
                [
                    "Données qui seront supprimées définitivement :",
                    f"• {summary['students']} étudiant(s)",
                    f"• {summary['templates']} maquette(s)",
                    f"• {summary['timetable_imports']} import(s) d'emploi du temps",
                    f"• {summary['master_team_members']} entrée(s) équipe du master",
                    "",
                    "La bibliothèque de cours (UE partagées) n'est pas supprimée.",
                    "",
                    "Cette action est irréversible. Exportez d'abord si besoin.",
                ]
            )
        else:
            lines.append(
                "Aucune donnée enregistrée pour ce millésime. "
                "Il sera simplement retiré de la liste d'accueil."
            )
        if custom:
            lines.append("")
            lines.append("Le millésime sera aussi retiré de la liste personnalisée.")

        reply = QMessageBox.warning(
            self,
            "Retirer le millésime",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            if total > 0:
                repo.delete_academic_year_data(year)
            remove_custom_academic_year(year)
            hide_academic_year(year)
            self._reload_year_combo()
            msg = f"Le millésime {year} a été retiré de l'accueil."
            if total > 0:
                msg += (
                    f"\n\nDonnées supprimées :"
                    f"\n• {summary['students']} étudiant(s)"
                    f"\n• {summary['templates']} maquette(s)"
                    f"\n• {summary['timetable_imports']} emploi du temps"
                )
            QMessageBox.information(self, "Terminé", msg)
        except Exception as exc:
            QMessageBox.critical(self, "Retrait", str(exc))

    def _reload_year_combo(self, *, preferred: str = "", select_current: bool = True) -> None:
        current = self.year_combo.currentText() if select_current else ""
        pick = preferred or current
        years = list_academic_year_choices()
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItems(years)
        if pick and pick in years:
            self.year_combo.setCurrentText(pick)
        else:
            current_label = current_academic_year_label()
            if current_label in years:
                self.year_combo.setCurrentText(current_label)
            elif years:
                self.year_combo.setCurrentIndex(0)
        self.year_combo.blockSignals(False)

    def _import_data(self) -> None:
        if self._db is None:
            QMessageBox.warning(self, "Import", "Base de données indisponible.")
            return
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
            "Remplacer toutes les données locales par celles du fichier choisi ?\n\n"
            "Les données actuelles seront écrasées.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            summary = import_data_package(path, self._db)
            self._reload_year_combo()
            QMessageBox.information(
                self,
                "Import terminé",
                f"Données restaurées depuis :\n{summary.path}\n\n"
                f"Fichiers joints : {summary.attachment_files}\n\n"
                "Choisissez l'année universitaire puis cliquez sur Ouvrir.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Import", str(exc))

    def _on_open(self) -> None:
        year = self.year_combo.currentText()
        self.year_selected.emit(year)

    def _quit_application(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()
