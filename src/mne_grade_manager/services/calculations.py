from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

GRADE_SCALE_DECIMALS = 2


def round_grade_mne(value: float | None) -> float | None:
    """Arrondi officiel MNE (2 décimales, demi-unité vers le haut) — seuils 7 et 10."""
    if value is None:
        return None
    q = Decimal("1").scaleb(-GRADE_SCALE_DECIMALS)
    return float(Decimal(str(float(value))).quantize(q, rounding=ROUND_HALF_UP))


def grade_meets_minimum(value: float | None, minimum: float) -> bool:
    rounded = round_grade_mne(value)
    if rounded is None:
        return False
    return rounded >= float(minimum)


def grade_below_threshold(value: float | None, threshold: float) -> bool:
    rounded = round_grade_mne(value)
    if rounded is None:
        return True
    return rounded < float(threshold)


def weighted_average(items: Iterable[tuple[float | None, float]]) -> float | None:
    total = 0.0
    total_coef = 0.0
    for grade, coef in items:
        if grade is None:
            continue
        total += grade * coef
        total_coef += coef
    if total_coef == 0:
        return None
    return total / total_coef


def strict_weighted_average(items: Iterable[tuple[float | None, float]]) -> float | None:
    """
    Moyenne pondérée stricte: si un item a une note manquante (None), la moyenne est None.

    Utile pour des composantes obligatoires d'une UE (moyenne non calculable si une note manque),
    contrairement à `weighted_average` qui ignore les notes manquantes.
    """
    total = 0.0
    total_coef = 0.0
    saw_none = False
    for grade, coef in items:
        if grade is None:
            saw_none = True
            continue
        total += float(grade) * float(coef)
        total_coef += float(coef)
    if total_coef == 0:
        return None
    if saw_none:
        return None
    return total / total_coef
