"""Édition et copie d'une liste d'adresses e-mail (étudiants filtrés ou sélectionnés)."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services.mailto_client import open_default_mail_client
from ..services.student_emails import (
    EMAIL_FORMAT_COMMA,
    EMAIL_FORMAT_LINES,
    EMAIL_FORMAT_SEMICOLON,
    EMAIL_MODE_BOTH,
    EMAIL_MODE_INSTITUTIONAL,
    EMAIL_MODE_INST_OR_PERSONAL,
    EMAIL_MODE_PERSONAL,
    build_student_email_list,
    format_email_block,
)


class StudentEmailListDialog(QDialog):
    def __init__(
        self,
        *,
        filtered_students: list[dict[str, Any]],
        selected_students: list[dict[str, Any]],
        parent=None,
    ):
        super().__init__(parent)
        self._filtered = list(filtered_students)
        self._selected = list(selected_students)
        self.setWindowTitle("Mailing liste — e-mails étudiants")
        root = QVBoxLayout(self)

        hint = QLabel(
            "Composez une liste d'adresses à partir des étudiants affichés ou de la sélection. "
            "Renseignez objet et message, puis ouvrez votre client mail (destinataires en Cci) "
            "ou copiez la liste pour un envoi manuel."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        form = QFormLayout()

        scope_row = QWidget()
        scope_lay = QHBoxLayout(scope_row)
        scope_lay.setContentsMargins(0, 0, 0, 0)
        self.scope_filtered = QRadioButton(
            f"Liste filtrée ({len(self._filtered)} étudiant(s))"
        )
        self.scope_selected = QRadioButton(
            f"Sélection uniquement ({len(self._selected)} étudiant(s))"
        )
        self.scope_filtered.setChecked(True)
        if not self._selected:
            self.scope_selected.setEnabled(False)
        scope_lay.addWidget(self.scope_filtered)
        scope_lay.addWidget(self.scope_selected)
        scope_lay.addStretch()
        form.addRow("Périmètre :", scope_row)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Institutionnel", EMAIL_MODE_INSTITUTIONAL)
        self.mode_combo.addItem("Personnel", EMAIL_MODE_PERSONAL)
        self.mode_combo.addItem("Institutionnel, sinon personnel", EMAIL_MODE_INST_OR_PERSONAL)
        self.mode_combo.addItem("Les deux (si différents)", EMAIL_MODE_BOTH)
        self.mode_combo.setCurrentIndex(2)
        form.addRow("Adresses :", self.mode_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItem("Une adresse par ligne", EMAIL_FORMAT_LINES)
        self.format_combo.addItem("Séparées par des points-virgules", EMAIL_FORMAT_SEMICOLON)
        self.format_combo.addItem("Séparées par des virgules", EMAIL_FORMAT_COMMA)
        form.addRow("Format :", self.format_combo)

        root.addLayout(form)

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Objet du message (optionnel)")
        form.addRow("Objet :", self.subject_edit)

        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("Corps du message (optionnel)")
        self.body_edit.setMaximumHeight(120)
        form.addRow("Message :", self.body_edit)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        self.emails_edit = QTextEdit()
        self.emails_edit.setPlaceholderText("Les adresses apparaîtront ici…")
        self.emails_edit.textChanged.connect(self._update_summary_from_editor)
        root.addWidget(self.emails_edit, 1)

        action_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Actualiser")
        self.refresh_btn.clicked.connect(self._refresh_list)
        self.open_mail_btn = QPushButton("Ouvrir dans le client mail (Cci)")
        self.open_mail_btn.setToolTip(
            "Ouvre le client mail par défaut avec les adresses en Cci, l'objet et le message."
        )
        self.open_mail_btn.clicked.connect(self._open_in_mail_app)
        self.copy_btn = QPushButton("Copier la liste")
        self.copy_btn.clicked.connect(self._copy)
        action_row.addWidget(self.refresh_btn)
        action_row.addWidget(self.open_mail_btn)
        action_row.addWidget(self.copy_btn)
        action_row.addStretch()
        root.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn:
            close_btn.clicked.connect(self.accept)
        root.addWidget(buttons)

        self.scope_filtered.toggled.connect(self._refresh_list)
        self.scope_selected.toggled.connect(self._refresh_list)
        self.mode_combo.currentIndexChanged.connect(self._refresh_list)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)

        self._refresh_list()

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(720, 560), minimum=(520, 420))

    def _current_students(self) -> list[dict[str, Any]]:
        if self.scope_selected.isEnabled() and self.scope_selected.isChecked():
            return self._selected
        return self._filtered

    def _emails_from_editor(self) -> list[str]:
        return parse_email_block(self.emails_edit.toPlainText())

    def _update_summary_from_editor(self) -> None:
        emails = self._emails_from_editor()
        students_n = len(self._current_students())
        if emails:
            self.summary_label.setText(
                f"{len(emails)} adresse(s) dans la liste ({students_n} étudiant(s) dans le périmètre)."
            )
        elif students_n:
            self.summary_label.setText(
                f"{students_n} étudiant(s) dans le périmètre — actualisez pour générer les adresses."
            )

    def _refresh_list(self) -> None:
        students = self._current_students()
        mode = str(self.mode_combo.currentData() or EMAIL_MODE_INST_OR_PERSONAL)
        emails, missing = build_student_email_list(students, mode)
        fmt = str(self.format_combo.currentData() or EMAIL_FORMAT_LINES)
        self.emails_edit.blockSignals(True)
        self.emails_edit.setPlainText(format_email_block(emails, fmt))
        self.emails_edit.blockSignals(False)
        if not students:
            self.summary_label.setText("Aucun étudiant dans ce périmètre.")
        elif not emails:
            self.summary_label.setText(
                f"{len(students)} étudiant(s) : aucune adresse trouvée avec ce critère."
            )
        elif missing:
            names = ", ".join(
                f"{s.get('last_name', '')} {s.get('first_name', '')}".strip() for s in missing[:8]
            )
            extra = f" (+{len(missing) - 8})" if len(missing) > 8 else ""
            self.summary_label.setText(
                f"{len(emails)} adresse(s) pour {len(students) - len(missing)} étudiant(s). "
                f"Sans adresse : {names}{extra}."
            )
        else:
            self._update_summary_from_editor()

    def _on_format_changed(self) -> None:
        students = self._current_students()
        mode = str(self.mode_combo.currentData() or EMAIL_MODE_INST_OR_PERSONAL)
        emails, _ = build_student_email_list(students, mode)
        fmt = str(self.format_combo.currentData() or EMAIL_FORMAT_LINES)
        self.emails_edit.blockSignals(True)
        self.emails_edit.setPlainText(format_email_block(emails, fmt))
        self.emails_edit.blockSignals(False)
        self._update_summary_from_editor()

    def _open_in_mail_app(self) -> None:
        emails = self._emails_from_editor()
        if not emails:
            QMessageBox.information(
                self,
                "E-mail",
                "Aucune adresse dans la liste. Actualisez ou saisissez des adresses.",
            )
            return
        subject = self.subject_edit.text().strip()
        body = self.body_edit.toPlainText().strip()
        result = open_default_mail_client(
            bcc=emails,
            subject=subject,
            body=body,
            clipboard=QGuiApplication.clipboard(),
        )
        if result.opened:
            QMessageBox.information(self, "E-mail", result.message)
        else:
            QMessageBox.warning(self, "E-mail", result.message)

    def _copy(self) -> None:
        text = self.emails_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Copier", "La liste est vide.")
            return
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copier", "Liste copiée dans le presse-papiers.")
