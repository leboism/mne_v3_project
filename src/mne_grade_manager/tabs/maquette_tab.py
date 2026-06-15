"""Onglet Maquette : structure du parcours, import / export Excel, édition des cours."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QInputDialog,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..gui.dialogs import (
    AddCourseToTemplateDialog,
    CourseDialog,
    EditMaquettePlacementDialog,
    TemplateDialog,
)
from ..gui.maquette_import_dialog import MaquetteImportDialog
from ..services.maquette_export import export_template_to_maquette_xlsx
from ..services.maquette_import import (
    enrich_maquette_rows_mne_codes,
    extract_academic_year_from_path,
    load_maquette_sheet,
)
from ..services.dates import suggest_next_academic_year
from ..services.maquette_io import import_maquette_row_dicts
from ..gui.widgets import make_actions_toolbar


def _course_fields_from_dialog(dlg: CourseDialog) -> dict:
    return dlg.fields_dict()


class MaquetteTab(QWidget):
    def __init__(self, repo, refresh_callbacks=None):
        super().__init__()
        self.repo = repo
        self.refresh_callbacks = refresh_callbacks or []
        self._template_ids: list[int] = []
        layout = QVBoxLayout(self)

        intro = QLabel(
            "<b>Maquette</b> : une maquette par <i>année + niveau + parcours</i> "
            "(M1&nbsp;: P ou C ; M2&nbsp;: NPD, NPO, DWM, NFC, NRPE). "
            "Pour conserver l’historique (relevés dans 5–10 ans), <b>dupliquez</b> ou "
            "<b>reportez</b> vers l’année suivante plutôt que de modifier une maquette passée. "
            "Chaque copie enregistre sa filiation (maquette parente, version, millésime)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.lineage_label = QLabel("")
        self.lineage_label.setWordWrap(True)
        self.lineage_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self.lineage_label)

        layout.addLayout(
            make_actions_toolbar(
                self,
                primary=[
                    ("Nouvelle maquette…", self.add_maquette),
                    ("Modifier…", self.edit_maquette),
                    ("Ajouter une UE…", self.add_ue_to_maquette),
                ],
                menu_sections=[
                    [
                        ("Renommer la maquette…", self.rename_maquette),
                        ("Dupliquer / nouvelle version…", self.clone_maquette),
                        ("Reporter vers l'année suivante…", self.rollover_maquette),
                        ("Reporter tout le millésime…", self.rollover_academic_year),
                        ("Supprimer la maquette", self.delete_maquette),
                    ],
                    [
                        ("Importer Excel (.xlsx)…", self.import_maquette),
                        ("Exporter Excel (.xlsx)…", self.export_maquette),
                    ],
                    [
                        ("Retirer l'UE de la maquette", self.remove_ue_from_maquette),
                        ("Modifier l'UE (cours)…", self.edit_course),
                        ("Emplacement (bloc / ordre)…", self.edit_placement),
                    ],
                ],
            ).layout
        )

        splitter = QSplitter()
        self.maquette_list = QListWidget()
        self.maquette_list.currentRowChanged.connect(self._on_maquette_selected)
        self.maquette_courses_table = QTableWidget()
        self.maquette_courses_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.maquette_courses_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        splitter.addWidget(self.maquette_list)
        splitter.addWidget(self.maquette_courses_table)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        self.refresh()

    def current_template_id(self) -> int | None:
        idx = self.maquette_list.currentRow()
        if idx < 0 or idx >= len(self._template_ids):
            return None
        return int(self._template_ids[idx])

    def selected_course_id(self) -> int | None:
        row = self.maquette_courses_table.currentRow()
        if row < 0:
            return None
        it = self.maquette_courses_table.item(row, 0)
        if it is None:
            return None
        raw = it.data(Qt.ItemDataRole.UserRole)
        return int(raw) if raw is not None else None

    def selected_course_ids(self) -> list[int]:
        rows = sorted({idx.row() for idx in self.maquette_courses_table.selectionModel().selectedRows()})
        out: list[int] = []
        for r in rows:
            it = self.maquette_courses_table.item(r, 0)
            if it is None:
                continue
            raw = it.data(Qt.ItemDataRole.UserRole)
            if raw is None:
                continue
            try:
                out.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not out:
            one = self.selected_course_id()
            return [one] if one is not None else []
        return out

    def refresh(self) -> None:
        templates = self.repo.list_templates()
        self._template_ids = [t["id"] for t in templates]
        self.maquette_list.clear()
        for t in templates:
            self.maquette_list.addItem(
                f"{t['name']}  |  {t['academic_year']}  |  {t['level']} {t['track']}  (v{t['version']})"
            )
        if templates:
            self.maquette_list.setCurrentRow(0)
            self._on_maquette_selected()
        else:
            self._clear_courses_table()
            self.lineage_label.setText("")

    def _clear_courses_table(self) -> None:
        self.maquette_courses_table.clear()
        self.maquette_courses_table.setRowCount(0)
        self.maquette_courses_table.setColumnCount(0)

    def _on_maquette_selected(self, _row: int = -1) -> None:
        self._refresh_lineage_label()
        self.refresh_maquette_courses()

    def _refresh_lineage_label(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            self.lineage_label.setText("")
            return
        prov = self.repo.format_template_provenance(int(tid))
        tpl = self.repo.get_template(int(tid)) or {}
        created = str(tpl.get("created_at") or "").strip()
        extra = f" — créée le {created[:10]}" if len(created) >= 10 else ""
        self.lineage_label.setText(f"<i>Filiation :</i> {prov}{extra}")

    def refresh_maquette_courses(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            self._clear_courses_table()
            self.lineage_label.setText("")
            return
        rows = self.repo.list_template_courses(tid)
        headers = ["Code", "Name", "Block", "Coef", "Order", "ECTS", "H. tot", "Opt", "Libre"]
        self.maquette_courses_table.setColumnCount(len(headers))
        self.maquette_courses_table.setHorizontalHeaderLabels(headers)
        self.maquette_courses_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [
                r["code"],
                r["name"],
                r.get("block_name") or "",
                r["global_coefficient"],
                r["display_order"],
                r["ects"],
                r.get("hours_total") if r.get("hours_total") is not None else "",
                r.get("optional", 0),
                r.get("free_ue", 0),
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(r["course_id"]))
                self.maquette_courses_table.setItem(i, c, item)
        self.maquette_courses_table.resizeColumnsToContents()

    def add_maquette(self) -> None:
        dlg = TemplateDialog(self)
        if dlg.exec():
            try:
                self.repo.add_template(
                    dlg.effective_name(),
                    dlg.level_value(),
                    dlg.track_value(),
                    dlg.academic_year.text().strip(),
                    dlg.version.text().strip(),
                )
                self.refresh()
                for cb in self.refresh_callbacks:
                    cb()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def edit_maquette(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            QMessageBox.information(self, "Maquette", "Sélectionnez une maquette dans la liste.")
            return
        meta = next((x for x in self.repo.list_templates() if int(x["id"]) == int(tid)), None)
        if not meta:
            QMessageBox.warning(self, "Maquette", "Maquette introuvable.")
            return
        dlg = TemplateDialog(
            self,
            template=meta,
            lineage_text=self.repo.format_template_provenance(int(tid)),
        )
        if dlg.exec():
            try:
                self.repo.update_template_metadata(
                    int(tid),
                    name=dlg.effective_name(),
                    level=dlg.level_value(),
                    track=dlg.track_value(),
                    academic_year=dlg.academic_year.text().strip(),
                    version=dlg.version.text().strip(),
                )
                self.refresh()
                for cb in self.refresh_callbacks:
                    cb()
            except Exception as exc:
                QMessageBox.critical(self, "Erreur", str(exc))

    def delete_maquette(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            QMessageBox.information(self, "Maquette", "Sélectionnez une maquette dans la liste.")
            return
        templates = self.repo.list_templates()
        meta = next((x for x in templates if int(x["id"]) == int(tid)), None)
        name = (meta or {}).get("name") or f"Maquette #{tid}"
        n_courses = len(self.repo.list_template_courses(int(tid)))
        n_students = len(self.repo.list_students_for_template(int(tid)))
        reply = QMessageBox.question(
            self,
            "Supprimer la maquette",
            f"Supprimer la maquette :\n\n{name}\n\n"
            f"- UE liées : {n_courses}\n"
            f"- Étudiants inscrits : {n_students}\n\n"
            "Les cours (bibliothèque) ne seront pas supprimés.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.repo.delete_template(int(tid))
            self.refresh()
            for cb in self.refresh_callbacks:
                cb()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def rename_maquette(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            QMessageBox.information(self, "Maquette", "Sélectionnez une maquette dans la liste.")
            return
        templates = self.repo.list_templates()
        meta = next((x for x in templates if int(x["id"]) == int(tid)), None)
        current = (meta or {}).get("name") or ""
        text, ok = QInputDialog.getText(
            self,
            "Renommer la maquette",
            "Nouveau nom :",
            text=str(current),
        )
        if not ok:
            return
        new_name = str(text).strip()
        if not new_name:
            QMessageBox.warning(self, "Maquette", "Le nom ne peut pas être vide.")
            return
        try:
            self.repo.rename_template(int(tid), new_name)
            self.refresh()
            for cb in self.refresh_callbacks:
                cb()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def clone_maquette(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            QMessageBox.information(self, "Maquette", "Sélectionnez une maquette dans la liste.")
            return
        templates = self.repo.list_templates()
        src = next((x for x in templates if int(x["id"]) == int(tid)), None)
        if src is None:
            return
        cur_year = str(src.get("academic_year") or "").strip()
        cur_version = str(src.get("version") or "1").strip() or "1"
        try:
            next_version = str(int(cur_version) + 1)
        except Exception:
            next_version = "2"

        name_default = str(src.get("name") or "").strip() or f"Maquette #{tid}"
        # 1) année cible
        year, ok = QInputDialog.getText(
            self,
            "Dupliquer la maquette",
            "Année universitaire cible (laisser identique pour nouvelle version) :",
            text=cur_year,
        )
        if not ok:
            return
        year = str(year).strip()
        # 2) version cible
        version, ok = QInputDialog.getText(
            self,
            "Dupliquer la maquette",
            "Version cible :",
            text=(next_version if year == cur_year else "1"),
        )
        if not ok:
            return
        version = str(version).strip() or "1"
        # 3) nom
        new_name, ok = QInputDialog.getText(
            self,
            "Dupliquer la maquette",
            "Nom de la nouvelle maquette :",
            text=name_default,
        )
        if not ok:
            return
        new_name = str(new_name).strip()
        if not new_name:
            QMessageBox.warning(self, "Maquette", "Le nom ne peut pas être vide.")
            return
        if not year:
            QMessageBox.warning(self, "Maquette", "L’année universitaire ne peut pas être vide.")
            return
        change_note, ok_note = QInputDialog.getMultiLineText(
            self,
            "Dupliquer la maquette",
            "Motif du changement (optionnel, pour l’historique) :",
            text=("Nouvelle version même millésime" if year == cur_year else f"Nouveau millésime {year}"),
        )
        if not ok_note:
            return
        try:
            new_id = self.repo.clone_template(
                int(tid),
                name=new_name,
                academic_year=year,
                version=version,
                change_note=str(change_note).strip(),
            )
            self.refresh()
            if int(new_id) in self._template_ids:
                self.maquette_list.setCurrentRow(self._template_ids.index(int(new_id)))
            for cb in self.refresh_callbacks:
                cb()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def rollover_maquette(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            QMessageBox.information(self, "Maquette", "Sélectionnez une maquette dans la liste.")
            return
        src = self.repo.get_template(int(tid))
        if not src:
            return
        cur_year = str(src.get("academic_year") or "").strip()
        next_year = suggest_next_academic_year(cur_year) if cur_year else ""
        year, ok = QInputDialog.getText(
            self,
            "Reporter la maquette",
            "Année universitaire cible :",
            text=next_year,
        )
        if not ok:
            return
        year = str(year).strip()
        if not year:
            QMessageBox.warning(self, "Maquette", "L’année universitaire ne peut pas être vide.")
            return
        change_note, ok_note = QInputDialog.getMultiLineText(
            self,
            "Reporter la maquette",
            "Commentaire (optionnel) :",
            text=f"Report annuel depuis {cur_year or '?'}",
        )
        if not ok_note:
            return
        try:
            new_id = self.repo.rollover_template_to_year(
                int(tid),
                year,
                change_note=str(change_note).strip(),
            )
            self.refresh()
            if int(new_id) in self._template_ids:
                self.maquette_list.setCurrentRow(self._template_ids.index(int(new_id)))
            for cb in self.refresh_callbacks:
                cb()
            QMessageBox.information(
                self,
                "Maquette",
                f"Maquette reportée vers {year} (nouvelle maquette #{int(new_id)}).",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Maquette", str(exc))

    def rollover_academic_year(self) -> None:
        tid = self.current_template_id()
        src_year = ""
        if tid is not None:
            tpl = self.repo.get_template(int(tid))
            src_year = str((tpl or {}).get("academic_year") or "").strip()
        if not src_year:
            src_year, ok = QInputDialog.getText(
                self,
                "Reporter le millésime",
                "Millésime source à reporter (ex. 2025-2026) :",
            )
            if not ok or not str(src_year).strip():
                return
            src_year = str(src_year).strip()
        tgt_year = suggest_next_academic_year(src_year)
        tgt_year, ok = QInputDialog.getText(
            self,
            "Reporter le millésime",
            f"Toutes les maquettes de {src_year} seront dupliquées vers :",
            text=tgt_year,
        )
        if not ok:
            return
        tgt_year = str(tgt_year).strip()
        if not tgt_year:
            QMessageBox.warning(self, "Maquette", "L’année cible ne peut pas être vide.")
            return
        if (
            QMessageBox.question(
                self,
                "Reporter le millésime",
                f"Créer les maquettes {tgt_year} à partir de {src_year} ?\n\n"
                "Les inscriptions et notes ne sont pas copiées.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            created, errors = self.repo.rollover_all_templates_for_year(src_year, tgt_year)
            self.refresh()
            for cb in self.refresh_callbacks:
                cb()
            msg = f"{len(created)} maquette(s) créée(s) pour {tgt_year}."
            if errors:
                msg += "\n\nIgnorées ou en erreur :\n" + "\n".join(f"• {e}" for e in errors)
            QMessageBox.information(self, "Maquette", msg)
        except Exception as exc:
            QMessageBox.critical(self, "Maquette", str(exc))

    def import_maquette(self) -> None:
        try:
            import openpyxl  # noqa: F401
        except Exception as exc:
            QMessageBox.critical(self, "Missing dependency", f"openpyxl is required.\n\n{exc}")
            return
        start = str(Path.home() / "Documents")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer une maquette Excel",
            start,
            "Excel (*.xlsx)",
        )
        if not path:
            return
        tid = self.current_template_id()
        try:
            dlg = MaquetteImportDialog(path, self, template_id=tid)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
        except Exception as exc:
            QMessageBox.critical(self, "Import maquette", str(exc))
            return
        update_existing = dlg.should_update_existing()

        # Mode OF consolidée : 1 maquette par parcours (PR1162 / PR1163)
        if dlg.is_of_consolidated_mode():
            plans = dlg.selected_consolidated_plans()
            if not plans:
                QMessageBox.information(
                    self, "Import maquette", "Aucun parcours sélectionné."
                )
                return
            created_tpl = 0
            total_created = total_updated = total_skipped = 0
            errors: list[str] = []
            academic_year = dlg.academic_year_value()
            for plan in plans:
                try:
                    tpl_id = int(
                        self.repo.add_template(
                            plan.name,
                            plan.level,
                            plan.track,
                            academic_year,
                            version="1",
                        )
                    )
                    created_tpl += 1
                    if not plan.rows:
                        errors.append(f"{plan.track}: aucune UE")
                        continue
                    c, u, s, e = import_maquette_row_dicts(
                        self.repo,
                        plan.rows,
                        update_existing=update_existing,
                        template_id=tpl_id,
                        attach_to_template=True,
                    )
                    total_created += c
                    total_updated += u
                    total_skipped += s
                    errors.extend([f"{plan.track}: {x}" for x in e])
                except Exception as exc:
                    errors.append(f"{plan.track}: {exc}")

            self.refresh()
            for cb in self.refresh_callbacks:
                cb()
            msg = (
                f"Maquettes créées : {created_tpl}\n"
                f"Cours créés : {total_created}\nMis à jour : {total_updated}\nIgnorés : {total_skipped}"
            )
            if errors:
                msg += "\n\nErreurs :\n" + "\n".join(errors[:12])
                if len(errors) > 12:
                    msg += f"\n… ({len(errors) - 12} de plus)"
            QMessageBox.information(self, "Import maquette", msg)
            return

        # Mode multi: 1 maquette par onglet sélectionné (ancien format)
        if dlg.is_multi_mode():
            selected = dlg.selected_sheets()
            if not selected:
                QMessageBox.information(self, "Import maquette", "Aucun onglet sélectionné.")
                return
            created_tpl = 0
            total_created = total_updated = total_skipped = 0
            errors: list[str] = []
            academic_year = dlg.academic_year_value()
            for entry in selected:
                sheet = entry["sheet"]
                level = entry.get("level", "")
                track = entry.get("track", "")
                name = entry.get("name", "") or sheet
                try:
                    tpl_id = int(self.repo.add_template(name, level, track, academic_year, version="1"))
                    created_tpl += 1
                    result = load_maquette_sheet(path, sheet)
                    if not result.rows:
                        errors.append(f"{sheet}: aucune ligne reconnue")
                        continue
                    rows = enrich_maquette_rows_mne_codes(
                        result.rows, level=level, track=track
                    )
                    c, u, s, e = import_maquette_row_dicts(
                        self.repo,
                        rows,
                        update_existing=update_existing,
                        template_id=tpl_id,
                        attach_to_template=True,
                    )
                    total_created += c
                    total_updated += u
                    total_skipped += s
                    errors.extend([f"{sheet}: {x}" for x in e])
                except Exception as exc:
                    errors.append(f"{sheet}: {exc}")

            self.refresh()
            for cb in self.refresh_callbacks:
                cb()
            msg = (
                f"Maquettes créées : {created_tpl}\n"
                f"Cours créés : {total_created}\nMis à jour : {total_updated}\nIgnorés : {total_skipped}"
            )
            if errors:
                msg += "\n\nErreurs :\n" + "\n".join(errors[:12])
                if len(errors) > 12:
                    msg += f"\n… ({len(errors) - 12} de plus)"
            QMessageBox.information(self, "Import maquette", msg)
            return

        # Mode 1 onglet
        try:
            result = load_maquette_sheet(path, dlg.sheet_name())
        except Exception as exc:
            QMessageBox.critical(self, "Import maquette", str(exc))
            return
        if not result.rows:
            QMessageBox.warning(
                self,
                "Import maquette",
                "Aucune ligne de cours reconnue (en-têtes « Code » / « Enseignements » introuvables ?).",
            )
            return
        attach = dlg.should_attach_to_maquette()
        tpl_for_attach = dlg.template_id() if attach else None

        # Ergonomie: si aucune maquette n’existe/sélectionnée, on en crée une automatiquement
        # et on y attache l’import, sinon l’utilisateur “ne voit rien” dans l’onglet Maquette.
        if tpl_for_attach is None:
            p = Path(path)
            academic_year = extract_academic_year_from_path(p)
            stem_up = p.stem.upper()
            level = "M1" if "M1" in stem_up else ("M2" if "M2" in stem_up else "")
            try:
                track = str((result.track_by_sheet or {}).get(result.sheet_title, "") or "").strip()
            except Exception:
                track = ""
            name = p.stem
            if academic_year or level or track:
                name = f"{academic_year or ''} — {level} {track}".strip(" —").strip()
            new_id = self.repo.add_template(name, level, track, academic_year, version="1")
            self.refresh()
            if int(new_id) in self._template_ids:
                self.maquette_list.setCurrentRow(self._template_ids.index(int(new_id)))
            tpl_for_attach = int(new_id)
            attach = True

        created, updated, skipped, errors = import_maquette_row_dicts(
            self.repo,
            result.rows,
            update_existing=update_existing,
            template_id=tpl_for_attach,
            attach_to_template=attach,
        )
        self.refresh_maquette_courses()
        for cb in self.refresh_callbacks:
            cb()
        msg = (
            f"Créés : {created}\nMis à jour : {updated}\nIgnorés (déjà présents, sans MAJ) : {skipped}\n"
            f"Feuille : {result.sheet_title}"
        )
        if attach and tpl_for_attach:
            msg += "\n\nLes nouveaux cours ont été ajoutés à la maquette courante (ordre en fin de liste)."
        if errors:
            msg += "\n\nErreurs :\n" + "\n".join(errors[:12])
            if len(errors) > 12:
                msg += f"\n… ({len(errors) - 12} de plus)"
        QMessageBox.information(self, "Import maquette", msg)

    def export_maquette(self) -> None:
        try:
            import openpyxl  # noqa: F401
        except Exception as exc:
            QMessageBox.critical(self, "Missing dependency", f"openpyxl is required.\n\n{exc}")
            return
        tid = self.current_template_id()
        if tid is None:
            QMessageBox.information(self, "Export", "Sélectionnez une maquette dans la liste.")
            return
        t = self.repo.list_templates()
        meta = next((x for x in t if int(x["id"]) == tid), None)
        name = (meta or {}).get("name", "maquette")
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(name))[:40]
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter la maquette",
            str(Path.home() / "Documents" / f"{safe}_maquette.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path = path + ".xlsx"
        try:
            sheet = (meta or {}).get("track") or "Maquette"
            export_template_to_maquette_xlsx(self.repo, tid, path, sheet_title=str(sheet)[:31])
            QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))

    def add_ue_to_maquette(self) -> None:
        tid = self.current_template_id()
        if tid is None:
            QMessageBox.warning(self, "Maquette", "Sélectionnez une maquette.")
            return
        courses = self.repo.list_courses()
        if not courses:
            QMessageBox.warning(self, "Maquette", "Aucun cours en base. Importez une maquette ou créez un cours dans l’onglet Cours.")
            return
        dlg = AddCourseToTemplateDialog(courses, self)
        if dlg.exec():
            try:
                self.repo.add_course_to_template(
                    tid,
                    int(dlg.course.currentData()),
                    dlg.block_name.text().strip(),
                    dlg.global_coefficient.value(),
                    dlg.display_order.value(),
                )
                self.refresh_maquette_courses()
                for cb in self.refresh_callbacks:
                    cb()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def remove_ue_from_maquette(self) -> None:
        tid = self.current_template_id()
        cids = self.selected_course_ids()
        if tid is None or not cids:
            QMessageBox.information(self, "Maquette", "Sélectionnez une ou plusieurs lignes dans le tableau des UE.")
            return
        reply = QMessageBox.question(
            self,
            "Retirer l’UE",
            f"Retirer {len(cids)} UE de la maquette ? "
            "(Les cours restent dans la bibliothèque ; les notes liées à la maquette peuvent être affectées.)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            for cid in cids:
                self.repo.remove_course_from_template(tid, int(cid))
            self.refresh_maquette_courses()
            for cb in self.refresh_callbacks:
                cb()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def edit_course(self) -> None:
        cid = self.selected_course_id()
        if cid is None:
            QMessageBox.information(self, "Edit course", "Sélectionnez une UE dans le tableau.")
            return
        course = self.repo.get_course(cid)
        if course is None:
            return
        dlg = CourseDialog(self, course=course)
        if dlg.exec():
            try:
                kw = _course_fields_from_dialog(dlg)
                self.repo.update_course(int(course["id"]), dlg.code.text().strip(), **kw)
                self.refresh_maquette_courses()
                for cb in self.refresh_callbacks:
                    cb()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def edit_placement(self) -> None:
        tid = self.current_template_id()
        cid = self.selected_course_id()
        if tid is None or cid is None:
            QMessageBox.information(self, "Placement", "Sélectionnez une UE dans le tableau.")
            return
        rows = self.repo.list_template_courses(tid)
        row = next((r for r in rows if int(r["course_id"]) == cid), None)
        if row is None:
            return
        dlg = EditMaquettePlacementDialog(
            self,
            block_name=str(row.get("block_name") or ""),
            global_coefficient=float(row.get("global_coefficient") or 1),
            display_order=int(row.get("display_order") or 0),
            optional=int(row.get("optional") or 0),
            free_ue=int(row.get("free_ue") or 0),
        )
        if dlg.exec():
            try:
                self.repo.update_template_course_placement(
                    tid,
                    cid,
                    block_name=dlg.block_name.text().strip(),
                    global_coefficient=dlg.global_coefficient.value(),
                    display_order=dlg.display_order.value(),
                    optional=dlg.optional.value(),
                    free_ue=1 if dlg.free_ue.isChecked() else 0,
                )
                self.refresh_maquette_courses()
                for cb in self.refresh_callbacks:
                    cb()
            except Exception as exc:
                QMessageBox.critical(self, "Error", str(exc))
