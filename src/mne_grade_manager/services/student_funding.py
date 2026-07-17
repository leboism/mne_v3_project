"""Bourses et exemptions de frais d'inscription (fiche étudiant)."""

from __future__ import annotations

FUNDING_CAMPUS_FRANCE = "campus_france"
FUNDING_IDEX = "idex"
FUNDING_EIFFEL = "eiffel"
FUNDING_TUITION_EXEMPTION = "tuition_exemption"

FUNDING_CHOICES: tuple[tuple[str, str], ...] = (
    (FUNDING_CAMPUS_FRANCE, "Campus France"),
    (FUNDING_IDEX, "IDEX"),
    (FUNDING_EIFFEL, "EIFFEL"),
    (FUNDING_TUITION_EXEMPTION, "Exemption frais d'inscription"),
)

_FUNDING_LABELS: dict[str, str] = dict(FUNDING_CHOICES)

_FUNDING_ALIASES: dict[str, str] = {
    "campus_france": FUNDING_CAMPUS_FRANCE,
    "campus france": FUNDING_CAMPUS_FRANCE,
    "campusfrance": FUNDING_CAMPUS_FRANCE,
    "idex": FUNDING_IDEX,
    "eiffel": FUNDING_EIFFEL,
    "tuition_exemption": FUNDING_TUITION_EXEMPTION,
    "exemption": FUNDING_TUITION_EXEMPTION,
    "exemption_frais": FUNDING_TUITION_EXEMPTION,
    "exemption_frais_inscription": FUNDING_TUITION_EXEMPTION,
    "frais_inscription": FUNDING_TUITION_EXEMPTION,
    "exonération": FUNDING_TUITION_EXEMPTION,
    "exoneration": FUNDING_TUITION_EXEMPTION,
    "exoneration_frais": FUNDING_TUITION_EXEMPTION,
}


def _normalize_token(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "_")


def parse_funding_codes(raw: str) -> set[str]:
    out: set[str] = set()
    for part in str(raw or "").split(","):
        tok = _normalize_token(part)
        if not tok:
            continue
        code = _FUNDING_ALIASES.get(tok, tok)
        if code in _FUNDING_LABELS:
            out.add(code)
    return out


def encode_funding_codes(codes: set[str] | list[str]) -> str:
    order = [code for code, _ in FUNDING_CHOICES]
    selected = {str(c).strip().lower() for c in codes if str(c).strip()}
    return ",".join(c for c in order if c in selected)


def funding_label_fr(code: str) -> str:
    return _FUNDING_LABELS.get(str(code or "").strip().lower(), str(code or "").strip())


def format_funding_display(funding: str, funding_other: str = "") -> str:
    parts = [funding_label_fr(c) for c in encode_funding_codes(parse_funding_codes(funding)).split(",") if c]
    other = str(funding_other or "").strip()
    if other:
        parts.append(other)
    return ", ".join(parts) if parts else "—"
