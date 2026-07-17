"""Fenêtre interactive de délibération : notes, points jury, S2, moyennes en direct."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services.calculations import grade_below_threshold, grade_meets_minimum, round_grade_mne
from ..services.timetable_legacy import course_public_code
from ..core.parcours import OTHER_TRACK_DATA, parcours_choices
from ..services.dates import suggest_next_academic_year
from ..services.jury_reports import (
    JURY_OUTCOME_LABELS,
    TRANSCRIPT_MENTION_LABELS,
    transcript_mention_code_from_grade,
)
from ..services.grade_status import (
    STATUS_ABJ,
    STATUS_DEF,
    STATUS_NEUT,
    STATUS_VAL,
    format_grade_display,
    parse_grade_cell,
    status_skips_average,
)
from ..services.lookups import student_combo_label
from ..services.student_status import STUDENT_STATUS_GRADUATED, normalize_student_status


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    rounded = round_grade_mne(float(v))
    return "—" if rounded is None else f"{rounded:.2f}"


_COLOR_NEUTRAL = QColor(232, 234, 237)
_COLOR_PASS = QColor(198, 239, 206)
_COLOR_WARN = QColor(255, 224, 178)
_COLOR_FAIL = QColor(255, 205, 210)
_COLOR_BLOCK_HDR = QColor(245, 245, 245)


def _color_for_grade_20(v: float | None) -> QColor | None:
    if v is None:
        return None
    x = float(v)
    if x > 10.0:
        return _COLOR_PASS
    if x >= 7.0:
        return _COLOR_WARN
    return _COLOR_FAIL


def _color_for_validation_average(v: float | None) -> QColor | None:
    if v is None:
        return None
    return _COLOR_PASS if grade_meets_minimum(v, 10.0) else _COLOR_FAIL


def _color_for_ue_display(display: str) -> QColor | None:
    if display in (STATUS_DEF, STATUS_ABJ):
        return _COLOR_FAIL
    if display == STATUS_NEUT:
        return _COLOR_NEUTRAL
    if display == STATUS_VAL:
        return _COLOR_PASS
    return None


def _brush(c: QColor | None) -> QBrush | None:
    return QBrush(c) if c is not None else None


def _ue_validation_status(
    repo,
    student_id: int,
    template_id: int,
    course_id: int,
    *,
    display: str,
    total_with_jury: float | None,
    waived: bool,
    compensation_allowed: bool = True,
    compensation_status: str = "allowed",
) -> tuple[str, QColor | None]:
    """
    Statut UE en délibération (règles MNE) — sur la note d'UE (MCC + jury) :

    - ≥ 10 : validée ;
    - 7–10 : compensable si le bloc est validé (moy. bloc ≥ 10, pas d'autre UE < 7) ;
    - < 7 : non validée, sauf dérogation jury « seuil 7 » → validée.
    """
    sid, tid, cid = int(student_id), int(template_id), int(course_id)
    disp = str(display or "").strip().upper()
    if repo.has_ue_ects_validation(sid, tid, cid):
        return "Validée ✓", _COLOR_PASS
    if disp in (STATUS_DEF, STATUS_ABJ):
        return disp, _COLOR_FAIL
    if disp == STATUS_VAL:
        return "Validée ✓", _COLOR_PASS
    if disp == STATUS_NEUT:
        return "NEUT", _COLOR_NEUTRAL
    if disp:
        return disp, _color_for_ue_display(disp)
    if total_with_jury is None:
        return "—", None
    total = float(total_with_jury)
    if grade_meets_minimum(total, 10.0):
        return "Validée ✓", _COLOR_PASS
    if waived and grade_below_threshold(total, 7.0):
        return "Validée ✓", _COLOR_PASS
    if grade_meets_minimum(total, 7.0):
        status = str(compensation_status or "allowed").strip().lower()
        if status == "allowed" and compensation_allowed:
            return "Compensable", _COLOR_WARN
        if status == "incomplete":
            return "Bloc incomplet", _COLOR_NEUTRAL
        return "Non compensable", _COLOR_NEUTRAL
    return "Non validée ✗", _COLOR_FAIL


def _ue_detail_dict(row: dict[str, Any], course_id: int) -> dict[str, Any]:
    ud = row.get("ue_detail") or {}
    cid = int(course_id)
    return ud.get(cid) or ud.get(str(cid)) or {}


def _ue_session_average(
    repo,
    student_id: int,
    template_id: int,
    course_id: int,
    row: dict[str, Any],
    *,
    view_session: str,
) -> float | None:
    """Moyenne UE affichée (comme l'onglet Résultats), avec repli sur le calcul direct."""
    sid, cid = int(student_id), int(course_id)
    d = _ue_detail_dict(row, cid)
    display = str(d.get("display") or "").strip()
    if display:
        return None
    vs = str(view_session or "s1").lower()
    use_s2 = bool(d.get("use_s2")) or repo.course_uses_session2_grades(
        sid, int(template_id), cid, view_session=vs
    )
    if vs == "s2":
        base = d.get("s2") if use_s2 else d.get("s1")
    else:
        base = d.get("s1")
    if base is not None:
        return float(base)
    if use_s2:
        avg = repo.compute_course_average_s2(sid, cid)
    else:
        avg = repo.compute_course_average_s1(sid, cid)
    return float(avg) if avg is not None else None


def _ue_total_with_jury(
    row: dict[str, Any],
    course_id: int,
    *,
    view_session: str,
    jury_points: float,
) -> float | None:
    """Note UE affichée (moyenne session + jury), alignée sur l'onglet Résultats."""
    d = _ue_detail_dict(row, int(course_id))
    display = str(d.get("display") or "").strip()
    if display:
        return None
    vs = str(view_session or "s1").lower()
    use_s2 = bool(d.get("use_s2")) or bool(d.get("sent_s2"))
    if vs == "s2":
        base = d.get("s2") if use_s2 else d.get("s1")
    else:
        base = d.get("s1")
    jp = float(jury_points)
    if base is None:
        return None
    total = float(base) + jp
    # DEF compte comme 0 en moyenne : ne pas afficher 0 + jury (ex. −0,1) à la place de DEF.
    if abs(float(base)) < 1e-12 and abs(jp) > 1e-12:
        return None
    return total


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
        try:
            self.repo.repair_jury_decision_session_links(self.template_id)
        except Exception:
            pass
        self._loading = False
        self._all_student_ids: list[int] = []
        self._student_ids: list[int] = []
        self._student_issue_cache: dict[int, bool] = {}
        self._block_spinboxes: dict[str, QDoubleSpinBox] = {}
        self._course_spinboxes: dict[int, QDoubleSpinBox] = {}
        self._s2_checks: dict[int, QCheckBox] = {}
        self._floor_waiver_checks: dict[int, QCheckBox] = {}
        self._block_validation_waiver_checks: dict[str, QCheckBox] = {}
        self._grade_items: dict[int, QTableWidgetItem] = {}
        self._grade_item_meta: dict[int, tuple[int, int]] = {}
        self._ue_table: QTableWidget | None = None
        self._assessment_table: QTableWidget | None = None
        self._ue_row_course: dict[int, int] = {}
        self._ue_block_rows: set[int] = set()
        self._block_ue_rows: dict[str, list[int]] = {}
        self._block_row_by_name: dict[str, int] = {}
        self._folded_blocks: set[str] = set()
        self._ue_total_items: dict[int, QTableWidgetItem] = {}
        self._ue_promo_items: dict[int, QTableWidgetItem] = {}
        self._cohort_ue_averages: dict[int, float | None] = {}
        self._ue_result_items: dict[int, QTableWidgetItem] = {}
        self._block_status_items: dict[int, QTableWidgetItem] = {}
        self._ue_detail_summary: QLabel | None = None
        self._selected_course_id: int | None = None
        self._year_spin: QDoubleSpinBox | None = None
        self._outcome_combo: QComboBox | None = None
        self._mention_combo: QComboBox | None = None
        self._validation_label: QLabel | None = None
        self._progression_year: QLineEdit | None = None
        self._m2_track_combo: QComboBox | None = None
        self._m2_track_other: QLineEdit | None = None
        self._m2_track_widget: QWidget | None = None
        self._m2_track_row_label: QLabel | None = None
        self._apply_progression_btn: QPushButton | None = None
        self._progression_status: QLabel | None = None
        self._session_notes_edit: QTextEdit | None = None
        self._notes_save_timer = QTimer(self)
        self._notes_save_timer.setSingleShot(True)
        self._notes_save_timer.setInterval(800)
        self._notes_save_timer.timeout.connect(self._save_deliberation_notes)

        tpl = next((t for t in repo.list_templates() if int(t["id"]) == self.template_id), None) or {}
        self._tpl = tpl
        self._template_level = str(tpl.get("level") or "").strip().upper()
        self._template_academic_year = str(tpl.get("academic_year") or "").strip()

        title = f"Délibération — {tpl.get('name', '')}"
        self.setWindowTitle(title)
        self.setMinimumSize(880, 560)

        root = QVBoxLayout(self)
        hint = QLabel(self._deliberation_hint_text())
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Étudiants"))
        nav_row = QHBoxLayout()
        prev_btn = QPushButton("◀ Préc.")
        next_btn = QPushButton("Suiv. ▶")
        prev_btn.setToolTip("Étudiant précédent (Ctrl+←)")
        next_btn.setToolTip("Étudiant suivant (Ctrl+→)")
        prev_btn.clicked.connect(self._prev_student)
        next_btn.clicked.connect(self._next_student)
        nav_row.addWidget(prev_btn)
        nav_row.addWidget(next_btn)
        ll.addLayout(nav_row)
        filter_row = QHBoxLayout()
        self._student_filter_combo = QComboBox()
        self._student_filter_combo.addItem("Tous les étudiants", "all")
        self._student_filter_combo.addItem("À problèmes uniquement", "issues")
        self._student_filter_combo.addItem("Sans problème uniquement", "ok")
        self._student_filter_combo.setToolTip(
            "« Sans problème » : année validée selon les règles (cas admis M2, etc.) — "
            "pour parcourir et enregistrer rapidement les décisions."
        )
        self._student_filter_combo.currentIndexChanged.connect(self._reload_student_list)
        filter_row.addWidget(self._student_filter_combo, 1)
        ll.addLayout(filter_row)
        self._filter_status_label = QLabel("")
        self._filter_status_label.setWordWrap(True)
        self._filter_status_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        ll.addWidget(self._filter_status_label)
        self._persist_visible_btn = QPushButton("Enregistrer décisions — liste affichée")
        self._persist_visible_btn.setToolTip(
            "Enregistre outcome + mention suggérés pour tous les étudiants visibles "
            "sans décision enregistrée (jury final)."
        )
        self._persist_visible_btn.clicked.connect(self._persist_visible_outcomes)
        self._persist_visible_btn.setVisible(self.session_kind == "FINAL")
        ll.addWidget(self._persist_visible_btn)
        self.student_list = QListWidget()
        self.student_list.currentRowChanged.connect(self._on_student_changed)
        ll.addWidget(self.student_list, 1)
        split.addWidget(left)

        QShortcut(QKeySequence("Ctrl+Left"), self, self._prev_student)
        QShortcut(QKeySequence("Ctrl+Right"), self, self._next_student)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        self.detail_host = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_host)
        right_scroll.setWidget(self.detail_host)
        split.addWidget(right_scroll)
        split.setSizes([240, 780])
        root.addWidget(split, 1)

        if self.jury_session_id is not None:
            notes_box = QGroupBox("Commentaires généraux de la délibération")
            notes_lay = QVBoxLayout(notes_box)
            notes_hint = QLabel(
                "Décisions collectives, dérogations pour toute la promotion, précisions pour le PV "
                "(ex. suppression du seuil 7 pour S2-C-CHEM pour tous les étudiants). "
                "Enregistrement automatique."
            )
            notes_hint.setWordWrap(True)
            notes_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
            notes_lay.addWidget(notes_hint)
            self._session_notes_edit = QTextEdit()
            self._session_notes_edit.setPlaceholderText(
                "Saisissez ici les commentaires du jury pour cette séance…"
            )
            self._session_notes_edit.setMinimumHeight(72)
            self._session_notes_edit.setMaximumHeight(140)
            self._session_notes_edit.textChanged.connect(self._schedule_deliberation_notes_save)
            sess = self.repo.get_jury_session(int(self.jury_session_id)) or {}
            self._session_notes_edit.setPlainText(str(sess.get("notes") or ""))
            notes_lay.addWidget(self._session_notes_edit)
            root.addWidget(notes_box)

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

    def _deliberation_grade_session(self, student_id: int, course_id: int) -> int:
        """Session d'épreuves affichée / min. : S1 au jury S1, sinon S2 si retenue."""
        if self._allow_s2_decision():
            return 1
        use_s2 = self.repo.course_uses_session2_grades(
            int(student_id),
            self.template_id,
            int(course_id),
            view_session=self.view_session,
        )
        return 2 if use_s2 else 1

    def _s2_cell_tooltip(
        self, student_id: int, course_id: int, *, sent_s2: bool, locked: bool
    ) -> str:
        sid, cid = int(student_id), int(course_id)
        has_s2 = self.repo.course_has_session2_activity(sid, cid)
        if locked:
            return (
                "Envoi figé : au moins une note de session 2 est saisie sur cette UE "
                "(ex. reprise automatique ou saisie en 2ᵉ session)."
            )
        if not self._allow_s2_decision():
            if self.session_kind == "FINAL":
                return "Jury final : la décision d'envoi en 2ᵉ session n'est plus modifiable."
            return "Vue session 2 : affichage des notes S2 — décision d'envoi figée."
        if has_s2:
            return (
                "Notes de session 2 déjà en base — en jury de session 1, vous pouvez "
                "quand même cocher ou décocher l'envoi en 2ᵉ session."
            )
        triggers = self.repo.course_triggers_second_session(sid, cid)
        if sent_s2 and triggers:
            return (
                "Envoi obligatoire : DEF ou ABJ en session 1 sur cette UE. "
                "Décochez seulement si le statut a été corrigé en base."
            )
        if sent_s2:
            return (
                "Envoi S2 enregistré alors que l'UE n'a plus de DEF/ABJ en S1 "
                "(ex. note corrigée). Décochez si la reprise n'est pas nécessaire."
            )
        if triggers:
            return (
                "Cocher automatiquement recommandé : DEF ou ABJ en session 1 sur cette UE."
            )
        return (
            "Cocher pour envoyer en 2ᵉ session (utile si l'UE reste non validée). "
            "Une UE compensable (7–10) n'a pas besoin d'envoi S2."
        )

    def _make_s2_cell_widget(self, student_id: int, course_id: int) -> QWidget | None:
        """Case S2 : modifiable au jury S1 tant qu'aucune note S2 n'existe sur l'UE."""
        sid, cid = int(student_id), int(course_id)
        sent_s2 = self.repo.is_sent_to_second_session(sid, self.template_id, cid)
        use_s2 = self.repo.course_uses_session2_grades(
            sid, self.template_id, cid, view_session=self.view_session
        )
        locked = self.repo.second_session_decision_locked(
            sid, self.template_id, cid, s1_jury=self._allow_s2_decision()
        )

        if not self._allow_s2_decision():
            if not (sent_s2 or use_s2 or locked):
                return None
            editable = False
            checked = bool(sent_s2 or use_s2 or locked)
        else:
            editable = not locked
            checked = bool(sent_s2)

        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk = QCheckBox()
        chk.setChecked(checked)
        chk.setToolTip(self._s2_cell_tooltip(sid, cid, sent_s2=sent_s2, locked=locked))
        if editable:
            chk.toggled.connect(
                lambda checked, _sid=sid, _cid=cid: self._save_s2(_sid, _cid, checked)
            )
            self._s2_checks[cid] = chk
        else:
            chk.setEnabled(False)
        lay.addWidget(chk)
        return wrap

    def _deliberation_hint_text(self) -> str:
        parts = [
            "Tableau de <b>synthèse</b> : chaque ligne de bloc affiche la <b>moyenne</b> et le statut "
            "<b>VALIDÉ</b> / <b>NON VALIDÉ</b> ; cliquez sur le bloc (▶/▼) pour replier ses UE. "
            "Sélectionnez une UE pour modifier les épreuves en bas. "
            "Survolez les en-têtes de colonnes pour l'aide. "
        ]
        if self._allow_s2_decision():
            parts.append(
                "Colonne <b>2ᵉ sess.</b> : cochez l'envoi en seconde session (auto si DEF/ABJ en S1). "
                "Même si des notes S2 existent déjà en base (tests), l'envoi reste modifiable ici. "
            )
        elif self.session_kind == "FINAL":
            parts.append(
                "Jury final : notes de 2ᵉ session retenues lorsqu'elles existent ; "
                "plus de nouvel envoi en 2ᵉ session. "
            )
        else:
            parts.append(
                "Vue session 2 : notes S2 affichées ; décisions d'envoi déjà prises au jury S1. "
            )
        parts.append(
            "« Valider (seuil 7) » : dérogation jury si la note de l'étudiant à l'UE est < 7/20. "
            "<b>Compensable</b> (orange) seulement si aucune autre UE du bloc n'est éliminatoire "
            "(note &lt; 7, DEF/ABJ) et que toutes les UE ont une note. "
        )
        if self.session_kind == "FINAL":
            parts.append(
                "Filtrez « Sans problème uniquement » pour valider rapidement les admis M2 ; "
                "propositions de décision et mention (≥ 12 Assez bien … ≥ 18 Excellent) — le jury enregistre. "
            )
            if self._template_level == "M1":
                parts.append(
                    "Pour un <b>admis en M2</b> ou un <b>redoublement</b>, renseignez le millésime cible "
                    "puis « Appliquer la décision sur la fiche étudiant ». "
                )
            elif self._template_level == "M2":
                parts.append(
                    "Pour une <b>année validée</b>, « Appliquer » clôt la formation (statut diplômé). "
                    "Pour un <b>redoublement M2</b>, indiquez le millésime cible avant d'appliquer. "
                )
        return "".join(parts)

    def _load_students(self) -> None:
        self._all_student_ids = [
            int(s["id"]) for s in self.repo.list_students_for_template(self.template_id)
        ]
        self._student_issue_cache.clear()
        self._reload_student_list()

    def _effective_view_session(self) -> str:
        if self.session_kind == "FINAL":
            return "mixed"
        return str(self.view_session or "s1")

    def _student_has_monitor_issues(self, student_id: int) -> bool:
        sid = int(student_id)
        cached = self._student_issue_cache.get(sid)
        if cached is not None:
            return cached
        vs = self._effective_view_session()
        data = self.repo.get_student_result_summary(
            self.template_id,
            view_session=vs,
            include_all_students=True,
            auto_sync_s2=self._allow_s2_decision(),
        )
        row = next((r for r in data if int(r.get("student_id") or 0) == sid), None)
        if row is None:
            self._student_issue_cache[sid] = False
            return False
        ev = self.repo.evaluate_student_year_validation(
            sid,
            self.template_id,
            view_session=vs,
            result_row=row,
            auto_sync_s2=False,
        )
        if not ev.get("validated"):
            self._student_issue_cache[sid] = True
            return True
        self._student_issue_cache[sid] = False
        return False

    def _invalidate_student_issue_cache(self, student_id: int) -> None:
        self._student_issue_cache.pop(int(student_id), None)

    def _student_filter_mode(self) -> str:
        if self._student_filter_combo is None:
            return "all"
        return str(self._student_filter_combo.currentData() or "all")

    def _student_outcome_saved(self, student_id: int) -> bool:
        if self.session_kind != "FINAL" or self.jury_session_id is None:
            return False
        oc = self.repo.get_jury_student_outcome(
            int(student_id), self.template_id, jury_session_id=self.jury_session_id
        )
        return bool(oc and str(oc.get("outcome") or "").strip())

    def _reload_student_list(self, *_args) -> None:
        prev_sid = self._current_student_id()
        self.student_list.blockSignals(True)
        self.student_list.clear()
        self._student_ids = []
        mode = self._student_filter_mode()
        pending_decision = 0
        for sid in self._all_student_ids:
            has_issues = self._student_has_monitor_issues(sid)
            if mode == "issues" and not has_issues:
                continue
            if mode == "ok" and has_issues:
                continue
            s = self.repo.get_student(sid) or {}
            self._student_ids.append(sid)
            label = student_combo_label(s)
            if self._student_outcome_saved(sid):
                label += " ✓"
            elif self.session_kind == "FINAL":
                pending_decision += 1
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, sid)
            if has_issues:
                it.setForeground(QBrush(QColor("#B71C1C")))
            else:
                it.setForeground(QBrush(QColor("#1B5E20")))
            self.student_list.addItem(it)
        self.student_list.blockSignals(False)
        n = len(self._student_ids)
        if self._filter_status_label is not None:
            if n == 0:
                txt = "Aucun étudiant pour ce filtre."
            elif mode == "ok":
                txt = f"{n} sans problème affiché{'s' if n != 1 else ''}"
                if self.session_kind == "FINAL":
                    txt += f" · {pending_decision} sans décision enregistrée"
            elif mode == "issues":
                txt = f"{n} à problème{'s' if n != 1 else ''}"
            else:
                txt = f"{n} étudiant{'s' if n != 1 else ''}"
            self._filter_status_label.setText(txt)
        if self._persist_visible_btn is not None:
            show_persist = (
                self.session_kind == "FINAL"
                and n > 0
                and pending_decision > 0
            )
            self._persist_visible_btn.setEnabled(show_persist)
        if not self._student_ids:
            self._clear_detail()
            return
        restore = 0
        if prev_sid is not None and prev_sid in self._student_ids:
            restore = self._student_ids.index(prev_sid)
        self.student_list.setCurrentRow(restore)

    def _persist_visible_outcomes(self) -> None:
        if self.session_kind != "FINAL" or self.jury_session_id is None:
            return
        vs = self._effective_view_session()
        saved = 0
        for sid in self._student_ids:
            if self._student_outcome_saved(sid):
                continue
            row = self._fetch_result_row(int(sid))
            ev = self.repo.evaluate_student_year_validation(
                int(sid),
                self.template_id,
                view_session=vs,
                result_row=row,
                auto_sync_s2=self._allow_s2_decision(),
            )
            outcome = str(ev.get("suggested_outcome") or "repeat")
            mention = ""
            if row and row.get("global_with_jury") is not None:
                mention = transcript_mention_code_from_grade(float(row["global_with_jury"]))
            self.repo.upsert_jury_student_outcome(
                int(sid),
                self.template_id,
                jury_session_id=self.jury_session_id,
                outcome=outcome,
                mention=mention,
            )
            saved += 1
        self._reload_student_list()
        QMessageBox.information(
            self,
            "Décisions enregistrées",
            f"{saved} décision(s) enregistrée(s) pour la liste affichée.",
        )

    def _refresh_list_item_for_student(self, student_id: int) -> None:
        sid = int(student_id)
        if sid not in self._student_ids or self.student_list is None:
            return
        row_idx = self._student_ids.index(sid)
        it = self.student_list.item(row_idx)
        if it is None:
            return
        s = self.repo.get_student(sid) or {}
        label = student_combo_label(s)
        if self._student_outcome_saved(sid):
            label += " ✓"
        it.setText(label)
        pending = sum(
            1
            for x in self._student_ids
            if self.session_kind == "FINAL" and not self._student_outcome_saved(x)
        )
        if self._filter_status_label is not None and self._student_filter_mode() == "ok":
            n = len(self._student_ids)
            self._filter_status_label.setText(
                f"{n} sans problème affiché{'s' if n != 1 else ''}"
                f" · {pending} sans décision enregistrée"
            )
        if self._persist_visible_btn is not None:
            self._persist_visible_btn.setEnabled(
                self.session_kind == "FINAL" and pending > 0
            )

    def _prev_student(self) -> None:
        row = self.student_list.currentRow()
        if row > 0:
            self.student_list.setCurrentRow(row - 1)

    def _next_student(self) -> None:
        row = self.student_list.currentRow()
        if row < self.student_list.count() - 1:
            self.student_list.setCurrentRow(row + 1)

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
        self._block_validation_waiver_checks.clear()
        self._grade_items.clear()
        self._grade_item_meta.clear()
        self._ue_table = None
        self._assessment_table = None
        self._ue_row_course.clear()
        self._ue_block_rows.clear()
        self._block_ue_rows.clear()
        self._block_row_by_name.clear()
        self._ue_total_items.clear()
        self._ue_promo_items.clear()
        self._cohort_ue_averages.clear()
        self._ue_result_items.clear()
        self._block_status_items.clear()
        self._ue_detail_summary = None
        self._selected_course_id = None
        self._year_spin = None
        self._outcome_combo = None
        self._mention_combo = None
        self._validation_label = None
        self._progression_year = None
        self._m2_track_combo = None
        self._m2_track_other = None
        self._m2_track_widget = None
        self._m2_track_row_label = None
        self._apply_progression_btn = None
        self._progression_status = None

    def _on_student_changed(self, _row: int) -> None:
        self._clear_detail()
        sid = self._current_student_id()
        if sid is None:
            return
        self._build_detail(sid)

    def _fetch_result_row(self, student_id: int) -> dict[str, Any] | None:
        sid = int(student_id)
        auto = self._allow_s2_decision()
        vs = self.view_session
        for include_all in (False, True):
            data = self.repo.get_student_result_summary(
                self.template_id,
                view_session=vs,
                auto_sync_s2=auto,
                include_all_students=include_all,
            )
            row = next((r for r in data if int(r.get("student_id") or 0) == sid), None)
            if row is not None:
                return row
        return None

    def _resolve_ue_display(
        self, student_id: int, course_id: int, row: dict[str, Any]
    ) -> str:
        """Libellé UE (DEF, ABJ, …) — repli si la synthèse n'a pas encore le statut."""
        cid = int(course_id)
        display = str(_ue_detail_dict(row, cid).get("display") or "").strip()
        if display:
            return display
        sid = int(student_id)
        if self._allow_s2_decision():
            vs = "s1"
        else:
            vs = str(self.view_session or "s1").lower()
        use_s2 = self.repo.course_uses_session2_grades(
            sid, self.template_id, cid, view_session=vs
        )
        if vs == "s1":
            if self.repo.course_session1_has_def(sid, cid):
                return STATUS_DEF
            if self.repo.course_session1_has_abj(sid, cid):
                return STATUS_ABJ
        elif use_s2:
            d = _ue_detail_dict(row, cid)
            s2_avg = d.get("s2")
            if s2_avg is None:
                if self.repo.course_session1_has_def(sid, cid):
                    return STATUS_DEF
                if self.repo.course_session1_has_abj(sid, cid):
                    return STATUS_ABJ
            if self.repo.course_session2_has_def(sid, cid):
                return STATUS_DEF
            if self.repo.course_session2_has_abj(sid, cid):
                return STATUS_ABJ
            if not self.repo.course_has_session2_activity(sid, cid):
                if self.repo.course_session1_has_def(sid, cid):
                    return STATUS_DEF
                if self.repo.course_session1_has_abj(sid, cid):
                    return STATUS_ABJ
        else:
            if self.repo.course_session1_has_def(sid, cid):
                return STATUS_DEF
            if self.repo.course_session1_has_abj(sid, cid):
                return STATUS_ABJ
        return ""

    def _jury_points(
        self,
        sid: int,
        scope: str,
        *,
        course_id: int | None = None,
        block_name: str = "",
        current_session_only: bool = False,
    ) -> float:
        total = 0.0
        found = False
        for row in self.repo.list_jury_adjustments_for_export(self.template_id):
            if int(row["student_id"]) != sid:
                continue
            if str(row.get("scope") or "").lower() != scope:
                continue
            if scope == "course" and int(row.get("course_id") or 0) != int(course_id or 0):
                continue
            if scope == "block" and str(row.get("block_name") or "").strip() != str(
                block_name or ""
            ).strip():
                continue
            if current_session_only and self.jury_session_id is not None:
                row_jsid = row.get("jury_session_id")
                if row_jsid is None or int(row_jsid) != int(self.jury_session_id):
                    continue
            total += float(row.get("points") or 0)
            found = True
        return total if found or not current_session_only else 0.0

    def _effective_jury_points(
        self,
        sid: int,
        scope: str,
        *,
        course_id: int | None = None,
        block_name: str = "",
        spin_value: float | None = None,
    ) -> float:
        """Total jury (toutes sessions) en tenant compte de la saisie en cours."""
        total = self._jury_points(sid, scope, course_id=course_id, block_name=block_name)
        if not self.jury_session_id or spin_value is None:
            return total
        session_pts = self._jury_points(
            sid,
            scope,
            course_id=course_id,
            block_name=block_name,
            current_session_only=True,
        )
        return total - session_pts + float(spin_value)

    def _jury_spin_tooltip(
        self,
        sid: int,
        scope: str,
        *,
        course_id: int | None = None,
        block_name: str = "",
    ) -> str:
        """Rappel des points jury des séances précédentes + saisie courante."""
        kwargs: dict[str, Any] = {}
        if course_id is not None:
            kwargs["course_id"] = int(course_id)
        if block_name:
            kwargs["block_name"] = block_name
        total = self._jury_points(sid, scope, **kwargs)
        if self.jury_session_id:
            session_pts = self._jury_points(
                sid, scope, current_session_only=True, **kwargs
            )
            prior = float(total) - float(session_pts)
            lines = [
                f"Total cumulé (toutes séances) : {total:+.3f}",
                f"Cette séance : {session_pts:+.3f}",
            ]
            if abs(prior) > 1e-12:
                lines.append(f"Séances précédentes : {prior:+.3f}")
            return "\n".join(lines)
        return f"Points jury cumulés : {total:+.3f}"

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

        self._setup_monitor_panels(sid)

        year_box = QGroupBox("Année")
        yl = QFormLayout(year_box)
        yjp = self._jury_points(sid, "year", current_session_only=bool(self.jury_session_id))
        self._year_spin = self._make_spin(yjp, lambda _v: self._save_year_jury(sid))
        self._year_spin.setToolTip(self._jury_spin_tooltip(sid, "year"))
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

            if self._template_level == "M1":
                self._progression_year = QLineEdit(
                    suggest_next_academic_year(self._template_academic_year)
                )
                yl.addRow("Millésime cible :", self._progression_year)

                track_row = QHBoxLayout()
                self._m2_track_combo = QComboBox()
                for code, lab in parcours_choices("M2"):
                    self._m2_track_combo.addItem(f"{code} — {lab}", code)
                self._m2_track_combo.addItem("Autre…", OTHER_TRACK_DATA)
                self._m2_track_other = QLineEdit()
                self._m2_track_other.setPlaceholderText("Code parcours M2")
                self._m2_track_combo.currentIndexChanged.connect(self._m2_track_changed)
                track_row.addWidget(self._m2_track_combo, stretch=1)
                track_row.addWidget(self._m2_track_other, stretch=1)
                self._m2_track_widget = QWidget()
                self._m2_track_widget.setLayout(track_row)
                self._m2_track_row_label = QLabel("Parcours M2 :")
                yl.addRow(self._m2_track_row_label, self._m2_track_widget)
                saved_track = str((oc or {}).get("progression_track") or "").strip()
                if saved_track:
                    self._set_m2_track_value(saved_track)
                self._m2_track_changed()
                self._m2_track_combo.currentIndexChanged.connect(
                    lambda: self._save_progression_track(sid)
                )
                self._m2_track_other.editingFinished.connect(
                    lambda: self._save_progression_track(sid)
                )

                self._apply_progression_btn = QPushButton(
                    "Appliquer la décision sur la fiche étudiant"
                )
                self._apply_progression_btn.clicked.connect(lambda: self._apply_progression(sid))
                yl.addRow("", self._apply_progression_btn)

                self._progression_status = QLabel("")
                self._progression_status.setWordWrap(True)
                self._progression_status.setStyleSheet("color: palette(mid); font-size: 11px;")
                yl.addRow("", self._progression_status)
                self._update_progression_ui(sid)

            elif self._template_level == "M2":
                self._progression_year = QLineEdit(
                    suggest_next_academic_year(self._template_academic_year)
                )
                yl.addRow("Millésime cible (redoublement) :", self._progression_year)

                self._apply_progression_btn = QPushButton(
                    "Appliquer la décision sur la fiche étudiant"
                )
                self._apply_progression_btn.clicked.connect(lambda: self._apply_progression(sid))
                yl.addRow("", self._apply_progression_btn)

                self._progression_status = QLabel("")
                self._progression_status.setWordWrap(True)
                self._progression_status.setStyleSheet("color: palette(mid); font-size: 11px;")
                yl.addRow("", self._progression_status)
                self._update_progression_ui(sid)

        self.detail_layout.addWidget(year_box)
        self.detail_layout.addStretch()
        self._refresh_summary(sid)

    def _apply_ue_table_header_tooltips(self) -> None:
        if self._ue_table is None:
            return
        specs = [
            (
                "UE",
                "Cliquez sur la ligne d'un bloc (▶/▼) pour afficher ou masquer ses UE. "
                "La moyenne du bloc et son statut de validation sont sur cette ligne.",
            ),
            (
                "Note\nUE",
                "Note d'UE (MCC + jury) — seuils : ≥ 10 validée, 7–10 compensable si le bloc "
                "est validé (moy. bloc ≥ 10, aucune autre UE < 7).",
            ),
            (
                "Moy.\npromo",
                "Moyenne de la promotion à cette UE (tous les inscrits, MCC, sans jury). "
                "Info-bulle : écart par rapport à l'étudiant sélectionné.",
            ),
            (
                "Pts jury",
                "Points de délibération du jury sur cette UE pour <b>cette séance</b> "
                "(−5 à +5). Les points des séances précédentes restent cumulés "
                "(info-bulle du champ). Au jury final, laisser 0 si déjà décidé en S1.",
            ),
            (
                "Statut\nUE",
                "Pour une UE : validée, compensable, etc. "
                "Pour un bloc : VALIDÉ / NON VALIDÉ / INCOMPLET.",
            ),
            ("2ᵉ sess.", "Envoi de l'étudiant en seconde session pour cette UE."),
            (
                "Seuil 7",
                "Dérogation jury : une note étudiant < 7/20 à l'UE peut être compensée.",
            ),
        ]
        for col, (label, tip) in enumerate(specs):
            hi = QTableWidgetItem(label)
            hi.setToolTip(tip)
            self._ue_table.setHorizontalHeaderItem(col, hi)

    def _setup_monitor_panels(self, student_id: int) -> None:
        sid = int(student_id)
        monitor_box = QGroupBox("Synthèse par UE et par bloc")
        ml = QVBoxLayout(monitor_box)
        legend_row = QHBoxLayout()
        legend = QLabel(
            "<span style='color:palette(mid); font-size:11px;'>"
            "Chaque <b>ligne de bloc</b> : moyenne pondérée ECTS des notes d'UE (MCC + jury UE + jury bloc). "
            "Bloc validé si moy. ≥ 10 et aucune note d'UE &lt; 7 (sauf dérogation <b>Seuil 7</b>). "
            "<b>Note UE</b> = MCC + jury UE ; <b>Moy. promo</b> = moyenne de la promotion à l'UE. "
            "Le jury peut déroger (seuil 7) ou envoyer en 2ᵉ session librement. "
            "<b>2ᵉ sess.</b> : automatique seulement si DEF/ABJ en S1."
            "</span>"
        )
        legend.setWordWrap(True)
        legend_row.addWidget(legend, 1)
        fold_all_btn = QPushButton("Tout replier")
        fold_all_btn.setToolTip("Masquer le détail des UE sous chaque bloc.")
        fold_all_btn.clicked.connect(self._fold_all_blocks)
        legend_row.addWidget(fold_all_btn)
        unfold_all_btn = QPushButton("Tout déplier")
        unfold_all_btn.setToolTip("Afficher toutes les UE sous chaque bloc.")
        unfold_all_btn.clicked.connect(self._unfold_all_blocks)
        legend_row.addWidget(unfold_all_btn)
        ml.addLayout(legend_row)

        split = QSplitter(Qt.Orientation.Vertical)

        self._ue_table = QTableWidget()
        self._ue_table.setColumnCount(7)
        self._apply_ue_table_header_tooltips()
        self._ue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._ue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._ue_table.setAlternatingRowColors(True)
        self._ue_table.verticalHeader().setVisible(False)
        self._ue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ue_hdr = self._ue_table.horizontalHeader()
        ue_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 7):
            ue_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._ue_table.itemSelectionChanged.connect(self._on_ue_selection_changed)
        self._ue_table.cellClicked.connect(self._on_ue_cell_clicked)
        self._fill_ue_table(sid)
        split.addWidget(self._ue_table)

        ass_wrap = QWidget()
        ass_layout = QVBoxLayout(ass_wrap)
        ass_layout.setContentsMargins(0, 0, 0, 0)
        ass_hint = QLabel(
            "<b>Épreuves</b> — sélectionnez une UE ci-dessus pour modifier les notes par épreuve."
        )
        ass_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        ass_layout.addWidget(ass_hint)
        self._ue_detail_summary = QLabel("—")
        self._ue_detail_summary.setWordWrap(True)
        self._ue_detail_summary.setStyleSheet(
            "font-size: 12px; padding: 6px 8px; border-radius: 4px; background: #f5f5f5;"
        )
        ass_layout.addWidget(self._ue_detail_summary)
        self._assessment_table = QTableWidget()
        self._assessment_table.setColumnCount(3)
        self._assessment_table.setHorizontalHeaderLabels(["Épreuve", "Coef.", "Note"])
        self._assessment_table.itemChanged.connect(self._on_grade_item_changed)
        self._assessment_table.verticalHeader().setVisible(False)
        ass_hdr = self._assessment_table.horizontalHeader()
        ass_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        ass_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        ass_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        ass_layout.addWidget(self._assessment_table, 1)
        split.addWidget(ass_wrap)
        split.setSizes([420, 200])

        ml.addWidget(split, 1)
        self.detail_layout.addWidget(monitor_box, 1)

        if self._ue_row_course:
            first = self._first_visible_ue_row()
            if first is not None:
                self._ue_table.selectRow(first)

    def _fill_ue_table(self, student_id: int) -> None:
        sid = int(student_id)
        if self._ue_table is None:
            return
        self._loading = True
        try:
            self._ue_table.blockSignals(True)
            self._ue_table.setRowCount(0)
            self._ue_row_course.clear()
            self._ue_block_rows.clear()
            self._block_ue_rows.clear()
            self._block_row_by_name.clear()
            self._block_spinboxes.clear()
            self._course_spinboxes.clear()
            self._s2_checks.clear()
            self._floor_waiver_checks.clear()
            self._block_validation_waiver_checks.clear()
            self._ue_total_items.clear()
            self._ue_promo_items.clear()
            self._cohort_ue_averages.clear()
            self._ue_result_items.clear()
            self._block_status_items.clear()

            row_idx = 0
            hide_s2_col = True
            self._cohort_ue_averages = {}
            blocks = self.repo.list_template_blocks_with_courses(self.template_id)
            for bk, clist in blocks:
                graded = [
                    c
                    for c in clist
                    if not int(c.get("optional") or 0) and not int(c.get("free_ue") or 0)
                ]
                if not graded:
                    continue

                bk_key = str(bk or "").strip() or "(no block)"
                self._block_ue_rows.setdefault(bk_key, [])

                self._ue_table.insertRow(row_idx)
                self._ue_block_rows.add(row_idx)
                self._block_row_by_name[bk_key] = row_idx
                expanded = bk_key not in self._folded_blocks
                arrow = "▼" if expanded else "▶"
                blk_title = QTableWidgetItem(f"{arrow} {bk_key}")
                blk_title.setData(Qt.ItemDataRole.UserRole, bk_key)
                blk_title.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                blk_title.setFont(
                    QFont(
                        blk_title.font().family(),
                        blk_title.font().pointSize(),
                        QFont.Weight.Bold,
                    )
                )
                blk_title.setToolTip(
                    "Moyenne pondérée du bloc (notes UE + points jury bloc). "
                    "Cliquez pour afficher ou masquer les UE du bloc."
                )
                self._ue_table.setItem(row_idx, 0, blk_title)

                blk_avg_item = QTableWidgetItem("—")
                blk_avg_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                blk_avg_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                blk_avg_item.setFont(
                    QFont(
                        blk_avg_item.font().family(),
                        blk_avg_item.font().pointSize() + 1,
                        QFont.Weight.Bold,
                    )
                )
                blk_avg_item.setToolTip("Moyenne du bloc (avec points jury bloc).")
                self._ue_table.setItem(row_idx, 1, blk_avg_item)

                blk_min_item = QTableWidgetItem("")
                blk_min_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._ue_table.setItem(row_idx, 2, blk_min_item)

                bjp = self._jury_points(
                    sid, "block", block_name=bk, current_session_only=bool(self.jury_session_id)
                )

                def _blk_change(_v: float, _sid: int = sid, _bk: str = bk) -> None:
                    self._save_block_jury(_sid, _bk)

                bsp = self._make_spin(bjp, _blk_change)
                bsp.setToolTip(self._jury_spin_tooltip(sid, "block", block_name=bk))
                self._block_spinboxes[bk] = bsp
                self._ue_table.setCellWidget(row_idx, 3, bsp)

                blk_status = QTableWidgetItem("")
                blk_status.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                blk_status.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                blk_status.setFont(
                    QFont(
                        blk_status.font().family(),
                        blk_status.font().pointSize(),
                        QFont.Weight.Bold,
                    )
                )
                self._ue_table.setItem(row_idx, 4, blk_status)
                self._block_status_items[row_idx] = blk_status

                bval = QCheckBox("Val. <10")
                bval.setToolTip(
                    "Dérogation jury : valider le bloc malgré une moyenne inférieure à 10."
                )
                bval.setChecked(
                    self.repo.has_block_jury_validation_waiver(sid, self.template_id, bk)
                )
                bval.toggled.connect(
                    lambda checked, _sid=sid, _bk=bk: self._save_block_validation_waiver(
                        _sid, _bk, checked
                    )
                )
                self._block_validation_waiver_checks[bk] = bval
                self._ue_table.setCellWidget(row_idx, 6, bval)

                for col in (5,):
                    filler = QTableWidgetItem("")
                    filler.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self._ue_table.setItem(row_idx, col, filler)

                row_idx += 1

                for c in graded:
                    cid = int(c["course_id"])
                    if self.repo.has_ue_ects_validation(sid, self.template_id, cid):
                        continue
                    use_s2 = self.repo.course_uses_session2_grades(
                        sid, self.template_id, cid, view_session=self.view_session
                    )
                    grade_rows = self.repo.get_grades_for_student_course(sid, cid)
                    sess = self._deliberation_grade_session(sid, cid)
                    assessments = [r for r in grade_rows if int(r.get("session") or 1) == sess]
                    if not assessments:
                        assessments = [r for r in grade_rows if int(r.get("session") or 1) == 1]
                    if not assessments:
                        continue

                    if cid not in self._cohort_ue_averages:
                        self._cohort_ue_averages[cid] = self.repo.get_course_cohort_ue_average(
                            self.template_id, cid, view_session=self.view_session
                        )

                    self._ue_table.insertRow(row_idx)
                    self._ue_row_course[row_idx] = cid
                    code = (
                        course_public_code(c, academic_year=self._template_academic_year)
                        or str(c.get("code") or "")
                    )
                    name = str(c.get("name") or "").strip()
                    ue_it = QTableWidgetItem(f"{code}  {name[:48]}")
                    ue_it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    ue_it.setData(Qt.ItemDataRole.UserRole, cid)
                    self._ue_table.setItem(row_idx, 0, ue_it)

                    tot_it = QTableWidgetItem("—")
                    tot_it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    tot_it.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                    self._ue_table.setItem(row_idx, 1, tot_it)
                    self._ue_total_items[cid] = tot_it

                    promo_it = QTableWidgetItem("—")
                    promo_it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    promo_it.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                    self._ue_table.setItem(row_idx, 2, promo_it)
                    self._ue_promo_items[cid] = promo_it

                    jp = self._jury_points(
                        sid,
                        "course",
                        course_id=cid,
                        current_session_only=bool(self.jury_session_id),
                    )

                    def _ue_change(_v: float, _sid: int = sid, _cid: int = cid) -> None:
                        self._save_course_jury(_sid, _cid)

                    sp = self._make_spin(jp, _ue_change)
                    sp.setToolTip(self._jury_spin_tooltip(sid, "course", course_id=cid))
                    self._course_spinboxes[cid] = sp
                    self._ue_table.setCellWidget(row_idx, 3, sp)

                    res_it = QTableWidgetItem("—")
                    res_it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    res_it.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                    self._ue_table.setItem(row_idx, 4, res_it)
                    self._ue_result_items[cid] = res_it

                    s2_w = self._make_s2_cell_widget(sid, cid)
                    if s2_w is not None:
                        hide_s2_col = False
                        self._ue_table.setCellWidget(row_idx, 5, s2_w)

                    wchk = QCheckBox()
                    wchk.setToolTip("Dérogation jury : note étudiant < 7 à l'UE.")
                    wchk.setChecked(self.repo.has_ue_jury_floor_waiver(sid, self.template_id, cid))
                    wchk.toggled.connect(
                        lambda checked, _sid=sid, _cid=cid: self._save_floor_waiver(_sid, _cid, checked)
                    )
                    self._floor_waiver_checks[cid] = wchk
                    self._ue_table.setCellWidget(row_idx, 6, wchk)
                    self._block_ue_rows[bk_key].append(row_idx)
                    row_idx += 1

            self._ue_table.setColumnHidden(5, hide_s2_col)
            self._apply_block_fold_state()
            row = self._fetch_result_row(sid)
            if row is not None:
                self._apply_ue_row_colors(sid, row)
        finally:
            self._ue_table.blockSignals(False)
            self._loading = False

    def _fill_assessment_table(self, student_id: int, course_id: int) -> None:
        sid = int(student_id)
        cid = int(course_id)
        if self._assessment_table is None:
            return
        self._loading = True
        try:
            self._assessment_table.blockSignals(True)
            self._grade_items.clear()
            self._grade_item_meta.clear()
            grade_rows = self.repo.get_grades_for_student_course(sid, cid)
            sess = self._deliberation_grade_session(sid, cid)
            assessments = [r for r in grade_rows if int(r.get("session") or 1) == sess]
            if not assessments:
                assessments = [r for r in grade_rows if int(r.get("session") or 1) == 1]
            self._assessment_table.setRowCount(len(assessments))
            for r, ar in enumerate(assessments):
                aid = int(ar["assessment_id"])
                ep = QTableWidgetItem(
                    f"{ar.get('kind', '')} — {str(ar.get('name') or '')[:60]}"
                )
                ep.setFlags(ep.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._assessment_table.setItem(r, 0, ep)

                try:
                    coef_txt = f"{float(ar.get('coefficient') or 0):.0f}%"
                except (TypeError, ValueError):
                    coef_txt = "—"
                coef_it = QTableWidgetItem(coef_txt)
                coef_it.setFlags(coef_it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                coef_it.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                self._assessment_table.setItem(r, 1, coef_it)

                note_it = QTableWidgetItem(
                    format_grade_display(
                        ar.get("grade"),
                        ar.get("status"),
                        assessment_session=int(ar.get("session") or 1),
                        assessment_kind=str(ar.get("kind") or ""),
                        assessment_name=str(ar.get("name") or ""),
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
                note_it.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                self._assessment_table.setItem(r, 2, note_it)
                self._grade_items[aid] = note_it
                self._grade_item_meta[aid] = (sid, cid)
                bg = _color_for_grade_20(ar.get("grade") if ar.get("grade") is not None else None)
                if bg:
                    note_it.setBackground(_brush(bg))
        finally:
            self._assessment_table.blockSignals(False)
            self._loading = False
        self._update_ue_detail_summary(sid, cid)

    def _on_ue_selection_changed(self) -> None:
        if self._loading or self._ue_table is None:
            return
        sid = self._current_student_id()
        if sid is None:
            return
        selected = self._ue_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        if self._ue_table.isRowHidden(row):
            return
        if row in self._ue_block_rows:
            return
        cid = self._ue_row_course.get(row)
        if cid is None:
            return
        self._selected_course_id = int(cid)
        self._fill_assessment_table(sid, int(cid))

    def _update_ue_detail_summary(
        self, student_id: int, course_id: int, row: dict[str, Any] | None = None
    ) -> None:
        if self._ue_detail_summary is None:
            return
        sid, cid = int(student_id), int(course_id)
        if row is None:
            row = self._fetch_result_row(sid)
        if row is None:
            self._ue_detail_summary.setText("—")
            return
        d = _ue_detail_dict(row, cid)
        display = self._resolve_ue_display(sid, cid, row)
        moy_ue = _ue_session_average(
            self.repo, sid, self.template_id, cid, row, view_session=self.view_session
        )
        jp = (
            self._effective_jury_points(
                sid, "course", course_id=cid, spin_value=float(self._course_spinboxes[cid].value())
            )
            if cid in self._course_spinboxes
            else self._jury_points(sid, "course", course_id=cid)
        )
        total = _ue_total_with_jury(
            row, cid, view_session=self.view_session, jury_points=jp
        )
        if total is None and moy_ue is not None:
            total = float(moy_ue) + jp
        promo = self._cohort_ue_averages.get(cid)
        if promo is None:
            promo = self.repo.get_course_cohort_ue_average(
                self.template_id, cid, view_session=self.view_session
            )
        waived = self.repo.has_ue_jury_floor_waiver(sid, self.template_id, cid)
        can_compensate = self.repo.block_allows_ue_compensation(
            sid,
            self.template_id,
            cid,
            result_row=row,
            view_session=self.view_session,
        )
        comp_status = self.repo.block_ue_compensation_status(
            sid,
            self.template_id,
            cid,
            result_row=row,
            view_session=self.view_session,
        )
        label, color = _ue_validation_status(
            self.repo,
            sid,
            self.template_id,
            cid,
            display=display,
            total_with_jury=total if not display else None,
            waived=waived,
            compensation_allowed=can_compensate,
            compensation_status=comp_status,
        )
        moy_txt = display if display else _fmt(moy_ue)
        tot_txt = display if display else _fmt(total)
        promo_txt = _fmt(promo)
        waiver_txt = " · dérogation seuil 7" if waived else ""
        if promo is not None and moy_ue is not None and not display:
            delta = float(moy_ue) - float(promo)
            sign = "+" if delta >= 0 else ""
            promo_cmp = f" ({sign}{delta:.2f} vs cet étudiant)"
        else:
            promo_cmp = ""
        if color == _COLOR_PASS:
            color_hex = "#1b5e20"
        elif color == _COLOR_FAIL:
            color_hex = "#b71c1c"
        elif color == _COLOR_WARN:
            color_hex = "#e65100"
        else:
            color_hex = "#424242"
        self._ue_detail_summary.setText(
            f"<b>Moyenne promo :</b> {promo_txt}{promo_cmp}  ·  "
            f"<b>MCC étudiant :</b> {moy_txt}  ·  "
            f"<b>Note d'UE (MCC + jury) :</b> <b>{tot_txt}</b>{waiver_txt}  ·  "
            f"<span style='color:{color_hex};'><b>{label}</b></span>"
            + (
                "<br/><span style='color:#b71c1c;'>"
                "Note d'UE &lt; 7 : cochez <b>Seuil 7</b> pour valider par dérogation jury."
                "</span>"
                if total is not None
                and grade_below_threshold(total, 7.0)
                and not waived
                and not display
                else ""
            )
        )
        if color == _COLOR_PASS:
            self._ue_detail_summary.setStyleSheet(
                "font-size: 12px; padding: 6px 8px; border-radius: 4px; background: #e8f5e9;"
            )
        elif color == _COLOR_FAIL:
            self._ue_detail_summary.setStyleSheet(
                "font-size: 12px; padding: 6px 8px; border-radius: 4px; background: #ffebee;"
            )
        elif color == _COLOR_WARN:
            self._ue_detail_summary.setStyleSheet(
                "font-size: 12px; padding: 6px 8px; border-radius: 4px; background: #fff3e0;"
            )
        else:
            self._ue_detail_summary.setStyleSheet(
                "font-size: 12px; padding: 6px 8px; border-radius: 4px; background: #f5f5f5;"
            )

    def _apply_ue_row_colors(self, student_id: int, row: dict[str, Any]) -> None:
        sid = int(student_id)
        vs = self.view_session
        if self._ue_table is None:
            return
        for table_row, cid in self._ue_row_course.items():
            cid = int(cid)
            d = _ue_detail_dict(row, cid)
            display = self._resolve_ue_display(sid, cid, row)
            display_up = display.upper()
            waived = self.repo.has_ue_jury_floor_waiver(sid, self.template_id, cid)

            moy_ue = _ue_session_average(
                self.repo, sid, self.template_id, cid, row, view_session=vs
            )
            promo = self._cohort_ue_averages.get(cid)
            if promo is None:
                promo = self.repo.get_course_cohort_ue_average(
                    self.template_id, cid, view_session=vs
                )
            promo_it = self._ue_promo_items.get(cid)
            if promo_it is not None:
                promo_it.setText(_fmt(promo))
                promo_it.setBackground(_brush(_COLOR_NEUTRAL))
                if promo is not None and moy_ue is not None:
                    delta = float(moy_ue) - float(promo)
                    sign = "+" if delta >= 0 else ""
                    promo_it.setToolTip(
                        f"MCC de l'étudiant : {_fmt(moy_ue)} ({sign}{delta:.2f} vs promo)"
                    )
                elif moy_ue is not None:
                    promo_it.setToolTip(f"MCC de l'étudiant : {_fmt(moy_ue)}")
                else:
                    promo_it.setToolTip("Moyenne promotion à cette UE")

            jp = self._effective_jury_points(
                sid,
                "course",
                course_id=cid,
                spin_value=(
                    float(self._course_spinboxes[cid].value())
                    if cid in self._course_spinboxes
                    else None
                ),
            )
            if cid not in self._course_spinboxes:
                jp = float(d.get("jury") or 0.0)
            base_f = _ue_session_average(
                self.repo, sid, self.template_id, cid, row, view_session=vs
            )
            total = _ue_total_with_jury(
                row, cid, view_session=vs, jury_points=jp
            )
            if total is None and base_f is not None:
                total = float(base_f) + jp
            elif total is None and base_f is None and not display:
                total = None

            tot_txt = display if display else _fmt(total)
            if waived and not display:
                tot_txt = f"{tot_txt} (seuil 7)"
            tot_it = self._ue_total_items.get(cid)
            if tot_it is not None:
                tot_it.setText(tot_txt)
                status_bg = _color_for_ue_display(display_up)
                tot_bg = status_bg or _color_for_grade_20(
                    float(total) if total is not None and not display else None
                )
                if tot_bg:
                    tot_it.setBackground(_brush(tot_bg))

            res_it = self._ue_result_items.get(cid)
            if res_it is not None:
                can_compensate = self.repo.block_allows_ue_compensation(
                    sid,
                    self.template_id,
                    cid,
                    result_row=row,
                    view_session=vs,
                )
                comp_status = self.repo.block_ue_compensation_status(
                    sid,
                    self.template_id,
                    cid,
                    result_row=row,
                    view_session=vs,
                )
                label, res_bg = _ue_validation_status(
                    self.repo,
                    sid,
                    self.template_id,
                    cid,
                    display=display,
                    total_with_jury=total if not display else None,
                    waived=waived,
                    compensation_allowed=can_compensate,
                    compensation_status=comp_status,
                )
                res_it.setText(label)
                if res_bg:
                    res_it.setBackground(_brush(res_bg))

        block_avgs = row.get("blocks") or {}
        for table_row in self._ue_block_rows:
            title = self._ue_table.item(table_row, 0)
            if title is None:
                continue
            bk_name = str(title.data(Qt.ItemDataRole.UserRole) or "").strip()
            self._paint_block_row(sid, table_row, bk_name, row, vs, block_avgs)
        self._ue_table.viewport().update()

    def _block_average_display(
        self,
        student_id: int,
        block_name: str,
        row: dict[str, Any],
        block_avgs: dict[str, Any],
    ) -> float | None:
        bk_name = str(block_name or "").strip()
        avg = block_avgs.get(bk_name)
        if avg is None:
            for k, v in block_avgs.items():
                if str(k or "").strip() == bk_name:
                    avg = v
                    break
        if avg is None:
            return None
        stored_bjp = self._jury_points(int(student_id), "block", block_name=bk_name)
        live_bjp = stored_bjp
        if bk_name in self._block_spinboxes:
            live_bjp = self._effective_jury_points(
                int(student_id),
                "block",
                block_name=bk_name,
                spin_value=float(self._block_spinboxes[bk_name].value()),
            )
        return float(avg) - float(stored_bjp) + float(live_bjp)

    def _paint_block_row(
        self,
        student_id: int,
        table_row: int,
        block_name: str,
        row: dict[str, Any],
        view_session: str,
        block_avgs: dict[str, Any] | None = None,
    ) -> None:
        if self._ue_table is None:
            return
        sid = int(student_id)
        bk_name = str(block_name or "").strip()
        avgs = block_avgs if block_avgs is not None else (row.get("blocks") or {})
        avg_display = self._block_average_display(sid, bk_name, row, avgs)

        ok = False
        if avg_display is not None and self.repo.block_has_mandatory_courses(
            self.template_id, bk_name
        ):
            ok = self.repo.block_is_validated(
                sid,
                self.template_id,
                bk_name,
                view_session=view_session,
                block_average=avg_display,
            )
        if avg_display is None:
            status_txt = "INCOMPLET"
            bg = _COLOR_NEUTRAL
            fg = QColor("#424242")
        elif ok:
            if (
                avg_display is not None
                and grade_below_threshold(avg_display, 10.0)
                and self.repo.has_block_jury_validation_waiver(sid, self.template_id, bk_name)
            ):
                status_txt = "VALIDÉ (dérog.) ✓"
            else:
                status_txt = "VALIDÉ ✓"
            bg = _COLOR_PASS
            fg = QColor("#1b5e20")
        else:
            status_txt = "NON VALIDÉ ✗"
            bg = _COLOR_FAIL
            fg = QColor("#b71c1c")

        expanded = bk_name not in self._folded_blocks
        arrow = "▼" if expanded else "▶"
        avg_txt = _fmt(avg_display)
        title = self._ue_table.item(table_row, 0)
        if title is not None:
            title.setText(f"{arrow} {bk_name}   ·   moy. {avg_txt}   ·   {status_txt}")
            title.setBackground(_brush(bg))
            title.setForeground(QBrush(fg))
        blk_avg_it = self._ue_table.item(table_row, 1)
        if blk_avg_it is not None:
            blk_avg_it.setText(avg_txt)
            blk_avg_it.setBackground(_brush(bg))
            blk_avg_it.setForeground(QBrush(fg))
        blk_status_it = self._block_status_items.get(table_row)
        if blk_status_it is not None:
            blk_status_it.setText(status_txt)
            blk_status_it.setBackground(_brush(bg))
            blk_status_it.setForeground(QBrush(fg))
        for col in (2, 5, 6):
            cell = self._ue_table.item(table_row, col)
            if cell is not None:
                cell.setBackground(_brush(bg))

    def _apply_block_fold_state(self) -> None:
        if self._ue_table is None:
            return
        for bk_name, ue_rows in self._block_ue_rows.items():
            hidden = bk_name in self._folded_blocks
            for r in ue_rows:
                self._ue_table.setRowHidden(int(r), hidden)
            block_row = self._block_row_by_name.get(bk_name)
            if block_row is None:
                continue
            title = self._ue_table.item(int(block_row), 0)
            if title is None:
                continue
            expanded = not hidden
            arrow = "▼" if expanded else "▶"
            text = title.text()
            if "   ·   moy." in text:
                rest = text.split("   ·   moy.", 1)[1]
                title.setText(f"{arrow} {bk_name}   ·   moy.{rest}")
            elif text.startswith("▶ ") or text.startswith("▼ "):
                title.setText(f"{arrow} {text[2:]}")

    def _toggle_block_fold(self, block_name: str) -> None:
        bk = str(block_name or "").strip()
        if not bk:
            return
        if bk in self._folded_blocks:
            self._folded_blocks.discard(bk)
        else:
            self._folded_blocks.add(bk)
        self._apply_block_fold_state()
        if self._ue_table is not None and self._selected_course_id is not None:
            sel_rows = {idx.row() for idx in self._ue_table.selectedIndexes()}
            if any(self._ue_table.isRowHidden(r) for r in sel_rows):
                if self._assessment_table is not None:
                    self._assessment_table.setRowCount(0)
                self._selected_course_id = None
                if self._ue_detail_summary is not None:
                    self._ue_detail_summary.setText(
                        "Sélectionnez une UE pour voir le détail des épreuves."
                    )

    def _fold_all_blocks(self) -> None:
        self._folded_blocks = set(self._block_ue_rows.keys())
        self._apply_block_fold_state()

    def _unfold_all_blocks(self) -> None:
        self._folded_blocks.clear()
        self._apply_block_fold_state()

    def _on_ue_cell_clicked(self, row: int, col: int) -> None:
        if self._loading or self._ue_table is None:
            return
        if row not in self._ue_block_rows:
            return
        if col not in (0, 1, 2, 4):
            return
        title = self._ue_table.item(row, 0)
        if title is None:
            return
        bk = str(title.data(Qt.ItemDataRole.UserRole) or "").strip()
        if bk:
            self._toggle_block_fold(bk)

    def _first_visible_ue_row(self) -> int | None:
        if self._ue_table is None:
            return None
        for r in sorted(self._ue_row_course):
            if not self._ue_table.isRowHidden(r):
                return r
        return None

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
        self._invalidate_student_issue_cache(int(sid))
        self.repo.maybe_clear_second_session_without_trigger(
            int(sid), self.template_id, int(_cid)
        )
        self._sync_s2_checkboxes(int(sid))
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
            jury_session_id=self.jury_session_id,
        )
        self._invalidate_student_issue_cache(int(student_id))
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
            jury_session_id=self.jury_session_id,
        )
        self._invalidate_student_issue_cache(int(student_id))
        self._refresh_summary(int(student_id))

    def _save_block_validation_waiver(
        self, student_id: int, block_name: str, waived: bool
    ) -> None:
        if self._loading:
            return
        self.repo.set_block_jury_validation_waiver(
            int(student_id),
            self.template_id,
            block_name,
            waived=bool(waived),
        )
        self._invalidate_student_issue_cache(int(student_id))
        self._refresh_summary(int(student_id))

    def _save_year_jury(self, student_id: int) -> None:
        if self._loading or self._year_spin is None:
            return
        self.repo.upsert_jury_adjustment(
            int(student_id),
            self.template_id,
            "year",
            points=float(self._year_spin.value()),
            jury_session_id=self.jury_session_id,
        )
        self._invalidate_student_issue_cache(int(student_id))
        self._refresh_summary(int(student_id))

    def _save_s2(self, student_id: int, course_id: int, sent: bool) -> None:
        if not self._allow_s2_decision():
            return
        sid, cid = int(student_id), int(course_id)
        if bool(sent) and not self.repo.can_send_to_second_session(
            sid, self.template_id, cid, s1_jury=True
        ):
            QMessageBox.warning(
                self,
                "2ᵉ session",
                "Envoi impossible : des notes de session 2 existent déjà pour cette UE.",
            )
            self._sync_s2_checkboxes(sid)
            return
        if (
            not bool(sent)
            and self.repo.second_session_decision_locked(
                sid, self.template_id, cid, s1_jury=self._allow_s2_decision()
            )
        ):
            QMessageBox.warning(
                self,
                "2ᵉ session",
                "Impossible d'annuler l'envoi : des notes de session 2 sont déjà saisies.",
            )
            self._sync_s2_checkboxes(sid)
            return
        try:
            self.repo.set_second_session_decision(
                sid,
                self.template_id,
                cid,
                sent=bool(sent),
                s1_jury=True,
                jury_session_id=self.jury_session_id,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "2ᵉ session", str(exc))
            self._sync_s2_checkboxes(sid)
            return
        self._invalidate_student_issue_cache(sid)
        self._sync_s2_checkboxes(sid)
        self._refresh_summary(sid)

    def _sync_s2_checkboxes(self, student_id: int) -> None:
        sid = int(student_id)
        for cid, chk in self._s2_checks.items():
            want = self.repo.is_sent_to_second_session(sid, self.template_id, int(cid))
            chk.blockSignals(True)
            chk.setChecked(bool(want))
            chk.blockSignals(False)

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
        self._invalidate_student_issue_cache(int(student_id))
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
        self._update_progression_ui(student_id)

    def _m2_track_changed(self) -> None:
        if self._m2_track_combo is None or self._m2_track_other is None:
            return
        is_other = self._m2_track_combo.currentData() == OTHER_TRACK_DATA
        self._m2_track_other.setVisible(is_other)
        self._m2_track_other.setEnabled(is_other)

    def _m2_track_value(self) -> str:
        if self._m2_track_combo is None:
            return ""
        data = self._m2_track_combo.currentData()
        if data == OTHER_TRACK_DATA:
            return (self._m2_track_other.text() if self._m2_track_other else "").strip().upper()
        return str(data or "").strip().upper()

    def _set_m2_track_value(self, code: str) -> None:
        if self._m2_track_combo is None:
            return
        target = str(code or "").strip().upper()
        idx = self._m2_track_combo.findData(target)
        if idx >= 0:
            self._m2_track_combo.setCurrentIndex(idx)
            return
        other_idx = self._m2_track_combo.findData(OTHER_TRACK_DATA)
        if other_idx >= 0 and self._m2_track_other is not None:
            self._m2_track_combo.setCurrentIndex(other_idx)
            self._m2_track_other.setText(target)

    def _save_progression_track(self, student_id: int) -> None:
        if self._outcome_combo is None or self._m2_track_combo is None:
            return
        if str(self._outcome_combo.currentData() or "") != "pass_m2":
            return
        track = self._m2_track_value()
        if not track:
            return
        self.repo.upsert_jury_student_outcome(
            int(student_id),
            self.template_id,
            jury_session_id=self.jury_session_id,
            progression_track=track,
        )

    def _update_progression_ui(self, student_id: int) -> None:
        if self._apply_progression_btn is None:
            return
        outcome = str(self._outcome_combo.currentData() or "") if self._outcome_combo else ""
        if self._template_level == "M1":
            show_m2 = outcome == "pass_m2"
            if self._m2_track_row_label is not None:
                self._m2_track_row_label.setVisible(show_m2)
            if self._m2_track_widget is not None:
                self._m2_track_widget.setVisible(show_m2)
            self._apply_progression_btn.setEnabled(outcome in {"pass_m2", "repeat"})
        elif self._template_level == "M2":
            if self._progression_year is not None:
                self._progression_year.setEnabled(outcome == "repeat")
            self._apply_progression_btn.setEnabled(outcome in {"validate_year", "repeat"})
        self._refresh_progression_status(student_id)

    def _refresh_progression_status(self, student_id: int) -> None:
        if self._progression_status is None:
            return
        outcome = str(self._outcome_combo.currentData() or "") if self._outcome_combo else ""
        student = self.repo.get_student(int(student_id)) or {}
        if self._template_level == "M2":
            if outcome == "validate_year":
                if normalize_student_status(student.get("status")) == STUDENT_STATUS_GRADUATED:
                    self._progression_status.setText(
                        "✓ Formation clôturée — statut diplômé (hors listes actives)."
                    )
                else:
                    self._progression_status.setText(
                        "Décision enregistrée ; cliquez sur « Appliquer » pour clore la formation."
                    )
                return
            if outcome != "repeat":
                self._progression_status.clear()
                return
        elif outcome not in {"pass_m2", "repeat"}:
            self._progression_status.clear()
            return
        if self._progression_year is None:
            return
        target_ay = self._progression_year.text().strip()
        lv = str(student.get("level") or "").strip().upper()
        cur_ay = str(student.get("academic_year") or "").strip()
        tr = str(student.get("track") or "").strip().upper()
        if outcome == "pass_m2":
            m2 = self._m2_track_value()
            if lv == "M2" and cur_ay == target_ay and tr == m2:
                self._progression_status.setText(
                    f"✓ Fiche à jour : M2 {tr}, millésime {cur_ay}."
                )
                return
            self._progression_status.setText(
                "La décision est enregistrée ; cliquez sur « Appliquer » pour mettre à jour la fiche."
            )
            return
        if lv == self._template_level and cur_ay == target_ay:
            self._progression_status.setText(
                f"✓ Fiche à jour : {lv}, millésime {cur_ay}."
            )
            return
        self._progression_status.setText(
            "La décision est enregistrée ; cliquez sur « Appliquer » pour le redoublement."
        )

    def _apply_progression(self, student_id: int) -> None:
        outcome = str(self._outcome_combo.currentData() or "") if self._outcome_combo else ""
        target_ay = ""
        m2_track = ""
        if outcome == "validate_year":
            if self._template_level != "M2":
                QMessageBox.information(
                    self,
                    "Progression",
                    "La clôture de formation concerne les décisions « Année validée » en M2.",
                )
                return
        elif outcome == "pass_m2":
            if self._progression_year is None:
                return
            target_ay = self._progression_year.text().strip()
            if not target_ay:
                QMessageBox.warning(self, "Progression", "Indiquez le millésime cible.")
                return
            m2_track = self._m2_track_value()
            if not m2_track:
                QMessageBox.warning(self, "Progression", "Choisissez le parcours M2.")
                return
            self._save_progression_track(student_id)
        elif outcome == "repeat":
            if self._progression_year is None:
                return
            target_ay = self._progression_year.text().strip()
            if not target_ay:
                QMessageBox.warning(self, "Progression", "Indiquez le millésime cible.")
                return
        else:
            QMessageBox.information(
                self,
                "Progression",
                "Décisions applicables : « Admis en M2 » / « Redoublement » (M1), "
                "« Année validée » / « Redoublement » (M2).",
            )
            return
        try:
            msg = self.repo.apply_final_jury_progression(
                int(student_id),
                self.template_id,
                jury_session_id=self.jury_session_id,
                new_academic_year=target_ay,
                m2_track=m2_track,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Progression", str(exc))
            return
        QMessageBox.information(self, "Progression", msg)
        self._update_progression_ui(student_id)

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
                "(décision enregistrée ; choisissez le parcours M2 puis « Appliquer » si besoin).</span>"
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
            retake_hint = ""
            if sug_key == "repeat":
                retake_vs = "mixed" if self.session_kind == "FINAL" else self.view_session
                retake = self.repo.courses_to_retake_for_student(
                    int(student_id),
                    self.template_id,
                    view_session=retake_vs,
                    result_row=row,
                )
                rtxt = self.repo.format_courses_to_retake_text(retake)
                if rtxt:
                    retake_hint = f"<br/><b>UE à repasser :</b> {rtxt}"
            self._validation_label.setText(
                f"<span style='color:#b71c1c;'><b>Non validé</b></span><br/>"
                f"<b>Propositions pour le jury :</b> {prop_labels}<br/>"
                f"<b>Présélection :</b> {sug} (modifiable ci-dessous).{hint_prior}"
                f"{retake_hint}<br/>{body}"
            )
            self._validation_label.setStyleSheet(
                "font-size: 11px; padding: 6px; border-radius: 4px; background: #ffebee;"
            )
        self._apply_suggested_outcome(int(student_id), row, ev)
        self._apply_suggested_mention(int(student_id), row)
        if self.session_kind == "FINAL":
            oc = self.repo.get_jury_student_outcome(
                int(student_id), self.template_id, jury_session_id=self.jury_session_id
            )
            if not (oc and str(oc.get("outcome") or "").strip()):
                self._save_outcome(int(student_id))
                self._save_mention(int(student_id))
                self._refresh_list_item_for_student(int(student_id))
            else:
                self._update_progression_ui(int(student_id))

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
            if self._assessment_table is not None:
                self._assessment_table.blockSignals(True)
            if self._ue_table is not None:
                self._ue_table.blockSignals(True)
            row = self._fetch_result_row(int(student_id))
            if row is None:
                if self.summary_label is not None:
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
                mark = "BLOC VALIDÉ ✓" if ok else "BLOC NON VALIDÉ ✗"
                if avg is None:
                    mark = "BLOC INCOMPLET"
                color = "#1b5e20" if ok else "#b71c1c"
                if avg is None:
                    color = "#616161"
                lines.append(
                    f"• <b>{bk}</b> : {_fmt(avg)} — "
                    f"<span style='color:{color};'><b>{mark}</b></span>"
                )

            editing_item: QTableWidgetItem | None = None
            if self._assessment_table is not None:
                if self._assessment_table.state() == QAbstractItemView.State.EditingState:
                    editing_item = self._assessment_table.currentItem()

            grade_rows_cache: dict[int, list[dict[str, Any]]] = {}
            for aid, note_it in self._grade_items.items():
                if note_it is editing_item:
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
                        assessment_kind=str(ar.get("kind") or ""),
                        assessment_name=str(ar.get("name") or ""),
                    )
                )
                bg = _color_for_grade_20(ar.get("grade") if ar.get("grade") is not None else None)
                if bg:
                    note_it.setBackground(_brush(bg))

            self._apply_ue_row_colors(int(student_id), row)
            self.summary_label.setText("<br/>".join(lines))
            self._refresh_validation_banner(int(student_id), row)
            if self._selected_course_id is not None:
                self._update_ue_detail_summary(
                    int(student_id), int(self._selected_course_id), row=row
                )
            self._sync_s2_checkboxes(int(student_id))
        finally:
            if self._assessment_table is not None:
                self._assessment_table.blockSignals(False)
            if self._ue_table is not None:
                self._ue_table.blockSignals(False)
            self._loading = False

    def _schedule_deliberation_notes_save(self) -> None:
        if self.jury_session_id is None:
            return
        self._notes_save_timer.start()

    def _save_deliberation_notes(self) -> None:
        if self.jury_session_id is None or self._session_notes_edit is None:
            return
        try:
            self.repo.update_jury_session(
                int(self.jury_session_id),
                notes=self._session_notes_edit.toPlainText(),
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Commentaires",
                f"Impossible d'enregistrer les commentaires :\n{exc}",
            )

    def accept(self) -> None:
        self._notes_save_timer.stop()
        self._save_deliberation_notes()
        if self.session_kind == "FINAL" and self.jury_session_id is not None:
            sid = self._current_student_id()
            if sid is not None:
                oc = self.repo.get_jury_student_outcome(
                    int(sid), self.template_id, jury_session_id=self.jury_session_id
                )
                if not (oc and str(oc.get("outcome") or "").strip()):
                    self._save_outcome(int(sid))
                    self._save_mention(int(sid))
        super().accept()

    def closeEvent(self, event) -> None:
        self._notes_save_timer.stop()
        self._save_deliberation_notes()
        super().closeEvent(event)
