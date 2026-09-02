# Use your parts in KiCad

Audience: end user

Put your workspace's parts in KiCad's symbol chooser, and pull symbols, footprints and 3D models into the workspace from a supplier download.

Two things have to be set up, and you need both. One puts your parts in the chooser; the other installs the symbol and footprint files those entries point at. **Settings → KiCad setup** walks through them and fills in the values for you — this page is the same thing in words.

## Connect KiCad in three steps

### 1. Make a token

A token is how KiCad proves it is allowed to read your workspace. It acts as you, in this workspace only.

1. Open **Settings → KiCad setup**.
2. Click **Mint a read-only token**.
3. Give it a name you will recognise later ("KiCad on the bench laptop"), tick **Read-only**, and click **Create**.
4. **Copy it now.** It is shown once and cannot be recovered — if you lose it, revoke it and make another.
5. Go back to **Settings → KiCad setup** and paste it into the box.

Choose read-only. The token ends up in a file on your computer, and read-only means a copy of that file can never change anything.

> _Screenshot: the KiCad setup page with a token pasted in and the download button enabled._

### 2. Add the parts library

1. On the setup page, click **Download stockmanager.kicad_httplib** and save it somewhere permanent — KiCad reads it from that path every time it starts, so don't leave it in Downloads.
2. In KiCad, open **Preferences → Manage Symbol Libraries**.
3. Go to the **Global Libraries** tab and click **+**.
4. Set **Library Path** to the file you saved, and **Library Format** to **Database/HTTP**. The nickname is yours to choose.
5. Click OK, then open the symbol chooser.

Your categories appear as sub-trees. Parts with no category land under **Uncategorized**.

### 3. Install the symbol files

The library from step 2 only *names* symbols and footprints. This step installs the files, so the names resolve.

1. On the setup page, copy the **repository URL** (it already has your token in it).
2. In KiCad, open **Preferences → Manage Plugin and Content Manager Repositories** and add it.
3. Open the **Plugin and Content Manager**, choose your new repository on the **Libraries** tab, and install the package.

When you add or change a symbol in Stock Manager, the Plugin and Content Manager will offer an update. Take it — that is how new parts reach your machine.

### If you run simulations

Only needed if you use ngspice. The setup page shows a variable name and a path.

1. Open **Preferences → Configure Paths**.
2. Add an environment variable with exactly the name and value shown on the setup page.
3. Restart KiCad.

## Import a supplier download

Most suppliers of CAD data — SnapEDA, Component Search Engine, UltraLibrarian — give you a zip. You do not need to unpack it.

1. Open the part in Stock Manager.
2. Go to the **CAD** tab, and find **Import from vendor zip**.
3. Leave **Replace what's already set** unticked to fill only the empty slots; tick it to overwrite what the part already has.
4. Click **Choose zip** and pick the file.

Stock Manager reads the zip, works out which supplier produced it, and takes the symbol, the footprint and any 3D models. Anything it did not take is listed with the reason — usually a file type that is not CAD data.

If a zip holds several parts, the import says so rather than guessing which one you meant. Open each of those parts and import the same zip there; Stock Manager matches it against that part's manufacturer part number.

## Fetch from LCSC

If the part has an LCSC part number, you don't need a download at all.

1. On the **CAD** tab, find **Fetch from LCSC**.
2. Enter the LCSC part number (it looks like `C25804`) and click **Fetch**.

Stock Manager asks EasyEDA for the symbol, footprint and 3D model and imports whatever it has. Parts often have no 3D model; you get the symbol and footprint anyway, and the missing piece is listed.

## What to do if it doesn't work

**A part vanished from the symbol chooser.**
A part is only offered to KiCad if it has a symbol. If someone archived the symbol, or archived the category that was supplying a default one, the part stops appearing and its detail page in KiCad shows nothing. Restore the symbol, or give the part one on its CAD tab.

**A renamed category still shows its old name.**
KiCad reads category names when it first connects to the library, not on the refresh timer. Reload the library — or restart KiCad — and the new name appears. Nothing is broken.

**An edit isn't showing up.**
KiCad caches what it fetched: about a minute for parts, ten minutes for categories. Wait it out, or close and reopen the symbol chooser.

**KiCad says the symbol is broken, or shows an empty box.**
The parts library is connected but the files are not installed. Do step 3. If you already did, open the Plugin and Content Manager and check the package is installed and up to date.

**Importing a zip says it is a KiCad 5 library.**
That file is in a format KiCad stopped using. The message names the command that converts it — run it on your own machine, then import the `.kicad_sym` it produces.

**The repository URL is refused.**
It only accepts read-only tokens. If you pasted a token that was not created with **Read-only** ticked, make a new one that is.

**A download or fetch says the part is too large.**
There are per-file size limits: 1 MB for a symbol, 2 MB for a footprint, 10 MB for a 3D model. A file over the limit is skipped and named in the result.
