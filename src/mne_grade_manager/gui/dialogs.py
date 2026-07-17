from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.institutions import MNE_CARRIER_PARTNERS, OTHER_CARRIER_DATA
from ..services.contact_emails import EMAIL_KEYS, EMAIL_LABELS_FR, read_emails
from ..services.contact_phones import PHONE_LABELS_FR, PHONE_KEYS, read_phones
from ..services.attachments import COURSE_SYLLABUS_SUFFIXES, abs_path_from_stored
from .student_files_panel import StudentFilesPanel
from .student_erasmus_courses_panel import StudentErasmusCoursesPanel
from .student_internships_panel import StudentInternshipsPanel

from ..core.mne_modules import lookup_mne_module, mne_module_choices, normalize_mne_module_code
from ..core.parcours import (
    OTHER_LEVEL_DATA,
    OTHER_TRACK_DATA,
    PARCOURS_BY_LEVEL,
    STANDARD_LEVELS,
    suggested_maquette_name,
    track_display_label,
)

from ..services.dates import normalize_birth_date_iso
from ..services.student_funding import (
    FUNDING_CHOICES,
    encode_funding_codes,
    parse_funding_codes,
)
from ..services.lookups import (
    adapt_institutional_email,
    is_valid_institutional_email,
    normalize_email,
    suggest_institutional_email,
)
from ..services.student_mobility import (
    MOBILITY_CHOICES,
    MOBILITY_ERASMUS,
    MOBILITY_MNE,
    normalize_mobility_type,
)

OTHER_INSTITUTION_DATA = "__OTHER_INSTITUTION__"


