"""Gestion de l'équipe pédagogique du master (mention, parcours, secrétariats)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
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
    MNE_ENROLLMENT_INSTITUTIONS,
    ROLE_SECRETARIAT,
    ROLE_TRACK,
    all_track_pairs,
    decode_tracks_scope,
    encode_tracks_scope,
    mention_director_label,
    tracks_scope_label,
)
from ..services.repository import Repository


def _person_label(row: dict[str, Any]) -> str:
    ln = str(row.get("last_name") or "").strip()
    fn = str(row.get("first_name") or "").strip()
    name = f"{ln} {fn}".strip() or "—"
    extra = str(row.get("email") or "").strip()
    if extra:
        return f"{name} ({extra})"
    return name


class MasterTeamDialog(QDialog):
    def __init__(self, repo: Repository, academic_year: str, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.academic_year = (academic_year or "").strip()
        self.setWindowTitle("Équipe du master")
        self.setMinimumSize(900, 560)
        self.resize(980, 620)

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

    # —— Directeurs de la mention (3 postes) ——

    def _build_mention_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            f"Les {MENTION_DIRECTOR_COUNT} directeurs de la mention MNE (ensemble du master). "
            "Les modifications sont enregistrées ligne par ligne."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(hint)

        self.mention_table = QTableWidget(MENTION_DIRECTOR_COUNT, 7)
        self.mention_table.setHorizontalHeaderLabels(
            ["Poste", "Nom", "Prénom", "Titre", "Institution", "Email", "Téléphone"]
        )
        self.mention_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.mention_table.verticalHeader().setVisible(False)
        self.mention_table.horizontalHeader().setStretchLastSection(True)
        for slot in range(MENTION_DIRECTOR_COUNT):
            poste = QTableWidgetItem(mention_director_label(slot))
            poste.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.mention_table.setItem(slot, 0, poste)
            for col in range(1, 7):
                it = QTableWidgetItem("")
                it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
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

    def _load_mention_table(self) -> None:
        directors = self.repo.list_mention_directors(self.academic_year)
        fields = ("last_name", "first_name", "title", "institution", "email", "phone")
        for slot, row in enumerate(directors):
            for col, key in enumerate(fields, start=1):
                it = self.mention_table.item(slot, col)
                if it is not None:
                    it.setText(str(row.get(key) or ""))

    def _save_mention_directors(self) -> None:
        existing = self.repo.list_mention_directors(self.academic_year)
        try:
            for slot in range(MENTION_DIRECTOR_COUNT):
                ln = self._cell_text(self.mention_table, slot, 1)
                fn = self._cell_text(self.mention_table, slot, 2)
                title = self._cell_text(self.mention_table, slot, 3)
                inst = self._cell_text(self.mention_table, slot, 4)
                email = self._cell_text(self.mention_table, slot, 5)
                phone = self._cell_text(self.mention_table, slot, 6)
                if not any((ln, fn, title, inst, email, phone)):
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
                    institution=inst,
                    email=email,
                    phone=phone,
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
            "Un responsable par parcours et niveau (M1 P, M1 C, M2 NPD…). "
            "Les modifications sont enregistrées ligne par ligne."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        lay.addWidget(hint)

        pairs = all_track_pairs()
        self.track_table = QTableWidget(len(pairs), 7)
        self.track_table.setHorizontalHeaderLabels(
            ["Niveau", "Parcours", "Nom", "Prénom", "Titre", "Email", "Téléphone"]
        )
        self.track_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.horizontalHeader().setStretchLastSection(True)
        self._track_rows: list[tuple[str, str]] = []
        for r, (lv, tr, lab) in enumerate(pairs):
            self._track_rows.append((lv, tr))
            for c, text, flags in (
                (0, lv, Qt.ItemFlag.ItemIsEnabled),
                (1, lab, Qt.ItemFlag.ItemIsEnabled),
                (2, "", Qt.ItemFlag.ItemIsEditable),
                (3, "", Qt.ItemFlag.ItemIsEditable),
                (4, "", Qt.ItemFlag.ItemIsEditable),
                (5, "", Qt.ItemFlag.ItemIsEditable),
                (6, "", Qt.ItemFlag.ItemIsEditable),
            ):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsSelectable | flags)
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
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.repo.list_master_team_members(self.academic_year, role_kind=ROLE_TRACK):
            lv = str(row.get("level") or "").strip().upper()
            tr = str(row.get("track") or "").strip().upper()
            by_key[(lv, tr)] = row
        for r, (lv, tr) in enumerate(self._track_rows):
            row = by_key.get((lv, tr), {})
            for col, key in enumerate(
                ("last_name", "first_name", "title", "email", "phone"), start=2
            ):
                it = self.track_table.item(r, col)
                if it is not None:
                    it.setText(str(row.get(key) or ""))

    def _save_track_directors(self) -> None:
        existing_by_key = {
            (
                str(row.get("level") or "").strip().upper(),
                str(row.get("track") or "").strip().upper(),
            ): row
            for row in self.repo.list_master_team_members(self.academic_year, role_kind=ROLE_TRACK)
        }
        try:
            for r, (lv, tr) in enumerate(self._track_rows):
                ln = (self.track_table.item(r, 2).text() if self.track_table.item(r, 2) else "").strip()
                fn = (self.track_table.item(r, 3).text() if self.track_table.item(r, 3) else "").strip()
                title = (self.track_table.item(r, 4).text() if self.track_table.item(r, 4) else "").strip()
                email = (self.track_table.item(r, 5).text() if self.track_table.item(r, 5) else "").strip()
                phone = (self.track_table.item(r, 6).text() if self.track_table.item(r, 6) else "").strip()
                if not any((ln, fn, title, email, phone)):
                    row = existing_by_key.get((lv, tr))
                    if row:
                        self.repo.delete_master_team_member(int(row["id"]))
                    continue
                self.repo.upsert_track_director(
                    self.academic_year,
                    lv,
                    tr,
                    last_name=ln,
                    first_name=fn,
                    title=title,
                    email=email,
                    phone=phone,
                )
        except Exception as exc:
            QMessageBox.critical(self, "Parcours", str(exc))
            return
        QMessageBox.information(self, "Parcours", "Responsables de parcours enregistrés.")

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
        for inst in MNE_ENROLLMENT_INSTITUTIONS:
            self.sec_institution.addItem(inst, inst)
        self.sec_last = QLineEdit()
        self.sec_first = QLineEdit()
        self.sec_title = QLineEdit()
        self.sec_email = QLineEdit()
        self.sec_phone = QLineEdit()
        self.sec_notes = QTextEdit()
        self.sec_notes.setMaximumHeight(56)
        form.addRow("Établissement", self.sec_institution)
        form.addRow("Nom", self.sec_last)
        form.addRow("Prénom", self.sec_first)
        form.addRow("Titre / fonction", self.sec_title)
        form.addRow("Email", self.sec_email)
        form.addRow("Téléphone", self.sec_phone)
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
        self.sec_email.setText(str(row_d.get("email") or ""))
        self.sec_phone.setText(str(row_d.get("phone") or ""))
        self.sec_notes.setPlainText(str(row_d.get("notes") or ""))
        self._set_secretariat_tracks(str(row_d.get("tracks_scope") or ""))

    def _clear_secretariat_form(self) -> None:
        self._sec_edit_id = None
        self.sec_institution.setCurrentIndex(0)
        for w in (self.sec_last, self.sec_first, self.sec_title, self.sec_email, self.sec_phone):
            w.clear()
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
            email=self.sec_email.text().strip(),
            phone=self.sec_phone.text().strip(),
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
        self._load_secretariat_list()


def open_master_team_dialog(repo: Repository, academic_year: str, parent=None) -> None:
    dlg = MasterTeamDialog(repo, academic_year, parent=parent)
    dlg.exec()
