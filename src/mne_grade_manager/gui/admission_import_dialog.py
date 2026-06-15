"""Prévisualisation et import de dossiers de candidature PDF."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..services.admission_import import AdmissionDossier
from ..services.admission_photo import save_extracted_photo_temp
from ..services.lookups import adapt_institutional_email, is_valid_institutional_email, normalize_email


class AdmissionImportDialog(QDialog):
    def __init__(
        self,
        dossiers: list[AdmissionDossier],
        *,
        default_academic_year: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Import dossiers de candidature")
        self.resize(1100, 520)
        self._dossiers = dossiers
        self._row_checks: list[QCheckBox] = []

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Vérifiez les champs extraits avant import. "
                "La photo d'identité est extraite automatiquement quand elle est détectée. "
                "Le PDF sera joint au profil (dossier de candidature)."
            )
        )

        form = QFormLayout()
        self.academic_year = QLineEdit()
        self.academic_year.setPlaceholderText("ex. 2026-2027")
        if default_academic_year:
            self.academic_year.setText(default_academic_year)
        form.addRow("Année universitaire (millésime ouvert)", self.academic_year)
        layout.addLayout(form)

        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels(
            [
                "Importer",
                "Photo",
                "Source",
                "Fichier",
                "Nom",
                "Prénom",
                "INE",
                "N° établ.",
                "Parcours",
                "Canal",
                "Email perso",
                "Avertissements",
            ]
        )
        self.table.setRowCount(len(dossiers))
        for row, dossier in enumerate(dossiers):
            cb = QCheckBox()
            cb.setChecked(dossier.importable)
            cb.setEnabled(dossier.importable)
            self._row_checks.append(cb)
            self.table.setCellWidget(row, 0, cb)

            self._set_item(row, 1, "Oui" if dossier.photo_found else "Non")
            self._set_item(row, 2, dossier.source)
            self._set_item(row, 3, _basename(dossier.source_file))
            self._set_item(row, 4, dossier.last_name)
            self._set_item(row, 5, dossier.first_name)
            self._set_item(row, 6, dossier.student_number_ine)
            self._set_item(row, 7, dossier.student_number_local)
            self._set_item(row, 8, dossier.track)
            self._set_item(row, 9, dossier.monmaster_channel or "—")
            self._set_item(row, 10, dossier.email_personal)
            warn = dossier.parse_error or "; ".join(dossier.warnings)
            self._set_item(row, 11, warn)

        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        select_all = QCheckBox("Tout sélectionner")
        select_all.stateChanged.connect(self._toggle_all)
        actions.addWidget(select_all)
        actions.addStretch()
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_item(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, col, item)

    def _toggle_all(self, state: int) -> None:
        checked = state == Qt.CheckState.Checked
        for cb in self._row_checks:
            if cb.isEnabled():
                cb.setChecked(checked)

    def _on_accept(self) -> None:
        if not any(cb.isChecked() for cb in self._row_checks):
            QMessageBox.warning(self, "Import", "Aucun dossier sélectionné.")
            return
        if not self.academic_year.text().strip():
            QMessageBox.warning(
                self,
                "Import",
                "Renseignez l'année universitaire (millésime ouvert à l'accueil).",
            )
            return
        self.accept()

    def selected_dossiers(self) -> list[AdmissionDossier]:
        enrollment_year = self.academic_year.text().strip()
        out: list[AdmissionDossier] = []
        for cb, dossier in zip(self._row_checks, self._dossiers):
            if cb.isChecked():
                if enrollment_year:
                    dossier.academic_year = enrollment_year
                out.append(dossier)
        return out


def _basename(path: str) -> str:
    from pathlib import Path

    return Path(path).name


def import_admission_dossiers(
    repo: Any,
    dossiers: list[AdmissionDossier],
    *,
    default_academic_year: str = "",
    attach_pdf: bool = True,
) -> tuple[int, int, list[str]]:
    """Crée les étudiants et attache les PDF. Retourne (créés, ignorés, erreurs)."""
    created = 0
    skipped = 0
    errors: list[str] = []

    existing = repo.list_students()
    by_ine: dict[str, dict] = {}
    by_name: dict[tuple[str, str], dict] = {}
    for s in existing:
        ine = str(s.get("student_number_ine") or "").strip().upper()
        if ine:
            by_ine[ine] = s
        key = (
            str(s.get("last_name") or "").strip().upper(),
            str(s.get("first_name") or "").strip().upper(),
        )
        if key[0] and key[1]:
            by_name[key] = s

    for dossier in dossiers:
        if not dossier.importable:
            skipped += 1
            continue

        ine = dossier.student_number_ine.strip().upper()
        name_key = (dossier.last_name.strip().upper(), dossier.first_name.strip().upper())
        if ine and ine in by_ine:
            skipped += 1
            errors.append(f"{dossier.display_name} : déjà présent (INE {ine}).")
            continue
        if name_key in by_name:
            skipped += 1
            errors.append(f"{dossier.display_name} : homonyme déjà en base.")
            continue

        email_inst = adapt_institutional_email(
            dossier.first_name,
            dossier.last_name,
            dossier.enrollment_institution,
            normalize_email(dossier.email_institutional),
        )
        if email_inst and not is_valid_institutional_email(email_inst):
            errors.append(f"{dossier.display_name} : email institutionnel invalide, ignoré.")
            email_inst = ""

        enrollment_year = (dossier.academic_year or default_academic_year or "").strip()
        if not enrollment_year:
            errors.append(f"{dossier.display_name} : année universitaire manquante.")
            continue

        try:
            new_id = repo.add_student(
                "",
                dossier.student_number_ine,
                dossier.student_number_local,
                dossier.last_name,
                dossier.first_name,
                normalize_email(dossier.email_personal),
                email_inst,
                dossier.enrollment_institution,
                dossier.application_platform,
                "",
                "",
                dossier.notes,
                dossier.level or "M1",
                dossier.track,
                enrollment_year,
                birth_date=dossier.birth_date,
                nationality=dossier.nationality,
                birth_place=dossier.birth_place,
                gender=dossier.gender,
                origin_institution=dossier.origin_institution,
                origin_institution_country=dossier.origin_institution_country,
                highest_diploma=dossier.highest_diploma,
            )
            if dossier.extracted_photo is not None:
                tmp = save_extracted_photo_temp(dossier.extracted_photo)
                try:
                    repo.import_student_photo(new_id, tmp)
                finally:
                    tmp.unlink(missing_ok=True)
            repo.sync_enrollments_for_student(new_id)
            if attach_pdf:
                repo.add_student_attachment(
                    new_id,
                    "admission_dossier",
                    dossier.source_file,
                    label=f"Candidature {dossier.source}",
                )
            created += 1
            if ine:
                by_ine[ine] = {"id": new_id}
            by_name[name_key] = {"id": new_id}
        except Exception as exc:
            errors.append(f"{dossier.display_name} : {exc}")

    return created, skipped, errors
