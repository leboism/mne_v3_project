"""
Génération PDF pour l’onglet Jury (brouillons : tableau de notes, PV, relevé partiel).
Les mises en page pourront être rapprochées des modèles institutionnels (PV_M1C, transcripts…).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository import Repository

from ..core.mne_modules import course_ue_code
from ..core.parcours import track_program_label
from . import terminology as T
from .calculations import weighted_average
from .lookups import student_transcript_number

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

# Charte visuelle des PV / tableaux jury (modèles MNE 2020–2025).
MNE_TITLE_BLUE = "#1F4E79"
MNE_HEADER_BLUE = "#2F5496"
MNE_ACCENT_ORANGE = "#ED7D31"
MNE_ROW_ALT = "#F2F2F2"
MNE_SIGNATURE_COL = 3


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        name="JuryTitle",
        parent=styles["Heading1"],
        fontSize=14,
        spaceAfter=0.4 * cm,
    )
    h2 = ParagraphStyle(
        name="JuryH2",
        parent=styles["Heading2"],
        fontSize=11,
        spaceAfter=0.2 * cm,
        spaceBefore=0.3 * cm,
    )
    body = ParagraphStyle(name="JuryBody", parent=styles["Normal"], fontSize=9, leading=11)
    return colors, title, h2, body


def _mne_pv_paragraph_styles(colors):
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle

    return {
        "master": ParagraphStyle(
            name="MneMasterTitle",
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            textColor=colors.HexColor(MNE_TITLE_BLUE),
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "year": ParagraphStyle(
            name="MneAcademicYear",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=colors.HexColor(MNE_ACCENT_ORANGE),
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "jury": ParagraphStyle(
            name="MneJuryTitle",
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=colors.HexColor(MNE_TITLE_BLUE),
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "program": ParagraphStyle(
            name="MneProgramLine",
            fontName="Helvetica",
            fontSize=10,
            leading=12,
            textColor=colors.HexColor(MNE_TITLE_BLUE),
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "section": ParagraphStyle(
            name="MneSectionHdr",
            fontName="Helvetica-BoldOblique",
            fontSize=11,
            leading=13,
            textColor=colors.black,
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
    inst = str(member.get("institution") or "").strip()
    if title and inst:
        return f"{title} — {inst}"
    return title or inst or ""


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
            color=colors.HexColor(MNE_TITLE_BLUE),
            spaceBefore=4,
            spaceAfter=0.35 * cm,
        )
    )


def _institutional_table_style(colors, *, font_size: int = 8) -> list[tuple]:
    return [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(MNE_HEADER_BLUE)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), font_size),
        ("FONTSIZE", (0, 1), (-1, -1), font_size),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B4B4B4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(MNE_ROW_ALT)]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]


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

    president, others = members[0], members[1:]
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


def _grade_matrix_table_style(colors, *, font_size: int = 8):
    from reportlab.platypus import TableStyle

    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(MNE_HEADER_BLUE)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("FONTSIZE", (0, 0), (-1, 0), max(7, font_size - 1)),
            ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(MNE_ROW_ALT)]),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )


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
    data: list[dict],
    blocks: list[tuple[str, list[dict[str, Any]]]],
    view_session: str,
    colors,
    h2_style,
    include_year_summary: bool = True,
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
            headers.extend(_ue_header_cell(course_ue_code(c) or str(c.get("code") or "")) for c in chunk)
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
                    blk_avg = (row.get("blocks") or {}).get(str(block_name or ""))
                    rcells.append(_pdf_num(float(blk_avg)) if blk_avg is not None else "—")
                rows.append(rcells)
            col_w = _grade_matrix_col_widths(len(chunk), with_block_avg=is_last)
            tbl = Table(rows, colWidths=col_w, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(_grade_matrix_table_style(colors))
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
        tbl = Table(rows, colWidths=col_w, repeatRows=1, hAlign="LEFT")
        tbl.setStyle(_grade_matrix_table_style(colors))
        story.append(tbl)


def write_grade_matrix_pdf(
    repo: Repository,
    *,
    template_id: int,
    view_session: str,
    path: str | Path,
) -> None:
    """Tableau des notes en portrait, découpé par blocs (modèle M1P / M1C)."""
    from reportlab.platypus import Paragraph, Spacer

    colors, title_style, h2_style, body_style = _styles()
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    title_txt = str(tpl.get("name") or "Maquette")
    ay = str(tpl.get("academic_year") or "")
    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    vs = str(view_session or "s1")

    data = repo.get_student_result_summary(int(template_id), view_session=vs)
    blocks = repo.list_template_blocks_with_courses(int(template_id))

    story: list = [
        Paragraph(f"Tableau des notes — {title_txt}", title_style),
        Paragraph(f"{ay} · {lv} {tr} · session affichée : {vs.upper()}", body_style),
        Spacer(1, 12),
    ]
    _append_portrait_grade_tables(
        story,
        data=data,
        blocks=blocks,
        view_session=vs,
        colors=colors,
        h2_style=h2_style,
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
    adjustments = repo.list_jury_adjustments_for_export(int(template_id))
    s2 = repo.list_second_session_for_export(int(template_id))

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

    story.append(Paragraph("Étudiants envoyés en seconde session (par UE)", ps["section"]))
    if not s2:
        story.append(Paragraph("Aucune décision enregistrée.", body_style))
    else:
        srows = [["Étudiant", "N° I.N.E.", "UE", "Libellé UE"]]
        for x in s2:
            srows.append(
                [
                    f"{x.get('st_last') or ''} {x.get('st_first') or ''}".strip(),
                    student_transcript_number(x),
                    str(x.get("course_code") or ""),
                    str(x.get("course_name") or "")[:45],
                ]
            )
        story.append(_institutional_data_table(srows, colors))

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
    if vs == "s2":
        base = d.get("s2") if d.get("sent_s2") else d.get("s1")
    else:
        base = d.get("s1")
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
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    _, title_style, h2_style, body_style = _styles()
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    program = track_program_label(lv, tr)

    data = repo.get_student_result_summary(int(template_id), view_session=str(view_session or "s1"))
    blocks = repo.list_template_blocks_with_courses(int(template_id))
    vs = str(view_session or "s1").lower()

    story: list = [
        Paragraph(f"Averages - {session_title}", title_style),
        Paragraph(f"{lv} Nuclear Energy", body_style),
        Paragraph(program, body_style),
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
            code = course_ue_code(c) or str(c.get("code") or "")
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
        story.insert(2, Paragraph(f"Année universitaire {ay}", body_style))

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
    from reportlab.platypus import Paragraph, Spacer

    colors, title_style, h2_style, body_style = _styles()
    tpl = next((t for t in repo.list_templates() if int(t["id"]) == int(template_id)), None) or {}
    lv = str(tpl.get("level") or "")
    tr = str(tpl.get("track") or "")
    ay = str(tpl.get("academic_year") or "")
    vs = str(view_session or "s1")

    data = repo.get_student_result_summary(int(template_id), view_session=vs)
    blocks = repo.list_template_blocks_with_courses(int(template_id))
    program = track_program_label(lv, tr)

    story: list = [
        Paragraph(f"{lv}{tr} — Tableau des notes", title_style),
        Paragraph(f"Master Nuclear Energy — {program}", body_style),
        Paragraph(f"Année universitaire {ay} · session {vs.upper()}", body_style),
        Spacer(1, 10),
    ]
    _append_portrait_grade_tables(
        story,
        data=data,
        blocks=blocks,
        view_session=vs,
        colors=colors,
        h2_style=h2_style,
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
    adjustments = repo.list_jury_adjustments_for_export(int(template_id))
    s2 = repo.list_second_session_for_export(int(template_id))
    outcomes = repo.list_jury_student_outcomes_for_export(
        int(template_id), jury_session_id=int(jury_session_id)
    )
    when = _format_pv_date(meeting_date.strip() or date.today().isoformat())

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
        story.append(Paragraph("Étudiants envoyés en seconde session (par UE)", ps["section"]))
        srows = [["Étudiant", "UE", "Intitulé"]]
        for x in s2:
            srows.append(
                [
                    f"{x.get('st_last') or ''} {x.get('st_first') or ''}".strip(),
                    str(x.get("course_code") or ""),
                    str(x.get("course_name") or "")[:45],
                ]
            )
        story.append(_institutional_data_table(srows, colors))
        story.append(Spacer(1, 10))

    if outcomes:
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

    if kind == "FINAL" and not outcomes:
        data = repo.get_student_result_summary(int(template_id), view_session=str(view_session or "s1"))
        n_ok = sum(1 for r in data if r.get("global_with_jury") is not None and float(r["global_with_jury"]) > 10)
        if n_ok == len(data) and data:
            story.append(
                Paragraph(
                    f"Tous les étudiants du parcours {lv} « {program} » ont validé leur année.",
                    body_style,
                )
            )

    _pv_doc_template(path).build(story)


def _transcript_grade_comma(v: float | None) -> str:
    if v is None:
        return ""
    return f"{float(v):.2f}".replace(".", ",")


def format_transcript_session_label(view_session: str, academic_year: str) -> str:
    vs = "S1" if str(view_session or "s1").lower() == "s1" else "S2"
    ay = str(academic_year or "").strip()
    if "-" in ay:
        a, b = ay.split("-", 1)
        return f"{vs} {a.strip()}/{b.strip()}"
    return f"{vs} {ay}" if ay else vs


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
    d = (row.get("ue_detail") or {}).get(cid) or {}
    display = str(d.get("display") or "").strip()
    if display in ("DEF", "ABJ"):
        return "Failed"
    if display == "VAL" or repo.has_ue_ects_validation(
        int(student_id), int(template_id), cid
    ):
        return "Passed"
    if display:
        return display
    grade = _transcript_ue_grade(row, cid, view_session)
    if grade is None:
        return ""
    return "Passed" if float(grade) >= 10.0 else "Failed"


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


def _transcript_doc_template(path: str | Path, *, header_emails: list[str]):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    emails = [e for e in header_emails if e]

    def _on_page(canvas, doc):
        from reportlab.lib.pagesizes import A4

        canvas.saveState()
        w, h = A4
        canvas.setFont("Helvetica-Bold", 10)
        canvas.setFillColorRGB(0.12, 0.31, 0.47)
        canvas.drawString(doc.leftMargin, h - 38, "Master Nuclear Energy")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColorRGB(0, 0, 0)
        y = h - 38
        for em in emails[:3]:
            canvas.drawRightString(w - doc.rightMargin, y, f"Email: {em}")
            y -= 10
        canvas.restoreState()

    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=72,
        bottomMargin=54,
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

    ``final=True`` : transcript définitif (S2, mention, classement) — jury final requis.
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
    program = track_program_label(lv, tr)
    vs = "s2" if final else str(view_session or "s1").lower()
    if vs not in {"s1", "s2"}:
        vs = "s2" if final else "s1"

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
    doc = _transcript_doc_template(path, header_emails=header_emails)

    title_blue = colors.HexColor(MNE_TITLE_BLUE)
    body = ParagraphStyle(name="TrBody", fontSize=9, leading=11)
    center = ParagraphStyle(name="TrCenter", parent=body, alignment=1)
    center_bold = ParagraphStyle(
        name="TrCenterBold", parent=center, fontName="Helvetica-Bold", fontSize=11, textColor=title_blue
    )
    center_orange = ParagraphStyle(
        name="TrYear",
        parent=center,
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=colors.HexColor(MNE_ACCENT_ORANGE),
    )
    label = ParagraphStyle(name="TrLabel", parent=body, fontName="Helvetica-Bold")

    story: list[Any] = [
        Spacer(1, 0.15 * cm),
        Paragraph("Master Nuclear Energy", center_bold),
        Paragraph(program, center_bold),
        Paragraph(f"Academic year {ay}", center_orange),
        Spacer(1, 0.2 * cm),
        Paragraph("Final Transcript:" if final else "Provisional Transcript:", center_bold),
        Spacer(1, 0.25 * cm),
    ]

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
                f"<b>Place of Birth:</b> {stu.get('birth_place') or stu.get('nationality') or ''}",
                body,
            ),
        ],
    ]
    id_tbl = Table(id_rows, colWidths=[8.5 * cm, 8.5 * cm], hAlign="LEFT")
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
    story.append(Spacer(1, 0.35 * cm))

    sess_lbl = _transcript_session_label(vs, ay)
    table_rows: list[list[Any]] = [
        [
            Paragraph("Credits", label),
            Paragraph("Courses", label),
            Paragraph("Grade", label),
            Paragraph("Result", label),
            Paragraph("Session", label),
        ]
    ]
    span_cmds: list[tuple] = []
    row_idx = 1
    current_section = ""

    for bk, clist in repo.list_template_blocks_with_courses(int(template_id)):
        section = _transcript_section_for_block(bk)
        if section != current_section:
            current_section = section
            table_rows.append(["", Paragraph(f"<b><i>{section}</i></b>", body), "", "", ""])
            span_cmds.append(("SPAN", (1, row_idx), (4, row_idx)))
            span_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#E8EEF4")))
            row_idx += 1

        graded = [c for c in clist if not int(c.get("optional") or 0)]
        block_ects = sum(
            float(c.get("ects") or 0) or float(c.get("global_coefficient") or 0) or 0 for c in graded
        )
        if block_ects <= 0 and graded:
            block_ects = float(len(graded))
        blk_avg = (row.get("blocks") or {}).get(bk)
        blk_grade = _transcript_grade_comma(float(blk_avg) if blk_avg is not None else None)
        blk_result = _transcript_block_passed(
            repo,
            student_id=int(student_id),
            template_id=int(template_id),
            block_name=bk,
            block_average=blk_avg,
            view_session=vs,
        )
        table_rows.append(
            [
                _transcript_grade_comma(block_ects) if block_ects else "",
                Paragraph(f"<b>{_transcript_block_display_name(bk)}</b>", body),
                Paragraph(f"<b>{blk_grade}</b>", body) if blk_grade else "",
                Paragraph(f"<b>{blk_result}</b>", body) if blk_result else "",
                sess_lbl,
            ]
        )
        row_idx += 1

        for c in clist:
            if int(c.get("optional") or 0):
                continue
            cid = int(c["course_id"])
            ects = float(c.get("ects") or 0) or float(c.get("global_coefficient") or 0) or 0
            grade = _transcript_ue_grade(row, cid, vs)
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
            grade_txt = disp if disp else _transcript_grade_comma(grade)
            ue_sess = repo.get_ue_transcript_session_label(
                int(student_id),
                int(template_id),
                cid,
                default_view_session=vs,
                default_academic_year=ay,
            )
            table_rows.append(
                [
                    _transcript_grade_comma(ects) if ects else "",
                    _transcript_course_label(c),
                    grade_txt,
                    result,
                    ue_sess,
                ]
            )
            row_idx += 1

    col_widths = [1.55 * cm, 8.2 * cm, 1.55 * cm, 1.55 * cm, 2.65 * cm]
    grades_tbl = Table(table_rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    style_cmds: list[tuple] = list(_institutional_table_style(colors_mod, font_size=8))
    style_cmds.extend(span_cmds)
    style_cmds.extend(
        [
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("ALIGN", (1, 0), (1, -1), "LEFT"),
        ]
    )
    grades_tbl.setStyle(TableStyle(style_cmds))
    story.append(grades_tbl)

    footer: list[Any] = [Spacer(1, 0.35 * cm)]
    gwj = row.get("global_with_jury")
    if final and gwj is not None:
        mention = resolve_transcript_mention(
            repo,
            student_id=int(student_id),
            template_id=int(template_id),
            grade=float(gwj),
        )
        rankable = repo.student_eligible_for_ranking(int(student_id), int(template_id))
        footer.append(
            Paragraph(f"<b>Final Result:</b> {_transcript_grade_comma(float(gwj))}/20", body)
        )
        footer.append(Paragraph("<b>Mention:</b> " + (mention or "—"), body))
        if rankable:
            rank = repo.student_global_rank(int(template_id), int(student_id), view_session=vs)
            if rank is not None:
                footer.append(Paragraph(f"<b>Ranking:</b> {rank}", body))
        else:
            footer.append(
                Paragraph(
                    "<i>Ranking: not applicable (second session).</i>",
                    body,
                )
            )
    else:
        footer.append(
            Paragraph(
                "<i>Provisional document — not valid for official certification until final jury.</i>",
                body,
            )
        )

    when = _format_transcript_issue_date(issue_date.strip() or None)
    footer.append(Spacer(1, 0.25 * cm))
    footer.append(Paragraph(f"Done in {place} {when}", body))
    footer.append(Spacer(1, 0.55 * cm))

    director = repo.get_track_director(ay, lv, tr)
    if director:
        title = str(director.get("title") or "Dr.").strip()
        ln = str(director.get("last_name") or "").strip().upper()
        fn = str(director.get("first_name") or "").strip()
        footer.append(Paragraph(f"{title} {fn} {ln}".strip(), label))
        role_line = str(director.get("notes") or "").strip() or f"Co-head of {lv} Nuclear Energy"
        footer.append(Paragraph(role_line, body))
    else:
        footer.append(Paragraph(f"Co-head of {lv} Nuclear Energy", label))

    story.append(KeepTogether(footer))
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
