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
from ..core.parcours import track_display_label
from ..services.student_mobility import is_erasmus_student, mobility_label_fr
from ..services.attachments import abs_path_from_stored
from ..services.dates import format_age_display
from ..services.lookups import gender_label_fr
from ..services.student_funding import format_funding_display
from ..services.student_status import (
    STUDENT_STATUS_GRADUATED,
    STUDENT_STATUS_WITHDRAWN,
    is_student_active,
    normalize_student_status,
    student_status_label_fr,
)
from .dialogs import StudentDialog
from .student_internships_panel import StudentInternshipsPanel
from .widgets import refresh_students_tab_ancestor


def _field(label: str, value: str) -> tuple[str, str]:
    v = (value or "").strip()
    return label, v if v else "—"


def _erasmus_courses_text(repo, student_id: int, academic_year: str) -> str:
    courses = repo.list_student_erasmus_courses(int(student_id), academic_year)
    if not courses:
        return "—"
    lines = []
    for c in courses:
        code = str(c.get("code") or c.get("ue_code") or "").strip()
        title = str(c.get("title") or c.get("name") or "").strip()
        if code and title:
            lines.append(f"{code} — {title}")
        else:
            lines.append(code or title or "?")
    return "\n".join(lines)


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
        self._status_banner = QLabel()
        self._status_banner.setWordWrap(True)
        self._status_banner.hide()
        root.addWidget(self._status_banner)
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

        funding_box = QGroupBox("Bourses & frais")
        ff = QFormLayout(funding_box)
        self._funding_label = QLabel(
            format_funding_display(
                str(student.get("funding") or ""),
                str(student.get("funding_other") or ""),
            )
        )
        self._funding_label.setWordWrap(True)
        ff.addRow("Bourses / exemptions :", self._funding_label)
        right.addWidget(funding_box)

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
        self.withdraw_btn = QPushButton("Démissionnaire")
        self.withdraw_btn.clicked.connect(self._toggle_withdrawn)
        bar.addWidget(self.edit_btn)
        bar.addWidget(self.withdraw_btn)
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
        self._update_status_ui()

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
        erasmus = is_erasmus_student(s)
        ay = str(s.get("academic_year") or "")
        lv = str(s.get("level") or "")
        tr = str(s.get("track") or "")
        tr_lab = track_display_label(lv, tr) if not erasmus else "—"
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
            ("mobility", "Profil", mobility_label_fr(s.get("mobility_type"))),
            ("level", "Niveau", "—" if erasmus else lv),
            ("track", "Parcours", tr_lab or "—"),
            ("academic_year", "Année universitaire", ay),
            ("student_status", "Statut", student_status_label_fr(s.get("status"))),
            ("student_number", "Identifiant interne (base)", sn),
        ]
        if erasmus:
            rows.insert(
                rows.index(next(r for r in rows if r[0] == "academic_year")),
                ("erasmus_courses", "UE suivies", _erasmus_courses_text(self.repo, self.student_id, ay)),
            )
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
            ("phone", "Téléphone", str(s.get("phone") or "")),
            ("enrollment_institution", "Établissement d'inscription", str(s.get("enrollment_institution") or "")),
            ("origin_institution", "Établissement d'origine", str(s.get("origin_institution") or "")),
            ("origin_country", "Pays (origine)", str(s.get("origin_institution_country") or "")),
            ("highest_diploma", "Plus haut diplôme actuel", str(s.get("highest_diploma") or "")),
            ("application_platform", "Plateforme candidature", str(s.get("application_platform") or "")),
            ("mon_master_ranking", "Classement Mon Master", str(s.get("mon_master_ranking") or "")),
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
        erasmus = is_erasmus_student(student)
        ay = str(student.get("academic_year") or "")
        tr_lab = track_display_label(lv, tr) if not erasmus else "—"
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
            "mobility": mobility_label_fr(student.get("mobility_type")),
            "level": "—" if erasmus else lv,
            "track": tr_lab or "—",
            "academic_year": ay,
            "student_status": student_status_label_fr(student.get("status")),
            "student_number": sn,
        }
        if erasmus:
            identity_values["erasmus_courses"] = _erasmus_courses_text(
                self.repo, self.student_id, ay
            )
        for key, val in identity_values.items():
            label = self._identity_labels.get(key)
            if label is not None:
                _, display = _field("", val)
                label.setText(display)

        contact_values = {
            "email_personal": str(student.get("email_personal") or ""),
            "email_institutional": str(student.get("email_institutional") or ""),
            "phone": str(student.get("phone") or ""),
            "enrollment_institution": str(student.get("enrollment_institution") or ""),
            "origin_institution": str(student.get("origin_institution") or ""),
            "origin_country": str(student.get("origin_institution_country") or ""),
            "highest_diploma": str(student.get("highest_diploma") or ""),
            "application_platform": str(student.get("application_platform") or ""),
            "mon_master_ranking": str(student.get("mon_master_ranking") or ""),
        }
        for key, val in contact_values.items():
            label = self._contact_labels.get(key)
            if label is not None:
                _, display = _field("", val)
                label.setText(display)

        self._funding_label.setText(
            format_funding_display(
                str(student.get("funding") or ""),
                str(student.get("funding_other") or ""),
            )
        )

        self._update_status_ui()

    def _update_status_ui(self) -> None:
        status = normalize_student_status(self._student.get("status"))
        if status == STUDENT_STATUS_GRADUATED:
            self._status_banner.setText(
                "Statut : diplômé — formation terminée. Fiche et historique conservés ; "
                "absent des listes actives (notes, convocations, statistiques…)."
            )
            self._status_banner.setStyleSheet(
                "background-color: #e8f5e9; color: #1b5e20; padding: 8px; "
                "border: 1px solid #a5d6a7; border-radius: 4px;"
            )
            self._status_banner.show()
            self.withdraw_btn.setText("Réactiver (actif)")
        elif status == STUDENT_STATUS_WITHDRAWN:
            self._status_banner.setText(
                "Statut : démissionnaire — fiche conservée, absent des listes actives "
                "(notes, convocations, statistiques…)."
            )
            self._status_banner.setStyleSheet(
                "background-color: #f5f5f5; color: #555; padding: 8px; "
                "border: 1px solid #ccc; border-radius: 4px;"
            )
            self._status_banner.show()
            self.withdraw_btn.setText("Réintégrer")
        else:
            self._status_banner.hide()
            self.withdraw_btn.setText("Démissionnaire")

    def _toggle_withdrawn(self) -> None:
        status = normalize_student_status(self._student.get("status"))
        if status in {STUDENT_STATUS_WITHDRAWN, STUDENT_STATUS_GRADUATED}:
            self.repo.restore_students_active([self.student_id])
        else:
            reply = QMessageBox.question(
                self,
                "Marquer démissionnaire",
                "Marquer cet étudiant comme démissionnaire ?\n\n"
                "Il disparaîtra des listes actives mais sa fiche sera conservée.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            self.repo.mark_students_withdrawn([self.student_id])
        self._reload_from_db()
        self._saved_changes = True
        refresh_students_tab_ancestor(self)

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
        fresh = self.repo.get_student(self.student_id)
        if fresh is not None:
            self._student = fresh
        dlg = StudentDialog(self, student=self._student, repo=self.repo)
        if not dlg.exec():
            return
        try:
            sn = str(self._student.get("student_number") or "")
            dlg.persist_update(self.repo, self.student_id, sn)
            self._reload_from_db()
            self._saved_changes = True
            refresh_students_tab_ancestor(self)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
