"""Ouvre le client mail par défaut (mailto: ou Mail/Outlook avec pièces jointes)."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import quote, urlencode


MAX_MAILTO_URL_LEN = 2048


def _encode_mailto_value(value: str) -> str:
    return quote(value, safe="")


def _applescript_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


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
    attachment_paths: tuple[str, ...] = ()


def _open_mail_macos_mail_app(
    *,
    to: Sequence[str],
    cc: Sequence[str],
    subject: str,
    body: str,
    attachment_paths: Sequence[Path],
) -> bool:
    to_addrs = [a.strip() for a in to if a and a.strip()]
    if not to_addrs:
        return False
    cc_addrs = [a.strip() for a in cc if a and a.strip()]
    att = [p.resolve() for p in attachment_paths if p.is_file()]
    lines = [
        'tell application "Mail"',
        (
            "set msg to make new outgoing message with properties "
            f'{{subject:"{_applescript_string(subject)}", '
            f'content:"{_applescript_string(body)}", visible:true}}'
        ),
        "tell msg",
    ]
    for addr in to_addrs:
        lines.append(
            "make new to recipient at end of to recipients "
            f'with properties {{address:"{_applescript_string(addr)}"}}'
        )
    for addr in cc_addrs:
        lines.append(
            "make new cc recipient at end of cc recipients "
            f'with properties {{address:"{_applescript_string(addr)}"}}'
        )
    lines.append("end tell")
    if att:
        lines.append("tell content of msg")
        for path in att:
            lines.append(
                "make new attachment with properties "
                f'{{file name:POSIX file "{_applescript_string(str(path))}"}} '
                "at after the last paragraph"
            )
        lines.append("end tell")
    lines.extend(["activate", "end tell"])
    script = "\n".join(lines)
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode == 0


def _open_mail_windows_outlook(
    *,
    to: Sequence[str],
    cc: Sequence[str],
    subject: str,
    body: str,
    attachment_paths: Sequence[Path],
) -> bool:
    to_addrs = [a.strip() for a in to if a and a.strip()]
    if not to_addrs:
        return False
    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError:
        return False
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        mail.To = ";".join(to_addrs)
        cc_addrs = [a.strip() for a in cc if a and a.strip()]
        if cc_addrs:
            mail.CC = ";".join(cc_addrs)
        mail.Subject = subject
        mail.Body = body.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        for path in attachment_paths:
            if path.is_file():
                mail.Attachments.Add(str(path.resolve()))
        mail.Display()
        return True
    except Exception:
        return False


def open_default_mail_client(
    *,
    bcc: Sequence[str] | None = None,
    to: Sequence[str] | None = None,
    cc: Sequence[str] | None = None,
    subject: str = "",
    body: str = "",
    attachments: Sequence[str | Path] | None = None,
    clipboard=None,
) -> MailtoOpenResult:
    """
    Ouvre l'application mail par défaut.

    Avec ``attachments``, tente Mail (macOS) ou Outlook (Windows) pour joindre les fichiers.
    Sinon, ou en secours, utilise ``mailto:`` (sans pièce jointe).
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    att_paths = [Path(p).expanduser() for p in (attachments or ()) if str(p or "").strip()]
    att_files = tuple(str(p.resolve()) for p in att_paths if p.is_file())
    to_list = [a.strip() for a in (to or ()) if a and a.strip()]
    cc_list = [a.strip() for a in (cc or ()) if a and a.strip()]

    if att_files:
        if sys.platform == "darwin":
            if _open_mail_macos_mail_app(
                to=to_list,
                cc=cc_list,
                subject=subject,
                body=body,
                attachment_paths=[Path(p) for p in att_files],
            ):
                names = ", ".join(Path(p).name for p in att_files)
                return MailtoOpenResult(
                    True,
                    f"Mail ouvert avec le message prérempli et la pièce jointe : {names}.",
                    attachment_paths=att_files,
                )
        elif sys.platform == "win32":
            if _open_mail_windows_outlook(
                to=to_list,
                cc=cc_list,
                subject=subject,
                body=body,
                attachment_paths=[Path(p) for p in att_files],
            ):
                names = ", ".join(Path(p).name for p in att_files)
                return MailtoOpenResult(
                    True,
                    f"Outlook ouvert avec le message prérempli et la pièce jointe : {names}.",
                    attachment_paths=att_files,
                )

    bcc_list = [a.strip() for a in (bcc or ()) if a and a.strip()]
    strategies: list[tuple[str, str, str]] = [
        ("full", subject, body),
        ("no_body", subject, ""),
    ]
    if bcc_list:
        strategies.append(("bcc_only", "", ""))

    extra = ""
    if att_files:
        extra = (
            "\n\nPièce(s) jointe(s) non ajoutée(s) automatiquement — glissez le PDF dans le mail :\n"
            + "\n".join(f"  {p}" for p in att_files)
        )
        if clipboard is not None:
            clipboard.setText("\n".join(att_files))

    for mode, subj, bod in strategies:
        url = build_mailto_url(bcc=bcc_list or None, to=to_list or None, cc=cc_list or None, subject=subj, body=bod)
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
                f"(destinataires en Cci / Bcc).{extra}",
                attachment_paths=att_files,
            )
        if mode == "no_body":
            if clipboard is not None and body.strip():
                clipboard.setText(body)
            return MailtoOpenResult(
                True,
                "Le client mail a été ouvert avec l'objet et les destinataires (Cci). "
                f"Le corps du message a été copié dans le presse-papiers — collez-le dans le mail.{extra}",
                body_on_clipboard=True,
                attachment_paths=att_files,
            )
        if clipboard is not None:
            parts = []
            if subject.strip():
                parts.append(f"Objet : {subject.strip()}")
            if body.strip():
                parts.append(body.strip())
            if att_files:
                parts.extend(att_files)
            if parts:
                clipboard.setText("\n\n".join(parts))
        return MailtoOpenResult(
            True,
            "Trop de destinataires pour un message entièrement prérempli : seules les adresses "
            f"Cci ont été transmises. Objet, corps et chemins PDF copiés dans le presse-papiers.{extra}",
            body_on_clipboard=True,
            attachment_paths=att_files,
        )

    return MailtoOpenResult(
        False,
        "Liste de destinataires trop longue pour mailto: (limite Windows). "
        "Copiez les adresses et le message avec les boutons ci-dessous.",
    )
