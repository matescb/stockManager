# KiCanvas — vendored bundle

Audience: engineer

Where `web/public/kicanvas/kicanvas.js` came from, how to reproduce it
byte-for-byte, and the things about it that constrain how
`web/src/components/eda/` uses it.

The bundle itself lives in `web/public/` because everything there is served
verbatim to the browser. This page does not: it names backend paths and
advertises that the viewer is alpha, neither of which belongs on a public
URL, so it lives in `docs/` and only `LICENSE.md` ships beside the code.

## Pin

| | |
| --- | --- |
| Source repo | <https://github.com/theacodes/kicanvas> |
| Commit | `b031159eb74aaa7eef2b026fd85d35bc05ff2095` (2026-04-28) |
| Upstream version | none — `package.json` says `0.0.0`; the project has never tagged a release, so the commit **is** the pin |
| License | MIT — `web/public/kicanvas/LICENSE.md`, which ships beside the bundle, plus the third-party notices at the bottom of it |
| Pristine build sha256 | `ca910f25276c3efb9aacb3a5d6341d4d9af4736d4c875fb0440d2cc856865ab7` (477451 bytes) |
| **`kicanvas.js` as shipped** | `4b618fdd7beb0516bb3702b5af9dde370b2046e0171fa4e9ec73caa9bc216776` (477190 bytes) |

The shipped file is **not** the pristine upstream build — it carries one
deletion, described under "The local patch" below. `kicanvasPin.test.ts`
pins the shipped sha.

Not on npm — `npm view kicanvas` 404s. Vendoring the built bundle is the
install path the project itself documents (<https://kicanvas.org/embedding>,
"download the bundled kicanvas.js, copy it into your project").

## Why `public/` and not `src/`

The bundle lives in `web/public/kicanvas/`, beside `public/scandit/`, rather than under `src/vendor/`,
because everything in `src` is linted (`npm run lint` is `eslint src`) and
type-checked. A minified third-party bundle produces 60 baseline-breaking
warnings about upstream's own variable names, and the repo's rule is to fix
the source rather than widen the ignore list — which is not an option for a
file we copy in verbatim. `public/` also keeps rollup from re-parsing half a
megabyte of already-bundled ESM, and guarantees the bytes stay out of the
main chunk: the file is served as a static asset at `/kicanvas/kicanvas.js`
and pulled in on demand by `components/eda/kicanvas.ts`. `LICENSE.md` ships
beside it, which is what the MIT notice requires of a distribution.

## Reproducing

```bash
git clone https://github.com/theacodes/kicanvas
cd kicanvas && git checkout b031159eb74aaa7eef2b026fd85d35bc05ff2095
npm ci && npm run build:no-check     # → build/kicanvas.js
```

`build:no-check` (not `build`) is what upstream's own Pages workflow runs —
`build` additionally runs `tsc`, which is their lint gate, not part of
producing the artifact. The output of that command is byte-identical to
the bundle served at <https://kicanvas.org/kicanvas/kicanvas.js> (sha256
`ca910f25…`); that match is how this commit was confirmed to be the one
behind the published bundle. Apply the patch below to reach the file
actually shipped.

## The local patch

One statement removed — the last one in the pristine bundle:

```js
document.body.appendChild(f`<link
        rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@48,400,0,0&family=Nunito:wght@300;400;500;600;700&display=swap"
        crossorigin="anonymous" />`);
```

261 bytes, nothing else touched. Upstream source:
`src/kicanvas/elements/kicanvas-embed.ts` (a `TODO: Package these up as
part of KiCanvas` sits directly above it, so upstream considers it
temporary too).

**Why.** It runs at module scope, so merely loading the bundle would:

1. **Break under our CSP.** `deploy/nginx-web.conf` sets
   `style-src 'self' 'unsafe-inline'` with no `fonts.googleapis.com` and
   no `font-src` at all. The stylesheet is blocked, so the font never
   arrives — the icons it exists to provide would not render either way.
   The patch removes a request that was already failing.
2. **Beacon every viewer to Google.** IP and `Referer` — which carries the
   part-detail URL — on every preview, for a font we cannot use. Nothing
   else in this app contacts a third party at page load, and a
   self-hosted parts inventory is not where to start.

**Why it is invisible.** The font supplies Material Symbols *ligature*
icons, and KiCanvas draws exactly two controls that way: `download` and
`flip`. `components/eda/KicanvasFrame.tsx` suppresses both
(`controlslist="nooverlay nodownload noflipview"`), which is enforced by a
test. Everything still on screen in `controls="basic"` — the bottom
toolbar's zoom buttons — uses the bundle's own SVG sprite sheet, not the
webfont. The other face, Nunito, is body text that falls back to the
platform stack.

If you ever raise `controls` to `full`, re-check this: the sidebar
activities (`layers`, `category`, `memory`, `hub`, `list`, `info`) are all
ligature icons and would render as literal words.

**Reproducing the patch** after a rebuild:

```bash
node -e '
const fs=require("fs"), p="kicanvas.js", s=fs.readFileSync(p,"utf-8");
const i=s.indexOf("document.body.appendChild(f`<link"), e=s.indexOf("/>`);", i);
if (i<0||e<0) throw new Error("injection site moved — re-read the docs");
fs.writeFileSync(p, s.slice(0,i)+s.slice(e+5));'
```

