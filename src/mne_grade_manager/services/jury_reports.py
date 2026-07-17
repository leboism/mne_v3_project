"""
Génération PDF pour l’onglet Jury (brouillons : tableau de notes, PV, relevé partiel).
Les mises en page pourront être rapprochées des modèles institutionnels (PV_M1C, transcripts…).
"""

from __future__ import annotations

import colorsys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository import Repository

from ..core.mne_modules import course_ue_code
from ..services.timetable_legacy import course_public_code
from ..core.parcours import mne_level_master_line, track_program_label
from . import terminology as T
from .calculations import grade_meets_minimum, weighted_average
from .lookups import student_transcript_number, TRACKS
from .grade_status import STATUS_ABJ, STATUS_DEF, STATUS_NEUT, STATUS_VAL
from .jury_excel import split_jury_president_and_members

TRANSCRIPT_MENTION_LABELS: dict[str, str] = {
    "": "—",
    "assez_bien": "Assez bien",
    "bien": "Bien",
    "tres_bien": "Très bien",
    "excellent": "Excellent",
}

JURY_OUTCOME_LABELS: dict[str, str] = {
    "validate_year": "Année validée",
    "pass_m2": "Admis en M2",
    "repeat": "Redoublement",
    "refuse_repeat": "Refus de redoublement",
}

# Charte visuelle MNE (alignée transcripts / PV / tableaux jury).
MNE_TITLE_BLUE = "#1F4E79"  # legacy
MNE_HEADER_BLUE = "#2F5496"  # legacy
MNE_ACCENT_ORANGE = "#ED7D31"
MNE_TRANSCRIPT_SECTION_BG = "#FBE5D6"
MNE_ROW_ALT = "#F2F2F2"
MNE_TABLE_GRID = "#B4B4B4"
# Fonds cellules onglet Résultats (QColor) — la police PDF reprend la même teinte.
MNE_RESULT_CELL_PASS_RGB = (198, 239, 206)
MNE_RESULT_CELL_WARN_RGB = (255, 224, 178)
MNE_RESULT_CELL_FAIL_RGB = (255, 205, 210)
MNE_RESULT_CELL_NEUTRAL_RGB = (232, 234, 237)
MNE_TRANSCRIPT_BLOCK_BG = "#D9E2F3"
MNE_TRANSCRIPT_BLOCK_TEXT = "#1F3864"
MNE_TRANSCRIPT_GRID = "#D0D0D0"
MNE_SIGNATURE_COL = 3
TRANSCRIPT_HEADER_HEIGHT_PT = 82.0
TRANSCRIPT_LOGO_GAP_PT = 5.0
TRANSCRIPT_TABLE_FONT_PT = 8.5
TRANSCRIPT_BODY_FONT_PT = 9.0
TRANSCRIPT_BODY_LEADING_PT = 11.0
TRANSCRIPT_PAGE_FOOTER_BAND_PT = 42.0
TRANSCRIPT_SIGNATURE_GAP_PT = 14.0
TRANSCRIPT_SIGNATURE_LIFT_CM = 1.0
TRANSCRIPT_SIGNATURE_LINE_PT = 11.0


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm

    styles = getSampleStyleSheet()
    accent = colors.HexColor(MNE_ACCENT_ORANGE)
    title = ParagraphStyle(
        name="JuryTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=16,
        textColor=accent,
        alignment=TA_CENTER,
        spaceAfter=0.4 * cm,
    )
    h2 = ParagraphStyle(
        name="JuryH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=accent,
        spaceAfter=0.2 * cm,
        spaceBefore=0.3 * cm,
    )
    body = ParagraphStyle(name="JuryBody", parent=styles["Normal"], fontSize=9, leading=11)
    return colors, title, h2, body


def _mne_pv_paragraph_styles(colors):
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle

    accent = colors.HexColor(MNE_ACCENT_ORANGE)
    return {
        "master": ParagraphStyle(
            name="MneMasterTitle",
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            textColor=accent,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "year": ParagraphStyle(
            name="MneAcademicYear",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=accent,
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "jury": ParagraphStyle(
            name="MneJuryTitle",
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=accent,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "program": ParagraphStyle(
            name="MneProgramLine",
            fontName="Helvetica",
            fontSize=10,
            leading=12,
            textColor=accent,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "section": ParagraphStyle(
            name="MneSectionHdr",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=accent,
            spaceBefore=8,
            spaceAfter=4,
        ),
    }


def _format_pv_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = raw.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        return f"{int(d):02d}/{int(m):02d}/{y}"
    return raw


def _member_affiliation(member: dict[str, Any]) -> str:
    title = str(member.get("title") or "").strip()
    aff = str(member.get("affiliation") or member.get("institution") or "").strip()
    if title and aff:
        return f"{title} — {aff}"
    return title or aff or ""


def _append_mne_pv_header(
    story: list,
    *,
    colors,
    academic_year: str = "",
    program_line: str = "",
    jury_title: str = "Jury",
    session_subtitle: str = "",
) -> None:
    from reportlab.lib.units import cm
    from reportlab.platypus import HRFlowable, Paragraph, Spacer

    ps = _mne_pv_paragraph_styles(colors)
    story.append(Paragraph("Master Nuclear Energy", ps["master"]))
    ay = str(academic_year or "").strip()
    if ay:
        story.append(Paragraph(f"Année Universitaire {ay}", ps["year"]))
    story.append(Spacer(1, 2))
    story.append(Paragraph(jury_title, ps["jury"]))
    sub = str(session_subtitle or "").strip()
    if sub:
        story.append(Paragraph(sub, ps["program"]))
    prog = str(program_line or "").strip()
    if prog:
        story.append(Paragraph(prog, ps["program"]))
    story.append(
        HRFlowable(
            width="100%",
            thickness=2,
            color=colors.HexColor(MNE_ACCENT_ORANGE),
            spaceBefore=4,
            spaceAfter=0.35 * cm,
        )
    )


def _institutional_table_style(
    colors, *, font_size: int = 8, header_hex: str = MNE_ACCENT_ORANGE
) -> list[tuple]:
    return [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_hex)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), font_size),
        ("FONTSIZE", (0, 1), (-1, -1), font_size),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor(MNE_TABLE_GRID)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(MNE_ROW_ALT)]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]


def _transcript_table_style(colors, *, font_size: float = TRANSCRIPT_TABLE_FONT_PT) -> list[tuple]:
    fs = int(round(font_size))
    grid = colors.HexColor(MNE_TRANSCRIPT_GRID)
    return [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(MNE_ACCENT_ORANGE)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), fs),
        ("FONTSIZE", (0, 1), (-1, -1), fs),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, grid),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor(MNE_ACCENT_ORANGE)),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
    ]


def _transcript_session_paragraph(session_label: str, *, body_compact) -> Any:
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    text = str(session_label or "").strip()
    if not text:
        return ""
    ps = ParagraphStyle(
        name="TrSession",
        parent=body_compact,
        fontSize=max(7.5, TRANSCRIPT_TABLE_FONT_PT - 0.5),
        leading=TRANSCRIPT_BODY_LEADING_PT,
        alignment=1,
    )
    return Paragraph(text, ps)


def _transcript_academic_year_line(academic_year: str, view_session: str, *, final: bool) -> str:
    ay = str(academic_year or "").strip()
    if final:
        return f"Academic year {ay}" if ay else ""
    vs = str(view_session or "mixed").strip().lower()
    if vs == "mixed":
        sess = "Retained grades (S2 when available)"
    elif vs == "s2":
        sess = "Second Session"
    else:
        sess = "First Session"
    return f"Academic year {ay} : {sess}" if ay else sess


def _signature_box_styles(colors, sig_col: int, n_rows: int) -> list[tuple]:
    from reportlab.lib.units import cm

    out: list[tuple] = [
        ("ROWHEIGHT", (sig_col, 1), (sig_col, -1), 1.35 * cm),
        ("BACKGROUND", (sig_col, 1), (sig_col, -1), colors.white),
    ]
    for row in range(1, n_rows):
        out.append(("BOX", (sig_col, row), (sig_col, row), 0.75, colors.HexColor("#808080")))
    return out


