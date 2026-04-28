/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0c0f",
        panel: "#14161b",
        border: "#222",
        muted: "#9aa0ac",
        text: "#e6e6e6",
        accent: "#4ade80",
        accentHover: "#22c55e",
        danger: "#f87171",
        warning: "#fbbf24",
        success: "#34d399",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