class StudentDialog(QDialog):
    """Add or edit a student. Pass ``student`` dict (from DB row) to edit."""

    def __init__(
        self,
        parent=None,
        default_academic_year: str = "",
        student: dict[str, Any] | None = None,
        repo: Any | None = None,
    ):
        super().__init__(parent)
        self.edit_id: int | None = None
        self._suppress_institution_email = False
        if student is not None:
            self.setWindowTitle("Edit student")
            self.edit_id = int(student["id"])
        else:
            self.setWindowTitle("Add student")
        self._repo = repo
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        tabs = self.tabs

        if student is not None:
            student = dict(student)
            if repo is not None and not str(student.get("track") or "").strip():
                from ..services.student_parcours_repair import infer_student_parcours

                inferred = infer_student_parcours(
                    repo.db,
                    int(student["id"]),
                    str(student.get("academic_year") or ""),
                )
                if inferred:
                    inf_lv, inf_tr, inf_ay = inferred
                    student["level"] = inf_lv
                    student["track"] = inf_tr
                    if inf_ay and not str(student.get("academic_year") or "").strip():
                        student["academic_year"] = inf_ay

        fiche_scroll = QScrollArea()
        fiche_scroll.setWidgetResizable(True)
        fiche_inner = QWidget()
        fiche_layout = QVBoxLayout(fiche_inner)
        fiche_layout.setSpacing(14)
        fiche_layout.setContentsMargins(8, 8, 8, 8)
        self.student_number = QLineEdit()
        self.student_number.setReadOnly(True)
        self.student_number.setPlaceholderText("Ex. MNE-DUPONT-JE-A7K2 (généré à partir de l'identité)")
        self.student_number_ine = QLineEdit()
        self.student_number_ine.setPlaceholderText(
            "Identifiant national (relevés de notes) — peut être complété plus tard"
        )
        self.student_number_local = QLineEdit()
        self.student_number_local.setPlaceholderText(
            "Attribué par l'établissement à l'inscription (optionnel)"
        )
        self.last_name = QLineEdit()
        self.first_name = QLineEdit()
        self.gender = QComboBox()
        self.gender.addItem("—", "")
        self.gender.addItem("Homme", "M")
        self.gender.addItem("Femme", "F")
        self.gender.addItem("Autre", "O")
        self.birth_date = QLineEdit()
        self.birth_date.setPlaceholderText("YYYY-MM-DD")
        self.nationality = QLineEdit()
        self.origin_institution = QLineEdit()
        self.origin_institution.setPlaceholderText("Université / école d’origine")
        self.origin_institution_country = QLineEdit()
        self.origin_institution_country.setPlaceholderText("Pays de l’établissement d’origine")
        self.highest_diploma = QLineEdit()
        self.highest_diploma.setPlaceholderText("ex. Licence Physique (Bac+3), Master M2…")
        self.birth_place = QLineEdit()
        self.email_personal = QLineEdit()
        self.email_institutional = QLineEdit()
        self.phone = QLineEdit()
        self.enrollment_institution = QComboBox()
        self.enrollment_institution.addItem("—", "")
        self.enrollment_institution.addItem("Université Paris-Saclay", "Université Paris-Saclay")
        self.enrollment_institution.addItem("Institut Polytechnique de Paris", "Institut Polytechnique de Paris")
        self.enrollment_institution.addItem("Chimie Paris PSL", "Chimie Paris PSL")
        self.enrollment_institution.addItem("ENSTA Paris", "ENSTA Paris")
        self.enrollment_institution.addItem("Autre (saisie libre)", OTHER_INSTITUTION_DATA)
        self.enrollment_institution_other = QLineEdit()
        self.enrollment_institution_other.setPlaceholderText("Établissement d’inscription")
        self.enrollment_institution.currentIndexChanged.connect(self._institution_changed)
        self.enrollment_institution_other.textEdited.connect(self._on_other_institution_edited)
        self.last_name.textEdited.connect(self._refresh_institutional_email)
        self.first_name.textEdited.connect(self._refresh_institutional_email)

        self.application_platform = QComboBox()
        self.application_platform.addItem("—", "")
        self.application_platform.addItem("MonMaster", "MonMaster")
        self.application_platform.addItem("UPSay", "UPSay")
        self.application_platform.addItem("Inception", "Inception")
        self.application_platform.addItem("IPParis", "IPParis")
        self.application_platform.addItem("Autre (saisie libre)", "__OTHER_PLATFORM__")
        self.application_platform_other = QLineEdit()
        self.application_platform_other.setPlaceholderText("Plateforme de candidature")
        self.application_platform.currentIndexChanged.connect(self._platform_changed)

        self.mon_master_ranking = QLineEdit()
        self.mon_master_ranking.setPlaceholderText(
            "ex. 1, 10, NC — classement Mon Master (optionnel, import Excel possible)"
        )

        self.acc_tiers_temps = QCheckBox("Tiers temps")
        self.acc_salle_isolee = QCheckBox("Salle isolée")
        self.acc_pc = QCheckBox("PC")
        self.acc_row = QWidget()
        acc_layout = QHBoxLayout(self.acc_row)
        acc_layout.setContentsMargins(0, 0, 0, 0)
        acc_layout.addWidget(self.acc_tiers_temps)
        acc_layout.addWidget(self.acc_salle_isolee)
        acc_layout.addWidget(self.acc_pc)
        self.acc_other = QLineEdit()
        self.acc_other.setPlaceholderText("Autres aménagements (texte libre)")

        self.funding_checks: dict[str, QCheckBox] = {}
        self.funding_row = QWidget()
        funding_layout = QHBoxLayout(self.funding_row)
        funding_layout.setContentsMargins(0, 0, 0, 0)
        for code, label in FUNDING_CHOICES:
            cb = QCheckBox(label)
            self.funding_checks[code] = cb
            funding_layout.addWidget(cb)
        self.funding_other = QLineEdit()
        self.funding_other.setPlaceholderText("Autre bourse ou exemption (texte libre)")

        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Notes / commentaires supplémentaires…")

        self.academic_year = QLineEdit()
        self.academic_year.textEdited.connect(self._on_academic_year_edited)

        self.mobility_combo = QComboBox()
        for code, label in MOBILITY_CHOICES:
            self.mobility_combo.addItem(label, code)
        self.mobility_combo.currentIndexChanged.connect(self._on_mobility_changed)

        self.level_combo = QComboBox()
        for lv in STANDARD_LEVELS:
            self.level_combo.addItem(lv, lv)
        self.level_combo.addItem("Autre (niveau libre)", OTHER_LEVEL_DATA)
        self.level_other = QLineEdit()
        self.level_other.setPlaceholderText("ex. M1")
        self.level_row = QWidget()
        lr = QHBoxLayout(self.level_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.addWidget(self.level_combo)
        lr.addWidget(self.level_other, 1)

        self.track_combo = QComboBox()
        self.track_other = QLineEdit()
        self.track_other.setPlaceholderText("Acronyme parcours")
        self.track_row = QWidget()
        tr = QHBoxLayout(self.track_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.addWidget(self.track_combo)
        tr.addWidget(self.track_other, 1)

        self.level_combo.currentIndexChanged.connect(self._student_level_changed)
        self.track_combo.currentIndexChanged.connect(self._student_track_changed)

        self._suppress_institution_email = True
        if student is not None:
            self.student_number.setText(str(student.get("student_number") or ""))
            self.student_number_ine.setText(str(student.get("student_number_ine") or ""))
            self.student_number_local.setText(str(student.get("student_number_local") or ""))
            self.last_name.setText(str(student.get("last_name") or ""))
            self.first_name.setText(str(student.get("first_name") or ""))
            g = str(student.get("gender") or "").strip().upper()
            if g not in {"", "M", "F", "O"}:
                g = ""
            gi = self.gender.findData(g)
            if gi >= 0:
                self.gender.setCurrentIndex(gi)
            self.birth_date.setText(str(student.get("birth_date") or "").strip())
            self.nationality.setText(str(student.get("nationality") or ""))
            self.origin_institution.setText(str(student.get("origin_institution") or ""))
            self.origin_institution_country.setText(
                str(student.get("origin_institution_country") or "")
            )
            self.highest_diploma.setText(str(student.get("highest_diploma") or ""))
            self.birth_place.setText(str(student.get("birth_place") or ""))
            self.email_personal.setText(str(student.get("email_personal") or ""))
            self.email_institutional.setText(str(student.get("email_institutional") or ""))
            self.phone.setText(str(student.get("phone") or ""))
            inst = str(student.get("enrollment_institution") or "").strip()
            ii = self.enrollment_institution.findData(inst)
            if ii >= 0:
                self.enrollment_institution.setCurrentIndex(ii)
            elif inst:
                oi = self.enrollment_institution.findData(OTHER_INSTITUTION_DATA)
                if oi >= 0:
                    self.enrollment_institution.setCurrentIndex(oi)
                    self.enrollment_institution_other.setText(inst)
            plat = str(student.get("application_platform") or "").strip()
            pi = self.application_platform.findData(plat)
            if pi >= 0:
                self.application_platform.setCurrentIndex(pi)
            elif plat:
                oi = self.application_platform.findData("__OTHER_PLATFORM__")
                if oi >= 0:
                    self.application_platform.setCurrentIndex(oi)
                    self.application_platform_other.setText(plat)
            self.mon_master_ranking.setText(str(student.get("mon_master_ranking") or ""))
            acc = str(student.get("accommodations") or "")
            acc_set = {x.strip().lower() for x in acc.split(",") if x and x.strip()}
            self.acc_tiers_temps.setChecked("tiers_temps" in acc_set or "tier_temps" in acc_set)
            self.acc_salle_isolee.setChecked("salle_isolee" in acc_set)
            self.acc_pc.setChecked("pc" in acc_set)
            self.acc_other.setText(str(student.get("accommodations_other") or ""))
            funding_set = parse_funding_codes(str(student.get("funding") or ""))
            for code, cb in self.funding_checks.items():
                cb.setChecked(code in funding_set)
            self.funding_other.setText(str(student.get("funding_other") or ""))
            self.notes.setPlainText(str(student.get("notes") or ""))
            self.academic_year.setText(str(student.get("academic_year") or ""))
            mob = normalize_mobility_type(student.get("mobility_type"))
            mi = self.mobility_combo.findData(mob)
            if mi >= 0:
                self.mobility_combo.setCurrentIndex(mi)
            self._apply_student_level_track(
                str(student.get("level") or ""),
                str(student.get("track") or ""),
            )
        elif default_academic_year:
            self.academic_year.setText(default_academic_year)
            self._student_level_changed()
        else:
            self._student_level_changed()

        identity_box = QGroupBox("Identité")
        form = QFormLayout(identity_box)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)
        if student is not None:
            form.addRow("Identifiant interne (base)", self.student_number)
        form.addRow("N° I.N.E.", self.student_number_ine)
        form.addRow("N° inscription (établissement)", self.student_number_local)
        form.addRow("Nom", self.last_name)
        form.addRow("Prénom", self.first_name)
        form.addRow("Genre", self.gender)
        form.addRow("Date de naissance", self.birth_date)
        form.addRow("Nationalité", self.nationality)
        form.addRow("Lieu de naissance", self.birth_place)

        scolarite_box = QGroupBox("Scolarité MNE")
        scolarite_form = QFormLayout(scolarite_box)
        scolarite_form.setVerticalSpacing(10)
        scolarite_form.setHorizontalSpacing(12)
        scolarite_form.addRow("Établissement d’origine", self.origin_institution)
        scolarite_form.addRow("Pays (établ. d’origine)", self.origin_institution_country)
        scolarite_form.addRow("Plus haut diplôme actuel", self.highest_diploma)
        scolarite_form.addRow("Profil", self.mobility_combo)
        scolarite_form.addRow("Niveau", self.level_row)
        scolarite_form.addRow("Parcours", self.track_row)
        scolarite_form.addRow("Année universitaire", self.academic_year)

        contact_box = QGroupBox("Contact & inscription")
        contact_form = QFormLayout(contact_box)
        contact_form.setVerticalSpacing(10)
        contact_form.setHorizontalSpacing(12)
        contact_form.addRow("Email personnel", self.email_personal)
        contact_form.addRow("Email institutionnel", self.email_institutional)
        contact_form.addRow("Téléphone", self.phone)
        contact_form.addRow("Établissement d’inscription", self.enrollment_institution)
        contact_form.addRow("", self.enrollment_institution_other)
        contact_form.addRow("Plateforme de candidature", self.application_platform)
        contact_form.addRow("", self.application_platform_other)
        contact_form.addRow("Classement Mon Master", self.mon_master_ranking)

        funding_box = QGroupBox("Bourses & frais")
        funding_form = QFormLayout(funding_box)
        funding_form.setVerticalSpacing(10)
        funding_form.setHorizontalSpacing(12)
        funding_form.addRow("Bourses / exemptions", self.funding_row)
        funding_form.addRow("", self.funding_other)

        extras_box = QGroupBox("Aménagements & notes")
        extras_form = QFormLayout(extras_box)
        extras_form.setVerticalSpacing(10)
        extras_form.setHorizontalSpacing(12)
        extras_form.addRow("Aménagement d’études", self.acc_row)
        extras_form.addRow("", self.acc_other)
        self.notes.setMaximumHeight(100)
        extras_form.addRow("Notes", self.notes)

        fiche_layout.addWidget(identity_box)
        fiche_layout.addWidget(scolarite_box)
        fiche_layout.addWidget(contact_box)
        fiche_layout.addWidget(funding_box)
        fiche_layout.addWidget(extras_box)
        fiche_layout.addStretch()
        fiche_scroll.setWidget(fiche_inner)
        tabs.addTab(fiche_scroll, "Fiche")
        sid = int(student["id"]) if student is not None else None
        ay_init = self.academic_year.text().strip() or default_academic_year
        self.erasmus_panel = StudentErasmusCoursesPanel(
            self, repo=repo, student_id=sid, academic_year=ay_init
        )
        self._erasmus_tab_index = tabs.addTab(self.erasmus_panel, "ERASMUS — UE suivies")
        tabs.setTabVisible(self._erasmus_tab_index, self.mobility_value() == MOBILITY_ERASMUS)
        self.files_panel = StudentFilesPanel(self, repo=repo, student_id=sid)
        tabs.addTab(self.files_panel, "Photo & documents")
        self.internships_panel = StudentInternshipsPanel(self, repo=repo, student_id=sid)
        tabs.addTab(self.internships_panel, "Stages")
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._institution_changed()
        self._platform_changed()
        self._suppress_institution_email = False
        if student is None:
            self._apply_institution_email()
        self._on_mobility_changed()
        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(900, 720), minimum=(680, 560))

    def mobility_value(self) -> str:
        return normalize_mobility_type(self.mobility_combo.currentData())

    def _on_mobility_changed(self) -> None:
        erasmus = self.mobility_value() == MOBILITY_ERASMUS
        self.level_row.setVisible(not erasmus)
        self.track_row.setVisible(not erasmus)
        if hasattr(self, "_erasmus_tab_index"):
            self.tabs.setTabVisible(self._erasmus_tab_index, erasmus)
        if hasattr(self, "erasmus_panel"):
            self.erasmus_panel.set_academic_year(self.academic_year.text().strip())

    def _on_academic_year_edited(self, _text: str) -> None:
        if hasattr(self, "erasmus_panel"):
            self.erasmus_panel.set_academic_year(self.academic_year.text().strip())

    def _apply_student_level_track(self, level: str, track: str) -> None:
        from ..services.student_parcours_repair import is_academic_year_label

        lv = (level or "").strip().upper()
        tr = (track or "").strip().upper()
        tr = "P" if tr in {"M1P"} else ("C" if tr in {"M1C"} else tr)
        if is_academic_year_label(tr):
            tr = ""
        if is_academic_year_label(lv):
            lv = ""
        self._suppress_level_changed = True
        self.level_combo.blockSignals(True)
        self.track_combo.blockSignals(True)
        try:
            if lv in PARCOURS_BY_LEVEL:
                i = self.level_combo.findData(lv)
                if i >= 0:
                    self.level_combo.setCurrentIndex(i)
                self._repopulate_track_combo_for_level()
                ti = self.track_combo.findData(tr) if tr else -1
                if ti >= 0:
                    self.track_combo.setCurrentIndex(ti)
                elif tr:
                    oi = self.track_combo.findData(OTHER_TRACK_DATA)
                    if oi >= 0:
                        self.track_combo.setCurrentIndex(oi)
                        self.track_other.setText(tr)
                else:
                    blank = self.track_combo.findData("")
                    if blank >= 0:
                        self.track_combo.setCurrentIndex(blank)
                return
            oi = self.level_combo.findData(OTHER_LEVEL_DATA)
            if oi >= 0:
                self.level_combo.setCurrentIndex(oi)
            self.level_other.setText(level or "")
            self._repopulate_track_combo_for_level()
            self.track_other.setText(tr)
        finally:
            self.track_combo.blockSignals(False)
            self.level_combo.blockSignals(False)
            self._suppress_level_changed = False
            self._student_track_changed()

    def _student_level_changed(self) -> None:
        if getattr(self, "_suppress_level_changed", False):
            return
        self._repopulate_track_combo_for_level()

    def _repopulate_track_combo_for_level(self) -> None:
        is_other = self.level_combo.currentData() == OTHER_LEVEL_DATA
        self.level_other.setVisible(is_other)
        if is_other:
            self.track_combo.blockSignals(True)
            self.track_combo.clear()
            self.track_combo.setVisible(False)
            self.track_combo.blockSignals(False)
            self.track_other.setVisible(True)
            return
        self.track_combo.setVisible(True)
        lvl = str(self.level_combo.currentData() or "")
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        self.track_combo.addItem("— (non renseigné)", "")
        for code, _lab in PARCOURS_BY_LEVEL.get(lvl, ()):
            self.track_combo.addItem(track_display_label(lvl, code), code)
        self.track_combo.addItem("Autre (saisie libre)", OTHER_TRACK_DATA)
        self.track_combo.blockSignals(False)
        self._student_track_changed()

    def _student_track_changed(self) -> None:
        if not self.track_combo.isVisible():
            return
        other = self.track_combo.currentData() == OTHER_TRACK_DATA
        self.track_other.setVisible(other)

    def _institution_changed(self) -> None:
        other = self.enrollment_institution.currentData() == OTHER_INSTITUTION_DATA
        self.enrollment_institution_other.setVisible(other)
        if not self._suppress_institution_email:
            self._apply_institution_email()

    def _on_other_institution_edited(self) -> None:
        if not self._suppress_institution_email:
            self._apply_institution_email()

    def _apply_institution_email(self) -> None:
        """Recalcule l'email institutionnel quand l'établissement d'inscription change."""
        inst = self.enrollment_institution_value()
        if not inst:
            return
        suggested = suggest_institutional_email(
            self.first_name.text().strip(),
            self.last_name.text().strip(),
            inst,
        )
        if suggested:
            self.email_institutional.setText(suggested)
            return
        adapted = adapt_institutional_email(
            self.first_name.text().strip(),
            self.last_name.text().strip(),
            inst,
            self.email_institutional.text(),
        )
        if adapted:
            self.email_institutional.setText(adapted)

    def _refresh_institutional_email(self) -> None:
        inst = self.enrollment_institution_value()
        if not inst:
            return
        adapted = adapt_institutional_email(
            self.first_name.text().strip(),
            self.last_name.text().strip(),
            inst,
            self.email_institutional.text(),
        )
        if adapted and adapted != self.email_institutional.text().strip():
            self.email_institutional.setText(adapted)

    def resolved_institutional_email(self) -> str:
        return adapt_institutional_email(
            self.first_name.text().strip(),
            self.last_name.text().strip(),
            self.enrollment_institution_value(),
            normalize_email(self.email_institutional.text()),
        )

    def resolved_gender(self) -> str:
        g_raw = self.gender.currentData()
        return str(g_raw).strip().upper() if g_raw else ""

    def resolved_birth_date(self) -> str:
        return normalize_birth_date_iso(self.birth_date.text().strip())

    def validation_error(self) -> str | None:
        if not is_valid_institutional_email(self.resolved_institutional_email()):
            return "Email institutionnel invalide."
        bd_raw = self.birth_date.text().strip()
        if bd_raw and not self.resolved_birth_date():
            return "Date de naissance invalide (AAAA-MM-JJ)."
        return None

    def accept(self) -> None:
        self._refresh_institutional_email()
        err = self.validation_error()
        if err:
            QMessageBox.warning(self, "Fiche étudiant", err)
            return
        super().accept()

    def persist_update(self, repo: Any, student_id: int, student_number: str) -> None:
        from ..services.student_parcours_repair import (
            coalesce_student_parcours_fields,
            infer_student_parcours,
        )

        existing = repo.get_student(int(student_id)) or {}
        form_track = self.track_value()
        mob = self.mobility_value()
        if mob == MOBILITY_ERASMUS:
            level, track, academic_year = (
                "",
                "",
                self.academic_year.text().strip(),
            )
        else:
            level, track, academic_year = coalesce_student_parcours_fields(
                self.level_value(),
                form_track,
                self.academic_year.text().strip(),
                existing,
            )
            if not form_track and not track:
                inferred = infer_student_parcours(repo.db, int(student_id), academic_year)
                if inferred:
                    inf_lv, inf_tr, inf_ay = inferred
                    level, track, academic_year = coalesce_student_parcours_fields(
                        inf_lv or level,
                        inf_tr,
                        inf_ay or academic_year,
                        existing,
                    )
        repo.update_student(
            int(student_id),
            student_number,
            self.student_number_ine.text().strip(),
            self.student_number_local.text().strip(),
            self.last_name.text().strip(),
            self.first_name.text().strip(),
            email_personal=normalize_email(self.email_personal.text()),
            email_institutional=self.resolved_institutional_email(),
            phone=self.phone.text().strip(),
            enrollment_institution=self.enrollment_institution_value(),
            application_platform=self.application_platform_value(),
            mon_master_ranking=self.mon_master_ranking.text().strip(),
            accommodations=self.accommodations_value(),
            accommodations_other=self.accommodations_other_value(),
            funding=self.funding_value(),
            funding_other=self.funding_other_value(),
            notes=self.notes_value(),
            level=level,
            track=track,
            academic_year=academic_year,
            birth_date=self.resolved_birth_date(),
            nationality=self.nationality.text().strip(),
            birth_place=self.birth_place.text().strip(),
            gender=self.resolved_gender(),
            origin_institution=self.origin_institution_value(),
            origin_institution_country=self.origin_institution_country_value(),
            highest_diploma=self.highest_diploma_value(),
            mobility_type=mob,
        )
        self.apply_pending_files(repo, int(student_id))
        if mob == MOBILITY_ERASMUS:
            self.erasmus_panel.persist(repo, int(student_id), academic_year)
        else:
            repo.sync_enrollments_for_student(int(student_id))

    def persist_create(self, repo: Any) -> int:
        mob = self.mobility_value()
        new_id = repo.add_student(
            "",
            self.student_number_ine.text().strip(),
            self.student_number_local.text().strip(),
            self.last_name.text().strip(),
            self.first_name.text().strip(),
            normalize_email(self.email_personal.text()),
            self.resolved_institutional_email(),
            self.phone.text().strip(),
            self.enrollment_institution_value(),
            self.application_platform_value(),
            self.mon_master_ranking.text().strip(),
            self.accommodations_value(),
            self.accommodations_other_value(),
            self.funding_value(),
            self.funding_other_value(),
            self.notes_value(),
            "" if mob == MOBILITY_ERASMUS else self.level_value(),
            "" if mob == MOBILITY_ERASMUS else self.track_value(),
            self.academic_year.text().strip(),
            birth_date=self.resolved_birth_date(),
            nationality=self.nationality.text().strip(),
            birth_place=self.birth_place.text().strip(),
            gender=self.resolved_gender(),
            origin_institution=self.origin_institution_value(),
            origin_institution_country=self.origin_institution_country_value(),
            highest_diploma=self.highest_diploma_value(),
            mobility_type=mob,
        )
        self.apply_pending_files(repo, new_id)
        ay = self.academic_year.text().strip()
        if mob == MOBILITY_ERASMUS:
            self.erasmus_panel.set_context(repo, new_id, ay)
            self.erasmus_panel.persist(repo, new_id, ay)
        else:
            repo.sync_enrollments_for_student(new_id)
        return new_id

    def _platform_changed(self) -> None:
        other = self.application_platform.currentData() == "__OTHER_PLATFORM__"
        self.application_platform_other.setVisible(other)

    def level_value(self) -> str:
        if self.level_combo.currentData() == OTHER_LEVEL_DATA:
            return self.level_other.text().strip()
        return str(self.level_combo.currentData() or "").strip()

    def track_value(self) -> str:
        if self.level_combo.currentData() == OTHER_LEVEL_DATA:
            return self.track_other.text().strip()
        data = self.track_combo.currentData()
        if data == OTHER_TRACK_DATA:
            return self.track_other.text().strip()
        return str(data if data is not None else "").strip()

    def enrollment_institution_value(self) -> str:
        if self.enrollment_institution.currentData() == OTHER_INSTITUTION_DATA:
            return self.enrollment_institution_other.text().strip()
        return str(self.enrollment_institution.currentData() or "").strip()

    def application_platform_value(self) -> str:
        if self.application_platform.currentData() == "__OTHER_PLATFORM__":
            return self.application_platform_other.text().strip()
        return str(self.application_platform.currentData() or "").strip()

    def accommodations_value(self) -> str:
        vals: list[str] = []
        if self.acc_tiers_temps.isChecked():
            vals.append("tiers_temps")
        if self.acc_salle_isolee.isChecked():
            vals.append("salle_isolee")
        if self.acc_pc.isChecked():
            vals.append("pc")
        return ",".join(vals)

    def accommodations_other_value(self) -> str:
        return self.acc_other.text().strip()

    def funding_value(self) -> str:
        selected = {code for code, cb in self.funding_checks.items() if cb.isChecked()}
        return encode_funding_codes(selected)

    def funding_other_value(self) -> str:
        return self.funding_other.text().strip()

    def notes_value(self) -> str:
        return self.notes.toPlainText().strip()

    def origin_institution_value(self) -> str:
        return self.origin_institution.text().strip()

    def origin_institution_country_value(self) -> str:
        return self.origin_institution_country.text().strip()

    def highest_diploma_value(self) -> str:
        return self.highest_diploma.text().strip()

    def apply_pending_files(self, repo: Any, student_id: int) -> None:
        self.files_panel.apply_pending_uploads(repo, int(student_id))


