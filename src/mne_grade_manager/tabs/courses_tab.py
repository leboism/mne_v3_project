from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.mne_modules import course_ue_code, is_legacy_semester_ue_code
from ..services.timetable_legacy import course_public_code
from ..gui.dialogs import AssessmentDialog, CourseDialog
from ..gui.exam_convocation_dialog import ExamConvocationDialog
from ..gui.internship_defense_planning_dialog import InternshipDefensePlanningDialog
from ..gui.widgets import fill_table, make_actions_toolbar
from ..services.course_tree import branch_sort_key, course_tree_branch
from ..services.mcc_parser import parse_mcc_text_to_assessments_dicts


def _course_fields_from_dialog(dlg: CourseDialog) -> dict:
    return dlg.fields_dict()


def _course_list_label(c: dict, *, academic_year: str = "") -> str:
    h = float(c.get("hours_total") or 0)
    ects = float(c.get("ects") or 0)
    extra = []
    if c.get("code_ip_paris"):
        extra.append(f"IP:{c['code_ip_paris']}")
    other = str(c.get("code_other") or "").strip()
    if other and not is_legacy_semester_ue_code(other):
        extra.append(f"+:{other}")
    tail = f" | {' '.join(extra)}" if extra else ""
    mne = course_ue_code(c)
    pub = course_public_code(c, academic_year=academic_year)
    head = f"{pub} — {c['name']}" if pub else f"{c['code']} — {c['name']}"
    if mne and pub != mne:
        head += f"  [{mne}]"
    elif mne and c.get("code") and str(c.get("code")) != pub:
        head += f"  [Apogée {c['code']}]"
    return f"{head}  |  {ects:g} ECTS  |  {h:g} h{tail}"


def _course_tooltip(c: dict, label: str) -> str:
    tip_parts = [label]
    if c.get("semester"):
        tip_parts.append(f"Semestre : {c['semester']}")
    mcc = (c.get("mcc_text") or "").strip()
    if mcc:
        tip_parts.append(mcc[:800] + ("…" if len(mcc) > 800 else ""))
    syl = str(c.get("syllabus_filename") or "").strip()
    if not syl and str(c.get("syllabus_path") or "").strip():
        syl = str(c.get("syllabus_path") or "").split("/")[-1]
    if syl:
        tip_parts.append(f"Syllabus : {syl}")
    return "\n\n".join(tip_parts)


