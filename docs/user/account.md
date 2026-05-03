# Your account

Audience: end user

Manage your profile, switch theme, accept workspace invitations, and sign out.

Account-level settings live under **Settings → Account** in the user menu (top right). They affect you, not the workspace.

## See your profile

Open **Settings → Account**.

> _Screenshot: the Account page showing name, email, and user ID._

You see:

- **Name**
- **Email**
- **User ID** (a long identifier — you don't need it day-to-day)

TODO(verify-ui): there's no obvious in-app form to edit your name or email; confirm whether profile editing is supported in the UI yet, and if so where.

## Change your password

TODO(verify-ui): no password-change form is visible in **Settings → Account** in the current UI. If you forgot your password, your administrator may need to reset it on the backend, or use a "forgot password" flow if one is configured. Confirm the current state before relying on this section.

## Switch theme

The app has a light and dark theme. Click the **sun / moon** icon in the top bar (next to the workspace switcher) to flip.

The choice is remembered in your browser. If you sign in from a different browser or device you'll see whatever default that browser picked.

## Accept a workspace invitation by token

If someone invited you to a workspace and you have the **token** (rather than a clickable email link):

> _Screenshot: the Accept workspace invitation card on the Account page._

1. Open **Settings → Account**.
2. Find the **Accept workspace invitation** card.
3. Paste the token into the input.
4. Click **Accept**.

You join the workspace as the role the inviter chose, and the app switches you into it. The token only works if the invitation was issued to the email you're signed in with.

## Switch between workspaces

The workspace name in the top bar (next to your avatar) is a switcher.

1. Click the workspace name.
2. Pick another workspace from the menu.

If you have unsaved work on the current page (e.g. a half-filled scan-import queue), the app warns you before switching.

## Sign out

1. Click your name (top right).
2. Click **Sign out** in the menu.

You're sent back to the sign-in page. Sign back in to resume — if you were on a deep page, the app remembers and takes you back there after sign-in.

## Sessions

A signed-in session lasts about 30 days unless you sign out manually. After 30 days you'll be sent back to the sign-in page; sign in again.

When you change your password (or an admin does it for you), every existing session is signed out for security — sign in again on each device.

TODO(verify-ui): there is no in-app "active sessions" list to revoke individual devices yet. Confirm before promising that feature in user training material.

## What to do if it doesn't work

- **The Accept button does nothing or says "invalid token"** — the token has been revoked, the invitation expired, or the email on the invitation doesn't match your account. Ask whoever invited you to re-send.
- **You're signed out unexpectedly** — your session expired (30 days), an admin reset your password, or you signed out elsewhere. Sign in again. The page you were on is preserved as a deep link, so the app brings you back.
- **The theme toggle doesn't change anything** — your browser may be blocking local storage, or the theme reset is being overridden by an OS-level "always dark" setting. Try a different browser or check your OS appearance settings.
