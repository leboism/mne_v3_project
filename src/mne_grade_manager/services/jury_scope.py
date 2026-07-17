"""Périmètre des délibérations (blocs) pour filtrer les PV multi-sessions."""

from __future__ import annotations

import re

_MAX_BLOC_NUMBER = 12


def block_key_bloc_number(block_key: str) -> int | None:
    """Numéro de bloc depuis un libellé maquette, ex. « Common courses 1 (block 1) » → 1."""
    text = str(block_key or "")
    m = re.search(r"\(block\s*(\d+)\)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"block\s*(\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def extract_bloc_numbers(scope_text: str) -> set[int]:
    """Extrait les numéros de blocs d'un libellé de périmètre jury."""
    text = str(scope_text or "")
    nums: set[int] = set()
    for m in re.finditer(
        r"blocs?\s*((?:\d+\s*(?:,|;|et|&)\s*)*\d+)",
        text,
        re.IGNORECASE,
    ):
        for n in re.findall(r"\d+", m.group(1)):
            val = int(n)
            if 1 <= val <= _MAX_BLOC_NUMBER:
                nums.add(val)
    for m in re.finditer(
        r"blocs?\s*(\d+)\s*(?:à|a|-|–)\s*(\d+)",
        text,
        re.IGNORECASE,
    ):
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi <= _MAX_BLOC_NUMBER:
            nums.update(range(min(lo, hi), max(lo, hi) + 1))
    if not nums:
        for m in re.finditer(r"\bblocs?\s*(\d+)\b", text, re.IGNORECASE):
            val = int(m.group(1))
            if 1 <= val <= _MAX_BLOC_NUMBER:
                nums.add(val)
    return nums


def scope_text_to_block_keys(scope_text: str, block_keys: list[str]) -> set[str]:
    """Associe un texte de périmètre aux noms de blocs de la maquette."""
    scope = str(scope_text or "").strip()
    if not scope:
        return set()
    nums = extract_bloc_numbers(scope)
    if not nums:
        return set()
    matched: set[str] = set()
    for bk in block_keys:
        bn = block_key_bloc_number(bk)
        if bn is not None and bn in nums:
            matched.add(bk)
    return matched


def suggest_scope_text(session_kind: str, *, ordinal: int = 0) -> str:
    """Proposition de périmètre selon le type et l'ordre de la délibération."""
    kind = str(session_kind or "S1").strip().upper()
    if kind == "FINAL":
        return "Année"
    if kind == "S2":
        return "Blocs 2 et 3 — S2" if ordinal > 0 else "Bloc 2 — S2"
    if ordinal <= 0:
        return "Bloc 1 — S1"
    if ordinal == 1:
        return "Blocs 2, 3 et 4 — S1"
    return f"Blocs … — S1 (délibération S1 n°{ordinal + 1})"


def scope_field_placeholder(session_kind: str = "S1") -> str:
    return suggest_scope_text(session_kind, ordinal=0)


def scope_example_help_text(
    session_kind: str,
    *,
    suggested: str = "",
    current: str = "",
) -> str:
    """Texte d'aide affiché sous le champ Périmètre."""
    kind = str(session_kind or "S1").strip().upper()
    example = (suggested or suggest_scope_text(kind)).strip()
    lines = [
        f"Exemple suggéré : {example}",
        "Écrire Bloc N ou Blocs N, M et P — les numéros correspondent à (block N) dans la maquette.",
    ]
    if kind == "S1":
        lines.append("1er jury S1 → Bloc 1 — S1 · 2e jury S1 → Blocs 2, 3 et 4 — S1.")
    elif kind == "FINAL":
        lines.append("Jury final → Année (décisions de passage, mentions).")
    if current and current != example:
        lines.append(f"Périmètre enregistré : {current}")
    return "\n".join(lines)


def _sanitize_filename_part(text: str, *, max_len: int = 48) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r'[<>:"/\\|?*]', "-", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .-_")[:max_len]


def extract_jury_date_token(*texts: str) -> str:
    """Retourne ``YYYYMMDD`` si une date jj/mm/aaaa est trouvée."""
    for text in texts:
        m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", str(text or ""))
        if not m:
            continue
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}{month:02d}{day:02d}"
    return ""


def _scope_filename_part(scope_text: str) -> str:
    s = str(scope_text or "")
    s = re.sub(r"\s*[-–—]\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\s*", " ", s)
    s = re.sub(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}", "", s)
    return _sanitize_filename_part(s)


def suggest_pv_pdf_filename(
    *,
    track: str,
    academic_year: str = "",
    session: dict | None = None,
    draft: bool = False,
) -> str:
    """Nom de fichier PDF suggéré : parcours, date du jury, périmètre."""
    tr = _sanitize_filename_part(str(track or "X").upper(), max_len=8) or "X"
    sess = session or {}
    scope = _scope_filename_part(str(sess.get("scope_text") or ""))
    date_tok = extract_jury_date_token(
        str(sess.get("label") or ""),
        str(sess.get("scope_text") or ""),
    )
    ay = str(academic_year or "").replace("-", "").strip()

    parts: list[str] = [f"PV jury {tr}"]
    if date_tok:
        parts.append(date_tok)
    elif ay:
        parts.append(ay)
    if scope:
        parts.append(scope)
    if draft:
        parts.append("brouillon")
    return " - ".join(parts) + ".pdf"