def _build_jury_members_table(members: list[dict[str, Any]], colors) -> Any:
    from reportlab.lib.units import cm
    from reportlab.platypus import Table, TableStyle

    headers = ["Nom", "Prénom", "Affiliation", "Signature"]
    rows: list[list[str]] = [headers]
    for member in members:
        rows.append(
            [
                str(member.get("last_name") or ""),
                str(member.get("first_name") or ""),
                _member_affiliation(member),
                "",
            ]
        )
    col_widths = [2.35 * cm, 2.35 * cm, 8.8 * cm, 2.75 * cm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    style_cmds = list(_institutional_table_style(colors, font_size=8))
    style_cmds.extend(_signature_box_styles(colors, MNE_SIGNATURE_COL, len(rows)))
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _append_jury_roster_sections(
    story: list,
    members: list[dict[str, Any]],
    *,
    colors,
) -> None:
    from reportlab.platypus import Paragraph, Spacer

    ps = _mne_pv_paragraph_styles(colors)
    if not members:
        story.append(Paragraph("Aucun membre du jury enregistré pour cette délibération.", ps["program"]))
        return

    president, others = split_jury_president_and_members(members)
    if president is None:
        story.append(Paragraph("Aucun membre du jury enregistré pour cette délibération.", ps["program"]))
        return
    story.append(Paragraph("Président", ps["section"]))
    story.append(_build_jury_members_table([president], colors))
    story.append(Spacer(1, 8))
    if others:
        story.append(Paragraph("Membres", ps["section"]))
        story.append(_build_jury_members_table(others, colors))


def _institutional_data_table(rows: list[list[Any]], colors, *, font_size: int = 8) -> Any:
    from reportlab.platypus import Table, TableStyle

    tbl = Table(rows, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle(_institutional_table_style(colors, font_size=font_size)))
    return tbl


def _group_second_session_by_course(
    repo: Repository,
    template_id: int,
    s2_rows: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Groupe les envois S2 par UE (ordre maquette), étudiants triés par nom."""
    by_course: dict[int, list[dict[str, Any]]] = {}
    for row in s2_rows:
        cid = int(row.get("course_id") or 0)
        if cid <= 0:
            continue
        by_course.setdefault(cid, []).append(row)
    for students in by_course.values():
        students.sort(
            key=lambda x: (str(x.get("st_last") or ""), str(x.get("st_first") or ""))
        )

    grouped: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    seen: set[int] = set()
    for _bk, courses in repo.list_template_blocks_with_courses(int(template_id)):
        for course in courses:
            cid = int(course.get("course_id") or 0)
            if cid in by_course:
                grouped.append((course, by_course[cid]))
                seen.add(cid)
    for cid, students in sorted(by_course.items(), key=lambda kv: str(kv[1][0].get("course_code") or "")):
        if cid in seen:
            continue
        row0 = students[0]
        grouped.append(
            (
                {
                    "course_id": cid,
                    "code": row0.get("course_code"),
                    "name": row0.get("course_name"),
                },
                students,
            )
        )
    return grouped


def _append_pv_general_comments(
    story: list,
    notes: str,
    *,
    ps,
    body_style,
    title: str = "Commentaires généraux",
) -> None:
    """Verbatim / décisions collectives enregistrées sur la délibération."""
    from reportlab.platypus import Paragraph, Spacer

    text = str(notes or "").strip()
    if not text:
        return
    title = str(title or "").strip()
    if title:
        story.append(Paragraph(title, ps["section"]))
    for line in text.splitlines():
        chunk = str(line).strip()
        if not chunk:
            story.append(Spacer(1, 4))
            continue
        safe = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(safe, body_style))
    story.append(Spacer(1, 8))


def _append_pv_second_session_by_ue(
    story: list,
    *,
    repo: Repository,
    template_id: int,
    s2_rows: list[dict[str, Any]],
    colors,
    ps,
    body_style,
    academic_year: str = "",
    include_ine: bool = True,
) -> None:
    """Section PV : une sous-partie par UE avec la liste des étudiants convoqués."""
    from reportlab.platypus import Paragraph, Spacer

    story.append(Paragraph("Étudiants envoyés en seconde session (par UE)", ps["section"]))
    if not s2_rows:
        story.append(Paragraph("Aucune décision enregistrée.", body_style))
        return

    for course, students in _group_second_session_by_course(repo, int(template_id), s2_rows):
        code = course_public_code(course, academic_year=academic_year) or str(course.get("code") or "")
        name = str(course.get("name") or "").strip()
        story.append(Paragraph(f"<b>{code}</b> — {name}", body_style))
        if include_ine:
            srows = [["Étudiant", "N° I.N.E."]]
            for x in students:
                srows.append(
                    [
                        f"{x.get('st_last') or ''} {x.get('st_first') or ''}".strip(),
                        student_transcript_number(x),
                    ]
                )
        else:
            srows = [["Étudiant"]]
            for x in students:
                srows.append([f"{x.get('st_last') or ''} {x.get('st_first') or ''}".strip()])
        story.append(_institutional_data_table(srows, colors))
        story.append(Spacer(1, 6))


def _pv_doc_template(path: str | Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    return SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=44,
        bottomMargin=36,
    )


def _pv_mixed_orientation_doc(path: str | Path):
    """PV portrait + section synthèse finale en paysage (jury final)."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    margin_h = 40
    top_margin = 44
    bottom_margin = 36
    pw, ph = A4
    lw, lh = landscape(A4)

    class _PVMixedDoc(BaseDocTemplate):
        def __init__(self, filename: str) -> None:
            BaseDocTemplate.__init__(
                self,
                filename,
                pagesize=A4,
                rightMargin=margin_h,
                leftMargin=margin_h,
                topMargin=top_margin,
                bottomMargin=bottom_margin,
            )
            portrait_frame = Frame(
                margin_h,
                bottom_margin,
                pw - 2 * margin_h,
                ph - top_margin - bottom_margin,
                id="portrait_frame",
            )
            landscape_frame = Frame(
                margin_h,
                bottom_margin,
                lw - 2 * margin_h,
                lh - top_margin - bottom_margin,
                id="landscape_frame",
            )
            self.addPageTemplates(
                [
                    PageTemplate(id="portrait", frames=portrait_frame, pagesize=A4),
                    PageTemplate(
                        id="landscape",
                        frames=landscape_frame,
                        pagesize=landscape(A4),
                    ),
                ]
            )

    return _PVMixedDoc(str(path))


def _pv_wrapped_cell_style(*, font_size: int = 7):
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        name="PVWrappedCell",
        fontName="Helvetica",
        fontSize=font_size,
        leading=font_size + 2,
        wordWrap="CJK",
    )


def _retake_paragraph_for_pv(retake: dict[str, list[dict[str, Any]]]):
    """Paragraphe PDF multi-lignes pour les UE à repasser (obligatoire / recommandé)."""
    import xml.sax.saxutils as saxutils

    from reportlab.platypus import Paragraph

    mand = retake.get("mandatory") or []
    rec = retake.get("recommended") or []
    if not mand and not rec:
        return ""
    parts: list[str] = []
    if mand:
        bits = []
        for c in mand:
            code = str(c.get("code") or c.get("name") or "UE").strip()
            st = str(c.get("status") or "").strip()
            n = c.get("note")
            if st:
                bits.append(f"{code} ({st})")
            elif n is not None:
                bits.append(f"{code} ({float(n):.2f})")
            else:
                bits.append(code)
        parts.append("<b>Obligatoire :</b> " + saxutils.escape(", ".join(bits)))
    if rec:
        bits = []
        for c in rec:
            code = str(c.get("code") or c.get("name") or "UE").strip()
            n = c.get("note")
            bits.append(f"{code} ({float(n):.2f})" if n is not None else code)
        parts.append("<b>Recommandé :</b> " + saxutils.escape(", ".join(bits)))
    return Paragraph("<br/>".join(parts), _pv_wrapped_cell_style(font_size=7))


def _institutional_landscape_roster_table(rows: list[list[Any]], colors) -> Any:
    """Tableau synthèse jury final en paysage, colonne UE à repasser avec retour à la ligne."""
    from reportlab.lib.units import cm
    from reportlab.platypus import Table, TableStyle

    col_widths = [4.2 * cm, 1.6 * cm, 1.8 * cm, 2.8 * cm, 3.2 * cm, 13.2 * cm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    style_cmds = list(_institutional_table_style(colors, font_size=7))
    style_cmds.extend(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 1), (3, -1), "CENTER"),
        ]
    )
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _pdf_table_header_style():
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        name="GradeMatrixHdr",
        fontSize=7,
        leading=8,
        alignment=1,
    )


def _portrait_doc_template(path: str | Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    return SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=44,
        bottomMargin=36,
    )


