"""Fiche étudiant en lecture seule (aperçu rapide depuis la liste)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.institutions import PEDAGOGICAL_CONTRACT_CATEGORY, STUDENT_ATTACHMENT_CATEGORIES
from ..core.parcours import track_label
from ..services.attachments import abs_path_from_stored
from ..services.dates import format_age_display
from ..services.lookups import gender_label_fr
from .dialogs import StudentDialog
from .student_internships_panel import StudentInternshipsPanel


def _field(label: str, value: str) -> tuple[str, str]:
    v = (value or "").strip()
    return label, v if v else "—"


class StudentProfileDialog(QDialog):
    def __init__(self, repo, student_id: int, *, parent=None, default_academic_year: str = ""):
        super().__init__(parent)
        self.repo = repo
        self.student_id = int(student_id)
        self.default_academic_year = default_academic_year
        student = repo.get_student(self.student_id)
        if student is None:
            QMessageBox.warning(parent, "Fiche étudiant", "Étudiant introuvable.")
            self.reject()
            return

        sn = str(student.get("student_number") or "")
        name = f"{student.get('last_name', '')} {student.get('first_name', '')}".strip()
        self.setWindowTitle(f"Fiche — {name}" + (f" ({sn})" if sn else ""))
        root = QVBoxLayout(self)
        if not repo.has_pedagogical_contract(self.student_id):
            alarm = QLabel(
                "⚠ Contrat pédagogique signé non renseigné — document obligatoire. "
                "Utilisez « Modifier… » → onglet « Photo & documents » : cochez la version papier "
                "ou ajoutez le PDF."
            )
            alarm.setWordWrap(True)
            alarm.setStyleSheet(
                "background-color: #ffebee; color: #b71c1c; padding: 10px; "
                "border: 1px solid #ef9a9a; border-radius: 4px; font-weight: bold;"
            )
            root.addWidget(alarm)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QHBoxLayout(inner)

        photo_box = QGroupBox("Photo")
        pb = QVBoxLayout(photo_box)
        self.photo_label = QLabel()
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setMinimumSize(200, 220)
        self.photo_label.setStyleSheet("border: 1px solid palette(mid);")
        self._show_photo(str(student.get("photo_path") or ""))
        pb.addWidget(self.photo_label)
        lay.addWidget(photo_box)

        info_box = QGroupBox("Identité & scolarité")
        form = QFormLayout(info_box)
        self._identity_labels: dict[str, QLabel] = {}
        lay.addWidget(info_box, 1)

        right = QVBoxLayout()
        contact_box = QGroupBox("Contact & inscription")
        cf = QFormLayout(contact_box)
        self._contact_labels: dict[str, QLabel] = {}
        right.addWidget(contact_box)

        acc = str(student.get("accommodations") or "")
        acc_other = str(student.get("accommodations_other") or "")
        acc_parts = [x.strip() for x in acc.split(",") if x.strip()]
        if acc_other:
            acc_parts.append(acc_other)
        acc_box = QGroupBox("Aménagements & notes")
        af = QFormLayout(acc_box)
        acc_l = QLabel(", ".join(acc_parts) if acc_parts else "—")
        acc_l.setWordWrap(True)
        af.addRow("Aménagements :", acc_l)
        notes_l = QLabel(str(student.get("notes") or "").strip() or "—")
        notes_l.setWordWrap(True)
        notes_l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        af.addRow("Notes :", notes_l)
        right.addWidget(acc_box)

        docs_box = QGroupBox("Documents")
        db = QVBoxLayout(docs_box)
        self.docs_list = QListWidget()
        contract_docs = repo.list_student_attachments(
            self.student_id, category=PEDAGOGICAL_CONTRACT_CATEGORY
        )
        paper = bool(int(student.get("pedagogical_contract_paper") or 0))
        has_pdf = bool(contract_docs)
        if paper and has_pdf:
            db.addWidget(QLabel("Contrat pédagogique : PDF + version papier"))
        elif paper:
            paper_l = QLabel("Contrat pédagogique : version papier archivée")
            paper_l.setStyleSheet("color: palette(mid); font-style: italic;")
            db.addWidget(paper_l)
        elif not contract_docs:
            miss = QLabel("⚠ Contrat pédagogique signé : absent")
            miss.setStyleSheet("color: #b71c1c; font-weight: bold;")
            db.addWidget(miss)
        for att in repo.list_student_attachments(self.student_id):
            cat = str(att.get("category") or "")
            cat_lab = next((l for k, l in STUDENT_ATTACHMENT_CATEGORIES if k == cat), cat)
            name = str(
                att.get("original_filename")
                or att.get("label")
                or att.get("file_path")
                or ""
            )
            self.docs_list.addItem(f"{cat_lab} — {name}")
        if self.docs_list.count() == 0:
            self.docs_list.addItem("Aucun document")
            self.docs_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
        db.addWidget(self.docs_list)
        right.addWidget(docs_box)

        stages_box = QGroupBox("Stages")
        sb = QVBoxLayout(stages_box)
        self.internships_panel = StudentInternshipsPanel(self, repo=repo, student_id=self.student_id)
        sb.addWidget(self.internships_panel)
        right.addWidget(stages_box, 1)
        lay.addLayout(right, 1)

        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        bar = QHBoxLayout()
        self.edit_btn = QPushButton("Modifier…")
        self.edit_btn.clicked.connect(self._open_edit)
        bar.addWidget(self.edit_btn)
        bar.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.clicked.connect(self.accept)
        bar.addWidget(buttons)
        root.addLayout(bar)

        self._student = student
        self._populate_identity_form(form)
        self._populate_contact_form(cf)
        self._saved_changes = False

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(820, 640), minimum=(640, 480))

    def _make_value_label(self, text: str) -> QLabel:
        w = QLabel(text)
        w.setWordWrap(True)
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return w

    def _populate_identity_form(self, form: QFormLayout) -> None:
        s = self._student
        sn = str(s.get("student_number") or "")
        lv = str(s.get("level") or "")
        tr = str(s.get("track") or "")
        tr_lab = track_label(lv, tr) if tr else ""
        rows = [
            ("ine", "N° I.N.E.", str(s.get("student_number_ine") or "")),
            ("enrollment_number", "N° inscription (établissement)", str(s.get("student_number_local") or "")),
            ("last_name", "Nom", str(s.get("last_name") or "")),
            ("first_name", "Prénom", str(s.get("first_name") or "")),
            ("gender", "Genre", gender_label_fr(str(s.get("gender") or ""))),
            ("birth_date", "Date de naissance", str(s.get("birth_date") or "")),
            ("age", "Âge", format_age_display(str(s.get("birth_date") or ""))),
            ("nationality", "Nationalité", str(s.get("nationality") or "")),
            ("birth_place", "Lieu de naissance", str(s.get("birth_place") or "")),
            ("level", "Niveau", lv),
            ("track", "Parcours", f"{tr_lab} ({tr})" if tr and tr_lab != tr else tr),
            ("academic_year", "Année universitaire", str(s.get("academic_year") or "")),
            ("student_number", "Identifiant interne (base)", sn),
        ]
        for key, lab, val in rows:
            _, display = _field(lab, val)
            w = self._make_value_label(display)
            form.addRow(lab + " :", w)
            self._identity_labels[key] = w

    def _populate_contact_form(self, form: QFormLayout) -> None:
        s = self._student
        rows = [
            ("email_personal", "Email personnel", str(s.get("email_personal") or "")),
            ("email_institutional", "Email institutionnel", str(s.get("email_institutional") or "")),
            ("enrollment_institution", "Établissement d'inscription", str(s.get("enrollment_institution") or "")),
            ("origin_institution", "Établissement d'origine", str(s.get("origin_institution") or "")),
            ("origin_country", "Pays (origine)", str(s.get("origin_institution_country") or "")),
            ("highest_diploma", "Plus haut diplôme actuel", str(s.get("highest_diploma") or "")),
            ("application_platform", "Plateforme candidature", str(s.get("application_platform") or "")),
        ]
        for key, lab, val in rows:
            _, display = _field(lab, val)
            w = self._make_value_label(display)
            form.addRow(lab + " :", w)
            self._contact_labels[key] = w

    def _reload_from_db(self) -> None:
        student = self.repo.get_student(self.student_id)
        if student is None:
            return
        self._student = student
        sn = str(student.get("student_number") or "")
        name = f"{student.get('last_name', '')} {student.get('first_name', '')}".strip()
        self.setWindowTitle(f"Fiche — {name}" + (f" ({sn})" if sn else ""))
        self._show_photo(str(student.get("photo_path") or ""))

        lv = str(student.get("level") or "")
        tr = str(student.get("track") or "")
        tr_lab = track_label(lv, tr) if tr else ""
        identity_values = {
            "ine": str(student.get("student_number_ine") or ""),
            "enrollment_number": str(student.get("student_number_local") or ""),
            "last_name": str(student.get("last_name") or ""),
            "first_name": str(student.get("first_name") or ""),
            "gender": gender_label_fr(str(student.get("gender") or "")),
            "birth_date": str(student.get("birth_date") or ""),
            "age": format_age_display(str(student.get("birth_date") or "")),
            "nationality": str(student.get("nationality") or ""),
            "birth_place": str(student.get("birth_place") or ""),
            "level": lv,
            "track": f"{tr_lab} ({tr})" if tr and tr_lab != tr else tr,
            "academic_year": str(student.get("academic_year") or ""),
            "student_number": sn,
        }
        for key, val in identity_values.items():
            label = self._identity_labels.get(key)
            if label is not None:
                _, display = _field("", val)
                label.setText(display)

        contact_values = {
            "email_personal": str(student.get("email_personal") or ""),
            "email_institutional": str(student.get("email_institutional") or ""),
            "enrollment_institution": str(student.get("enrollment_institution") or ""),
            "origin_institution": str(student.get("origin_institution") or ""),
            "origin_country": str(student.get("origin_institution_country") or ""),
            "highest_diploma": str(student.get("highest_diploma") or ""),
            "application_platform": str(student.get("application_platform") or ""),
        }
        for key, val in contact_values.items():
            label = self._contact_labels.get(key)
            if label is not None:
                _, display = _field("", val)
                label.setText(display)

    def accept(self) -> None:
        if self._saved_changes:
            parent = self.parent()
            if parent is not None and hasattr(parent, "refresh"):
                parent.refresh()
        super().accept()

    def _show_photo(self, stored: str) -> None:
        if not stored:
            self.photo_label.setText("Aucune photo")
            self.photo_label.setPixmap(QPixmap())
            return
        p = abs_path_from_stored(stored)
        if p.is_file():
            pix = QPixmap(str(p))
            if not pix.isNull():
                self.photo_label.setPixmap(
                    pix.scaled(
                        220,
                        260,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.photo_label.setText("")
                return
        self.photo_label.setText("Photo introuvable")

    def _open_edit(self) -> None:
        dlg = StudentDialog(self, student=self._student, repo=self.repo)
        if not dlg.exec():
            return
        try:
            sn = str(self._student.get("student_number") or "")
            dlg.persist_update(self.repo, self.student_id, sn)
            self._reload_from_db()
            self._saved_changes = True
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
