from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParsedAssessment:
    name: str
    kind: str
    coefficient: float
    session: int
    display_order: int


_SESSION_RE = re.compile(
    r"MCC\s*SESSION\s*(?P<session>[12])\s*:\s*(?P<formula>.*?)(?=MCC\s*SESSION\s*[12]\s*:|$)",
    flags=re.IGNORECASE | re.DOTALL,
)

# Exemples :
# - EE * 40%
# - CC [Rep] * 30%
# - CCTP * 20% + EE * 80%
# - Mémoire * 100% [RapM]
_TERM_PCT_RE = re.compile(r"\*\s*(?P<pct>[0-9]+(?:[.,][0-9]+)?)\s*%\s*(?P<bracket>\[[^\]]*\])?\s*$")


def _normalize_kind(s: str) -> str:
    # Keep letters only, upper-case, compact; fits DB kind usage.
    s = (s or "").strip().upper()
    s = re.sub(r"\\s+", " ", s)
    # Remove punctuation except letters/spaces
    s = re.sub(r"[^A-ZÀ-ÖØ-Ý\\s]", "", s)
    s = s.strip().replace(" ", "_")
    # Limit length to keep UI tidy
    return s[:12] if s else ""


def _clean_bracket_text(s: str | None) -> str:
    if not s:
        return ""
    inner = s.strip()[1:-1].strip()  # remove [ ]
    # Normaliser un peu (évite les espaces parasites)
    inner = re.sub(r"\s+", " ", inner)
    return inner


def parse_mcc_text_to_assessments(mcc_text: str, *, display_order_start: int = 0) -> list[ParsedAssessment]:
    """
    Parse un texte MCC de maquette et retourne des assessments.

    Hypothèse : MCC ressemble à :
    - "MCC SESSION 1 : CC * 30% + EE * 70%\n MCC SESSION 2 : CC [Rep] * 30% + EE * 70%"
    """
    text = (mcc_text or "").strip()
    if not text:
        return []

    assessments: list[ParsedAssessment] = []
    order = display_order_start
    for m in _SESSION_RE.finditer(text):
        session = int(m.group("session"))
        formula = m.group("formula") or ""
        formula = formula.replace("\n", " ").strip()

        for chunk in [c.strip() for c in formula.split("+") if c and c.strip()]:
            mm = _TERM_PCT_RE.search(chunk)
            if not mm:
                continue
            pct_raw = (mm.group("pct") or "").strip()
            pct = float(pct_raw.replace(",", "."))
            bracket = _clean_bracket_text(mm.group("bracket"))

            # Left side before '*' contains the kind (and sometimes bracket like "CC [Rep]")
            lhs = chunk.split("*", 1)[0].strip()
            # If bracket was before '*', move it to bracket variable
            b2 = re.search(r"\[[^\]]*\]", lhs)
            if b2 and not bracket:
                bracket = _clean_bracket_text(b2.group(0))
                lhs = lhs.replace(b2.group(0), " ").strip()

            kind = _normalize_kind(lhs)

            if not kind:
                continue
            label = kind if not bracket else f"{kind} {bracket}"
            # Le pourcentage est dans le coefficient; on l’ajoute au nom pour distinguer les multiples termes.
            name = f"{label} ({pct:g}%)"
            assessments.append(
                ParsedAssessment(
                    name=name,
                    kind=kind,
                    coefficient=pct,
                    session=session,
                    display_order=order,
                )
            )
            order += 1

    # Option : dédoublonner des assessments strictement identiques (au cas où Excel duplique)
    dedup: dict[tuple[str, str, float, int, int], ParsedAssessment] = {}
    for a in assessments:
        dedup[(a.name, a.kind, a.coefficient, a.session, a.display_order)] = a
    return list(dedup.values())


def parse_mcc_text_to_assessments_dicts(mcc_text: str, *, display_order_start: int = 0) -> list[dict[str, Any]]:
    """Version dict pratique pour l’insertion DB."""
    out = parse_mcc_text_to_assessments(mcc_text, display_order_start=display_order_start)
    return [
        {
            "name": a.name,
            "kind": a.kind,
            "coefficient": a.coefficient,
            "session": a.session,
            "display_order": a.display_order,
        }
        for a in out
    ]