def _grade_matrix_table_style(colors, *, font_size: int = 8, extra_cmds: list[tuple] | None = None):
    from reportlab.platypus import TableStyle

    cmds: list[tuple] = [
        *_institutional_table_style(colors, font_size=font_size, header_hex=MNE_ACCENT_ORANGE),
        ("FONTSIZE", (0, 0), (-1, 0), max(7, font_size - 1)),
        ("ALIGN", (3, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if extra_cmds:
        cmds.extend(extra_cmds)
    return TableStyle(cmds)


def _grade_matrix_col_widths(n_ue_cols: int, *, with_block_avg: bool = False, with_year_avg: bool = False):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm

    page_w = A4[0]
    margins = 80  # left + right
    id_w = (2.55 * cm, 2.55 * cm, 1.8 * cm)
    tail = 0
    if with_block_avg:
        tail += 1
    if with_year_avg:
        tail += 1
    tail_w = 1.05 * cm
    fixed = sum(id_w) + tail * tail_w
    budget = page_w - margins - fixed
    if n_ue_cols <= 0:
        ue_w = 1.0 * cm
    else:
        ue_w = max(0.82 * cm, min(1.35 * cm, budget / n_ue_cols))
    return [*id_w, *[ue_w] * n_ue_cols, *([tail_w] * tail)]


def _chunk_list(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def _ue_header_cell(code: str):
    from reportlab.platypus import Paragraph

    return Paragraph(str(code or "?").replace("&", "&amp;"), _pdf_table_header_style())


def _grade_matrix_value(row: dict, course_id: int, view_session: str) -> str:
    d = (row.get("ue_detail") or {}).get(int(course_id)) or {}
    display = str(d.get("display") or "").strip()
    if display:
        return display
    v = _ue_display_value(row, int(course_id), view_session)
    return _pdf_num(v) if v is not None else "—"


def _pdf_text_hex_from_result_cell_rgb(rgb: tuple[int, int, int]) -> str:
    """Teinte police PDF dérivée du fond cellule Résultats (même teinte, lisible sur blanc)."""
    r, g, b = (x / 255.0 for x in rgb)
    h, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    tr, tg, tb = colorsys.hls_to_rgb(h, 0.34, min(1.0, saturation + 0.38))
    return f"#{int(tr * 255):02X}{int(tg * 255):02X}{int(tb * 255):02X}"


def _pdf_grade_cell_kind(v: float | None) -> str | None:
    """Vert > 10, orange 7–10, rouge < 7 ; rien si note absente."""
    if v is None:
        return None
    x = float(v)
    if x > 10.0:
        return "pass"
    if x >= 7.0:
        return "warn"
    return "fail"


def _pdf_hex_for_grade_cell_kind(kind: str) -> str:
    mapping = {
        "pass": MNE_RESULT_CELL_PASS_RGB,
        "warn": MNE_RESULT_CELL_WARN_RGB,
        "fail": MNE_RESULT_CELL_FAIL_RGB,
        "neutral": MNE_RESULT_CELL_NEUTRAL_RGB,
    }
    return _pdf_text_hex_from_result_cell_rgb(mapping[kind])


def _pdf_ue_display_status(row: dict[str, Any], course_id: int) -> str:
    d = (row.get("ue_detail") or {}).get(int(course_id)) or {}
    if d.get("ects_validated"):
        return STATUS_VAL
    return str(d.get("display") or "").strip().upper()


def _pdf_ue_cell_color_kind(row: dict[str, Any], course_id: int, view_session: str) -> str | None:
    display = _pdf_ue_display_status(row, course_id)
    if display in (STATUS_DEF, STATUS_ABJ):
        return "fail"
    if display == STATUS_NEUT:
        return "neutral"
    if display == STATUS_VAL:
        return "pass"
    v = _ue_display_value(row, int(course_id), view_session)
    return _pdf_grade_cell_kind(v)


def _pdf_block_avg_color_kind(
    repo: Repository,
    *,
    template_id: int,
    row: dict[str, Any],
    block_name: str,
    view_session: str,
) -> str | None:
    if not repo.block_has_mandatory_courses(int(template_id), block_name):
        return "neutral"
    sid = int(row.get("student_id") or 0)
    avg = (row.get("blocks") or {}).get(block_name)
    if avg is None:
        return "fail"
    if repo.block_is_validated(
        sid,
        int(template_id),
        block_name,
        view_session=view_session,
        block_average=avg,
    ):
        return "pass"
    return "fail"


def _pdf_validation_color_kind(v: float | None) -> str | None:
    if v is None:
        return None
    return "pass" if float(v) >= 10.0 else "fail"


def _grade_matrix_color_cmds(
    colors,
    *,
    repo: Repository,
    template_id: int,
    data: list[dict[str, Any]],
    chunk: list[dict[str, Any]],
    block_name: str,
    view_session: str,
    with_block_avg: bool,
    row_offset: int = 1,
) -> list[tuple]:
    """Commandes TableStyle : couleur de police alignée sur l'onglet Résultats."""
    vs = str(view_session or "s1").lower()
    ue_col_start = 3
    cmds: list[tuple] = []
    for r_idx, row in enumerate(data):
        table_row = row_offset + r_idx
        for c_idx, course in enumerate(chunk):
            kind = _pdf_ue_cell_color_kind(row, int(course["course_id"]), vs)
            if not kind:
                continue
            col = ue_col_start + c_idx
            hex_color = _pdf_hex_for_grade_cell_kind(kind)
            cmds.append(
                ("TEXTCOLOR", (col, table_row), (col, table_row), colors.HexColor(hex_color))
            )
            if kind in ("fail", "warn"):
                cmds.append(
                    ("FONTNAME", (col, table_row), (col, table_row), "Helvetica-Bold")
                )
        if with_block_avg:
            blk_col = ue_col_start + len(chunk)
            kind = _pdf_block_avg_color_kind(
                repo,
                template_id=int(template_id),
                row=row,
                block_name=block_name,
                view_session=vs,
            )
            if kind:
                hex_color = _pdf_hex_for_grade_cell_kind(kind)
                cmds.append(
                    ("TEXTCOLOR", (blk_col, table_row), (blk_col, table_row), colors.HexColor(hex_color))
                )
                if kind == "fail":
                    cmds.append(
                        ("FONTNAME", (blk_col, table_row), (blk_col, table_row), "Helvetica-Bold")
                    )
    return cmds


def _year_summary_color_cmds(colors, *, data: list[dict[str, Any]]) -> list[tuple]:
    cmds: list[tuple] = []
    year_col = 3
    for r_idx, row in enumerate(data):
        table_row = r_idx + 1
        kind = _pdf_validation_color_kind(row.get("global_with_jury"))
        if not kind:
            continue
        hex_color = _pdf_hex_for_grade_cell_kind(kind)
        cmds.append(
            ("TEXTCOLOR", (year_col, table_row), (year_col, table_row), colors.HexColor(hex_color))
        )
        if kind == "fail":
            cmds.append(
                ("FONTNAME", (year_col, table_row), (year_col, table_row), "Helvetica-Bold")
            )
    return cmds


def _max_ue_cols_portrait(*, with_block_avg: bool = False, with_year_avg: bool = False) -> int:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm

    page_w = A4[0]
    margins = 80
    id_w = 2.55 * cm + 2.55 * cm + 1.8 * cm
    tail = (1 if with_block_avg else 0) + (1 if with_year_avg else 0)
    tail_w = 1.05 * cm * tail
    budget = page_w - margins - id_w - tail_w
    return max(1, int(budget / (0.82 * cm)))


def _append_portrait_grade_tables(
    story: list,
    *,
    repo: Repository,
    template_id: int,
    data: list[dict],
    blocks: list[tuple[str, list[dict[str, Any]]]],
    view_session: str,
    colors,
    h2_style,
    include_year_summary: bool = True,
    academic_year: str = "",
) -> None:
    from reportlab.platypus import Paragraph, Spacer, Table

    vs = str(view_session or "s1").lower()
    max_cols = _max_ue_cols_portrait(with_block_avg=True)

    for block_name, clist in blocks:
        courses = [c for c in clist if not int(c.get("optional") or 0)]
        if not courses:
            continue
        story.append(Paragraph(str(block_name or "Bloc"), h2_style))
        chunks = _chunk_list(courses, max_cols)
        for chunk_idx, chunk in enumerate(chunks):
            is_last = chunk_idx == len(chunks) - 1
            headers: list[Any] = ["Nom", "Prénom", "I.N.E."]
            headers.extend(
                _ue_header_cell(course_public_code(c, academic_year=academic_year) or str(c.get("code") or ""))
                for c in chunk
            )
            if is_last:
                headers.append("Moy. bloc")
            rows = [headers]
            for row in data:
                rcells: list[Any] = [
                    str(row.get("last_name") or ""),
                    str(row.get("first_name") or ""),
                    student_transcript_number(row),
                ]
                for c in chunk:
                    rcells.append(_grade_matrix_value(row, int(c["course_id"]), vs))
                if is_last:
                    blk_avg = (row.get("blocks") or {}).get(block_name)
                    if blk_avg is None:
                        blk_avg = (row.get("blocks") or {}).get(str(block_name or ""))
                    rcells.append(_pdf_num(float(blk_avg)) if blk_avg is not None else "—")
                rows.append(rcells)
            col_w = _grade_matrix_col_widths(len(chunk), with_block_avg=is_last)
            color_cmds = _grade_matrix_color_cmds(
                colors,
                repo=repo,
                template_id=int(template_id),
                data=data,
                chunk=chunk,
                block_name=block_name,
                view_session=vs,
                with_block_avg=is_last,
            )
            tbl = Table(rows, colWidths=col_w, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(_grade_matrix_table_style(colors, extra_cmds=color_cmds))
            story.append(tbl)
            if not is_last:
                story.append(Spacer(1, 6))
        story.append(Spacer(1, 10))

    if include_year_summary and data:
        story.append(Paragraph("Moyenne année", h2_style))
        rows = [["Nom", "Prénom", "I.N.E.", "Moy. année"]]
        for row in data:
            gw = row.get("global_with_jury")
            rows.append(
                [
                    str(row.get("last_name") or ""),
                    str(row.get("first_name") or ""),
                    student_transcript_number(row),
                    _pdf_num(float(gw)) if gw is not None else "—",
                ]
            )
        col_w = _grade_matrix_col_widths(0, with_year_avg=True)
        year_cmds = _year_summary_color_cmds(colors, data=data)
        tbl = Table(rows, colWidths=col_w, repeatRows=1, hAlign="LEFT")
        tbl.setStyle(_grade_matrix_table_style(colors, extra_cmds=year_cmds))
        story.append(tbl)


def write_grade_matrix_pdf(
    repo: Repository,
    *,
    template_id: int,
    view_session: str,
    path: str | Path,
) -> None:
    """Tableau des notes en portrait, découpé par blocs (modèle M1P / M1C)."""
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer

    colors, title_style, h2_style, body_style = _styles()
    accent = colors.HexColor(MNE_ACCENT_ORANGE)
    subtitle = ParagraphStyle(
        name="JuryGradeSubtitle",
        parent=body_style,
        alignment=TA_CENTER,
        textColor=accent,
    )
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    title_txt = str(tpl.get("name") or "Maquette")
    ay = str(tpl.get("academic_year") or "")
    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    vs = str(view_session or "s1")

    data = repo.get_student_result_summary(
        int(template_id), view_session=vs, include_all_students=True
    )
    blocks = repo.list_template_blocks_with_courses(int(template_id))

    story: list = [
        Paragraph(f"Tableau des notes — {title_txt}", title_style),
        Paragraph(f"{ay} · {lv} {tr} · session affichée : {vs.upper()}", subtitle),
        Spacer(1, 12),
    ]
    _append_portrait_grade_tables(
        story,
        repo=repo,
        template_id=int(template_id),
        data=data,
        blocks=blocks,
        view_session=vs,
        colors=colors,
        h2_style=h2_style,
        academic_year=ay,
    )
    _portrait_doc_template(path).build(story)


def write_pv_jury_pdf(
    repo: Repository,
    *,
    template_id: int,
    jury_session_id: int,
    view_session: str,
    path: str | Path,
) -> None:
    """PV : membres du jury de la délibération, points de délibération, envois en 2ᵉ session."""
    from reportlab.platypus import Paragraph, Spacer

    colors, _title_style, _h2_style, body_style = _styles()
    ps = _mne_pv_paragraph_styles(colors)
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    sess = repo.get_jury_session(int(jury_session_id))
    if not sess:
        raise ValueError("Délibération introuvable.")

    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    program = track_program_label(lv, tr)
    kind = str(sess.get("session_kind") or "")
    label = str(sess.get("label") or "").strip()
    session_subtitle = (
        f"{T.DELIBERATION} : {kind}" + (f" — {label}" if label else "")
    )

    members = repo.list_jury_members_for_deliberation(int(jury_session_id))
    adjustments = repo.list_jury_adjustments_for_export(
        int(template_id), jury_session_id=int(jury_session_id)
    )
    s2 = repo.list_second_session_for_export(
        int(template_id), jury_session_id=int(jury_session_id)
    )

    story: list = []
    _append_mne_pv_header(
        story,
        colors=colors,
        academic_year=ay,
        program_line=f"{lv} Energie Nucléaire parcours {program}",
        jury_title="Jury",
        session_subtitle=f"{session_subtitle} (brouillon)",
    )
    story.append(
        Paragraph(
            f"Vue notes utilisée pour le contexte : {str(view_session or 's1').upper()}",
            body_style,
        )
    )
    story.append(Spacer(1, 8))
    _append_jury_roster_sections(story, members, colors=colors)

    _append_pv_general_comments(
        story,
        str(sess.get("notes") or ""),
        ps=ps,
        body_style=body_style,
    )

    story.append(Spacer(1, 10))
    story.append(Paragraph(T.DELIB_POINTS, ps["section"]))
    if not adjustments:
        story.append(Paragraph("Aucun point de délibération saisi.", body_style))
    else:
        arows = [["Étudiant", "N° I.N.E.", "Portée", "UE / Bloc", "Points", "Commentaire"]]
        for a in adjustments:
            sc = str(a.get("scope") or "")
            ue = ""
            if sc == "course":
                ue = f"{a.get('course_code') or ''} — {a.get('course_name') or ''}".strip(" —")
            elif sc == "block":
                ue = str(a.get("block_name") or "")
            elif sc == "year":
                ue = "Année"
            arows.append(
                [
                    f"{a.get('st_last') or ''} {a.get('st_first') or ''}".strip(),
                    student_transcript_number(a),
                    sc,
                    ue[:40],
                    f"{float(a.get('points') or 0):.3f}",
                    str(a.get("comment") or "")[:50],
                ]
            )
        story.append(_institutional_data_table(arows, colors, font_size=7))

    _append_pv_second_session_by_ue(
        story,
        repo=repo,
        template_id=int(template_id),
        s2_rows=s2,
        colors=colors,
        ps=ps,
        body_style=body_style,
        academic_year=ay,
        include_ine=True,
    )

    _pv_doc_template(path).build(story)


def write_transcript_pdf(
    repo: Repository,
    *,
    template_id: int,
    student_id: int,
    view_session: str,
    path: str | Path,
) -> None:
    """Relevé / transcript (brouillon) — mise en page institutionnelle."""
    write_institutional_transcript_pdf(
        repo,
        template_id=int(template_id),
        student_id=int(student_id),
        path=path,
        final=False,
        view_session=str(view_session or "s1"),
    )


def _pdf_num(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v):.3f}".replace(".", ",")


def _ue_display_value(row: dict, course_id: int, view_session: str) -> float | None:
    """Note UE affichée (session + jury UE) si numérique."""
    d = (row.get("ue_detail") or {}).get(int(course_id)) or {}
    if str(d.get("display") or "").strip():
        return None
    vs = str(view_session or "s1").lower()
    use_s2 = bool(d.get("use_s2"))
    if vs == "s1":
        base = d.get("s1")
    else:
        base = d.get("s2") if use_s2 else d.get("s1")
    jp = float(d.get("jury") or 0.0)
    if base is None:
        return jp if abs(jp) > 1e-12 else None
    return float(base) + jp


def write_track_averages_pdf(
    repo: Repository,
    *,
    template_id: int,
    view_session: str,
    path: str | Path,
    session_title: str = "First Session",
) -> None:
    """Moyennes de classe par UE et par bloc (modèle « Averages COURS … »)."""
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    colors, title_style, h2_style, body_style = _styles()
    accent = colors.HexColor(MNE_ACCENT_ORANGE)
    subtitle = ParagraphStyle(
        name="JuryAvgSubtitle",
        parent=body_style,
        alignment=TA_CENTER,
        textColor=accent,
    )
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    program = track_program_label(lv, tr)

    data = repo.get_student_result_summary(
        int(template_id),
        view_session=str(view_session or "s1"),
        include_all_students=True,
    )
    blocks = repo.list_template_blocks_with_courses(int(template_id))
    vs = str(view_session or "s1").lower()

    story: list = [
        Paragraph(f"Averages - {session_title}", title_style),
        Paragraph(f"{lv} Nuclear Energy", subtitle),
        Paragraph(program, subtitle),
        Spacer(1, 14),
    ]

    for bk, clist in blocks:
        story.append(Paragraph(str(bk or "Bloc"), h2_style))
        ue_vals: list[tuple[float | None, float]] = []
        for c in clist:
            if int(c.get("optional") or 0):
                continue
            cid = int(c["course_id"])
            nums = [_ue_display_value(r, cid, vs) for r in data]
            nums = [x for x in nums if x is not None]
            avg_ue = sum(nums) / len(nums) if nums else None
            code = course_public_code(c, academic_year=ay) or str(c.get("code") or "")
            name = str(c.get("name") or "").strip()
            line = f"{code} {name} {_pdf_num(avg_ue)}"
            story.append(Paragraph(line, body_style))
            w = float(c.get("ects") or 0) or float(c.get("global_coefficient") or 1) or 1.0
            ue_vals.append((avg_ue, w))
        if ue_vals and not any(g is None for g, _ in ue_vals):
            blk_avg = weighted_average([(float(g), w) for g, w in ue_vals if g is not None])
            story.append(Paragraph(f"<b>Average {bk}: {_pdf_num(blk_avg)}</b>", body_style))
        else:
            story.append(Paragraph(f"<b>Average {bk}: —</b>", body_style))
        story.append(Spacer(1, 8))

    if ay:
        story.insert(2, Paragraph(f"Année universitaire {ay}", subtitle))

    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=48, leftMargin=48, topMargin=48, bottomMargin=36)
    doc.build(story)


def write_track_grade_matrix_pdf(
    repo: Repository,
    *,
    template_id: int,
    view_session: str,
    path: str | Path,
) -> None:
    """Tableau étudiant × UE (modèle M1P / M1C …), format portrait A4."""
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer

    colors, title_style, h2_style, body_style = _styles()
    accent = colors.HexColor(MNE_ACCENT_ORANGE)
    subtitle = ParagraphStyle(
        name="JuryMatrixSubtitle",
        parent=body_style,
        alignment=TA_CENTER,
        textColor=accent,
    )
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    vs = str(view_session or "s1")

    data = repo.get_student_result_summary(
        int(template_id), view_session=vs, include_all_students=True
    )
    blocks = repo.list_template_blocks_with_courses(int(template_id))
    program = track_program_label(lv, tr)

    story: list = [
        Paragraph(f"{lv}{tr} — Tableau des notes", title_style),
        Paragraph(f"Master Nuclear Energy — {program}", subtitle),
        Paragraph(f"Année universitaire {ay} · session {vs.upper()}", subtitle),
        Spacer(1, 10),
    ]
    _append_portrait_grade_tables(
        story,
        repo=repo,
        template_id=int(template_id),
        data=data,
        blocks=blocks,
        view_session=vs,
        colors=colors,
        h2_style=h2_style,
        academic_year=ay,
    )
    _portrait_doc_template(path).build(story)


def export_jury_pdf_bundle(
    repo: Repository,
    *,
    template_ids: list[int],
    view_session: str,
    dest_dir: str | Path,
    session_title: str = "First Session",
) -> list[Path]:
    """Génère pour chaque parcours : « Averages COURS X » + « M1X » / « M2X »."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    vs = str(view_session or "s1")
    for tid in template_ids:
        tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(tid)), None)
        if not tpl:
            continue
        lv = str(tpl.get("level") or "").strip().upper()
        tr = str(tpl.get("track") or "").strip().upper()
        avg_name = f"Averages COURS {tr}.pdf"
        matrix_name = f"{lv}{tr}.pdf"
        p1 = dest / avg_name
        p2 = dest / matrix_name
        write_track_averages_pdf(
            repo, template_id=int(tid), view_session=vs, path=p1, session_title=session_title
        )
        write_track_grade_matrix_pdf(repo, template_id=int(tid), view_session=vs, path=p2)
        created.extend([p1, p2])
    return created


def _pv_final_mention_label(
    repo: Repository,
    *,
    student_id: int,
    template_id: int,
    jury_session_id: int,
    grade: float | None,
) -> str:
    oc = repo.get_jury_student_outcome(
        int(student_id), int(template_id), jury_session_id=int(jury_session_id)
    )
    if oc:
        saved = transcript_mention_label(str(oc.get("mention") or ""))
        if saved and saved != "—":
            return saved
    return transcript_mention_from_grade(grade) or "—"


def _append_pv_final_student_roster(
    story: list,
    repo: Repository,
    *,
    template_id: int,
    jury_session_id: int,
    view_session: str,
    colors,
    ps,
) -> None:
    """PV jury final : tous les étudiants, décision, mention et classement (page paysage)."""
    from reportlab.platypus import NextPageTemplate, PageBreak, Paragraph, Spacer

    vs = str(view_session or "mixed").strip().lower()
    data = repo.get_student_result_summary(
        int(template_id), view_session=vs, include_all_students=True
    )
    if not data:
        return

    outcome_by_sid: dict[int, dict[str, Any]] = {}
    for o in repo.list_jury_student_outcomes_for_export(
        int(template_id), jury_session_id=int(jury_session_id)
    ):
        outcome_by_sid[int(o["student_id"])] = o

    story.append(NextPageTemplate("landscape"))
    story.append(PageBreak())
    story.append(Paragraph("Synthèse des décisions (tous les étudiants)", ps["section"]))
    rows: list[list[Any]] = [
        ["Étudiant", "Moyenne", "Clas. parc. / cohorte", "Mention", "Décision", "UE à repasser"]
    ]
    tid = int(template_id)
    jsid = int(jury_session_id)
    for row in data:
        sid = int(row["student_id"])
        name = f"{row.get('last_name') or ''} {row.get('first_name') or ''}".strip()
        gwj = row.get("global_with_jury")
        avg_txt = _pdf_num(float(gwj)) if gwj is not None else "—"

        if repo.student_eligible_for_ranking(sid, tid):
            track_rank = repo.student_track_rank(tid, sid, view_session=vs)
            cohort_rank = repo.student_cohort_rank(tid, sid, view_session=vs)
            if track_rank is not None and cohort_rank is not None:
                rank_txt = f"{track_rank} / {cohort_rank}"
            elif track_rank is not None:
                rank_txt = str(track_rank)
            else:
                rank_txt = "—"
        else:
            rank_txt = "NC"

        mention = _pv_final_mention_label(
            repo,
            student_id=sid,
            template_id=tid,
            jury_session_id=jsid,
            grade=float(gwj) if gwj is not None else None,
        )

        oc = outcome_by_sid.get(sid) or repo.get_jury_student_outcome(
            sid, tid, jury_session_id=jsid
        )
        outcome_key = str((oc or {}).get("outcome") or "").strip()
        if not outcome_key:
            ev = repo.evaluate_student_year_validation(
                sid, tid, view_session=vs, result_row=row
            )
            outcome_key = str(ev.get("suggested_outcome") or "")
        decision = JURY_OUTCOME_LABELS.get(outcome_key, outcome_key or "—")

        retake_cell: Any = ""
        saved_outcome = str((oc or {}).get("outcome") or "").strip().lower()
        if saved_outcome == "repeat":
            retake = repo.courses_to_retake_for_student(
                sid, tid, view_session=vs, result_row=row
            )
            retake_cell = _retake_paragraph_for_pv(retake)

        rows.append([name, avg_txt, rank_txt, mention, decision, retake_cell])

    story.append(_institutional_landscape_roster_table(rows, colors))
    story.append(Spacer(1, 10))


def write_institutional_pv_pdf(
    repo: Repository,
    *,
    template_id: int,
    jury_session_id: int,
    view_session: str,
    path: str | Path,
    place: str = "Orsay",
    meeting_date: str = "",
) -> None:
    """PV de délibération (structure proche des modèles MNE : jury, décisions, S2, issues)."""
    from datetime import date

    from reportlab.platypus import Paragraph, Spacer

    colors, _title_style, _h2_style, body_style = _styles()
    ps = _mne_pv_paragraph_styles(colors)
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    sess = repo.get_jury_session(int(jury_session_id))
    if not sess:
        raise ValueError("Délibération introuvable.")

    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    program = track_program_label(lv, tr)
    kind = str(sess.get("session_kind") or "").strip().upper()
    label = str(sess.get("label") or "").strip()
    members = repo.list_jury_members_for_deliberation(int(jury_session_id))
    adjustments = repo.list_jury_adjustments_for_export(
        int(template_id), jury_session_id=int(jury_session_id)
    )
    s2 = repo.list_second_session_for_export(
        int(template_id), jury_session_id=int(jury_session_id)
    )
    outcomes = repo.list_jury_student_outcomes_for_export(
        int(template_id), jury_session_id=int(jury_session_id)
    )
    when = _format_pv_date(meeting_date.strip() or date.today().isoformat())

    if kind == "FINAL" and str(view_session or "").strip().lower() not in {"mixed", "s2"}:
        view_session = "mixed"

    if kind == "FINAL":
        session_subtitle = f"Jury d'année de {lv}"
    elif label:
        session_subtitle = label
    else:
        session_subtitle = f"{T.DELIBERATION} {kind}".strip()

    story: list = []
    _append_mne_pv_header(
        story,
        colors=colors,
        academic_year=ay,
        program_line=f"{lv} Energie Nucléaire parcours {program}",
        jury_title="Jury",
        session_subtitle=session_subtitle,
    )
    if when:
        story.append(Paragraph(f"À {place} le {when}", body_style))
    story.append(Spacer(1, 10))
    _append_jury_roster_sections(story, members, colors=colors)
    story.append(Spacer(1, 12))
    story.append(Paragraph("Verbatim des délibérations", ps["section"]))
    _append_pv_general_comments(
        story,
        str(sess.get("notes") or ""),
        ps=ps,
        body_style=body_style,
        title="",
    )
    if not str(sess.get("notes") or "").strip():
        story.append(
            Paragraph(
                "<i>Aucun commentaire général enregistré pour cette délibération.</i>",
                body_style,
            )
        )
        story.append(Spacer(1, 8))

    if adjustments:
        story.append(Paragraph(T.DELIB_POINTS, ps["section"]))
        arows = [["Étudiant", "Portée", "Détail", "Points", "Commentaire"]]
        for a in adjustments:
            sc = str(a.get("scope") or "")
            detail = ""
            if sc == "course":
                detail = f"{a.get('course_code') or ''} {a.get('course_name') or ''}".strip()
            elif sc == "block":
                detail = str(a.get("block_name") or "")
            elif sc == "year":
                detail = "Année"
            arows.append(
                [
                    f"{a.get('st_last') or ''} {a.get('st_first') or ''}".strip(),
                    sc,
                    detail[:35],
                    _pdf_num(float(a.get("points") or 0)),
                    str(a.get("comment") or "")[:40],
                ]
            )
        story.append(_institutional_data_table(arows, colors, font_size=7))
        story.append(Spacer(1, 10))

    if s2:
        _append_pv_second_session_by_ue(
            story,
            repo=repo,
            template_id=int(template_id),
            s2_rows=s2,
            colors=colors,
            ps=ps,
            body_style=body_style,
            academic_year=ay,
            include_ine=False,
        )
        story.append(Spacer(1, 4))

    if kind == "FINAL":
        _append_pv_final_student_roster(
            story,
            repo,
            template_id=int(template_id),
            jury_session_id=int(jury_session_id),
            view_session=str(view_session or "s2"),
            colors=colors,
            ps=ps,
        )
    elif outcomes:
        story.append(Paragraph("Décisions individuelles", ps["section"]))
        orows = [["Étudiant", "Décision", "Commentaire"]]
        for o in outcomes:
            oc = str(o.get("outcome") or "")
            orows.append(
                [
                    f"{o.get('st_last') or ''} {o.get('st_first') or ''}".strip(),
                    JURY_OUTCOME_LABELS.get(oc, oc),
                    str(o.get("comment") or "")[:50],
                ]
            )
        story.append(_institutional_data_table(orows, colors))
        story.append(Spacer(1, 10))

    doc = _pv_mixed_orientation_doc(path) if kind == "FINAL" else _pv_doc_template(path)
    doc.build(story)


def _transcript_grade_comma(v: float | None) -> str:
    if v is None:
        return ""
    return f"{float(v):.2f}".replace(".", ",")


def _transcript_jury_points_display(jury_points: float | None) -> str:
    """Points de délibération affichés avec signe (+0,50 / −0,25)."""
    if jury_points is None:
        return ""
    jp = float(jury_points)
    if abs(jp) < 1e-12:
        return ""
    if jp < 0:
        return f"−{_transcript_grade_comma(abs(jp))}"
    return f"+{_transcript_grade_comma(jp)}"


def _transcript_ue_jury_points(row: dict[str, Any], course_id: int) -> float:
    d = (row.get("ue_detail") or {}).get(int(course_id)) or {}
    return float(d.get("jury") or 0.0)


def _transcript_block_jury_points(row: dict[str, Any], block_name: str) -> float:
    jury = row.get("jury") or {}
    block_map = jury.get("block") or {}
    return float(block_map.get(block_name) or block_map.get(str(block_name or "")) or 0.0)


def _transcript_year_jury_points(row: dict[str, Any]) -> float:
    jury = row.get("jury") or {}
    return float(jury.get("year") or 0.0)


def _transcript_row_has_jury_points(row: dict[str, Any]) -> bool:
    jury = row.get("jury") or {}
    if abs(float(jury.get("year") or 0.0)) > 1e-12:
        return True
    for jp in (jury.get("course") or {}).values():
        if abs(float(jp or 0.0)) > 1e-12:
            return True
    for jp in (jury.get("block") or {}).values():
        if abs(float(jp or 0.0)) > 1e-12:
            return True
    return False


def format_transcript_session_label(view_session: str, academic_year: str) -> str:
    vs = "S1" if str(view_session or "s1").lower() == "s1" else "S2"
    ay = str(academic_year or "").strip()
    if "-" in ay:
        a, b = ay.split("-", 1)
        return f"{vs} {a.strip()}/{b.strip()}"
    return f"{vs} {ay}" if ay else vs


def _transcript_session_short(view_session: str) -> str:
    vs = str(view_session or "s1").strip().lower()
    if vs == "s2":
        return "2nd Session"
    if vs == "mixed":
        return "Retained"
    return "1st Session"


def _transcript_session_label(view_session: str, academic_year: str) -> str:
    return format_transcript_session_label(view_session, academic_year)


def _ordinal_day(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _format_transcript_issue_date(value: str | None = None) -> str:
    """Ex. « July 16th 2025 » (modèle MNE)."""
    if value and str(value).strip():
        raw = str(value).strip()
        if "-" in raw:
            parts = raw.split("-")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                y, m, d = (int(parts[0]), int(parts[1]), int(parts[2]))
                dt = datetime(y, m, d, tzinfo=timezone.utc)
                return f"{dt.strftime('%B')} {_ordinal_day(d)} {y}"
        return raw
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%B')} {_ordinal_day(now.day)} {now.year}"


def _transcript_birth_display(iso_date: str) -> str:
    raw = str(iso_date or "").strip()
    if not raw:
        return ""
    parts = raw.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = parts
        return f"{int(d)}/{int(m)}/{y}"
    return raw


def _transcript_birth_place_display(student: dict[str, Any]) -> str:
    """Ville de naissance avec le pays entre parenthèses (ex. Lyon (France))."""
    place = str(student.get("birth_place") or "").strip()
    country = str(student.get("nationality") or "").strip()
    if not place and not country:
        return ""
    if not place:
        return country
    if not country:
        return place
    if place.lower() == country.lower():
        return place
    if f"({country})" in place:
        return place
    return f"{place} ({country})"


def transcript_mention_from_grade(grade: float | None) -> str:
    """Mention proposée (≥ 12) : Assez bien … Excellent (≥ 18)."""
    if grade is None or float(grade) < 12.0:
        return ""
    g = float(grade)
    if g >= 18.0:
        return "Excellent"
    if g >= 16.0:
        return "Très bien"
    if g >= 14.0:
        return "Bien"
    return "Assez bien"


def transcript_mention_code_from_grade(grade: float | None) -> str:
    label = transcript_mention_from_grade(grade)
    for code, lab in TRANSCRIPT_MENTION_LABELS.items():
        if lab == label:
            return code
    return ""


def transcript_mention_label(code: str) -> str:
    return TRANSCRIPT_MENTION_LABELS.get(str(code or "").strip().lower(), "")


def _transcript_mention(grade: float | None) -> str:
    return transcript_mention_from_grade(grade)


def resolve_transcript_mention(
    repo: Repository,
    *,
    student_id: int,
    template_id: int,
    grade: float | None,
) -> str:
    """Mention jury enregistrée, sinon proposition automatique depuis la moyenne."""
    jsid = repo.get_final_jury_session_id(int(template_id))
    oc = repo.get_jury_student_outcome(
        int(student_id), int(template_id), jury_session_id=jsid
    )
    if oc:
        saved = transcript_mention_label(str(oc.get("mention") or ""))
        if saved and saved != "—":
            return saved
    return transcript_mention_from_grade(grade)


def _transcript_section_for_block(block_name: str) -> str:
    low = str(block_name or "").lower()
    if "stage" in low or "intern" in low:
        return "Internship"
    if "spécifique" in low or "specific" in low or "spe" in low:
        return "Specific courses"
    return "Common Courses"


def _transcript_course_label(course: dict[str, Any]) -> str:
    name = str(course.get("name") or "").strip()
    code = str(course.get("code") or "").strip()
    if name.upper().startswith("TU"):
        return name
    if code:
        return f"TU - {name}" if name else f"TU - {code}"
    return f"TU - {name}" if name else "—"


def _transcript_block_display_name(block_name: str) -> str:
    raw = str(block_name or "").strip()
    if not raw or raw == "(no block)":
        return "Courses"
    return raw


def _transcript_ue_session_grade(
    row: dict[str, Any], course_id: int, view_session: str
) -> float | None:
    """Moyenne UE de session affichée dans la colonne Grade (sans points de jury)."""
    d = (row.get("ue_detail") or {}).get(int(course_id)) or {}
    if str(d.get("display") or "").strip():
        return None
    vs = str(view_session or "s1").lower()
    use_s2 = bool(d.get("use_s2"))
    if vs == "s1":
        base = d.get("s1")
    else:
        base = d.get("s2") if use_s2 else d.get("s1")
    if base is None:
        return None
    return float(base)


def _transcript_block_session_average(
    row: dict[str, Any], block_name: str, block_average: float | None
) -> float | None:
    """Moyenne de bloc affichée dans la colonne Grade (sans points de jury bloc)."""
    if block_average is None:
        return None
    jp = _transcript_block_jury_points(row, block_name)
    return float(block_average) - float(jp)


def _transcript_ue_grade(row: dict[str, Any], course_id: int, view_session: str) -> float | None:
    return _ue_display_value(row, int(course_id), view_session)


def _transcript_ue_result(
    repo: Repository,
    *,
    student_id: int,
    template_id: int,
    course: dict[str, Any],
    row: dict[str, Any],
    view_session: str,
) -> str:
    cid = int(course["course_id"])
    sid, tid = int(student_id), int(template_id)
    d = (row.get("ue_detail") or {}).get(cid) or {}
    display = str(d.get("display") or "").strip()
    if display in ("DEF", "ABJ"):
        return "Failed"
    if display == "VAL" or repo.has_ue_ects_validation(sid, tid, cid):
        return "Validated"
    if display:
        return display
    grade = _transcript_ue_grade(row, cid, view_session)
    if grade is None:
        return ""
    if grade_meets_minimum(grade, 10.0):
        return "Passed"
    if repo.has_ue_jury_floor_waiver(sid, tid, cid):
        return "Validated"
    if grade_meets_minimum(grade, 7.0):
        if (
            repo.block_ue_compensation_status(
                sid,
                tid,
                cid,
                result_row=row,
                view_session=view_session,
            )
            == "allowed"
        ):
            return "Compensated"
    return "Failed"


def _transcript_block_passed(
    repo: Repository,
    *,
    student_id: int,
    template_id: int,
    block_name: str,
    block_average: float | None,
    view_session: str,
) -> str:
    if block_average is None:
        return ""
    if repo.block_is_validated(
        int(student_id),
        int(template_id),
        str(block_name),
        view_session=view_session,
        block_average=block_average,
    ):
        return "Passed"
    return "Failed"


def _transcript_filename(stu: dict[str, Any], *, level: str, track: str, final: bool) -> str:
    ln = str(stu.get("last_name") or "Student").strip().replace("/", "-")
    fn = str(stu.get("first_name") or "").strip().replace("/", "-")
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    tag = "Final" if final else "Provisional"
    return f"{ln} {fn} {tag} Transcript {lv}{tr}.pdf"


def _transcript_logo_paths() -> list[Path]:
    """Logos partenaires du bandeau d'en-tête (ordre gauche → droite)."""
    base = Path(__file__).resolve().parent.parent / "assets" / "transcript_logos"
    names = (
        "01_mne.png",
        "02_upsay_orsay.png",
        "03_psl.png",
        "04_instn.png",
        "05_ensta_ip.png",
        "06_chimie_paris.png",
        "07_centrale.png",
        "08_ponts.png",
        "09_ip_paris.png",
    )
    return [base / name for name in names if (base / name).is_file()]


def _scaled_logo_row(
    paths: list[Path], *, max_height: float, max_width: float, gap: float
) -> list[tuple[Path, float, float]]:
    from reportlab.lib.utils import ImageReader

    if not paths:
        return []
    sizes: list[tuple[Path, float, float]] = []
    for path in paths:
        iw, ih = ImageReader(str(path)).getSize()
        if ih <= 0 or iw <= 0:
            continue
        scale = min(max_height / float(ih), 1.0)
        sizes.append((path, float(iw) * scale, float(ih) * scale))
    if not sizes:
        return []
    total_w = sum(w for _, w, _ in sizes) + gap * (len(sizes) - 1)
    if total_w > max_width > 0:
        shrink = max_width / total_w
        sizes = [(p, w * shrink, h * shrink) for p, w, h in sizes]
    return sizes


def _draw_transcript_page_header(canvas, doc) -> None:
    """Bandeau logos partenaires en tête de page."""
    from reportlab.lib.pagesizes import A4

    w, page_h = A4
    canvas.saveState()
    banner_h = TRANSCRIPT_HEADER_HEIGHT_PT
    banner_bottom = page_h - banner_h - 18

    logo_paths = _transcript_logo_paths()
    logo_max_w = w - doc.leftMargin - doc.rightMargin
    row = _scaled_logo_row(
        logo_paths,
        max_height=banner_h - 8,
        max_width=logo_max_w,
        gap=TRANSCRIPT_LOGO_GAP_PT,
    )
    x = doc.leftMargin
    for path, lw, lh in row:
        y = banner_bottom + (banner_h - lh) / 2.0
        canvas.drawImage(
            str(path),
            x,
            y,
            width=lw,
            height=lh,
            preserveAspectRatio=True,
            mask="auto",
        )
        x += lw + TRANSCRIPT_LOGO_GAP_PT
    canvas.restoreState()


def _transcript_footer_program_label(level: str, track: str) -> str:
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    return track_program_label(lv, tr)


def _draw_transcript_page_footer(
    canvas, doc, *, level: str, program: str, emails: list[str]
) -> None:
    """Pied de page : Master + parcours (gauche), e-mail responsable (droite)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4

    w, _ = A4
    canvas.saveState()
    x_left = doc.leftMargin
    x_right = w - doc.rightMargin
    line_h = 10.0
    y_base = 22.0

    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(colors.HexColor(MNE_ACCENT_ORANGE))
    canvas.drawString(x_left, y_base + line_h, mne_level_master_line(level))
    prog = str(program or "").strip()
    if prog:
        canvas.setFont("Helvetica", 8)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.drawString(x_left, y_base, prog)

    canvas.setFont("Helvetica", 8)
    canvas.setFillColorRGB(0, 0, 0)
    if emails:
        for i, em in enumerate(emails[:3]):
            canvas.drawRightString(x_right, y_base + i * line_h, f"Email: {em}")
    canvas.restoreState()


def _transcript_signature_lines(
    repo: Repository,
    *,
    final: bool,
    student_id: int,
    template_id: int,
    academic_year: str,
    level: str,
    track: str,
    view_session: str,
    row: dict[str, Any],
    place: str,
    issue_date: str,
) -> list[tuple[str, str]]:
    """Lignes du bloc signature (texte, style: normal|bold|italic)."""
    ay = str(academic_year or "").strip()
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    vs = str(view_session or "s1").strip().lower()
    lines: list[tuple[str, str]] = []
    gwj = row.get("global_with_jury")
    year_jury = _transcript_year_jury_points(row)

    if gwj is not None:
        result_label = "Final Result" if final else "Year average"
        lines.append((f"{result_label}: {_transcript_grade_comma(float(gwj))}/20", "bold"))
        jury_txt = _transcript_jury_points_display(year_jury)
        if jury_txt:
            lines.append((f"Jury deliberation (year): {jury_txt}", "italic"))

    if final and gwj is not None:
        mention = resolve_transcript_mention(
            repo,
            student_id=int(student_id),
            template_id=int(template_id),
            grade=float(gwj),
        )
        lines.append((f"Mention: {mention or '—'}", "normal"))
        rankable = repo.student_eligible_for_ranking(int(student_id), int(template_id))
        if rankable:
            track_rank = repo.student_track_rank(
                int(template_id), int(student_id), view_session=vs
            )
            cohort_rank = repo.student_cohort_rank(
                int(template_id), int(student_id), view_session=vs
            )
            if track_rank is not None:
                lines.append((f"Track ranking: {track_rank}", "normal"))
            if cohort_rank is not None:
                lines.append((f"Cohort ranking: {cohort_rank}", "normal"))
        elif not rankable:
            lines.append(("Ranking: not applicable (second session).", "italic"))

    when = _format_transcript_issue_date(issue_date.strip() or None)
    lines.append((f"Done in {place} {when}", "normal"))

    directors = [d for d in repo.list_track_directors(ay, lv, tr) if d.get("id")]
    named = [
        d
        for d in directors
        if str(d.get("last_name") or "").strip() or str(d.get("first_name") or "").strip()
    ]
    if named:
        for director in named:
            title = str(director.get("title") or "Dr.").strip()
            ln = str(director.get("last_name") or "").strip().upper()
            fn = str(director.get("first_name") or "").strip()
            lines.append((f"{title} {fn} {ln}".strip(), "bold"))
            role_line = str(director.get("notes") or "").strip() or f"Co-head of {lv} Nuclear Energy"
            lines.append((role_line, "normal"))
    else:
        lines.append((f"Co-head of {lv} Nuclear Energy", "bold"))
    return lines


def _draw_transcript_signature_block(
    canvas, doc, lines: list[tuple[str, str]]
) -> None:
    """Bloc signature en bas à droite, au-dessus du bandeau pied de page."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm

    if not lines:
        return
    w, _ = A4
    canvas.saveState()
    x_right = w - doc.rightMargin
    y = (
        TRANSCRIPT_PAGE_FOOTER_BAND_PT
        + TRANSCRIPT_SIGNATURE_GAP_PT
        + TRANSCRIPT_SIGNATURE_LIFT_CM * cm
    )
    for text, style in reversed(lines):
        if style == "bold":
            canvas.setFont("Helvetica-Bold", TRANSCRIPT_BODY_FONT_PT)
        elif style == "italic":
            canvas.setFont("Helvetica-Oblique", TRANSCRIPT_BODY_FONT_PT)
        else:
            canvas.setFont("Helvetica", TRANSCRIPT_BODY_FONT_PT)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.drawRightString(x_right, y, str(text))
        y += TRANSCRIPT_SIGNATURE_LINE_PT
    canvas.restoreState()


def _transcript_doc_template(
    path: str | Path,
    *,
    level: str,
    program: str,
    header_emails: list[str],
    signature_lines: list[tuple[str, str]] | None = None,
):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    emails = [e for e in header_emails if e]
    footer_program = str(program or "").strip()
    footer_level = str(level or "").strip().upper()
    sig_lines = list(signature_lines or [])
    sig_height = (
        TRANSCRIPT_PAGE_FOOTER_BAND_PT
        + TRANSCRIPT_SIGNATURE_GAP_PT
        + TRANSCRIPT_SIGNATURE_LIFT_CM * cm
        + len(sig_lines) * TRANSCRIPT_SIGNATURE_LINE_PT
        + 8.0
    )

    def _on_page(canvas, doc):
        _draw_transcript_page_header(canvas, doc)
        _draw_transcript_signature_block(canvas, doc, sig_lines)
        _draw_transcript_page_footer(
            canvas, doc, level=str(level or "").strip().upper(), program=footer_program, emails=emails
        )

    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=int(TRANSCRIPT_HEADER_HEIGHT_PT + 32),
        bottomMargin=int(max(62.0, sig_height)),
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="transcript", frames=[frame], onPage=_on_page)])
    return doc


