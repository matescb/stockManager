# Alerts

Audience: end user

How to create and manage sourcing alerts for parts and project BOMs.

Alerts live under **Alerts** in the sidebar. Use them when you want Stock Manager to email the right people after stock or authorized-supply conditions change.

## Create an alert

1. Open **Alerts**.
2. Click **Create alert**.
3. Choose the alert type.
4. Choose the part or project.
5. Fill in the threshold if the alert type needs one.
6. Choose recipients, or leave recipients empty to notify workspace admins.
7. Click **Create alert**.

Part alerts can watch internal stock, authorized stock availability, or price movement. Project alerts can watch when a BOM becomes buyable for a build quantity.

> _Screenshot: the Alerts page with the create modal open._

## Create an alert from a part

1. Open a part.
2. Click **Authorized supply**.
3. Click **Set alert**.
4. Choose the alert type and finish the form.

The part is already selected in the form.

## Create an alert from Source BOM

1. Open a project.
2. Click **Sourcing**.
3. Set the build quantity you care about.
4. Click **Set BOM-buyable alert**.
5. Save the alert.

The project and build quantity are already selected in the form.

## Manage existing alerts

Use the filters at the top of **Alerts** to narrow by alert type, enabled state, or archived alerts.

- Click **Edit** to change the threshold, recipients, cooldown, or enabled state.
- Click **Archive** to stop an alert from evaluating.
- Turn **Include archived** on to review archived alerts.

## What to do if it doesn't work

- **You do not see a distributor, country, or currency** — ask a workspace admin to add it under **Settings** → **Workspace**.
- **No email arrives** — check that the alert is enabled and that the cooldown has passed. Workspace admins receive alerts when no recipients are selected.
- **A part alert cannot be saved** — choose a part and check that the threshold value is inside the allowed range.
