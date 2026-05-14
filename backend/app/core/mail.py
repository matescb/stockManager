"""Pluggable email backend for transactional mail (SEC2-014).

Two backends are available:

* **StdoutBackend** (default in dev): logs a single WARNING containing
  the verification link so the developer can copy-paste it without
  needing a real SMTP server. **Refuses to run when ``APP_ENV == "prod"``**
  (issue #281): the verification link is a bearer credential and must
  never be written to container logs in prod. The config validator
  (`_require_smtp_in_prod`) is supposed to have failed closed at import
  time; the runtime guard here is defence in depth.

* **SmtpBackend**: sends via SMTP with STARTTLS. Selected whenever
  ``APP_ENV == "prod"``. The validator guarantees ``SMTP_HOST`` etc.
  are populated.

Call :func:`send_verification_email` from route code — it picks the
right backend automatically based on the current settings. Use
:func:`send_account_exists_email` for duplicate signup attempts so the
HTTP response can remain non-enumerating while the mailbox owner gets a
private signal. Background jobs that need transactional mail call
:func:`send` so they stay on the same stdout/dev and SMTP/prod path.

Design notes:
- All functions are synchronous; FastAPI routes calling them must use
  ``run_in_executor`` if they need async (not required today — the
  signup flow is already a sync def).
- No retry logic here.  A failure surfaces as a 500; the caller should
  not mint a ``PendingUser`` row before a successful send so no orphaned
  rows are left behind.
- Issue #281: never log the message body. The dev backend logs only a
  one-line WARNING with the link (intentional — the dev workflow needs
  it visible) and the SMTP backend never logs the body at all.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

_log = logging.getLogger(__name__)


def _build_verification_email(to: str, verification_link: str) -> MIMEMultipart:
    """Build a MIME multipart message with plain-text and HTML parts."""
    cfg = settings()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Verify your Stock Manager account"
    msg["From"] = cfg.MAIL_FROM
    msg["To"] = to

    plain = (
        f"Welcome to Stock Manager!\n\n"
        f"Please verify your email address by visiting:\n{verification_link}\n\n"
        f"This link expires in 24 hours.\n\n"
        f"If you did not sign up, you can safely ignore this email."
    )
    html = (
        f"<p>Welcome to <strong>Stock Manager</strong>!</p>"
        f"<p>Please verify your email address by clicking the link below:</p>"
        f'<p><a href="{verification_link}">Verify my email</a></p>'
        f"<p>Or copy and paste this URL: {verification_link}</p>"
        f"<p><small>This link expires in 24 hours. If you did not sign up, "
        f"ignore this email.</small></p>"
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def _build_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> MIMEMultipart:
    """Build a generic MIME multipart message."""
    cfg = settings()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.MAIL_FROM
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain"))
    if html_body is not None:
        msg.attach(MIMEText(html_body, "html"))
    return msg


def send(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """Send a transactional email on the configured mail backend.

    Dev logs only recipient + subject, never the body. Prod uses the same
    STARTTLS SMTP path as verification mail and raises on failure so
    callers can decide whether to fail closed or continue.
    """
    cfg = settings()
    if cfg.APP_ENV == "prod":
        _send_smtp_message(
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        return
    _log.warning(
        "dev mail backend (SMTP not configured): message to %s subject=%s",
        to,
        subject,
    )


def send_verification_email(*, to: str, verification_link: str) -> None:
    """Send an email-verification message to ``to``.

    Picks the stdout backend in dev and the SMTP backend in prod.
    Raises on failure so the caller can abort the signup transaction.

    Issue #281: the dispatch is on ``APP_ENV`` alone (not on
    ``APP_ENV and SMTP_HOST``). A misconfigured prod that reached this
    point would previously have fallen through to the stdout backend and
    leaked the link into container logs. The config validator now fails
    closed at import; this dispatch and the guard inside ``_send_stdout``
    are defence in depth.
    """
    cfg = settings()
    if cfg.APP_ENV == "prod":
        _send_smtp(to=to, verification_link=verification_link)
    else:
        _send_stdout(to=to, verification_link=verification_link)


def send_account_exists_email(*, to: str) -> None:
    """Notify an existing user that a signup was attempted for their email."""
    send(
        to=to,
        subject="Stock Manager account already exists",
        text_body=(
            "Someone tried to sign up for Stock Manager with this email address.\n\n"
            "An account already exists for this address, so no new account was created.\n"
            "If this was you, sign in with your existing account. If you did not try "
            "to sign up, you can safely ignore this email."
        ),
        html_body=(
            "<p>Someone tried to sign up for Stock Manager with this email address.</p>"
            "<p>An account already exists for this address, so no new account was created.</p>"
            "<p>If this was you, sign in with your existing account. If you did not try "
            "to sign up, you can safely ignore this email.</p>"
        ),
    )


def _send_stdout(*, to: str, verification_link: str) -> None:
    """Dev backend: log a single WARNING with the verification link.

    Refuses to run in prod (issue #281) — the verification link is a
    bearer credential and must never be written to container logs. In
    dev the link is logged at WARNING so it surfaces under the default
    log level without needing the older `print` banner.
    """
    cfg = settings()
    if cfg.APP_ENV == "prod":
        # Belt-and-braces: the config validator should already have
        # prevented boot, but if some future code path reaches here in
        # prod, fail loud rather than leak.
        raise RuntimeError(
            "stdout mail backend refused in prod (issue #281): SMTP must "
            "be configured. This indicates a misconfigured deploy that "
            "bypassed the config validator."
        )
    _log.warning(
        "dev mail backend (SMTP not configured): verification link for %s: %s",
        to,
        verification_link,
    )


def _send_smtp(*, to: str, verification_link: str) -> None:
    """Prod backend: send via SMTP with STARTTLS."""
    cfg = settings()
    msg = _build_verification_email(to=to, verification_link=verification_link)
    try:
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if cfg.SMTP_USER:
                smtp.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            smtp.sendmail(cfg.MAIL_FROM, [to], msg.as_string())
    except Exception as exc:
        _log.error("SMTP send failed to %s: %s", to, exc)
        raise


def _send_smtp_message(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """Prod backend: send a generic message via SMTP with STARTTLS."""
    cfg = settings()
    msg = _build_email(
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
    with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=10) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if cfg.SMTP_USER:
            smtp.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        smtp.sendmail(cfg.MAIL_FROM, [to], msg.as_string())
