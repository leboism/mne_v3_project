"""Onglet Emploi du temps — consultation et import Excel secrétariat."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..gui.widgets import fill_table, make_actions_toolbar
from ..services.timetable_import import load_timetable_workbook

_DAY_FR = {
    "Monday": "Lundi",
    "Tuesday": "Mardi",
    "Wednesday": "Mercredi",
    "Thursday": "Jeudi",
    "Friday": "Vendredi",
}
_M1_PERIOD_CODES = ("S1", "S2")
_M2_PERIOD_CODES = ("S1", "S2", "S3")
_PERIOD_LABELS_M1 = {
    "S1": "Période S1 — sept. → janv.",
    "S2": "Période S2 — janv. → juin",
}
_PERIOD_LABELS_M2 = {
    "S1": "Période S1 — sept. → déc.",
    "S2": "Période S2 — janv. → juin",
    "S3": "Période été — juil. → août",
}
_WEEK_WINDOW_SIZE_M1 = 18
_WEEK_WINDOW_SIZE_M2 = 10


def _format_timetable_date(value: str) -> str:
    """Affiche une date de grille (ISO ou datetime Excel) en jj/mm/aaaa."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if " " in raw:
        raw = raw.split(" ", 1)[0].strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{d:02d}/{mo:02d}/{y}"
    try:
        if isinstance(value, datetime):
            dt = value.date()
        elif isinstance(value, date):
            dt = value
        else:
            return raw
        return f"{dt.day:02d}/{dt.month:02d}/{dt.year}"
    except (TypeError, ValueError):
        return raw


def _week_column_label(w: dict) -> str:
    num = int(w.get("week_number") or 0)
    label = str(w.get("week_label") or f"Week {num}").strip()
    mon = _format_timetable_date(str(w.get("monday_date") or ""))
    fri = _format_timetable_date(str(w.get("friday_date") or ""))
    if mon and fri:
        return f"{label} — {mon} → {fri}"
    if mon:
        return f"{label} — à partir du {mon}"
    return label


def _period_codes_for_level(level: str) -> tuple[str, ...]:
    return _M2_PERIOD_CODES if (level or "").strip().upper() == "M2" else _M1_PERIOD_CODES


def _period_label_for_level(level: str, period: str) -> str:
    code = (period or "S1").strip().upper()
    labels = _PERIOD_LABELS_M2 if (level or "").strip().upper() == "M2" else _PERIOD_LABELS_M1
    return labels.get(code, code)


