"""Dialogue de programmation / modification d'un créneau emploi du temps."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..services.contact_emails import primary_email
from ..services.mailto_client import open_default_mail_client
from ..services.timetable_scheduling import suggest_next_available_slots
from .screen_layout import adapt_window_size


class TimetableSlotDialog(QDialog):
    def __init__(
        self,
        repo,
        *,
        import_id: int,
        academic_year: str,
        level: str,
        track: str,
        period: str,
        week_number: int,
        day_of_week: str,
        time_slot: str,
        existing_slot: dict[str, Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.import_id = int(import_id)
        self.academic_year = str(academic_year or "").strip()
        self.level = str(level or "M1").strip().upper()
        self.track = str(track or "P").strip().upper()
        self.period = str(period or "S1").strip().upper()
        self.week_number = int(week_number)
        self.day_of_week = str(day_of_week or "")
        self.time_slot = str(time_slot or "")
        self.existing_slot = existing_slot

        self.setWindowTitle("Programmer un créneau" if not existing_slot else "Modifier le créneau")
        root = QVBoxLayout(self)

        info = QLabel(
            f"<b>{self.day_of_week}</b> — semaine {self.week_number} — "
            f"<b>{self.time_slot}</b> — parcours {self.track} — {self.period}"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        form = QFormLayout()
        self.course_combo = QComboBox()
        self.course_combo.setMinimumWidth(360)
        courses = repo.list_courses_for_timetable_level(
            academic_year=self.academic_year, level=self.level
        )
        self.course_combo.addItem("— Choisir un cours —", None)
        for c in courses:
            mne = str(c.get("mne_module_code") or "").strip()
            label = f"{mne or c.get('code', '')} — {c.get('name', '')}"
            resp = " ".join(
                x
                for x in [
                    str(c.get("teacher_first_name") or "").strip(),
                    str(c.get("teacher_last_name") or "").strip(),
                ]
                if x
            )
            if resp:
                label += f" ({resp})"
            self.course_combo.addItem(label, int(c["id"]))
        self.course_combo.currentIndexChanged.connect(self._fill_suggestions)

        self.recurrence_spin = QSpinBox()
        self.recurrence_spin.setRange(1, 20)
        self.recurrence_spin.setValue(1)
        self.recurrence_spin.setToolTip(
            "Nombre de semaines consécutives (même jour / même créneau)."
        )
        form.addRow("Récurrence (semaines)", self.recurrence_spin)

        self.room_edit = QComboBox()
        self.room_edit.setEditable(True)
        self.room_edit.addItems(["", "Orsay", "INSTN", "ENSTA", "CentraleSupélec"])
        form.addRow("Lieu / salle", self.room_edit)
        root.addLayout(form)

        if existing_slot:
            cid = existing_slot.get("course_id")
            if cid:
                idx = self.course_combo.findData(int(cid))
                if idx >= 0:
                    self.course_combo.setCurrentIndex(idx)
            self.room_edit.setCurrentText(str(existing_slot.get("room") or ""))
            if int(existing_slot.get("is_cancelled") or 0):
                warn = QLabel("Ce créneau est annulé.")
                warn.setStyleSheet("color: #c62828;")
                root.addWidget(warn)

        sugg_box = QLabel("<b>Prochains créneaux disponibles</b> (tronc commun / spécialité)")
        root.addWidget(sugg_box)
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(140)
        root.addWidget(self.suggestions)
        self._fill_suggestions()

        btn_row = QHBoxLayout()
        self.mail_btn = QPushButton("E-mail responsable…")
        self.mail_btn.clicked.connect(self._mail_responsible)
        btn_row.addWidget(self.mail_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        buttons = QDialogButtonBox()
        if existing_slot:
            self.cancel_btn = buttons.addButton("Annuler le créneau", QDialogButtonBox.ButtonRole.DestructiveRole)
            self.cancel_btn.clicked.connect(self._cancel_slot)
            self.cancel_series_chk = QCheckBox("Toute la série récurrente")
            root.addWidget(self.cancel_series_chk)
        save_btn = buttons.addButton(QDialogButtonBox.StandardButton.Save)
        save_btn.clicked.connect(self._save)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        adapt_window_size(self, preferred=(560, 520), minimum=(480, 420))

    def _selected_course(self) -> dict[str, Any] | None:
        cid = self.course_combo.currentData()
        if cid is None:
            return None
        return self.repo.get_course(int(cid))

    def _fill_suggestions(self) -> None:
        self.suggestions.clear()
        course = self._selected_course()
        if not course:
            return
        mne = str(course.get("mne_module_code") or "")
        slots = self.repo.list_timetable_slots_for_period(
            self.import_id, track=self.track, period=self.period
        )
        for sug in suggest_next_available_slots(
            academic_year=self.academic_year,
            level=self.level,
            period=self.period,
            track=self.track,
            mne_module_code=mne,
            slots=slots,
            from_week_number=self.week_number,
            limit=10,
        ):
            it = QListWidgetItem(sug["label"])
            it.setData(Qt.ItemDataRole.UserRole, sug)
            self.suggestions.addItem(it)

    def _mail_responsible(self) -> None:
        course = self._selected_course()
        if not course and self.existing_slot and self.existing_slot.get("course_id"):
            course = self.repo.get_course(int(self.existing_slot["course_id"]))
        if not course:
            QMessageBox.information(self, "E-mail", "Sélectionnez d'abord un cours.")
            return
        email = primary_email(course, prefix="teacher")
        if not email:
            QMessageBox.warning(
                self,
                "E-mail",
                "Aucune adresse e-mail renseignée pour le responsable de ce cours.\n"
                "Complétez la fiche UE (onglet Cours).",
            )
            return
        name = str(course.get("name") or "")
        mne = str(course.get("mne_module_code") or "")
        subject = f"MNE — {mne or name} — emploi du temps"
        body = (
            f"Dear {course.get('teacher_first_name', '')} {course.get('teacher_last_name', '')},\n\n"
            f"Regarding the course {mne} — {name} "
            f"(week {self.week_number}, {self.day_of_week}, {self.time_slot}).\n\n"
            "Best regards,\n"
        )
        result = open_default_mail_client(
            to=[email],
            subject=subject,
            body=body,
            clipboard=QGuiApplication.clipboard(),
        )
        if not result.opened:
            QMessageBox.warning(self, "E-mail", result.message)

    def _save(self) -> None:
        course = self._selected_course()
        if not course:
            QMessageBox.warning(self, "Créneau", "Choisissez un cours de la maquette.")
            return
        try:
            self.repo.upsert_timetable_slot(
                import_id=self.import_id,
                level=self.level,
                track=self.track,
                period=self.period,
                week_number=self.week_number,
                day_of_week=self.day_of_week,
                time_slot=self.time_slot,
                course_id=int(course["id"]),
                room=self.room_edit.currentText().strip(),
                recurrence_weeks=int(self.recurrence_spin.value()),
                slot_id=int(self.existing_slot["id"]) if self.existing_slot else None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Chevauchement", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Créneau", str(exc))
            return
        self.accept()

    def _cancel_slot(self) -> None:
        if not self.existing_slot:
            return
        series = bool(getattr(self, "cancel_series_chk", None) and self.cancel_series_chk.isChecked())
        try:
            self.repo.cancel_timetable_slot(int(self.existing_slot["id"]), cancel_series=series)
        except Exception as exc:
            QMessageBox.critical(self, "Annulation", str(exc))
            return
        self.accept()
