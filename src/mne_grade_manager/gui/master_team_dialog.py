"""Gestion de l'équipe pédagogique du master (mention, parcours, secrétariats)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.master_team import (
    MENTION_DIRECTOR_COUNT,
    MNE_TEAM_AFFILIATIONS,
    ROLE_SECRETARIAT,
    STUDENT_REP_COUNT_M1,
    STUDENT_REP_COUNT_M2_PER_TRACK,
    all_track_pairs,
    decode_tracks_scope,
    encode_tracks_scope,
    m2_track_pairs,
    mention_director_label,
    tracks_scope_label,
    track_director_table_rows,
)
from ..services.contact_emails import EMAIL_KEYS, EMAIL_LABELS_FR, any_email, primary_email, read_emails
from ..services.contact_phones import PHONE_KEYS, PHONE_LABELS_FR, any_phone, read_phones
from ..services.repository import Repository

_EMAIL_HEADERS: tuple[str, ...] = tuple(EMAIL_LABELS_FR[k] for k in EMAIL_KEYS)
_PHONE_HEADERS: tuple[str, ...] = tuple(PHONE_LABELS_FR[k] for k in PHONE_KEYS)
_MENTION_EMAIL_COL = 5
_MENTION_PHONE_COL = _MENTION_EMAIL_COL + len(EMAIL_KEYS)
_TRACK_EMAIL_COL = 6
_TRACK_PHONE_COL = _TRACK_EMAIL_COL + len(EMAIL_KEYS)


def _affiliation_text(row: dict[str, Any] | None) -> str:
    data = row or {}
    return str(data.get("affiliation") or data.get("institution") or "").strip()


def _make_affiliation_combo() -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItem("", "")
    for inst in MNE_TEAM_AFFILIATIONS:
        combo.addItem(inst, inst)
    return combo


def _set_affiliation_combo(combo: QComboBox | None, value: str) -> None:
    if combo is None:
        return
    text = str(value or "").strip()
    idx = combo.findText(text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setEditText(text)


def _affiliation_combo_text(combo: QComboBox | None) -> str:
    if combo is None:
        return ""
    return combo.currentText().strip()

_EDITABLE = (
    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable
)
_READ_ONLY = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

_TABLE_EDIT_TRIGGERS = (
    QAbstractItemView.EditTrigger.DoubleClicked
    | QAbstractItemView.EditTrigger.AnyKeyPressed
    | QAbstractItemView.EditTrigger.EditKeyPressed
)


def _person_label(row: dict[str, Any]) -> str:
    ln = str(row.get("last_name") or "").strip()
    fn = str(row.get("first_name") or "").strip()
    name = f"{ln} {fn}".strip() or "—"
    extra = primary_email(row)
    if extra:
        return f"{name} ({extra})"
    return name


def _student_combo_label(student: dict[str, Any]) -> str:
    ln = str(student.get("last_name") or "").strip()
    fn = str(student.get("first_name") or "").strip()
    label = f"{ln} {fn}".strip()
    tr = str(student.get("track") or "").strip()
    if tr:
        label = f"{label} ({tr})" if label else tr
    return label or f"Étudiant #{student.get('id')}"


def _fill_student_combo(
    combo: QComboBox, students: list[dict[str, Any]], selected_id: int | None
) -> None:
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("—", None)
    for student in students:
        sid = int(student["id"])
        combo.addItem(_student_combo_label(student), sid)
    if selected_id:
        idx = combo.findData(int(selected_id))
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.blockSignals(False)


class MasterTeamDialog(QDialog):
    def __init__(self, repo: Repository, academic_year: str, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.academic_year = (academic_year or "").strip()
        self.setWindowTitle("Équipe du master")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(640, 420)
        self._initial_geometry_done = False

        root = QVBoxLayout(self)
        header = QLabel(
            f"Millésime : <b>{self.academic_year or '—'}</b> — "
            "directeurs de la mention, responsables de parcours et secrétariats pédagogiques."
        )
        header.setWordWrap(True)
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_mention_tab(), "Directeurs de la mention")
        self.tabs.addTab(self._build_track_tab(), "Responsables de parcours")
        self.tabs.addTab(self._build_student_rep_tab(), "Représentants étudiants")
        self.tabs.addTab(self._build_secretariat_tab(), "Secrétariats pédagogiques")
        root.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("Fermer")
        root.addWidget(buttons)

        self._load_all()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._initial_geometry_done:
            from .screen_layout import adapt_window_size

            adapt_window_size(
                self,
                preferred=(1320, 660),
                minimum=(800, 480),
                screen_fraction=0.92,
            )
            self._initial_geometry_done = True
        self.raise_()
        self.activateWindow()

    # —— Directeurs de la mention (3 postes) ——

    def _build_mention_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            f"Les {MENTION_DIRECTOR_COUNT} directeurs de la mention MNE — libellés de poste personnalisables "
            "(ex. UPSay, INSTN, écoles). Double-cliquez une cellule ou tapez après l’avoir sélectionnée. "
            "Cliquez sur « Enregistrer » pour sauvegarder."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(hint)

        self.mention_table = QTableWidget(MENTION_DIRECTOR_COUNT, 5 + len(EMAIL_KEYS) + len(PHONE_KEYS))
        self.mention_table.setHorizontalHeaderLabels(
            ["Poste", "Nom", "Prénom", "Titre", "Affiliation", *_EMAIL_HEADERS, *_PHONE_HEADERS]
        )
        self.mention_table.setEditTriggers(_TABLE_EDIT_TRIGGERS)
        self.mention_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.mention_table.verticalHeader().setVisible(False)
        self.mention_table.horizontalHeader().setStretchLastSection(True)
        for slot in range(MENTION_DIRECTOR_COUNT):
            poste = QTableWidgetItem(mention_director_label(slot))
            poste.setFlags(_EDITABLE)
            self.mention_table.setItem(slot, 0, poste)
            for col in range(1, 4):
                it = QTableWidgetItem("")
                it.setFlags(_EDITABLE)
                self.mention_table.setItem(slot, col, it)
            self.mention_table.setCellWidget(slot, 4, _make_affiliation_combo())
            for col in range(5, _MENTION_PHONE_COL + len(PHONE_KEYS)):
                it = QTableWidgetItem("")
                it.setFlags(_EDITABLE)
                self.mention_table.setItem(slot, col, it)
        lay.addWidget(self.mention_table, 1)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_btn = QPushButton("Enregistrer les directeurs de la mention")
        save_btn.clicked.connect(self._save_mention_directors)
        save_row.addWidget(save_btn)
        lay.addLayout(save_row)
        return w

    def _cell_text(self, table: QTableWidget, row: int, col: int) -> str:
        it = table.item(row, col)
        return (it.text() if it else "").strip()

    def _cell_phones(self, table: QTableWidget, row: int, start_col: int) -> tuple[str, str, str]:
        return tuple(self._cell_text(table, row, start_col + i) for i in range(len(PHONE_KEYS)))

    def _cell_emails(self, table: QTableWidget, row: int, start_col: int) -> tuple[str, str, str]:
        return tuple(self._cell_text(table, row, start_col + i) for i in range(len(EMAIL_KEYS)))

    def _set_row_phones(
        self, table: QTableWidget, row: int, start_col: int, data: dict[str, Any]
    ) -> None:
        work, work2, mobile = read_phones(data)
        for col, val in zip(range(start_col, start_col + len(PHONE_KEYS)), (work, work2, mobile)):
            it = table.item(row, col)
            if it is not None:
                it.setText(val)

    def _set_row_emails(
        self, table: QTableWidget, row: int, start_col: int, data: dict[str, Any]
    ) -> None:
        work, work2, personal = read_emails(data)
        for col, val in zip(
            range(start_col, start_col + len(EMAIL_KEYS)), (work, work2, personal)
        ):
            it = table.item(row, col)
            if it is not None:
                it.setText(val)

    def _load_mention_table(self) -> None:
        directors = self.repo.list_mention_directors(self.academic_year)
        fields = ("last_name", "first_name", "title")
        for slot, row in enumerate(directors):
            poste_it = self.mention_table.item(slot, 0)
            if poste_it is not None:
                custom = str(row.get("post_label") or "").strip()
                poste_it.setText(custom or mention_director_label(slot))
            for col, key in enumerate(fields, start=1):
                it = self.mention_table.item(slot, col)
                if it is not None:
                    it.setText(str(row.get(key) or ""))
            _set_affiliation_combo(
                self.mention_table.cellWidget(slot, 4),
                _affiliation_text(row),
            )
            self._set_row_emails(self.mention_table, slot, _MENTION_EMAIL_COL, row)
            self._set_row_phones(self.mention_table, slot, _MENTION_PHONE_COL, row)

    def _save_mention_directors(self) -> None:
        existing = self.repo.list_mention_directors(self.academic_year)
        try:
            for slot in range(MENTION_DIRECTOR_COUNT):
                poste = self._cell_text(self.mention_table, slot, 0)
                default_poste = mention_director_label(slot)
                ln = self._cell_text(self.mention_table, slot, 1)
                fn = self._cell_text(self.mention_table, slot, 2)
                title = self._cell_text(self.mention_table, slot, 3)
                inst = _affiliation_combo_text(self.mention_table.cellWidget(slot, 4))
                email_work, email_work_2, email_personal = self._cell_emails(
                    self.mention_table, slot, _MENTION_EMAIL_COL
                )
                phone_work, phone_work_2, phone_mobile = self._cell_phones(
                    self.mention_table, slot, _MENTION_PHONE_COL
                )
                has_person = any((ln, fn, title, inst)) or any_email(
                    email_work, email_work_2, email_personal
                ) or any_phone(phone_work, phone_work_2, phone_mobile)
                has_custom_poste = bool(poste) and poste != default_poste
                if not has_person and not has_custom_poste:
                    prev = existing[slot] if slot < len(existing) else {}
                    if prev.get("id"):
                        self.repo.delete_master_team_member(int(prev["id"]))
                    continue
                self.repo.upsert_mention_director(
                    self.academic_year,
                    slot,
                    last_name=ln,
                    first_name=fn,
                    title=title,
                    affiliation=inst,
                    email_work=email_work,
                    email_work_2=email_work_2,
                    email_personal=email_personal,
                    phone_work=phone_work,
                    phone_work_2=phone_work_2,
                    phone_mobile=phone_mobile,
                    post_label=poste if poste != default_poste else "",
                )
        except Exception as exc:
            QMessageBox.critical(self, "Directeurs de la mention", str(exc))
            return
        QMessageBox.information(self, "Directeurs de la mention", "Directeurs enregistrés.")

    # —— Parcours ——

    def _build_track_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            "Un responsable par parcours en M1 (P, C). "
            "Deux responsables par parcours en M2 (NPD, NPO, DWM, NFC, NRPE). "
            "Double-cliquez une cellule éditable ou tapez après l’avoir sélectionnée. "
            "Cliquez sur « Enregistrer » pour sauvegarder."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(hint)

        rows = track_director_table_rows()
        self.track_table = QTableWidget(len(rows), _TRACK_PHONE_COL + len(PHONE_KEYS))
        self.track_table.setHorizontalHeaderLabels(
            ["Niveau", "Parcours", "Nom", "Prénom", "Titre", "Affiliation", *_EMAIL_HEADERS, *_PHONE_HEADERS]
        )
        self.track_table.setEditTriggers(_TABLE_EDIT_TRIGGERS)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.horizontalHeader().setStretchLastSection(True)
        self._track_rows: list[tuple[str, str, int]] = []
        for r, (lv, tr, lab, slot) in enumerate(rows):
            self._track_rows.append((lv, tr, slot))
            for c, text, editable in (
                (0, lv, False),
                (1, lab, False),
                (2, "", True),
                (3, "", True),
                (4, "", True),
            ):
                it = QTableWidgetItem(text)
                it.setFlags(_EDITABLE if editable else _READ_ONLY)
                self.track_table.setItem(r, c, it)
            self.track_table.setCellWidget(r, 5, _make_affiliation_combo())
            for c in range(6, _TRACK_PHONE_COL + len(PHONE_KEYS)):
                it = QTableWidgetItem("")
                it.setFlags(_EDITABLE)
                self.track_table.setItem(r, c, it)
        lay.addWidget(self.track_table, 1)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_btn = QPushButton("Enregistrer les responsables de parcours")
        save_btn.clicked.connect(self._save_track_directors)
        save_row.addWidget(save_btn)
        lay.addLayout(save_row)
        return w

    def _load_track_table(self) -> None:
        for r, (lv, tr, slot) in enumerate(self._track_rows):
            directors = self.repo.list_track_directors(self.academic_year, lv, tr)
            row = directors[slot] if slot < len(directors) else {}
            for col, key in enumerate(("last_name", "first_name", "title"), start=2):
                it = self.track_table.item(r, col)
                if it is not None:
                    it.setText(str(row.get(key) or ""))
            _set_affiliation_combo(
                self.track_table.cellWidget(r, 5),
                _affiliation_text(row),
            )
            self._set_row_emails(self.track_table, r, _TRACK_EMAIL_COL, row)
            self._set_row_phones(self.track_table, r, _TRACK_PHONE_COL, row)

    def _save_track_directors(self) -> None:
        try:
            for r, (lv, tr, slot) in enumerate(self._track_rows):
                ln = (self.track_table.item(r, 2).text() if self.track_table.item(r, 2) else "").strip()
                fn = (self.track_table.item(r, 3).text() if self.track_table.item(r, 3) else "").strip()
                title = (self.track_table.item(r, 4).text() if self.track_table.item(r, 4) else "").strip()
                affiliation = _affiliation_combo_text(self.track_table.cellWidget(r, 5))
                email_work, email_work_2, email_personal = self._cell_emails(
                    self.track_table, r, _TRACK_EMAIL_COL
                )
                phone_work, phone_work_2, phone_mobile = self._cell_phones(
                    self.track_table, r, _TRACK_PHONE_COL
                )
                self.repo.upsert_track_director(
                    self.academic_year,
                    lv,
                    tr,
                    slot,
                    last_name=ln,
                    first_name=fn,
                    title=title,
                    affiliation=affiliation,
                    email_work=email_work,
                    email_work_2=email_work_2,
                    email_personal=email_personal,
                    phone_work=phone_work,
                    phone_work_2=phone_work_2,
                    phone_mobile=phone_mobile,
                )
        except Exception as exc:
            QMessageBox.critical(self, "Parcours", str(exc))
            return
        QMessageBox.information(self, "Parcours", "Responsables de parcours enregistrés.")

    # —— Représentants étudiants ——

    def _build_student_rep_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            "Deux représentants pour la promotion M1 (ensemble du niveau), "
            "deux par parcours en M2. Choisissez un étudiant inscrit sur le millésime courant "
            "ou laissez vide pour effacer. Cliquez sur « Enregistrer » pour sauvegarder."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(hint)

        m1_box = QGroupBox("M1 — promotion entière")
        m1_form = QFormLayout(m1_box)
        self._m1_rep_combos: list[QComboBox] = []
        for slot in range(STUDENT_REP_COUNT_M1):
            cb = QComboBox()
            cb.setMinimumWidth(320)
            m1_form.addRow(f"Représentant {slot + 1}", cb)
            self._m1_rep_combos.append(cb)
        lay.addWidget(m1_box)

        m2_pairs = m2_track_pairs()
        self._m2_rep_table = QTableWidget(len(m2_pairs), 1 + STUDENT_REP_COUNT_M2_PER_TRACK)
        self._m2_rep_table.setHorizontalHeaderLabels(
            ["Parcours"] + [f"Représentant {i + 1}" for i in range(STUDENT_REP_COUNT_M2_PER_TRACK)]
        )
        self._m2_rep_table.verticalHeader().setVisible(False)
        self._m2_rep_table.horizontalHeader().setStretchLastSection(True)
        self._m2_rep_combos: list[tuple[str, str, int, QComboBox]] = []
        for row, (_lv, tr, lab) in enumerate(m2_pairs):
            parcours_it = QTableWidgetItem(lab)
            parcours_it.setFlags(_READ_ONLY)
            self._m2_rep_table.setItem(row, 0, parcours_it)
            for slot in range(STUDENT_REP_COUNT_M2_PER_TRACK):
                cb = QComboBox()
                cb.setMinimumWidth(240)
                self._m2_rep_table.setCellWidget(row, 1 + slot, cb)
                self._m2_rep_combos.append(("M2", tr, slot, cb))
        lay.addWidget(self._m2_rep_table, 1)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_btn = QPushButton("Enregistrer les représentants étudiants")
        save_btn.clicked.connect(self._save_student_reps)
        save_row.addWidget(save_btn)
        lay.addLayout(save_row)
        return w

    def _load_student_rep_tab(self) -> None:
        m1_students = self.repo.list_students_for_track(
            academic_year=self.academic_year, level="M1"
        )
        m1_reps = self.repo.list_student_representatives_m1(self.academic_year)
        for slot, combo in enumerate(self._m1_rep_combos):
            rep = m1_reps[slot] if slot < len(m1_reps) else {}
            sid = rep.get("student_id")
            selected = int(sid) if sid else None
            _fill_student_combo(combo, m1_students, selected)

        m2_reps = self.repo.list_student_representatives_m2(self.academic_year)
        for lv, tr, slot, combo in self._m2_rep_combos:
            students = self.repo.list_students_for_track(
                academic_year=self.academic_year, level=lv, track=tr
            )
            reps = m2_reps.get((lv, tr), [])
            rep = reps[slot] if slot < len(reps) else {}
            sid = rep.get("student_id")
            selected = int(sid) if sid else None
            _fill_student_combo(combo, students, selected)

    def _save_student_reps(self) -> None:
        try:
            for slot, combo in enumerate(self._m1_rep_combos):
                sid = combo.currentData()
                self.repo.upsert_student_representative(
                    self.academic_year,
                    "M1",
                    "",
                    slot,
                    student_id=int(sid) if sid else None,
                )
            for lv, tr, slot, combo in self._m2_rep_combos:
                sid = combo.currentData()
                self.repo.upsert_student_representative(
                    self.academic_year,
                    lv,
                    tr,
                    slot,
                    student_id=int(sid) if sid else None,
                )
        except Exception as exc:
            QMessageBox.critical(self, "Représentants étudiants", str(exc))
            return
        QMessageBox.information(self, "Représentants étudiants", "Représentants enregistrés.")
        self._load_student_rep_tab()

    # —— Secrétariat ——

    def _build_secretariat_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)

        self.sec_list = QListWidget()
        self.sec_list.currentRowChanged.connect(self._load_secretariat_form)
        lay.addWidget(self.sec_list, 1)

        right = QVBoxLayout()
        form_box = QGroupBox("Secrétariat")
        form = QFormLayout(form_box)
        self.sec_institution = QComboBox()
        self.sec_institution.setEditable(True)
        for inst in MNE_TEAM_AFFILIATIONS:
            self.sec_institution.addItem(inst, inst)
        self.sec_last = QLineEdit()
        self.sec_first = QLineEdit()
        self.sec_title = QLineEdit()
        self.sec_affiliation = QComboBox()
        self.sec_affiliation.setEditable(True)
        for inst in MNE_TEAM_AFFILIATIONS:
            self.sec_affiliation.addItem(inst, inst)
        self.sec_email_work = QLineEdit()
        self.sec_email_work_2 = QLineEdit()
        self.sec_email_personal = QLineEdit()
        self.sec_phone_work = QLineEdit()
        self.sec_phone_work_2 = QLineEdit()
        self.sec_phone_mobile = QLineEdit()
        self.sec_notes = QTextEdit()
        self.sec_notes.setMaximumHeight(56)
        form.addRow("Établissement", self.sec_institution)
        form.addRow("Nom", self.sec_last)
        form.addRow("Prénom", self.sec_first)
        form.addRow("Titre / fonction", self.sec_title)
        form.addRow("Affiliation", self.sec_affiliation)
        form.addRow(EMAIL_LABELS_FR[EMAIL_KEYS[0]], self.sec_email_work)
        form.addRow(EMAIL_LABELS_FR[EMAIL_KEYS[1]], self.sec_email_work_2)
        form.addRow(EMAIL_LABELS_FR[EMAIL_KEYS[2]], self.sec_email_personal)
        form.addRow(PHONE_LABELS_FR[PHONE_KEYS[0]], self.sec_phone_work)
        form.addRow(PHONE_LABELS_FR[PHONE_KEYS[1]], self.sec_phone_work_2)
        form.addRow(PHONE_LABELS_FR[PHONE_KEYS[2]], self.sec_phone_mobile)
        form.addRow("Notes", self.sec_notes)
        right.addWidget(form_box)

        tracks_box = QGroupBox("Parcours couverts")
        tracks_scroll = QScrollArea()
        tracks_scroll.setWidgetResizable(True)
        tracks_inner = QWidget()
        tracks_lay = QVBoxLayout(tracks_inner)
        self._sec_track_checks: list[tuple[QCheckBox, str, str]] = []
        for lv, tr, lab in all_track_pairs():
            cb = QCheckBox(lab)
            tracks_lay.addWidget(cb)
            self._sec_track_checks.append((cb, lv, tr))
        tracks_lay.addStretch(1)
        tracks_scroll.setWidget(tracks_inner)
        tb_lay = QVBoxLayout(tracks_box)
        tb_lay.addWidget(tracks_scroll)
        right.addWidget(tracks_box, 1)

        btn_row = QHBoxLayout()
        self.sec_add_btn = QPushButton("Ajouter")
        self.sec_add_btn.clicked.connect(self._secretariat_add)
        self.sec_save_btn = QPushButton("Enregistrer")
        self.sec_save_btn.clicked.connect(self._secretariat_save)
        self.sec_del_btn = QPushButton("Supprimer")
        self.sec_del_btn.clicked.connect(self._secretariat_delete)
        btn_row.addWidget(self.sec_add_btn)
        btn_row.addWidget(self.sec_save_btn)
        btn_row.addWidget(self.sec_del_btn)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        lay.addLayout(right, 2)
        self._sec_edit_id: int | None = None
        return w

    def _load_secretariat_list(self) -> None:
        self.sec_list.blockSignals(True)
        self.sec_list.clear()
        for row in self.repo.list_master_team_members(self.academic_year, role_kind=ROLE_SECRETARIAT):
            inst = str(row.get("institution") or "—")
            scope = tracks_scope_label(str(row.get("tracks_scope") or ""))
            it = QListWidgetItem(f"{inst} — {_person_label(row)} [{scope}]")
            it.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.sec_list.addItem(it)
        self.sec_list.blockSignals(False)
        if self.sec_list.count():
            self.sec_list.setCurrentRow(0)
        else:
            self._clear_secretariat_form()

    def _set_secretariat_tracks(self, raw: str) -> None:
        pairs = set(decode_tracks_scope(raw))
        for cb, lv, tr in self._sec_track_checks:
            cb.setChecked((lv, tr) in pairs)

    def _secretariat_tracks_value(self) -> str:
        pairs = [(lv, tr) for cb, lv, tr in self._sec_track_checks if cb.isChecked()]
        return encode_tracks_scope(pairs)

    def _load_secretariat_form(self, row: int) -> None:
        it = self.sec_list.item(row)
        if it is None:
            self._clear_secretariat_form()
            return
        mid = int(it.data(Qt.ItemDataRole.UserRole))
        row_d = self.repo.get_master_team_member(mid)
        if not row_d:
            self._clear_secretariat_form()
            return
        self._sec_edit_id = mid
        inst = str(row_d.get("institution") or "")
        idx = self.sec_institution.findText(inst)
        if idx >= 0:
            self.sec_institution.setCurrentIndex(idx)
        else:
            self.sec_institution.setEditText(inst)
        self.sec_last.setText(str(row_d.get("last_name") or ""))
        self.sec_first.setText(str(row_d.get("first_name") or ""))
        self.sec_title.setText(str(row_d.get("title") or ""))
        aff = _affiliation_text(row_d)
        idx_aff = self.sec_affiliation.findText(aff)
        if idx_aff >= 0:
            self.sec_affiliation.setCurrentIndex(idx_aff)
        else:
            self.sec_affiliation.setEditText(aff)
        ew, ew2, ep = read_emails(row_d)
        self.sec_email_work.setText(ew)
        self.sec_email_work_2.setText(ew2)
        self.sec_email_personal.setText(ep)
        pw, pw2, pm = read_phones(row_d)
        self.sec_phone_work.setText(pw)
        self.sec_phone_work_2.setText(pw2)
        self.sec_phone_mobile.setText(pm)
        self.sec_notes.setPlainText(str(row_d.get("notes") or ""))
        self._set_secretariat_tracks(str(row_d.get("tracks_scope") or ""))

    def _clear_secretariat_form(self) -> None:
        self._sec_edit_id = None
        self.sec_institution.setCurrentIndex(0)
        for w in (
            self.sec_last,
            self.sec_first,
            self.sec_title,
            self.sec_email_work,
            self.sec_email_work_2,
            self.sec_email_personal,
            self.sec_phone_work,
            self.sec_phone_work_2,
            self.sec_phone_mobile,
        ):
            w.clear()
        self.sec_affiliation.setCurrentIndex(0)
        self.sec_affiliation.setEditText("")
        self.sec_notes.clear()
        for cb, _lv, _tr in self._sec_track_checks:
            cb.setChecked(False)

    def _secretariat_add(self) -> None:
        self._clear_secretariat_form()
        self.sec_list.clearSelection()
        self.sec_institution.setFocus()

    def _secretariat_save(self) -> None:
        inst = self.sec_institution.currentText().strip()
        ln = self.sec_last.text().strip()
        fn = self.sec_first.text().strip()
        scope = self._secretariat_tracks_value()
        if not inst:
            QMessageBox.warning(self, "Secrétariat", "Indiquez l'établissement.")
            return
        if not ln and not fn:
            QMessageBox.warning(self, "Secrétariat", "Indiquez au moins un nom ou un prénom.")
            return
        if not scope:
            QMessageBox.warning(self, "Secrétariat", "Cochez au moins un parcours couvert.")
            return
        fields = dict(
            institution=inst,
            tracks_scope=scope,
            last_name=ln,
            first_name=fn,
            title=self.sec_title.text().strip(),
            affiliation=self.sec_affiliation.currentText().strip(),
            email_work=self.sec_email_work.text().strip(),
            email_work_2=self.sec_email_work_2.text().strip(),
            email_personal=self.sec_email_personal.text().strip(),
            phone_work=self.sec_phone_work.text().strip(),
            phone_work_2=self.sec_phone_work_2.text().strip(),
            phone_mobile=self.sec_phone_mobile.text().strip(),
            notes=self.sec_notes.toPlainText().strip(),
        )
        try:
            if self._sec_edit_id is None:
                mid = self.repo.add_master_team_member(
                    self.academic_year, ROLE_SECRETARIAT, **fields
                )
            else:
                mid = self._sec_edit_id
                self.repo.update_master_team_member(mid, **fields)
        except Exception as exc:
            QMessageBox.critical(self, "Secrétariat", str(exc))
            return
        self._sec_edit_id = mid
        self._load_secretariat_list()
        for i in range(self.sec_list.count()):
            it = self.sec_list.item(i)
            if it and int(it.data(Qt.ItemDataRole.UserRole)) == mid:
                self.sec_list.setCurrentRow(i)
                break

    def _secretariat_delete(self) -> None:
        if self._sec_edit_id is None:
            return
        if (
            QMessageBox.question(self, "Supprimer", "Supprimer ce secrétariat ?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.repo.delete_master_team_member(self._sec_edit_id)
        except Exception as exc:
            QMessageBox.critical(self, "Secrétariat", str(exc))
            return
        self._load_secretariat_list()

    def _load_all(self) -> None:
        self._load_mention_table()
        self._load_track_table()
        self._load_student_rep_tab()
        self._load_secretariat_list()


def open_master_team_dialog(repo: Repository, academic_year: str, parent=None) -> None:
    dlg = MasterTeamDialog(repo, academic_year, parent=parent)
    dlg.exec()
