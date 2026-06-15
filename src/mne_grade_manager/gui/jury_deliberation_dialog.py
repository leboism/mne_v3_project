"""Fenêtre interactive de délibération : notes, points jury, S2, moyennes en direct."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.mne_modules import course_ue_code
from ..services.jury_reports import (
    JURY_OUTCOME_LABELS,
    TRANSCRIPT_MENTION_LABELS,
    transcript_mention_code_from_grade,
)
from ..services.grade_status import format_grade_display, parse_grade_cell
from ..services.lookups import student_combo_label


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{float(v):.3f}"


class JuryDeliberationDialog(QDialog):
    def __init__(
        self,
        repo,
        template_id: int,
        *,
        view_session: str = "s1",
        jury_session_id: int | None = None,
        session_kind: str = "S1",
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.template_id = int(template_id)
        self.session_kind = str(session_kind or "S1").upper()
        if self.session_kind == "FINAL":
            self.view_session = "s2"
        else:
            self.view_session = str(view_session or "s1").lower()
        self.jury_session_id = int(jury_session_id) if jury_session_id is not None else None
        self._loading = False
        self._student_ids: list[int] = []
        self._block_spinboxes: dict[str, QDoubleSpinBox] = {}
        self._course_spinboxes: dict[int, QDoubleSpinBox] = {}
        self._s2_checks: dict[int, QCheckBox] = {}
        self._floor_waiver_checks: dict[int, QCheckBox] = {}
        self._grade_items: dict[int, QTableWidgetItem] = {}
        self._grade_item_meta: dict[int, tuple[int, int]] = {}
        self._total_items: dict[int, QTableWidgetItem] = {}
        self._grade_tables: list[QTableWidget] = []
        self._year_spin: QDoubleSpinBox | None = None
        self._outcome_combo: QComboBox | None = None
        self._mention_combo: QComboBox | None = None
        self._validation_label: QLabel | None = None

        tpl = next((t for t in repo.list_templates() if int(t["id"]) == self.template_id), None) or {}
        title = f"Délibération — {tpl.get('name', '')}"
        self.setWindowTitle(title)
        self.setMinimumSize(1020, 640)

        root = QVBoxLayout(self)
        hint = QLabel(self._deliberation_hint_text())
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Étudiants"))
        self.student_list = QListWidget()
        self.student_list.currentRowChanged.connect(self._on_student_changed)
        ll.addWidget(self.student_list, 1)
        split.addWidget(left)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        self.detail_host = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_host)
        right_scroll.setWidget(self.detail_host)
        split.addWidget(right_scroll)
        split.setSizes([240, 780])
        root.addWidget(split, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._load_students()

    def _allow_s2_decision(self) -> bool:
        """Envoi S2 : jury S1 uniquement (pas en vue S2 ni au jury final)."""
        return self.session_kind != "FINAL" and self.view_session == "s1"

    def _allow_s2_decision_for_ue(self, student_id: int, course_id: int) -> bool:
        """Pas d’envoi S2 si l’UE a déjà des notes de session 2."""
        if not self._allow_s2_decision():
            return False
        return self.repo.can_send_to_second_session(
            int(student_id), self.template_id, int(course_id)
        )

    def _deliberation_hint_text(self) -> str:
        parts = [
            "Parcourez les étudiants, modifiez les <b>notes par épreuve</b> (nombre, ABJ, DEF, NEUT, VAL) "
            "et les points de délibération (UE, bloc, année). "
        ]
        if self._allow_s2_decision():
            parts.append(
                "Cochez les envois en 2ᵉ session (auto si DEF ou ABJ en S1) — "
                "impossible si des notes S2 existent déjà sur l’UE. "
            )
        elif self.session_kind == "FINAL":
            parts.append(
                "Jury final : notes de 2ᵉ session retenues lorsqu’elles existent ; "
                "aucun nouvel envoi en 2ᵉ session. "
            )
        else:
            parts.append(
                "Vue session 2 : les notes S2 sont affichées ; l’envoi en 2ᵉ session n’est plus modifiable. "
            )
        parts.append(
            "« Valider (seuil 7) » : dérogation jury pour une UE avec note d'épreuve < 7. "
        )
        if self.session_kind == "FINAL":
            parts.append(
                "Propositions de décision et mention (≥ 12 Assez bien … ≥ 18 Excellent) — le jury enregistre."
            )
        return "".join(parts)

    def _load_students(self) -> None:
        self.student_list.blockSignals(True)
        self.student_list.clear()
        self._student_ids = []
        for s in self.repo.list_students_for_template(self.template_id):
            sid = int(s["id"])
            self._student_ids.append(sid)
            it = QListWidgetItem(student_combo_label(s))
            it.setData(Qt.ItemDataRole.UserRole, sid)
            self.student_list.addItem(it)
        self.student_list.blockSignals(False)
        if self.student_list.count():
            self.student_list.setCurrentRow(0)
        else:
            self._clear_detail()

    def _current_student_id(self) -> int | None:
        row = self.student_list.currentRow()
        if row < 0 or row >= len(self._student_ids):
            return None
        return self._student_ids[row]

    def _clear_detail(self) -> None:
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._block_spinboxes.clear()
        self._course_spinboxes.clear()
        self._s2_checks.clear()
        self._floor_waiver_checks.clear()
        self._grade_items.clear()
        self._grade_item_meta.clear()
        self._total_items.clear()
        self._grade_tables.clear()
        self._year_spin = None
        self._outcome_combo = None
        self._mention_combo = None
        self._validation_label = None

    def _on_student_changed(self, _row: int) -> None:
        self._clear_detail()
        sid = self._current_student_id()
        if sid is None:
            return
        self._build_detail(sid)

    def _jury_points(self, sid: int, scope: str, *, course_id: int | None = None, block_name: str = "") -> float:
        for row in self.repo.list_jury_adjustments_for_export(self.template_id):
            if int(row["student_id"]) != sid:
                continue
            if str(row.get("scope") or "").lower() != scope:
                continue
            if scope == "course" and int(row.get("course_id") or 0) != int(course_id or 0):
                continue
            if scope == "block" and str(row.get("block_name") or "").strip() != str(block_name or "").strip():
                continue
            return float(row.get("points") or 0)
        return 0.0

    def _make_spin(self, value: float, on_change) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(-5.0, 5.0)
        sp.setDecimals(3)
        sp.setSingleStep(0.05)
        sp.setValue(float(value))
        sp.valueChanged.connect(on_change)
        return sp

    def _build_detail(self, student_id: int) -> None:
        sid = int(student_id)
        stu = self.repo.get_student(sid) or {}
        heading = QLabel(f"<b>{stu.get('last_name', '')} {stu.get('first_name', '')}</b>")
        self.detail_layout.addWidget(heading)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-size: 12px;")
        self.detail_layout.addWidget(self.summary_label)

        self._validation_label = QLabel("")
        self._validation_label.setWordWrap(True)
        self._validation_label.setStyleSheet("font-size: 11px; padding: 4px; border-radius: 4px;")
        self.detail_layout.addWidget(self._validation_label)

        blocks = self.repo.list_template_blocks_with_courses(self.template_id)
        for bk, clist in blocks:
            box = QGroupBox(str(bk or "Bloc"))
            bl = QVBoxLayout(box)
            table = QTableWidget()
            table.setColumnCount(7)
            table.setHorizontalHeaderLabels(
                [
                    "UE",
                    "Épreuve",
                    "Note",
                    "Pts jury",
                    "Moy.+jury",
                    "S2",
                    "Valider\n(seuil 7)",
                ]
            )
            table.itemChanged.connect(self._on_grade_item_changed)
            self._grade_tables.append(table)

            row_idx = 0
            for c in clist:
                if int(c.get("optional") or 0) or int(c.get("free_ue") or 0):
                    continue
                cid = int(c["course_id"])
                if self.repo.has_ue_ects_validation(sid, self.template_id, cid):
                    continue
                sent_s2 = self.repo.is_sent_to_second_session(sid, self.template_id, cid)
                use_s2 = self.repo.course_uses_session2_grades(
                    sid, self.template_id, cid, view_session=self.view_session
                )
                if use_s2:
                    self.repo.carry_over_reprise_grades_from_session1(sid, cid)
                grade_rows = self.repo.get_grades_for_student_course(sid, cid)
                sess = 2 if use_s2 else 1
                assessments = [r for r in grade_rows if int(r.get("session") or 1) == sess]
                if not assessments:
                    assessments = [r for r in grade_rows if int(r.get("session") or 1) == 1]
                if not assessments:
                    continue

                first_row = row_idx
                n = len(assessments)
                table.setRowCount(row_idx + n)
                code = course_ue_code(c) or str(c.get("code") or "")
                name = str(c.get("name") or "")
                ue_lbl = QTableWidgetItem(f"{code}\n{name[:40]}")
                ue_lbl.setFlags(ue_lbl.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(first_row, 0, ue_lbl)

                for j, ar in enumerate(assessments):
                    r = row_idx + j
                    aid = int(ar["assessment_id"])
                    ep = QTableWidgetItem(
                        f"{ar.get('kind', '')} — {str(ar.get('name') or '')[:36]}"
                    )
                    ep.setFlags(ep.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(r, 1, ep)

                    note_it = QTableWidgetItem(
                        format_grade_display(
                            ar.get("grade"),
                            ar.get("status"),
                            assessment_session=int(ar.get("session") or 1),
                        )
                    )
                    note_it.setData(Qt.ItemDataRole.UserRole, aid)
                    note_it.setData(Qt.ItemDataRole.UserRole + 1, cid)
                    locked = int(ar.get("locked") or 0)
                    if locked:
                        note_it.setFlags(note_it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    else:
                        note_it.setFlags(
                            note_it.flags()
                            | Qt.ItemFlag.ItemIsEditable
                            | Qt.ItemFlag.ItemIsEnabled
                        )
                    table.setItem(r, 2, note_it)
                    self._grade_items[aid] = note_it
                    self._grade_item_meta[aid] = (sid, cid)

                jp = self._jury_points(sid, "course", course_id=cid)

                def _ue_change(_v: float, _sid: int = sid, _cid: int = cid) -> None:
                    self._save_course_jury(_sid, _cid)

                sp = self._make_spin(jp, _ue_change)
                self._course_spinboxes[cid] = sp
                table.setCellWidget(first_row, 3, sp)

                tot_it = QTableWidgetItem("—")
                tot_it.setFlags(tot_it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(first_row, 4, tot_it)
                self._total_items[cid] = tot_it

                if self._allow_s2_decision_for_ue(sid, cid):
                    chk = QCheckBox()
                    chk.setChecked(sent_s2)
                    chk.toggled.connect(
                        lambda checked, _sid=sid, _cid=cid: self._save_s2(_sid, _cid, checked)
                    )
                    self._s2_checks[cid] = chk
                    table.setCellWidget(first_row, 5, chk)
                elif (
                    use_s2
                    or sent_s2
                    or self.repo.second_session_decision_locked(sid, self.template_id, cid)
                ):
                    s2_lbl = QLabel("S2")
                    s2_lbl.setToolTip(
                        "UE en session 2 (notes S2 présentes) — envoi en 2ᵉ session non modifiable."
                    )
                    s2_lbl.setStyleSheet("color: palette(mid);")
                    table.setCellWidget(first_row, 5, s2_lbl)

                wchk = QCheckBox()
                wchk.setToolTip(
                    "Dérogation jury : valider l'UE malgré une note d'épreuve < 7 "
                    "(DEF et ABJ restent bloquants)."
                )
                wchk.setChecked(self.repo.has_ue_jury_floor_waiver(sid, self.template_id, cid))
                wchk.toggled.connect(
                    lambda checked, _sid=sid, _cid=cid: self._save_floor_waiver(_sid, _cid, checked)
                )
                self._floor_waiver_checks[cid] = wchk
                table.setCellWidget(first_row, 6, wchk)

                if n > 1:
                    table.setSpan(first_row, 0, n, 1)
                    table.setSpan(first_row, 3, n, 1)
                    table.setSpan(first_row, 4, n, 1)
                    table.setSpan(first_row, 5, n, 1)
                    table.setSpan(first_row, 6, n, 1)

                row_idx += n

            if row_idx == 0:
                table.setRowCount(0)
            if not self._allow_s2_decision() and not any(
                self.repo.course_has_session2_activity(sid, int(c["course_id"]))
                for c in clist
                if not int(c.get("optional") or 0) and not int(c.get("free_ue") or 0)
            ):
                table.setColumnHidden(5, True)
            bl.addWidget(table)

            blk_row = QHBoxLayout()
            blk_row.addWidget(QLabel("Points jury bloc :"))
            bjp = self._jury_points(sid, "block", block_name=bk)

            def _blk_change(_v: float, _sid: int = sid, _bk: str = bk) -> None:
                self._save_block_jury(_sid, _bk)

            bsp = self._make_spin(bjp, _blk_change)
            self._block_spinboxes[bk] = bsp
            blk_row.addWidget(bsp)
            blk_row.addStretch()
            bl.addLayout(blk_row)
            self.detail_layout.addWidget(box)

        year_box = QGroupBox("Année")
        yl = QFormLayout(year_box)
        yjp = self._jury_points(sid, "year")
        self._year_spin = self._make_spin(yjp, lambda _v: self._save_year_jury(sid))
        yl.addRow("Points jury année :", self._year_spin)

        if self.session_kind == "FINAL":
            self._outcome_combo = QComboBox()
            self._outcome_combo.addItem("—", "")
            for key, lab in JURY_OUTCOME_LABELS.items():
                self._outcome_combo.addItem(lab, key)
            oc = self.repo.get_jury_student_outcome(
                sid, self.template_id, jury_session_id=self.jury_session_id
            )
            saved_outcome = str((oc or {}).get("outcome") or "").strip()
            if saved_outcome:
                idx = self._outcome_combo.findData(saved_outcome)
                if idx >= 0:
                    self._outcome_combo.setCurrentIndex(idx)
            self._outcome_combo.currentIndexChanged.connect(lambda: self._save_outcome(sid))
            yl.addRow("Décision jury :", self._outcome_combo)

            self._mention_combo = QComboBox()
            for key, lab in TRANSCRIPT_MENTION_LABELS.items():
                self._mention_combo.addItem(lab, key)
            saved_mention = str((oc or {}).get("mention") or "").strip()
            if saved_mention:
                midx = self._mention_combo.findData(saved_mention)
                if midx >= 0:
                    self._mention_combo.setCurrentIndex(midx)
            self._mention_combo.currentIndexChanged.connect(lambda: self._save_mention(sid))
            yl.addRow("Mention (transcript) :", self._mention_combo)

        self.detail_layout.addWidget(year_box)
        self.detail_layout.addStretch()
        self._refresh_summary(sid)

    def _on_grade_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        if item.column() != 2:
            return
        aid = item.data(Qt.ItemDataRole.UserRole)
        if aid is None:
            return
        try:
            assessment_id = int(aid)
        except (TypeError, ValueError):
            return
        meta = self._grade_item_meta.get(assessment_id)
        if not meta:
            return
        sid, _cid = meta
        text = item.text().strip()
        grade, status, err = parse_grade_cell(text)
        if err:
            QMessageBox.warning(self, "Note", err)
            self._refresh_summary(int(sid))
            return
        rows = self.repo.get_grades_for_student_course(int(sid), int(_cid))
        row = next((r for r in rows if int(r["assessment_id"]) == assessment_id), None)
        locked = int((row or {}).get("locked") or 0)
        comment = str((row or {}).get("comment") or "")
        try:
            self.repo.upsert_grade(
                int(sid),
                assessment_id,
                grade,
                status=status,
                locked=locked,
                comment=comment,
            )
        except Exception:
            return
        assess_session = int((row or {}).get("session") or 1)
        if assess_session == 2 or self.repo.course_has_session2_activity(int(sid), int(_cid)):
            self._rebuild_student_detail(int(sid))
        else:
            self._refresh_summary(int(sid))

    def _save_course_jury(self, student_id: int, course_id: int) -> None:
        if self._loading:
            return
        sp = self._course_spinboxes.get(int(course_id))
        if sp is None:
            return
        self.repo.upsert_jury_adjustment(
            int(student_id),
            self.template_id,
            "course",
            course_id=int(course_id),
            points=float(sp.value()),
        )
        self._refresh_summary(int(student_id))

    def _save_block_jury(self, student_id: int, block_name: str) -> None:
        if self._loading:
            return
        sp = self._block_spinboxes.get(block_name)
        if sp is None:
            return
        self.repo.upsert_jury_adjustment(
            int(student_id),
            self.template_id,
            "block",
            block_name=block_name,
            points=float(sp.value()),
        )
        self._refresh_summary(int(student_id))

    def _save_year_jury(self, student_id: int) -> None:
        if self._loading or self._year_spin is None:
            return
        self.repo.upsert_jury_adjustment(
            int(student_id),
            self.template_id,
            "year",
            points=float(self._year_spin.value()),
        )
        self._refresh_summary(int(student_id))

    def _save_s2(self, student_id: int, course_id: int, sent: bool) -> None:
        if not self._allow_s2_decision_for_ue(int(student_id), int(course_id)):
            return
        try:
            self.repo.set_second_session_decision(
                int(student_id), self.template_id, int(course_id), sent=bool(sent)
            )
        except ValueError as exc:
            QMessageBox.warning(self, "2ᵉ session", str(exc))
            self._rebuild_student_detail(int(student_id))
            return
        self._rebuild_student_detail(int(student_id))

    def _rebuild_student_detail(self, student_id: int) -> None:
        row = self.student_list.currentRow()
        self._clear_detail()
        self._build_detail(int(student_id))
        if 0 <= row < self.student_list.count():
            self.student_list.blockSignals(True)
            self.student_list.setCurrentRow(row)
            self.student_list.blockSignals(False)

    def _save_floor_waiver(self, student_id: int, course_id: int, waived: bool) -> None:
        self.repo.set_ue_jury_floor_waiver(
            int(student_id),
            self.template_id,
            int(course_id),
            waived=bool(waived),
        )
        self._refresh_summary(int(student_id))

    def _save_outcome(self, student_id: int) -> None:
        if self._outcome_combo is None:
            return
        mention = None
        if self._mention_combo is not None:
            mention = str(self._mention_combo.currentData() or "")
        self.repo.upsert_jury_student_outcome(
            int(student_id),
            self.template_id,
            jury_session_id=self.jury_session_id,
            outcome=str(self._outcome_combo.currentData() or ""),
            mention=mention,
        )

    def _save_mention(self, student_id: int) -> None:
        if self._mention_combo is None:
            return
        outcome = None
        if self._outcome_combo is not None:
            outcome = str(self._outcome_combo.currentData() or "")
        self.repo.upsert_jury_student_outcome(
            int(student_id),
            self.template_id,
            jury_session_id=self.jury_session_id,
            outcome=outcome,
            mention=str(self._mention_combo.currentData() or ""),
        )

    def _apply_suggested_outcome(
        self, student_id: int, row: dict[str, Any], evaluation: dict[str, Any] | None = None
    ) -> None:
        if self._outcome_combo is None or self.session_kind != "FINAL":
            return
        oc = self.repo.get_jury_student_outcome(
            int(student_id), self.template_id, jury_session_id=self.jury_session_id
        )
        if oc and str(oc.get("outcome") or "").strip():
            return
        if evaluation is None:
            evaluation = self.repo.evaluate_student_year_validation(
                int(student_id),
                self.template_id,
                view_session=self.view_session,
                result_row=row,
                auto_sync_s2=self._allow_s2_decision(),
            )
        suggested = str(evaluation.get("suggested_outcome") or "repeat")
        idx = self._outcome_combo.findData(suggested)
        if idx < 0:
            return
        self._outcome_combo.blockSignals(True)
        self._outcome_combo.setCurrentIndex(idx)
        self._outcome_combo.blockSignals(False)

    def _refresh_validation_banner(self, student_id: int, row: dict[str, Any]) -> None:
        if self._validation_label is None:
            return
        ev = self.repo.evaluate_student_year_validation(
            int(student_id),
            self.template_id,
            view_session=self.view_session,
            result_row=row,
            auto_sync_s2=self._allow_s2_decision(),
        )
        if ev.get("validated"):
            self._validation_label.setText(
                "<span style='color:#1b5e20;'>✓ Année validée selon les règles — "
                f"proposition : {JURY_OUTCOME_LABELS.get(ev.get('suggested_outcome', ''), '')} "
                "(le jury confirme).</span>"
            )
            self._validation_label.setStyleSheet(
                "font-size: 11px; padding: 6px; border-radius: 4px; background: #e8f5e9;"
            )
        else:
            issues = ev.get("issues") or []
            proposed = ev.get("proposed_outcomes") or ["repeat", "refuse_repeat"]
            prop_labels = " · ".join(
                JURY_OUTCOME_LABELS.get(str(k), str(k)) for k in proposed
            )
            sug_key = str(ev.get("suggested_outcome") or "repeat")
            sug = JURY_OUTCOME_LABELS.get(sug_key, sug_key)
            prior = int(ev.get("prior_same_level_years") or 0)
            hint_prior = ""
            if prior >= 1 and sug_key == "refuse_repeat":
                ay_word = "années" if prior != 1 else "année"
                hint_prior = (
                    f"<br/><i>Déjà {prior} {ay_word} antérieure{'s' if prior != 1 else ''} "
                    f"au même niveau — présélection « {sug} ».</i>"
                )
            body = "<br/>".join(f"• {issue}" for issue in issues[:8])
            if len(issues) > 8:
                body += f"<br/>• … ({len(issues) - 8} autre(s))"
            self._validation_label.setText(
                f"<span style='color:#b71c1c;'><b>Non validé</b></span><br/>"
                f"<b>Propositions pour le jury :</b> {prop_labels}<br/>"
                f"<b>Présélection :</b> {sug} (modifiable ci-dessous, non enregistrée tant que vous "
                f"ne changez pas la liste).{hint_prior}<br/>{body}"
            )
            self._validation_label.setStyleSheet(
                "font-size: 11px; padding: 6px; border-radius: 4px; background: #ffebee;"
            )
        self._apply_suggested_outcome(int(student_id), row, ev)
        self._apply_suggested_mention(int(student_id), row)

    def _apply_suggested_mention(self, student_id: int, row: dict[str, Any]) -> None:
        if self._mention_combo is None or self.session_kind != "FINAL":
            return
        oc = self.repo.get_jury_student_outcome(
            int(student_id), self.template_id, jury_session_id=self.jury_session_id
        )
        if oc and str(oc.get("mention") or "").strip():
            return
        gwj = row.get("global_with_jury")
        if gwj is None:
            return
        code = transcript_mention_code_from_grade(float(gwj))
        idx = self._mention_combo.findData(code)
        if idx < 0:
            return
        self._mention_combo.blockSignals(True)
        self._mention_combo.setCurrentIndex(idx)
        self._mention_combo.blockSignals(False)

    def _refresh_summary(self, student_id: int) -> None:
        self._loading = True
        try:
            for tbl in self._grade_tables:
                tbl.blockSignals(True)
            data = self.repo.get_student_result_summary(
                self.template_id,
                view_session=self.view_session,
                auto_sync_s2=self._allow_s2_decision(),
            )
            row = next(
                (r for r in data if int(r.get("student_id") or 0) == int(student_id)), None
            )
            if row is None:
                self.summary_label.setText("Aucune donnée.")
                if self._validation_label is not None:
                    self._validation_label.clear()
                return

            vs = self.view_session
            lines = [
                f"<b>Moyenne année :</b> {_fmt(row.get('global_average'))}  "
                f"| <b>avec jury :</b> {_fmt(row.get('global_with_jury'))}"
            ]
            for bk, avg in (row.get("blocks") or {}).items():
                ok = self.repo.block_is_validated(
                    int(student_id),
                    self.template_id,
                    str(bk),
                    view_session=vs,
                    block_average=avg,
                )
                mark = "✓" if ok else "✗"
                lines.append(f"• {bk} : {_fmt(avg)} {mark}")

            editing_items = {
                tbl.currentItem()
                for tbl in self._grade_tables
                if tbl.state() == QAbstractItemView.State.EditingState
            }
            grade_rows_cache: dict[int, list[dict[str, Any]]] = {}
            for aid, note_it in self._grade_items.items():
                if note_it in editing_items:
                    continue
                meta = self._grade_item_meta.get(int(aid))
                if not meta:
                    continue
                sid, cid = meta
                if int(sid) != int(student_id):
                    continue
                if cid not in grade_rows_cache:
                    grade_rows_cache[cid] = self.repo.get_grades_for_student_course(
                        int(sid), int(cid)
                    )
                ar = next(
                    (r for r in grade_rows_cache[cid] if int(r["assessment_id"]) == int(aid)),
                    None,
                )
                if ar is None:
                    continue
                note_it.setText(
                    format_grade_display(
                        ar.get("grade"),
                        ar.get("status"),
                        assessment_session=int(ar.get("session") or 1),
                    )
                )

            for cid, sp in self._course_spinboxes.items():
                d = (row.get("ue_detail") or {}).get(int(cid)) or {}
                display = str(d.get("display") or "").strip()
                if vs == "s2":
                    base = d.get("s2") if d.get("use_s2") else d.get("s1")
                else:
                    base = d.get("s1")
                jp = float(sp.value())
                total = (
                    (float(base) + jp)
                    if base is not None and not display
                    else (jp if abs(jp) > 1e-12 else None)
                )
                tot_txt = display if display else _fmt(total)
                if self.repo.has_ue_jury_floor_waiver(
                    int(student_id), self.template_id, int(cid)
                ):
                    if not display:
                        tot_txt = f"{tot_txt} (seuil 7)"
                if cid in self._total_items:
                    self._total_items[cid].setText(tot_txt)

            self.summary_label.setText("<br/>".join(lines))
            self._refresh_validation_banner(int(student_id), row)
        finally:
            for tbl in self._grade_tables:
                tbl.blockSignals(False)
            self._loading = False
