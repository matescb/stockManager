/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // All resolved from CSS variables defined in src/index.css.
        // Keep the rgb()/<alpha-value> form so utility classes like
        // bg-accent/20 keep working (Tailwind injects the alpha).
        bg:           "rgb(var(--c-bg)           / <alpha-value>)",
        panel:        "rgb(var(--c-panel)        / <alpha-value>)",
        panel2:       "rgb(var(--c-panel2)       / <alpha-value>)",
        border:       "rgb(var(--c-border)       / <alpha-value>)",
        borderStrong: "rgb(var(--c-borderStrong) / <alpha-value>)",
        rowHover:     "rgb(var(--c-rowHover)     / <alpha-value>)",
        panelHover:   "rgb(var(--c-panelHover)   / <alpha-value>)",
        text:         "rgb(var(--c-text)         / <alpha-value>)",
        muted:        "rgb(var(--c-muted)        / <alpha-value>)",
        accent:       "rgb(var(--c-accent)       / <alpha-value>)",
        accentHover:  "rgb(var(--c-accentHover)  / <alpha-value>)",
        danger:       "rgb(var(--c-danger)       / <alpha-value>)",
        warning:      "rgb(var(--c-warning)      / <alpha-value>)",
        success:      "rgb(var(--c-success)      / <alpha-value>)",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
