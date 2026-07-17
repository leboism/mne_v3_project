"""Équipe pédagogique du master (mention, parcours, secrétariats)."""

from __future__ import annotations

from typing import Any

from .parcours import PARCOURS_BY_LEVEL, track_label

ROLE_MENTION = "mention"
ROLE_TRACK = "track"
ROLE_SECRETARIAT = "secretariat"
ROLE_STUDENT_REP = "student_rep"

# Trois directeurs de la mention (ensemble du master MNE).
MENTION_DIRECTOR_COUNT = 3

# Représentants des étudiants : 2 pour l'ensemble du M1, 2 par parcours en M2.
STUDENT_REP_COUNT_M1 = 2
STUDENT_REP_COUNT_M2_PER_TRACK = 2

# Responsables de parcours : 1 en M1, 2 en M2 par parcours.
TRACK_DIRECTOR_COUNT_M1 = 1
TRACK_DIRECTOR_COUNT_M2_PER_TRACK = 2

ROLE_LABELS: dict[str, str] = {
    ROLE_MENTION: "Directeur de la mention",
    ROLE_TRACK: "Responsable de parcours",
    ROLE_SECRETARIAT: "Secrétariat pédagogique",
    ROLE_STUDENT_REP: "Représentant des étudiants",
}


def mention_director_label(slot: int) -> str:
    """Libellé de poste par défaut pour le slot 0…2."""
    n = int(slot) + 1
    return f"Directeur {n} de la mention"


def mention_director_post_label(row: dict[str, Any] | None, slot: int) -> str:
    """Intitulé de poste affiché (personnalisé ou défaut)."""
    if row:
        custom = str(row.get("post_label") or "").strip()
        if custom:
            return custom
    return mention_director_label(slot)

# Établissements d'inscription étudiants (fiche étudiant).
MNE_ENROLLMENT_INSTITUTIONS: tuple[str, ...] = (
    "Université Paris-Saclay",
    "Institut Polytechnique de Paris",
    "Chimie Paris PSL",
    "ENSTA Paris",
)

# Affiliations des responsables pédagogiques (directeurs, parcours, secrétariat).
MNE_TEAM_AFFILIATIONS: tuple[str, ...] = (
    "Université Paris-Saclay",
    "ENSTA Paris",
    "Chimie ParisTech-PSL",
    "CentraleSupélec",
    "CEA / INSTN",
    "École des Ponts ParisTech",
    "Institut Polytechnique de Paris",
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


def m2_track_pairs() -> list[tuple[str, str, str]]:
    """Parcours M2 uniquement (représentants étudiants)."""
    return [(lv, code, lab) for lv, code, lab in all_track_pairs() if lv == "M2"]


def track_director_slot_count(level: str) -> int:
    """Nombre de responsables pour un parcours (1 en M1, 2 en M2)."""
    if str(level or "").strip().upper() == "M2":
        return TRACK_DIRECTOR_COUNT_M2_PER_TRACK
    return TRACK_DIRECTOR_COUNT_M1


def track_director_table_rows() -> list[tuple[str, str, str, int]]:
    """(niveau, parcours, libellé ligne, slot) pour le tableau responsables."""
    out: list[tuple[str, str, str, int]] = []
    for lv, code, lab in all_track_pairs():
        slots = track_director_slot_count(lv)
        for slot in range(slots):
            row_lab = f"{lab} — resp. {slot + 1}" if slots > 1 else lab
            out.append((lv, code, row_lab, slot))
    return out
