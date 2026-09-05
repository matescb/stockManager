// Copies the end-user documentation shelf (docs/user/*.md) and the root
// CHANGELOG.md into web/src/generated/ so Vite can inline them into the
// bundle with `import.meta.glob(..., { query: "?raw" })`.
//
// Why a copy step rather than a backend route: `docs/` is outside the
// backend Docker build context (docker-compose.prod.yml `context: ./backend`)
// and the backend mounts no StaticFiles anywhere, so the files would never
// reach the container. Vite, on the other hand, can only import from inside
// its own project root — hence the copy. Same shape as
// scripts/copy-zxing-wasm.mjs, which is already wired as predev/prebuild.
//
// Run as `predev` / `prebuild` / `pretest`. Idempotent; the destination
// directory is wiped first so a deleted source page cannot linger in the
// bundle. The destination is gitignored — it is build output, and the
// single source of truth stays `docs/user/`.
//
// Path contract: the repo root is resolved relative to THIS FILE
// (`web/scripts/..(web)/..(root)`), so the same script works in three
// places:
//   * the host checkout        → <repo>/docs/user, <repo>/CHANGELOG.md
//   * the prod image build     → /docs/user, /CHANGELOG.md
//                                (web/Dockerfile.prod COPYs them there,
//                                 because WORKDIR is /app == web/)
//   * the dev container        → same paths, bind-mounted read-only by
//                                docker-compose.dev.yml
import { mkdirSync, rmSync, readdirSync, copyFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");

const USER_DOCS_SRC = path.join(repoRoot, "docs", "user");
const CHANGELOG_SRC = path.join(repoRoot, "CHANGELOG.md");

const GENERATED_DIR = path.resolve(here, "..", "src", "generated");
const USER_DOCS_DST = path.join(GENERATED_DIR, "user-docs");
const CHANGELOG_DST = path.join(GENERATED_DIR, "CHANGELOG.md");

function fail(message) {
  // Loud, not silent: an empty in-app manual is a shipped-broken feature,
  // and it would only be noticed by a user looking for help.
  console.error(`copy-docs: ${message}`);
  process.exit(1);
}

if (!existsSync(USER_DOCS_SRC)) fail(`missing source directory ${USER_DOCS_SRC}`);
if (!existsSync(CHANGELOG_SRC)) fail(`missing source file ${CHANGELOG_SRC}`);

rmSync(USER_DOCS_DST, { recursive: true, force: true });
mkdirSync(USER_DOCS_DST, { recursive: true });

const pages = readdirSync(USER_DOCS_SRC).filter(f => f.endsWith(".md")).sort();
if (pages.length === 0) fail(`no .md files under ${USER_DOCS_SRC}`);

for (const file of pages) {
  copyFileSync(path.join(USER_DOCS_SRC, file), path.join(USER_DOCS_DST, file));
}
copyFileSync(CHANGELOG_SRC, CHANGELOG_DST);

console.log(`copy-docs: ${pages.length} user doc(s) + CHANGELOG.md → src/generated/`);
