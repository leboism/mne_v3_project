from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..gui.course_grades_dialog import CourseGradesDialog
from ..gui.enrollment_dialog import EnrollmentDialog
from ..gui.internship_dialog import InternshipDialog
from ..core.institutions import INTERNSHIP_STATUS_CHOICES
from ..gui.widgets import fill_table, make_actions_toolbar
from ..services.grade_status import format_grade_display, parse_grade_cell
from ..services.timetable_legacy import course_public_code


class GradesTab(QWidget):
    def __init__(self, repo, refresh_callbacks=None, *, default_academic_year: str = ""):
        super().__init__()
        self.repo = repo
        self.refresh_callbacks = refresh_callbacks or []
        self.default_academic_year = (default_academic_year or "").strip()
        self.template_ids: list[int] = []
        self.student_ids: list[int] = []
        self.course_ids: list[int] = []
        self._loading_table = False
        self._dirty = False
        self._prev_template_index = -1
        self._prev_student_index = -1
        self._prev_course_index = -1

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.template_combo = QComboBox()
        self.student_combo = QComboBox()
        self.course_combo = QComboBox()
        self.template_combo.currentIndexChanged.connect(self._on_template_combo_changed)
        self.student_combo.currentIndexChanged.connect(self._on_student_combo_changed)
        self.course_combo.currentIndexChanged.connect(self._on_course_combo_changed)
        form.addRow("Maquette", self.template_combo)
        form.addRow("Étudiant", self.student_combo)
        form.addRow("UE", self.course_combo)
        layout.addLayout(form)

        grades_tb = make_actions_toolbar(
            self,
            primary=[
                ("Enregistrer", self.save_grades),
                ("Saisie par matière…", self.open_course_entry),
            ],
            menu_sections=[
                [("Gérer les inscriptions…", self.manage_enrollments)],
                [("Dossier stage (raccourci)…", self.open_internship_dossier)],
            ],
        )
        layout.addLayout(grades_tb.layout)
        self.internship_action = grades_tb.menu_actions["Dossier stage (raccourci)…"]
        save_btn = grades_tb.primary_buttons[0] if grades_tb.primary_buttons else None
        if save_btn is not None:
            save_btn.setToolTip("Enregistrer les modifications (Ctrl+S)")

        self.info_label = QLabel(
            "Sélectionnez une maquette, un étudiant et une UE. "
            "Pour une UE commune à plusieurs parcours, utilisez « Saisie par matière… » "
            "(fusion automatique des étudiants inscrits sur toutes les maquettes concernées). "
            "Note : nombre /20, ou ABJ (absence justifiée, bloque la validation UE/bloc), "
            "DEF (défaillant, compte comme 0 et bloque la validation), "
            "NEUT (neutralisée, exclue de la moyenne), VAL (validée sans note sur une épreuve). "
            "Cochez « UE validée sans note » pour valider toute l’UE sans moyenne chiffrée "
            "(UE libre ou autre cas : équivalence, validation administrative…). "
            "Sélectionnez une ou plusieurs cases « Note » (clic + glisser ou Ctrl+clic), "
            "puis Suppr ou Retour arrière pour vider ; les lignes « Garder » cochées sont ignorées."
        )
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self.info_label)

        self.ue_validated_row = QWidget()
        val_row = QHBoxLayout(self.ue_validated_row)
        val_row.setContentsMargins(0, 0, 0, 0)
        self.ects_validated_cb = QCheckBox(
            "UE validée sans note (ECTS acquises — pas de saisie chiffrée requise)"
        )
        self.ects_validated_cb.toggled.connect(self._on_ects_validated_toggled)
        val_row.addWidget(self.ects_validated_cb)
        val_row.addStretch()
        self.ue_validated_row.hide()
        layout.addWidget(self.ue_validated_row)

        self.table = QTableWidget()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.installEventFilter(self)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table)

        self._install_shortcuts()
        self.refresh()

    def _install_shortcuts(self) -> None:
        save_act = QAction(self)
        save_act.setShortcut(QKeySequence.Save)
        save_act.triggered.connect(self.save_grades)
        self.addAction(save_act)

    def eventFilter(self, obj, event):  # noqa: ANN001
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self._clear_selected_note_cells():
                    return True
        return super().eventFilter(obj, event)

    def _mark_dirty(self, dirty: bool = True) -> None:
        self._dirty = bool(dirty)
        base = self.info_label.text().split("  |  ")[0].strip()
        if self._dirty:
            if "modifications non enregistrées" not in self.info_label.text().lower():
                self.info_label.setText(f"{base}  |  ⚠ Modifications non enregistrées (Ctrl+S)")
        else:
            self.info_label.setText(base)

    def _confirm_discard_or_save(self, *, title: str) -> str:
        """
        Retourne 'save' | 'discard' | 'cancel'.
        """
        if not self._dirty:
            return "discard"
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText("Des modifications n’ont pas été enregistrées.")
        msg.setInformativeText("Voulez-vous enregistrer avant de changer de sélection ?")
        save_btn = msg.addButton("Enregistrer", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = msg.addButton("Ignorer", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = msg.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(save_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is save_btn:
            self.save_grades()
            return "discard" if not self._dirty else "cancel"
        if clicked is discard_btn:
            self._mark_dirty(False)
            return "discard"
        if clicked is cancel_btn:
            return "cancel"
        return "cancel"

    def _on_template_combo_changed(self, idx: int) -> None:
        if self._prev_template_index < 0:
            self._prev_template_index = idx
            self._template_changed()
            return
        decision = self._confirm_discard_or_save(title="Changer de maquette")
        if decision == "cancel":
            self.template_combo.blockSignals(True)
            self.template_combo.setCurrentIndex(self._prev_template_index)
            self.template_combo.blockSignals(False)
            return
        self._prev_template_index = idx
        self._template_changed()

    def _on_student_combo_changed(self, idx: int) -> None:
        if self._prev_student_index < 0:
            self._prev_student_index = idx
            template_id = self.template_combo.currentData()
            if template_id is not None:
                self._refresh_course_combo(int(template_id), self.course_combo.currentData(), None)
            self.refresh_assessment_table()
            return
        decision = self._confirm_discard_or_save(title="Changer d’étudiant")
        if decision == "cancel":
            self.student_combo.blockSignals(True)
            self.student_combo.setCurrentIndex(self._prev_student_index)
            self.student_combo.blockSignals(False)
            return
        self._prev_student_index = idx
        template_id = self.template_combo.currentData()
        if template_id is not None:
            self._refresh_course_combo(int(template_id), self.course_combo.currentData(), None)
        self.refresh_assessment_table()

    def _on_course_combo_changed(self, idx: int) -> None:
        if self._prev_course_index < 0:
            self._prev_course_index = idx
            self.refresh_assessment_table()
            return
        decision = self._confirm_discard_or_save(title="Changer d’UE")
        if decision == "cancel":
            self.course_combo.blockSignals(True)
            self.course_combo.setCurrentIndex(self._prev_course_index)
            self.course_combo.blockSignals(False)
            return
        self._prev_course_index = idx
        self.refresh_assessment_table()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_table:
            return
        if item is None:
            return
        # Colonnes éditables : Note (5), Garder (6) et Commentaire (7)
        if item.column() in (5, 6, 7):
            self._mark_dirty(True)

    def _clear_selected_note_cells(self) -> bool:
        """Vide la colonne Note (5) pour les cellules sélectionnées, sauf si « Garder » est coché."""
        idxs = self.table.selectedIndexes()
        if not idxs:
            return False
        note_col = 5
        done = False
        for ix in idxs:
            if ix.column() != note_col:
                continue
            row = ix.row()
            lock_it = self.table.item(row, 6)
            if lock_it and lock_it.checkState() == Qt.Checked:
                continue
            it = self.table.item(row, note_col)
            if it is None:
                continue
            it.setText("")
            done = True
        if done:
            self._mark_dirty(True)
        return done

    def refresh(self) -> None:
        try:
            self.repo.repair_internship_assessments()
        except Exception:
            pass
        templates = self.repo.list_templates(academic_year=self.default_academic_year or None)
        self.template_ids = [t["id"] for t in templates]
        self.template_combo.clear()
        for t in templates:
            lv, tr = (t.get("level") or "").strip(), (t.get("track") or "").strip()
            suffix = f" — {lv} {tr}" if lv or tr else ""
            self.template_combo.addItem(f"{t['name']} [{t['academic_year']}]{suffix}", t["id"])
        self._template_changed()

    def _template_changed(self) -> None:
        template_id = self.template_combo.currentData()
        prev_student_id = self.student_combo.currentData()
        prev_course_id = self.course_combo.currentData()

        self.student_combo.blockSignals(True)
        self.course_combo.blockSignals(True)
        self.student_combo.clear()
        self.course_combo.clear()
        self.student_ids = []
        self.course_ids = []
        if template_id is None:
            self.student_combo.blockSignals(False)
            self.course_combo.blockSignals(False)
            fill_table(self.table, [], [])
            return
        students = self.repo.list_students_for_template(int(template_id))
        self.student_ids = [s["id"] for s in students]
        from ..services.lookups import student_combo_label

        for s in students:
            self.student_combo.addItem(student_combo_label(s), s["id"])

        if prev_student_id is not None:
            idx = self.student_combo.findData(prev_student_id)
            if idx >= 0:
                self.student_combo.setCurrentIndex(idx)

        self.student_combo.blockSignals(False)
        self._refresh_course_combo(int(template_id), prev_course_id, prev_student_id)
        self.refresh_assessment_table()

    def _refresh_course_combo(
        self, template_id: int, prev_course_id: Any, prev_student_id: Any
    ) -> None:
        student_id = self.student_combo.currentData() or prev_student_id
        if student_id is not None:
            courses = self.repo.list_template_courses_for_student(
                int(student_id), int(template_id)
            )
        else:
            courses = self.repo.list_template_courses(int(template_id))
        tpl = next(
            (t for t in self.repo.list_templates() if int(t["id"]) == int(template_id)),
            None,
        ) or {}
        tpl_ay = str(tpl.get("academic_year") or self.default_academic_year or "").strip()
        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        self.course_ids = [c["course_id"] for c in courses]
        for c in courses:
            pub = course_public_code(c, academic_year=tpl_ay)
            self.course_combo.addItem(f"{pub} - {c['name']}", c["course_id"])
        if prev_course_id is not None:
            cidx = self.course_combo.findData(prev_course_id)
            if cidx >= 0:
                self.course_combo.setCurrentIndex(cidx)
        self.course_combo.blockSignals(False)

    def manage_enrollments(self) -> None:
        template_id = self.template_combo.currentData()
        if template_id is None:
            QMessageBox.warning(self, "Avertissement", "Sélectionnez d'abord une maquette.")
            return
        dlg = EnrollmentDialog(self.repo, template_id=int(template_id), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.repo.set_enrollments_for_template(int(template_id), dlg.selected_student_ids())
            self._template_changed()
            for cb in self.refresh_callbacks:
                cb()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _on_ects_validated_toggled(self, checked: bool) -> None:
        student_id = self.student_combo.currentData()
        course_id = self.course_combo.currentData()
        template_id = self.template_combo.currentData()
        if student_id is None or course_id is None or template_id is None:
            return
        try:
            self.repo.set_ue_ects_validation(
                int(student_id),
                int(template_id),
                int(course_id),
                validated=checked,
            )
            for cb in self.refresh_callbacks:
                cb()
            self._mark_dirty(False)
        except Exception as exc:
            QMessageBox.critical(self, "Validation UE", str(exc))
            self.refresh_assessment_table()

    def refresh_assessment_table(self) -> None:
        student_id = self.student_combo.currentData()
        course_id = self.course_combo.currentData()
        template_id = self.template_combo.currentData()
        if student_id is None or course_id is None:
            self.ue_validated_row.hide()
            fill_table(self.table, [], [])
            return
        self._loading_table = True
        self.ue_validated_row.setVisible(template_id is not None)
        if template_id is not None:
            validated = self.repo.has_ue_ects_validation(
                int(student_id), int(template_id), int(course_id)
            )
            self.ects_validated_cb.blockSignals(True)
            self.ects_validated_cb.setChecked(validated)
            self.ects_validated_cb.blockSignals(False)
            self.table.setEnabled(not validated)
        if template_id is not None:
            if not self.repo.is_sent_to_second_session(
                int(student_id), int(template_id), int(course_id)
            ):
                self.repo.purge_carried_s2_reprises_without_send(
                    int(student_id), int(course_id), template_id=int(template_id)
                )
        if self.repo.is_internship_course(int(course_id)):
            try:
                from ..services.internship_grades import ensure_internship_mcc_and_assessments

                ensure_internship_mcc_and_assessments(self.repo, int(course_id))
            except Exception:
                pass
        rows = self.repo.get_grades_for_student_course(int(student_id), int(course_id))
        headers = ["ID éval.", "Nom", "Type", "Coef", "Session", "Note", "Garder", "Commentaire"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(row["assessment_id"])))
            self.table.setItem(i, 1, QTableWidgetItem(row["name"]))
            self.table.setItem(i, 2, QTableWidgetItem(row["kind"]))
            self.table.setItem(i, 3, QTableWidgetItem(str(row["coefficient"])))
            self.table.setItem(i, 4, QTableWidgetItem(str(row["session"])))
            self.table.setItem(
                i,
                5,
                QTableWidgetItem(
                    format_grade_display(
                        row["grade"],
                        row.get("status"),
                        assessment_session=int(row.get("session") or 1),
                        assessment_kind=str(row.get("kind") or ""),
                        assessment_name=str(row.get("name") or ""),
                    )
                ),
            )
            lock_it = QTableWidgetItem("")
            lock_it.setFlags(lock_it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            locked = int(row.get("locked") or 0)
            lock_it.setCheckState(Qt.Checked if locked else Qt.Unchecked)
            self.table.setItem(i, 6, lock_it)
            self.table.setItem(i, 7, QTableWidgetItem(str(row.get("comment") or "")))
        self.table.resizeColumnsToContents()
        validated = (
            template_id is not None
            and self.repo.has_ue_ects_validation(
                int(student_id), int(template_id), int(course_id)
            )
        )
        if validated:
            msg = "UE validée sans note (VAL) — pas de moyenne chiffrée."
        else:
            avg = self.repo.compute_course_average(int(student_id), int(course_id))
            msg = "Moyenne UE : —" if avg is None else f"Moyenne UE : {avg:.3f}/20"
        is_stage = self.repo.is_internship_course(int(course_id))
        self.internship_action.setVisible(is_stage)
        if is_stage:
            msg += (
                "  |  Stage : encadrant 50 %, rapport 25 %, soutenance 25 % "
                "(ou cochez « UE validée sans note »)."
            )
        if is_stage and template_id is not None:
            rec = self.repo.get_internship_record(
                int(student_id), int(template_id), int(course_id)
            )
            if rec:
                st_key = str(rec.get("follow_up_status") or "")
                st_lab = next(
                    (l for k, l in INTERNSHIP_STATUS_CHOICES if k == st_key),
                    st_key or "—",
                )
                msg += f"  |  Stage : {st_lab}"
        self.info_label.setText(msg)
        self._mark_dirty(False)
        self._loading_table = False

    def save_grades(self) -> None:
        student_id = self.student_combo.currentData()
        template_id = self.template_combo.currentData()
        course_id = self.course_combo.currentData()
        if student_id is None:
            QMessageBox.warning(self, "Avertissement", "Sélectionnez un étudiant.")
            return
        if (
            template_id is not None
            and course_id is not None
            and self.repo.has_ue_ects_validation(
                int(student_id), int(template_id), int(course_id)
            )
        ):
            QMessageBox.information(
                self,
                "Validation UE",
                "Cette UE est validée sans note : décochez la case pour modifier les épreuves.",
            )
            return
        try:
            for row in range(self.table.rowCount()):
                assessment_id = int(self.table.item(row, 0).text())
                text = self.table.item(row, 5).text().strip() if self.table.item(row, 5) else ""
                locked = 1 if (self.table.item(row, 6) and self.table.item(row, 6).checkState() == Qt.Checked) else 0
                comment = self.table.item(row, 7).text().strip() if self.table.item(row, 7) else ""
                grade, status, err = parse_grade_cell(text)
                if err:
                    QMessageBox.warning(self, "Grade", err)
                    return
                self.repo.upsert_grade(
                    int(student_id),
                    assessment_id,
                    grade,
                    status=status,
                    locked=locked,
                    comment=comment,
                )
            self.refresh_assessment_table()
            for cb in self.refresh_callbacks:
                cb()
            self._mark_dirty(False)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def open_internship_dossier(self) -> None:
        template_id = self.template_combo.currentData()
        student_id = self.student_combo.currentData()
        course_id = self.course_combo.currentData()
        if template_id is None or student_id is None or course_id is None:
            QMessageBox.information(
                self, "Stage", "Sélectionnez une maquette, un étudiant et l’UE de stage."
            )
            return
        if not self.repo.is_internship_course(int(course_id)):
            QMessageBox.information(
                self,
                "Stage",
                "Cette UE n’est pas marquée comme stage. Cochez « UE de type stage » dans la fiche cours.",
            )
            return
        dlg = InternshipDialog(
            self.repo,
            student_id=int(student_id),
            template_id=int(template_id),
            course_id=int(course_id),
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh_assessment_table()

    def open_course_entry(self) -> None:
        template_id = self.template_combo.currentData()
        course_id = self.course_combo.currentData()
        if template_id is None or course_id is None:
            QMessageBox.information(self, "Saisie par matière", "Sélectionnez une maquette et une UE d'abord.")
            return
        dlg = CourseGradesDialog(self.repo, template_id=int(template_id), course_id=int(course_id), parent=self)
        dlg.exec()
