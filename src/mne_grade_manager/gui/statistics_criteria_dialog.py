"""Sélection des critères pour les statistiques (millésimes, population, indicateurs)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from ..services.statistics import ALL_STATISTICS_GENDER_CODES, STATISTICS_GENDER_CHOICES, StatisticsCriteria


class StatisticsCriteriaDialog(QDialog):
    def __init__(
        self,
        repo,
        criteria: StatisticsCriteria,
        *,
        default_academic_year: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self._default_academic_year = (default_academic_year or "").strip()
        self.setWindowTitle("Critères des statistiques")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)

        intro = QLabel(
            "Choisissez un ou plusieurs millésimes et les indicateurs à calculer. "
            "Les effectifs et répartitions sont agrégés sur la population sélectionnée ; "
            "la réussite et le suivi stages se font maquette par maquette."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        years_box = QGroupBox("Millésimes")
        years_lay = QVBoxLayout(years_box)
        years_btns = QHBoxLayout()
        select_all_years = QCheckBox("Tout sélectionner")
        years_btns.addWidget(select_all_years)
        years_btns.addStretch()
        years_lay.addLayout(years_btns)
        self.year_list = QListWidget()
        self.year_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        years_lay.addWidget(self.year_list)
        root.addWidget(years_box)

        pop_box = QGroupBox("Population")
        pop_form = QFormLayout(pop_box)
        self.level_combo = QComboBox()
        self.level_combo.addItem("Tous", "")
        self.level_combo.addItem("M1", "M1")
        self.level_combo.addItem("M2", "M2")
        self.track_combo = QComboBox()
        self.track_combo.addItem("Tous", "")
        pop_form.addRow("Niveau", self.level_combo)
        pop_form.addRow("Parcours", self.track_combo)
        gender_row = QHBoxLayout()
        self._gender_checks: dict[str, QCheckBox] = {}
        for code, label in STATISTICS_GENDER_CHOICES:
            chk = QCheckBox(label)
            chk.setChecked(True)
            self._gender_checks[code] = chk
            gender_row.addWidget(chk)
        gender_row.addStretch()
        pop_form.addRow("Genre (inclusivité)", gender_row)
        root.addWidget(pop_box)

        ind_box = QGroupBox("Indicateurs — effectifs et diversité")
        ind_lay = QVBoxLayout(ind_box)
        self.chk_by_year = QCheckBox("Répartition par millésime")
        self.chk_by_level = QCheckBox("Répartition par niveau")
        self.chk_by_track = QCheckBox("Répartition par parcours")
        self.chk_by_gender = QCheckBox("Répartition par genre (inclusivité)")
        self.chk_nationality = QCheckBox("Nationalités")
        self.chk_origin_country = QCheckBox("Pays — établissement d'origine")
        self.chk_origin_inst = QCheckBox("Établissements d'origine")
        self.chk_enrollment = QCheckBox("Établissements d'inscription")
        for w in (
            self.chk_by_year,
            self.chk_by_level,
            self.chk_by_track,
            self.chk_by_gender,
            self.chk_nationality,
            self.chk_origin_country,
            self.chk_origin_inst,
            self.chk_enrollment,
        ):
            ind_lay.addWidget(w)
        root.addWidget(ind_box)

        succ_box = QGroupBox("Réussite et stages (maquette)")
        succ_lay = QVBoxLayout(succ_box)
        self.chk_success = QCheckBox("Taux de réussite (moyenne année, blocs)")
        self.chk_internship = QCheckBox("Suivi des stages")
        succ_lay.addWidget(self.chk_success)
        succ_lay.addWidget(self.chk_internship)
        session_row = QHBoxLayout()
        session_row.addWidget(QLabel("Session :"))
        self.session_combo = QComboBox()
        self.session_combo.addItem("Session 1", "s1")
        self.session_combo.addItem("Session 2", "s2")
        session_row.addWidget(self.session_combo)
        session_row.addStretch()
        succ_lay.addLayout(session_row)
        self.template_list = QListWidget()
        self.template_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        succ_lay.addWidget(QLabel("Maquette(s) :"))
        succ_lay.addWidget(self.template_list)
        root.addWidget(succ_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._syncing_years = False
        self._select_all_years_cb = select_all_years
        select_all_years.toggled.connect(self._on_select_all_years_toggled)
        self.year_list.itemChanged.connect(self._on_year_item_changed)

        self._populate_years(criteria.academic_years)
        self._populate_tracks()
        self._apply_criteria(criteria)
        self._refresh_template_list()

    def _populate_years(self, preselected: list[str]) -> None:
        years = sorted(
            {
                str(s.get("academic_year") or "").strip()
                for s in self.repo.list_students(include_withdrawn=False)
                if str(s.get("academic_year") or "").strip()
            },
            reverse=True,
        )
        if self._default_academic_year and self._default_academic_year not in years:
            years.insert(0, self._default_academic_year)
        pre = {y.strip() for y in preselected if y.strip()}
        if not pre and self._default_academic_year:
            pre = {self._default_academic_year}
        elif not pre and years:
            pre = {years[0]}

        self.year_list.blockSignals(True)
        self.year_list.clear()
        for y in years:
            item = QListWidgetItem(y)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if y in pre else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, y)
            self.year_list.addItem(item)
        self.year_list.blockSignals(False)
        self._sync_select_all_years()

    def _populate_tracks(self) -> None:
        tracks = sorted(
            {
                str(s.get("track") or "").strip().upper()
                for s in self.repo.list_students(include_withdrawn=False)
                if str(s.get("track") or "").strip()
            }
        )
        self.track_combo.blockSignals(True)
        cur = self.track_combo.currentData()
        self.track_combo.clear()
        self.track_combo.addItem("Tous", "")
        for t in tracks:
            self.track_combo.addItem(t, t)
        if cur is not None:
            i = self.track_combo.findData(cur)
            if i >= 0:
                self.track_combo.setCurrentIndex(i)
        self.track_combo.blockSignals(False)

    def _apply_criteria(self, c: StatisticsCriteria) -> None:
        self.level_combo.setCurrentIndex(self.level_combo.findData(c.level or ""))
        self.track_combo.setCurrentIndex(self.track_combo.findData(c.track or ""))
        if c.genders is None or set(c.genders) >= ALL_STATISTICS_GENDER_CODES:
            selected = set(ALL_STATISTICS_GENDER_CODES)
        else:
            selected = {str(g) for g in c.genders}
        for code, chk in self._gender_checks.items():
            chk.setChecked(code in selected)
        self.chk_by_year.setChecked(c.include_by_academic_year)
        self.chk_by_level.setChecked(c.include_by_level)
        self.chk_by_track.setChecked(c.include_by_track)
        self.chk_by_gender.setChecked(c.include_by_gender)
        self.chk_nationality.setChecked(c.include_nationality)
        self.chk_origin_country.setChecked(c.include_origin_country)
        self.chk_origin_inst.setChecked(c.include_origin_institution)
        self.chk_enrollment.setChecked(c.include_enrollment_institution)
        self.chk_success.setChecked(c.include_success)
        self.chk_internship.setChecked(c.include_internship)
        i = self.session_combo.findData(c.view_session or "s1")
        if i >= 0:
            self.session_combo.setCurrentIndex(i)
        self._preselected_template_ids = list(c.template_ids)

    def _selected_years(self) -> list[str]:
        out: list[str] = []
        for i in range(self.year_list.count()):
            item = self.year_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                y = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if y:
                    out.append(y)
        return out

    def _refresh_template_list(self) -> None:
        years = set(self._selected_years())
        pre = set(getattr(self, "_preselected_template_ids", []) or [])
        self.template_list.blockSignals(True)
        self.template_list.clear()
        for t in self.repo.list_templates():
            ay = str(t.get("academic_year") or "").strip()
            if years and ay and ay not in years:
                continue
            tid = int(t["id"])
            label = f"{t.get('name', '')} [{ay}]"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = (not pre and years and ay in years) or tid in pre
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, tid)
            self.template_list.addItem(item)
        self.template_list.blockSignals(False)
        self._preselected_template_ids = []

    def _on_select_all_years_toggled(self, checked: bool) -> None:
        self._syncing_years = True
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.year_list.blockSignals(True)
        for i in range(self.year_list.count()):
            self.year_list.item(i).setCheckState(state)
        self.year_list.blockSignals(False)
        self._syncing_years = False
        self._refresh_template_list()

    def _on_year_item_changed(self) -> None:
        if not getattr(self, "_syncing_years", False):
            self._sync_select_all_years()
        self._refresh_template_list()

    def _sync_select_all_years(self) -> None:
        total = self.year_list.count()
        checked = sum(
            1
            for i in range(total)
            if self.year_list.item(i).checkState() == Qt.CheckState.Checked
        )
        cb = self._select_all_years_cb
        cb.blockSignals(True)
        if checked == 0:
            cb.setCheckState(Qt.CheckState.Unchecked)
        elif checked == total:
            cb.setCheckState(Qt.CheckState.Checked)
        else:
            cb.setCheckState(Qt.CheckState.PartiallyChecked)
        cb.blockSignals(False)

    def _on_accept(self) -> None:
        years = self._selected_years()
        if not years:
            QMessageBox.warning(self, "Critères", "Sélectionnez au moins un millésime.")
            return
        if not self._selected_genders():
            QMessageBox.warning(
                self,
                "Critères",
                "Sélectionnez au moins un genre (Homme, Femme, Autre ou non renseigné).",
            )
            return
        if (self.chk_success.isChecked() or self.chk_internship.isChecked()) and not self._selected_template_ids():
            QMessageBox.warning(
                self,
                "Critères",
                "Cochez au moins une maquette pour la réussite ou le suivi stages, "
                "ou décochez ces indicateurs.",
            )
            return
        self.accept()

    def _selected_template_ids(self) -> list[int]:
        out: list[int] = []
        for i in range(self.template_list.count()):
            item = self.template_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return out

    def _selected_genders(self) -> list[str] | None:
        selected = [code for code, chk in self._gender_checks.items() if chk.isChecked()]
        if not selected or set(selected) >= ALL_STATISTICS_GENDER_CODES:
            return None
        return selected

    def criteria(self) -> StatisticsCriteria:
        return StatisticsCriteria(
            academic_years=self._selected_years(),
            level=str(self.level_combo.currentData() or ""),
            track=str(self.track_combo.currentData() or ""),
            genders=self._selected_genders(),
            include_by_academic_year=self.chk_by_year.isChecked(),
            include_by_level=self.chk_by_level.isChecked(),
            include_by_track=self.chk_by_track.isChecked(),
            include_by_gender=self.chk_by_gender.isChecked(),
            include_nationality=self.chk_nationality.isChecked(),
            include_origin_country=self.chk_origin_country.isChecked(),
            include_origin_institution=self.chk_origin_inst.isChecked(),
            include_enrollment_institution=self.chk_enrollment.isChecked(),
            include_success=self.chk_success.isChecked(),
            include_internship=self.chk_internship.isChecked(),
            template_ids=self._selected_template_ids(),
            view_session=str(self.session_combo.currentData() or "s1"),
        )
