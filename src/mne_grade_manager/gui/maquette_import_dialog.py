"""Dialogue d’import Excel maquette (feuille + options)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..services.maquette_import import (
    MAQUETTE_SHEET_TO_TRACK,
    ConsolidatedTrackPlan,
    detect_maquette_import_mode,
    extract_academic_year_from_path,
    list_maquette_sheets,
    plan_consolidated_of_import,
)


class MaquetteImportDialog(QDialog):
    def __init__(self, file_path: str, parent=None, template_id: int | None = None):
        super().__init__(parent)
        self.setWindowTitle("Import maquette Excel")
        self._path = file_path
        self._template_id = template_id
        self._sheet_names = list_maquette_sheets(file_path)
        self._default_mode = detect_maquette_import_mode(file_path, self._sheet_names)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Fichier :\n{file_path}"))
        form = QFormLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(
            "Offre de formation (OF) — 1 maquette par parcours",
            "of_consolidated",
        )
        self.mode_combo.addItem("Créer 1 maquette par onglet (ancien format)", "multi")
        self.mode_combo.addItem("Importer 1 onglet", "single")
        form.addRow("Mode", self.mode_combo)

        self.sheet_combo = QComboBox()
        for name in self._sheet_names:
            track = MAQUETTE_SHEET_TO_TRACK.get(name, "")
            suffix = f"  →  {track}" if track else ""
            self.sheet_combo.addItem(f"{name}{suffix}", name)
        self.sheet_label = QLabel("Onglet source :")
        form.addRow(self.sheet_label, self.sheet_combo)

        self.academic_year = QLineEdit()
        self.academic_year.setPlaceholderText("ex. 2026-2027")
        year = extract_academic_year_from_path(file_path)
        if year:
            self.academic_year.setText(year)
        form.addRow("Année universitaire (nouvelles maquettes)", self.academic_year)

        layout.addLayout(form)

        self.sheets_table = QTableWidget()
        self.sheets_table.setColumnCount(5)
        self.sheets_table.setHorizontalHeaderLabels(
            ["Importer", "Source", "Niveau", "Parcours", "Nom de maquette"]
        )
        layout.addWidget(self.sheets_table)

        form2 = QFormLayout()
        self.update_existing = QCheckBox("Mettre à jour les cours dont le code existe déjà")
        self.update_existing.setChecked(True)
        form2.addRow(self.update_existing)
        self.attach_to_maquette = QCheckBox(
            "Ajouter les cours importés à la maquette sélectionnée (si pas déjà présents)"
        )
        self.attach_to_maquette.setChecked(bool(template_id))
        self.attach_to_maquette.setEnabled(template_id is not None)
        if template_id is None:
            self.attach_to_maquette.setToolTip(
                "Sélectionnez une maquette dans la liste de gauche avant d’importer."
            )
        form2.addRow(self.attach_to_maquette)
        layout.addLayout(form2)

        self.track_hint = QLabel("")
        self.track_hint.setWordWrap(True)
        layout.addWidget(self.track_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        idx = self.mode_combo.findData(self._default_mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)

        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.sheet_combo.currentIndexChanged.connect(self._refresh_tables)
        self.academic_year.textChanged.connect(self._refresh_tables)
        self._mode_changed()

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(820, 580), minimum=(520, 400))

    def sheet_name(self) -> str:
        return str(self.sheet_combo.currentData() or self.sheet_combo.currentText())

    def is_of_consolidated_mode(self) -> bool:
        return str(self.mode_combo.currentData() or "") == "of_consolidated"

    def is_multi_mode(self) -> bool:
        return str(self.mode_combo.currentData() or "") == "multi"

    def selected_consolidated_plans(self) -> list[ConsolidatedTrackPlan]:
        out: list[ConsolidatedTrackPlan] = []
        for r in range(self.sheets_table.rowCount()):
            it0 = self.sheets_table.item(r, 0)
            if it0 is None or it0.checkState() != Qt.Checked:
                continue
            plan = it0.data(Qt.ItemDataRole.UserRole)
            if isinstance(plan, ConsolidatedTrackPlan):
                name_item = self.sheets_table.item(r, 4)
                if name_item is not None:
                    custom = name_item.text().strip()
                    if custom:
                        plan = ConsolidatedTrackPlan(
                            level=plan.level,
                            track=plan.track,
                            name=custom,
                            sheet=plan.sheet,
                            rows=plan.rows,
                        )
                out.append(plan)
        return out

    def selected_sheets(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in range(self.sheets_table.rowCount()):
            it0 = self.sheets_table.item(r, 0)
            if it0 is None or it0.checkState() != Qt.Checked:
                continue
            sheet = (self.sheets_table.item(r, 1).text() if self.sheets_table.item(r, 1) else "").strip()
            level = (self.sheets_table.item(r, 2).text() if self.sheets_table.item(r, 2) else "").strip()
            track = (self.sheets_table.item(r, 3).text() if self.sheets_table.item(r, 3) else "").strip()
            name = (self.sheets_table.item(r, 4).text() if self.sheets_table.item(r, 4) else "").strip()
            if sheet:
                out.append({"sheet": sheet, "level": level, "track": track, "name": name})
        return out

    def academic_year_value(self) -> str:
        return self.academic_year.text().strip()

    def should_update_existing(self) -> bool:
        return self.update_existing.isChecked()

    def should_attach_to_maquette(self) -> bool:
        return self._template_id is not None and self.attach_to_maquette.isChecked()

    def template_id(self) -> int | None:
        return self._template_id

    def _refresh_tables(self) -> None:
        if self.is_of_consolidated_mode():
            self._fill_of_table()
        elif self.is_multi_mode():
            self._fill_legacy_multi_table()

    def _fill_of_table(self) -> None:
        sheet = self.sheet_name()
        year = self.academic_year_value()
        try:
            plans = plan_consolidated_of_import(self._path, sheet, academic_year=year)
        except Exception as exc:
            self.sheets_table.setRowCount(1)
            it0 = QTableWidgetItem("")
            it0.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sheets_table.setItem(0, 0, it0)
            self.sheets_table.setItem(0, 1, QTableWidgetItem(f"Erreur : {exc}"))
            return

        self.sheets_table.setRowCount(len(plans))
        for r, plan in enumerate(plans):
            it0 = QTableWidgetItem("")
            it0.setCheckState(Qt.CheckState.Checked)
            it0.setFlags(it0.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            it0.setData(Qt.ItemDataRole.UserRole, plan)
            self.sheets_table.setItem(r, 0, it0)

            src = QTableWidgetItem(f"{plan.sheet} ({plan.row_count} UE)")
            src.setFlags(src.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sheets_table.setItem(r, 1, src)

            lv = QTableWidgetItem(plan.level)
            lv.setFlags(lv.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sheets_table.setItem(r, 2, lv)

            tr = QTableWidgetItem(plan.track)
            tr.setFlags(tr.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sheets_table.setItem(r, 3, tr)

            self.sheets_table.setItem(r, 4, QTableWidgetItem(plan.name))
        self.sheets_table.resizeColumnsToContents()

    def _fill_legacy_multi_table(self) -> None:
        self.sheets_table.setRowCount(len(self._sheet_names))
        ay = self.academic_year_value()
        for r, name in enumerate(self._sheet_names):
            track = (MAQUETTE_SHEET_TO_TRACK.get(name, "") or "").strip()
            level = ""
            u = name.upper().strip()
            if u.startswith("M1"):
                level = "M1"
            elif u.startswith("M2"):
                level = "M2"
            it0 = QTableWidgetItem("")
            it0.setCheckState(Qt.CheckState.Checked)
            it0.setFlags(it0.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            self.sheets_table.setItem(r, 0, it0)
            it1 = QTableWidgetItem(name)
            it1.setFlags(it1.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sheets_table.setItem(r, 1, it1)
            it2 = QTableWidgetItem(level)
            it2.setFlags(it2.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sheets_table.setItem(r, 2, it2)
            it3 = QTableWidgetItem(track)
            it3.setFlags(it3.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sheets_table.setItem(r, 3, it3)
            default_name = name
            if ay or level or track:
                default_name = f"{ay or ''} — {level} {track}".strip(" —").strip()
            self.sheets_table.setItem(r, 4, QTableWidgetItem(default_name or name))
        self.sheets_table.resizeColumnsToContents()

    def _mode_changed(self) -> None:
        of_mode = self.is_of_consolidated_mode()
        multi = self.is_multi_mode()
        self.sheet_combo.setVisible(of_mode or not multi)
        self.sheet_label.setVisible(of_mode or not multi)
        self.sheets_table.setVisible(of_mode or multi)
        self.attach_to_maquette.setVisible(not of_mode and not multi)

        if of_mode:
            self.track_hint.setText(
                "Fichier OF consolidé (PR1162 / PR1163) : le programme crée une maquette par parcours. "
                "Les UE de tronc commun sont incluses dans chaque parcours ; les blocs spécifiques "
                "(ex. « Bloc 4 NFC », « Spécialité Physique ») ne vont que dans le parcours concerné."
            )
            self._fill_of_table()
        elif multi:
            self.track_hint.setText(
                "Ancien format : un onglet Excel par parcours (M1P, M2 NPD, …). "
                "Une maquette est créée pour chaque onglet coché."
            )
            self._fill_legacy_multi_table()
        else:
            self.track_hint.setText(
                "Importe toutes les UE de l’onglet vers le catalogue (et optionnellement la maquette sélectionnée)."
            )
            self.sheets_table.setVisible(False)
