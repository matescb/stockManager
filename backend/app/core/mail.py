"""Pluggable email backend for transactional mail (SEC2-014).

Two backends are available:

* **StdoutBackend** (default in dev): writes the full email body to
  stdout / the container log so the developer can copy-paste the
  verification link without needing a real SMTP server.

* **SmtpBackend**: sends via SMTP with STARTTLS.  Used when
  ``APP_ENV == "prod"`` and ``SMTP_HOST`` is non-empty.

Call :func:`send_verification_email` from route code — it picks the
right backend automatically based on the current settings.

Design notes:
- All functions are synchronous; FastAPI routes calling them must use
  ``run_in_executor`` if they need async (not required today — the
  signup flow is already a sync def).
- No retry logic here.  A failure surfaces as a 500; the caller should
  not mint a ``PendingUser`` row before a successful send so no orphaned
  rows are left behind.
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


def send_verification_email(*, to: str, verification_link: str) -> None:
    """Send an email-verification message to ``to``.

    Picks the stdout backend in dev and the SMTP backend in prod.
    Raises on failure so the caller can abort the signup transaction.
    """
    cfg = settings()
    if cfg.APP_ENV == "prod" and cfg.SMTP_HOST:
        _send_smtp(to=to, verification_link=verification_link)
    else:
        _send_stdout(to=to, verification_link=verification_link)


def _send_stdout(*, to: str, verification_link: str) -> None:
    """Dev backend: write the email to stdout / container logs."""
    _log.info(
        "MAIL (stdout backend) To: %s\nVerification link: %s",
        to,
        verification_link,
    )
    # Also print directly so it appears even when log level is WARNING.
    print(
        f"\n{'='*60}\n"
        f"[DEV MAIL] Verification email\n"
        f"To: {to}\n"
        f"Link: {verification_link}\n"
        f"{'='*60}\n",
        flush=True,
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
