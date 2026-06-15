"""Dossier stage : suivi, encadrant, convention PDF."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
)

from ..core.institutions import INTERNSHIP_STATUS_CHOICES
from ..services.dates import normalize_birth_date_iso, normalize_time_hhmm
from ..services.attachments import abs_path_from_stored

if TYPE_CHECKING:
    from ..services.repository import Repository


class InternshipDialog(QDialog):
    def __init__(
        self,
        repo: Repository,
        *,
        student_id: int,
        template_id: int,
        course_id: int,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.student_id = int(student_id)
        self.template_id = int(template_id)
        self.course_id = int(course_id)
        self.setWindowTitle("Dossier stage")
        self.setMinimumWidth(520)

        course = repo.get_course(self.course_id) or {}
        st = repo.get_student(self.student_id) or {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"<b>{st.get('last_name', '')} {st.get('first_name', '')}</b> — "
                f"{course.get('code', '')} {course.get('name', '')}"
            )
        )

        form = QFormLayout()
        self.status_combo = QComboBox()
        for key, label in INTERNSHIP_STATUS_CHOICES:
            self.status_combo.addItem(label, key)
        self.topic = QTextEdit()
        self.topic.setPlaceholderText("Sujet du stage")
        self.topic.setMaximumHeight(72)
        self.sup_last = QLineEdit()
        self.sup_first = QLineEdit()
        self.sup_email = QLineEdit()
        self.sup_institution = QLineEdit()
        self.sup_phone = QLineEdit()
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(64)

        form.addRow("Suivi", self.status_combo)
        form.addRow("Sujet", self.topic)
        form.addRow("Encadrant — nom", self.sup_last)
        form.addRow("Encadrant — prénom", self.sup_first)
        form.addRow("Encadrant — email", self.sup_email)
        form.addRow("Encadrant — établissement", self.sup_institution)
        form.addRow("Encadrant — téléphone", self.sup_phone)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)

        defense_box = QGroupBox("Soutenance")
        df = QFormLayout(defense_box)
        self.defense_date = QDateEdit()
        self.defense_date.setCalendarPopup(True)
        self.defense_date.setDisplayFormat("yyyy-MM-dd")
        self.defense_date.setSpecialValueText("—")
        self.defense_date.setDate(QDate(2000, 1, 1))
        self.defense_date.setMinimumDate(QDate(2000, 1, 1))
        self.defense_time = QTimeEdit()
        self.defense_time.setDisplayFormat("HH:mm")
        self.reporter_last = QLineEdit()
        self.reporter_first = QLineEdit()
        self.reporter_institution = QLineEdit()
        df.addRow("Date de soutenance", self.defense_date)
        df.addRow("Heure", self.defense_time)
        df.addRow("Rapporteur — nom", self.reporter_last)
        df.addRow("Rapporteur — prénom", self.reporter_first)
        df.addRow("Rapporteur — établissement", self.reporter_institution)
        layout.addWidget(defense_box)

        self.paper_convention_cb = QCheckBox(
            "Convention papier archivée (version physique en dossier)"
        )
        self.paper_convention_cb.toggled.connect(self._refresh_convention_label)
        layout.addWidget(self.paper_convention_cb)

        conv_row = QHBoxLayout()
        self.conv_label = QLabel("Aucune convention PDF")
        self.conv_upload_btn = QPushButton("Importer convention (PDF)…")
        self.conv_upload_btn.clicked.connect(self._upload_convention)
        self.conv_open_btn = QPushButton("Ouvrir")
        self.conv_open_btn.clicked.connect(self._open_convention)
        self.conv_clear_btn = QPushButton("Retirer")
        self.conv_clear_btn.clicked.connect(self._clear_convention)
        conv_row.addWidget(self.conv_label, 1)
        conv_row.addWidget(self.conv_upload_btn)
        conv_row.addWidget(self.conv_open_btn)
        conv_row.addWidget(self.conv_clear_btn)
        layout.addLayout(conv_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._convention_path = ""
        rec = repo.get_internship_record(self.student_id, self.template_id, self.course_id)
        if rec:
            idx = self.status_combo.findData(str(rec.get("follow_up_status") or ""))
            if idx >= 0:
                self.status_combo.setCurrentIndex(idx)
            self.topic.setPlainText(str(rec.get("topic") or ""))
            self.sup_last.setText(str(rec.get("supervisor_last_name") or ""))
            self.sup_first.setText(str(rec.get("supervisor_first_name") or ""))
            self.sup_email.setText(str(rec.get("supervisor_email") or ""))
            self.sup_institution.setText(str(rec.get("supervisor_institution") or ""))
            self.sup_phone.setText(str(rec.get("supervisor_phone") or ""))
            self.notes.setPlainText(str(rec.get("notes") or ""))
            self._convention_path = str(rec.get("convention_path") or "")
            self.paper_convention_cb.setChecked(bool(int(rec.get("convention_paper") or 0)))
            d_iso = normalize_birth_date_iso(str(rec.get("defense_date") or ""))
            if d_iso:
                y, m, d = (int(x) for x in d_iso.split("-"))
                self.defense_date.setDate(QDate(y, m, d))
            else:
                self.defense_date.setDate(QDate(2000, 1, 1))
            t_hm = normalize_time_hhmm(str(rec.get("defense_time") or ""))
            if t_hm:
                h, mi = (int(x) for x in t_hm.split(":"))
                self.defense_time.setTime(QTime(h, mi))
            self.reporter_last.setText(str(rec.get("reporter_last_name") or ""))
            self.reporter_first.setText(str(rec.get("reporter_first_name") or ""))
            self.reporter_institution.setText(str(rec.get("reporter_institution") or ""))
        self._refresh_convention_label()

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(640, 720), minimum=(480, 420))

    def _defense_date_value(self) -> str:
        if self.defense_date.date() == QDate(2000, 1, 1):
            return ""
        return self.defense_date.date().toString("yyyy-MM-dd")

    def _defense_time_value(self) -> str:
        return self.defense_time.time().toString("HH:mm")

    def _refresh_convention_label(self) -> None:
        parts: list[str] = []
        if self.paper_convention_cb.isChecked():
            parts.append("papier")
        if self._convention_path:
            name = Path(abs_path_from_stored(self._convention_path).name).name
            parts.append(f"PDF : {name}")
        if parts:
            self.conv_label.setText("Convention — " + " · ".join(parts))
            self.conv_open_btn.setEnabled(bool(self._convention_path))
            self.conv_clear_btn.setEnabled(bool(self._convention_path))
        else:
            self.conv_label.setText("Aucune convention renseignée")
            self.conv_open_btn.setEnabled(False)
            self.conv_clear_btn.setEnabled(False)

    def _upload_convention(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Convention de stage", str(Path.home()), "PDF (*.pdf)")
        if not path:
            return
        try:
            self._convention_path = self.repo.import_internship_convention(
                self.student_id, self.template_id, self.course_id, path
            )
            self._refresh_convention_label()
        except Exception as exc:
            QMessageBox.critical(self, "Convention", str(exc))

    def _open_convention(self) -> None:
        if not self._convention_path:
            return
        p = abs_path_from_stored(self._convention_path)
        if not p.is_file():
            QMessageBox.warning(self, "Convention", "Fichier introuvable.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["start", "", str(p)], shell=True, check=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False)
        except Exception as exc:
            QMessageBox.warning(self, "Convention", str(exc))

    def _clear_convention(self) -> None:
        if QMessageBox.question(self, "Confirmer", "Retirer la convention ?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repo.clear_internship_convention(self.student_id, self.template_id, self.course_id)
            self._convention_path = ""
            self._refresh_convention_label()
        except Exception as exc:
            QMessageBox.critical(self, "Convention", str(exc))

    def _save(self) -> None:
        try:
            self.repo.upsert_internship_record(
                self.student_id,
                self.template_id,
                self.course_id,
                topic=self.topic.toPlainText(),
                supervisor_last_name=self.sup_last.text(),
                supervisor_first_name=self.sup_first.text(),
                supervisor_email=self.sup_email.text(),
                supervisor_institution=self.sup_institution.text(),
                supervisor_phone=self.sup_phone.text(),
                follow_up_status=str(self.status_combo.currentData() or ""),
                notes=self.notes.toPlainText(),
                convention_path=self._convention_path,
                convention_paper=self.paper_convention_cb.isChecked(),
                reporter_last_name=self.reporter_last.text(),
                reporter_first_name=self.reporter_first.text(),
                reporter_institution=self.reporter_institution.text(),
                defense_date=self._defense_date_value(),
                defense_time=self._defense_time_value(),
            )
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Stage", str(exc))
