"""Convocation d'examen : e-mail en anglais adressé aux étudiants + liste To/Bcc."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDate, QTime
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
)

from ..core.mne_modules import course_ue_code, lookup_mne_module, normalize_mne_module_code
from ..services.contact_emails import primary_email
from ..services.exam_convocation import (
    ConvocationParams,
    build_convocation_email,
    format_curricula_summary,
    format_exam_date_english,
)
from ..services.mailto_client import open_default_mail_client
from ..services.student_emails import parse_email_block


_EXAM_FORMATS: tuple[str, ...] = (
    "Written examination",
    "Oral examination",
    "Written + oral examination",
    "Continuous assessment (in-class)",
    "Practical examination",
    "Other (see notes)",
)


class ExamConvocationDialog(QDialog):
    def __init__(
        self,
        repo,
        *,
        course_id: int | None = None,
        academic_year: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self._default_year = (academic_year or "").strip()
        self._template_ids: list[int] = []
        self._templates: list[dict[str, Any]] = []
        self.setWindowTitle("Examination convocation (e-mail to students)")
        root = QVBoxLayout(self)
        hint = QLabel(
            "E-mail in English addressed to students. Recipients: all students enrolled in every "
            "curriculum (maquette) that contains the module (common courses merged, no duplicates). "
            "Use « Open in mail app » to compose the message in your default mail client "
            "(Outlook, Mail, Thunderbird… — works on Windows and macOS). Recipients are placed in Bcc."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        form = QFormLayout()

        self.year_combo = QComboBox()
        self.year_combo.setMinimumWidth(200)
        self.year_combo.currentIndexChanged.connect(self._on_scope_changed)
        form.addRow("Academic year :", self.year_combo)

        self.course_search = QLineEdit()
        self.course_search.setPlaceholderText("Search course / module… (code, MNE, title)")
        self.course_search.textChanged.connect(self._apply_course_filter)
        form.addRow("Search :", self.course_search)

        self.course_combo = QComboBox()
        self.course_combo.setMinimumWidth(360)
        self.course_combo.currentIndexChanged.connect(self._on_scope_changed)
        form.addRow("Course / module :", self.course_combo)

        self.templates_info = QLabel("—")
        self.templates_info.setWordWrap(True)
        form.addRow("Curricula concerned :", self.templates_info)

        self.student_count_label = QLabel("—")
        form.addRow("Students (merged) :", self.student_count_label)

        self.exam_date = QDateEdit()
        self.exam_date.setCalendarPopup(True)
        self.exam_date.setDate(QDate.currentDate())
        form.addRow("Exam date :", self.exam_date)

        time_row = QHBoxLayout()
        self.start_time = QTimeEdit()
        self.start_time.setDisplayFormat("HH:mm")
        self.start_time.setTime(QTime(9, 0))
        self.end_time = QTimeEdit()
        self.end_time.setDisplayFormat("HH:mm")
        self.end_time.setTime(QTime(12, 15))
        time_row.addWidget(QLabel("From"))
        time_row.addWidget(self.start_time)
        time_row.addWidget(QLabel("to"))
        time_row.addWidget(self.end_time)
        time_row.addStretch()
        form.addRow("Time :", time_row)

        self.location = QLineEdit("INSTN, CEA Saclay — Building 395 (room TBC)")
        form.addRow("Location :", self.location)

        self.exam_format = QComboBox()
        for f in _EXAM_FORMATS:
            self.exam_format.addItem(f)
        form.addRow("Exam format :", self.exam_format)

        self.session_spin = QSpinBox()
        self.session_spin.setRange(1, 2)
        self.session_spin.setValue(1)
        form.addRow("Exam session :", self.session_spin)

        self.extra_notes = QTextEdit()
        self.extra_notes.setPlaceholderText(
            "Materials allowed, registration instructions, online exam link, etc."
        )
        self.extra_notes.setMaximumHeight(72)
        form.addRow("Additional notes :", self.extra_notes)

        root.addLayout(form)

        gen_row = QHBoxLayout()
        self.generate_btn = QPushButton("Generate e-mail")
        self.generate_btn.clicked.connect(self._generate)
        self.open_mail_btn = QPushButton("Open in mail app")
        self.open_mail_btn.setToolTip(
            "Ouvre le client mail par défaut du système avec objet, corps et destinataires (Cci)."
        )
        self.open_mail_btn.clicked.connect(self._open_in_mail_app)
        gen_row.addWidget(self.generate_btn)
        gen_row.addWidget(self.open_mail_btn)
        gen_row.addStretch()
        root.addLayout(gen_row)

        root.addWidget(QLabel("Subject"))
        self.subject_edit = QLineEdit()
        self.subject_edit.setReadOnly(True)
        root.addWidget(self.subject_edit)

        root.addWidget(QLabel("E-mail body (to students)"))
        self.body_edit = QTextEdit()
        self.body_edit.setReadOnly(True)
        root.addWidget(self.body_edit, 2)

        root.addWidget(QLabel("Recipients — institutional e-mails (To / Bcc)"))
        self.recipients_edit = QTextEdit()
        self.recipients_edit.setReadOnly(True)
        self.recipients_edit.setMaximumHeight(100)
        root.addWidget(self.recipients_edit)

        copy_row = QHBoxLayout()
        self.copy_subject_btn = QPushButton("Copy subject")
        self.copy_subject_btn.clicked.connect(lambda: self._copy(self.subject_edit.text()))
        self.copy_body_btn = QPushButton("Copy message")
        self.copy_body_btn.clicked.connect(lambda: self._copy(self.body_edit.toPlainText()))
        self.copy_emails_btn = QPushButton("Copy recipients")
        self.copy_emails_btn.clicked.connect(lambda: self._copy(self._emails_block))
        copy_row.addWidget(self.copy_subject_btn)
        copy_row.addWidget(self.copy_body_btn)
        copy_row.addWidget(self.copy_emails_btn)
        copy_row.addStretch()
        root.addLayout(copy_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn:
            close_btn.clicked.connect(self.accept)
        root.addWidget(buttons)

        self._emails_block = ""
        self._recipient_emails: list[str] = []
        self._all_courses: list[dict[str, Any]] = []
        self._populate_years()
        self._populate_courses()
        if course_id is not None:
            idx = self.course_combo.findData(int(course_id))
            if idx >= 0:
                self.course_combo.setCurrentIndex(idx)
        self._on_scope_changed()

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(820, 720), minimum=(640, 520))

    def _populate_years(self) -> None:
        years = sorted(
            {str(t.get("academic_year") or "").strip() for t in self.repo.list_templates() if str(t.get("academic_year") or "").strip()},
            reverse=True,
        )
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for y in years:
            self.year_combo.addItem(y, y)
        if self._default_year:
            i = self.year_combo.findData(self._default_year)
            if i >= 0:
                self.year_combo.setCurrentIndex(i)
            else:
                self.year_combo.insertItem(0, self._default_year, self._default_year)
                self.year_combo.setCurrentIndex(0)
        elif self.year_combo.count():
            self.year_combo.setCurrentIndex(0)
        self.year_combo.blockSignals(False)

    def _populate_courses(self) -> None:
        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        self._all_courses = list(self.repo.list_courses())
        self._fill_course_combo(self._all_courses)
        self.course_combo.blockSignals(False)

    def _fill_course_combo(self, courses: list[dict[str, Any]]) -> None:
        self.course_combo.clear()
        for c in courses:
            cid = int(c["id"])
            mne = course_ue_code(c)
            code = str(c.get("code") or "")
            name = str(c.get("name") or "")
            head = f"{mne} — {name}" if mne else f"{code} — {name}"
            self.course_combo.addItem(head, cid)

    def _apply_course_filter(self) -> None:
        q = (self.course_search.text() or "").strip().lower()
        prev = self.course_combo.currentData()
        if not q:
            filtered = self._all_courses
        else:
            filtered = []
            for c in self._all_courses:
                bits = [
                    str(c.get("code") or ""),
                    str(course_ue_code(c) or ""),
                    str(c.get("name") or ""),
                ]
                hay = " ".join(bits).lower()
                if q in hay:
                    filtered.append(c)
        self.course_combo.blockSignals(True)
        self._fill_course_combo(filtered)
        if prev is not None:
            i = self.course_combo.findData(prev)
            if i >= 0:
                self.course_combo.setCurrentIndex(i)
        if self.course_combo.count() == 0:
            self.templates_info.setText("No course matches this filter.")
            self.student_count_label.setText("—")
        self.course_combo.blockSignals(False)
        self._on_scope_changed()

    def _selected_year(self) -> str:
        return str(self.year_combo.currentData() or "").strip()

    def _refresh_scope(self) -> tuple[int | None, list[dict[str, Any]], list[dict[str, Any]]]:
        cid = self.course_combo.currentData()
        if cid is None:
            self._template_ids = []
            self._templates = []
            return None, [], []

        ay = self._selected_year()
        templates = self.repo.list_templates_containing_course(int(cid), academic_year=ay)
        self._templates = templates
        self._template_ids = [int(t["id"]) for t in templates]

        students: list[dict[str, Any]] = []
        if self._template_ids:
            students = self.repo.list_students_for_course_in_templates(int(cid), self._template_ids)

        return int(cid), templates, students

    def _on_scope_changed(self) -> None:
        cid, templates, students = self._refresh_scope()
        if cid is None:
            self.templates_info.setText("—")
            self.student_count_label.setText("—")
            return

        if not templates:
            ay = self._selected_year()
            self.templates_info.setText(
                f"No curriculum includes this module"
                + (f" for {ay}." if ay else ". Add the course to a maquette first.")
            )
            self.student_count_label.setText("0 student(s)")
            return

        lines: list[str] = []
        for t in templates:
            lv = str(t.get("level") or "").strip()
            tr = str(t.get("track") or "").strip()
            suffix = f" ({lv} {tr})" if lv or tr else ""
            lines.append(f"• {t.get('name', '')}{suffix}")

        if len(templates) > 1:
            lines.append(
                f"\n→ Single convocation: {len(students)} student(s) across {len(templates)} curricula "
                f"(duplicates removed)."
            )
        else:
            lines.append(f"\n→ {len(students)} enrolled student(s).")

        self.templates_info.setText("\n".join(lines))
        self.student_count_label.setText(f"{len(students)} student(s) (unique, all curricula)")

    def _current_course(self) -> dict[str, Any] | None:
        cid = self.course_combo.currentData()
        if cid is None:
            return None
        return self.repo.get_course(int(cid))

    def _generate(self) -> None:
        cid, templates, students = self._refresh_scope()
        if cid is None:
            QMessageBox.warning(self, "Convocation", "Select a course.")
            return
        if not templates:
            QMessageBox.warning(
                self,
                "Convocation",
                "This module is not linked to any curriculum for the selected academic year.",
            )
            return
        if not students:
            QMessageBox.warning(
                self,
                "Convocation",
                "No enrolled students found across the concerned curricula.",
            )

        course = self._current_course() or {}
        ay = self._selected_year() or str(templates[0].get("academic_year") or "")

        mne = course_ue_code(course)
        mod = lookup_mne_module(mne) if mne else None
        title = str(course.get("name") or (mod.title if mod else "") or "")
        if not mne:
            QMessageBox.warning(
                self,
                "Convocation",
                "This course has no MNE module code (e.g. M1B1-C-NUCL). "
                "Set it in the course form so the e-mail subject is clear for students.",
            )

        teacher = " ".join(
            p
            for p in (
                str(course.get("teacher_first_name") or "").strip(),
                str(course.get("teacher_last_name") or "").strip(),
            )
            if p
        )

        curricula = format_curricula_summary(templates)

        params = ConvocationParams(
            academic_year=ay,
            mne_module_code=mne,
            course_title=title,
            exam_date=format_exam_date_english(
                self.exam_date.date().year(),
                self.exam_date.date().month(),
                self.exam_date.date().day(),
            ),
            start_time=self.start_time.time().toString("HH:mm"),
            end_time=self.end_time.time().toString("HH:mm"),
            location=self.location.text().strip(),
            exam_format=self.exam_format.currentText(),
            session=int(self.session_spin.value()),
            extra_notes=self.extra_notes.toPlainText().strip(),
            teacher_name=teacher,
            teacher_email=primary_email(course, prefix="teacher"),
            apogee_code=str(course.get("code") or ""),
            curricula_summary=curricula,
        )
        subject, body, emails_block = build_convocation_email(params, students)
        self._emails_block = emails_block
        self._recipient_emails = parse_email_block(emails_block)
        self.subject_edit.setText(subject)
        self.body_edit.setPlainText(body)
        self.recipients_edit.setPlainText(emails_block)

    def _open_in_mail_app(self) -> None:
        if not self.body_edit.toPlainText().strip():
            self._generate()
        subject = self.subject_edit.text().strip()
        body = self.body_edit.toPlainText().strip()
        if not body:
            QMessageBox.information(
                self,
                "Convocation",
                "Generate the e-mail first (select course, then Generate or Open in mail app).",
            )
            return
        if not self._recipient_emails:
            QMessageBox.warning(
                self,
                "Convocation",
                "No recipient e-mail on file. Check student records, then generate again.",
            )
            return

        result = open_default_mail_client(
            bcc=self._recipient_emails,
            subject=subject,
            body=body,
            clipboard=QGuiApplication.clipboard(),
        )
        if result.opened:
            QMessageBox.information(self, "Convocation", result.message)
        else:
            QMessageBox.warning(self, "Convocation", result.message)

    def _copy(self, text: str) -> None:
        if not text.strip():
            QMessageBox.information(self, "Copy", "Nothing to copy — generate the e-mail first.")
            return
        QGuiApplication.clipboard().setText(text)
