# Runbook: SMTP outage

Audience: engineer / on-call

The transactional-mail backend (`backend/app/core/mail.py`) sends signup
verification emails, workspace invitation emails, and sourcing-alert
emails. When the SMTP path fails, signups, invitations, and alert
delivery stall — but the rest of the app keeps working.

- **When to run**:
  - Users report that they signed up but never received the verification
    email.
  - Users report that they accepted an invitation link but the inviter
    sees no follow-up activity (no record of the invite arriving).
  - Sentry reports `smtplib.SMTPException` /
    `smtplib.SMTPAuthenticationError` from
    `backend/app/core/mail.py:_send_smtp` (around line 93).
  - Backend logs full of "connection refused" / "timeout" against the
    SMTP host.
- **Severity**: SEV-2. Existing users continue to work; only signup +
  invitation flows are blocked.
- **Time-to-recovery target**: 1 h to mitigate, 4 h for permanent fix.
- **Owner**: `<TODO(verify): on-call rotation>`

## What's affected vs not

| Feature | Affected? |
|---|---|
| Existing users logging in | No |
| Existing users using the app (parts, stock, BOM, etc.) | No |
| New user signup → click verification link → activate account | **Yes** — verification email never arrives |
| Admin sends invitation → invitee receives email | **Yes** |
| Invitee accepts an invitation link they already received | No — `POST /api/invitations/accept` doesn't send mail |
| Password-reset request | **Yes** — reset email never arrives, but the request still returns the generic `202 Accepted` response |
| Sourcing alert email delivery | **Yes** — alert evaluation continues, but SMTP delivery can fail |

## Intentional silent responses

Password-reset requests intentionally hide SMTP delivery failures. For
`POST /api/auth/request-password-reset`, the backend records
`mail.send_failed` when SMTP raises, then still returns the same generic
`202 Accepted` response used for successful, unknown-email, and throttled
requests. This preserves the account-enumeration defense and is
consistent with the signup and invitation flows: the caller should not
learn whether an address exists or whether a mail send failed. Sources:
`backend/app/api/routes/auth.py:500-543`,
`backend/app/core/mail.py:162-184`.

In prod the SMTP backend is the **only** option. The
`_require_smtp_in_prod` validator (`backend/app/core/config.py`) refuses
to construct `Settings` when `APP_ENV == "prod"` and any of `SMTP_HOST`
/ `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` / `APP_BASE_URL` is empty
or set to a dev default — the container fails to boot. Belt-and-braces:
`backend/app/core/mail.py::_send_stdout` raises `RuntimeError` if
invoked under `APP_ENV == "prod"`. So an "SMTP outage" here means the
configured SMTP host is reachable + accepted the boot config but is
returning errors at send-time. See [ADR-0018](../adr/0018-prod-smtp-fail-closed.md).

## Retry stance

SMTP sends are single-shot from the app. `_send_smtp` opens one SMTP
connection with `timeout=10`, performs STARTTLS/login/sendmail once, logs
the exception, and re-raises it; the generic alert-mail path uses the
same one-connection send sequence. Sources:
`backend/app/core/mail.py:165-179`,
`backend/app/core/mail.py:182-203`.

Do not wait for an in-process retry queue or repeatedly trigger the same
signup/invitation while SMTP is down. For signup and invitation mail,
mitigate manually below, then retry the user action once the provider or
credential problem is fixed. For sourcing alerts, `_send_alert_email`
logs `sourcing_alert.smtp_failed` and continues; alert bookkeeping is
committed before SMTP delivery, so the cooldown controls the next
automatic attempt. Sources:
`backend/app/domain/sourcing/alerts_evaluator.py:135-141`,
`backend/app/domain/sourcing/alerts_evaluator.py:544-572`.

## Pre-flight

- SSH access to the VPS as `deploy`.
- SMTP credentials owner identified (the provider's dashboard — the
  account that owns the `SMTP_USER` in `.env.prod`).
- Confirm which provider is in use:
  ```bash
  ssh deploy@<vps-host>
  sudo -u deploy grep '^SMTP_HOST=' /srv/stockmanager/.env.prod
  ```
  `<TODO(verify): SMTP host from .env.prod — likely a transactional
  service like Mailgun / SendGrid / Postmark or a host SMTP relay>`

## Steps

### 1. Confirm the failure mode

