const jsxA11y = require("eslint-plugin-jsx-a11y");

jsxA11y.rules["anchor-rel-noreferrer-noopener"] ??= require(
  "./eslint-rules/anchor-rel-noreferrer-noopener.cjs",
);

// Minimal ESLint config — CQ-005 / issue #121. Intentionally narrow:
// only @typescript-eslint/recommended + react-hooks/recommended. No
// prettier, no react/recommended (would conflict with the existing
// in-tree style). Bumping the rule set is a deliberate, separate PR.
//
// CI runs `npm run lint` (no --max-warnings cap on the first land so
// the gate is non-blocking; see .github/workflows/ci.yml). Promote to
// error per-file as cleanup PRs land.
module.exports = {
  root: true,
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  plugins: ["@typescript-eslint", "react-hooks", "jsx-a11y"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  rules: {
    // The codebase uses `_`-prefixed args/vars to mark intentionally
    // unused — keep that escape hatch.
    "@typescript-eslint/no-unused-vars": [
      "warn",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
    ],
    // Loosened on the first land — many existing files use `any` for
    // provider catalog / DataTable cells. Tighten in a follow-up.
    "@typescript-eslint/no-explicit-any": "off",
    "no-empty": ["warn", { allowEmptyCatch: true }],
    "jsx-a11y/anchor-rel-noreferrer-noopener": "error",
    "no-restricted-syntax": [
      "error",
      {
        selector:
          "CallExpression[callee.object.object.name='Sentry'][callee.object.property.name='logger'] Identifier[name=/token|secret|password|passwd|credential|authorization|cookie|session|csrf|dsn|apiKey|workspaceId/i]",
        message:
          "Do not pass sensitive identifiers to Sentry.logger.*; log a redacted scalar or omit the field.",
      },
      {
        selector: "Property[key.name='queryFn'] > ArrowFunctionExpression[params.length=0]",
        message: "TanStack queryFn must accept the query context and thread signal to API reads.",
      },
      {
        selector:
          "Property[key.name='queryFn'] > ArrowFunctionExpression > ObjectPattern:not(:has(Property[key.name='signal']))",
        message: "TanStack queryFn object context must destructure signal.",
      },
      {
        selector: "Property[key.name='queryFn'] > FunctionExpression[params.length=0]",
        message: "TanStack queryFn must accept the query context and thread signal to API reads.",
      },
      {
        selector:
          "Property[key.name='queryFn'] > FunctionExpression > ObjectPattern:not(:has(Property[key.name='signal']))",
        message: "TanStack queryFn object context must destructure signal.",
      },
    ],
  },
  ignorePatterns: [
    "dist/",
    // Auto-emitted by the composite TypeScript project (gitignored,
    // see CLAUDE.md "things that have bitten us").
    "vite.config.js",
    "scripts/",
    "node_modules/",
  ],
};
