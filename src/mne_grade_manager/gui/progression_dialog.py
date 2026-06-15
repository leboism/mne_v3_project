from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.parcours import OTHER_TRACK_DATA, parcours_choices
from ..services.dates import suggest_next_academic_year


class StudentProgressionDialog(QDialog):
    """Passage M1 → M2 ou redoublement avec conservation partielle des notes."""

    def __init__(self, repo, *, student_id: int, default_academic_year: str = "", parent=None):
        super().__init__(parent)
        self.repo = repo
        self.student_id = int(student_id)
        self._student: dict[str, Any] | None = repo.get_student(self.student_id)
        self.setWindowTitle("Parcours : passage en M2 / redoublement")

        root = QVBoxLayout(self)
        s = self._student or {}
        who = f"{s.get('first_name', '')} {s.get('last_name', '')}".strip()
        inf = f"{s.get('level', '')} {s.get('track', '')} — {s.get('academic_year', '')}".strip()
        self.summary = QLabel(f"<b>{who}</b><br>{inf}")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        cur_ay = str(s.get("academic_year") or "").strip()
        next_ay = suggest_next_academic_year(cur_ay) or (default_academic_year or "").strip()

        # —— Onglet passage M2
        self.tab_m2 = QWidget()
        m2_l = QVBoxLayout(self.tab_m2)
        m2_l.addWidget(
            QLabel(
                "L’étudiant passe en <b>M2</b> sur la <b>même fiche</b> (pas de doublon) : "
                "l’inscription M1 est conservée pour l’historique ; une inscription à la maquette M2 "
                "est ajoutée. Les <b>notes M1 restent en base</b>."
            )
        )
        m2_l.addWidget(QLabel("Choisissez le parcours M2 (NPD, DWM, etc.)."))
        form_m2 = QFormLayout()
        self.m2_year = QLineEdit(next_ay)
        form_m2.addRow("Nouvelle année universitaire :", self.m2_year)
        track_row = QHBoxLayout()
        self.m2_track_combo = QComboBox()
        for code, lab in parcours_choices("M2"):
            self.m2_track_combo.addItem(f"{code} — {lab}", code)
        self.m2_track_combo.addItem("Autre…", OTHER_TRACK_DATA)
        self.m2_track_other = QLineEdit()
        self.m2_track_other.setPlaceholderText("Code parcours")
        self.m2_track_combo.currentIndexChanged.connect(self._m2_track_changed)
        track_row.addWidget(self.m2_track_combo, stretch=1)
        track_row.addWidget(self.m2_track_other, stretch=1)
        w_track = QWidget()
        w_track.setLayout(track_row)
        form_m2.addRow("Parcours M2 :", w_track)
        m2_l.addLayout(form_m2)
        m2_l.addStretch()
        self._m2_track_changed()
        self.tabs.addTab(self.tab_m2, "Passage en M2")

        # —— Onglet redoublement
        self.tab_repeat = QWidget()
        rep_l = QVBoxLayout(self.tab_repeat)
        rep_l.addWidget(
            QLabel(
                "Même niveau (ex. redoublement M1), <b>nouvelle année</b> : toujours une seule fiche "
                "étudiant ; les inscriptions aux maquettes des années précédentes sont conservées. "
                "Pour chaque UE : cochez <b>Conserver les notes</b> pour garder les notes ; "
                "décochez pour les effacer (à repasser)."
            )
        )
        form_rep = QFormLayout()
        self.repeat_year = QLineEdit(next_ay)
        form_rep.addRow("Nouvelle année universitaire :", self.repeat_year)
        rep_l.addLayout(form_rep)
        self.table_courses = QTableWidget()
        self.table_courses.setColumnCount(4)
        self.table_courses.setHorizontalHeaderLabels(["Conserver", "Code", "UE", "id"])
        self.table_courses.hideColumn(3)
        rep_l.addWidget(self.table_courses)
        self.tabs.addTab(self.tab_repeat, "Redoublement (même niveau)")

        self._fill_courses_table()

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._try_commit)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        lv = str(s.get("level") or "").strip().upper()
        if lv != "M1":
            self.tabs.setTabEnabled(0, False)
            if lv == "M2":
                tip = "Passage en M2 : disponible uniquement pour les étudiants actuellement en M1."
            else:
                tip = "Passage en M2 : indiquez M1 dans la fiche étudiant pour activer cet onglet."
            self.tabs.setTabToolTip(0, tip)

        from .screen_layout import adapt_window_size

        adapt_window_size(self, preferred=(720, 520), minimum=(560, 400))

    def _m2_track_changed(self) -> None:
        is_other = self.m2_track_combo.currentData() == OTHER_TRACK_DATA
        self.m2_track_other.setVisible(is_other)
        self.m2_track_other.setEnabled(is_other)

    def _m2_track_value(self) -> str:
        d = self.m2_track_combo.currentData()
        if d == OTHER_TRACK_DATA:
            return self.m2_track_other.text().strip()
        return str(d or "").strip()

    def _fill_courses_table(self) -> None:
        courses = self.repo.list_courses_with_grades_for_student(self.student_id)
        self.table_courses.setRowCount(len(courses))
        for r, c in enumerate(courses):
            cid = int(c["course_id"])
            chk = QTableWidgetItem("")
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked)
            chk.setData(Qt.ItemDataRole.UserRole, cid)
            self.table_courses.setItem(r, 0, chk)
            code_it = QTableWidgetItem(str(c.get("code") or ""))
            code_it.setFlags(code_it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_courses.setItem(r, 1, code_it)
            name_it = QTableWidgetItem(str(c.get("name") or ""))
            name_it.setFlags(name_it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_courses.setItem(r, 2, name_it)
            id_it = QTableWidgetItem(str(cid))
            id_it.setData(Qt.ItemDataRole.UserRole, cid)
            self.table_courses.setItem(r, 3, id_it)
        self.table_courses.resizeColumnsToContents()

    def _course_ids_to_clear(self) -> list[int]:
        out: list[int] = []
        for r in range(self.table_courses.rowCount()):
            it = self.table_courses.item(r, 0)
            if it is None:
                continue
            if it.checkState() == Qt.CheckState.Checked:
                continue
            raw = it.data(Qt.ItemDataRole.UserRole)
            if raw is not None:
                try:
                    out.append(int(raw))
                except (TypeError, ValueError):
                    pass
        return out

    def _try_commit(self) -> None:
        if self._student is None:
            QMessageBox.warning(self, "Erreur", "Étudiant introuvable.")
            return
        try:
            if self.tabs.currentIndex() == 0:
                y = self.m2_year.text().strip()
                tr = self._m2_track_value()
                if not y or not tr:
                    QMessageBox.warning(self, "Saisie incomplète", "Année et parcours M2 obligatoires.")
                    return
                self.repo.promote_student_to_m2(self.student_id, y, tr)
            else:
                y = self.repeat_year.text().strip()
                if not y:
                    QMessageBox.warning(self, "Saisie incomplète", "Indiquez la nouvelle année universitaire.")
                    return
                if not str(self._student.get("level") or "").strip():
                    QMessageBox.warning(
                        self,
                        "Niveau manquant",
                        "Renseignez le niveau (M1, M2…) dans la fiche avant un redoublement.",
                    )
                    return
                to_clear = self._course_ids_to_clear()
                self.repo.repeat_student_same_level(self.student_id, y, to_clear)
        except ValueError as e:
            QMessageBox.warning(self, "Progression", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        self.accept()
