from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.parcours import PARCOURS_BY_LEVEL, track_label
from ..gui.jury_deliberation_dialog import JuryDeliberationDialog
from ..gui.jury_roster_pick_dialog import JuryRosterCopyDialog, JuryRosterPickDialog
from ..gui.widgets import make_actions_toolbar
from ..services import terminology as T
from ..services.jury_excel import parse_jury_members_workbook, write_jury_import_template
from ..services.jury_reports import (
    export_jury_pdf_bundle,
    write_grade_matrix_pdf,
    write_institutional_pv_pdf,
    write_pv_jury_pdf,
    write_transcript_pdf,
)


_KIND_TABS: tuple[tuple[str, str], ...] = (
    ("S1", "1ʳᵉ session"),
    ("S2", "2ᵉ session"),
    ("FINAL", "Finale"),
)


def _parcours_roster_combo_label(
    level: str, track: str, roster_name: str, member_count: int
) -> str:
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    tlab = track_label(lv, tr) or tr
    name = str(roster_name or "").strip()
    bits = [f"{lv} {tlab}"]
    if name:
        bits.append(name)
    if member_count:
        bits.append(f"{member_count} membre{'s' if member_count != 1 else ''}")
    return " — ".join(bits)


class _AddMemberDialog(QDialog):
    def __init__(self, parent=None, *, title: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title or T.JURY_MEMBER_ADD)
        form = QFormLayout(self)
        self.last_name = QLineEdit()
        self.first_name = QLineEdit()
        self.member_title = QLineEdit()
        self.institution = QLineEdit()
        form.addRow("Nom", self.last_name)
        form.addRow("Prénom", self.first_name)
        form.addRow("Qualité / titre", self.member_title)
        form.addRow("Institution", self.institution)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class DeliberationsTab(QWidget):
    """Compositions réutilisables du jury + délibérations (réunions) et exports PV."""

    def __init__(self, repo):
        super().__init__()
        self.repo = repo
        self._session_lists: dict[str, QListWidget] = {}
        self._members_loading = False
        self._roster_loading = False

        root = QVBoxLayout(self)
        root.setSpacing(8)

        head = QVBoxLayout()
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Maquette (délibération) :"))
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(280)
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        row1.addWidget(self.template_combo, 1)
        head.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Parcours (exports PDF) :"))
        self._track_checks_host = QWidget()
        self._track_checks_layout = QHBoxLayout(self._track_checks_host)
        self._track_checks_layout.setContentsMargins(0, 0, 0, 0)
        self._track_checkboxes: dict[str, QCheckBox] = {}
        row2.addWidget(self._track_checks_host, 1)
        head.addLayout(row2)
        root.addLayout(head)

        self.main_tabs = QTabWidget()

        # —— Onglet compositions (réutilisables) ——
        comp_tab = QWidget()
        comp_lay = QVBoxLayout(comp_tab)
        comp_hint = QLabel(
            "Une composition par parcours (maquette M1 P, M1 C, M2 NPD…), réutilisable sur plusieurs délibérations. "
            "Renseignez-la ici ; elle n'est créée qu'à la première saisie ou import. "
            "Vous pouvez copier vers un autre parcours ou n'en importer que certains membres."
        )
        comp_hint.setWordWrap(True)
        comp_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        comp_lay.addWidget(comp_hint)

        parcours_row = QHBoxLayout()
        parcours_row.addWidget(QLabel("Parcours :"))
        self.comp_parcours_combo = QComboBox()
        self.comp_parcours_combo.setMinimumWidth(220)
        self.comp_parcours_combo.currentIndexChanged.connect(self._on_comp_parcours_changed)
        parcours_row.addWidget(self.comp_parcours_combo, 1)
        comp_lay.addLayout(parcours_row)

        self.roster_heading = QLabel("Sélectionnez un parcours.")
        self.roster_heading.setStyleSheet("font-weight: bold;")
        comp_lay.addWidget(self.roster_heading)
        self.roster_members_table = QTableWidget()
        self.roster_members_table.setColumnCount(4)
        self.roster_members_table.setHorizontalHeaderLabels(["Nom", "Prénom", "Qualité", "Institution"])
        self.roster_members_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.roster_members_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.roster_members_table.cellChanged.connect(self._on_roster_member_cell_changed)
        comp_lay.addWidget(self.roster_members_table, 1)
        roster_members_tb = make_actions_toolbar(
            self,
            primary=[("Importer composition (Excel)…", self._import_roster_excel)],
            menu_sections=[
                [
                    ("Modèle d'import Excel…", self._export_roster_import_template),
                    ("+ Membre…", self._add_roster_member),
                    ("Copier vers autre parcours…", self._copy_roster_to_track),
                    ("Importer membres depuis autre parcours…", self._import_members_from_roster),
                    ("Vider cette composition", self._clear_comp_roster),
                ],
                [("Retirer la sélection", self._delete_roster_member)],
            ],
        )
        comp_lay.addLayout(roster_members_tb.layout)
        self.roster_add_member_btn = roster_members_tb.menu_actions["+ Membre…"]
        self.roster_del_member_btn = roster_members_tb.menu_actions["Retirer la sélection"]
        self.main_tabs.addTab(comp_tab, "Compositions du jury")

        # —— Onglet délibérations ——
        delib_tab = QWidget()
        delib_lay = QVBoxLayout(delib_tab)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 4, 0)
        left_lay.addWidget(QLabel(T.DELIBERATIONS))
        self.kind_tabs = QTabWidget()
        for code, title in _KIND_TABS:
            tab = QWidget()
            tab_lay = QVBoxLayout(tab)
            tab_lay.setContentsMargins(4, 8, 4, 4)
            lst = QListWidget()
            lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            lst.currentItemChanged.connect(self._on_session_list_changed)
            self._session_lists[code] = lst
            tab_lay.addWidget(lst, 1)
            tab_lay.addLayout(
                make_actions_toolbar(
                    self,
                    primary=[(f"+ {T.DELIBERATION}", lambda k=code: self._new_session(k))],
                    menu_sections=[[("Supprimer", lambda k=code: self._delete_session(k))]],
                ).layout
            )
            self.kind_tabs.addTab(tab, title)
        self.kind_tabs.currentChanged.connect(self._on_kind_tab_changed)
        left_lay.addWidget(self.kind_tabs, 1)
        left.setMinimumWidth(220)
        left.setMaximumWidth(320)
        splitter.addWidget(left)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 0, 0, 0)

        self.session_heading = QLabel(T.DELIBERATION_SELECT)
        self.session_heading.setStyleSheet("font-weight: bold; font-size: 13px;")
        right_lay.addWidget(self.session_heading)

        meta_form = QFormLayout()
        self.session_label_edit = QLineEdit()
        self.session_label_edit.setPlaceholderText(T.DELIBERATION_LABEL_PLACEHOLDER)
        self.session_label_edit.editingFinished.connect(self._save_session_meta)
        self.session_label_edit.setEnabled(False)
        meta_form.addRow("Libellé :", self.session_label_edit)

        self.roster_combo = QComboBox()
        self.roster_combo.setEnabled(False)
        self.roster_combo.currentIndexChanged.connect(self._on_delib_roster_changed)
        meta_form.addRow("Jury :", self.roster_combo)

        self.scope_edit = QLineEdit()
        self.scope_edit.setPlaceholderText("Ex. Bloc 1 — 1ʳᵉ session, puis Blocs 2–3 S1…")
        self.scope_edit.editingFinished.connect(self._save_session_meta)
        self.scope_edit.setEnabled(False)
        meta_form.addRow("Périmètre :", self.scope_edit)
        right_lay.addLayout(meta_form)

        members_box = QGroupBox(T.JURY_MEMBERS)
        mb = QVBoxLayout(members_box)
        self.members_hint = QLabel("")
        self.members_hint.setWordWrap(True)
        self.members_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        mb.addWidget(self.members_hint)
        delib_members_tb = make_actions_toolbar(
            self,
            primary=[("Importer composition (Excel)…", self._import_deliberation_composition)],
            menu_sections=[
                [
                    ("Modèle d'import Excel…", self._export_roster_import_template),
                    ("+ Membre (ad hoc)…", self._add_member_dialog),
                ],
                [("Retirer la sélection", self._delete_member_row)],
            ],
        )
        mb.addLayout(delib_members_tb.layout)
        self.add_member_btn = delib_members_tb.primary_buttons[0]
        self.del_member_btn = delib_members_tb.menu_actions["Retirer la sélection"]

        self.members_table = QTableWidget()
        self.members_table.setColumnCount(4)
        self.members_table.setHorizontalHeaderLabels(["Nom", "Prénom", "Qualité", "Institution"])
        self.members_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.members_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.members_table.setAlternatingRowColors(True)
        self.members_table.cellChanged.connect(self._on_member_cell_changed)
        mb.addWidget(self.members_table, 1)
        right_lay.addWidget(members_box, 2)

        pdf_box = QGroupBox("Documents (PDF)")
        pdf_lay = QVBoxLayout(pdf_box)
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("Notes affichées :"))
        self.pdf_session_combo = QComboBox()
        self.pdf_session_combo.addItem("Session 1", "s1")
        self.pdf_session_combo.addItem("Session 2", "s2")
        view_row.addWidget(self.pdf_session_combo)
        view_row.addStretch()
        pdf_lay.addLayout(view_row)

        tr_row = QHBoxLayout()
        tr_row.addWidget(QLabel("Relevé :"))
        self.student_combo = QComboBox()
        self.student_combo.setMinimumWidth(200)
        tr_row.addWidget(self.student_combo, 1)
        self.tr_session_combo = QComboBox()
        self.tr_session_combo.addItem("S1", "s1")
        self.tr_session_combo.addItem("S2", "s2")
        tr_row.addWidget(self.tr_session_combo)
        tr_row.addStretch()
        pdf_lay.addLayout(tr_row)
        pdf_tb = make_actions_toolbar(
            self,
            primary=[
                ("Délibération interactive…", self._open_deliberation),
                ("Générer PDF parcours cochés…", self._export_bundle),
            ],
            menu_sections=[
                [("Tableau des notes (1 parcours)", self._export_matrix)],
                [(T.PV_BUTTON, self._export_institutional_pv)],
                [("PV brouillon (détail)", self._export_pv)],
                [("Exporter relevé (PDF)", self._export_transcript)],
            ],
        )
        pdf_lay.addLayout(pdf_tb.layout)
        self.btn_pv = pdf_tb.menu_actions[T.PV_BUTTON]
        right_lay.addWidget(pdf_box, 0)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 900])
        delib_lay.addWidget(splitter, 1)

        delib_hint = QLabel(
            "Cochez un ou plusieurs parcours du même niveau pour les exports « Averages COURS » et "
            "« M1P / M1C / M2… ». Utilisez « Délibération interactive… » pour parcourir les étudiants, "
            "saisir les points jury et les envois S2 avec recalcul des moyennes en direct. "
            "Le PV institutionnel reprend membres, points, S2 et décisions (passage M2, redoublement…). "
            "Jury final : propositions redoublement / refus de redoublement (ou passage M2) selon les règles ; "
            "le jury choisit et enregistre la décision."
        )
        delib_hint.setWordWrap(True)
        delib_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        delib_lay.addWidget(delib_hint)
        self.main_tabs.addTab(delib_tab, "Délibérations & PV")

        root.addWidget(self.main_tabs, 1)
        self._set_detail_enabled(False)
        self._set_roster_detail_enabled(False)
        self._refresh_templates()

    def _tid(self) -> int | None:
        d = self.template_combo.currentData()
        return int(d) if d is not None else None

    def _active_kind(self) -> str:
        idx = self.kind_tabs.currentIndex()
        if 0 <= idx < len(_KIND_TABS):
            return _KIND_TABS[idx][0]
        return "S1"

    def _active_session_list(self) -> QListWidget:
        return self._session_lists[self._active_kind()]

    def _current_jury_session_id(self) -> int | None:
        it = self._active_session_list().currentItem()
        if it is None:
            return None
        d = it.data(Qt.ItemDataRole.UserRole)
        return int(d) if d is not None else None

    def _comp_template_id(self) -> int | None:
        d = self.comp_parcours_combo.currentData()
        return int(d) if d is not None else None

    def _current_comp_roster_id(self) -> int | None:
        tid = self._comp_template_id()
        if tid is None:
            return None
        try:
            return self.repo.get_template_roster(tid)
        except Exception:
            return None

    def _ensure_comp_roster_id(self) -> int | None:
        tid = self._comp_template_id()
        if tid is None:
            return None
        try:
            return self.repo.ensure_template_roster(tid)
        except Exception:
            return None

    def _set_roster_detail_enabled(self, on: bool) -> None:
        self.roster_add_member_btn.setEnabled(on)
        self.roster_del_member_btn.setEnabled(on)
        self.roster_members_table.setEnabled(on)
        if not on:
            self.roster_heading.setText("Sélectionnez un parcours.")
            self.roster_members_table.setRowCount(0)

    def _set_detail_enabled(self, on: bool) -> None:
        self.session_label_edit.setEnabled(on)
        self.roster_combo.setEnabled(on)
        self.scope_edit.setEnabled(on)
        self.add_member_btn.setEnabled(on)
        self.del_member_btn.setEnabled(on)
        self.members_table.setEnabled(on)
        self.btn_pv.setEnabled(on)
        if not on:
            self.session_heading.setText(T.DELIBERATION_SELECT)
            self.session_label_edit.clear()
            self.scope_edit.clear()
            self.roster_combo.blockSignals(True)
            self.roster_combo.clear()
            self.roster_combo.addItem("—", None)
            self.roster_combo.blockSignals(False)

    def refresh_all(self) -> None:
        self._refresh_templates()

    def _refresh_templates(self) -> None:
        self.template_combo.blockSignals(True)
        try:
            prev = self.template_combo.currentData()
            self.template_combo.clear()
            for t in self.repo.list_templates():
                lv, tr = (t.get("level") or "").strip(), (t.get("track") or "").strip()
                suffix = f" — {lv} {tr}" if lv or tr else ""
                self.template_combo.addItem(f"{t['name']} [{t['academic_year']}]{suffix}", int(t["id"]))
            if prev is not None:
                i = self.template_combo.findData(prev)
                if i >= 0:
                    self.template_combo.setCurrentIndex(i)
            elif self.template_combo.count():
                self.template_combo.setCurrentIndex(0)
        finally:
            self.template_combo.blockSignals(False)
        self._on_template_changed()

    def _on_template_changed(self) -> None:
        self._rebuild_track_checkboxes()
        self._rebuild_comp_parcours_combo()
        self._refresh_all_session_lists()
        self._refresh_students_combo()
        self._populate_delib_roster_combo()

    def _current_template_meta(self) -> dict | None:
        tid = self._tid()
        if tid is None:
            return None
        return next((t for t in self.repo.list_templates() if int(t["id"]) == tid), None)

    def _rebuild_track_checkboxes(self) -> None:
        while self._track_checks_layout.count():
            item = self._track_checks_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._track_checkboxes.clear()
        tpl = self._current_template_meta()
        if tpl is None:
            return
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        cur_tr = str(tpl.get("track") or "").strip().upper()
        siblings = self.repo.list_templates_for_year_level(ay, lv)
        for t in siblings:
            tr = str(t.get("track") or "").strip().upper()
            if not tr:
                continue
            lab = tr
            for code, name in PARCOURS_BY_LEVEL.get(lv, ()):
                if code == tr:
                    lab = f"{code} ({name})"
                    break
            cb = QCheckBox(lab)
            cb.setProperty("template_id", int(t["id"]))
            cb.setChecked(tr == cur_tr)
            self._track_checkboxes[tr] = cb
            self._track_checks_layout.addWidget(cb)
        self._track_checks_layout.addStretch()

    def _checked_template_ids(self) -> list[int]:
        ids: list[int] = []
        for cb in self._track_checkboxes.values():
            if cb.isChecked():
                tid = cb.property("template_id")
                if tid is not None:
                    ids.append(int(tid))
        if not ids:
            tid = self._tid()
            if tid is not None:
                ids.append(int(tid))
        return ids

    def _rebuild_comp_parcours_combo(self) -> None:
        tpl = self._current_template_meta()
        prev_tid = self._comp_template_id()
        self.comp_parcours_combo.blockSignals(True)
        self.comp_parcours_combo.clear()
        if tpl is None:
            self.comp_parcours_combo.blockSignals(False)
            self._on_comp_parcours_changed()
            return
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        cur_tid = int(tpl["id"])
        for t in self.repo.list_templates_for_year_level(ay, lv):
            tid = int(t["id"])
            tr = str(t.get("track") or "").strip().upper()
            tlab = track_label(lv, tr) or tr
            rid = self.repo.get_template_roster(tid)
            member_n = 0
            rname = ""
            if rid is not None:
                roster = self.repo.get_jury_roster(rid) or {}
                rname = str(roster.get("name") or "")
                member_n = len(self.repo.list_jury_roster_members(rid))
            if member_n:
                lbl = _parcours_roster_combo_label(lv, tr, rname, member_n)
            else:
                lbl = f"{lv} {tlab} — non renseignée"
            self.comp_parcours_combo.addItem(lbl, tid)
        if prev_tid is not None:
            idx = self.comp_parcours_combo.findData(prev_tid)
            if idx >= 0:
                self.comp_parcours_combo.setCurrentIndex(idx)
        else:
            idx = self.comp_parcours_combo.findData(cur_tid)
            if idx >= 0:
                self.comp_parcours_combo.setCurrentIndex(idx)
        self.comp_parcours_combo.blockSignals(False)
        self._on_comp_parcours_changed()

    def _on_comp_parcours_changed(self) -> None:
        tid = self._comp_template_id()
        if tid is None:
            self._set_roster_detail_enabled(False)
            self.roster_heading.setText("Sélectionnez un parcours.")
            return
        tpl = self.repo.get_template(tid) or {}
        lv = str(tpl.get("level") or "").strip().upper()
        tr = str(tpl.get("track") or "").strip().upper()
        ay = str(tpl.get("academic_year") or "").strip()
        tlab = track_label(lv, tr) or tr
        rid = self.repo.get_template_roster(tid)
        if rid is None:
            self.roster_heading.setText(
                f"Jury du parcours {lv} {tlab} · {ay} · non renseignée — ajoutez ou importez des membres"
            )
            self._set_roster_detail_enabled(True)
            self.roster_members_table.setRowCount(0)
            return
        roster = self.repo.get_jury_roster(rid) or {}
        name = str(roster.get("name") or self.repo.default_roster_name_for_template(tid))
        n = len(self.repo.list_jury_roster_members(rid))
        self.roster_heading.setText(
            f"Jury du parcours {lv} {tlab} · {ay} · {name} · {n} membre{'s' if n != 1 else ''}"
        )
        self._set_roster_detail_enabled(True)
        self._load_roster_members_table()

    def _load_roster_members_table(self) -> None:
        rid = self._current_comp_roster_id()
        self._roster_loading = True
        self.roster_members_table.blockSignals(True)
        try:
            self.roster_members_table.setRowCount(0)
            if rid is None:
                return
            rows = self.repo.list_jury_roster_members(rid)
            self.roster_members_table.setRowCount(len(rows))
            for i, m in enumerate(rows):
                mid = int(m["id"])
                for j, key in enumerate(("last_name", "first_name", "title", "institution")):
                    it = QTableWidgetItem(str(m.get(key) or ""))
                    it.setData(Qt.ItemDataRole.UserRole, mid)
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
                    self.roster_members_table.setItem(i, j, it)
        finally:
            self.roster_members_table.blockSignals(False)
            self._roster_loading = False

    def _clear_comp_roster(self) -> None:
        rid = self._current_comp_roster_id()
        if rid is None:
            return
        if (
            QMessageBox.question(
                self,
                "Vider la composition",
                "Retirer tous les membres de la composition de ce parcours ?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.repo.replace_jury_roster_members(rid, [])
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._rebuild_comp_parcours_combo()
        self._populate_delib_roster_combo()

    def _roster_catalog_filters(self) -> tuple[str, str]:
        tpl = self._current_template_meta() or {}
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        return ay, lv

    def _pick_source_roster(self, *, exclude_current: bool) -> int | None:
        ay, lv = self._roster_catalog_filters()
        ex = self._current_comp_roster_id() if exclude_current else None
        catalog = self.repo.list_jury_rosters_catalog(
            academic_year=ay,
            level=lv,
            exclude_roster_id=ex,
        )
        if not catalog:
            QMessageBox.information(
                self,
                "Compositions",
                "Aucune autre composition trouvée pour ce millésime et ce niveau.\n"
                "Créez-en une sur un autre parcours (changez de maquette en haut).",
            )
            return None
        dlg = JuryRosterPickDialog(catalog, title="Choisir une composition source", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.selected_roster_id is None:
            return None
        return int(dlg.selected_roster_id)

    def _copy_roster_to_track(self) -> None:
        src_id = self._current_comp_roster_id()
        if src_id is None:
            QMessageBox.information(
                self, "Copier", "Sélectionnez le parcours source à copier vers un autre parcours."
            )
            return
        src = self.repo.get_jury_roster(src_id) or {}
        tpl = self._current_template_meta() or {}
        ay = str(tpl.get("academic_year") or "").strip()
        lv = str(tpl.get("level") or "").strip().upper()
        cur_tid = int(tpl["id"]) if tpl.get("id") else self._tid()
        targets = [
            t
            for t in self.repo.list_templates_for_year_level(ay, lv)
            if cur_tid is None or int(t["id"]) != int(cur_tid)
        ]
        if not targets:
            QMessageBox.information(
                self,
                "Copier",
                "Aucun autre parcours disponible pour ce millésime et ce niveau.",
            )
            return
        dlg = JuryRosterCopyDialog(targets, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.target_template_id is None:
            return
        try:
            new_id = self.repo.copy_jury_roster_to_template(
                int(src_id),
                int(dlg.target_template_id),
                new_name=dlg.roster_name,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        tgt = next((t for t in targets if int(t["id"]) == int(dlg.target_template_id)), {})
        tr = str(tgt.get("track") or "")
        QMessageBox.information(
            self,
            "Copie effectuée",
            f"Composition copiée vers le parcours {lv} {tr}.",
        )
        self._rebuild_comp_parcours_combo()
        idx = self.comp_parcours_combo.findData(int(dlg.target_template_id))
        if idx >= 0:
            self.comp_parcours_combo.setCurrentIndex(idx)
        self._populate_delib_roster_combo()

    def _import_members_from_roster(self) -> None:
        tgt_id = self._ensure_comp_roster_id()
        if tgt_id is None:
            QMessageBox.information(
                self,
                "Importer",
                "Sélectionnez le parcours de destination.",
            )
            return
        src_id = self._pick_source_roster(exclude_current=True)
        if src_id is None:
            return
        try:
            n = self.repo.append_roster_members_from(int(src_id), int(tgt_id))
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._load_roster_members_table()
        QMessageBox.information(
            self,
            "Import",
            f"{n} membre(s) ajouté(s) (doublons nom/prénom ignorés).",
        )

    def _export_roster_import_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Modèle composition jury",
            str(Path.home() / "modele_composition_jury.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            write_jury_import_template(path)
            QMessageBox.information(
                self,
                "Modèle créé",
                f"Fichier enregistré :\n{path}\n\n"
                "Renseignez une ligne par membre (nom, prénom, qualité, institution) puis importez.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))

    def _pick_jury_excel_file(self) -> str | None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer une composition du jury",
            "",
            "Excel (*.xlsx)",
        )
        return path or None

    def _import_roster_excel(self) -> None:
        rid = self._ensure_comp_roster_id()
        if rid is None:
            QMessageBox.information(
                self,
                "Import",
                "Sélectionnez un parcours pour importer sa composition.",
            )
            return
        path = self._pick_jury_excel_file()
        if not path:
            return
        try:
            members, errors = parse_jury_members_workbook(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import", f"Lecture du fichier impossible.\n\n{exc}")
            return
        if errors and not members:
            QMessageBox.warning(self, "Import", "\n".join(errors[:20]))
            return
        existing = self.repo.list_jury_roster_members(rid)
        mode = "replace"
        if existing:
            ans = QMessageBox.question(
                self,
                "Composition existante",
                f"Cette composition contient déjà {len(existing)} membre(s).\n\n"
                "Remplacer toute la liste par l'import ?\n"
                "Non = ajouter les lignes du fichier à la suite.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            mode = "replace" if ans == QMessageBox.StandardButton.Yes else "append"
        try:
            if mode == "replace":
                n = self.repo.replace_jury_roster_members(rid, members)
            else:
                n = 0
                for m in members:
                    self.repo.add_jury_roster_member(rid, **m)
                    n += 1
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._load_roster_members_table()
        self._rebuild_comp_parcours_combo()
        msg = f"{n} membre(s) importé(s)."
        if errors:
            msg += "\n\nAvertissements :\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Import terminé", msg)

    def _import_deliberation_composition(self) -> None:
        jsid = self._current_jury_session_id()
        tid = self._tid()
        if jsid is None or tid is None:
            QMessageBox.information(self, "Import", "Sélectionnez une délibération.")
            return
        path = self._pick_jury_excel_file()
        if not path:
            return
        try:
            members, errors = parse_jury_members_workbook(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import", f"Lecture du fichier impossible.\n\n{exc}")
            return
        if not members:
            QMessageBox.warning(
                self,
                "Import",
                "\n".join(errors) if errors else "Aucun membre valide dans le fichier.",
            )
            return
        ans = QMessageBox.question(
            self,
            "Import jury",
            f"{len(members)} membre(s) trouvé(s).\n\n"
            "Enregistrer dans la composition de ce parcours et lier la délibération ?\n"
            "(Non = membres ad hoc, propres à cette délibération uniquement)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if ans == QMessageBox.StandardButton.Cancel:
            return
        try:
            if ans == QMessageBox.StandardButton.Yes:
                roster_id = self.repo.ensure_template_roster(tid)
                self.repo.replace_jury_roster_members(roster_id, members)
                self.repo.update_jury_session(jsid, roster_id=roster_id)
            else:
                self.repo.update_jury_session(jsid, clear_roster=True)
                self.repo.replace_jury_session_members(jsid, members)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._rebuild_comp_parcours_combo()
        self._populate_delib_roster_combo()
        self.roster_combo.blockSignals(True)
        if ans == QMessageBox.StandardButton.Yes:
            idx = self.roster_combo.findData(roster_id)
            tpl = self.repo.get_template(tid) or {}
            lv = str(tpl.get("level") or "").strip().upper()
            tr = str(tpl.get("track") or "").strip().upper()
            msg = (
                f"{len(members)} membre(s) enregistré(s) dans la composition du parcours "
                f"{lv} {track_label(lv, tr)} et liée à cette délibération."
            )
        else:
            idx = self.roster_combo.findData(None)
            msg = f"{len(members)} membre(s) ad hoc importé(s) pour cette délibération."
        if idx >= 0:
            self.roster_combo.setCurrentIndex(idx)
        self.roster_combo.blockSignals(False)
        self._load_members_table()
        if errors:
            msg += "\n\nAvertissements :\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Import terminé", msg)

    def _add_roster_member(self) -> None:
        rid = self._ensure_comp_roster_id()
        if rid is None:
            return
        dlg = _AddMemberDialog(self, title="Ajouter un membre à la composition")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.last_name.text().strip() and not dlg.first_name.text().strip():
            QMessageBox.warning(self, T.JURY_MEMBERS, "Indiquez au moins un nom ou un prénom.")
            return
        try:
            self.repo.add_jury_roster_member(
                rid,
                last_name=dlg.last_name.text(),
                first_name=dlg.first_name.text(),
                title=dlg.member_title.text(),
                institution=dlg.institution.text(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._load_roster_members_table()

    def _selected_roster_member_id(self) -> int | None:
        r = self.roster_members_table.currentRow()
        if r < 0:
            return None
        it = self.roster_members_table.item(r, 0)
        if it is None:
            return None
        d = it.data(Qt.ItemDataRole.UserRole)
        return int(d) if d is not None else None

    def _on_roster_member_cell_changed(self, row: int, col: int) -> None:
        if self._roster_loading:
            return
        it = self.roster_members_table.item(row, 0)
        if it is None:
            return
        mid = it.data(Qt.ItemDataRole.UserRole)
        if mid is None:
            return
        try:
            self.repo.update_jury_roster_member(
                int(mid),
                last_name=self.roster_members_table.item(row, 0).text() if self.roster_members_table.item(row, 0) else "",
                first_name=self.roster_members_table.item(row, 1).text() if self.roster_members_table.item(row, 1) else "",
                title=self.roster_members_table.item(row, 2).text() if self.roster_members_table.item(row, 2) else "",
                institution=self.roster_members_table.item(row, 3).text() if self.roster_members_table.item(row, 3) else "",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Enregistrement", str(exc))
            self._load_roster_members_table()

    def _delete_roster_member(self) -> None:
        mid = self._selected_roster_member_id()
        if mid is None:
            QMessageBox.information(self, T.JURY_MEMBERS, T.MSG_SELECT_MEMBER)
            return
        try:
            self.repo.delete_jury_roster_member(mid)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._load_roster_members_table()

    def _populate_delib_roster_combo(self) -> None:
        tid = self._tid()
        jsid = self._current_jury_session_id()
        sess = self.repo.get_jury_session(jsid) if jsid else None
        linked = int(sess["roster_id"]) if sess and sess.get("roster_id") else None
        tpl = self._current_template_meta() or {}
        ay = str(tpl.get("academic_year") or "").strip()
        self.roster_combo.blockSignals(True)
        self.roster_combo.clear()
        self.roster_combo.addItem("— Membres ad hoc (cette délibération) —", None)
        seen: set[int] = set()
        catalog = self.repo.list_jury_rosters_catalog(
            academic_year=ay, only_with_members=True
        )
        local_rid = self.repo.get_template_roster(tid) if tid is not None else None
        for rec in catalog:
            rid = int(rec["id"])
            if rid in seen:
                continue
            lv = str(rec.get("level") or "").strip().upper()
            tr = str(rec.get("track") or "").strip().upper()
            n = int(rec.get("member_count") or 0)
            rname = str(rec.get("name") or "")
            label = _parcours_roster_combo_label(lv, tr, rname, n)
            if local_rid is not None and rid == local_rid:
                label = f"{label} · cette maquette"
            self.roster_combo.addItem(label, rid)
            seen.add(rid)
        if linked is not None:
            idx = self.roster_combo.findData(linked)
            if idx >= 0:
                self.roster_combo.setCurrentIndex(idx)
            else:
                roster = self.repo.get_jury_roster(linked) or {}
                tpl_src = self.repo.get_template(int(roster.get("template_id") or 0)) or {}
                lv = str(tpl_src.get("level") or "").strip().upper()
                tr = str(tpl_src.get("track") or "").strip().upper()
                n = len(self.repo.list_jury_roster_members(linked))
                label = _parcours_roster_combo_label(
                    lv, tr, str(roster.get("name") or ""), n
                )
                self.roster_combo.addItem(label, linked)
                self.roster_combo.setCurrentIndex(self.roster_combo.count() - 1)
        self.roster_combo.blockSignals(False)

    def _on_kind_tab_changed(self, _idx: int) -> None:
        self._on_session_list_changed()

    def _refresh_all_session_lists(self) -> None:
        tid = self._tid()
        prev_sid = self._current_jury_session_id()
        all_sessions = self.repo.list_jury_sessions(tid) if tid is not None else []
        for code, lst in self._session_lists.items():
            lst.blockSignals(True)
            lst.clear()
            for s in all_sessions:
                if str(s.get("session_kind")) != code:
                    continue
                lab = (s.get("label") or "").strip() or f"{T.DELIBERATION} #{s['id']}"
                scope = str(s.get("scope_text") or "").strip()
                if scope:
                    lab = f"{lab} — {scope}"
                it = QListWidgetItem(lab)
                it.setData(Qt.ItemDataRole.UserRole, int(s["id"]))
                lst.addItem(it)
            lst.blockSignals(False)

        if prev_sid is not None:
            for lst in self._session_lists.values():
                for i in range(lst.count()):
                    if lst.item(i).data(Qt.ItemDataRole.UserRole) == prev_sid:
                        lst.setCurrentRow(i)
                        self._on_session_list_changed(lst.item(i), None)
                        return
        cur = self._active_session_list()
        if cur.count():
            cur.setCurrentRow(0)
        else:
            for idx, (code, lst) in enumerate(self._session_lists.items()):
                if lst.count():
                    self.kind_tabs.setCurrentIndex(idx)
                    lst.setCurrentRow(0)
                    return
            self._on_session_list_changed(None, None)

    def _refresh_students_combo(self) -> None:
        self.student_combo.clear()
        tid = self._tid()
        if tid is None:
            return
        from ..services.lookups import student_combo_label

        for s in self.repo.list_students_for_template(tid):
            self.student_combo.addItem(student_combo_label(s), int(s["id"]))

    def _on_session_list_changed(
        self, current: QListWidgetItem | None = None, _previous: QListWidgetItem | None = None
    ) -> None:
        if current is None:
            current = self._active_session_list().currentItem()
        jsid = self._current_jury_session_id()
        if jsid is None:
            self.members_table.setRowCount(0)
            self._set_detail_enabled(False)
            return
        sess = self.repo.get_jury_session(jsid) or {}
        kind_lab = next((t for c, t in _KIND_TABS if c == str(sess.get("session_kind"))), "")
        self.session_heading.setText(f"{kind_lab} — {T.DELIBERATION.lower()} #{jsid}")
        self.session_label_edit.blockSignals(True)
        self.session_label_edit.setText(str(sess.get("label") or ""))
        self.session_label_edit.blockSignals(False)
        self.scope_edit.blockSignals(True)
        self.scope_edit.setText(str(sess.get("scope_text") or ""))
        self.scope_edit.blockSignals(False)
        self._set_detail_enabled(True)
        self._populate_delib_roster_combo()
        self._load_members_table()

    def _save_session_meta(self) -> None:
        jsid = self._current_jury_session_id()
        if jsid is None:
            return
        label = self.session_label_edit.text().strip()
        scope = self.scope_edit.text().strip()
        try:
            self.repo.update_jury_session(jsid, label=label, scope_text=scope)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._refresh_all_session_lists()

    def _on_delib_roster_changed(self) -> None:
        jsid = self._current_jury_session_id()
        if jsid is None:
            return
        rid = self.roster_combo.currentData()
        try:
            if rid is None:
                self.repo.update_jury_session(jsid, clear_roster=True)
            else:
                self.repo.update_jury_session(jsid, roster_id=int(rid))
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._load_members_table()

    def _delib_uses_roster(self) -> bool:
        jsid = self._current_jury_session_id()
        if jsid is None:
            return False
        sess = self.repo.get_jury_session(jsid) or {}
        return bool(sess.get("roster_id"))

    def _load_members_table(self) -> None:
        jsid = self._current_jury_session_id()
        uses_roster = self._delib_uses_roster()
        self.add_member_btn.setEnabled(jsid is not None and not uses_roster)
        self.del_member_btn.setEnabled(jsid is not None and not uses_roster)
        if uses_roster:
            roster = self.repo.get_jury_roster(
                int((self.repo.get_jury_session(jsid) or {})["roster_id"])
            )
            rname = str((roster or {}).get("name") or "")
            tpl_src = self.repo.get_template(int((roster or {}).get("template_id") or 0)) or {}
            slv = str(tpl_src.get("level") or "").strip().upper()
            strk = str(tpl_src.get("track") or "").strip().upper()
            src = f"{slv} {track_label(slv, strk)}".strip()
            self.members_hint.setText(
                f"Composition « {rname} » ({src}) — modifiable dans l'onglet Compositions."
            )
        else:
            self.members_hint.setText(
                "Composition ad hoc : membres propres à cette délibération uniquement."
            )

        self._members_loading = True
        self.members_table.blockSignals(True)
        try:
            self.members_table.setRowCount(0)
            if jsid is None:
                return
            rows = self.repo.list_jury_members_for_deliberation(jsid)
            self.members_table.setRowCount(len(rows))
            for i, m in enumerate(rows):
                mid = m.get("id")
                editable = mid is not None and not uses_roster
                for j, key in enumerate(("last_name", "first_name", "title", "institution")):
                    it = QTableWidgetItem(str(m.get(key) or ""))
                    if mid is not None:
                        it.setData(Qt.ItemDataRole.UserRole, int(mid))
                    if editable:
                        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
                    else:
                        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.members_table.setItem(i, j, it)
        finally:
            self.members_table.blockSignals(False)
            self._members_loading = False

    def _new_session(self, kind: str) -> None:
        tid = self._tid()
        if tid is None:
            return
        label, ok = QInputDialog.getText(self, T.DELIBERATION_NEW, T.DELIBERATION_LABEL_PROMPT)
        if not ok:
            return
        scope, ok2 = QInputDialog.getText(
            self,
            T.DELIBERATION_NEW,
            "Périmètre de la délibération (optionnel, ex. Bloc 1 — S1) :",
        )
        if not ok2:
            return
        roster_id: int | None = None
        local_rid = self.repo.get_template_roster(tid)
        if local_rid is not None and self.repo.list_jury_roster_members(local_rid):
            roster_id = local_rid
        try:
            new_id = self.repo.add_jury_session(
                tid,
                kind,
                label=str(label),
                scope_text=str(scope),
                roster_id=roster_id,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        tab_idx = next((i for i, (c, _) in enumerate(_KIND_TABS) if c == kind), 0)
        self.kind_tabs.setCurrentIndex(tab_idx)
        self.main_tabs.setCurrentIndex(1)
        self._refresh_all_session_lists()
        lst = self._session_lists[kind]
        for i in range(lst.count()):
            if lst.item(i).data(Qt.ItemDataRole.UserRole) == new_id:
                lst.setCurrentRow(i)
                break

    def _delete_session(self, kind: str) -> None:
        lst = self._session_lists[kind]
        jsid = None
        it = lst.currentItem()
        if it is not None:
            jsid = int(it.data(Qt.ItemDataRole.UserRole))
        if jsid is None:
            QMessageBox.information(self, T.DELIBERATIONS, "Sélectionnez une délibération à supprimer.")
            return
        if (
            QMessageBox.question(self, "Confirmer", T.MSG_DELETE_DELIBERATION)
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.repo.delete_jury_session(jsid)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._refresh_all_session_lists()

    def _add_member_dialog(self) -> None:
        if self._delib_uses_roster():
            QMessageBox.information(
                self,
                T.JURY_MEMBERS,
                "Cette délibération utilise une composition : modifiez les membres dans l'onglet Compositions.",
            )
            return
        jsid = self._current_jury_session_id()
        if jsid is None:
            return
        dlg = _AddMemberDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.last_name.text().strip() and not dlg.first_name.text().strip():
            QMessageBox.warning(self, T.JURY_MEMBERS, "Indiquez au moins un nom ou un prénom.")
            return
        try:
            self.repo.add_jury_member(
                jsid,
                last_name=dlg.last_name.text(),
                first_name=dlg.first_name.text(),
                title=dlg.member_title.text(),
                institution=dlg.institution.text(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._load_members_table()

    def _selected_member_id(self) -> int | None:
        r = self.members_table.currentRow()
        if r < 0:
            return None
        it = self.members_table.item(r, 0)
        if it is None:
            return None
        d = it.data(Qt.ItemDataRole.UserRole)
        return int(d) if d is not None else None

    def _on_member_cell_changed(self, row: int, col: int) -> None:
        if self._members_loading or self._delib_uses_roster():
            return
        it = self.members_table.item(row, 0)
        if it is None:
            return
        mid = it.data(Qt.ItemDataRole.UserRole)
        if mid is None:
            return
        try:
            self.repo.update_jury_member(
                int(mid),
                last_name=self.members_table.item(row, 0).text() if self.members_table.item(row, 0) else "",
                first_name=self.members_table.item(row, 1).text() if self.members_table.item(row, 1) else "",
                title=self.members_table.item(row, 2).text() if self.members_table.item(row, 2) else "",
                institution=self.members_table.item(row, 3).text() if self.members_table.item(row, 3) else "",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Enregistrement", str(exc))
            self._load_members_table()

    def _delete_member_row(self) -> None:
        if self._delib_uses_roster():
            return
        mid = self._selected_member_id()
        if mid is None:
            QMessageBox.information(self, T.JURY_MEMBERS, T.MSG_SELECT_MEMBER)
            return
        try:
            self.repo.delete_jury_member(mid)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        self._load_members_table()

    def _check_reportlab(self) -> bool:
        try:
            import reportlab  # noqa: F401
        except ImportError:
            QMessageBox.warning(
                self,
                "Dépendance",
                "Le module « reportlab » est requis pour les PDF.\n"
                "Installez-le : pip install reportlab",
            )
            return False
        return True

    def _open_deliberation(self) -> None:
        tid = self._tid()
        if tid is None:
            QMessageBox.warning(self, T.DELIBERATIONS, "Sélectionnez une maquette.")
            return
        vs = str(self.pdf_session_combo.currentData() or "s1")
        jsid = self._current_jury_session_id()
        kind = self._active_kind()
        dlg = JuryDeliberationDialog(
            self.repo,
            int(tid),
            view_session=vs,
            jury_session_id=jsid,
            session_kind=kind,
            parent=self,
        )
        dlg.exec()

    def _export_bundle(self) -> None:
        if not self._check_reportlab():
            return
        tids = self._checked_template_ids()
        if not tids:
            QMessageBox.warning(self, "PDF", "Cochez au moins un parcours.")
            return
        dest = QFileDialog.getExistingDirectory(
            self, "Dossier de sortie des PDF jury", str(Path.home())
        )
        if not dest:
            return
        vs = str(self.pdf_session_combo.currentData() or "s1")
        session_title = "First Session" if vs == "s1" else "Second Session"
        try:
            created = export_jury_pdf_bundle(
                self.repo,
                template_ids=tids,
                view_session=vs,
                dest_dir=dest,
                session_title=session_title,
            )
        except Exception as exc:
            QMessageBox.critical(self, "PDF", str(exc))
            return
        QMessageBox.information(
            self,
            "PDF",
            f"{len(created)} fichier(s) créé(s) dans :\n{dest}",
        )

    def _export_institutional_pv(self) -> None:
        if not self._check_reportlab():
            return
        tid = self._tid()
        jsid = self._current_jury_session_id()
        if tid is None or jsid is None:
            QMessageBox.warning(self, T.DELIBERATIONS, T.MSG_SELECT_DELIBERATION)
            return
        tpl = self._current_template_meta() or {}
        lv = str(tpl.get("level") or "M")
        tr = str(tpl.get("track") or "")
        ay = str(tpl.get("academic_year") or "").replace("-", "")
        default = str(Path.home() / f"{tr} jury - {ay}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, T.PV_BUTTON, default, "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        vs = str(self.pdf_session_combo.currentData() or "s1")
        try:
            write_institutional_pv_pdf(
                self.repo,
                template_id=int(tid),
                jury_session_id=int(jsid),
                view_session=vs,
                path=path,
            )
        except Exception as exc:
            QMessageBox.critical(self, "PDF", str(exc))
            return
        QMessageBox.information(self, "PDF", f"Fichier créé :\n{path}")

    def _export_matrix(self) -> None:
        if not self._check_reportlab():
            return
        tid = self._tid()
        if tid is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Tableau des notes", str(Path.home() / "tableau_notes.pdf"), "PDF (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        vs = str(self.pdf_session_combo.currentData() or "s1")
        try:
            write_grade_matrix_pdf(self.repo, template_id=tid, view_session=vs, path=path)
        except Exception as exc:
            QMessageBox.critical(self, "PDF", str(exc))
            return
        QMessageBox.information(self, "PDF", f"Fichier créé :\n{path}")

    def _export_pv(self) -> None:
        if not self._check_reportlab():
            return
        tid = self._tid()
        jsid = self._current_jury_session_id()
        if tid is None or jsid is None:
            QMessageBox.warning(self, T.DELIBERATIONS, T.MSG_SELECT_DELIBERATION)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Procès-verbal", str(Path.home() / "PV_jury.pdf"), "PDF (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        vs = str(self.pdf_session_combo.currentData() or "s1")
        try:
            write_pv_jury_pdf(self.repo, template_id=tid, jury_session_id=jsid, view_session=vs, path=path)
        except Exception as exc:
            QMessageBox.critical(self, "PDF", str(exc))
            return
        QMessageBox.information(self, "PDF", f"Fichier créé :\n{path}")

    def _export_transcript(self) -> None:
        if not self._check_reportlab():
            return
        tid = self._tid()
        sid = self.student_combo.currentData()
        if tid is None or sid is None:
            QMessageBox.warning(self, T.DELIBERATIONS, "Choisissez une maquette et un étudiant.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Relevé de notes", str(Path.home() / "releve.pdf"), "PDF (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        vs = str(self.tr_session_combo.currentData() or "s1")
        try:
            write_transcript_pdf(self.repo, template_id=tid, student_id=int(sid), view_session=vs, path=path)
        except Exception as exc:
            QMessageBox.critical(self, "PDF", str(exc))
            return
        QMessageBox.information(self, "PDF", f"Fichier créé :\n{path}")


JuryTab = DeliberationsTab