class CourseDialog(QDialog):
    """Ajout ou édition d’un cours (codes multi-établissements, heures maquette, modalités MCC)."""

    def __init__(self, parent=None, course: dict[str, Any] | None = None):
        super().__init__(parent)
        self.edit_id: int | None = None
        self._pending_syllabus: Path | None = None
        self._syllabus_remove_pending = False
        self._stored_syllabus_path = ""
        self._stored_syllabus_name = ""
        if course is not None:
            self.setWindowTitle("Edit course")
            self.edit_id = int(course["id"])
            self._stored_syllabus_path = str(course.get("syllabus_path") or "").strip()
            self._stored_syllabus_name = str(course.get("syllabus_filename") or "").strip()
        else:
            self.setWindowTitle("Add course")
        self.setMinimumWidth(520)
        self.setMinimumHeight(480)

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)

        hint = QLabel(
            "Code MNE (module) : acronyme officiel du guide MNE (ex. M2B1-C-SAFE). "
            "Code Apogée : référence administrative établissement (ex. EN00005920)."
        )
        hint.setWordWrap(True)
        form.addRow(hint)

        self.mne_module_code = QComboBox()
        self.mne_module_code.setEditable(True)
        self.mne_module_code.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.mne_module_code.addItem("— (saisie libre)", "")
        for _code, label in mne_module_choices():
            self.mne_module_code.addItem(label, _code)
        self.mne_module_code.currentIndexChanged.connect(self._on_mne_module_picked)

        self.code = QLineEdit()
        self.name = QLineEdit()
        self.ects = QDoubleSpinBox()
        self.ects.setRange(0, 120)
        self.ects.setDecimals(1)
        self.semester = QLineEdit()
        self.semester.setPlaceholderText("ex. Semestre 1")
        self.ead_flag = QLineEdit()
        self.ead_flag.setPlaceholderText("OUI / NON")

        hours_box = QGroupBox("Heures (maquette)")
        hours_form = QFormLayout(hours_box)
        self.hours_total = QDoubleSpinBox()
        self.hours_total.setRange(0, 800)
        self.hours_total.setDecimals(1)
        self.hours_cm = QDoubleSpinBox()
        self.hours_cm.setRange(0, 500)
        self.hours_cm.setDecimals(1)
        self.hours_td = QDoubleSpinBox()
        self.hours_td.setRange(0, 500)
        self.hours_td.setDecimals(1)
        self.hours_tp = QDoubleSpinBox()
        self.hours_tp.setRange(0, 500)
        self.hours_tp.setDecimals(1)
        self.hours_project = QDoubleSpinBox()
        self.hours_project.setRange(0, 500)
        self.hours_project.setDecimals(1)
        self.hours_pt = QDoubleSpinBox()
        self.hours_pt.setRange(0, 500)
        self.hours_pt.setDecimals(1)
        self.hours_aa = QDoubleSpinBox()
        self.hours_aa.setRange(0, 500)
        self.hours_aa.setDecimals(1)
        hours_form.addRow("Total", self.hours_total)
        row_cm_td = QHBoxLayout()
        row_cm_td.addWidget(QLabel("CM"))
        row_cm_td.addWidget(self.hours_cm)
        row_cm_td.addWidget(QLabel("TD"))
        row_cm_td.addWidget(self.hours_td)
        hours_form.addRow(row_cm_td)
        row_tp_pr = QHBoxLayout()
        row_tp_pr.addWidget(QLabel("TP"))
        row_tp_pr.addWidget(self.hours_tp)
        row_tp_pr.addWidget(QLabel("Projet"))
        row_tp_pr.addWidget(self.hours_project)
        hours_form.addRow(row_tp_pr)
        row_pt_aa = QHBoxLayout()
        row_pt_aa.addWidget(QLabel("PT"))
        row_pt_aa.addWidget(self.hours_pt)
        row_pt_aa.addWidget(QLabel("AA"))
        row_pt_aa.addWidget(self.hours_aa)
        hours_form.addRow(row_pt_aa)

        self.code_ip_paris = QLineEdit()
        self.code_other = QLineEdit()
        self.code_other.setPlaceholderText("ex. code mutualisé, autre établissement…")

        self.description = QTextEdit()
        self.description.setPlaceholderText("Notes libres")
        self.description.setMaximumHeight(72)
        self.mcc_text = QTextEdit()
        self.mcc_text.setPlaceholderText("Modalités de contrôle des connaissances (texte maquette / MCC)")
        self.mcc_text.setMinimumHeight(100)

        self.is_internship = QCheckBox("UE de type stage (suivi dédié dans l’onglet Notes)")
        self.is_internship.toggled.connect(self._on_internship_toggled)
        teacher_box = QGroupBox("Enseignant responsable")
        tf = QFormLayout(teacher_box)
        self.teacher_last_name = QLineEdit()
        self.teacher_first_name = QLineEdit()
        self.teacher_email_work = QLineEdit()
        self.teacher_email_work_2 = QLineEdit()
        self.teacher_email_personal = QLineEdit()
        self.teacher_phone_work = QLineEdit()
        self.teacher_phone_work_2 = QLineEdit()
        self.teacher_phone_mobile = QLineEdit()
        self.teacher_institution = QLineEdit()
        tf.addRow("Nom", self.teacher_last_name)
        tf.addRow("Prénom", self.teacher_first_name)
        tf.addRow(EMAIL_LABELS_FR[EMAIL_KEYS[0]], self.teacher_email_work)
        tf.addRow(EMAIL_LABELS_FR[EMAIL_KEYS[1]], self.teacher_email_work_2)
        tf.addRow(EMAIL_LABELS_FR[EMAIL_KEYS[2]], self.teacher_email_personal)
        tf.addRow(PHONE_LABELS_FR[PHONE_KEYS[0]], self.teacher_phone_work)
        tf.addRow(PHONE_LABELS_FR[PHONE_KEYS[1]], self.teacher_phone_work_2)
        tf.addRow(PHONE_LABELS_FR[PHONE_KEYS[2]], self.teacher_phone_mobile)
        tf.addRow("Établissement", self.teacher_institution)

        carrier_box = QGroupBox("Porteur MNE")
        cf = QFormLayout(carrier_box)
        self.carrier_partner = QComboBox()
        for label, data in MNE_CARRIER_PARTNERS:
            self.carrier_partner.addItem(label, data)
        self.carrier_partner_other = QLineEdit()
        self.carrier_partner_other.setPlaceholderText("Préciser si « Autre »")
        self.carrier_partner.currentIndexChanged.connect(self._carrier_changed)
        cf.addRow("Établissement partenaire", self.carrier_partner)
        cf.addRow("", self.carrier_partner_other)

        syllabus_box = QGroupBox("Syllabus (PDF ou Word)")
        sb = QVBoxLayout(syllabus_box)
        self.syllabus_label = QLabel("Aucun fichier")
        self.syllabus_label.setWordWrap(True)
        sb.addWidget(self.syllabus_label)
        srow = QHBoxLayout()
        self.syllabus_pick_btn = QPushButton("Choisir un fichier…")
        self.syllabus_pick_btn.clicked.connect(self._pick_syllabus)
        self.syllabus_open_btn = QPushButton("Ouvrir")
        self.syllabus_open_btn.clicked.connect(self._open_syllabus)
        self.syllabus_clear_btn = QPushButton("Retirer")
        self.syllabus_clear_btn.clicked.connect(self._clear_syllabus)
        srow.addWidget(self.syllabus_pick_btn)
        srow.addWidget(self.syllabus_open_btn)
        srow.addWidget(self.syllabus_clear_btn)
        srow.addStretch()
        sb.addLayout(srow)

        form.addRow("Code UE (nomenclature MNE)", self.mne_module_code)
        form.addRow("Code Apogée (principal)", self.code)
        form.addRow("Code IP Paris (optionnel)", self.code_ip_paris)
        form.addRow("Autre code établissement", self.code_other)
        form.addRow("Intitulé", self.name)
        form.addRow("ECTS", self.ects)
        form.addRow("Semestre", self.semester)
        form.addRow("EAD", self.ead_flag)
        form.addRow(hours_box)
        form.addRow("Notes", self.description)
        form.addRow("Modalités MCC (maquette)", self.mcc_text)
        form.addRow(self.is_internship)
        form.addRow(teacher_box)
        form.addRow(carrier_box)
        form.addRow(syllabus_box)

        if course is not None:
            mne = str(course.get("mne_module_code") or "").strip()
            if mne:
                mi = self.mne_module_code.findData(mne)
                if mi >= 0:
                    self.mne_module_code.setCurrentIndex(mi)
                else:
                    self.mne_module_code.setEditText(mne)
            self.code.setText(str(course.get("code") or ""))
            self.name.setText(str(course.get("name") or ""))
            self.ects.setValue(float(course.get("ects") or 0))
            self.description.setPlainText(str(course.get("description") or ""))
            self.hours_total.setValue(float(course.get("hours_total") or 0))
            self.hours_cm.setValue(float(course.get("hours_cm") or 0))
            self.hours_td.setValue(float(course.get("hours_td") or 0))
            self.hours_tp.setValue(float(course.get("hours_tp") or 0))
            self.hours_project.setValue(float(course.get("hours_project") or 0))
            self.hours_pt.setValue(float(course.get("hours_pt") or 0))
            self.hours_aa.setValue(float(course.get("hours_aa") or 0))
            self.code_ip_paris.setText(str(course.get("code_ip_paris") or ""))
            self.code_other.setText(str(course.get("code_other") or ""))
            self.semester.setText(str(course.get("semester") or ""))
            self.mcc_text.setPlainText(str(course.get("mcc_text") or ""))
            self.ead_flag.setText(str(course.get("ead_flag") or ""))
            self.is_internship.setChecked(
                str(course.get("course_type") or "").strip().lower() == "internship"
            )
            self.teacher_last_name.setText(str(course.get("teacher_last_name") or ""))
            self.teacher_first_name.setText(str(course.get("teacher_first_name") or ""))
            ew, ew2, ep = read_emails(course, prefix="teacher")
            self.teacher_email_work.setText(ew)
            self.teacher_email_work_2.setText(ew2)
            self.teacher_email_personal.setText(ep)
            tw, tw2, tm = read_phones(course, prefix="teacher")
            self.teacher_phone_work.setText(tw)
            self.teacher_phone_work_2.setText(tw2)
            self.teacher_phone_mobile.setText(tm)
            self.teacher_institution.setText(str(course.get("teacher_institution") or ""))
            cp = str(course.get("carrier_partner") or "").strip()
            ci = self.carrier_partner.findData(cp)
            if ci >= 0:
                self.carrier_partner.setCurrentIndex(ci)
            elif cp:
                oi = self.carrier_partner.findData(OTHER_CARRIER_DATA)
                if oi >= 0:
                    self.carrier_partner.setCurrentIndex(oi)
            self.carrier_partner_other.setText(str(course.get("carrier_partner_other") or cp or ""))

        self._carrier_changed()
        self._refresh_syllabus_label()
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _carrier_changed(self) -> None:
        other = self.carrier_partner.currentData() == OTHER_CARRIER_DATA
        self.carrier_partner_other.setVisible(other)

    def _on_mne_module_picked(self) -> None:
        data = self.mne_module_code.currentData()
        if not data:
            return
        self._apply_mne_catalog(str(data))

    def _apply_mne_catalog(self, code: str) -> None:
        mod = lookup_mne_module(code)
        if mod is None:
            return
        if not self.name.text().strip():
            self.name.setText(mod.title)
        if float(self.ects.value() or 0) == 0:
            self.ects.setValue(float(mod.ects))

    def mne_module_code_value(self) -> str:
        data = self.mne_module_code.currentData()
        if data:
            return normalize_mne_module_code(str(data))
        return normalize_mne_module_code(self.mne_module_code.currentText())

    def carrier_partner_value(self) -> str:
        if self.carrier_partner.currentData() == OTHER_CARRIER_DATA:
            return self.carrier_partner_other.text().strip()
        return str(self.carrier_partner.currentData() or "").strip()

    def fields_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.text().strip(),
            "ects": self.ects.value(),
            "description": self.description.toPlainText().strip(),
            "hours_total": self.hours_total.value(),
            "hours_cm": self.hours_cm.value(),
            "hours_td": self.hours_td.value(),
            "hours_tp": self.hours_tp.value(),
            "hours_project": self.hours_project.value(),
            "hours_pt": self.hours_pt.value(),
            "hours_aa": self.hours_aa.value(),
            "code_ip_paris": self.code_ip_paris.text().strip(),
            "code_other": self.code_other.text().strip(),
            "mne_module_code": self.mne_module_code_value(),
            "semester": self.semester.text().strip(),
            "mcc_text": self.mcc_text.toPlainText().strip(),
            "ead_flag": self.ead_flag.text().strip(),
            "course_type": "internship" if self.is_internship.isChecked() else "standard",
            "teacher_last_name": self.teacher_last_name.text().strip(),
            "teacher_first_name": self.teacher_first_name.text().strip(),
            "teacher_email_work": self.teacher_email_work.text().strip(),
            "teacher_email_work_2": self.teacher_email_work_2.text().strip(),
            "teacher_email_personal": self.teacher_email_personal.text().strip(),
            "teacher_email": self.teacher_email_work.text().strip(),
            "teacher_phone_work": self.teacher_phone_work.text().strip(),
            "teacher_phone_work_2": self.teacher_phone_work_2.text().strip(),
            "teacher_phone_mobile": self.teacher_phone_mobile.text().strip(),
            "teacher_phone": self.teacher_phone_mobile.text().strip(),
            "teacher_institution": self.teacher_institution.text().strip(),
            "carrier_partner": self.carrier_partner_value(),
            "carrier_partner_other": (
                self.carrier_partner_other.text().strip()
                if self.carrier_partner.currentData() == OTHER_CARRIER_DATA
                else ""
            ),
        }

    def _syllabus_display_name(self) -> str:
        if self._pending_syllabus is not None:
            return self._pending_syllabus.name
        if self._stored_syllabus_name:
            return self._stored_syllabus_name
        if self._stored_syllabus_path:
            return Path(self._stored_syllabus_path).name
        return ""

    def _refresh_syllabus_label(self) -> None:
        name = self._syllabus_display_name()
        if self._syllabus_remove_pending:
            self.syllabus_label.setText("Aucun fichier (sera retiré à l'enregistrement)")
            has_file = False
        elif name:
            pending = " (en attente)" if self._pending_syllabus is not None else ""
            self.syllabus_label.setText(f"{name}{pending}")
            has_file = True
        else:
            self.syllabus_label.setText("Aucun fichier")
            has_file = False
        self.syllabus_open_btn.setEnabled(has_file)
        self.syllabus_clear_btn.setEnabled(has_file)

    def _pick_syllabus(self) -> None:
        filters = "Documents (*.pdf *.doc *.docx);;PDF (*.pdf);;Word (*.doc *.docx)"
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Syllabus de l'UE",
            str(Path.home() / "Documents"),
            filters,
        )
        if not path:
            return
        src = Path(path)
        if src.suffix.lower() not in COURSE_SYLLABUS_SUFFIXES:
            QMessageBox.warning(
                self,
                "Syllabus",
                "Format non pris en charge (PDF ou Word attendu).",
            )
            return
        self._pending_syllabus = src
        self._syllabus_remove_pending = False
        self._refresh_syllabus_label()

    def _open_syllabus(self) -> None:
        if self._pending_syllabus is not None:
            p = self._pending_syllabus
        elif self._stored_syllabus_path and not self._syllabus_remove_pending:
            p = abs_path_from_stored(self._stored_syllabus_path)
        else:
            return
        if not p.is_file():
            QMessageBox.warning(self, "Syllabus", "Fichier introuvable.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["start", "", str(p)], shell=True, check=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False)
        except Exception as exc:
            QMessageBox.warning(self, "Syllabus", str(exc))

    def _clear_syllabus(self) -> None:
        self._pending_syllabus = None
        self._syllabus_remove_pending = bool(self._stored_syllabus_path)
        self._refresh_syllabus_label()

    def apply_syllabus(self, repo: Any, course_id: int) -> None:
        if self._syllabus_remove_pending:
            repo.clear_course_syllabus(int(course_id))
            self._stored_syllabus_path = ""
            self._stored_syllabus_name = ""
            self._syllabus_remove_pending = False
        if self._pending_syllabus is not None:
            repo.import_course_syllabus(int(course_id), self._pending_syllabus)
            row = repo.get_course(int(course_id)) or {}
            self._stored_syllabus_path = str(row.get("syllabus_path") or "")
            self._stored_syllabus_name = str(row.get("syllabus_filename") or "")
            self._pending_syllabus = None
        self._refresh_syllabus_label()

    def _on_internship_toggled(self, checked: bool) -> None:
        if not checked:
            return
        if self.mcc_text.toPlainText().strip():
            return
        from ..services.internship_grades import INTERNSHIP_MCC_TEXT

        self.mcc_text.setPlainText(INTERNSHIP_MCC_TEXT)

    def _try_accept(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if not self.code.text().strip() or not self.name.text().strip():
            QMessageBox.warning(self, "Course", "Code et intitulé sont obligatoires.")
            return
        self.accept()


class AssessmentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add assessment")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.kind = QComboBox()
        # Types utilisés dans les MCC (maquette) — ex: CC (contrôle continu), EE (examen écrit), EO (examen oral).
        # L’app calcule la moyenne via le champ `coefficient`, le `kind` sert surtout à structurer/sortir dans l’UI.
        self.kind.addItems(
            [
                "CC",
                "EE",
                "EO",
                "CT",
                "TP",
                "ORAL",
                "CCTP",
                "PROJET",
                "RATTRAPAGE",
            ]
        )
        self.coefficient = QDoubleSpinBox()
        self.coefficient.setRange(0, 100)
        self.coefficient.setDecimals(2)
        self.coefficient.setValue(1.0)
        self.session = QSpinBox()
        self.session.setRange(1, 2)
        self.display_order = QSpinBox()
        self.display_order.setRange(0, 100)
        form.addRow("Name", self.name)
        form.addRow("Kind", self.kind)
        form.addRow("Coefficient", self.coefficient)
        form.addRow("Session", self.session)
        form.addRow("Display order", self.display_order)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TemplateDialog(QDialog):
    """Nouvelle maquette ou édition : une maquette = année + niveau + parcours (liste officielle M1/M2)."""

    def __init__(
        self,
        parent=None,
        template: dict[str, Any] | None = None,
        lineage_text: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Modifier la maquette" if template is not None else "Nouvelle maquette")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.academic_year = QLineEdit()
        self.academic_year.setPlaceholderText("ex. 2025-2026")
        self.version = QLineEdit("1")

        self.level_combo = QComboBox()
        for lv in STANDARD_LEVELS:
            self.level_combo.addItem(lv, lv)
        self.level_combo.addItem("Autre (niveau libre)", OTHER_LEVEL_DATA)
        self.level_other = QLineEdit()
        self.level_other.setPlaceholderText("Niveau")
        self.level_row = QWidget()
        lr = QHBoxLayout(self.level_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.addWidget(self.level_combo)
        lr.addWidget(self.level_other, 1)

        self.track_combo = QComboBox()
        self.track_other = QLineEdit()
        self.track_other.setPlaceholderText("Acronyme parcours")
        self.track_row = QWidget()
        tr = QHBoxLayout(self.track_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.addWidget(self.track_combo)
        tr.addWidget(self.track_other, 1)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Laisser vide pour : année — niveau parcours")

        self.level_combo.currentIndexChanged.connect(self._tpl_level_changed)
        self.track_combo.currentIndexChanged.connect(self._tpl_track_changed)
        self.academic_year.textChanged.connect(self._maybe_sync_placeholder_name)

        form.addRow("Année universitaire", self.academic_year)
        form.addRow("Niveau", self.level_row)
        form.addRow("Parcours", self.track_row)
        form.addRow("Nom (affichage)", self.name)
        form.addRow("Version", self.version)
        layout.addLayout(form)
        hint = QLabel(
            "<small>M1 : parcours <b>P</b> ou <b>C</b>. M2 : <b>NPD</b>, <b>NPO</b>, <b>DWM</b>, "
            "<b>NFC</b>, <b>NRPE</b>. Chaque combinaison année+niveau+parcours correspond à une maquette. "
            "Pour un changement de structure, préférez <b>Dupliquer</b> plutôt que modifier une maquette "
            "d’une année passée.</small>"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if str(lineage_text or "").strip():
            lineage = QLabel(f"<small><i>Filiation :</i> {lineage_text}</small>")
            lineage.setWordWrap(True)
            layout.addWidget(lineage)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Prefill for edit mode
        if template is not None:
            self.academic_year.setText(str(template.get("academic_year") or ""))
            self.version.setText(str(template.get("version") or "1"))
            self.name.setText(str(template.get("name") or ""))

            lv = str(template.get("level") or "").strip().upper()
            trv = str(template.get("track") or "").strip().upper()

            if lv:
                idx = self.level_combo.findData(lv)
                if idx >= 0:
                    self.level_combo.setCurrentIndex(idx)
                else:
                    self.level_combo.setCurrentIndex(self.level_combo.findData(OTHER_LEVEL_DATA))
                    self.level_other.setText(lv)

            # Build track choices based on level selection
            self._tpl_level_changed()

            if trv:
                if self.track_combo.isVisible():
                    tidx = self.track_combo.findData(trv)
                    if tidx >= 0:
                        self.track_combo.setCurrentIndex(tidx)
                    else:
                        self.track_combo.setCurrentIndex(self.track_combo.findData(OTHER_TRACK_DATA))
                        self.track_other.setText(trv)
                    self._tpl_track_changed()
                else:
                    self.track_other.setText(trv)
        else:
            self._tpl_level_changed()

    def _tpl_level_changed(self) -> None:
        is_other = self.level_combo.currentData() == OTHER_LEVEL_DATA
        self.level_other.setVisible(is_other)
        if is_other:
            self.track_combo.blockSignals(True)
            self.track_combo.clear()
            self.track_combo.setVisible(False)
            self.track_combo.blockSignals(False)
            self.track_other.setVisible(True)
            self._maybe_sync_placeholder_name()
            return
        self.track_combo.setVisible(True)
        lvl = str(self.level_combo.currentData() or "")
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        for code, lab in PARCOURS_BY_LEVEL.get(lvl, ()):
            self.track_combo.addItem(f"{lab} ({code})", code)
        self.track_combo.addItem("Autre (saisie libre)", OTHER_TRACK_DATA)
        self.track_combo.blockSignals(False)
        self._tpl_track_changed()
        self._maybe_sync_placeholder_name()

    def _tpl_track_changed(self) -> None:
        if not self.track_combo.isVisible():
            return
        other = self.track_combo.currentData() == OTHER_TRACK_DATA
        self.track_other.setVisible(other)
        self._maybe_sync_placeholder_name()

    def _maybe_sync_placeholder_name(self) -> None:
        if self.name.text().strip():
            return
        y = self.academic_year.text().strip()
        lv = self.level_value()
        tr = self.track_value()
        ph = suggested_maquette_name(y, lv, tr)
        self.name.setPlaceholderText(ph or "Nom de la maquette")

    def level_value(self) -> str:
        if self.level_combo.currentData() == OTHER_LEVEL_DATA:
            return self.level_other.text().strip()
        return str(self.level_combo.currentData() or "").strip()

    def track_value(self) -> str:
        if not self.track_combo.isVisible():
            return self.track_other.text().strip()
        if self.track_combo.currentData() == OTHER_TRACK_DATA:
            return self.track_other.text().strip()
        return str(self.track_combo.currentData() or "").strip()

    def effective_name(self) -> str:
        raw = self.name.text().strip()
        if raw:
            return raw
        return suggested_maquette_name(
            self.academic_year.text().strip(),
            self.level_value(),
            self.track_value(),
        )

    def _try_accept(self) -> None:
        if not self.academic_year.text().strip():
            QMessageBox.warning(self, "Maquette", "Indiquez l’année universitaire.")
            return
        lv = self.level_value()
        tr = self.track_value()
        if not lv or not tr:
            QMessageBox.warning(self, "Maquette", "Indiquez le niveau et le parcours.")
            return
        if not self.effective_name():
            QMessageBox.warning(self, "Maquette", "Indiquez un nom ou des champs pour le nom automatique.")
            return
        self.accept()


from ..services.timetable_legacy import course_public_code


class AddCourseToTemplateDialog(QDialog):
    def __init__(self, courses: list[dict], parent=None, *, academic_year: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Add course to template")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.course = QComboBox()
        ay = str(academic_year or "").strip()
        for c in courses:
            pub = course_public_code(c, academic_year=ay)
            self.course.addItem(f"{pub} - {c['name']}", c["id"])
        self.block_name = QLineEdit()
        self.global_coefficient = QDoubleSpinBox()
        self.global_coefficient.setRange(0, 100)
        self.global_coefficient.setValue(1.0)
        self.display_order = QSpinBox()
        self.display_order.setRange(0, 100)
        form.addRow("Course", self.course)
        form.addRow("Block", self.block_name)
        form.addRow("Global coefficient", self.global_coefficient)
        form.addRow("Display order", self.display_order)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class EditMaquettePlacementDialog(QDialog):
    """Bloc, pondération et ordre d’un cours dans une maquette (template)."""

    def __init__(
        self,
        parent=None,
        block_name: str = "",
        global_coefficient: float = 1.0,
        display_order: int = 0,
        optional: int = 0,
        free_ue: int = 0,
    ):
        super().__init__(parent)
        self.setWindowTitle("Placement in Maquette")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.block_name = QLineEdit()
        self.block_name.setText(block_name)
        self.block_name.setPlaceholderText("ex. Bloc 1 — cours communs")
        self.global_coefficient = QDoubleSpinBox()
        self.global_coefficient.setRange(0, 100)
        self.global_coefficient.setDecimals(2)
        self.global_coefficient.setValue(float(global_coefficient))
        self.display_order = QSpinBox()
        self.display_order.setRange(0, 500)
        self.display_order.setValue(int(display_order))
        self.optional = QSpinBox()
        self.optional.setRange(0, 1)
        self.optional.setValue(int(optional))
        self.optional.setToolTip("1 = UE optionnelle (exclue des moyennes bloc / année)")
        self.free_ue = QCheckBox("UE libre (ECTS validables sans note)")
        self.free_ue.setToolTip(
            "Différent de l'optionnel et de la neutralisation « Garder » : l'étudiant peut "
            "valider les ECTS sans saisie de notes (ex. mobilité, crédits antérieurs)."
        )
        self.free_ue.setChecked(bool(int(free_ue)))
        form.addRow("Block name", self.block_name)
        form.addRow("Pondération (= ECTS)", self.global_coefficient)
        self.global_coefficient.setToolTip(
            "Utilisée pour les moyennes bloc/année si les ECTS de l’UE sont à 0 ; "
            "sinon les ECTS priment automatiquement."
        )
        form.addRow("Display order", self.display_order)
        form.addRow("Optional (0/1)", self.optional)
        form.addRow("", self.free_ue)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
