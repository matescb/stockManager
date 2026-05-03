# Get started with Stock Manager

Audience: end user

Sign up, verify your email, land in your first workspace, and add your first part. Five minutes.

## 1. Create your account

> _Screenshot: the Sign up form with name, email, password and "Workspace name (optional)" fields._

1. Open the app and click **Sign up** at the bottom of the sign-in page.
2. Enter your name, email, and a password (minimum 8 characters).
3. Optionally enter a workspace name (you can rename it later). Leave it blank to get a default one.
4. Click **Create account**.

If your administrator has email verification enabled, you will see a "Check your inbox" page instead of being signed in. Open the verification link in the email; it stays valid for 24 hours. Clicking the link signs you in and drops you into your new workspace.

If verification is off (some self-hosted setups), the **Create account** button signs you in directly.

## 2. Sign in next time

> _Screenshot: the Sign in form._

1. Go to the app URL.
2. Enter your email and password.
3. Click **Sign in**.

After five wrong attempts the app locks the account for a few minutes. Wait, then try again — the message tells you when.

## 3. Find your way around

After sign-in you land on the **Parts** list. The left sidebar lists the main areas:

- **Parts** — what you buy and store.
- **Storage** — shelves, bins, reels.
- **Orders** — purchase orders to suppliers.
- **Projects** — bills of materials.
- **Builds** — when you actually consume parts.
- **Reports** — low stock, value, BOM shortage, expiring lots.

Top-right is your name. Click it to open the user menu (workspace switcher, account, sign out). Click the moon/sun icon to flip the theme.

> _Screenshot: the sidebar and the user menu open._

## 4. Add your first storage location

You don't have to, but stock you add later is easier to track if there is somewhere to put it.

1. Click **Storage** in the sidebar.
2. Click **+ Storage** (top right).
3. Give it a name (e.g. "Shelf A1") and an optional description.
4. Click **Create**.

See [storage locations](storage.md) for the checkbox options.

## 5. Add your first part

> _Screenshot: the Create part form._

1. Click **Parts** in the sidebar.
2. Click **+ Part** (top right).
3. Pick a **Type**:
   - **Linked (MPN)** — the typical case. The app looks up the part on a supplier (Mouser/DigiKey) by manufacturer part number.
   - **Local** — a part with no upstream supplier (in-house items, custom hardware).
   - **Meta-part** — a placeholder that several real parts can satisfy (e.g. "10k 0402 1%").
   - **Sub-assembly** — something you build from other parts.
4. Enter the **manufacturer part number** in the **MPN** field. For Linked parts a **Lookup** button appears; click it to pull the manufacturer name, description, datasheet, image, and specs from your supplier.
5. The **Name** field defaults to the MPN if you leave it blank.
6. Optionally pick a **Default storage location**.
7. Click **Create**.

If you see "MPN is already used by part X", that part already exists — click the **Open existing part** link instead of making a duplicate.

After Create the page jumps to the part's **Part info** tab. Provider data (if any) is tagged with the supplier name; you can refresh it later from the Linked banner.

For the full reference see [parts](parts.md).

## 6. Put some stock on a part

1. From the part page, click the **Add stock** tab.
2. Enter a **Quantity**.
3. Pick a **Storage location** (optional but recommended).
4. Optionally fill in **Price**, **Lot name**, **Serial number**, **Comments**.
5. Click **Add**.

The page jumps back to the **Stock** tab and shows the new total. Every add/remove/move is recorded in the **History** tab — nothing is overwritten.

See [stock](stock.md) for remove, move, lots, and the "found bag" flow.

## What to do if it doesn't work

- **Verification email never arrives** — check spam. Links expire after 24 hours; sign up again to get a fresh one. If your administrator hasn't configured email yet, the Sign up button signs you in directly with no email step.
- **"Email already registered"** on signup — sign in instead, or use a different email.
- **You signed up but a page is empty** — you may be looking at a different workspace than you expect. Open the workspace switcher (top-right, next to your name) and pick the right one.
- **The Lookup button does nothing or returns no result** — your workspace's supplier provider isn't configured, or the MPN doesn't exist upstream. An admin sets the provider in **Settings → Workspace**.
