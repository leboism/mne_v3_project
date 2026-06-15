"""Équipe pédagogique du master (mention, parcours, secrétariats)."""

from __future__ import annotations

from .parcours import PARCOURS_BY_LEVEL, track_label

ROLE_MENTION = "mention"
ROLE_TRACK = "track"
ROLE_SECRETARIAT = "secretariat"

# Trois directeurs de la mention (ensemble du master MNE).
MENTION_DIRECTOR_COUNT = 3

ROLE_LABELS: dict[str, str] = {
    ROLE_MENTION: "Directeur de la mention",
    ROLE_TRACK: "Responsable de parcours",
    ROLE_SECRETARIAT: "Secrétariat pédagogique",
}


def mention_director_label(slot: int) -> str:
    """Libellé de poste pour le slot 0…2."""
    n = int(slot) + 1
    return f"Directeur {n} de la mention"

# Établissements d'inscription (aligné sur la fiche étudiant).
MNE_ENROLLMENT_INSTITUTIONS: tuple[str, ...] = (
    "Université Paris-Saclay",
    "Institut Polytechnique de Paris",
    "Chimie Paris PSL",
    "ENSTA Paris",
)


def encode_tracks_scope(pairs: list[tuple[str, str]]) -> str:
    """Encode ``[(M1, P), (M2, NPD)]`` → ``M1:P|M2:NPD``."""
    out: list[str] = []
    for lv, tr in pairs:
        lv_s = str(lv or "").strip().upper()
        tr_s = str(tr or "").strip().upper()
        if lv_s and tr_s:
            out.append(f"{lv_s}:{tr_s}")
    return "|".join(out)


def decode_tracks_scope(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for chunk in str(raw or "").split("|"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        lv, tr = chunk.split(":", 1)
        lv, tr = lv.strip().upper(), tr.strip().upper()
        if lv and tr:
            pairs.append((lv, tr))
    return pairs


def tracks_scope_label(raw: str) -> str:
    pairs = decode_tracks_scope(raw)
    if not pairs:
        return "—"
    return ", ".join(f"{lv} {track_label(lv, tr)}" for lv, tr in pairs)


def all_track_pairs() -> list[tuple[str, str, str]]:
    """(level, track_code, display_label) pour cases à cocher."""
    out: list[tuple[str, str, str]] = []
    for lv, tracks in PARCOURS_BY_LEVEL.items():
        for code, _lab in tracks:
            out.append((lv, code, f"{lv} {track_label(lv, code)}"))
    return out
