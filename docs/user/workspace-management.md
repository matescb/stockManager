# Workspace members and roles

Audience: end user

Invite people to your workspace, assign roles, and remove access when someone leaves.

A **workspace** is an isolated container — its parts, stock, orders, projects, and reports cannot be seen by anyone outside it. People you invite become **members**.

You manage members from **Settings → Workspace** in the sidebar.

## Roles

Every member has one of four roles. The role decides what they can do.

| Role | Can read | Can edit | Can manage members | Can delete workspace |
|---|---|---|---|---|
| **Owner** | yes | yes | yes | yes |
| **Admin** | yes | yes | yes | no |
| **Member** | yes | yes | no | no |
| **Viewer** | yes | no | no | no |

The user who created the workspace is its owner. Each workspace has exactly one owner.

- **Viewers** can browse parts, stock, orders, projects, and reports but cannot change anything. Use this for accountants, auditors, or read-only stakeholders.
- **Members** are the typical role for warehouse and engineering staff — full day-to-day work, no workspace settings.
- **Admins** can do everything members can, plus invite/remove other members and edit workspace settings (provider keys, lot control, public catalog).
- **Owners** are admins with one extra power: deleting the workspace itself.

Only admins and owners see the management UI below.

## Invite someone

> _Screenshot: the Workspace settings page with the invite form open._

1. Open **Settings → Workspace**.
2. Find the **Members** card.
3. Type their email into **Invite by email**.
4. Pick a **Role** (admin / member / viewer).
5. Click **Send invitation**.

The invitation lands as a row in the **Invitations** card with status **pending**. Two paths from here, depending on your administrator's email setup:

- **If your workspace can send email** — the invitee receives an email with a link to accept. Once they accept (signing up if they don't have an account), the invitation flips to **accepted** and they appear in **Members**.
- **If email isn't configured** — copy the invitation **token** from the row and send it to them yourself (chat, email, in person). They paste it into **Settings → Account → Accept workspace invitation** to join.

The invitation must be issued for the email address the invitee uses on their account.

## Revoke a pending invitation

If you change your mind before they accept:

1. Find the row in the **Invitations** card.
2. Click **Revoke** and confirm.

The token stops working immediately.

## Change a member's role

> _Screenshot: the Members card with a role dropdown next to a member's name._

1. Open **Settings → Workspace → Members**.
2. Find the row.
3. Pick a new role from the dropdown.

The change takes effect immediately. The member sees the new permissions on their next page load.

You cannot change the owner's role. To hand off ownership, TODO(verify-ui): there isn't an obvious "transfer ownership" UI — confirm whether ownership transfer is supported via the role dropdown when the current owner downgrades, or whether it requires backend assistance.

## Remove a member

1. Find the member's row.
2. Click **Remove** and confirm.

They lose access to the workspace immediately. Their past activity (orders they created, stock they added, comments) stays in the history under their name.

You cannot remove the owner.

## Switch between workspaces

If you're a member of more than one workspace, the workspace name appears next to your avatar at the top right.

1. Click the workspace name.
2. Pick a workspace from the dropdown.

The page reloads into the new workspace. Open queries are cancelled — you won't see stale data from the previous workspace mixed in.

## Create another workspace

If you need a separate, isolated set of parts and stock (e.g. for a different team or product line):

1. Open **Settings → Workspace**.
2. Find the **Workspaces** section near the top.
3. Type a name in **Create workspace** and click **Create**.

You're added as the owner. Switch into it from the workspace switcher.

## Choose active sourcing lists

The workspace settings page has an **Active currencies / countries / distributors** card. Admins use it to keep sourcing filters focused on the markets and suppliers the workspace actually uses.

1. Open **Settings → Workspace**.
2. Find **Active currencies / countries / distributors**.
3. Search within a list if needed.
4. Tick the currencies, countries, and distributors to keep active.
5. Click **Save active lists**.

Each list must keep at least one checked item.

## What to do if it doesn't work

- **The invitee says the link is expired** — invitations expire after some time; revoke the old one and send a fresh one.
- **They paste the token but get "invitation is for a different email"** — the invitation was issued for one email and they're signed in as another. Revoke, re-invite the email they actually use.
- **You can't change a role or invite anyone** — you're a member or viewer, not an admin or owner. Ask an admin to do it.
- **The workspace switcher doesn't list a workspace you expect** — your invitation hasn't been accepted, or the workspace was deleted.
- **Active lists won't save** — make sure every list has at least one selected item.
