"""Notification par e-mail : transcript final, mention, classement et décision de jury (anglais)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from ..services.final_transcript_email import gather_jury_student_notification
from ..services.jury_reports import (
    find_success_certificate_pdf_in_dir,
    find_transcript_pdf_in_dir,
    is_final_transcript_pdf,
    is_provisional_transcript_pdf,
    student_eligible_for_success_certificate,
    success_certificate_default_filename,
    transcript_default_filename,
    write_institutional_transcript_pdf,
    write_success_certificate_pdf,
)
from ..services.mailto_client import open_default_mail_client
from .screen_layout import adapt_window_size


class FinalTranscriptEmailDialog(QDialog):
    def __init__(
        self,
        repo,
        *,
        template_id: int,
        jury_session_id: int,
        session_kind: str = "FINAL",
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.template_id = int(template_id)
        self.jury_session_id = int(jury_session_id)
        self.session_kind = str(session_kind or "FINAL").strip().upper()
        self._is_final = self.session_kind == "FINAL"
        self._view_session = "mixed" if self._is_final else ("s1" if self.session_kind == "S1" else "s2")
        self._transcript_view_session = "mixed"
        self._pdf_dir: Path | None = None
        self._pdf_by_student: dict[int, Path] = {}
        self._cert_by_student: dict[int, Path] = {}
        self._students: list[dict[str, Any]] = []
        self._current_row = -1

        tpl = repo.get_template(self.template_id) or {}
        self._tpl_meta = tpl
        title_bits = [
            str(tpl.get("level") or ""),
            str(tpl.get("track") or ""),
            str(tpl.get("academic_year") or ""),
        ]
        self.setWindowTitle(
            ("Final transcript e-mails" if self._is_final else f"{self.session_kind} jury e-mails")
            + " — "
            + " ".join(x for x in title_bits if x).strip()
        )

        root = QVBoxLayout(self)
        if self._is_final:
            hint_text = (
                "Send an individual e-mail in English to each student with their final average, "
                "honours (mention, in French), track and cohort rankings, and jury decision. "
                "Mention and track directors and the pedagogical secretariat are added in Cc. "
                "Generate the final transcript PDFs first (successful students also get a "
                "Certificate of Achievement), then open your mail client for each "
                "student — PDFs are attached automatically when possible (Mail on macOS, Outlook on Windows). "
                "Recipient: institutional e-mail, or personal if institutional is missing."
            )
        else:
            hint_text = (
                f"Send an individual e-mail in English to each student after the {self.session_kind} jury: "
                "average, courses to retake in the second session, and transcript PDF. "
                "Mention and track directors and the pedagogical secretariat are added in Cc. "
                "Generate transcript PDFs first, then open your mail client for each student "
                "(PDF attached automatically when possible). "
                "Recipient: institutional e-mail, or personal if institutional is missing."
            )
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Transcript PDF folder:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText(
            "Choose a folder, then generate PDFs for all students"
        )
        folder_row.addWidget(self.folder_edit, 1)
        self.pick_folder_btn = QPushButton("Choose folder…")
        self.pick_folder_btn.clicked.connect(self._pick_folder)
        self.gen_pdfs_btn = QPushButton(
            "Generate all final transcripts (+ certificates)"
            if self._is_final
            else "Generate all transcripts"
        )
        self.gen_pdfs_btn.clicked.connect(self._generate_all_pdfs)
        folder_row.addWidget(self.pick_folder_btn)
        folder_row.addWidget(self.gen_pdfs_btn)
        root.addLayout(folder_row)

        self.table = QTableWidget(0, 5)
        headers = ["Student", "E-mail", "PDF", "Honours", "Jury decision / retakes"]
        if not self._is_final:
            headers[3] = "—"
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        root.addWidget(self.table, 1)

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.clicked.connect(self._prev_student)
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(self._next_student)
        self.student_label = QLabel("—")
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        nav_row.addWidget(self.student_label, 1)
        root.addLayout(nav_row)

        mail_row = QHBoxLayout()
        self.open_mail_btn = QPushButton("Open in mail app (this student)")
        self.open_mail_btn.setToolTip(
            "Opens the default mail client with To, Cc, subject, body and transcript PDF prefilled."
        )
        self.open_mail_btn.clicked.connect(self._open_in_mail_app)
        self.open_pdf_btn = QPushButton("Show PDF in folder")
        self.open_pdf_btn.clicked.connect(self._reveal_pdf)
        self.refresh_btn = QPushButton("Refresh preview")
        self.refresh_btn.clicked.connect(self._refresh_preview)
        mail_row.addWidget(self.open_mail_btn)
        mail_row.addWidget(self.open_pdf_btn)
        mail_row.addWidget(self.refresh_btn)
        mail_row.addStretch()
        root.addLayout(mail_row)

        self.cc_label = QLabel("Cc (directors & pedagogical secretariat): —")
        self.cc_label.setWordWrap(True)
        self.cc_label.setStyleSheet("font-size: 11px; color: palette(mid);")
        root.addWidget(self.cc_label)

        root.addWidget(QLabel("Subject"))
        self.subject_edit = QLineEdit()
        self.subject_edit.setReadOnly(True)
        root.addWidget(self.subject_edit)

        root.addWidget(QLabel("E-mail body (English)"))
        self.body_edit = QTextEdit()
        self.body_edit.setReadOnly(True)
        root.addWidget(self.body_edit, 2)

        self.pdf_path_label = QLabel("PDF: —")
        self.pdf_path_label.setWordWrap(True)
        self.pdf_path_label.setStyleSheet("font-size: 11px; color: palette(mid);")
        root.addWidget(self.pdf_path_label)

        copy_row = QHBoxLayout()
        self.copy_subject_btn = QPushButton("Copy subject")
        self.copy_subject_btn.clicked.connect(lambda: self._copy(self.subject_edit.text()))
        self.copy_body_btn = QPushButton("Copy message")
        self.copy_body_btn.clicked.connect(lambda: self._copy(self.body_edit.toPlainText()))
        self.copy_email_btn = QPushButton("Copy recipient")
        self.copy_email_btn.clicked.connect(lambda: self._copy(self._current_email()))
        copy_row.addWidget(self.copy_subject_btn)
        copy_row.addWidget(self.copy_body_btn)
        copy_row.addWidget(self.copy_email_btn)
        copy_row.addStretch()
        root.addLayout(copy_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn:
            close_btn.clicked.connect(self.accept)
        root.addWidget(buttons)

        self._load_students()
        adapt_window_size(self, preferred=(900, 720), minimum=(720, 560))
        self._refresh_cc_label()

    def _gather(self, sid: int, pdf: Path | None = None, certificate: Path | None = None):
        return gather_jury_student_notification(
            self.repo,
            template_id=self.template_id,
            student_id=sid,
            jury_session_id=self.jury_session_id,
            session_kind=self.session_kind,
            pdf_path=pdf,
            view_session=self._view_session,
            certificate_path=certificate,
        )

    def _refresh_cc_label(self) -> None:
        cc = self.repo.jury_notification_cc_emails(self.template_id)
        if cc:
            self.cc_label.setText(
                "Cc (directors & pedagogical secretariat): " + ", ".join(cc)
            )
        else:
            self.cc_label.setText(
                "Cc (directors & pedagogical secretariat): none on file — "
                "fill in « Équipe pédagogique » for this academic year."
            )

    def _student_record(self, sid: int) -> dict[str, Any] | None:
        for stu in self._students:
            if int(stu.get("id") or 0) == int(sid):
                return stu
        return None

    def _sync_pdfs_from_folder(self) -> None:
        """Indexe les PDF déjà présents dans le dossier sélectionné (sans régénérer)."""
        if self._pdf_dir is None:
            return
        lv = str(self._tpl_meta.get("level") or "")
        tr = str(self._tpl_meta.get("track") or "")
        synced: dict[int, Path] = {}
        synced_cert: dict[int, Path] = {}
        for stu in self._students:
            sid = int(stu["id"])
            found = find_transcript_pdf_in_dir(
                self._pdf_dir, stu, level=lv, track=tr, final=self._is_final
            )
            if found is not None:
                synced[sid] = found
            if self._is_final:
                cert = find_success_certificate_pdf_in_dir(
                    self._pdf_dir, stu, level=lv, track=tr
                )
                if cert is not None:
                    synced_cert[sid] = cert
        self._pdf_by_student = synced
        self._cert_by_student = synced_cert

    def _resolve_student_pdf(self, sid: int) -> Path | None:
        """
        Chemin PDF à joindre : le dossier sélectionné fait foi (évite un cache périmé
        après changement de dossier ou mélange Provisional / Final).
        """
        stu = self._student_record(sid)
        if stu is None:
            return None
        lv = str(self._tpl_meta.get("level") or "")
        tr = str(self._tpl_meta.get("track") or "")

        if self._pdf_dir is not None:
            found = find_transcript_pdf_in_dir(
                self._pdf_dir, stu, level=lv, track=tr, final=self._is_final
            )
            if found is not None:
                self._pdf_by_student[sid] = found
                return found
            self._pdf_by_student.pop(sid, None)
            return None

        cached = self._pdf_by_student.get(sid)
        if cached is None or not cached.is_file():
            return None
        if self._is_final and is_provisional_transcript_pdf(cached):
            return None
        if not self._is_final and is_final_transcript_pdf(cached):
            return cached.resolve()
        return cached.resolve()

    def _resolve_student_certificate(self, sid: int) -> Path | None:
        if not self._is_final:
            return None
        stu = self._student_record(sid)
        if stu is None:
            return None
        lv = str(self._tpl_meta.get("level") or "")
        tr = str(self._tpl_meta.get("track") or "")
        if self._pdf_dir is not None:
            found = find_success_certificate_pdf_in_dir(
                self._pdf_dir, stu, level=lv, track=tr
            )
            if found is not None:
                self._cert_by_student[sid] = found
                return found
            self._cert_by_student.pop(sid, None)
            return None
        cached = self._cert_by_student.get(sid)
        if cached is None or not cached.is_file():
            return None
        return cached.resolve()

    def _load_students(self) -> None:
        self._students = self.repo.list_students_for_template(self.template_id)
        self._students.sort(
            key=lambda s: (
                str(s.get("last_name") or "").lower(),
                str(s.get("first_name") or "").lower(),
            )
        )
        self.table.setRowCount(len(self._students))
        for row, stu in enumerate(self._students):
            sid = int(stu["id"])
            name = f"{stu.get('last_name', '')} {stu.get('first_name', '')}".strip()
            pdf = self._resolve_student_pdf(sid)
            cert = self._resolve_student_certificate(sid)
            notif = self._gather(sid, pdf, cert)
            name_it = QTableWidgetItem(name)
            name_it.setData(Qt.ItemDataRole.UserRole, sid)
            self.table.setItem(row, 0, name_it)
            email_it = QTableWidgetItem(notif.email or "—")
            if not notif.has_email:
                email_it.setForeground(QBrush(QColor("#c62828")))
            self.table.setItem(row, 1, email_it)
            if pdf and cert:
                pdf_txt = "✓ + cert"
            elif pdf:
                pdf_txt = "✓"
            else:
                pdf_txt = "—"
            self.table.setItem(row, 2, QTableWidgetItem(pdf_txt))
            self.table.setItem(row, 3, QTableWidgetItem(notif.mention))
            self.table.setItem(row, 4, QTableWidgetItem(notif.jury_decision[:80]))
        if self._students:
            self.table.selectRow(0)

    def _student_id_at_row(self, row: int) -> int | None:
        if row < 0 or row >= self.table.rowCount():
            return None
        it = self.table.item(row, 0)
        if it is None:
            return None
        raw = it.data(Qt.ItemDataRole.UserRole)
        return int(raw) if raw is not None else None

    def _pick_folder(self) -> None:
        start = str(self._pdf_dir or Path.home())
        dest = QFileDialog.getExistingDirectory(self, "Folder for final transcript PDFs", start)
        if not dest:
            return
        self._pdf_dir = Path(dest)
        self.folder_edit.setText(str(self._pdf_dir))
        self._sync_pdfs_from_folder()
        self._load_students()
        if self._current_row >= 0:
            self.table.selectRow(self._current_row)
        elif self.table.rowCount():
            self.table.selectRow(0)

    def _generate_all_pdfs(self) -> None:
        if not self._check_reportlab():
            return
        if self._pdf_dir is None:
            self._pick_folder()
        if self._pdf_dir is None:
            return
        lv = str(self._tpl_meta.get("level") or "")
        tr = str(self._tpl_meta.get("track") or "")
        errors: list[str] = []
        self._pdf_by_student.clear()
        self._cert_by_student.clear()
        cert_count = 0
        for stu in self._students:
            sid = int(stu["id"])
            fname = transcript_default_filename(
                stu, level=lv, track=tr, final=self._is_final
            )
            out = self._pdf_dir / fname
            label = f"{stu.get('last_name', '')} {stu.get('first_name', '')}".strip()
            try:
                write_institutional_transcript_pdf(
                    self.repo,
                    template_id=self.template_id,
                    student_id=sid,
                    path=out,
                    final=self._is_final,
                    view_session=self._transcript_view_session,
                )
                self._pdf_by_student[sid] = out
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                continue
            if self._is_final and student_eligible_for_success_certificate(
                self.repo,
                template_id=self.template_id,
                student_id=sid,
                jury_session_id=self.jury_session_id,
            ):
                cert_name = success_certificate_default_filename(stu, level=lv, track=tr)
                cert_out = self._pdf_dir / cert_name
                try:
                    write_success_certificate_pdf(
                        self.repo,
                        template_id=self.template_id,
                        student_id=sid,
                        path=cert_out,
                        jury_session_id=self.jury_session_id,
                    )
                    self._cert_by_student[sid] = cert_out
                    cert_count += 1
                except Exception as exc:
                    errors.append(f"{label} (certificate): {exc}")
        self._load_students()
        if self._current_row >= 0:
            self.table.selectRow(self._current_row)
        msg = f"{len(self._pdf_by_student)} transcript PDF(s) generated in:\n{self._pdf_dir}"
        if self._is_final:
            msg += f"\n{cert_count} Certificate of Achievement PDF(s) (successful students only)."
        if errors:
            preview = "\n".join(f"• {e}" for e in errors[:6])
            if len(errors) > 6:
                preview += f"\n… and {len(errors) - 6} more error(s)"
            msg += f"\n\n{len(errors)} failure(s):\n{preview}"
        QMessageBox.information(self, "Transcripts", msg)

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self._current_row = rows[0].row()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        sid = self._student_id_at_row(self._current_row)
        if sid is None:
            self.subject_edit.clear()
            self.body_edit.clear()
            self.student_label.setText("—")
            self.pdf_path_label.setText("PDF: —")
            return
        pdf = self._resolve_student_pdf(sid)
        cert = self._resolve_student_certificate(sid)
        notif = self._gather(sid, pdf, cert)
        self.subject_edit.setText(notif.subject)
        self.body_edit.setPlainText(notif.body)
        self.student_label.setText(
            f"{notif.student_name}"
            + (f" — {notif.email}" if notif.email else " — no e-mail on file")
        )
        bits: list[str] = []
        if pdf and pdf.is_file():
            bits.append(f"Transcript: {pdf.name}")
        else:
            gen_label = (
                "Generate all final transcripts (+ certificates)"
                if self._is_final
                else "Generate all transcripts"
            )
            bits.append(f"Transcript: not generated yet — use « {gen_label} »")
        if self._is_final:
            if cert and cert.is_file():
                bits.append(f"Certificate: {cert.name}")
            elif student_eligible_for_success_certificate(
                self.repo,
                template_id=self.template_id,
                student_id=sid,
                jury_session_id=self.jury_session_id,
            ):
                bits.append("Certificate: not generated yet")
            else:
                bits.append("Certificate: not applicable (no success decision)")
        self.pdf_path_label.setText("\n".join(bits))

    def _current_email(self) -> str:
        sid = self._student_id_at_row(self._current_row)
        if sid is None:
            return ""
        return self._gather(
            sid,
            self._resolve_student_pdf(sid),
            self._resolve_student_certificate(sid),
        ).email

    def _open_in_mail_app(self) -> None:
        sid = self._student_id_at_row(self._current_row)
        if sid is None:
            QMessageBox.information(self, "E-mail", "Select a student in the table.")
            return
        pdf = self._resolve_student_pdf(sid)
        if pdf is None:
            if self._is_final:
                QMessageBox.warning(
                    self,
                    "E-mail",
                    "No final transcript PDF found for this student in the selected folder.\n"
                    "Choose the folder that contains the « Final Transcript » files, "
                    "or use « Generate all final transcripts (+ certificates) ».",
                )
            else:
                QMessageBox.warning(
                    self,
                    "E-mail",
                    "No transcript PDF found for this student in the selected folder.\n"
                    "Choose a folder or generate transcripts first.",
                )
            return
        cert = self._resolve_student_certificate(sid)
        notif = self._gather(sid, pdf, cert)
        if not notif.body.strip():
            QMessageBox.information(self, "E-mail", "Nothing to send — select a student.")
            return
        if not notif.has_email:
            QMessageBox.warning(
                self,
                "E-mail",
                f"No e-mail address for {notif.student_name}. "
                "Update the student record (institutional or personal e-mail), then refresh.",
            )
            return
        attachments = [pdf]
        if cert is not None and cert.is_file():
            attachments.append(cert)
        result = open_default_mail_client(
            to=[notif.email],
            cc=notif.cc_emails or None,
            subject=notif.subject,
            body=notif.body,
            attachments=attachments,
            clipboard=QGuiApplication.clipboard(),
        )
        if result.opened:
            names = ", ".join(p.name for p in attachments)
            extra = f"\n\nAttached: {names}"
            if not result.attachment_paths:
                extra += (
                    "\n\nThe PDF path was copied to the clipboard — "
                    "attach it manually if it is missing from the draft."
                )
            if notif.cc_emails:
                extra += f"\n\nCc: {', '.join(notif.cc_emails)}"
            QMessageBox.information(self, "E-mail", result.message + extra)
        else:
            QMessageBox.warning(self, "E-mail", result.message)

    def _reveal_pdf(self) -> None:
        sid = self._student_id_at_row(self._current_row)
        if sid is None:
            return
        pdf = self._resolve_student_pdf(sid)
        if pdf is None or not pdf.is_file():
            QMessageBox.information(
                self,
                "PDF",
                "No PDF for this student yet. Choose a folder and generate transcripts first.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf.parent)))

    def _prev_student(self) -> None:
        if self.table.rowCount() == 0:
            return
        row = max(0, self._current_row - 1) if self._current_row >= 0 else 0
        self.table.selectRow(row)

    def _next_student(self) -> None:
        if self.table.rowCount() == 0:
            return
        row = min(self.table.rowCount() - 1, self._current_row + 1) if self._current_row >= 0 else 0
        self.table.selectRow(row)

    def _copy(self, text: str) -> None:
        if not str(text or "").strip():
            QMessageBox.information(self, "Copy", "Nothing to copy.")
            return
        QGuiApplication.clipboard().setText(text)

    def _check_reportlab(self) -> bool:
        try:
            import reportlab  # noqa: F401
        except ImportError:
            QMessageBox.warning(
                self,
                "Dependency",
                "The « reportlab » module is required for PDF transcripts.\n"
                "Install it: pip install reportlab",
            )
            return False
        return True
