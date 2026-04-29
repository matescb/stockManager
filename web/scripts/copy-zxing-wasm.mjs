// Copies the zxing-wasm reader wasm out of node_modules into public/zxing/ so
// Vite serves it from our own origin. Without this, zxing-wasm's default
// `locateFile` pulls the wasm from jsDelivr at runtime — fine for demos,
// not fine for a self-hosted app that must work without third-party CDNs.
//
// Run as `prebuild`. Idempotent.
import { mkdirSync, copyFileSync } from "node:fs";

const SRC = "node_modules/zxing-wasm/dist/reader/zxing_reader.wasm";
const DST_DIR = "public/zxing";
const DST = `${DST_DIR}/zxing_reader.wasm`;

mkdirSync(DST_DIR, { recursive: true });
copyFileSync(SRC, DST);
console.log(`copied ${SRC} → ${DST}`);