It throws rather than silently no-opping if upstream moves the statement,
which is the behaviour you want on a bump.

## It is alpha software

Upstream says so on every docs page, and it shows: `<kicanvas-embed>` is
"a proposed API with an incomplete implementation". Consequences we rely on:

- **A parse failure must never break the page.** `SymbolPreview` /
  `FootprintPreview` render inside `PreviewBoundary`, which catches and
  shows a plain "preview unavailable" card. This is not defensive
  boilerplate — it is the documented state of the dependency.
- **Attributes that work at this pin**: `src`, `controls`
  (`none` | `basic` | `full`), `controlslist`, `type`, `name`. Attributes
  the docs mark ⚠️ not-implemented — `theme`, `zoom`, and most
  `controlslist` values other than `nooverlay` / `nodownload` /
  `noflipview` — are **not** used by this app; setting them is silently ignored.
  Of `controlslist`, only `nooverlay`, `nodownload`, `download`,
  `noflipview` and `flipview` are actually wired up at this commit.
- **No usable events.** Every `kicanvas:*` event in the docs (including
  `kicanvas:error` and `kicanvas:load`) is marked not-yet-implemented and
  none is dispatched at this commit. There is therefore no in-band way to
  learn that a document failed to load — which is the other reason the
  error boundary carries the whole burden.

## It cannot read `.kicad_sym` or `.kicad_mod`

The single most load-bearing fact about this dependency, and the reason
`domain/eda/preview.py` exists.

KiCanvas reads exactly four document types — `.kicad_sch`, `.kicad_pcb`,
`.kicad_wks`, `.kicad_pro`. At this commit `src/` contains no occurrence of
`kicad_sym` or `kicad_mod` at all: `KiCanvasSourceElement.determine_file_type`
sniffs only `(kicad_sch`, `(kicad_pcb` and `(kicad_wks`, and `Project.load`
dispatches only on those filename suffixes. Pointing `<kicanvas-embed>` at a
symbol library or a footprint file yields "Unknown file type".

What it *does* have is complete `LibSymbol` (`src/kicad/schematic.ts`) and
`Footprint` (`src/kicad/board.ts`) parsers — it simply only reaches them
through a schematic or a board. So the backend synthesises the container:
a symbol entry is served inside a one-symbol `(kicad_sch …)`, a footprint
inside a one-footprint `(kicad_pcb …)`. See `backend/app/domain/eda/preview.py`,
which documents the two non-obvious constraints that shape those documents
(`lib_id` must match the entry name; a placement needs a `Value` property).

## The contract test, and the second bundle

Because a document this viewer cannot parse renders **blank** rather than
erroring, "does the preview still work?" cannot be answered by reading
code or by watching Sentry. `web/src/components/eda/__tests__/kicanvasContract.test.ts`
answers it by parsing the exact documents the backend emits — checked in
at `backend/tests/fixtures/eda/preview/` — with KiCanvas's own
`KicadSch` / `KicadPCB`, and asserting real geometry survived: the
symbol's unit sub-symbols and pins, the placement resolving its
`lib_symbol`, the footprint's pads on declared layers.

It cannot import *this* file to do it: the shipped bundle exports nothing
(upstream's entry is three side-effect imports and it calls
`window.customElements.define` at module scope). So a second, parser-only
build of the **same commit** lives at `web/test-vendor/kicanvas-parsers/`.
Its README has the build command. The two must be rebuilt together —
`kicanvasPin.test.ts` fails if this bundle's sha256 stops matching what
this page records, which is what stops one being bumped without the other.

## Upgrading

There is no changelog and no releases, so treat any bump as a behaviour
change:

1. Rebuild per the command above **and** rebuild
   `web/test-vendor/kicanvas-parsers/` from the same commit.
2. Re-apply the local patch, and check whether it is still needed — if
   upstream has packaged the fonts (their TODO says they intend to), drop
   the patch and this section instead of carrying it forward.
3. Update the commit / both sha256s / sizes on this page and in that directory's
   README, and the pinned sha in `kicanvasPin.test.ts`.
4. Re-check the attribute list above and the four recognised document
   types against the new `src/` before trusting either.
5. Grep the new bundle for further network calls —
   `grep -oE 'https?://[^"'"'"'`]+' kicanvas.js` — and reconcile anything
   new against the CSP in `deploy/nginx-web.conf`.
6. Run the contract test. If it fails, the preview is broken — fix the
   wrapping in `backend/app/domain/eda/preview.py`, do not relax the test.

## What to glance at after deploying

Two things about the viewer interact with the production CSP, and neither is covered by
an automated test, because nothing in this repo's harness renders WebGL:

* **Zoom icons.** The bottom toolbar's two buttons come from an SVG sprite
  sheet the bundle builds with `URL.createObjectURL`, so they load over a
  `blob:` URL. `deploy/nginx-web.conf` carries `blob:` in `img-src` for
  exactly this. If the buttons are blank boxes, that directive is why.
* **No literal-word icons.** If "download" or "flip" appears as text on a
  preview, a `controlslist` value was dropped and the removed webfont is
  showing through.

Open a part's CAD tab, select a hosted symbol and a hosted footprint, and
confirm both draw with clean icons and no CSP violations in the console.

`backend/tests/test_eda_preview.py` pins the route behaviour and
`web/src/components/eda/__dom__/` pins the element wiring.
