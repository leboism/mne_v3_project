from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics

from ..core.mne_modules import course_ue_code
from ..gui.widgets import make_actions_toolbar

_NOTE_COL_WIDTH = 76
_ID_COL_WIDTHS = (88, 100, 100)
_HEADER_PAD = 10
_HEADER_MAX_LINES = 2


def _fmt_note(x: float | None) -> str:
    return "—" if x is None else f"{float(x):.3f}"


def _header_usable_width(column_width: int) -> int:
    return max(28, int(column_width) - _HEADER_PAD)


def _truncate_to_width(text: str, fm: QFontMetrics, width: int) -> str:
    s = text
    while s and fm.horizontalAdvance(s + "…") > width:
        s = s[:-1]
    return (s + "…") if s else "…"


def _wrap_header_lines(
    text: str,
    *,
    fm: QFontMetrics,
    column_width: int = _NOTE_COL_WIDTH,
    max_lines: int = _HEADER_MAX_LINES,
) -> str:
    """Découpe un libellé sur 1–2 lignes selon la largeur réelle de la colonne."""
    raw = " ".join((text or "").split())
    if not raw:
        return ""
    usable = _header_usable_width(column_width)
    if fm.horizontalAdvance(raw) <= usable:
        return raw

    words = raw.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if current and fm.horizontalAdvance(candidate) > usable:
            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                lines.append(_truncate_to_width(current, fm, usable))
                return "\n".join(lines[:max_lines])
        else:
            current = candidate
    if current:
        if len(lines) >= max_lines:
            lines[-1] = _truncate_to_width(lines[-1], fm, usable)
        else:
            lines.append(current)
    return "\n".join(lines[:max_lines])


def _short_block_label(block_name: str) -> str:
    raw = (block_name or "").strip()
    if not raw:
        return "Bloc"
    if ":" in raw:
        return raw.split(":", 1)[0].strip()
    return raw


def _mne_code_header_lines(mne: str, *, fm: QFontMetrics, column_width: int) -> str:
    code = (mne or "").strip()
    if not code:
        return ""
    usable = _header_usable_width(column_width)
    if fm.horizontalAdvance(code) <= usable:
        return code
    parts = code.split("-")
    if len(parts) >= 3:
        line1 = "-".join(parts[:-1])
        line2 = parts[-1]
        if fm.horizontalAdvance(line1) <= usable and fm.horizontalAdvance(line2) <= usable:
            return f"{line1}\n{line2}"
    return _wrap_header_lines(code.replace("-", " "), fm=fm, column_width=column_width)


def _course_header_labels(
    course: dict[str, Any],
    *,
    fm: QFontMetrics,
    column_width: int = _NOTE_COL_WIDTH,
) -> tuple[str, str]:
    """Libellé court (1–2 lignes) + infobulle avec l'intitulé complet."""
    name = str(course.get("name") or "").strip()
    code = str(course.get("code") or "").strip()
    mne = course_ue_code(course)
    opt = int(course.get("optional") or 0)
    is_free = int(course.get("free_ue") or 0)
    tag = " (opt.)" if opt else (" (libre)" if is_free else "")

    tooltip_parts = [p for p in (mne, name, f"Apogée {code}" if code else "") if p]
    tooltip = "\n".join(tooltip_parts) + (f"\n{tag.strip()}" if tag.strip() else "")

    if mne:
        display = _mne_code_header_lines(mne, fm=fm, column_width=column_width)
    else:
        display = _wrap_header_lines(name or code or "UE", fm=fm, column_width=column_width)

    if tag.strip():
        tag_short = "opt." if opt else "libre"
        lines = display.split("\n")
        if len(lines) >= _HEADER_MAX_LINES:
            lines[-1] = _truncate_to_width(f"{lines[-1]} · {tag_short}", fm, _header_usable_width(column_width))
            display = "\n".join(lines)
        else:
            display = f"{display}\n{tag_short}"

    return display, tooltip.strip()