def write_institutional_transcript_pdf(
    repo: Repository,
    *,
    template_id: int,
    student_id: int,
    path: str | Path,
    final: bool = False,
    view_session: str = "s1",
    place: str = "Orsay",
    issue_date: str = "",
) -> None:
    """
    Transcript institutionnel (modèle MNE).

    ``final=True`` : transcript définitif (mention, classement) — jury final requis.

    Les notes et la colonne « Session » utilisent toujours la vue ``mixed`` :
    note S2 si elle existe pour l'UE, sinon S1, avec libellé ``S1/S2 AAAA/AAAA`` par UE.
    Le paramètre ``view_session`` est conservé pour compatibilité mais n'influence plus le contenu.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    if final and not repo.has_final_jury_session(int(template_id)):
        raise ValueError(
            "Le transcript définitif nécessite une délibération « Finale » pour cette maquette."
        )

    stu = repo.get_student(int(student_id))
    if not stu:
        raise ValueError("Étudiant introuvable.")
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    lv = str(tpl.get("level") or "").strip().upper()
    tr = str(tpl.get("track") or "").strip().upper()
    ay = str(tpl.get("academic_year") or "").strip()
    from .student_mobility import is_erasmus_student

    if is_erasmus_student(stu):
        program = "ERASMUS Mobility"
        footer_program = "ERASMUS — cours suivis"
        footer_level = ""
    else:
        program = track_program_label(lv, tr)
        footer_program = _transcript_footer_program_label(lv, tr)
        footer_level = lv
    vs = "mixed"

    data = repo.get_student_result_summary(
        int(template_id),
        view_session=vs,
        include_all_students=True,
    )
    row = next((r for r in data if int(r.get("student_id") or 0) == int(student_id)), None)
    if not row:
        raise ValueError("Étudiant non inscrit à cette maquette ou filtré par la session.")

    colors_mod, _, _, _ = _styles()
    header_emails = repo.transcript_header_emails(int(template_id))
    signature_lines = _transcript_signature_lines(
        repo,
        final=final,
        student_id=int(student_id),
        template_id=int(template_id),
        academic_year=ay,
        level=lv,
        track=tr,
        view_session=vs,
        row=row,
        place=place,
        issue_date=issue_date,
    )
    doc = _transcript_doc_template(
        path,
        level=footer_level,
        program=footer_program,
        header_emails=header_emails,
        signature_lines=signature_lines,
    )

    title_orange = colors.HexColor(MNE_ACCENT_ORANGE)
    body = ParagraphStyle(
        name="TrBody",
        fontSize=TRANSCRIPT_BODY_FONT_PT,
        leading=TRANSCRIPT_BODY_LEADING_PT,
    )
    body_compact = ParagraphStyle(
        name="TrBodyCompact",
        parent=body,
        fontSize=TRANSCRIPT_TABLE_FONT_PT,
        leading=TRANSCRIPT_BODY_LEADING_PT,
    )
    center = ParagraphStyle(name="TrCenter", parent=body, alignment=1)
    center_bold = ParagraphStyle(
        name="TrCenterBold",
        parent=center,
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=12,
        textColor=title_orange,
    )
    center_orange = ParagraphStyle(
        name="TrYear",
        parent=center,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=11,
        textColor=title_orange,
    )
    body_ue = ParagraphStyle(
        name="TrBodyUE",
        parent=body_compact,
        leftIndent=8,
        textColor=colors.HexColor("#333333"),
    )
    body_block = ParagraphStyle(
        name="TrBodyBlock",
        parent=body_compact,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor(MNE_TRANSCRIPT_BLOCK_TEXT),
    )

    transcript_kind = "Final Transcript:" if final else "Partial Transcript:"
    story: list[Any] = [
        Spacer(1, 0.1 * cm),
        Paragraph(mne_level_master_line(lv), center_bold),
        Paragraph(program, center_bold),
        Paragraph(_transcript_academic_year_line(ay, vs, final=final), center_orange),
        Spacer(1, 0.15 * cm),
        Paragraph(transcript_kind, center_bold),
        Spacer(1, 0.2 * cm),
    ]

    show_jury = _transcript_row_has_jury_points(row)
    if show_jury:
        col_widths = [1.4 * cm, 5.55 * cm, 1.4 * cm, 1.1 * cm, 2.45 * cm, 2.35 * cm]
    else:
        col_widths = [1.4 * cm, 6.5 * cm, 1.4 * cm, 2.45 * cm, 2.35 * cm]
    id_half_w = sum(col_widths) / 2

    id_rows = [
        [
            Paragraph(f"<b>Name :</b> {stu.get('last_name', '')}", body),
            Paragraph(f"<b>INE Number :</b> {student_transcript_number(stu)}", body),
        ],
        [
            Paragraph(f"<b>First Name :</b> {stu.get('first_name', '')}", body),
            "",
        ],
        [
            Paragraph(
                f"<b>Date of Birth :</b> {_transcript_birth_display(str(stu.get('birth_date') or ''))}",
                body,
            ),
            Paragraph(
                f"<b>Place of Birth:</b> {_transcript_birth_place_display(stu)}",
                body,
            ),
        ],
    ]
    id_tbl = Table(id_rows, colWidths=[id_half_w, id_half_w], hAlign="CENTER")
    id_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(id_tbl)
    story.append(Spacer(1, 0.25 * cm))

    result_col = 4 if show_jury else 3
    session_col = 5 if show_jury else 4
    last_col = session_col

    header_row: list[Any] = ["Credits", "Courses", "Grade"]
    if show_jury:
        header_row.append("Jury")
    header_row.extend(["Result", "Session"])
    table_rows: list[list[Any]] = [header_row]
    span_cmds: list[tuple] = []
    cell_style_cmds: list[tuple] = []
    row_idx = 1
    current_section = ""

    for bk, clist in repo.list_template_blocks_with_courses(int(template_id)):
        section = _transcript_section_for_block(bk)
        if section != current_section:
            current_section = section
            table_rows.append(
                ["", Paragraph(f"<b><i>{section}</i></b>", body_compact), *([""] * (last_col - 1))]
            )
            span_cmds.append(("SPAN", (1, row_idx), (last_col, row_idx)))
            span_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor(MNE_TRANSCRIPT_SECTION_BG)))
            row_idx += 1

        graded = [c for c in clist if not int(c.get("optional") or 0)]
        block_ects = sum(
            float(c.get("ects") or 0) or float(c.get("global_coefficient") or 0) or 0 for c in graded
        )
        if block_ects <= 0 and graded:
            block_ects = float(len(graded))
        blk_avg = (row.get("blocks") or {}).get(bk)
        blk_session_avg = _transcript_block_session_average(row, bk, blk_avg)
        blk_grade = _transcript_grade_comma(
            float(blk_session_avg) if blk_session_avg is not None else None
        )
        blk_jury = _transcript_jury_points_display(_transcript_block_jury_points(row, bk))
        blk_result = _transcript_block_passed(
            repo,
            student_id=int(student_id),
            template_id=int(template_id),
            block_name=bk,
            block_average=blk_avg,
            view_session=vs,
        )
        block_row: list[Any] = [
            _transcript_grade_comma(block_ects) if block_ects else "",
            Paragraph(f"<b>{_transcript_block_display_name(bk)}</b>", body_block),
            blk_grade,
        ]
        if show_jury:
            block_row.append(blk_jury)
        block_row.extend(
            [
                blk_result or "",
                "",
            ]
        )
        table_rows.append(block_row)
        cell_style_cmds.append(
            ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor(MNE_TRANSCRIPT_BLOCK_BG))
        )
        cell_style_cmds.append(("FONTNAME", (0, row_idx), (2, row_idx), "Helvetica-Bold"))
        cell_style_cmds.append(
            ("TEXTCOLOR", (0, row_idx), (2, row_idx), colors.HexColor(MNE_TRANSCRIPT_BLOCK_TEXT))
        )
        if blk_result:
            cell_style_cmds.append(
                ("FONTNAME", (result_col, row_idx), (result_col, row_idx), "Helvetica-Bold")
            )
        row_idx += 1

        for c in clist:
            if int(c.get("optional") or 0):
                continue
            cid = int(c["course_id"])
            ects = float(c.get("ects") or 0) or float(c.get("global_coefficient") or 0) or 0
            grade_base = _transcript_ue_session_grade(row, cid, vs)
            result = _transcript_ue_result(
                repo,
                student_id=int(student_id),
                template_id=int(template_id),
                course=c,
                row=row,
                view_session=vs,
            )
            d = (row.get("ue_detail") or {}).get(cid) or {}
            disp = str(d.get("display") or "").strip()
            grade_txt = disp if disp else _transcript_grade_comma(grade_base)
            ue_jury = _transcript_jury_points_display(_transcript_ue_jury_points(row, cid))
            ue_sess_vs = "s2" if d.get("use_s2") else "s1"
            ue_sess = repo.get_ue_transcript_session_label(
                int(student_id),
                int(template_id),
                cid,
                default_view_session=ue_sess_vs,
                default_academic_year=ay,
            )
            ue_row: list[Any] = [
                _transcript_grade_comma(ects) if ects else "",
                Paragraph(_transcript_course_label(c), body_ue),
                grade_txt,
            ]
            if show_jury:
                ue_row.append(ue_jury)
            ue_row.extend(
                [
                    result or "",
                    _transcript_session_paragraph(ue_sess, body_compact=body_compact),
                ]
            )
            table_rows.append(ue_row)
            row_idx += 1

    grades_tbl = Table(table_rows, colWidths=col_widths, repeatRows=1, hAlign="CENTER")
    style_cmds: list[tuple] = list(_transcript_table_style(colors_mod))
    style_cmds.extend(span_cmds)
    style_cmds.extend(cell_style_cmds)
    style_cmds.extend(
        [
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (2, 0), (2, -1), "CENTER"),
            ("ALIGN", (result_col, 0), (session_col, -1), "CENTER"),
            ("ALIGN", (1, 0), (1, -1), "LEFT"),
            ("FONTSIZE", (session_col, 1), (session_col, -1), max(7.5, TRANSCRIPT_TABLE_FONT_PT - 0.5)),
        ]
    )
    if show_jury:
        style_cmds.append(("ALIGN", (3, 0), (3, -1), "CENTER"))
    grades_tbl.setStyle(TableStyle(style_cmds))
    story.append(grades_tbl)

    if _transcript_row_has_jury_points(row):
        story.append(Spacer(1, 0.2 * cm))
        story.append(
            Paragraph(
                "<i>Grade: session average before jury adjustment. "
                "Jury: deliberation points (UE, block and year). "
                "Effective grade = Grade + Jury.</i>",
                body,
            )
        )

    if not final:
        story.append(Spacer(1, 0.35 * cm))
        story.append(
            Paragraph(
                "<i>Provisional document — not valid for official certification until final jury.</i>",
                body,
            )
        )

    doc.build(story)

    snapshot = repo.build_template_snapshot(int(template_id))
    repo.log_transcript_export(
        student_id=int(student_id),
        template_id=int(template_id),
        view_session="final" if final else vs,
        file_path=str(path),
        snapshot=snapshot,
    )


def transcript_default_filename(
    stu: dict[str, Any], *, level: str, track: str, final: bool
) -> str:
    return _transcript_filename(stu, level=level, track=track, final=final)


def find_transcript_pdf_in_dir(
    directory: str | Path,
    stu: dict[str, Any],
    *,
    level: str,
    track: str,
    final: bool,
) -> Path | None:
    """Retrouve le PDF transcript d'un étudiant dans un dossier (nom exact)."""
    dest = Path(directory).expanduser()
    if not dest.is_dir():
        return None
    exact = dest / _transcript_filename(stu, level=level, track=track, final=final)
    if exact.is_file():
        return exact.resolve()
    if final:
        return None
    fallback = dest / _transcript_filename(stu, level=level, track=track, final=False)
    if fallback.is_file():
        return fallback.resolve()
    return None


