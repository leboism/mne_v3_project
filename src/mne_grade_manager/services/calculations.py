from __future__ import annotations

from typing import Iterable


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
