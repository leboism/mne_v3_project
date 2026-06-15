"""Planning des soutenances de stage depuis l'onglet Cours."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..services.dates import format_defense_slot
from ..services.internship_defense_planning import (
    PLANNING_HEADERS,
    planning_rows_for_export,
    planning_to_text,
)


class InternshipDefensePlanningDialog(QDialog):
    def __init__(
        self,
        repo,
        *,
        course_id: int | None = None,
        academic_year: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self._default_year = (academic_year or "").strip()
        self._course_ids: list[int] = []
        self._rows: list[dict[str, Any]] = []
        self.setWindowTitle("Planning des soutenances de stage")
        root = QVBoxLayout(self)
        hint = QLabel(
            "Liste les étudiants inscrits à toutes les maquettes contenant l'UE stage "
            "(millésime choisi). Les dates et rapporteurs se renseignent dans chaque "
            "dossier stage (fiche étudiant → Stages)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        form = QFormLayout()
        self.year_combo = QComboBox()
        self.year_combo.currentIndexChanged.connect(self._reload)
        form.addRow("Millésime :", self.year_combo)

        self.course_combo = QComboBox()
        self.course_combo.setMinimumWidth(360)
        self.course_combo.currentIndexChanged.connect(self._reload)
        form.addRow("UE stage :", self.course_combo)
        root.addLayout(form)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.copy_btn = QPushButton("Copier le planning…")
        self.copy_btn.clicked.connect(self._copy_text)
        self.csv_btn = QPushButton("Exporter CSV…")
        self.csv_btn.clicked.connect(self._export_csv)
        actions.addWidget(self.copy_btn)
        actions.addWidget(self.csv_btn)
        actions.addStretch()
        root.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.clicked.connect(self.accept)
        root.addWidget(buttons)

        self._populate_years()
        self._populate_courses(course_id)
        self._reload()

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(980, 640), minimum=(720, 480))

    def _populate_years(self) -> None:
        years = sorted(
            {
                str(t.get("academic_year") or "").strip()
                for t in self.repo.list_templates()
                if str(t.get("academic_year") or "").strip()
            },
            reverse=True,
        )
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for y in years:
            self.year_combo.addItem(y, y)
        if self._default_year:
            i = self.year_combo.findData(self._default_year)
            if i >= 0:
                self.year_combo.setCurrentIndex(i)
            else:
                self.year_combo.insertItem(0, self._default_year, self._default_year)
                self.year_combo.setCurrentIndex(0)
        elif self.year_combo.count():
            self.year_combo.setCurrentIndex(0)
        self.year_combo.blockSignals(False)

    def _populate_courses(self, preselect: int | None) -> None:
        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        self._course_ids = []
        for c in self.repo.list_courses():
            if not self.repo.is_internship_course(int(c["id"])):
                continue
            self._course_ids.append(int(c["id"]))
            self.course_combo.addItem(
                f"{c.get('code', '')} — {c.get('name', '')}".strip(" —"),
                int(c["id"]),
            )
        if preselect is not None:
            idx = self.course_combo.findData(int(preselect))
            if idx >= 0:
                self.course_combo.setCurrentIndex(idx)
        self.course_combo.blockSignals(False)

    def _current_course_id(self) -> int | None:
        data = self.course_combo.currentData()
        return int(data) if data is not None else None

    def _reload(self) -> None:
        cid = self._current_course_id()
        year = str(self.year_combo.currentData() or "")
        if cid is None:
            self._rows = []
            self.table.setRowCount(0)
            self.summary.setText("Sélectionnez une UE de type stage.")
            return
        self._rows = self.repo.list_internship_defense_planning(int(cid), academic_year=year)
        course = self.repo.get_course(int(cid)) or {}
        course_label = f"{course.get('code', '')} — {course.get('name', '')}".strip(" —")
        scheduled = sum(1 for r in self._rows if str(r.get("defense_date") or "").strip())
        self.summary.setText(
            f"<b>{course_label}</b> — {len(self._rows)} étudiant(s), "
            f"{scheduled} avec date de soutenance renseignée."
        )
        headers = ["Date / heure", "Étudiant", "Maquette", "Sujet", "Rapporteur", "Établ. rapporteur"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            slot = format_defense_slot(
                str(row.get("defense_date") or ""),
                str(row.get("defense_time") or ""),
            )
            who = f"{row.get('last_name', '')} {row.get('first_name', '')}".strip()
            from ..services.lookups import student_transcript_number

            sn = student_transcript_number(row)
            if sn:
                who = f"{who} ({sn})"
            tpl = str(row.get("template_name") or "")
            lv = str(row.get("level") or "").strip()
            tr = str(row.get("track") or "").strip()
            if lv or tr:
                tpl = f"{tpl} ({lv} {tr})".strip()
            vals = [
                slot,
                who,
                tpl,
                str(row.get("topic") or "").replace("\n", " ")[:120],
                str(row.get("reporter_name") or ""),
                str(row.get("reporter_institution") or ""),
            ]
            for c, txt in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(txt))
        self.table.resizeColumnsToContents()

    def _copy_text(self) -> None:
        cid = self._current_course_id()
        if cid is None:
            return
        course = self.repo.get_course(int(cid)) or {}
        course_label = f"{course.get('code', '')} — {course.get('name', '')}".strip(" —")
        text = planning_to_text(
            self._rows,
            course_label=course_label,
            academic_year=str(self.year_combo.currentData() or ""),
        )
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "Planning", "Planning copié dans le presse-papiers.")

    def _export_csv(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "Export", "Rien à exporter.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter le planning",
            str(Path.home() / "planning_soutenances_stage.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.writer(f)
            writer.writerow(PLANNING_HEADERS)
            writer.writerows(planning_rows_for_export(self._rows))
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