def is_provisional_transcript_pdf(path: str | Path) -> bool:
    return " Provisional Transcript " in Path(path).name


def is_final_transcript_pdf(path: str | Path) -> bool:
    return " Final Transcript " in Path(path).name


def export_transcripts_batch(
    repo: Repository,
    *,
    template_id: int,
    student_ids: list[int],
    dest_dir: str | Path,
    final: bool = False,
    view_session: str = "s1",
) -> tuple[list[Path], list[tuple[str, str]]]:
    """Génère un PDF par étudiant dans ``dest_dir`` (continue en cas d'échec isolé)."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    created: list[Path] = []
    errors: list[tuple[str, str]] = []
    for sid in student_ids:
        stu = repo.get_student(int(sid)) or {}
        label = f"{stu.get('last_name', '')} {stu.get('first_name', '')}".strip() or f"#{sid}"
        fname = _transcript_filename(stu, level=lv, track=tr, final=final)
        out = dest / fname
        try:
            write_institutional_transcript_pdf(
                repo,
                template_id=int(template_id),
                student_id=int(sid),
                path=out,
                final=final,
                view_session=view_session,
            )
        except Exception as exc:
            errors.append((label, str(exc)))
            continue
        created.append(out)
    return created, errors


# Outcomes that warrant an English « Certificate of Achievement ».
SUCCESS_CERTIFICATE_OUTCOMES: frozenset[str] = frozenset({"pass_m2", "validate_year"})


def success_certificate_outcome_key(
    repo: Repository,
    *,
    template_id: int,
    student_id: int,
    jury_session_id: int | None = None,
) -> str:
    """Décision jury finale (ou suggestion) pour l'éligibilité attestation."""
    tid, sid = int(template_id), int(student_id)
    jsid = jury_session_id
    if jsid is None:
        jsid = repo.get_final_jury_session_id(tid)
    oc = repo.get_jury_student_outcome(sid, tid, jury_session_id=jsid) or {}
    outcome = str(oc.get("outcome") or "").strip().lower()
    if outcome:
        return outcome
    rows = repo.get_student_result_summary(tid, view_session="mixed", include_all_students=True)
    row = next((r for r in rows if int(r.get("student_id") or 0) == sid), None)
    ev = repo.evaluate_student_year_validation(
        sid, tid, view_session="mixed", result_row=row
    )
    return str(ev.get("suggested_outcome") or "").strip().lower()