class CoursesTab(QWidget):
    def __init__(self, repo, refresh_callbacks=None, default_academic_year: str = ""):
        super().__init__()
        self.repo = repo
        self.refresh_callbacks = refresh_callbacks or []
        self.default_academic_year = (default_academic_year or "").strip()
        layout = QVBoxLayout(self)
        self.intro_label = QLabel(
            "<b>Bibliothèque de cours (UE)</b> — fiches détaillées, heures, MCC, syllabus (PDF/Word), "
            "et assessments pour la saisie des notes. "
            "L'arborescence suit la nomenclature secrétariat (S1-C/P/X) ou MNE selon le millésime. "
            "Seules les UE des <b>maquettes du millésime ouvert</b> sont listées ici. "
            "Pour constituer un parcours (blocs, ordre, import Excel), utilisez l'onglet <b>Maquette</b>."
        )
        self.intro_label.setWordWrap(True)
        self.intro_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self.intro_label)
        layout.addLayout(
            make_actions_toolbar(
                self,
                primary=[
                    ("Ajouter", self.add_course),
                    ("Modifier…", self.edit_course),
                ],
                menu_sections=[
                    [("Supprimer le cours", self.delete_course)],
                    [
                        ("Ajouter une épreuve…", self.add_assessment),
                        ("Générer épreuves depuis MCC", self.generate_assessments_from_mcc),
                    ],
                    [
                        ("Convocation examen (e-mail)…", self.open_convocation),
                        ("Planning soutenances de stage…", self.open_defense_planning),
                    ],
                ],
            ).layout
        )
        splitter = QSplitter()
        self.course_tree = QTreeWidget()
        self.course_tree.setHeaderHidden(True)
        self.course_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.course_tree.setUniformRowHeights(True)
        self.course_tree.currentItemChanged.connect(self._on_course_selection_changed)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.assessments_hint = QLabel("")
        self.assessments_hint.setWordWrap(True)
        right_layout.addWidget(self.assessments_hint)
        self.assessments_table = QTableWidget()
        right_layout.addWidget(self.assessments_table)
        splitter.addWidget(self.course_tree)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        self.refresh()

    def _folder_item(self, parent: QTreeWidgetItem | None, text: str) -> QTreeWidgetItem:
        # Top-level folders must hang off the invisible root, not parent=None (orphan → GC).
        if parent is None:
            parent = self.course_tree.invisibleRootItem()
        it = QTreeWidgetItem(parent, [text])
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        it.setData(0, Qt.ItemDataRole.UserRole, None)
        return it

    def _leaf_item(self, parent: QTreeWidgetItem, course: dict) -> QTreeWidgetItem:
        label = _course_list_label(course, academic_year=self.default_academic_year)
        it = QTreeWidgetItem(parent, [label])
        it.setData(0, Qt.ItemDataRole.UserRole, int(course["id"]))
        it.setToolTip(0, _course_tooltip(course, label))
        return it

    def _collect_expanded_paths(self, item: QTreeWidgetItem, prefix: str = "") -> set[str]:
        paths: set[str] = set()
        text = item.text(0)
        path = f"{prefix}/{text}" if prefix else text
        if item.childCount() and item.isExpanded():
            paths.add(path)
            for i in range(item.childCount()):
                paths |= self._collect_expanded_paths(item.child(i), path)
        return paths

    def _expand_paths(self, item: QTreeWidgetItem, paths: set[str], prefix: str = "") -> None:
        text = item.text(0)
        path = f"{prefix}/{text}" if prefix else text
        if path in paths:
            item.setExpanded(True)
        for i in range(item.childCount()):
            self._expand_paths(item.child(i), paths, path)

    def _item_course_id(self, item: QTreeWidgetItem | None) -> int | None:
        if item is None:
            return None
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _current_course_id(self) -> int | None:
        return self._item_course_id(self.course_tree.currentItem())

    def _selected_course_ids(self) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        for item in self.course_tree.selectedItems():
            cid = self._item_course_id(item)
            if cid is not None and cid not in seen:
                seen.add(cid)
                ids.append(cid)
        return ids

    def _find_item_for_course(self, course_id: int) -> QTreeWidgetItem | None:
        root = self.course_tree.invisibleRootItem()
        stack = [root.child(i) for i in range(root.childCount())]
        while stack:
            item = stack.pop()
            cid = self._item_course_id(item)
            if cid == int(course_id):
                return item
            for i in range(item.childCount()):
                stack.append(item.child(i))
        return None

    def _on_course_selection_changed(
        self, _current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        self.refresh_assessments()

    def refresh(self) -> None:
        prev_id = self._current_course_id()
        expanded = self._collect_expanded_paths(self.course_tree.invisibleRootItem())

        if self.default_academic_year:
            courses = self.repo.list_courses_for_academic_year(self.default_academic_year)
            self.intro_label.setText(
                f"<b>Bibliothèque de cours (UE)</b> — millésime <b>{self.default_academic_year}</b> : "
                f"<b>{len(courses)}</b> UE issue(s) des maquettes de cette année. "
                "Fiches détaillées, MCC, syllabus, épreuves. "
                "Pour modifier la composition d'un parcours, utilisez l'onglet <b>Maquette</b>."
            )
        else:
            courses = self.repo.list_courses()
        visible_ids = {int(c["id"]) for c in courses}
        if prev_id is not None and prev_id not in visible_ids:
            prev_id = None

        self.course_tree.blockSignals(True)
        self.course_tree.clear()

        tree: dict[str, dict[str, dict[str, list[dict]]]] = {}
        level_labels: dict[str, str] = {}
        block_labels: dict[tuple[str, str], str] = {}
        track_labels: dict[tuple[str, str, str], str] = {}

        for c in courses:
            lk, ll, bk, bl, tk, tl = course_tree_branch(
                c, academic_year=self.default_academic_year
            )
            level_labels[lk] = ll
            block_labels[(lk, bk)] = bl
            track_labels[(lk, bk, tk)] = tl
            tree.setdefault(lk, {}).setdefault(bk, {}).setdefault(tk, []).append(c)

        level_keys = sorted(tree.keys(), key=lambda k: branch_sort_key(k, "_na", "_na"))

        first_leaf: QTreeWidgetItem | None = None
        for lk in level_keys:
            level_item = self._folder_item(None, level_labels[lk])
            block_keys = sorted(
                tree[lk].keys(),
                key=lambda b, lv=lk: branch_sort_key(lv, b, "_na"),
            )
            for bk in block_keys:
                block_item = self._folder_item(level_item, block_labels[(lk, bk)])
                track_keys = sorted(
                    tree[lk][bk].keys(),
                    key=lambda t, lv=lk, b=bk: branch_sort_key(lv, b, t),
                )
                for tk in track_keys:
                    tl = track_labels[(lk, bk, tk)]
                    courses_in_track = sorted(
                        tree[lk][bk][tk],
                        key=lambda row: (
                            course_ue_code(row) or str(row.get("code") or ""),
                            str(row.get("name") or "").lower(),
                        ),
                    )
                    if len(track_keys) > 1 or tk not in {"C", "_na"}:
                        parent = self._folder_item(block_item, tl)
                    else:
                        parent = block_item
                    for c in courses_in_track:
                        leaf = self._leaf_item(parent, c)
                        if first_leaf is None:
                            first_leaf = leaf

        for i in range(self.course_tree.topLevelItemCount()):
            self._expand_paths(self.course_tree.topLevelItem(i), expanded)

        if not expanded and self.course_tree.topLevelItemCount():
            self.course_tree.topLevelItem(0).setExpanded(True)
            top = self.course_tree.topLevelItem(0)
            if top and top.childCount():
                top.child(0).setExpanded(True)

        target_course_id: int | None = prev_id
        if target_course_id is None and first_leaf is not None:
            target_course_id = self._item_course_id(first_leaf)

        try:
            if target_course_id is not None:
                target_item = self._find_item_for_course(target_course_id)
                if target_item is not None:
                    self.course_tree.setCurrentItem(target_item)
            elif not courses:
                if self.default_academic_year:
                    self.assessments_hint.setText(
                        f"Aucune UE pour {self.default_academic_year} — importez ou créez une maquette "
                        "dans l'onglet Maquette."
                    )
                else:
                    self.assessments_hint.setText("")
                fill_table(self.assessments_table, [], [])
        finally:
            self.course_tree.blockSignals(False)

        self.refresh_assessments()

    def refresh_assessments(self) -> None:
        cid = self._current_course_id()
        if cid is None:
            self.assessments_hint.setText("")
            fill_table(self.assessments_table, [], [])
            return
        course = self.repo.get_course(cid)
        rows = self.repo.list_assessments(cid)
        table_rows = [
            [r["id"], r["name"], r["kind"], r["coefficient"], r["session"], r["display_order"]] for r in rows
        ]
        hint = ""
        if course:
            mcc = (course.get("mcc_text") or "").strip()
            short_mcc = (mcc[:200] + "…") if len(mcc) > 200 else mcc
            hint = f"Assessments — {course['code']} — {course['name']}"
            if short_mcc:
                hint += f"\nMCC (maquette) : {short_mcc}"
        self.assessments_hint.setText(hint)
        fill_table(
            self.assessments_table,
            ["ID", "Name", "Kind", "Coef", "Session", "Order"],
            table_rows,
        )

    def open_convocation(self) -> None:
        cid = self._current_course_id()
        dlg = ExamConvocationDialog(
            self.repo,
            course_id=cid,
            academic_year=self.default_academic_year,
            parent=self,
        )
        dlg.exec()

    def open_defense_planning(self) -> None:
        cid = self._current_course_id()
        if cid is not None and not self.repo.is_internship_course(cid):
            cid = None
        dlg = InternshipDefensePlanningDialog(
            self.repo,
            course_id=cid,
            academic_year=self.default_academic_year,
            parent=self,
        )
        dlg.exec()

    def add_course(self) -> None:
        dlg = CourseDialog(self)
        if dlg.exec():
            try:
                kw = _course_fields_from_dialog(dlg)
                new_id = self.repo.add_course(dlg.code.text().strip(), **kw)
                dlg.apply_syllabus(self.repo, int(new_id))
                self.refresh()
                item = self._find_item_for_course(int(new_id))
                if item is not None:
                    self.course_tree.setCurrentItem(item)
                for cb in self.refresh_callbacks:
                    cb()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def edit_course(self) -> None:
        cid = self._current_course_id()
        if cid is None:
            QMessageBox.information(self, "Edit course", "Select a course in the tree.")
            return
        course = self.repo.get_course(cid)
        if course is None:
            return
        dlg = CourseDialog(self, course=course)
        if dlg.exec():
            try:
                kw = _course_fields_from_dialog(dlg)
                self.repo.update_course(cid, dlg.code.text().strip(), **kw)
                dlg.apply_syllabus(self.repo, cid)
                self.refresh()
                for cb in self.refresh_callbacks:
                    cb()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def delete_course(self) -> None:
        course_ids = self._selected_course_ids()
        if not course_ids:
            cid = self._current_course_id()
            if cid is not None:
                course_ids = [cid]
        if not course_ids:
            QMessageBox.information(self, "Delete course", "Select a course in the tree.")
            return
        if len(course_ids) == 1:
            course = self.repo.get_course(int(course_ids[0]))
            if course is None:
                return
            msg = (
                f"Delete course {course['code']} — {course['name']}?\n"
                "Assessments and template links for this course will be removed."
            )
        else:
            msg = (
                f"Delete {len(course_ids)} courses?\n"
                "Assessments and template links for these courses will be removed."
            )
        reply = QMessageBox.question(
            self,
            "Delete course",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            for cid in course_ids:
                self.repo.delete_course(int(cid))
            self.refresh()
            for cb in self.refresh_callbacks:
                cb()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def add_assessment(self) -> None:
        cid = self._current_course_id()
        if cid is None:
            QMessageBox.warning(self, "Warning", "Select a course first.")
            return
        dlg = AssessmentDialog(self)
        if dlg.exec():
            try:
                self.repo.add_assessment(
                    cid,
                    dlg.name.text().strip(),
                    dlg.kind.currentText(),
                    dlg.coefficient.value(),
                    dlg.session.value(),
                    dlg.display_order.value(),
                )
                self.refresh_assessments()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def generate_assessments_from_mcc(self) -> None:
        course_ids = self._selected_course_ids()
        if not course_ids:
            cid = self._current_course_id()
            if cid is not None:
                course_ids = [cid]
        if not course_ids:
            QMessageBox.information(self, "Generate assessments", "Select one or more courses in the tree.")
            return

        targets: list[tuple[int, dict, list[dict]]] = []
        no_mcc: list[str] = []
        not_parsed: list[str] = []
        errors: list[str] = []

        for cid in course_ids:
            course = self.repo.get_course(int(cid))
            if course is None:
                continue
            label = f"{course.get('code','')} — {course.get('name','')}".strip(" —")
            mcc_text = (course.get("mcc_text") or "").strip()
            if not mcc_text:
                no_mcc.append(label)
                continue
            parsed = parse_mcc_text_to_assessments_dicts(mcc_text, display_order_start=0)
            if not parsed:
                not_parsed.append(label)
                continue
            targets.append((int(cid), course, parsed))

        if not targets:
            msg = "No course with usable MCC text found in the selection."
            if no_mcc:
                msg += "\n\nNo MCC:\n- " + "\n- ".join(no_mcc[:10]) + ("\n…" if len(no_mcc) > 10 else "")
            if not_parsed:
                msg += "\n\nCould not parse:\n- " + "\n- ".join(not_parsed[:10]) + ("\n…" if len(not_parsed) > 10 else "")
            QMessageBox.warning(self, "Generate assessments", msg)
            return

        preview = [f"{c.get('code','')} ({len(p)} assessments)" for _, c, p in targets[:8]]
        tail = "\n…" if len(targets) > 8 else ""
        msg = (
            f"This will delete existing assessments and recreate them from MCC for {len(targets)} course(s).\n\n"
            + "\n".join(preview)
            + tail
            + "\n\nThis may delete existing student grades for those assessments (cascade delete). Continue?"
        )
        if no_mcc:
            msg += f"\n\nSkipped (no MCC): {len(no_mcc)}"
        if not_parsed:
            msg += f"\nSkipped (could not parse MCC): {len(not_parsed)}"

        reply = QMessageBox.question(
            self,
            "Generate assessments",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            for cid, course, parsed in targets:
                try:
                    self.repo.delete_assessments_for_course(int(cid))
                    for a in parsed:
                        self.repo.add_assessment(
                            int(cid),
                            a["name"],
                            a["kind"],
                            float(a["coefficient"]),
                            int(a["session"]),
                            int(a["display_order"]),
                        )
                except Exception as exc:
                    errors.append(f"{course.get('code','')}: {exc}")
            self.refresh_assessments()
            for cb in self.refresh_callbacks:
                cb()
            if errors:
                QMessageBox.warning(
                    self,
                    "Generate assessments",
                    "Completed with errors:\n"
                    + "\n".join(errors[:12])
                    + (f"\n… ({len(errors)-12} more)" if len(errors) > 12 else ""),
                )
        except Exception as exc:
            QMessageBox.critical(self, "Generate assessments", str(exc))