def _color_for_grade_20(v: float | None) -> QColor | None:
    """Vert > 10, orange de 7 à 10 inclus, rouge < 7 ; pas de fond si note absente."""
    if v is None:
        return None
    x = float(v)
    if x > 10.0:
        return QColor(198, 239, 206)
    if x >= 7.0:
        return QColor(255, 224, 178)
    return QColor(255, 205, 210)


def _ue_uses_s2(d: dict[str, Any]) -> bool:
    return bool(d.get("use_s2") if "use_s2" in d else d.get("sent_s2"))


def _ue_session_numeric(row: dict[str, Any], course_id: int, mode: str) -> float | None:
    d = (row.get("ue_detail") or {}).get(course_id) or {}
    if mode == "s2":
        base = d.get("s2") if _ue_uses_s2(d) else d.get("s1")
    else:
        base = d.get("s1")
    return float(base) if base is not None else None


def _ue_cell_text(row: dict[str, Any], course_id: int, mode: str) -> str:
    """Texte affiché pour une UE : statut (DEF, ABJ, …) ou note chiffrée."""
    d = (row.get("ue_detail") or {}).get(course_id) or {}
    display = str(d.get("display") or "").strip()
    if display:
        return display
    if d.get("ects_validated"):
        return "VAL"
    return _fmt_note(_ue_total_numeric(row, course_id, mode))


def _ue_total_numeric(row: dict[str, Any], course_id: int, mode: str) -> float | None:
    d = (row.get("ue_detail") or {}).get(course_id) or {}
    if mode == "s2":
        base = d.get("s2") if _ue_uses_s2(d) else d.get("s1")
    else:
        base = d.get("s1")
    jp = d.get("jury")
    if base is None:
        if jp is not None and abs(float(jp)) > 1e-12:
            return float(jp)
        return None
    return float(base) + float(jp or 0.0)


