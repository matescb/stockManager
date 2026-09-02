# User Help

Audience: end user

How to use Stock Manager. If you're an engineer working on the code, you want [`docs/`](../README.md) instead.

## What Stock Manager does

Stock Manager tracks **parts** (the things you buy and store), **stock** (how many you have and where), **orders** (what you've ordered from suppliers), **projects** (the bills of materials of things you build), and **builds** (when you actually consume stock to assemble something).

Stock is **append-only**: every change is a row in a history. Nothing is overwritten. This means you can always see when, why, and by whom a quantity changed.

Each **workspace** is isolated. Members of one workspace cannot see another workspace's data. You can be a member of more than one workspace; switch between them from the user menu.

## Tasks

### Getting started

- [Sign up, verify your email, create your first workspace and part](getting-started.md)

### Day-to-day

- [Add and manage parts](parts.md)
- [Scan bag codes to bulk-import stock](scan-import.md)
- [Add, remove, or move stock](stock.md)
- [Storage locations](storage.md)
- [Purchase orders and receiving](orders.md)
- [Projects and bill of materials](projects-and-bom.md)
- [Builds — consume stock against a BOM](builds.md)
- [Use your parts in KiCad](kicad.md)
- [Reports](reports.md)
- [Alerts](alerts.md)

### Settings

- [Workspace members and roles](workspace-management.md)
- [Account, password, theme](account.md)

## When something goes wrong

- A page is empty when you expected data → check the workspace switcher in the user menu; you may be in a different workspace than you think.
- A red error toast says something failed → the message is what the server told us. If it's not actionable, the underlying error is in your browser console (paste to your admin).
- The app logs you out unexpectedly → your session expired (default 30 days). Sign in again; deep-links preserve where you were going.
