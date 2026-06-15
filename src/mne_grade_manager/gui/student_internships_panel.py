"""Stages d'un étudiant : un dossier par maquette / année (UE de type stage)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.institutions import INTERNSHIP_STATUS_CHOICES
from ..services.dates import format_defense_slot
from .internship_dialog import InternshipDialog

if TYPE_CHECKING:
    from ..services.repository import Repository

_STATUS_LABELS = {k: lab for k, lab in INTERNSHIP_STATUS_CHOICES}


def internship_convention_label(rec: dict[str, Any] | None) -> str:
    if not rec:
        return "—"
    paper = bool(int(rec.get("convention_paper") or 0))
    path = str(rec.get("convention_path") or "").strip()
    if paper and path:
        return "PDF + Papier"
    if paper:
        return "Papier"
    if path:
        return "PDF"
    return "—"


class StudentInternshipsPanel(QWidget):
    def __init__(self, parent=None, *, repo: Repository | None = None, student_id: int | None = None):
        super().__init__(parent)
        self.repo = repo
        self.student_id = student_id
        self._slots: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(
            "Un dossier stage par année universitaire (maquette M1, M2, redoublement…). "
            "La saisie des notes de stage reste dans l'onglet Notes ; le suivi administratif se fait ici."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                "Année",
                "Maquette",
                "UE stage",
                "Soutenance",
                "Rapporteur",
                "Suivi",
                "Convention",
            ]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._open_selected)
        root.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.open_btn = QPushButton("Ouvrir le dossier…")
        self.open_btn.clicked.connect(self._open_selected)
        bar.addWidget(self.open_btn)
        bar.addStretch()
        root.addLayout(bar)

        if student_id is not None and repo is not None:
            self.refresh()

    def set_student_context(self, repo: Repository, student_id: int) -> None:
        self.repo = repo
        self.student_id = int(student_id)
        self.refresh()

    def refresh(self) -> None:
        if self.repo is None or self.student_id is None:
            self._slots = []
            self.table.setRowCount(0)
            return
        self._slots = self.repo.list_student_internship_slots(int(self.student_id))
        self.table.setRowCount(len(self._slots))
        for r, slot in enumerate(self._slots):
            tpl = slot.get("template") or {}
            course = slot.get("course") or {}
            rec = slot.get("record") or {}
            ay = str(tpl.get("academic_year") or "")
            tpl_lab = str(tpl.get("name") or "")
            lv = str(tpl.get("level") or "").strip()
            tr = str(tpl.get("track") or "").strip()
            if lv or tr:
                tpl_lab = f"{tpl_lab} ({lv} {tr})".strip()
            ue = f"{course.get('code', '')} — {course.get('name', '')}".strip(" —")
            st_key = str(rec.get("follow_up_status") or "").strip()
            st_lab = _STATUS_LABELS.get(st_key, st_key or "Non renseigné")
            conv = internship_convention_label(rec if rec else None)
            sout = format_defense_slot(
                str(rec.get("defense_date") or ""),
                str(rec.get("defense_time") or ""),
            )
            rep = " ".join(
                x
                for x in (
                    str(rec.get("reporter_first_name") or "").strip(),
                    str(rec.get("reporter_last_name") or "").strip(),
                )
                if x
            )
            rep_inst = str(rec.get("reporter_institution") or "").strip()
            if rep and rep_inst:
                rep = f"{rep} ({rep_inst})"
            elif rep_inst:
                rep = rep_inst
            for c, txt in enumerate((ay, tpl_lab, ue, sout, rep or "—", st_lab, conv)):
                it = QTableWidgetItem(txt)
                it.setData(Qt.ItemDataRole.UserRole, r)
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _selected_slot(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._slots):
            return None
        return self._slots[row]

    def _open_selected(self) -> None:
        if self.repo is None or self.student_id is None:
            return
        slot = self._selected_slot()
        if slot is None:
            QMessageBox.information(self, "Stage", "Sélectionnez un stage dans la liste.")
            return
        tpl = slot.get("template") or {}
        course = slot.get("course") or {}
        dlg = InternshipDialog(
            self.repo,
            student_id=int(self.student_id),
            template_id=int(tpl["id"]),
            course_id=int(course["id"]),
            parent=self,
        )
        if dlg.exec():
            self.refresh()
