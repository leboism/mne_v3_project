"""Sélection des UE suivies pour un étudiant ERASMUS / mobilité."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..services.repository import Repository

from ..services.timetable_legacy import course_public_code


class StudentErasmusCoursesPanel(QWidget):
    def __init__(
        self,
        parent=None,
        *,
        repo: Repository | None = None,
        student_id: int | None = None,
        academic_year: str = "",
    ):
        super().__init__(parent)
        self.repo = repo
        self.student_id = student_id
        self.academic_year = (academic_year or "").strip()

        root = QVBoxLayout(self)
        hint = QLabel(
            "Cochez les UE que l'étudiant suit ce semestre. "
            "Seules ces UE apparaîtront à la saisie des notes et sur son relevé / PV."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        root.addWidget(hint)

        box = QGroupBox("UE suivies")
        bl = QVBoxLayout(box)
        self.course_list = QListWidget()
        self.course_list.setAlternatingRowColors(True)
        bl.addWidget(self.course_list)
        root.addWidget(box, 1)

        if repo is not None and student_id is not None:
            self.reload()

    def set_context(
        self, repo: Repository, student_id: int | None, academic_year: str
    ) -> None:
        self.repo = repo
        self.student_id = int(student_id) if student_id is not None else None
        self.academic_year = (academic_year or "").strip()
        self.reload()

    def set_academic_year(self, academic_year: str) -> None:
        self.academic_year = (academic_year or "").strip()
        self.reload()

    def reload(self) -> None:
        self.course_list.clear()
        if self.repo is None or self.student_id is None:
            return
        ay = self.academic_year
        if not ay:
            item = QListWidgetItem("Renseignez l'année universitaire sur l'onglet Fiche.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.course_list.addItem(item)
            return
        courses = self.repo.list_available_erasmus_courses(ay)
        if not courses:
            item = QListWidgetItem(f"Aucune UE trouvée pour le millésime {ay}.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.course_list.addItem(item)
            return
        selected = set(self.repo.list_student_erasmus_course_ids(int(self.student_id), ay))
        for c in courses:
            cid = int(c["id"])
            code = course_public_code(c, academic_year=ay)
            name = str(c.get("name") or "")
            label = f"{code} — {name}".strip(" —")
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, cid)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked if cid in selected else Qt.CheckState.Unchecked
            )
            self.course_list.addItem(it)

    def selected_course_ids(self) -> list[int]:
        ids: list[int] = []
        for i in range(self.course_list.count()):
            it = self.course_list.item(i)
            if it is None or not (it.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            if it.checkState() == Qt.CheckState.Checked:
                cid = it.data(Qt.ItemDataRole.UserRole)
                if cid is not None:
                    ids.append(int(cid))
        return ids

    def persist(self, repo: Repository, student_id: int, academic_year: str) -> None:
        ay = (academic_year or self.academic_year or "").strip()
        if not ay:
            QMessageBox.warning(
                self,
                "ERASMUS",
                "Année universitaire requise pour enregistrer les UE suivies.",
            )
            return
        repo.set_student_erasmus_courses(int(student_id), ay, self.selected_course_ids())
