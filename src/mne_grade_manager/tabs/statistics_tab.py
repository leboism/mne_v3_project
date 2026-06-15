"""Aperçu statistiques (effectifs, diversité, réussite) — base évolutive."""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..gui.widgets import make_actions_toolbar

from ..services.statistics import (
    enrollment_overview,
    internship_follow_up_summary,
    template_success_summary,
)


class StatisticsTab(QWidget):
    def __init__(self, repo, default_academic_year: str = ""):
        super().__init__()
        self.repo = repo
        self._default_academic_year = (default_academic_year or "").strip()
        self._last_export_rows: list[list[str]] = []

        root = QVBoxLayout(self)
        intro = QLabel(
            "<b>Statistiques</b> — aperçu des effectifs et indicateurs de réussite. "
            "Les calculs de réussite reprennent les mêmes règles que l'onglet <b>Résultats</b> "
            "(moyenne année &gt; 10 ; blocs validés). D'autres indicateurs pourront s'ajouter ici."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        filt = QFormLayout()
        self.year_combo = QComboBox()
        self.level_combo = QComboBox()
        self.level_combo.addItem("Tous", "")
        self.level_combo.addItem("M1", "M1")
        self.level_combo.addItem("M2", "M2")
        self.track_combo = QComboBox()
        self.track_combo.addItem("Tous", "")
        self.template_combo = QComboBox()
        self.session_combo = QComboBox()
        self.session_combo.addItem("Session 1", "s1")
        self.session_combo.addItem("Session 2", "s2")
        filt.addRow("Millésime", self.year_combo)
        filt.addRow("Niveau", self.level_combo)
        filt.addRow("Parcours", self.track_combo)
        filt.addRow("Maquette (réussite)", self.template_combo)
        filt.addRow("Session affichée", self.session_combo)
        root.addLayout(filt)

        root.addLayout(
            make_actions_toolbar(
                self,
                primary=[("Actualiser", self.refresh)],
                menu_sections=[[("Exporter CSV…", self.export_csv)]],
            ).layout
        )

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        self.tables: dict[str, QTableWidget] = {}
        for key, title in (
            ("nationality", "Nationalités"),
            ("origin_country", "Pays — établissement d'origine"),
            ("origin_inst", "Établissements d'origine"),
            ("enrollment", "Établissements d'inscription"),
            ("success", "Réussite (maquette sélectionnée)"),
            ("internship", "Suivi stages"),
        ):
            box = QGroupBox(title)
            lay = QVBoxLayout(box)
            tbl = QTableWidget()
            tbl.setAlternatingRowColors(True)
            lay.addWidget(tbl)
            self.tables[key] = tbl
            root.addWidget(box)

        self._fill_filter_combos()
        self.refresh()

    def _fill_filter_combos(self) -> None:
        years = sorted(
            {str(s.get("academic_year") or "").strip() for s in self.repo.list_students()},
            reverse=True,
        )
        years = [y for y in years if y]
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItem("Tous", "")
        for y in years:
            self.year_combo.addItem(y, y)
        if self._default_academic_year:
            i = self.year_combo.findData(self._default_academic_year)
            if i >= 0:
                self.year_combo.setCurrentIndex(i)
            else:
                self.year_combo.addItem(self._default_academic_year, self._default_academic_year)
                self.year_combo.setCurrentIndex(self.year_combo.count() - 1)
        self.year_combo.blockSignals(False)

        tracks = sorted(
            {str(s.get("track") or "").strip().upper() for s in self.repo.list_students() if s.get("track")}
        )
        self.track_combo.blockSignals(True)
        cur_tr = self.track_combo.currentData()
        self.track_combo.clear()
        self.track_combo.addItem("Tous", "")
        for t in tracks:
            self.track_combo.addItem(t, t)
        if cur_tr is not None:
            i = self.track_combo.findData(cur_tr)
            if i >= 0:
                self.track_combo.setCurrentIndex(i)
        self.track_combo.blockSignals(False)

        self.template_combo.blockSignals(True)
        cur_tpl = self.template_combo.currentData()
        self.template_combo.clear()
        self.template_combo.addItem("—", None)
        for t in self.repo.list_templates():
            label = f"{t.get('name', '')} [{t.get('academic_year', '')}]"
            self.template_combo.addItem(label, int(t["id"]))
        if cur_tpl is not None:
            i = self.template_combo.findData(cur_tpl)
            if i >= 0:
                self.template_combo.setCurrentIndex(i)
        self.template_combo.blockSignals(False)

    def _filter_values(self) -> tuple[str, str, str]:
        return (
            str(self.year_combo.currentData() or ""),
            str(self.level_combo.currentData() or ""),
            str(self.track_combo.currentData() or ""),
        )

    def _fill_table(self, key: str, headers: list[str], rows: list[list]) -> None:
        tbl = self.tables[key]
        tbl.clear()
        tbl.setColumnCount(len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                tbl.setItem(r, c, QTableWidgetItem(str(val)))
        tbl.resizeColumnsToContents()

    def refresh(self) -> None:
        self._fill_filter_combos()
        ay, lv, tr = self._filter_values()
        ov = enrollment_overview(self.repo, academic_year=ay, level=lv, track=tr)
        total = int(ov["total"])
        self.summary_label.setText(
            f"<b>{total}</b> étudiant(s) dans le périmètre "
            f"({ay or 'toutes années'}, {lv or 'tous niveaux'}, {tr or 'tous parcours'})."
        )

        def dist_rows(counter: dict[str, int]) -> list[list]:
            if not counter:
                return []
            t = sum(counter.values()) or 1
            return [[k, n, f"{100.0 * n / t:.1f}%"] for k, n in counter.items()]

        self._fill_table(
            "nationality",
            ["Nationalité", "Effectif", "%"],
            dist_rows(ov["by_nationality"]),
        )
        self._fill_table(
            "origin_country",
            ["Pays", "Effectif", "%"],
            dist_rows(ov["by_origin_country"]),
        )
        self._fill_table(
            "origin_inst",
            ["Établissement", "Effectif", "%"],
            dist_rows(ov["by_origin_institution"]),
        )
        self._fill_table(
            "enrollment",
            ["Établissement", "Effectif", "%"],
            dist_rows(ov["by_enrollment_institution"]),
        )

        export_rows: list[list[str]] = [
            ["# Effectifs", str(total)],
            [],
            ["Nationalité", "Effectif", "%"],
        ]
        for row in dist_rows(ov["by_nationality"]):
            export_rows.append([str(x) for x in row])

        tid = self.template_combo.currentData()
        if tid is not None:
            vs = str(self.session_combo.currentData() or "s1")
            succ = template_success_summary(self.repo, int(tid), view_session=vs)
            succ_rows = [
                ["Inscrits (session)", succ["enrolled"]],
                ["Avec moyenne année calculable", succ["with_year_average"]],
                [
                    f"Moyenne année > {succ['year_threshold']}",
                    succ["year_average_above_threshold"],
                    f"{succ.get('year_success_rate_pct') or '—'} % (sur calculables)",
                ],
                [
                    "Moyenne année + jury > seuil",
                    succ["year_with_jury_above_threshold"],
                    f"{succ.get('year_jury_success_rate_pct') or '—'} %",
                ],
            ]
            for bk, st in succ.get("blocks", {}).items():
                succ_rows.append(
                    [
                        f"Bloc {bk} validé",
                        st["validated"],
                        f"{st.get('validation_rate_pct') or '—'} %",
                    ]
                )
            self._fill_table("success", ["Indicateur", "Valeur", "Taux"], succ_rows)
            export_rows.extend([[], ["Réussite", "Valeur", "Taux"]] + [[str(x) for x in r] for r in succ_rows])

            intern = internship_follow_up_summary(self.repo, int(tid))
            if intern["internship_course_count"]:
                ist_rows = [
                    ["UE stage dans la maquette", intern["internship_course_count"]],
                    ["Créneaux suivi (étudiant × UE stage)", intern["follow_up_slots"]],
                ]
                for k, v in intern.get("by_status", {}).items():
                    ist_rows.append([k, v])
                self._fill_table("internship", ["Statut / info", "Valeur"], ist_rows)
            else:
                self._fill_table("internship", ["Statut / info", "Valeur"], [["Aucune UE stage", "—"]])
        else:
            self._fill_table(
                "success",
                ["Indicateur", "Valeur", "Taux"],
                [["Sélectionnez une maquette", "", ""]],
            )
            self._fill_table(
                "internship",
                ["Statut / info", "Valeur"],
                [["Sélectionnez une maquette", ""]],
            )

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