def _parse_grid_date(value: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if " " in raw:
        raw = raw.split(" ", 1)[0].strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
    except TypeError:
        return None
    return None


def _academic_year_end_july(academic_year: str) -> date | None:
    m = re.match(r"^(\d{4})\s*[-–]\s*(\d{4})$", str(academic_year or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(2)), 7, 1)
    except ValueError:
        return None


def _academic_year_end_august(academic_year: str) -> date | None:
    m = re.match(r"^(\d{4})\s*[-–]\s*(\d{4})$", str(academic_year or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(2)), 8, 31)
    except ValueError:
        return None


def _filter_weeks_for_display(
    weeks: list[dict],
    *,
    level: str,
    period: str,
    academic_year: str,
) -> list[dict]:
    """M2 : fenêtre S2 (janv.–juin) et S3 (juil.–août) sur les créneaux importés en période S2."""
    lv = (level or "").strip().upper()
    code = (period or "").strip().upper()
    if lv != "M2" or code not in {"S2", "S3"}:
        return weeks
    july1 = _academic_year_end_july(academic_year)
    aug_end = _academic_year_end_august(academic_year)
    if july1 is None:
        return weeks
    out: list[dict] = []
    for w in weeks:
        mon = _parse_grid_date(str(w.get("monday_date") or ""))
        if mon is None:
            if code == "S2":
                out.append(w)
            continue
        if code == "S2" and mon < july1:
            out.append(w)
        elif code == "S3" and aug_end and july1 <= mon <= aug_end:
            out.append(w)
    return out


def _week_window_size(level: str) -> int:
    return _WEEK_WINDOW_SIZE_M2 if (level or "").strip().upper() == "M2" else _WEEK_WINDOW_SIZE_M1


def _qcolor_from_hex(hex_rgb: str) -> QColor | None:
    h = (hex_rgb or "").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{6}", h):
        return None
    return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _text_color_for_bg(bg: QColor) -> QColor:
    lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QColor(255, 255, 255) if lum < 140 else QColor(30, 30, 30)


def _slot_display(slot: dict) -> str:
    if int((slot or {}).get("is_cancelled") or 0):
        return "ANNULÉ"
    kind = (slot.get("slot_kind") or "").strip()
    legacy = (slot.get("legacy_code") or "").strip()
    mne = (slot.get("mne_module_code") or "").strip()
    teacher = (slot.get("teacher_initials") or "").strip()
    raw = (slot.get("raw_text") or "").strip()

    if kind == "holiday":
        return "Vacances"
    if kind == "exam" and (legacy or mne):
        code = legacy or mne
        return f"{code}\n(EXAMEN)"
    code = legacy or mne
    if code:
        lines = [code]
        if mne and mne != legacy:
            lines.append(mne)
        if teacher:
            lines.append(teacher)
        return "\n".join(lines)
    if raw:
        first = raw.splitlines()[0].strip()
        return first[:80] + ("…" if len(first) > 80 else "")
    return ""


class TimetableTab(QWidget):
    def __init__(self, repo, *, default_academic_year: str = ""):
        super().__init__()
        self.repo = repo
        self.default_academic_year = (default_academic_year or "").strip()
        self._import_id: int | None = None
        self._import_academic_year = ""
        self._period = "S1"
        self._week_window_page = 0

        layout = QVBoxLayout(self)
        self.intro_label = QLabel(
            "<b>Emploi du temps</b> — grille calendrier (semaines × jours × matin/après-midi), "
            "comme le fichier secrétariat. <b>Cliquez sur une case</b> pour programmer un cours "
            "de la maquette (M1/M2). Les parcours P et C peuvent avoir des cours en parallèle ; "
            "le tronc commun ne peut pas chevaucher une spécialité. "
            "Import Excel toujours possible pour pré-remplir."
        )
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        layout.addLayout(
            make_actions_toolbar(
                self,
                primary=[
                    ("Importer Excel…", self.import_excel),
                    ("Actualiser", self.refresh),
                ],
                menu_sections=[
                    [("Initialiser calendrier vide…", self.init_empty_calendar)],
                    [("Compléter responsables M1 (PDF 2026-27)…", self.apply_m1_supervisors)],
                    [("Supprimer l'emploi du temps…", self.delete_timetable)],
                ],
            ).layout
        )

        filters = QHBoxLayout()
        self.level_combo = QComboBox()
        self.level_combo.addItems(["M1", "M2"])
        self.track_combo = QComboBox()
        self.track_combo.addItem("Physique (P)", "P")
        self.track_combo.addItem("Chimie (C)", "C")
        self.track_combo.addItem("Common (X)", "X")
        self.period_prev_btn = QPushButton("◀")
        self.period_prev_btn.setToolTip("Période précédente")
        self.period_prev_btn.setFixedWidth(36)
        self.period_label = QLabel(_period_label_for_level("M1", self._period))
        self.period_label.setMinimumWidth(240)
        self.period_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.period_next_btn = QPushButton("▶")
        self.period_next_btn.setToolTip("Période suivante")
        self.period_next_btn.setFixedWidth(36)
        period_nav = QWidget()
        period_nav_lay = QHBoxLayout(period_nav)
        period_nav_lay.setContentsMargins(0, 0, 0, 0)
        period_nav_lay.setSpacing(4)
        period_nav_lay.addWidget(self.period_prev_btn)
        period_nav_lay.addWidget(self.period_label, stretch=1)
        period_nav_lay.addWidget(self.period_next_btn)
        self.week_window_prev_btn = QPushButton("◀")
        self.week_window_prev_btn.setToolTip("Fenêtre de semaines précédente")
        self.week_window_prev_btn.setFixedWidth(36)
        self.week_window_label = QLabel("")
        self.week_window_label.setMinimumWidth(180)
        self.week_window_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.week_window_next_btn = QPushButton("▶")
        self.week_window_next_btn.setToolTip("Fenêtre de semaines suivante")
        self.week_window_next_btn.setFixedWidth(36)
        week_window_nav = QWidget()
        week_window_lay = QHBoxLayout(week_window_nav)
        week_window_lay.setContentsMargins(0, 0, 0, 0)
        week_window_lay.setSpacing(4)
        week_window_lay.addWidget(self.week_window_prev_btn)
        week_window_lay.addWidget(self.week_window_label, stretch=1)
        week_window_lay.addWidget(self.week_window_next_btn)
        self.week_window_nav = week_window_nav
        filters.addWidget(QLabel("Niveau"))
        filters.addWidget(self.level_combo)
        filters.addWidget(QLabel("Parcours"))
        filters.addWidget(self.track_combo)
        filters.addWidget(QLabel("Période"))
        filters.addWidget(period_nav)
        filters.addWidget(QLabel("Fenêtre"))
        filters.addWidget(week_window_nav)
        layout.addLayout(filters)

        self.meta_label = QLabel("")
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self.meta_label)

        splitter = QSplitter(Qt.Orientation.Vertical)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.grid_table = QTableWidget()
        self.grid_table.setWordWrap(True)
        self.grid_table.setAlternatingRowColors(False)
        self.grid_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.grid_table.verticalHeader().setVisible(False)
        self.grid_table.setMinimumHeight(320)
        self.grid_table.cellClicked.connect(self._on_grid_cell_clicked)
        self._grid_row_kinds: list[str] = []
        self._grid_slot_map: list[list[dict | None]] = []
        self._grid_weeks: list[dict] = []
        grid_scroll.setWidget(self.grid_table)
        splitter.addWidget(grid_scroll)

        hours_wrap = QWidget()
        hours_lay = QVBoxLayout(hours_wrap)
        hours_lay.setContentsMargins(0, 0, 0, 0)
        hours_hint = QLabel(
            "Synthèse heures : l’<b>écart</b> compare les heures planifiées (créneaux × 3 h 15) "
            "aux heures prévues du référentiel (onglet « Supervisors » / « Courses »). "
            "Vide si les heures prévues ne sont pas dans le fichier importé."
        )
        hours_hint.setWordWrap(True)
        hours_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        hours_lay.addWidget(hours_hint)
        self.hours_table = QTableWidget()
        self.hours_table.setAlternatingRowColors(True)
        self.hours_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.hours_table.horizontalHeader().setStretchLastSection(True)
        hours_lay.addWidget(self.hours_table)
        splitter.addWidget(hours_wrap)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        self.level_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.track_combo.currentIndexChanged.connect(self._on_scope_changed)
        self.period_prev_btn.clicked.connect(lambda: self._period_step(-1))
        self.period_next_btn.clicked.connect(lambda: self._period_step(1))
        self.week_window_prev_btn.clicked.connect(lambda: self._week_window_step(-1))
        self.week_window_next_btn.clicked.connect(lambda: self._week_window_step(1))
        self._sync_period_ui_for_level()
        self._update_period_nav_buttons()

    def refresh(self) -> None:
        year = self.default_academic_year
        level = self.level_combo.currentText().strip().upper()
        if year and level:
            try:
                self.repo.ensure_timetable_scaffold(academic_year=year, level=level)
            except Exception:
                pass
        imp = self.repo.get_latest_timetable_import(academic_year=year, level=level) if year else None
        self._import_id = int(imp["id"]) if imp else None
        self._import_academic_year = str(imp.get("academic_year") or year or "").strip() if imp else ""
        if imp:
            self.meta_label.setText(
                f"Millésime {imp['academic_year']} — {imp['level']} — "
                f"import « {imp.get('source_filename') or '—'} » "
                f"({(imp.get('imported_at') or '')[:10]})"
            )
        elif year:
            self.meta_label.setText(
                f"Calendrier {year} / {level} prêt — cliquez sur une case pour programmer un cours, "
                "ou importez l'Excel secrétariat."
            )
        else:
            self.meta_label.setText("Ouvrez un millésime depuis l'écran d'accueil.")
        self._render_grid()
        self._render_hours_summary()

    def _on_filters_changed(self) -> None:
        if self.level_combo.currentText().strip().upper() == "M2":
            if self.track_combo.currentData() != "X":
                i = self.track_combo.findData("X")
                if i >= 0:
                    self.track_combo.blockSignals(True)
                    self.track_combo.setCurrentIndex(i)
                    self.track_combo.blockSignals(False)
        self._sync_period_ui_for_level()
        self.refresh()

    def _on_scope_changed(self) -> None:
        if self.level_combo.currentText().strip().upper() == "M2":
            if self.track_combo.currentData() != "X":
                i = self.track_combo.findData("X")
                if i >= 0:
                    self.track_combo.blockSignals(True)
                    self.track_combo.setCurrentIndex(i)
                    self.track_combo.blockSignals(False)
        self._week_window_page = 0
        self._render_grid()
        self._render_hours_summary()

    def _current_level(self) -> str:
        return self.level_combo.currentText().strip().upper()

    def _sync_period_ui_for_level(self) -> None:
        level = self._current_level()
        codes = _period_codes_for_level(level)
        if self._period not in codes:
            self._period = codes[0]
        self.period_label.setText(_period_label_for_level(level, self._period))
        self._week_window_page = 0
        self._update_period_nav_buttons()

    def _period_step(self, delta: int) -> None:
        codes = _period_codes_for_level(self._current_level())
        try:
            idx = codes.index(self._period) + delta
        except ValueError:
            idx = 0
        if 0 <= idx < len(codes):
            self._set_period(codes[idx])

    def _set_period(self, period: str) -> None:
        codes = _period_codes_for_level(self._current_level())
        code = (period or "S1").strip().upper()
        if code not in codes:
            code = codes[0]
        self._period = code
        self._week_window_page = 0
        self.period_label.setText(_period_label_for_level(self._current_level(), code))
        self._update_period_nav_buttons()
        self._render_grid()
        self._render_hours_summary()

    def _update_period_nav_buttons(self) -> None:
        codes = _period_codes_for_level(self._current_level())
        try:
            idx = codes.index(self._period)
        except ValueError:
            idx = 0
        self.period_prev_btn.setEnabled(idx > 0)
        self.period_next_btn.setEnabled(idx < len(codes) - 1)

    def _db_period_for_query(self) -> str:
        """Période stockée en base (M2 S3 = créneaux importés sous S2)."""
        if self._current_level() == "M2" and self._period == "S3":
            return "S2"
        return self._period

    def _week_window_step(self, delta: int) -> None:
        self._week_window_page = max(0, self._week_window_page + delta)
        self._render_grid()

    def _update_week_window_nav(self, total_weeks: int, page_size: int, shown: int) -> None:
        if total_weeks <= page_size:
            self.week_window_nav.hide()
            self.week_window_label.clear()
            return
        self.week_window_nav.show()
        pages = max(1, (total_weeks + page_size - 1) // page_size)
        if self._week_window_page >= pages:
            self._week_window_page = pages - 1
        start = self._week_window_page * page_size + 1
        end = min(total_weeks, (self._week_window_page + 1) * page_size)
        self.week_window_label.setText(f"Semaines {start}–{end} / {total_weeks}")
        self.week_window_prev_btn.setEnabled(self._week_window_page > 0)
        self.week_window_next_btn.setEnabled(self._week_window_page < pages - 1)

    def _style_grid_cell(
        self,
        item: QTableWidgetItem,
        *,
        col: int,
        slot: dict | None = None,
        is_day_header: bool = False,
        is_time_label: bool = False,
    ) -> None:
        if is_day_header or is_time_label or col == 0:
            item.setBackground(QColor(245, 245, 245))
            item.setForeground(QColor(30, 30, 30))
            align = (
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                if col == 0
                else Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            item.setTextAlignment(int(align))
            return
        item.setTextAlignment(
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        )
        bg = _qcolor_from_hex(str((slot or {}).get("fill_color") or ""))
        if bg:
            item.setBackground(bg)
            item.setForeground(_text_color_for_bg(bg))

    def _render_grid(self) -> None:
        year = self.default_academic_year
        level = self._current_level()
        if self._import_id is None and year and level:
            try:
                self._import_id = self.repo.ensure_timetable_scaffold(
                    academic_year=year, level=level
                )
                self._import_academic_year = year
            except Exception:
                pass
        if self._import_id is None:
            fill_table(self.grid_table, [""], [])
            self._grid_row_kinds = []
            self._grid_slot_map = []
            self._grid_weeks = []
            return

        track = self.track_combo.currentData() or "P"
        level = self._current_level()
        db_period = self._db_period_for_query()
        weeks_all = self.repo.list_timetable_weeks(self._import_id, track=track, period=db_period)
        weeks_all = _filter_weeks_for_display(
            weeks_all,
            level=level,
            period=self._period,
            academic_year=self._import_academic_year or self.default_academic_year,
        )
        if not weeks_all:
            self.week_window_nav.hide()
            if level == "M2" and self._period == "S3":
                fill_table(
                    self.grid_table,
                    [""],
                    [["Aucune semaine juil.–août dans l'import — complétez le fichier Excel M2."]],
                )
            else:
                fill_table(self.grid_table, [""], [])
            return

        page_size = _week_window_size(level)
        total = len(weeks_all)
        pages = max(1, (total + page_size - 1) // page_size)
        if self._week_window_page >= pages:
            self._week_window_page = pages - 1
        start = self._week_window_page * page_size
        weeks = weeks_all[start : start + page_size]
        self._update_week_window_nav(total, page_size, len(weeks))

        slots = self.repo.list_timetable_slots_for_period(
            self._import_id,
            track=track,
            period=db_period,
        )
        visible_week_numbers = {int(w.get("week_number") or 0) for w in weeks_all}
        by_key: dict[tuple[int, str, str], dict] = {}
        day_date_by_week_day: dict[tuple[int, str], str] = {}
        for s in slots:
            if int(s.get("is_cancelled") or 0):
                continue
            week_number = int(s.get("week_number") or 0)
            if week_number not in visible_week_numbers:
                continue
            day = str(s.get("day_of_week") or "")
            time_slot = str(s.get("time_slot") or "")
            by_key[(week_number, day, time_slot)] = s
            d = _format_timetable_date(str(s.get("week_start_date") or ""))
            if d:
                day_date_by_week_day[(week_number, day)] = d

        headers = [""]
        for w in weeks:
            headers.append(_week_column_label(w))

        rows: list[list[str]] = []
        slot_grid: list[list[dict | None]] = []
        row_kinds: list[str] = []

        for day_en in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            day_fr = _DAY_FR.get(day_en, day_en)
            day_row = [day_fr]
            day_slots: list[dict | None] = []
            for w in weeks:
                week_number = int(w.get("week_number") or 0)
                day_slots.append(None)
                day_row.append(day_date_by_week_day.get((week_number, day_en), ""))
            rows.append(day_row)
            slot_grid.append(day_slots)
            row_kinds.append("day_header")

            for ts, ts_label in (
                ("9:00-12:15", "Matin\n9h00–12h15"),
                ("1:15-4:30", "Après-midi\n13h15–16h30"),
            ):
                slot_row: list[dict | None] = []
                row = [ts_label]
                for w in weeks:
                    week_number = int(w.get("week_number") or 0)
                    slot = by_key.get((week_number, day_en, ts))
                    slot_row.append(slot)
                    row.append(_slot_display(slot) if slot else "")
                rows.append(row)
                slot_grid.append(slot_row)
                row_kinds.append("time_slot")

        self._grid_weeks = list(weeks)
        self._grid_row_kinds = list(row_kinds)
        self._grid_slot_map = slot_grid

        fill_table(self.grid_table, headers, rows)
        hdr = self.grid_table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        week_col_width = 118 if level == "M2" else 110
        for c in range(1, self.grid_table.columnCount()):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.grid_table.setColumnWidth(c, week_col_width)
        min_width = 120 + week_col_width * max(1, len(weeks))
        self.grid_table.setMinimumWidth(min_width)

        for r in range(self.grid_table.rowCount()):
            kind = row_kinds[r] if r < len(row_kinds) else "time_slot"
            self.grid_table.setRowHeight(r, 34 if kind == "day_header" else 72)
            for c in range(self.grid_table.columnCount()):
                item = self.grid_table.item(r, c)
                if not item:
                    continue
                slot = None
                if c > 0 and r < len(slot_grid):
                    slot = slot_grid[r][c - 1]
                self._style_grid_cell(
                    item,
                    col=c,
                    slot=slot,
                    is_day_header=kind == "day_header",
                    is_time_label=kind == "time_slot" and c == 0,
                )
                if item.text().strip():
                    item.setToolTip(item.text())

    def _render_hours_summary(self) -> None:
        headers = [
            "Code EdT",
            "Code MNE",
            "Cours",
            "Heures prévues",
            "Créneaux",
            "Heures planifiées",
            "Écart",
        ]
        if self._import_id is None:
            fill_table(self.hours_table, headers, [])
            return
        track = self.track_combo.currentData() or "P"
        period = self._db_period_for_query()
        week_nums: set[int] | None = None
        if self._current_level() == "M2" and self._period in {"S2", "S3"}:
            weeks = _filter_weeks_for_display(
                self.repo.list_timetable_weeks(self._import_id, track=track, period="S2"),
                level="M2",
                period=self._period,
                academic_year=self._import_academic_year or self.default_academic_year,
            )
            week_nums = {int(w.get("week_number") or 0) for w in weeks}
        summary = self.repo.summarize_timetable_hours(
            self._import_id,
            track=track,
            period=period,
            week_numbers=week_nums,
        )
        rows = []
        for item in summary:
            delta = item.get("hours_delta")
            if delta is not None:
                delta_s = f"{delta:+.1f} h"
            elif float(item.get("hours_expected") or 0) <= 0:
                delta_s = "— (pas d’heures prévues)"
            else:
                delta_s = ""
            rows.append(
                [
                    item.get("legacy_code") or "",
                    item.get("mne_module_code") or "",
                    item.get("course_title") or "",
                    f"{float(item.get('hours_expected') or 0):g}" if item.get("hours_expected") else "—",
                    str(item.get("slot_count") or 0),
                    f"{float(item.get('hours_scheduled') or 0):.1f}",
                    delta_s,
                ]
            )
        fill_table(self.hours_table, headers, rows)

    def delete_timetable(self) -> None:
        if self._import_id is None:
            QMessageBox.information(
                self,
                "Emploi du temps",
                "Aucun emploi du temps à supprimer pour ce millésime et ce niveau.",
            )
            return
        level = self.level_combo.currentText().strip().upper()
        imp = self.repo.get_latest_timetable_import(
            academic_year=self.default_academic_year,
            level=level,
        )
        if not imp:
            QMessageBox.information(
                self,
                "Emploi du temps",
                "Aucun emploi du temps à supprimer.",
            )
            return
        filename = imp.get("source_filename") or "—"
        imported_at = (imp.get("imported_at") or "")[:10]
        reply = QMessageBox.question(
            self,
            "Supprimer l'emploi du temps",
            f"Supprimer l'emploi du temps importé ?\n\n"
            f"Millésime {imp['academic_year']} — {imp['level']}\n"
            f"Fichier : {filename}\n"
            f"Importé le : {imported_at}\n\n"
            "Les créneaux et le référentiel heures seront effacés.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repo.delete_timetable_import(int(imp["id"]))
        except Exception as exc:
            QMessageBox.critical(self, "Emploi du temps", str(exc))
            return
        QMessageBox.information(self, "Emploi du temps", "Emploi du temps supprimé.")
        self.refresh()

    def import_excel(self) -> None:
        if not self.default_academic_year:
            QMessageBox.warning(
                self,
                "Emploi du temps",
                "Ouvrez d'abord un millésime depuis l'écran d'accueil.",
            )
            return
        try:
            import openpyxl  # noqa: F401
        except ImportError as exc:
            QMessageBox.critical(self, "Dépendance", f"openpyxl est requis.\n\n{exc}")
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer l'emploi du temps Excel",
            str(Path.home() / "Documents"),
            "Excel (*.xlsx);;Tous les fichiers (*.*)",
        )
        if not path:
            return
        try:
            result = load_timetable_workbook(
                path,
                academic_year=self.default_academic_year,
            )
            if not result.academic_year:
                result.academic_year = self.default_academic_year
            import_id = self.repo.import_timetable(result)
        except Exception as exc:
            QMessageBox.critical(self, "Import emploi du temps", str(exc))
            return

        msg = (
            f"Import terminé (id {import_id}).\n\n"
            f"• {len(result.reference_courses)} lignes référentiel (heures prévues)\n"
            f"• {len(result.slots)} créneaux grille"
        )
        if result.warnings:
            msg += "\n\nAvertissements :\n" + "\n".join(f"• {w}" for w in result.warnings)
        QMessageBox.information(self, "Emploi du temps", msg)
        self.refresh()

    def init_empty_calendar(self) -> None:
        if not self.default_academic_year:
            QMessageBox.warning(self, "Emploi du temps", "Ouvrez d'abord un millésime.")
            return
        level = self._current_level()
        try:
            iid = self.repo.ensure_timetable_scaffold(
                academic_year=self.default_academic_year, level=level
            )
        except Exception as exc:
            QMessageBox.critical(self, "Emploi du temps", str(exc))
            return
        QMessageBox.information(
            self,
            "Emploi du temps",
            f"Calendrier vide initialisé pour {self.default_academic_year} / {level} (id {iid}).\n"
            "Cliquez sur une case pour programmer un cours.",
        )
        self.refresh()

    def apply_m1_supervisors(self) -> None:
        from ..services.timetable_m1_supervisors_2026 import apply_m1_2026_supervisors

        if self._current_level() != "M1":
            QMessageBox.information(
                self,
                "Responsables",
                "Cette action concerne les UE M1 du référentiel PDF 2026-2027.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Responsables M1",
            "Mettre à jour les responsables d'UE M1 dans la base "
            "(référentiel Timetable 2026-2027 v0) ?\n\n"
            "Les e-mails déjà renseignés ne sont pas effacés.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = apply_m1_2026_supervisors(self.repo)
        except Exception as exc:
            QMessageBox.critical(self, "Responsables", str(exc))
            return
        msg = f"{result['updated']} fiche(s) UE mise(s) à jour."
        if result.get("missing"):
            msg += f"\n\nCodes MNE absents de la base : {', '.join(result['missing'][:8])}"
            if len(result["missing"]) > 8:
                msg += f" … (+{len(result['missing']) - 8})"
        QMessageBox.information(self, "Responsables", msg)

    def _on_grid_cell_clicked(self, row: int, col: int) -> None:
        if self._import_id is None or col <= 0:
            return
        if row >= len(self._grid_row_kinds) or self._grid_row_kinds[row] != "time_slot":
            return
        if row >= len(self._grid_slot_map) or col - 1 >= len(self._grid_slot_map[row]):
            return
        week_idx = col - 1
        if week_idx >= len(self._grid_weeks):
            return
        week = self._grid_weeks[week_idx]
        week_number = int(week.get("week_number") or 0)
        day_en = ""
        for i in range(row, -1, -1):
            if i < len(self._grid_row_kinds) and self._grid_row_kinds[i] == "day_header":
                day_fr = self.grid_table.item(i, 0)
                if day_fr:
                    inv = {v: k for k, v in _DAY_FR.items()}
                    day_en = inv.get(day_fr.text().strip(), "")
                break
        ts_label = self.grid_table.item(row, 0)
        time_slot = "9:00-12:15"
        if ts_label and "13" in ts_label.text():
            time_slot = "1:15-4:30"
        existing = self._grid_slot_map[row][week_idx]
        from ..gui.timetable_slot_dialog import TimetableSlotDialog

        dlg = TimetableSlotDialog(
            self.repo,
            import_id=int(self._import_id),
            academic_year=self.default_academic_year,
            level=self._current_level(),
            track=str(self.track_combo.currentData() or "P"),
            period=self._db_period_for_query(),
            week_number=week_number,
            day_of_week=day_en,
            time_slot=time_slot,
            existing_slot=existing,
            parent=self,
        )
        if dlg.exec():
            self._render_grid()
            self._render_hours_summary()