class ResultsTab(QWidget):
    def __init__(self, repo):
        super().__init__()
        self.repo = repo
        self.template_ids: list[int] = []
        self._current_template_id: int | None = None
        self._col_metas: list[dict[str, Any] | None] = []
        self._expanded_blocks: set[str] = set()
        self._block_avg_col_index: dict[int, str] = {}
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        filters = QGridLayout()
        filters.setHorizontalSpacing(8)
        self.template_combo = QComboBox()
        self.template_combo.currentIndexChanged.connect(self._on_template_changed_results)
        self.session_combo = QComboBox()
        self.session_combo.addItem("Session 1", "s1")
        self.session_combo.addItem("Session 2", "s2")
        self.session_combo.currentIndexChanged.connect(self.refresh_table)
        self.student_combo = QComboBox()
        self.student_combo.setMinimumWidth(220)
        self.student_combo.currentIndexChanged.connect(self.refresh_table)
        filters.addWidget(QLabel("Maquette :"), 0, 0)
        filters.addWidget(self.template_combo, 0, 1)
        filters.addWidget(QLabel("Session :"), 0, 2)
        filters.addWidget(self.session_combo, 0, 3)
        filters.addWidget(QLabel("Étudiant :"), 1, 0)
        filters.addWidget(self.student_combo, 1, 1, 1, 3)
        layout.addLayout(filters)
        layout.addLayout(
            make_actions_toolbar(
                self,
                primary=[("Transcript (sélection)…", self._export_transcript_selection)],
                menu_sections=[
                    [("Synchroniser les inscriptions", self.compute_and_refresh)],
                    [("Exporter CSV…", self.export_csv)],
                    [
                        ("Transcripts provisoires (tous)…", self._export_transcripts_all_provisional),
                        ("Transcript provisoire (sélection)…", self._export_transcript_selection),
                    ],
                    [
                        ("Transcripts finaux (tous)…", self._export_transcripts_all_final),
                        ("Transcript final (sélection)…", self._export_transcript_final_selection),
                    ],
                ],
            ).layout
        )
        self.hint = QLabel(
            "Vue synthétique : notes retenues (session choisie + points de délibération le cas échéant) "
            "et moyennes de bloc / année. Transcripts PDF : un fichier par étudiant "
            "(filtre « Tous les étudiants » ou menu « tous ») ; provisoire (session affichée) ou final "
            "(jury final requis, notes S2 ; mention validée en délibération finale ; "
            "pas de classement si 2ᵉ session). "
            "Envois en 2ᵉ session, points de délibération détaillés et PV : "
            "onglet « PV & délibérations ». "
            "Couleurs : vert > 10, orange 7–10, rouge < 7 ; fond de bloc = validation (moy. > 10, pas de < 7 non gardé). "
            "Statuts UE : DEF (défaillant), ABJ (absence justifiée), NEUT (neutralisée), "
            "VAL (validée sans note). « Garder » en saisie = neutralisation d’une épreuve ; "
            "dérogation seuil 7 = case « Valider (seuil 7) » en délibération interactive. "
            "Cliquez sur l'en-tête d'un bloc (▶/▼) pour afficher ou masquer le détail des UE."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self.hint)
        self.table = QTableWidget()
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.sectionClicked.connect(self._on_header_clicked)
        layout.addWidget(self.table)
        self.refresh_templates()

    def _on_template_changed_results(self, _idx: int) -> None:
        self._expanded_blocks.clear()
        self._repopulate_student_filter()
        self.refresh_table()

    def _on_header_clicked(self, col: int) -> None:
        block = self._block_avg_col_index.get(col)
        if block is None:
            return
        if block in self._expanded_blocks:
            self._expanded_blocks.discard(block)
        else:
            self._expanded_blocks.add(block)
        self.refresh_table()

    def _repopulate_student_filter(self) -> None:
        self.student_combo.blockSignals(True)
        try:
            prev_sid = self.student_combo.currentData()
            self.student_combo.clear()
            self.student_combo.addItem("Tous les étudiants", None)
            tid = self.template_combo.currentData()
            if tid is not None:
                from ..services.lookups import student_combo_label

                for s in self.repo.list_students_for_template(int(tid)):
                    self.student_combo.addItem(student_combo_label(s), int(s["id"]))
            if prev_sid is not None:
                idx = self.student_combo.findData(prev_sid)
                if idx >= 0:
                    self.student_combo.setCurrentIndex(idx)
                else:
                    self.student_combo.setCurrentIndex(0)
            elif self.student_combo.count() > 0:
                self.student_combo.setCurrentIndex(0)
        finally:
            self.student_combo.blockSignals(False)

    def _filter_rows_for_student(
        self, data: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        sid = self.student_combo.currentData()
        if sid is None:
            return data
        try:
            want = int(sid)
        except (TypeError, ValueError):
            return data
        return [r for r in data if int(r.get("student_id") or 0) == want]

    def _brush_for_results_cell(
        self, meta: dict[str, Any] | None, row: dict[str, Any], mode: str
    ) -> QBrush | None:
        if not meta:
            return None
        t = meta.get("type")
        if t == "blk_avg":
            bk = str(meta.get("block_name") or "")
            return QBrush(self._color_for_block_row(row, bk, mode))
        if t == "ue_note":
            v = _ue_session_numeric(row, int(meta["course_id"]), mode)
            c = _color_for_grade_20(v)
            return QBrush(c) if c else None
        if t == "ue_total":
            v = _ue_total_numeric(row, int(meta["course_id"]), mode)
            c = _color_for_grade_20(v)
            return QBrush(c) if c else None
        if t == "year_avg":
            c = _color_for_grade_20(row.get("global_average"))
            return QBrush(c) if c else None
        if t == "year_total":
            c = _color_for_grade_20(row.get("global_with_jury"))
            return QBrush(c) if c else None
        return None

    def _color_for_block_row(self, row: dict[str, Any], block_name: str, mode: str) -> QColor:
        """Fond bloc : vert seulement si règles de validation (moy. > 10, pas de < 7 non gardées)."""
        tid = self._current_template_id
        if tid is None:
            return QColor(255, 205, 210)
        sid = int(row.get("student_id") or 0)
        avg = (row.get("blocks") or {}).get(block_name)
        if self.repo.block_is_validated(
            sid, int(tid), block_name, view_session=mode, block_average=avg
        ):
            return QColor(198, 239, 206)
        return QColor(255, 205, 210)

    def compute_and_refresh(self) -> None:
        template_id = self.template_combo.currentData()
        if template_id is None:
            self.refresh_table()
            return

        meta = next((x for x in self.repo.list_templates() if int(x["id"]) == int(template_id)), None) or {}
        ay = str(meta.get("academic_year") or "").strip()

        matched = created = 0
        if ay:
            try:
                matched, created = self.repo.sync_enrollments_for_academic_year(ay)
            except Exception as exc:
                QMessageBox.warning(self, "Résultats", f"Erreur pendant la synchronisation :\n{exc}")

        self.refresh_templates()

        if ay:
            QMessageBox.information(
                self,
                "Résultats",
                f"Synchronisation des inscriptions ({ay}) terminée.\n"
                f"- Étudiants correspondants : {matched}\n"
                f"- Nouvelles inscriptions créées : {created}",
            )

    def refresh_templates(self) -> None:
        self.template_combo.blockSignals(True)
        try:
            prev = self.template_combo.currentData()
            templates = self.repo.list_templates()
            self.template_ids = [t["id"] for t in templates]
            self.template_combo.clear()
            for t in templates:
                lv, tr = (t.get("level") or "").strip(), (t.get("track") or "").strip()
                suffix = f" — {lv} {tr}" if lv or tr else ""
                self.template_combo.addItem(f"{t['name']} [{t['academic_year']}]{suffix}", t["id"])
            if prev is not None:
                idx = self.template_combo.findData(prev)
                if idx >= 0:
                    self.template_combo.setCurrentIndex(idx)
            elif self.template_combo.count() > 0:
                self.template_combo.setCurrentIndex(0)
        finally:
            self.template_combo.blockSignals(False)
        self._repopulate_student_filter()
        self.refresh_table()

    def _build_columns(
        self, template_id: int, *, show_all_ue: bool = False
    ) -> tuple[list[str], list[Callable[[dict[str, Any]], str]]]:
        mode = str(self.session_combo.currentData() or "s1")
        from ..services.lookups import student_transcript_number

        headers: list[str] = ["N° I.N.E.", "Nom", "Prénom"]
        cols: list[Callable[[dict[str, Any]], str]] = [
            lambda row: student_transcript_number(row),
            lambda row: str(row["last_name"] or ""),
            lambda row: str(row["first_name"] or ""),
        ]
        metas: list[dict[str, Any] | None] = [None, None, None]
        block_avg_col_index: dict[int, str] = {}

        blocks_with_courses = self.repo.list_template_blocks_with_courses(int(template_id))
        fm = QFontMetrics(self.table.horizontalHeader().font())

        for bk, clist in blocks_with_courses:
            expanded = show_all_ue or bk in self._expanded_blocks
            arrow = "▼" if expanded else "▶"
            col_idx = len(headers)
            blk_short = _short_block_label(bk)
            blk_header = _wrap_header_lines(
                f"{arrow} {blk_short}",
                fm=fm,
                column_width=_NOTE_COL_WIDTH,
            )
            headers.append(blk_header)
            block_avg_col_index[col_idx] = bk

            def blk_avg(row: dict[str, Any], _bk: str = bk) -> str:
                return _fmt_note((row.get("blocks") or {}).get(_bk))

            cols.append(blk_avg)
            metas.append({"type": "blk_avg", "block_name": bk, "tooltip": bk})

            if expanded:
                for c in clist:
                    cid = int(c["course_id"])
                    label, tip = _course_header_labels(c, fm=fm, column_width=_NOTE_COL_WIDTH)
                    headers.append(label)

                    def ue_effective(row: dict[str, Any], _cid: int = cid, _mode: str = mode) -> str:
                        return _ue_cell_text(row, _cid, _mode)

                    cols.append(ue_effective)
                    metas.append({"type": "ue_total", "course_id": cid, "tooltip": tip})

        headers.append(_wrap_header_lines("Moy.\nannée", fm=fm, column_width=_NOTE_COL_WIDTH))
        cols.append(lambda row: _fmt_note(row.get("global_with_jury")))
        metas.append({"type": "year_total"})

        self._col_metas = metas
        if not show_all_ue:
            self._block_avg_col_index = block_avg_col_index
        return headers, cols

    def _apply_header_items(self, headers: list[str]) -> None:
        header = self.table.horizontalHeader()
        for c, label in enumerate(headers):
            item = QTableWidgetItem(label)
            item.setTextAlignment(int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter))
            meta = self._col_metas[c] if c < len(self._col_metas) else None
            tip = str((meta or {}).get("tooltip") or "").strip()
            if not tip and meta and meta.get("type") == "blk_avg":
                tip = str(meta.get("block_name") or "")
            if tip:
                item.setToolTip(tip)
            self.table.setHorizontalHeaderItem(c, item)

    def _apply_column_layout(self, headers: list[str]) -> None:
        header = self.table.horizontalHeader()
        max_header_lines = max((h.count("\n") + 1 for h in headers), default=1)
        line_h = header.fontMetrics().lineSpacing()
        header.setMinimumHeight(line_h * max_header_lines + 12)
        header.setDefaultSectionSize(_NOTE_COL_WIDTH)

        for c in range(self.table.columnCount()):
            meta = self._col_metas[c] if c < len(self._col_metas) else None
            if c < len(_ID_COL_WIDTHS):
                header.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
                self.table.setColumnWidth(c, _ID_COL_WIDTHS[c])
            elif meta and meta.get("type") in ("blk_avg", "ue_total", "year_total"):
                header.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
                self.table.setColumnWidth(c, _NOTE_COL_WIDTH)
            else:
                header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)

    def refresh_table(self) -> None:
        template_id = self.template_combo.currentData()
        if template_id is None:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        self._current_template_id = int(template_id)
        mode = str(self.session_combo.currentData() or "s1")
        data = self.repo.get_student_result_summary(int(template_id), view_session=mode)
        data = self._filter_rows_for_student(data)
        headers, extractors = self._build_columns(int(template_id))
        self.table.blockSignals(True)
        try:
            self.table.setColumnCount(len(headers))
            self.table.setRowCount(len(data))
            self._apply_header_items(headers)
            for r, row in enumerate(data):
                sid = int(row.get("student_id") or 0)
                for c, fn in enumerate(extractors):
                    txt = fn(row)
                    it = QTableWidgetItem(txt)
                    meta = self._col_metas[c] if c < len(self._col_metas) else None
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if meta and meta.get("type") in ("blk_avg", "ue_total", "year_total"):
                        it.setTextAlignment(
                            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
                        )
                    br = self._brush_for_results_cell(meta, row, mode)
                    if br is not None:
                        it.setBackground(br)
                    self.table.setItem(r, c, it)
            self._apply_column_layout(headers)
        finally:
            self.table.blockSignals(False)

    def _check_reportlab(self) -> bool:
        try:
            import reportlab  # noqa: F401
        except ImportError:
            QMessageBox.warning(
                self,
                "PDF",
                "Le module « reportlab » est requis pour les transcripts PDF.\n"
                "Installez-le dans l'environnement Python du projet.",
            )
            return False
        return True

    def _template_id(self) -> int | None:
        tid = self.template_combo.currentData()
        return int(tid) if tid is not None else None

    def _student_ids_for_export(self, *, force_all: bool = False) -> list[int]:
        tid = self._template_id()
        if tid is None:
            QMessageBox.information(self, "Transcript", "Sélectionnez une maquette.")
            return []
        enrolled = [int(s["id"]) for s in self.repo.list_students_for_template(int(tid))]
        if not enrolled:
            QMessageBox.information(self, "Transcript", "Aucun étudiant inscrit sur cette maquette.")
            return []
        if force_all:
            return enrolled
        sid = self.student_combo.currentData()
        if sid is not None:
            return [int(sid)]
        return enrolled

    def _export_transcript_selection(self) -> None:
        self._export_transcripts(final=False, force_all=False)

    def _export_transcript_final_selection(self) -> None:
        self._export_transcripts(final=True, force_all=False)

    def _export_transcripts_all_provisional(self) -> None:
        self._export_transcripts(final=False, force_all=True)

    def _export_transcripts_all_final(self) -> None:
        self._export_transcripts(final=True, force_all=True)

    def _export_transcripts(self, *, final: bool, force_all: bool) -> None:
        if not self._check_reportlab():
            return
        tid = self._template_id()
        if tid is None:
            return
        if final and not self.repo.has_final_jury_session(int(tid)):
            QMessageBox.warning(
                self,
                "Transcript final",
                "Le transcript définitif n'est disponible qu'après création d'une délibération "
                "« Finale » dans l'onglet Jury (PV & délibérations).",
            )
            return
        sids = self._student_ids_for_export(force_all=force_all)
        if not sids:
            return
        from ..services.jury_reports import (
            export_transcripts_batch,
            transcript_default_filename,
            write_institutional_transcript_pdf,
        )

        vs = str(self.session_combo.currentData() or "s1")
        if len(sids) == 1:
            stu = self.repo.get_student(int(sids[0])) or {}
            tpl = next((t for t in self.repo.list_templates() if int(t["id"]) == int(tid)), {}) or {}
            default = transcript_default_filename(
                stu,
                level=str(tpl.get("level") or ""),
                track=str(tpl.get("track") or ""),
                final=final,
            )
            path, _ = QFileDialog.getSaveFileName(self, "Enregistrer le transcript", str(Path.home() / default), "PDF (*.pdf)")
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            try:
                write_institutional_transcript_pdf(
                    self.repo,
                    template_id=int(tid),
                    student_id=int(sids[0]),
                    path=path,
                    final=final,
                    view_session=vs,
                )
            except Exception as exc:
                QMessageBox.critical(self, "PDF", str(exc))
                return
            QMessageBox.information(self, "PDF", f"Transcript créé :\n{path}")
            return

        dest = QFileDialog.getExistingDirectory(
            self, "Dossier des transcripts", str(Path.home())
        )
        if not dest:
            return
        try:
            created, errors = export_transcripts_batch(
                self.repo,
                template_id=int(tid),
                student_ids=sids,
                dest_dir=dest,
                final=final,
                view_session=vs,
            )
        except Exception as exc:
            QMessageBox.critical(self, "PDF", str(exc))
            return
        msg = f"{len(created)} transcript(s) créé(s) dans :\n{dest}"
        if errors:
            preview = "\n".join(f"• {name}: {err}" for name, err in errors[:8])
            if len(errors) > 8:
                preview += f"\n… et {len(errors) - 8} autre(s) erreur(s)"
            msg += f"\n\n{len(errors)} échec(s) :\n{preview}"
        QMessageBox.information(self, "PDF", msg)

    def export_csv(self) -> None:
        template_id = self.template_combo.currentData()
        if template_id is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter CSV", str(Path.home() / "results.csv"), "CSV (*.csv)"
        )
        if not path:
            return
        mode = str(self.session_combo.currentData() or "s1")
        data = self.repo.get_student_result_summary(int(template_id), view_session=mode)
        data = self._filter_rows_for_student(data)
        headers, extractors = self._build_columns(int(template_id), show_all_ue=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([h.replace("\n", " ") for h in headers])
            for row in data:
                writer.writerow([fn(row) for fn in extractors])
