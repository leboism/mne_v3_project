"""Ouvre le client mail par défaut du système via une URL mailto: (Windows, macOS, Linux)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from urllib.parse import quote, urlencode

# Limite prudente (Outlook / navigateurs sous Windows ~ 2 Ko).
MAX_MAILTO_URL_LEN = 2048


def _encode_mailto_value(value: str) -> str:
    return quote(value, safe="")


def build_mailto_url(
    *,
    to: Sequence[str] | None = None,
    cc: Sequence[str] | None = None,
    bcc: Sequence[str] | None = None,
    subject: str = "",
    body: str = "",
) -> str:
    """Construit une URL ``mailto:`` (destinataires en Bcc par défaut pour les convocations)."""
    to_addrs = [a.strip() for a in (to or ()) if a and a.strip()]
    cc_addrs = [a.strip() for a in (cc or ()) if a and a.strip()]
    bcc_addrs = [a.strip() for a in (bcc or ()) if a and a.strip()]

    query: list[tuple[str, str]] = []
    if cc_addrs:
        query.append(("cc", ",".join(cc_addrs)))
    if bcc_addrs:
        query.append(("bcc", ",".join(bcc_addrs)))
    if subject.strip():
        query.append(("subject", subject.strip()))
    if body:
        body_norm = body.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        query.append(("body", body_norm))

    q = urlencode(query, quote_via=lambda s, safe, enc, errors: _encode_mailto_value(s))

    if to_addrs:
        path = ",".join(to_addrs)
        return f"mailto:{path}?{q}" if q else f"mailto:{path}"
    return f"mailto:?{q}" if q else "mailto:"


@dataclass(frozen=True)
class MailtoOpenResult:
    opened: bool
    message: str
    body_on_clipboard: bool = False


def open_default_mail_client(
    *,
    bcc: Sequence[str] | None = None,
    to: Sequence[str] | None = None,
    cc: Sequence[str] | None = None,
    subject: str = "",
    body: str = "",
    clipboard=None,
) -> MailtoOpenResult:
    """
    Ouvre l'application mail par défaut.

    Si l'URL dépasse la limite (nombreux destinataires / long corps), le corps est
    retiré de l'URL puis copié dans le presse-papiers si ``clipboard`` est fourni.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    bcc_list = [a.strip() for a in (bcc or ()) if a and a.strip()]
    strategies: list[tuple[str, str, str]] = [
        ("full", subject, body),
        ("no_body", subject, ""),
    ]
    if bcc_list:
        strategies.append(("bcc_only", "", ""))

    for mode, subj, bod in strategies:
        url = build_mailto_url(bcc=bcc_list or None, to=to, cc=cc, subject=subj, body=bod)
        if len(url) > MAX_MAILTO_URL_LEN:
            continue
        if not QDesktopServices.openUrl(QUrl(url)):
            return MailtoOpenResult(
                False,
                "Impossible d'ouvrir le client mail (aucun programme associé à mailto: ?). "
                "Utilisez les boutons Copier.",
            )
        if mode == "full":
            return MailtoOpenResult(
                True,
                "Le client mail par défaut a été ouvert avec le message prérempli "
                "(destinataires en Cci / Bcc).",
            )
        if mode == "no_body":
            if clipboard is not None and body.strip():
                clipboard.setText(body)
            return MailtoOpenResult(
                True,
                "Le client mail a été ouvert avec l'objet et les destinataires (Cci). "
                "Le corps du message a été copié dans le presse-papiers — collez-le dans le mail.",
                body_on_clipboard=True,
            )
        if clipboard is not None:
            parts = []
            if subject.strip():
                parts.append(f"Objet : {subject.strip()}")
            if body.strip():
                parts.append(body.strip())
            if parts:
                clipboard.setText("\n\n".join(parts))
        return MailtoOpenResult(
            True,
            "Trop de destinataires pour un message entièrement prérempli : seules les adresses "
            "Cci ont été transmises. Objet et corps copiés dans le presse-papiers.",
            body_on_clipboard=True,
        )

    return MailtoOpenResult(
        False,
        "Liste de destinataires trop longue pour mailto: (limite Windows). "
        "Copiez les adresses et le message avec les boutons ci-dessous.",
    )
