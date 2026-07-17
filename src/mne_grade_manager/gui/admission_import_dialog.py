"""Prévisualisation et import de dossiers de candidature PDF."""

from __future__ import annotations

from pathlib import Path
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

from ..services.admission_import import (
    AdmissionDossier,
    build_existing_student_indexes,
    find_existing_student,
    link_existing_students,
    normalize_admission_level_track,
)
from ..services.admission_photo import save_extracted_photo_temp
from ..services.lookups import adapt_institutional_email, is_valid_institutional_email, normalize_email


class AdmissionImportDialog(QDialog):
    def __init__(
        self,
        dossiers: list[AdmissionDossier],
        *,
        repo: Any,
        default_academic_year: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Import dossiers de candidature")
        self.resize(1320, 580)
        self._dossiers = dossiers
        self._row_checks: list[QCheckBox] = []
        link_existing_students(self._dossiers, repo.list_students(include_withdrawn=True))

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

        self.update_existing = QCheckBox(
            "Mettre à jour les fiches existantes (INE, nom/prénom ou email identique)"
        )
        self.update_existing.setChecked(True)
        layout.addWidget(self.update_existing)

        self.table = QTableWidget()
        self.table.setColumnCount(16)
        self.table.setHorizontalHeaderLabels(
            [
                "Importer",
                "Action",
                "Photo",
                "Source",
                "Fichier",
                "Nom",
                "Prénom",
                "INE",
                "N° établ.",
                "Niveau",
                "Parcours",
                "Année",
                "Inscription",
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

            if dossier.has_existing_match:
                action = f"Mise à jour (#{dossier.existing_student_id})"
                if dossier.existing_match_reason:
                    action += f" — {dossier.existing_match_reason}"
            else:
                action = "Nouvelle fiche"
            self._set_item(row, 1, action)
            self._set_item(row, 2, "Oui" if dossier.photo_found else "Non")
            self._set_item(row, 3, dossier.source)
            self._set_item(row, 4, _basename(dossier.source_file))
            self._set_item(row, 5, dossier.last_name)
            self._set_item(row, 6, dossier.first_name)
            self._set_item(row, 7, dossier.student_number_ine)
            self._set_item(row, 8, dossier.student_number_local)
            self._set_item(row, 9, dossier.level)
            self._set_item(row, 10, dossier.track)
            self._set_item(row, 11, dossier.academic_year)
            self._set_item(row, 12, dossier.enrollment_institution)
            self._set_item(row, 13, dossier.monmaster_channel or "—")
            self._set_item(row, 14, dossier.email_personal or dossier.email_institutional)
            warn = dossier.parse_error or "; ".join(dossier.warnings)
            self._set_item(row, 15, warn)

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

    def should_update_existing(self) -> bool:
        return self.update_existing.isChecked()


def _basename(path: str) -> str:
    return Path(path).name


def _pick(new: str, old: str) -> str:
    return str(new or "").strip() or str(old or "").strip()


def _admission_pdf_already_attached(repo: Any, student_id: int, source_file: str) -> bool:
    src_name = Path(source_file).name
    for att in repo.list_student_attachments(int(student_id), category="admission_dossier"):
        if str(att.get("original_filename") or "").strip() == src_name:
            return True
        label = str(att.get("label") or "")
        if src_name and src_name in label:
            return True
    return False


def _persist_admission_dossier(
    repo: Any,
    dossier: AdmissionDossier,
    *,
    student_id: int | None,
    enrollment_year: str,
    attach_pdf: bool,
) -> int:
    email_inst = adapt_institutional_email(
        dossier.first_name,
        dossier.last_name,
        dossier.enrollment_institution,
        normalize_email(dossier.email_institutional),
    )
    if email_inst and not is_valid_institutional_email(email_inst):
        email_inst = ""

    level, track = normalize_admission_level_track(dossier.level, dossier.track)
    existing = repo.get_student(int(student_id)) if student_id else None

    if existing:
        sid = int(student_id)
        repo.update_student(
            sid,
            str(existing.get("student_number") or ""),
            _pick(dossier.student_number_ine, str(existing.get("student_number_ine") or "")),
            _pick(dossier.student_number_local, str(existing.get("student_number_local") or "")),
            dossier.last_name,
            dossier.first_name,
            email_personal=_pick(normalize_email(dossier.email_personal), str(existing.get("email_personal") or "")),
            email_institutional=_pick(email_inst, str(existing.get("email_institutional") or "")),
            phone=str(existing.get("phone") or ""),
            enrollment_institution=_pick(
                dossier.enrollment_institution, str(existing.get("enrollment_institution") or "")
            ),
            application_platform=_pick(
                dossier.application_platform, str(existing.get("application_platform") or "")
            ),
            accommodations=str(existing.get("accommodations") or ""),
            accommodations_other=str(existing.get("accommodations_other") or ""),
            notes=_pick(dossier.notes, str(existing.get("notes") or "")),
            level=level or str(existing.get("level") or "").strip(),
            track=track or str(existing.get("track") or "").strip(),
            academic_year=enrollment_year or str(existing.get("academic_year") or "").strip(),
            birth_date=_pick(dossier.birth_date, str(existing.get("birth_date") or "")),
            nationality=_pick(dossier.nationality, str(existing.get("nationality") or "")),
            birth_place=_pick(dossier.birth_place, str(existing.get("birth_place") or "")),
            gender=_pick(dossier.gender, str(existing.get("gender") or "")),
            origin_institution=_pick(
                dossier.origin_institution, str(existing.get("origin_institution") or "")
            ),
            origin_institution_country=_pick(
                dossier.origin_institution_country,
                str(existing.get("origin_institution_country") or ""),
            ),
            highest_diploma=_pick(dossier.highest_diploma, str(existing.get("highest_diploma") or "")),
        )
    else:
        sid = repo.add_student(
            "",
            dossier.student_number_ine,
            dossier.student_number_local,
            dossier.last_name,
            dossier.first_name,
            email_personal=normalize_email(dossier.email_personal),
            email_institutional=email_inst,
            phone="",
            enrollment_institution=dossier.enrollment_institution,
            application_platform=dossier.application_platform,
            accommodations="",
            accommodations_other="",
            notes=dossier.notes,
            level=level,
            track=track,
            academic_year=enrollment_year,
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
            repo.import_student_photo(sid, tmp)
        finally:
            tmp.unlink(missing_ok=True)

    repo.sync_enrollments_for_student(sid)

    if attach_pdf and not _admission_pdf_already_attached(repo, sid, dossier.source_file):
        repo.add_student_attachment(
            sid,
            "admission_dossier",
            dossier.source_file,
            label=f"Candidature {dossier.source}",
        )
    return sid


def import_admission_dossiers(
    repo: Any,
    dossiers: list[AdmissionDossier],
    *,
    default_academic_year: str = "",
    attach_pdf: bool = True,
    update_existing: bool = True,
) -> tuple[int, int, int, list[str]]:
    """Crée ou met à jour les étudiants. Retourne (créés, mis à jour, ignorés, erreurs)."""
    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    by_ine, by_name, by_email = build_existing_student_indexes(repo.list_students(include_withdrawn=True))

    for dossier in dossiers:
        if not dossier.importable:
            skipped += 1
            continue

        enrollment_year = (dossier.academic_year or default_academic_year or "").strip()
        if not enrollment_year:
            errors.append(f"{dossier.display_name} : année universitaire manquante.")
            continue

        existing_id = dossier.existing_student_id
        if existing_id is None:
            match, reason = find_existing_student(
                dossier, by_ine=by_ine, by_name=by_name, by_email=by_email
            )
            if match:
                existing_id = int(match["id"])
                dossier.existing_match_reason = reason

        if existing_id is not None and not update_existing:
            skipped += 1
            detail = dossier.existing_match_reason or "correspondance en base"
            errors.append(f"{dossier.display_name} : déjà en base ({detail}).")
            continue

        try:
            sid = _persist_admission_dossier(
                repo,
                dossier,
                student_id=existing_id,
                enrollment_year=enrollment_year,
                attach_pdf=attach_pdf,
            )
            row = repo.get_student(sid) or {}
            ine = str(dossier.student_number_ine or "").strip().upper()
            if ine:
                by_ine[ine] = row
            name_key = (
                str(dossier.last_name or "").strip().upper(),
                str(dossier.first_name or "").strip().upper(),
            )
            if name_key[0] and name_key[1]:
                by_name[name_key] = row
            for em in (
                normalize_email(dossier.email_personal),
                normalize_email(dossier.email_institutional),
            ):
                if em:
                    by_email[em.lower()] = row

            if existing_id is not None:
                updated += 1
            else:
                created += 1
        except Exception as exc:
            errors.append(f"{dossier.display_name} : {exc}")

    return created, updated, skipped, errors