def student_eligible_for_success_certificate(
    repo: Repository,
    *,
    template_id: int,
    student_id: int,
    jury_session_id: int | None = None,
) -> bool:
    """Attestation uniquement pour réussite M1 (pass_m2) ou clôture M2 (validate_year)."""
    return (
        success_certificate_outcome_key(
            repo,
            template_id=int(template_id),
            student_id=int(student_id),
            jury_session_id=jury_session_id,
        )
        in SUCCESS_CERTIFICATE_OUTCOMES
    )


def _success_certificate_filename(stu: dict[str, Any], *, level: str, track: str) -> str:
    ln = str(stu.get("last_name") or "Student").strip().replace("/", "-")
    fn = str(stu.get("first_name") or "").strip().replace("/", "-")
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    return f"{ln} {fn} Certificate of Achievement {lv}{tr}.pdf"


def success_certificate_default_filename(
    stu: dict[str, Any], *, level: str, track: str
) -> str:
    return _success_certificate_filename(stu, level=level, track=track)


def find_success_certificate_pdf_in_dir(
    directory: str | Path,
    stu: dict[str, Any],
    *,
    level: str,
    track: str,
) -> Path | None:
    dest = Path(directory).expanduser()
    if not dest.is_dir():
        return None
    exact = dest / _success_certificate_filename(stu, level=level, track=track)
    if exact.is_file():
        return exact.resolve()
    return None


