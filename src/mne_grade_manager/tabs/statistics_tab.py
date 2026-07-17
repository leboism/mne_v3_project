"""Aperçu statistiques (effectifs, diversité, réussite) — base évolutive."""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..gui.statistics_criteria_dialog import StatisticsCriteriaDialog
from ..gui.widgets import make_actions_toolbar

from ..services.statistics import (
    StatisticsCriteria,
    enrollment_overview,
    gender_filter_summary,
    internship_follow_up_summary,
    template_success_summary,
)

_COMPACT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("by_year", "Répartition par millésime"),
    ("by_level", "Répartition par niveau"),
    ("by_track", "Répartition par parcours"),
    ("by_gender", "Répartition par genre"),
    ("nationality", "Nationalités"),
    ("origin_country", "Pays d'origine"),
    ("origin_inst", "Établissements d'origine"),
    ("enrollment", "Établissements d'inscription"),
)

_WIDE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("success", "Réussite"),
    ("internship", "Suivi stages"),
)

_TABLE_ROW_HEIGHT = 28
_DIST_TABLE_MAX_ROWS = 8
_WIDE_TABLE_MAX_ROWS = 14


class StatisticsTab(QWidget):
    def __init__(self, repo, default_academic_year: str = ""):
        super().__init__()
        self.repo = repo
        self._default_academic_year = (default_academic_year or "").strip()
        self._last_export_rows: list[list[str]] = []
        self._criteria = self._default_criteria()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        intro = QLabel(
            "<b>Statistiques</b> — <i>Critères…</i> pour le périmètre, <i>Générer</i> pour actualiser."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.criteria_label = QLabel("")
        self.criteria_label.setWordWrap(True)
        self.criteria_label.setStyleSheet("color: palette(mid);")
        root.addWidget(self.criteria_label)

        root.addLayout(
            make_actions_toolbar(
                self,
                primary=[
                    ("Critères…", self.open_criteria),
                    ("Générer", self.refresh),
                ],
                menu_sections=[[("Exporter CSV…", self.export_csv)]],
            ).layout
        )

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        compact_box = QGroupBox("Effectifs et diversité")
        self._compact_grid = QGridLayout(compact_box)
        self._compact_grid.setHorizontalSpacing(10)
        self._compact_grid.setVerticalSpacing(8)
        content_layout.addWidget(compact_box)

        wide_box = QGroupBox("Réussite et stages")
        wide_layout = QVBoxLayout(wide_box)
        wide_layout.setSpacing(8)
        content_layout.addWidget(wide_box)

        self.tables: dict[str, QTableWidget] = {}
        self._table_boxes: dict[str, QGroupBox] = {}

        for idx, (key, title) in enumerate(_COMPACT_SECTIONS):
            box, tbl = self._make_section_box(title)
            self._compact_grid.addWidget(box, idx // 2, idx % 2)
            self.tables[key] = tbl
            self._table_boxes[key] = box

        for key, title in _WIDE_SECTIONS:
            box, tbl = self._make_section_box(title)
            wide_layout.addWidget(box)
            self.tables[key] = tbl
            self._table_boxes[key] = box

        content_layout.addStretch(1)
        scroll.setWidget(content)
        self.refresh()

    def _make_section_box(self, title: str) -> tuple[QGroupBox, QTableWidget]:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 10, 8, 8)
        tbl = QTableWidget()
        tbl.setAlternatingRowColors(True)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tbl.verticalHeader().setVisible(False)
        tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(tbl)
        return box, tbl

    def _default_criteria(self) -> StatisticsCriteria:
        years = [self._default_academic_year] if self._default_academic_year else []
        template_ids: list[int] = []
        if years:
            year_set = set(years)
            for t in self.repo.list_templates():
                if str(t.get("academic_year") or "").strip() in year_set:
                    template_ids.append(int(t["id"]))
        return StatisticsCriteria(academic_years=years, template_ids=template_ids)

    def open_criteria(self) -> None:
        dlg = StatisticsCriteriaDialog(
            self.repo,
            self._criteria,
            default_academic_year=self._default_academic_year,
            parent=self,
        )
        if dlg.exec():
            self._criteria = dlg.criteria()
            self.refresh()

    def _fill_table(
        self,
        key: str,
        headers: list[str],
        rows: list[list],
        *,
        wide: bool = False,
    ) -> None:
        tbl = self.tables[key]
        tbl.clear()
        tbl.setColumnCount(len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                text = str(val)
                item = QTableWidgetItem(text)
                if c > 0:
                    item.setTextAlignment(
                        int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    )
                if len(text) > 48:
                    item.setToolTip(text)
                tbl.setItem(r, c, item)

        hdr = tbl.horizontalHeader()
        if wide:
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for col in range(1, len(headers)):
                hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            max_rows = _WIDE_TABLE_MAX_ROWS
        else:
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for col in range(1, len(headers)):
                hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            max_rows = _DIST_TABLE_MAX_ROWS

        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        row_count = max(len(rows), 1)
        if row_count > max_rows:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            visible_rows = max_rows
        else:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            visible_rows = row_count

        height = hdr.height() + _TABLE_ROW_HEIGHT * visible_rows + 8
        tbl.setFixedHeight(height)
        tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _set_section_visible(self, key: str, visible: bool) -> None:
        self._table_boxes[key].setVisible(visible)

    def refresh(self) -> None:
        c = self._criteria
        self.criteria_label.setText(c.summary_label())

        ov = enrollment_overview(
            self.repo,
            academic_years=c.academic_years,
            level=c.level,
            track=c.track,
            genders=c.genders,
        )
        total = int(ov["total"])
        years_txt = ", ".join(c.academic_years) if c.academic_years else "toutes années"
        gen_txt = gender_filter_summary(c.genders)
        self.summary_label.setText(
            f"<b>{total}</b> étudiant(s) — {years_txt} · "
            f"{c.level or 'tous niveaux'} · {c.track or 'tous parcours'} · {gen_txt}"
        )

        def dist_rows(counter: dict[str, int]) -> list[list]:
            if not counter:
                return []
            t = sum(counter.values()) or 1
            return [[k, n, f"{100.0 * n / t:.1f}%"] for k, n in counter.items()]

        def show_dist(key: str, headers: list[str], counter: dict[str, int], *, enabled: bool) -> None:
            rows = dist_rows(counter) if enabled else []
            visible = enabled and bool(rows)
            self._set_section_visible(key, visible)
            if visible:
                self._fill_table(key, headers, rows, wide=False)
            elif enabled:
                self._fill_table(key, headers, [["(aucune donnée)", "—", "—"]], wide=False)
                self._set_section_visible(key, True)

        show_dist("by_year", ["Millésime", "Eff.", "%"], ov["by_academic_year"], enabled=c.include_by_academic_year)
        show_dist("by_level", ["Niveau", "Eff.", "%"], ov["by_level"], enabled=c.include_by_level)
        show_dist("by_track", ["Parcours", "Eff.", "%"], ov["by_track"], enabled=c.include_by_track)
        show_dist("by_gender", ["Genre", "Eff.", "%"], ov["by_gender"], enabled=c.include_by_gender)
        show_dist(
            "nationality",
            ["Nationalité", "Eff.", "%"],
            ov["by_nationality"],
            enabled=c.include_nationality,
        )
        show_dist(
            "origin_country",
            ["Pays", "Eff.", "%"],
            ov["by_origin_country"],
            enabled=c.include_origin_country,
        )
        show_dist(
            "origin_inst",
            ["Établissement", "Eff.", "%"],
            ov["by_origin_institution"],
            enabled=c.include_origin_institution,
        )
        show_dist(
            "enrollment",
            ["Établissement", "Eff.", "%"],
            ov["by_enrollment_institution"],
            enabled=c.include_enrollment_institution,
        )

        export_rows: list[list[str]] = [
            ["# Effectifs", str(total)],
            ["Millésimes", years_txt],
            ["Genres", gen_txt],
            [],
        ]
        if c.include_by_gender and ov["by_gender"]:
            export_rows.extend(
                [["Genre", "Effectif", "%"]]
                + [[str(x) for x in row] for row in dist_rows(ov["by_gender"])]
            )
            export_rows.append([])
        if c.include_nationality and ov["by_nationality"]:
            export_rows.extend(
                [["Nationalité", "Effectif", "%"]]
                + [[str(x) for x in row] for row in dist_rows(ov["by_nationality"])]
            )
            export_rows.append([])

        succ_rows: list[list] = []
        intern_rows: list[list] = []
        vs = str(c.view_session or "s1")
        if c.include_success or c.include_internship:
            for tid in c.template_ids:
                tpl = self.repo.get_template(int(tid)) or {}
                tpl_label = f"{tpl.get('name', '')} [{tpl.get('academic_year', '')}]"
                if c.include_success:
                    succ = template_success_summary(
                        self.repo, int(tid), view_session=vs, genders=c.genders
                    )
                    succ_rows.append([tpl_label, "", ""])
                    succ_rows.extend(
                        [
                            ["Inscrits (session)", succ["enrolled"], ""],
                            ["Moyenne année calculable", succ["with_year_average"], ""],
                            [
                                f"Moyenne année ≥ {succ['year_threshold']}",
                                succ["year_average_above_threshold"],
                                f"{succ.get('year_success_rate_pct') or '—'} %",
                            ],
                            [
                                "Moyenne année + jury ≥ seuil",
                                succ["year_with_jury_above_threshold"],
                                f"{succ.get('year_jury_success_rate_pct') or '—'} %",
                            ],
                        ]
                    )
                    for bk, st in succ.get("blocks", {}).items():
                        succ_rows.append(
                            [
                                f"  Bloc {bk} validé",
                                st["validated"],
                                f"{st.get('validation_rate_pct') or '—'} %",
                            ]
                        )
                    succ_rows.append(["", "", ""])
                if c.include_internship:
                    intern = internship_follow_up_summary(self.repo, int(tid), genders=c.genders)
                    intern_rows.append([tpl_label, ""])
                    if intern["internship_course_count"]:
                        intern_rows.extend(
                            [
                                ["UE stage", intern["internship_course_count"]],
                                ["Créneaux suivi", intern["follow_up_slots"]],
                            ]
                        )
                        for k, v in intern.get("by_status", {}).items():
                            intern_rows.append([f"  {k}", v])
                    else:
                        intern_rows.append(["Aucune UE stage", "—"])
                    intern_rows.append(["", ""])

        if succ_rows and succ_rows[-1] == ["", "", ""]:
            succ_rows.pop()
        if intern_rows and intern_rows[-1] == ["", ""]:
            intern_rows.pop()

        if c.include_success:
            if succ_rows:
                self._set_section_visible("success", True)
                self._fill_table("success", ["Indicateur", "Valeur", "Taux"], succ_rows, wide=True)
                export_rows.extend([["Réussite", "Valeur", "Taux"]] + [[str(x) for x in r] for r in succ_rows])
            else:
                self._set_section_visible("success", True)
                self._fill_table(
                    "success",
                    ["Indicateur", "Valeur", "Taux"],
                    [["Choisissez une maquette dans Critères…", "", ""]],
                    wide=True,
                )
        else:
            self._set_section_visible("success", False)

        if c.include_internship:
            if intern_rows:
                self._set_section_visible("internship", True)
                self._fill_table("internship", ["Statut", "Valeur"], intern_rows, wide=True)
            else:
                self._set_section_visible("internship", True)
                self._fill_table(
                    "internship",
                    ["Statut", "Valeur"],
                    [["Choisissez une maquette dans Critères…", ""]],
                    wide=True,
                )
        else:
            self._set_section_visible("internship", False)

        self._last_export_rows = export_rows

    def export_csv(self) -> None:
        if not self._last_export_rows:
            QMessageBox.information(self, "Export", "Rien à exporter.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter statistiques CSV",
            str(Path.home() / "Documents" / "statistiques_mne.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerows(self._last_export_rows)
            QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
