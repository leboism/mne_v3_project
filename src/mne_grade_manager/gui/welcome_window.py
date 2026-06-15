"""Fenêtre d'accueil : logo du Master et sélection de l'année universitaire."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .screen_layout import adapt_window_size

APP_DIR = Path.home() / ".mne_grade_manager"
CUSTOM_YEARS_FILE = APP_DIR / "custom_years.json"


def _academic_years() -> list[str]:
    """Génère les libellés d'années universitaires (année en cours - 2 à + 1)."""
    from datetime import date
    today = date.today()
    # année universitaire: à partir de septembre (09)
    start_year = today.year if today.month >= 9 else today.year - 1
    years = [f"{start_year - i}-{start_year - i + 1}" for i in range(2, -2, -1)]
    return years  # ex. 2023-2024, 2024-2025, 2025-2026, 2026-2027


def _current_academic_year_label() -> str:
    from datetime import date
    today = date.today()
    start_year = today.year if today.month >= 9 else today.year - 1
    return f"{start_year}-{start_year + 1}"


def _load_custom_years() -> list[str]:
    """Charge les années personnalisées enregistrées."""
    if not CUSTOM_YEARS_FILE.is_file():
        return []
    try:
        data = json.loads(CUSTOM_YEARS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_custom_years(years: list[str]) -> None:
    """Enregistre les années personnalisées."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_YEARS_FILE.write_text(json.dumps(years, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_academic_year(value: str) -> str | None:
    """Retourne le libellé normalisé (ex. 2027-2028) si valide, sinon None."""
    value = value.strip()
    if not value:
        return None
    m = re.match(r"^(\d{4})\s*[-–]\s*(\d{4})$", value)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        if y2 == y1 + 1 and 1990 <= y1 <= 2100:
            return f"{y1}-{y2}"
    return None


class WelcomeWindow(QWidget):
    """Fenêtre d'accueil avec logo du Master et choix de l'année."""

    year_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MNE Grade Manager — Accueil")
        self.setMinimumSize(480, 520)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setSpacing(24)
        layout.setContentsMargins(32, 32, 32, 32)

        # Logo du Master
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

        # Titre
        title = QLabel("Choisissez l'année universitaire")
        title.setStyleSheet("font-size: 14px; color: #444;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Menu déroulant année
        self.year_combo = QComboBox()
        default_years = _academic_years()
        custom = _load_custom_years()
        seen = set(default_years)
        for y in custom:
            if y not in seen and _validate_academic_year(y):
                seen.add(y)
                default_years.append(y)
        self.year_combo.addItems(default_years)
        # sélection par défaut = année universitaire courante
        current_label = _current_academic_year_label()
        if current_label in default_years:
            self.year_combo.setCurrentText(current_label)
        self.year_combo.setMinimumWidth(200)
        self.year_combo.setStyleSheet(
            "min-height: 28px; padding: 6px 12px; font-size: 13px;"
        )
        layout.addWidget(self.year_combo, alignment=Qt.AlignCenter)

        # Ajouter une année personnalisée
        add_layout = QHBoxLayout()
        add_layout.setSpacing(8)
        self.year_edit = QLineEdit()
        self.year_edit.setPlaceholderText("ex. 2027-2028")
        self.year_edit.setMaximumWidth(140)
        self.year_edit.setStyleSheet("padding: 6px 10px; font-size: 13px;")
        self.add_year_btn = QPushButton("Ajouter une année")
        self.add_year_btn.clicked.connect(self._on_add_year)
        add_layout.addStretch()
        add_layout.addWidget(self.year_edit)
        add_layout.addWidget(self.add_year_btn)
        add_layout.addStretch()
        layout.addLayout(add_layout)

        # Bouton pour ouvrir l'application
        self.open_btn = QPushButton("Ouvrir")
        self.open_btn.setMinimumWidth(180)
        self.open_btn.setMinimumHeight(40)
        self.open_btn.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 8px 24px;"
        )
        self.open_btn.clicked.connect(self._on_open)
        layout.addWidget(self.open_btn, alignment=Qt.AlignCenter)

        layout.addStretch()

        adapt_window_size(
            self,
            preferred=(520, 580),
            minimum=(420, 480),
            screen_fraction=0.55,
        )

    def _on_add_year(self) -> None:
        raw = self.year_edit.text()
        year = _validate_academic_year(raw)
        if year is None:
            QMessageBox.warning(
                self,
                "Année invalide",
                "Saisissez une année au format AAAA-AAAA (ex. 2027-2028).\n"
                "La deuxième année doit être la suivante (ex. 2027-2028).",
            )
            return
        existing = [self.year_combo.itemText(i) for i in range(self.year_combo.count())]
        if year in existing:
            QMessageBox.information(
                self,
                "Déjà présente",
                f"L'année {year} est déjà dans la liste.",
            )
            self.year_edit.clear()
            return
        self.year_combo.addItem(year)
        self.year_combo.setCurrentText(year)
        custom = _load_custom_years()
        if year not in custom:
            custom.append(year)
            custom.sort()
            _save_custom_years(custom)
        self.year_edit.clear()
        QMessageBox.information(self, "Ajouté", f"L'année {year} a été ajoutée.")

    def _on_open(self) -> None:
        year = self.year_combo.currentText()
        self.year_selected.emit(year)