def _success_certificate_statement(
    *,
    outcome: str,
    level: str,
    track: str,
    full_name: str,
    academic_year: str,
) -> str:
    """Texte principal de l'attestation (anglais)."""
    lv = str(level or "").strip().upper()
    tr = str(track or "").strip().upper()
    ay = str(academic_year or "").strip() or "—"
    program = track_program_label(lv, tr)
    name = str(full_name or "").strip() or "the student"
    degree = f"Master of Nuclear Energy ({lv} {program})" if program else "Master of Nuclear Energy"
    oc = str(outcome or "").strip().lower()

    if oc == "pass_m2" or lv == "M1":
        return (
            f"This is to certify that {name} has successfully completed the first year (M1) "
            f"of the {degree}, academic year {ay}."
        )
    if oc == "validate_year" or lv == "M2":
        return (
            f"This is to certify that {name} has successfully completed the "
            f"{degree}, academic year {ay}."
        )
    return (
        f"This is to certify that {name} has successfully completed the "
        f"{degree}, academic year {ay}."
    )


def write_success_certificate_pdf(
    repo: Repository,
    *,
    template_id: int,
    student_id: int,
    path: str | Path,
    jury_session_id: int | None = None,
    place: str = "Orsay",
    issue_date: str = "",
) -> None:
    """
    Certificate of Achievement (English) — short official letter for successful students.

    Requires a final jury session and a success outcome (``pass_m2`` / ``validate_year``).
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Spacer

    tid, sid = int(template_id), int(student_id)
    if not repo.has_final_jury_session(tid):
        raise ValueError(
            "The certificate of achievement requires a « Final » deliberation for this track."
        )

    outcome = success_certificate_outcome_key(
        repo, template_id=tid, student_id=sid, jury_session_id=jury_session_id
    )
    if outcome not in SUCCESS_CERTIFICATE_OUTCOMES:
        raise ValueError(
            "No success decision recorded for this student "
            "(certificate is issued for year validated / admitted to M2 only)."
        )

    stu = repo.get_student(sid)
    if not stu:
        raise ValueError("Student not found.")
    tpl = repo.get_template(tid) or {}
    lv = str(tpl.get("level") or "").strip().upper()
    tr = str(tpl.get("track") or "").strip().upper()
    ay = str(tpl.get("academic_year") or "").strip()
    program = track_program_label(lv, tr)
    footer_program = _transcript_footer_program_label(lv, tr)

    data = repo.get_student_result_summary(
        tid, view_session="mixed", include_all_students=True
    )
    row = next((r for r in data if int(r.get("student_id") or 0) == sid), None)
    if not row:
        raise ValueError("Student is not enrolled in this track template.")

    gwj = row.get("global_with_jury")
    avg = float(gwj) if gwj is not None else None
    mention = resolve_transcript_mention(
        repo, student_id=sid, template_id=tid, grade=avg
    )

    full_name = f"{stu.get('first_name', '')} {stu.get('last_name', '')}".strip()
    statement = _success_certificate_statement(
        outcome=outcome,
        level=lv,
        track=tr,
        full_name=full_name,
        academic_year=ay,
    )

    header_emails = repo.transcript_header_emails(tid)
    signature_lines = _transcript_signature_lines(
        repo,
        final=True,
        student_id=sid,
        template_id=tid,
        academic_year=ay,
        level=lv,
        track=tr,
        view_session="mixed",
        row=row,
        place=place,
        issue_date=issue_date,
    )
    doc = _transcript_doc_template(
        path,
        level=lv,
        program=footer_program,
        header_emails=header_emails,
        signature_lines=signature_lines,
    )

    title_orange = colors.HexColor(MNE_ACCENT_ORANGE)
    body = ParagraphStyle(
        name="CertBody",
        fontSize=11,
        leading=15,
        spaceAfter=6,
    )
    center = ParagraphStyle(name="CertCenter", parent=body, alignment=1)
    title_style = ParagraphStyle(
        name="CertTitle",
        parent=center,
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=title_orange,
        spaceAfter=4,
    )
    subtitle = ParagraphStyle(
        name="CertSub",
        parent=center,
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=title_orange,
        spaceAfter=12,
    )
    identity = ParagraphStyle(
        name="CertId",
        parent=body,
        fontSize=10.5,
        leading=14,
        spaceAfter=3,
    )
    statement_style = ParagraphStyle(
        name="CertStatement",
        parent=body,
        fontSize=11,
        leading=16,
        spaceBefore=10,
        spaceAfter=10,
    )

    birth = _transcript_birth_display(str(stu.get("birth_date") or ""))
    birth_place = _transcript_birth_place_display(stu)
    stu_no = student_transcript_number(stu)

    story: list[Any] = []
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph("Certificate of Achievement", title_style))
    story.append(Paragraph(mne_level_master_line(lv), subtitle))
    if program:
        story.append(Paragraph(program, center))
    story.append(Paragraph(f"Academic year {ay}", center))
    story.append(Spacer(1, 0.8 * cm))

    story.append(
        Paragraph(
            f"<b>{stu.get('last_name', '')} {stu.get('first_name', '')}</b>".strip(),
            identity,
        )
    )
    if stu_no:
        story.append(Paragraph(f"Student number: {stu_no}", identity))
    if birth:
        place_bit = f", {birth_place}" if birth_place else ""
        story.append(Paragraph(f"Date of birth: {birth}{place_bit}", identity))
    elif birth_place:
        story.append(Paragraph(f"Place of birth: {birth_place}", identity))

    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(statement, statement_style))

    if avg is not None:
        story.append(
            Paragraph(
                f"Final average (including jury deliberation): {_transcript_grade_comma(avg)}/20",
                identity,
            )
        )
    if mention:
        story.append(Paragraph(f"Honours (mention): {mention}", identity))

    decision_en = {
        "pass_m2": "Jury decision: first year (M1) validated — admitted to proceed to M2.",
        "validate_year": "Jury decision: Master year validated — programme completed.",
    }.get(outcome, "")
    if decision_en:
        story.append(Paragraph(decision_en, identity))

    story.append(Spacer(1, 0.4 * cm))
    story.append(
        Paragraph(
            "This certificate is issued following the final jury deliberation "
            "of the Master of Nuclear Energy.",
            identity,
        )
    )

    doc.build(story)
