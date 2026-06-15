from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class EnrollmentDialog(QDialog):
    """Gérer les étudiants inscrits à une maquette (enrollments)."""

    def __init__(self, repo, *, template_id: int, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.template_id = int(template_id)
        self.setWindowTitle("Gérer les inscriptions à la maquette")

        layout = QVBoxLayout(self)

        t = next((x for x in self.repo.list_templates() if int(x["id"]) == self.template_id), None) or {}
        title = f"{t.get('name','')} [{t.get('academic_year','')}] — {t.get('level','')} {t.get('track','')}".strip()
        self.header = QLabel(f"<b>Maquette</b> : {title}")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        self.hint = QLabel(
            "Cochez les étudiants à suivre dans cette maquette. La liste inclut tous les étudiants "
            "du même millésime que la maquette : vous pouvez retirer des inscriptions ou ajouter "
            "des étudiants supplémentaires (hors parcours). La colonne « Correspondance » indique "
            "si le niveau et le parcours en fiche correspondent à la maquette."
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        actions = QHBoxLayout()
        self.select_all_btn = QPushButton("Tout cocher")
        self.select_none_btn = QPushButton("Tout décocher")
        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all(False))
        actions.addWidget(self.select_all_btn)
        actions.addWidget(self.select_none_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self.table = QTableWidget()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._rows: list[dict[str, Any]] = []
        self.refresh()

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(950, 600), minimum=(640, 400))

    def refresh(self) -> None:
        candidates = self.repo.list_students_for_enrollment_editor(self.template_id)
        enrolled = {int(s["id"]) for s in self.repo.list_students_for_template(self.template_id)}
        self._rows = candidates

        headers = [
            "Inscrit",
            "N° INE",
            "Nom",
            "Prénom",
            "Parcours (fiche)",
            "Correspondance",
            "Année",
        ]
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(candidates))
        for r, s in enumerate(candidates):
            sid = int(s["id"])
            it0 = QTableWidgetItem("")
            it0.setFlags(it0.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it0.setCheckState(Qt.Checked if sid in enrolled else Qt.Unchecked)
            it0.setData(Qt.ItemDataRole.UserRole, sid)
            self.table.setItem(r, 0, it0)

            lv = str(s.get("level") or "").strip()
            tr = str(s.get("track") or "").strip()
            parcours_fiche = f"{lv} {tr}".strip() or "—"
            match_ok = self.repo.student_matches_template_parcours(s, self.template_id)
            corres = "Oui" if match_ok else "Non"

            vals = [
                s.get("student_number_ine", "") or s.get("student_number", ""),
                s.get("last_name", ""),
                s.get("first_name", ""),
                parcours_fiche,
                corres,
                s.get("academic_year", ""),
            ]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(str(v))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def selected_student_ids(self) -> list[int]:
        ids: list[int] = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is None or it.checkState() != Qt.Checked:
                continue
            raw = it.data(Qt.ItemDataRole.UserRole)
            if raw is None:
                continue
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return ids

    def _set_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is not None:
                it.setCheckState(state)