1. SSH in.
2. Tail the backend logs:
   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       logs --tail=200 backend | grep -iE 'smtp|mail|verification'
   ```
3. Confirm `SMTP_HOST` is actually set:
   ```bash
   sudo -u deploy grep -E '^SMTP_(HOST|PORT|USER)=' /srv/stockmanager/.env.prod
   sudo -u deploy grep '^MAIL_FROM=' /srv/stockmanager/.env.prod
   ```
   Defaults from `backend/app/core/config.py:74-82`:
   - `SMTP_PORT=587`
   - `MAIL_FROM=noreply@stockmanager.local` (must be overridden in
     prod or messages will be rejected by most providers).

### 2. Test SMTP connectivity from the VPS

1. From the VPS host (not inside the container — that way you separate
   network from app):
   ```bash
   nc -zv <SMTP_HOST> <SMTP_PORT>
   ```
2. STARTTLS handshake:
   ```bash
   openssl s_client -connect <SMTP_HOST>:<SMTP_PORT> -starttls smtp -crlf
   ```
   Expect `250` greeting; if the cert chain is broken or the host is
   unresolvable, the issue is outside the app.

### 3. Categorise

| Symptom | Cause | Action |
|---|---|---|
| `nc` succeeds, `openssl s_client` succeeds, app still fails with auth error | Wrong `SMTP_USER` / `SMTP_PASSWORD` | See "Rotate SMTP credentials" below |
| `nc` fails / times out | Network / provider outage | Wait + workaround (see below) |
| App fails with "MAIL FROM rejected" or "from address not allowed" | `MAIL_FROM` not whitelisted at provider | Update `.env.prod` and restart |
| Provider's status page shows incident | Provider outage | Workaround |

### 4. Mitigation — manual signup completion

While SMTP is down, **don't** retry signups in a loop hoping one will
land. Instead, complete signups manually for users who report not
receiving the email:

1. Find the pending signup in the DB:
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec db psql -U stockmgr stockmgr
   ```
   ```sql
   SELECT id, email, email_verified_at FROM users
    WHERE email = '<user-email>';
   ```
2. Find the verification token they would have received. The
   verification link is logged on the stdout backend; on the SMTP
   backend the link is constructed but the token is also stored on the
   user row.
   `<TODO(verify): exact verification token column on the users table —
   likely something like email_verification_token / _hash>`
3. Either:
   - Reconstruct the link and email it to the user out-of-band (e.g.
     from your own mail client), or
   - Mark the user verified directly:
     ```sql
     UPDATE users SET email_verified_at = NOW() WHERE id = '<user-id>';
     ```
     **Only do this** after independently confirming the email
     belongs to the person asking. This is the manual override; log
     it in the incident channel.

### 5. Mitigation — manual invitation delivery

Same pattern for invitations:

1. Inspect the pending invitation:
   ```sql
   SELECT id, email, status, created_at
     FROM workspace_invitations
    WHERE email = '<invitee-email>' AND status = 'pending';
   ```
2. The acceptance flow at
   `backend/app/api/routes/invitations.py:267` consumes the composite
   token `{invitation_id}:{plaintext_token}`. The plaintext is generated
   at invite-creation time and not stored — only the hash is on the row
   (see `backend/app/domain/workspaces/models.py:97`).
3. If the plaintext was never delivered, **revoke and re-issue** the
   invitation rather than fishing for a token that no longer exists:
   ```bash
   curl -X DELETE https://parts.matescb.cz/api/invitations/<invitation-id> \
       -H 'Cookie: <admin-session>' \
       -H 'X-Workspace-Id: <ws-id>'
   ```
   Then create a fresh invitation through the UI; capture the link
   from the response and deliver it manually.

### 6. Permanent fix paths

- **Provider outage**: wait. Retry once the provider's status page
  goes green.
- **Bad credentials**: rotate. Get a new SMTP password from the
  provider; update `.env.prod`:
  ```bash
  sudo -u deploy $EDITOR /srv/stockmanager/.env.prod
  # update SMTP_PASSWORD=<NEW>
  cd /srv/stockmanager
  sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
      up -d backend
  ```
- **MAIL_FROM rejected**: set `MAIL_FROM` to a domain you own and have
  configured at the provider (SPF + DKIM). Update `.env.prod` and
  restart backend.

## Verification

1. Trigger a signup with a throwaway email you control. The
   verification email should arrive within 1 min.
2. Send an invitation to a throwaway email. Same.
3. Backend logs are quiet — no `smtplib.*` exceptions in the last
   minute:
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       logs --tail=100 backend | grep -iE 'smtp|smtplib' || echo "clean"
   ```
4. Sentry: no new `smtplib.SMTPException` events.

## Rollback

- **Don't flip `SMTP_HOST` to empty as a test in prod.** `_require_smtp_in_prod`
  refuses to construct `Settings` (`backend/app/core/config.py`) and the
  container won't boot — you'll convert an SMTP outage into a full prod
  outage. If you accidentally did this: restore the previous `SMTP_HOST`
  and restart, no silent fallback exists. See [ADR-0018](../adr/0018-prod-smtp-fail-closed.md).
- If you manually verified a user (step 4) and they later turn out to
  not own the email: disable the user (`UPDATE users SET … WHERE id =
  '<user-id>'`), and treat as a security incident
  (`incident-response.md`).
- If you rotated `SMTP_PASSWORD` to the wrong value, restoring the old
  one is the rollback. The provider may rate-limit auth attempts —
  wait if you see "too many login attempts".

## Post-mortem prompts

- Did anything alert us, or did we learn from a user report?
- Is there a way to detect this without an alert (e.g. an
  `SMTP_HEALTHCHECK_OK_URL` analogous to the backup heartbeat)?
- Are there pending invitations / unverified users we never finished
  delivering to? Sweep the DB and follow up.
- Is `MAIL_FROM` set to a domain we control? If we ever swap providers
  this matters.
